import secrets
from functools import wraps

from .units import fmt as fmt_dist, show as _units_show
from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from psycopg.errors import UniqueViolation

from .action_response import action_response
from . import backup as bk
from . import airports as ap
from . import feeders as fd
from . import stations as st
from . import maplayers as ml
from . import tools as tl
from . import history as hist
from .audit import audit
from .db import get_db
from .mailer import MODES, send_test_email, smtp_config
from .models import User
from .permissions import PERMISSIONS
from .setting_specs import SPECS, validate as validate_spec
from .settings import DEFAULTS, cfg, cfg_int, has_secret, set_secret, set_setting
from .roles import (can_grant, enabled_super_admins, get_role, role_perms, roles_overview,
                    valid_permissions)

bp = Blueprint("admin", __name__, url_prefix="/admin")


def permission_required(perm):
    def deco(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.has_permission(perm):
                abort(404)
            return view(*args, **kwargs)
        return wrapped
    return deco


def admin_area_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.has_admin_access:
            abort(404)
        return view(*args, **kwargs)
    return wrapped


@bp.route("/")
@admin_area_required
def index():
    from . import adminoverview
    return render_template("admin_overview.html", active="overview", ov=adminoverview.build(current_user))


# ---------------------------------------------------------------- users

def _users_context():
    roles = get_db().execute("SELECT * FROM roles ORDER BY lower(name)").fetchall()
    return {"users": User.all_with_roles(), "all_roles": [r for r in roles if can_grant(current_user, r)]}


def _users_response(ok, message):
    return action_response(ok, message, redirect_endpoint="admin.users",
                           fragment_template="partials/_users_table.html",
                           fragment_context=_users_context())


@bp.route("/users")
@permission_required("manage_users")
def users():
    return render_template("admin_users.html", active="users", **_users_context())


@bp.route("/users/<int:user_id>", methods=["POST"])
@permission_required("manage_users")
def user_action(user_id):
    target = User.get(user_id)
    if target is None:
        abort(404)
    action = request.form.get("action")
    d = get_db()
    if action == "disable":
        if target.id == current_user.id:
            return _users_response(False, "You cannot disable your own account.")
        d.execute("UPDATE users SET disabled=TRUE WHERE id=%s", (target.id,))
        if enabled_super_admins() < 1:
            d.rollback()
            return _users_response(False, "At least one enabled Super Admin account must remain.")
        d.commit()
        nudge_matrix()
        audit("user.disable", target=target.username)
        return _users_response(True, f"{target.username} has been disabled.")
    if action == "enable":
        d.execute("UPDATE users SET disabled=FALSE WHERE id=%s", (target.id,))
        d.commit()
        nudge_matrix()
        audit("user.enable", target=target.username)
        return _users_response(True, f"{target.username} has been enabled.")
    if action == "reset_2fa":
        d.execute("UPDATE users SET totp_secret='', totp_enabled=FALSE, totp_last=0 WHERE id=%s", (target.id,))
        d.execute("DELETE FROM recovery_codes WHERE user_id=%s", (target.id,))
        d.commit()
        audit("user.reset_2fa", target=target.username)
        return _users_response(True, f"Two-factor sign-in has been turned off for {target.username}. They can set it up again under My Account > Two-Factor.")
    if action == "reset_password":
        temp = secrets.token_urlsafe(12).replace("-", "x").replace("_", "y")
        target.set_password(temp, must_change=True)
        audit("user.reset_password", target=target.username)
        return _users_response(True, f"Temporary password for {target.username}: {temp}. "
                                     "It is shown only once, and the user must change it at their next sign-in.")
    abort(400)


@bp.route("/users/<int:user_id>/roles", methods=["POST"])
@permission_required("manage_users")
def user_roles(user_id):
    target = User.get(user_id)
    role = get_role(request.form.get("role_id", type=int) or 0)
    if target is None or role is None:
        abort(404)
    if not can_grant(current_user, role):
        return _users_response(False, f"You do not have permission to assign or remove the {role['name']} role.")
    d = get_db()
    action = request.form.get("action")
    if action == "assign":
        d.execute("INSERT INTO user_roles (user_id, role_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                  (target.id, role["id"]))
        d.commit()
        audit("user.role_assign", target=target.username, detail=role["name"])
        return _users_response(True, f"{role['name']} assigned to {target.username}.")
    if action == "remove":
        d.execute("DELETE FROM user_roles WHERE user_id=%s AND role_id=%s", (target.id, role["id"]))
        if enabled_super_admins() < 1:
            d.rollback()
            return _users_response(False, "At least one enabled Super Admin account must remain.")
        d.commit()
        audit("user.role_remove", target=target.username, detail=role["name"])
        return _users_response(True, f"{role['name']} removed from {target.username}.")
    abort(400)


# ---------------------------------------------------------------- roles

def _roles_context():
    d = get_db()
    return {"roles": roles_overview(), "permissions": PERMISSIONS,
            "all_users": d.execute("SELECT id, username FROM users ORDER BY lower(username)").fetchall(),
            "actor": current_user}


def _roles_response(ok, message):
    return action_response(ok, message, redirect_endpoint="admin.roles",
                           fragment_template="partials/_roles_list.html",
                           fragment_context=_roles_context())


@bp.route("/roles")
@permission_required("manage_roles")
def roles():
    return render_template("admin_roles.html", active="roles", **_roles_context())


@bp.route("/roles/create", methods=["POST"])
@permission_required("manage_roles")
def role_create():
    name = request.form.get("name", "").strip()
    if not name or len(name) > 60:
        return _roles_response(False, "Enter a role name of up to 60 characters.")
    d = get_db()
    try:
        d.execute("INSERT INTO roles (name) VALUES (%s)", (name,))
        d.commit()
    except UniqueViolation:
        d.rollback()
        return _roles_response(False, "A role with that name already exists.")
    audit("role.create", target=name)
    return _roles_response(True, f"Role {name} created. Open it to choose its permissions.")


@bp.route("/roles/<int:role_id>/save", methods=["POST"])
@permission_required("manage_roles")
def role_save(role_id):
    role = get_role(role_id)
    if role is None:
        abort(404)
    if role["is_super_admin"] and not current_user.is_super_admin:
        return _roles_response(False, "Only a Super Admin can change a Super Admin role.")
    name = role["name"] if role["is_builtin"] else request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()[:300]
    wanted = set(valid_permissions(request.form.getlist("permissions")))
    make_super = bool(request.form.get("is_super_admin"))
    if not name or len(name) > 60:
        return _roles_response(False, "Enter a role name of up to 60 characters.")
    if not current_user.is_super_admin:
        if make_super:
            return _roles_response(False, "Only a Super Admin can create a Super Admin role.")
        added = wanted - role_perms(role_id)
        if any(not current_user.has_permission(p) for p in added):
            return _roles_response(False, "You cannot grant a permission you do not hold yourself.")
    d = get_db()
    try:
        d.execute("UPDATE roles SET name=%s, description=%s, is_super_admin=%s WHERE id=%s",
                  (name, description, make_super, role_id))
    except UniqueViolation:
        d.rollback()
        return _roles_response(False, "A role with that name already exists.")
    d.execute("DELETE FROM role_permissions WHERE role_id=%s", (role_id,))
    for p in wanted:
        d.execute("INSERT INTO role_permissions (role_id, permission) VALUES (%s,%s)", (role_id, p))
    if enabled_super_admins() < 1:
        d.rollback()
        return _roles_response(False, "At least one enabled Super Admin account must remain.")
    d.commit()
    audit("role.update", target=name, detail=", ".join(sorted(wanted)) + (" [super admin]" if make_super else ""))
    return _roles_response(True, f"Role {name} saved.")


@bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@permission_required("manage_roles")
def role_delete(role_id):
    role = get_role(role_id)
    if role is None:
        abort(404)
    if role["is_builtin"]:
        return _roles_response(False, f"The built-in {role['name']} role cannot be deleted.")
    if role["is_super_admin"] and not current_user.is_super_admin:
        return _roles_response(False, "Only a Super Admin can delete a Super Admin role.")
    d = get_db()
    d.execute("DELETE FROM roles WHERE id=%s", (role_id,))
    if enabled_super_admins() < 1:
        d.rollback()
        return _roles_response(False, "At least one enabled Super Admin account must remain.")
    d.commit()
    audit("role.delete", target=role["name"])
    return _roles_response(True, f"Role {role['name']} deleted.")


@bp.route("/roles/<int:role_id>/members", methods=["POST"])
@permission_required("manage_roles")
def role_members(role_id):
    role = get_role(role_id)
    target = User.get(request.form.get("user_id", type=int) or 0)
    if role is None or target is None:
        abort(404)
    if not can_grant(current_user, role):
        return _roles_response(False, f"You do not have permission to change who holds the {role['name']} role.")
    d = get_db()
    action = request.form.get("action")
    if action == "add":
        d.execute("INSERT INTO user_roles (user_id, role_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                  (target.id, role_id))
        d.commit()
        audit("user.role_assign", target=target.username, detail=role["name"])
        return _roles_response(True, f"{target.username} now holds the {role['name']} role.")
    if action == "remove":
        d.execute("DELETE FROM user_roles WHERE user_id=%s AND role_id=%s", (target.id, role_id))
        if enabled_super_admins() < 1:
            d.rollback()
            return _roles_response(False, "At least one enabled Super Admin account must remain.")
        d.commit()
        audit("user.role_remove", target=target.username, detail=role["name"])
        return _roles_response(True, f"{target.username} no longer holds the {role['name']} role.")
    abort(400)


# ---------------------------------------------------------------- SMTP

EMAIL_SHAPE = __import__("re").compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.route("/smtp")
@permission_required("manage_settings")
def smtp():
    return render_template("admin_smtp.html", active="smtp", cfg=smtp_config(),
                           password_saved=has_secret("smtp_password"), modes=MODES)


@bp.route("/smtp/save", methods=["POST"])
@permission_required("manage_settings")
def smtp_save():
    host = request.form.get("host", "").strip()
    port = request.form.get("port", "").strip()
    security = request.form.get("security", "starttls")
    sender = request.form.get("from", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if security not in MODES:
        return action_response(False, "Choose a valid Connection Security option.", redirect_endpoint="admin.smtp")
    if port and not (port.isdigit() and 1 <= int(port) <= 65535):
        return action_response(False, "SMTP Port must be a number between 1 and 65535.", redirect_endpoint="admin.smtp")
    if sender and not EMAIL_SHAPE.match(sender):
        return action_response(False, "From Address must be a valid email address.", redirect_endpoint="admin.smtp")
    set_setting("smtp_host", host)
    set_setting("smtp_port", port)
    set_setting("smtp_security", security)
    set_setting("smtp_skip_cert", "1" if request.form.get("skip_cert") else "0")
    set_setting("smtp_username", username)
    set_setting("smtp_from", sender)
    if request.form.get("clear_password"):
        set_secret("smtp_password", "")
    elif password:
        set_secret("smtp_password", password)
    audit("settings.smtp_update", target=host or "(cleared)")
    note = "" if host else " Email is now disabled because no SMTP Host is set."
    return action_response(True, "SMTP settings saved." + note, redirect_endpoint="admin.smtp")


@bp.route("/smtp/test", methods=["POST"])
@permission_required("manage_settings")
def smtp_test():
    recipient = request.form.get("recipient", "").strip()
    if not EMAIL_SHAPE.match(recipient):
        return action_response(False, "Enter a valid address to send the test to.", redirect_endpoint="admin.smtp")
    ok, message = send_test_email(recipient)
    audit("settings.smtp_test", target=recipient, detail="ok" if ok else "failed")
    return action_response(ok, message, redirect_endpoint="admin.smtp")


# ---------------------------------------------------------------- system settings

from .privacy import choices as _privacy_choices
PRIVACY_CHOICES = _privacy_choices()   # the one list of location choices (app/privacy.py)
FEED_MODE_CHOICES = [("open", "Open (anyone can feed)"), ("registration", "Registration required (not yet enforced)")]


def _timezones():
    from zoneinfo import available_timezones
    return sorted(available_timezones())


# Sections of the hand-built settings (the table-driven ones come from SPECS). Each section saves on its own.
CORE_SECTIONS = [
    ("General", ["site_name", "registration_open"]),
    ("Feeders", ["default_privacy", "feed_mode", "feed_host", "feed_port", "mlat_enabled", "mlat_port", "uat_enabled"]),
    ("Map Defaults", ["map_lat", "map_lon", "map_zoom", "map_zoom_page"]),
    ("Display", ["timezone"]),
    ("Security", ["max_failed_attempts", "lockout_minutes"]),
    ("History", ["history_retention_days", "max_rules_per_user", "sample_retention_days"]),
]
CHECKBOXES = {"registration_open", "mlat_enabled", "uat_enabled"}
INT_RANGES = {"max_failed_attempts": ("Failed Sign-In Attempts", 3, 20), "lockout_minutes": ("Lockout Duration", 1, 1440),
              "history_retention_days": ("History Retention", 0, 3650), "sample_retention_days": ("Feeder Statistics Detail", 1, 365),
              "max_rules_per_user": ("Alert Rules per User", 1, 200)}


# The settings sub-nav: which tab each section sits under. A section not listed here lands under Other, so a new setting group is never lost.
SETTING_TABS = [
    ("General", ["General", "Display", "Units", "Themes", "Public Pages", "Contacts", "Service Status", "Data Retention", "Shopping Links", "My Flights"]),
    ("Accounts and Security", ["Accounts", "Security"]),
    ("Map and Data", ["Map Defaults", "Map Areas", "Home Page and Live Map", "Map Tiles and Weather", "Flight History", "History", "Range Statistics"]),
    ("Facilities and Lookups", ["Hospitals", "Air Ambulance", "Interesting Aircraft", "Places and Search", "Address Finders", "Aircraft Lookups", "Weather Stations", "Radiosondes", "Prediction Service", "Repeaters", "APRS", "Routing", "Map Data", "Counties"]),
    ("Feeders and Alerts", ["Feeders", "Feeders and Claiming", "Feeder Filter", "Alerts"]),
    ("Updates and Backups", ["Scheduled Updates", "Backups"]),
]


def _tab_of(section_name):
    from . import adminservices as svc
    return svc.LABELS[svc.service_of(section_name)]


def _slug(name):
    return name.lower().replace(" ", "-")


def _sections():
    """All settings sections in page order: [(name, slug, core keys, spec rows)]."""
    out = [(name, _slug(name), keys, []) for name, keys in CORE_SECTIONS]
    index = {name: row for name, _, _, row in out}
    for group, key, label, kind, help_, extra in SPECS:
        if group not in index:
            row = []
            index[group] = row
            out.append((group, _slug(group), [], row))
        index[group].append({"key": key, "label": label, "kind": kind, "help": help_, "extra": extra})
    return out


@bp.route("/settings")
@bp.route("/settings/<service>")
@permission_required("manage_settings")
def settings_page(service=None):
    """Every setting (tabs by service), or one service's settings (a tab for each of its sections)."""
    from . import adminservices as svc
    values = {k: cfg(k) for k in DEFAULTS}
    sections = _sections()
    if service is None:
        tabs = [(label, _slug(label)) for key, label, _n in svc.SERVICES]
        return render_template("admin_settings.html", active="allsettings", v=values, timezones=_timezones(), sections=sections, tab_of=_tab_of, tabs=tabs, service="", service_label="", section_home=_section_home(sections),
                               privacy_choices=PRIVACY_CHOICES, feed_mode_choices=FEED_MODE_CHOICES)
    if service not in svc.LABELS:
        abort(404)
    sections = [s for s in sections if svc.service_of(s[0]) == service]
    return render_template("admin_settings.html", active="settings" if service == "site" else "set_" + service, v=values, timezones=_timezones(), sections=sections, tab_of=lambda n: n, tabs=[(s[0], _slug(s[0])) for s in sections],
                           service=service, service_label=svc.LABELS[service], section_home=_section_home(_sections()), privacy_choices=PRIVACY_CHOICES, feed_mode_choices=FEED_MODE_CHOICES)


def _section_home(sections):
    from . import adminservices as svc
    return {sl: svc.settings_href(svc.service_of(name), "") for name, sl, _k, _r in sections}


@bp.route("/settings/search.json")
@permission_required("manage_settings")
def settings_search():
    """Find a setting anywhere: its name, key, section or help text. Each result says where it lives (the service's Settings page, the section and the field)."""
    from . import adminservices as svc
    q = request.args.get("q", "").strip().lower()[:60]
    if len(q) < 2:
        return jsonify({"results": []})
    words = q.split()
    out = []
    for name, sl, core_keys, rows in _sections():
        service = svc.service_of(name)
        items = [{"key": k, "label": _core_label(k), "help": ""} for k in core_keys] + [{"key": r["key"], "label": r["label"], "help": r["help"]} for r in rows]
        for it in items:
            hay = " ".join([it["label"], it["key"].replace("_", " "), name, it["help"]]).lower()
            if all(w in hay for w in words):
                score = (0 if all(w in it["label"].lower() for w in words) else 1 if all(w in (it["label"] + " " + it["key"].replace("_", " ")).lower() for w in words) else 2)
                out.append((score, {"label": it["label"], "section": name, "service": svc.LABELS[service], "href": svc.settings_href(service, sl, it["key"]), "help": it["help"][:140]}))
    out.sort(key=lambda t: (t[0], t[1]["label"]))
    return jsonify({"results": [o for _s, o in out[:25]], "total": len(out)})


def _core_label(key):
    if key in INT_RANGES:
        return INT_RANGES[key][0]
    return key.replace("_", " ").title()


def _validate_core(key, raw):
    """Return (clean value, error) for one hand-built setting."""
    if key in CHECKBOXES:
        return ("1" if raw else "0"), None
    raw = (raw or "").strip()
    if key == "site_name":
        if not raw or len(raw) > 80:
            return None, "Site Name must be between 1 and 80 characters."
    elif key == "default_privacy":
        if raw not in dict(PRIVACY_CHOICES):
            return None, "Choose a valid Default Location Privacy."
    elif key == "feed_mode":
        if raw not in dict(FEED_MODE_CHOICES):
            return None, "Choose a valid Feed Mode."
    elif key in ("map_lat", "map_lon", "map_zoom", "map_zoom_page"):
        limits = {"map_lat": (-90, 90, "Map latitude must be -90 to 90."), "map_lon": (-180, 180, "Map longitude must be -180 to 180."),
                  "map_zoom": (1, 18, "Map zoom must be 1 to 18."), "map_zoom_page": (1, 18, "Full-page map zoom must be 1 to 18.")}[key]
        try:
            ok = limits[0] <= float(raw) <= limits[1]
        except ValueError:
            return None, "Map latitude, longitude and zoom must all be numbers."
        if not ok:
            return None, limits[2]
    elif key == "feed_host":
        raw = raw.lower()
        if not __import__("re").match(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$", raw):
            return None, "Feed Hostname must be a valid hostname such as feed.example.org."
    elif key in ("feed_port", "mlat_port"):
        if not (raw.isdigit() and 1 <= int(raw) <= 65535):
            return None, f"{'Feed' if key == 'feed_port' else 'MLAT'} Port must be a number from 1 to 65535."
    elif key == "timezone":
        if raw not in _timezones():
            return None, "Choose a valid Display Timezone."
    elif key in INT_RANGES:
        label, lo, hi = INT_RANGES[key]
        if not (raw.isdigit() and lo <= int(raw) <= hi):
            return None, f"{label} must be a whole number from {lo} to {hi}."
    return raw, None


def _back():
    """Where a settings save returns to: the service's Settings page it came from, or the page with every setting."""
    from . import adminservices as svc
    sv = request.form.get("service", "")
    return {"service": sv} if sv in svc.LABELS else {}


@bp.route("/settings/save", methods=["POST"])
@permission_required("manage_settings")
def settings_save():
    """Save one section (the form's hidden `section` field) or, without it, every section at once."""
    f = request.form
    wanted = f.get("section", "")
    sections = _sections()
    if wanted and wanted not in {slug for _, slug, _, _ in sections}:
        return action_response(False, "Unknown settings section.", redirect_endpoint="admin.settings_page", redirect_kwargs=_back())
    chosen = [sec for sec in sections if not wanted or sec[1] == wanted]

    new = {}
    for name, _slug_, core_keys, rows in chosen:
        for key in core_keys:
            clean, err = _validate_core(key, f.get(key, cfg(key) if key == "map_zoom_page" else ""))
            if not err and key == "feed_host" and clean and clean != cfg(key):
                from . import siteaddress
                err = siteaddress.check(clean, serves_pages=False)
            if err:
                return action_response(False, err, redirect_endpoint="admin.settings_page", redirect_kwargs=_back())
            new[key] = clean
        for s in rows:
            raw = ",".join(f.getlist(s["key"])) if s["kind"] == "collist" and (wanted or s["key"] in f) else f.get(s["key"], cfg(s["key"]))
            if s["kind"] == "dist" and (s["key"] not in f or raw.strip() == _units_show(cfg(s["key"]), s["extra"]["base"])):
                new[s["key"]] = cfg(s["key"])   # not sent, or the shown number untouched: keep the stored value exactly (a stored 250 m must never be read back as 250 feet)
                continue
            if s["kind"] == "secret":   # never shown: left blank keeps what is saved, the box clears it
                if f.get(s["key"] + "__clear"):
                    new[s["key"]] = ""
                    continue
                if not raw.strip():
                    new[s["key"]] = cfg(s["key"])
                    continue
            clean, err = validate_spec(s["key"], s["kind"], s["label"], raw, s["extra"])
            if not err and s["key"] == "admin_allow_addresses" and clean != cfg(s["key"]):
                from . import adminallow
                err = adminallow.check(clean)
            if not err and s["key"] in ("main_address", "feed_host") and clean and clean != cfg(s["key"]):
                from . import siteaddress
                err = siteaddress.check(clean, serves_pages=s["key"] == "main_address")
            if err:
                return action_response(False, err, redirect_endpoint="admin.settings_page", redirect_kwargs=_back())
            new[s["key"]] = clean
    from .settings import SEALED_SETTINGS
    changed = [(f"{k}: changed" if k in SEALED_SETTINGS else f"{k}: {cfg(k)} -> {v}") for k, v in new.items() if cfg(k) != v]   # a secret's value never goes into the audit log
    for k, v in new.items():
        set_setting(k, v)
    if changed:
        audit("settings.update", detail="; ".join(changed)[:900])
    label = chosen[0][0] if wanted else "Settings"
    return action_response(True, (f"{label} saved." if wanted else "Settings saved.") if changed else "No changes to save.",
                           redirect_endpoint="admin.settings_page", redirect_kwargs=_back())


# ---------------------------------------------------------------- backups

def _backups_context():
    from . import offsite
    return {"backups": bk.list_backups(scan=False), "v": {k: cfg(k) for k in DEFAULTS},
            "last_status": cfg_get("backup_last_status"), "last_run": bk.last_run(), **offsite.context()}


def cfg_get(key):
    from .settings import get_setting
    return get_setting(key)


def _backups_response(ok, message):
    return action_response(ok, message, redirect_endpoint="admin.backups",
                           fragment_template="partials/_backups_list.html", fragment_context=_backups_context())


@bp.route("/backups")
@permission_required("manage_settings")
def backups():
    return render_template("admin_backups.html", active="backups", **_backups_context())


@bp.route("/backups/run", methods=["POST"])
@permission_required("manage_settings")
def backup_run():
    try:
        name, manifest = bk.create_backup(current_app.config["DATABASE_URL"], "manual")
    except Exception as e:
        return _backups_response(False, f"The backup failed: {e}")
    audit("backup.create", target=name)
    return _backups_response(True, f"Backup created: {name} ({sum(manifest['row_counts'].values())} rows).")


@bp.route("/backups/schedule", methods=["POST"])
@permission_required("manage_settings")
def backup_schedule():
    f = request.form
    mode, time_of_day, keep = f.get("schedule", ""), f.get("time", "").strip(), f.get("retention", "").strip()
    if mode not in ("off", "hourly", "daily", "weekly"):
        return _backups_response(False, "Choose a valid schedule.")
    if not __import__("re").match(r"^([01]\d|2[0-3]):[0-5]\d$", time_of_day):
        return _backups_response(False, "Time must be in 24-hour HH:MM format, for example 02:00.")
    if not (keep.isdigit() and 1 <= int(keep) <= 365):
        return _backups_response(False, "Keep must be a whole number from 1 to 365.")
    set_setting("backup_schedule", mode)
    set_setting("backup_time", time_of_day)
    set_setting("backup_retention", keep)
    audit("backup.schedule", detail=f"{mode} at {time_of_day}, keep {keep}")
    return _backups_response(True, "Backup schedule saved.")


@bp.route("/backups/<name>/download")
@permission_required("manage_settings")
def backup_download(name):
    if not bk.valid_name(name) or not __import__("os").path.exists(bk.backup_path(name)):
        abort(404)
    audit("backup.download", target=name)
    return send_file(bk.backup_path(name), as_attachment=True, download_name=name)


@bp.route("/backups/<name>/delete", methods=["POST"])
@permission_required("manage_settings")
def backup_delete(name):
    if not bk.valid_name(name):
        abort(404)
    try:
        bk.delete_backup(name)
    except FileNotFoundError:
        return _backups_response(False, "That backup no longer exists.")
    audit("backup.delete", target=name)
    return _backups_response(True, f"Deleted {name}.")


def _do_restore(path, label):
    url = current_app.config["DATABASE_URL"]
    get_db().rollback()  # release this request's own read locks so the restore can replace the tables
    try:
        bk.validate_archive(path, url)
    except ValueError as e:
        return _backups_response(False, str(e))
    try:
        safety, _ = bk.create_backup(url, "pre-restore")
    except Exception as e:
        return _backups_response(False, f"The safety backup failed, so nothing was restored: {e}")
    try:
        manifest = bk.restore_backup(path, url)
    except Exception as e:
        audit("backup.restore_failed", target=label, detail=str(e)[:300])
        return _backups_response(False, f"The restore failed and no data was changed: {e}")
    bk.apply_retention()
    audit("backup.restore", target=label, detail=f"safety backup {safety}")
    return _backups_response(True, f"Restored {sum(manifest['row_counts'].values())} rows from {label}. "
                                   f"The previous data was saved first as {safety}. You may need to sign in again.")


@bp.route("/backups/<name>/restore", methods=["POST"])
@permission_required("manage_settings")
def backup_restore(name):
    if not bk.valid_name(name) or not __import__("os").path.exists(bk.backup_path(name)):
        abort(404)
    return _do_restore(bk.backup_path(name), name)


@bp.route("/backups/upload", methods=["POST"])
@permission_required("manage_settings")
def backup_upload():
    import os
    import tempfile
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _backups_response(False, "Choose a backup file to upload.")
    fd, tmp = tempfile.mkstemp(suffix=".tar.gz", dir=bk.backup_dir())
    os.close(fd)
    try:
        upload.save(tmp)
        return _do_restore(tmp, f"uploaded file {os.path.basename(upload.filename)}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---------------------------------------------------------------- history

def _page():
    return max(1, request.args.get("page", 1, type=int) or 1)


def _pager(total, page):
    pages = max(1, -(-total // cfg_int("page_size")))
    args = {k: v for k, v in request.args.items() if k != "page"}
    return {"page": page, "pages": pages, "total": total, "args": args}


@bp.route("/audit")
@permission_required("view_history")
def audit_log():
    q, action, days = request.args.get("q", "").strip(), request.args.get("action", ""), request.args.get("days", "30")
    if request.args.get("format") == "csv":
        rows, _ = hist.audit_query(q, action, days, 1, hist.EXPORT_LIMIT)
        body = hist.to_csv(["time_utc", "actor", "action", "target", "detail", "ip"],
                           [[r["ts"].isoformat(), r["actor_name"], r["action"], r["target"], r["detail"], r["ip"]] for r in rows])
        audit("history.export", target="audit log", detail=f"{len(rows)} rows")
        return Response(body, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=audit-log.csv"})
    page = _page()
    rows, total = hist.audit_query(q, action, days, page)
    return render_template("admin_audit.html", active="audit", rows=rows, pager=_pager(total, page), q=q, action=action,
                           days=days, actions=hist.audit_actions(), day_choices=hist.DAY_CHOICES)


@bp.route("/signins")
@permission_required("view_history")
def signins():
    q, result, days = request.args.get("q", "").strip(), request.args.get("result", ""), request.args.get("days", "30")
    if request.args.get("format") == "csv":
        rows, _ = hist.signin_query(q, result, days, 1, hist.EXPORT_LIMIT)
        body = hist.to_csv(["time_utc", "username", "result", "ip", "detail"],
                           [[r["ts"].isoformat(), r["username"], "ok" if r["success"] else "failed", r["ip"], r["detail"]] for r in rows])
        audit("history.export", target="sign-in history", detail=f"{len(rows)} rows")
        return Response(body, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=sign-ins.csv"})
    page = _page()
    rows, total = hist.signin_query(q, result, days, page)
    return render_template("admin_signins.html", active="signins", rows=rows, pager=_pager(total, page), q=q, result=result,
                           days=days, day_choices=hist.DAY_CHOICES)


# ---------------------------------------------------------------- feeders (admin)

def _mlat_row(uuid):
    from . import mlat
    return mlat.feeder_status(uuid)


@bp.route("/feeders")
@permission_required("manage_feeders")
def feeders_list():
    from .settings import get_setting
    from . import mlat
    mlat_ok = mlat.working_uuids()
    return render_template("admin_feeders.html", active="feeders", privacy=st.PRIVACY, setup_fields=st.SETUP_FIELDS, default_privacy=cfg("default_privacy"), feeders=[dict(f, mlat=f["uuid"] in mlat_ok) for f in fd.list_feeders()],
                           hub_configured=bool(current_app.config.get("HUB_URL")),
                           last_ok=get_setting("collector_last_ok"), last_error=get_setting("collector_last_error"))


@bp.route("/services.json")
@permission_required("manage_settings")
def services_json():
    """The service registry as the pages see it (app/services.py), for tests/services_test.py: what each enabled service adds to the shared pages."""
    from . import services
    out = []
    for s in services.enabled():
        row = {"key": s["key"], "label": s["label"], "permission": s.get("permission"), "status_key": s.get("status_key"), "about": s.get("about"), "feed": s.get("feed")}
        for hook in ("home_layers", "home_now", "admin_tiles", "admin_attention", "admin_data"):
            row[hook] = None
            if s.get(hook):
                try:
                    row[hook] = s[hook]()
                except Exception as e:
                    get_db().rollback()
                    row[hook] = {"error": str(e)[:120]}
        out.append(row)
    return jsonify({"services": out})


@bp.route("/members/search.json")
@login_required
def member_search():
    """Live member lookup for the admin add/assign forms: user name, display name, call sign or email; each match carries the member's feeders (and whether one already has a weather station)."""
    if not any(current_user.has_permission(p) for p in ("manage_feeders", "manage_weather", "manage_sondes")):
        abort(404)
    q = (request.args.get("q") or "").strip()[:80]
    if len(q) < 1:
        return jsonify({"members": []})
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    d = get_db()
    rows = d.execute("SELECT id, username, display_name, callsign, email FROM users WHERE NOT disabled AND (username ILIKE %s OR display_name ILIKE %s OR callsign ILIKE %s OR email ILIKE %s) "
                     "ORDER BY (lower(username) = lower(%s)) DESC, lower(username) LIMIT 10", (like, like, like, like, q)).fetchall()
    out = []
    for r in rows:
        fs = d.execute("SELECT f.uuid, f.station_id, f.name, EXISTS (SELECT 1 FROM wx_stations w WHERE w.feeder_uuid = f.uuid) AS has_wx FROM feeders f WHERE f.owner_id=%s ORDER BY f.first_seen", (r["id"],)).fetchall()
        out.append({"username": r["username"], "display_name": r["display_name"] or "", "callsign": r["callsign"] or "", "email": r["email"] or "",
                    "feeders": [{"uuid": f["uuid"], "station_id": f["station_id"] or "", "name": f["name"] or "", "has_wx": f["has_wx"]} for f in fs]})
    return jsonify({"members": out})


@bp.route("/feeders/add", methods=["POST"])
@permission_required("manage_feeders")
def feeder_add():
    """Register an ADS-B feeder for a member before it has connected (to help set one up): it belongs to them from the start, and when the receiver connects with that UUID it is simply theirs."""
    from . import accounts, stations as st
    f = request.form
    uuid = (f.get("uuid") or "").strip().lower()
    back = dict(redirect_endpoint="admin.feeders_list")
    if not accounts.UUID_RE.match(uuid):
        return action_response(False, "The UUID must look like 123e4567-e89b-12d3-a456-426614174000 (the UUID value in the receiver's configuration).", **back)
    d = get_db()
    known = d.execute("SELECT 1 FROM feeders WHERE uuid=%s", (uuid,)).fetchone()
    u = d.execute("SELECT id, username FROM users WHERE lower(username)=lower(%s) OR lower(email)=lower(%s)", ((f.get("username") or "").strip(), (f.get("username") or "").strip())).fetchone()
    if u is None:
        return action_response(False, "No member has that user name or email address.", **back)
    if known:   # a receiver the hub has already heard: it is handed to the member (taken over if it belonged to someone else)
        d.execute("UPDATE feeders SET owner_id=%s, claimed_at=COALESCE(claimed_at, now()), claim_method='admin', privacy=COALESCE(privacy,%s) WHERE uuid=%s", (u["id"], cfg("default_privacy"), uuid))
        d.execute("INSERT INTO feeder_events (feeder_uuid, event, detail) VALUES (%s,'owner_changed',%s)", (uuid, f"Owner set to {u['username']}"))
        d.commit()
        audit("feeder.assign", target=uuid, detail=u["username"])
        flash(f"That receiver is now {u['username']}'s feeder.", "success")
        return redirect(url_for("admin.feeder_detail", uuid=uuid))
    sid = (f.get("station_id") or "").strip().upper()
    if sid and not st.STATION_ID_RE.match(sid):
        return action_response(False, "A station ID is 3 to 16 characters: letters, numbers and dashes.", **back)
    if sid and st._taken(sid):
        return action_response(False, f"{sid} is already in use.", **back)
    form = request.form.to_dict()
    form.setdefault("privacy", cfg("default_privacy"))
    details, err = st.parse_details(form)   # the same details a member can set on My Feeders
    if err:
        return action_response(False, err, **back)
    d.execute("INSERT INTO feeders (uuid, name, first_seen, last_seen, online, owner_id, claimed_at, claim_method, privacy) VALUES (%s,%s,now(),now(),FALSE,%s,now(),'admin',%s)",
              (uuid, (f.get("name") or "").strip()[:80] or None, u["id"], cfg("default_privacy")))
    d.execute("INSERT INTO feeder_events (feeder_uuid, event, detail) VALUES (%s,'claimed',%s)", (uuid, f"{u['username']} (added by an administrator)"))
    d.commit()
    st.save_details(uuid, details)
    if sid:
        st.set_station_id(uuid, sid, "custom")
    audit("feeder.assign", target=uuid, detail=f"{u['username']} (added by an administrator)")
    flash(f"Feeder registered for {u['username']}. It shows as theirs under My Feeders and is online as soon as the receiver connects with that UUID.", "success")
    return redirect(url_for("admin.feeder_detail", uuid=uuid))


@bp.route("/mlat")
@permission_required("manage_feeders")
def mlat_page():
    from . import mlat
    matched, unmatched = mlat.status_by_feeder()
    if matched is None:
        return render_template("admin_mlat.html", active="mlat", rows=None, enabled=cfg("mlat_enabled") == "1")
    feeders = {f["uuid"]: f for f in fd.list_feeders()}
    rows = [dict(r, feeder=feeders.get(u)) for u, r in matched.items()] + [dict(r, feeder=None) for r in unmatched]
    rows.sort(key=lambda r: -r["message_rate"])
    return render_template("admin_mlat.html", active="mlat", rows=rows, enabled=cfg("mlat_enabled") == "1")


@bp.route("/feeders/<uuid>")
@permission_required("manage_feeders")
def feeder_detail(uuid):
    f = fd.get_feeder(uuid)
    if f is None:
        abort(404)
    period = request.args.get("period", "24h")
    if period not in fd.PERIODS:
        period = "24h"
    points, uptime = fd.series(uuid, period)
    return render_template("admin_feeder.html", active="feeders", f=f, events=fd.feeder_events(uuid),
                           chart=fd.line_chart(points, period), uptime=uptime, period=period, periods=fd.PERIODS,
                           latest=points[-1] if points else None, mlat=_mlat_row(uuid),
                           all_users=get_db().execute("SELECT id, username FROM users ORDER BY lower(username)").fetchall())


# ---------------------------------------------------------------- airports (admin)

@bp.route("/airports")
@permission_required("manage_map_data")
def airports_admin():
    from zoneinfo import ZoneInfo
    return render_template("admin_airports.html", active="airports", st=ap.status(), countries=cfg("airport_countries"),
                           schedule=ap.next_update(ap.status()["last"], int(cfg("airports_update_days")), cfg("airports_update_time"), ZoneInfo(cfg("timezone"))))


@bp.route("/airports/import", methods=["POST"])
@permission_required("manage_map_data")
def airports_import():
    countries = request.form.get("countries", "").upper().replace(" ", "")
    codes = [c for c in countries.split(",") if c]
    if not codes or not all(len(c) == 2 and c.isalpha() for c in codes):
        return action_response(False, "Enter one or more two-letter country codes separated by commas, for example US or US,CA.",
                               redirect_endpoint="admin.airports_admin")
    set_setting("airport_countries", ",".join(codes))
    get_db().rollback()
    try:
        counts = ap.import_airports(codes)
    except Exception as e:
        get_db().rollback()
        return action_response(False, f"The airport data could not be updated: {e}", redirect_endpoint="admin.airports_admin")
    audit("airports.import", detail=f"{','.join(codes)}: {counts['airports']} airports")
    return action_response(True, f"Airport data updated: {counts['airports']} airports, {counts['frequencies']} frequencies, "
                                 f"{counts['runways']} runways.", redirect_endpoint="admin.airports_admin")


@bp.route("/places")
@permission_required("manage_map_data")
def places_admin():
    from . import places as pl
    return render_template("admin_places.html", active="places", st=pl.status(), year=cfg("places_year"))


@bp.route("/places/import", methods=["POST"])
@permission_required("manage_map_data")
def places_import():
    from . import places as pl
    get_db().rollback()
    try:
        c = pl.import_places()
    except Exception as e:
        get_db().rollback()
        return action_response(False, f"The places data could not be updated: {e}", redirect_endpoint="admin.places_admin")
    audit("places.import", detail=f"{c['place']} cities and towns, {c['zip']} ZIP areas, {c['county']} counties")
    return action_response(True, f"Places updated: {c['place']} cities and towns, {c['zip']} ZIP areas, {c['county']} counties.", redirect_endpoint="admin.places_admin")


@bp.route("/hospitals")
@permission_required("manage_map_data")
def hospitals_admin():
    from . import hospitals as hp
    q = request.args.get("q", "").strip()[:40]
    iss = request.args.get("issue", "")
    chk = request.args.get("check", "")
    rows, total = hp.listing(q, request.args.get("state", ""), "", "", 200, 0, check=chk if chk in ("review", "agreed", "single", "conflict", "none") else "", issue=iss if iss == "any" or iss in dict(hp.ISSUES) else "")
    hmedia = {r["key"]: dict(r) for r in get_db().execute("SELECT key, website, photo_url, photo_credit, edited FROM place_media WHERE kind='hospital'").fetchall()}
    return render_template("admin_hospitals.html", active="hospitals", hmedia=hmedia, st=hp.status(), rows=rows, total=total, q=q, check=chk, issue=iss, issue_labels=dict(hp.ISSUES), cfg_agree=cfg("verify_agree_meters"), cfg_map_lat=cfg("map_lat"), cfg_map_lon=cfg("map_lon"), cfg_warn=cfg("hospital_heliport_warn_km"), trauma=hp.TRAUMA, services=hp.SERVICES, states=cfg("hospital_states"),
                           kept_types=cfg("hospital_types").replace(",", ", ") or "all", excluded_words=cfg("hospital_exclude_words") or "none")


@bp.route("/hospitals/import", methods=["POST"])
@permission_required("manage_map_data")
def hospitals_import():
    from . import hospitals as hp
    get_db().rollback()
    try:
        c = hp.import_hospitals()
    except Exception as e:
        get_db().rollback()
        return action_response(False, f"The hospital list could not be updated: {e}", redirect_endpoint="admin.hospitals_admin")
    audit("hospitals.import", detail=f"{c['added']} added, {c['updated']} refreshed")
    return action_response(True, f"Hospitals updated: {c['added']} added, {c['updated']} refreshed, {c['removed']} removed by the type filter, {c['geocoded']} located with the Census geocoder; {c['total']} in all.", redirect_endpoint="admin.hospitals_admin")


@bp.route("/hospitals/apply-filter", methods=["POST"])
@permission_required("manage_map_data")
def hospitals_apply_filter():
    from . import hospitals as hp
    n = hp.apply_filter()
    audit("hospitals.apply_filter", detail=f"{n} removed")
    return action_response(True, f"{n} facilit{'y' if n == 1 else 'ies'} removed that do not match the type filter. Anything you edited by hand was kept.", redirect_endpoint="admin.hospitals_admin")


@bp.route("/hospitals/trauma-template.csv")
@permission_required("manage_map_data")
def trauma_template_csv():
    from . import hospitals as hp
    from flask import Response
    return Response(hp.trauma_template(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=trauma-levels-template.csv"})


@bp.route("/hospitals/trauma-import", methods=["POST"])
@permission_required("manage_map_data")
def trauma_import():
    from . import hospitals as hp
    up = request.files.get("file")
    text = (up.read(2_000_000).decode("utf-8-sig", "replace") if up and up.filename else request.form.get("pasted", ""))
    if not text.strip():
        return action_response(False, "Choose a CSV file or paste the list.", redirect_endpoint="admin.hospitals_admin")
    apply = request.form.get("mode") == "import"
    get_db().rollback()
    try:
        r = hp.import_trauma(text, apply)
    except ValueError as e:
        return action_response(False, str(e), redirect_endpoint="admin.hospitals_admin")
    except Exception as e:
        get_db().rollback()
        return action_response(False, f"The list could not be read: {e}", redirect_endpoint="admin.hospitals_admin")
    msg = (f"{'Imported' if apply else 'Preview, nothing changed yet'}: {r['matched']} matched ({r['changed']} with a different level), {r['added']} new"
           + (f" ({'; '.join(r['added_names'][:6])}{'...' if len(r['added_names']) > 6 else ''})" if r["added_names"] else "")
           + (f"; services not understood: {', '.join(sorted(set(r['unknown_services']))[:8])}" if r["unknown_services"] else "") + (f"; {len(r['unreadable'])} skipped ({'; '.join(r['unreadable'][:4])}{'...' if len(r['unreadable']) > 4 else ''})" if r["unreadable"] else "") + ".")
    if apply:
        audit("hospitals.trauma_import", detail=f"{r['matched']} matched, {r['added']} added")
    return action_response(True, msg, redirect_endpoint="admin.hospitals_admin")


@bp.route("/hospitals/locate", methods=["POST"])
@permission_required("manage_map_data")
def hospitals_locate():
    from . import hospitals as hp
    only = request.form.get("only") or None
    get_db().rollback()
    done, failed, left = hp.locate_approximate(limit=1 if only else 12, only=only)
    audit("hospitals.locate", target=only or "", detail=f"{done} found, {failed} not found, {left} left")
    msg = f"OpenStreetMap: {done} located, {failed} not found" + (f", {left} more still approximate: press again." if left else ".")
    return action_response(done > 0 or not failed, msg, redirect_endpoint="admin.hospitals_admin")


@bp.route("/hospitals/verify-all", methods=["POST"])
@permission_required("manage_map_data")
def hospitals_verify_all():
    """Check All Positions: starts the Hospital Positions Check in the background (the same job as in Data Updates)."""
    from . import dataupdates as du
    from .settings import get_setting
    if get_setting("hospverify_running"):
        return action_response(False, "The check is already running; reload this page to see how far it is.", redirect_endpoint="admin.hospitals_admin")
    audit("hospitals.verify_all")
    du.run_in_background(current_app._get_current_object(), "hospverify")
    return action_response(True, "Checking every hospital's address with the Census and Photon. It runs in the background (several minutes); reload this page to see the progress and the result.", redirect_endpoint="admin.hospitals_admin")


@bp.route("/hospitals/<fid>/check", methods=["POST"])
@permission_required("manage_map_data")
def hospital_check(fid):
    """Check This Position: the same check as Check All Positions, for one hospital, answered at once."""
    from . import addrlookup, hospitals as hp
    h = hp.get(fid)
    if h is None:
        abort(404)
    get_db().rollback()
    status = addrlookup.apply_check(get_db(), dict(h))
    audit("hospitals.check", target=fid, detail=status)
    say = {"agreed": "Confirmed: the Census and Photon agree, so the position is stored as verified.", "manual": "This position was set by hand and is kept.",
           "single": "Only one finder knew this address. Open Address Lookup to see its answer and decide.", "conflict": "The finders disagree. Open Address Lookup to see both answers on the map and decide.",
           "none": "Neither finder knew this address. Open Address Lookup, try a different wording, or paste a position.", "error": "Neither finder could be asked just now (is Photon running?)."}
    return action_response(status in ("agreed", "manual"), say[status], redirect_endpoint="admin.hospitals_admin")


@bp.route("/hospitals/<fid>/lookup", methods=["POST"])
@permission_required("manage_map_data")
def hospital_lookup(fid):
    """Ask the address finders about one address (the box is pre-filled with the CMS address). Answers come back as JSON for the page to list and map."""
    from . import addrlookup, hospitals as hp
    h = hp.get(fid)
    if h is None:
        abort(404)
    bias = (h["lat"], h["lon"]) if h["lat"] is not None else None
    get_db().rollback()
    cands, errors = addrlookup.lookup(request.form.get("q", ""), bias, request.form.getlist("finders") or None)
    audit("hospitals.lookup", target=fid, detail=f"{len(cands)} candidates, failed: {','.join(errors) or 'none'}")
    return jsonify({"ok": True, "candidates": cands, "errors": errors})


@bp.route("/hospitals/<fid>/position", methods=["POST"])
@permission_required("manage_map_data")
def hospital_position(fid):
    """Use This Position: store a hand-set position (pos_source manual), which imports never overwrite."""
    from . import hospitals as hp
    if hp.get(fid) is None:
        abort(404)
    try:
        lat, lon = float(request.form.get("lat", "")), float(request.form.get("lon", ""))
    except ValueError:
        return action_response(False, "That position could not be read.", redirect_endpoint="admin.hospitals_admin")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return action_response(False, "That position is outside the world.", redirect_endpoint="admin.hospitals_admin")
    d = get_db()
    d.execute("UPDATE hospitals SET lat=%s, lon=%s, pos_source='manual', pos_check='manual', pos_checked=now() WHERE facility_id=%s", (lat, lon, fid))   # a person set it: no longer needs review
    d.commit()
    hp.link_heliports()
    audit("hospitals.position", target=fid, detail=f"{lat:.5f}, {lon:.5f} from {request.form.get('finder', 'a lookup')[:30]}")
    return action_response(True, f"Position saved: {lat:.5f}, {lon:.5f}. Imports keep a hand-set position.", redirect_endpoint="admin.hospitals_admin")


@bp.route("/counties")
@permission_required("manage_map_data")
def counties_admin():
    from . import counties as co, trauma as tr
    media = {r["key"]: dict(r) for r in get_db().execute("SELECT key, website, photo_url, photo_credit, source, edited FROM place_media WHERE kind='county'").fetchall()}
    q = request.args.get("q", "").strip().lower()[:40]
    rows = []
    for c in sorted(co.shapes(), key=lambda c: (c["state"], c["base"])):
        if q and q not in c["name"].lower():
            continue
        t = tr.county_trauma(c["base"], c["state"]) or {}
        rows.append({"slug": c["slug"], "name": c["name"], "state": c["state"], "region": t.get("region", ""), "zone": t.get("zone", ""), **media.get(c["slug"], {})})
    return render_template("admin_counties.html", active="counties", rows=rows, q=q, total=len(rows))


@bp.route("/counties/<slug>/save", methods=["POST"])
@permission_required("manage_map_data")
def county_media_save(slug):
    from . import counties as co, trauma as tr
    if co.get(slug) is None:
        abort(404)
    f = request.form
    err = tr.save_media("county", slug, f.get("website", ""), f.get("photo_url", ""), f.get("photo_credit", ""))
    if err:
        return action_response(False, err, redirect_endpoint="admin.counties_admin")
    audit("counties.media", target=slug, detail="website and picture")
    return action_response(True, "County saved. Lookups keep these entries.", redirect_endpoint="admin.counties_admin")


@bp.route("/hospitals/<fid>/save", methods=["POST"])
@permission_required("manage_map_data")
def hospital_save(fid):
    from . import hospitals as hp
    f = request.form
    def num(k):
        try:
            return float(f.get(k, "")) if f.get(k, "").strip() else None
        except ValueError:
            return "bad"
    lat, lon = num("lat"), num("lon")
    if "bad" in (lat, lon) or (lat is None) != (lon is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180)):
        return action_response(False, "Enter both a latitude and a longitude, or leave both blank.", redirect_endpoint="admin.hospitals_admin")
    if hp.get(fid) is None:
        abort(404)
    err = hp.save_edit(fid, f.get("trauma", ""), f.getlist("services"), f.get("heliport", "").strip(), f.get("notes", "").strip(), lat, lon)
    if err:
        return action_response(False, err, redirect_endpoint="admin.hospitals_admin")
    from . import trauma as tr
    merr = tr.save_media("hospital", fid, f.get("website", ""), f.get("photo_url", ""), f.get("photo_credit", "")) if "website" in f else ""
    if merr:
        return action_response(False, merr, redirect_endpoint="admin.hospitals_admin")
    audit("hospitals.edit", target=fid, detail=f"trauma {f.get('trauma') or 'none'}, heliport {f.get('heliport') or 'none'}")
    km = hp.heliport_distance_km(fid)
    warn = float(cfg("hospital_heliport_warn_km"))
    if km is not None and km > warn:
        return action_response(True, f"Hospital saved, but check the heliport: {f.get('heliport', '').strip()} is {fmt_dist(km, 'km')} from this hospital (the warning distance is {fmt_dist(warn, 'km')}). Either the link or the hospital's position may be wrong.", redirect_endpoint="admin.hospitals_admin")
    return action_response(True, "Hospital saved. The next update keeps these entries.", redirect_endpoint="admin.hospitals_admin")


def _hems_ctx(q=""):
    from . import hems
    rows, total = hems.listing(q)
    return {"st": hems.status(), "rows": rows, "total": total, "q": q, "operators": cfg("hems_operators")}


def _hems_response(ok, msg):
    return action_response(ok, msg, redirect_endpoint="admin.hems_admin", fragment_template="partials/_hems_list.html", fragment_context=_hems_ctx())


@bp.route("/air-ambulance")
@permission_required("manage_map_data")
def hems_admin():
    return render_template("admin_hems.html", active="hems", **_hems_ctx(request.args.get("q", "").strip()[:30]))


@bp.route("/air-ambulance/update", methods=["POST"])
@permission_required("manage_map_data")
def hems_update():
    from . import hems
    get_db().rollback()
    try:
        r = hems.import_registry()
    except Exception as e:
        get_db().rollback()
        return _hems_response(False, f"The aircraft database could not be read: {e}")
    audit("hems.update", detail=f"{r['registry']} by owner name, {r['listed']} from the list")
    return _hems_response(True, f"Updated: {r['registry']} found by owner name, {r['listed']} from your tail number list" + (f", {r['unresolved']} tail numbers not found in the database yet" if r["unresolved"] else "") + ".")


@bp.route("/air-ambulance/tails", methods=["POST"])
@permission_required("manage_map_data")
def hems_tails():
    from . import hems
    text = request.form.get("tails", "")
    if not hems.parse_tails(text):
        return _hems_response(False, "Enter tail numbers such as N123AE, one per line or separated by spaces or commas.")
    added, known = hems.add_tails(text, request.form.get("note", ""), request.form.get("kind", "heli"))
    audit("hems.tails", detail=f"{added} added, {known} already known")
    msg = f"{added} tail number{'' if added == 1 else 's'} added" + (f", {known} already in the list" if known else "") + "."
    try:
        r = hems.import_registry()
        msg += f" Looked up in the aircraft database: {r['listed']} found" + (f", {r['unresolved']} not found" if r["unresolved"] else "") + "."
    except Exception as e:
        get_db().rollback()
        msg += f" They will be looked up at the next update ({e})."
    return _hems_response(True, msg)


@bp.route("/air-ambulance/<path:ident>/<action>", methods=["POST"])
@permission_required("manage_map_data")
def hems_entry(ident, action):
    from . import hems
    if action == "exclude":
        hems.set_excluded(ident, True)
    elif action == "include":
        hems.set_excluded(ident, False)
    elif action == "remove":
        if not hems.remove(ident):
            return _hems_response(False, "Only entries you added can be removed; exclude a registry entry instead.")
    else:
        abort(404)
    audit("hems.entry", target=ident, detail=action)
    return _hems_response(True, {"exclude": "Excluded: it will not be counted as an air ambulance.", "include": "Included again.", "remove": "Removed."}[action])


def _int_ctx(q="", category=""):
    from . import interesting
    rows, total = interesting.listing(q, category)
    return {"st": interesting.status(), "rows": rows, "total": total, "q": q, "category": category, "pa_categories": cfg("interesting_pa_categories")}


def _int_response(ok, msg):
    return action_response(ok, msg, redirect_endpoint="admin.interesting_admin", fragment_template="partials/_interesting_list.html", fragment_context=_int_ctx())


@bp.route("/interesting")
@permission_required("manage_map_data")
def interesting_admin():
    return render_template("admin_interesting.html", active="interesting", **_int_ctx(request.args.get("q", "").strip()[:40], request.args.get("category", "").strip()[:60]))


@bp.route("/interesting/template.csv")
@permission_required("manage_map_data")
def interesting_template():
    from . import interesting
    return Response(interesting.csv_template(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=interesting-aircraft-template.csv"})


@bp.route("/interesting/add", methods=["POST"])
@permission_required("manage_map_data")
def interesting_add():
    from . import interesting
    f = request.form
    err = interesting.add_entry(f.get("hex", ""), f.get("reg", ""), f.get("type", ""), f.get("category", ""), f.get("note", ""), f.get("owner", ""))
    if err:
        return _int_response(False, err)
    audit("interesting.add", target=f.get("hex", "").lower()[:8], detail=f.get("category", "")[:40])
    return _int_response(True, "Added to the Interesting Aircraft list.")


@bp.route("/interesting/import", methods=["POST"])
@permission_required("manage_map_data")
def interesting_import():
    from . import interesting
    up = request.files.get("file")
    text = (up.read(5_000_000).decode("utf-8-sig", "replace") if up and up.filename else request.form.get("pasted", ""))
    if not text.strip():
        return _int_response(False, "Choose a CSV file or paste the list.")
    try:
        entries, skipped = interesting.parse_csv(text)
    except ValueError as e:
        return _int_response(False, str(e))
    if request.form.get("mode") != "import":
        return _int_response(True, f"Preview, nothing changed yet: {len(entries)} aircraft read" + (f", {len(skipped)} lines skipped (no valid hex code: line {', '.join(map(str, skipped[:5]))})" if skipped else "") + ".")
    added, updated, left = interesting.import_entries(entries, "csv")
    audit("interesting.import", detail=f"{added} added, {updated} updated, {left} left alone")
    return _int_response(True, f"Imported: {added} added, {updated} updated" + (f", {left} left alone (excluded or already added by hand)" if left else "") + (f", {len(skipped)} lines skipped" if skipped else "") + ".")


@bp.route("/interesting/planealert", methods=["POST"])
@permission_required("manage_map_data")
def interesting_planealert():
    from . import interesting
    get_db().rollback()
    try:
        r = interesting.import_planealert()
    except Exception as e:
        get_db().rollback()
        return _int_response(False, f"The Plane-Alert list could not be read: {e}")
    audit("interesting.planealert", detail=f"{r['added']} imported of {r['total']}")
    return _int_response(True, f"Updated: {r['added']} aircraft from Plane-Alert ({r['total']} in its list)" + (f", {r['left']} left alone" if r["left"] else "") + ".")


@bp.route("/interesting/<hexid>/<action>", methods=["POST"])
@permission_required("manage_map_data")
def interesting_entry(hexid, action):
    from . import interesting
    if action == "exclude":
        interesting.set_excluded(hexid, True)
    elif action == "include":
        interesting.set_excluded(hexid, False)
    elif action == "remove":
        if not interesting.remove(hexid):
            return _int_response(False, "Only entries you added or imported can be removed; exclude a Plane-Alert entry instead.")
    else:
        abort(404)
    audit("interesting.entry", target=hexid, detail=action)
    return _int_response(True, {"exclude": "Excluded: it will not be flagged as interesting.", "include": "Included again.", "remove": "Removed."}[action])


@bp.route("/feeders/<uuid>/manage", methods=["POST"])
@permission_required("manage_feeders")
def feeder_manage(uuid):
    f = fd.get_feeder(uuid)
    if f is None:
        abort(404)
    action = request.form.get("action")
    d = get_db()
    ok, msg = True, ""
    back = {"list": "admin.feeders_list", "mlat": "admin.mlat_page"}.get(request.form.get("next"))   # the list and MLAT pages have the same buttons and come back to themselves
    if action == "owner":
        owner = request.form.get("owner_id", type=int)
        if owner:
            u = User.get(owner)
            if u is None:
                return action_response(False, "That user does not exist.", redirect_endpoint="admin.feeder_detail", redirect_kwargs={"uuid": uuid})
            d.execute("UPDATE feeders SET owner_id=%s, claimed_at=COALESCE(claimed_at, now()), claim_method=COALESCE(claim_method,'admin'), "
                      "privacy=COALESCE(privacy,%s) WHERE uuid=%s", (u.id, cfg("default_privacy"), uuid))
            msg = f"Owner set to {u.username}."
            audit("feeder.assign", target=uuid, detail=u.username)
        else:
            d.execute("UPDATE feeders SET owner_id=NULL, claimed_at=NULL, claim_method=NULL WHERE uuid=%s", (uuid,))
            msg = "Owner removed. The feeder is now unclaimed."
            audit("feeder.unassign", target=uuid)
        d.execute("INSERT INTO feeder_events (feeder_uuid, event, detail) VALUES (%s,'owner_changed',%s)", (uuid, msg))
        d.commit()
    elif action == "station_id":
        sid = request.form.get("station_id", "").strip()
        if not sid:
            d.execute("UPDATE feeders SET station_id=NULL, station_id_kind=NULL WHERE uuid=%s", (uuid,))
            d.commit()
            msg = "Station ID cleared."
        else:
            try:
                ok, msg = st.set_station_id(uuid, sid, "admin")
            except Exception:
                d.rollback()
                ok, msg = False, "That station ID is already in use."
        if ok:
            audit("feeder.station_id", target=uuid, detail=sid or "(cleared)")
    elif action == "website":
        from urllib.parse import urlparse
        site = request.form.get("website", "").strip()
        if site:
            u = urlparse(site)
            if u.scheme not in ("http", "https") or not u.netloc or len(site) > 200:
                return action_response(False, "Website must be a full http:// or https:// address.", redirect_endpoint="admin.feeder_detail", redirect_kwargs={"uuid": uuid})
        public = bool(request.form.get("website_public")) and bool(site)
        d.execute("UPDATE feeders SET website=%s, website_public=%s WHERE uuid=%s", (site or None, public, uuid))
        d.commit()
        msg = ("Website saved and shown publicly." if public else "Website saved, not shown publicly.") if site else "Website cleared."
        audit("feeder.website", target=uuid, detail=(site or "(cleared)") + (" public" if public else ""))
    elif action == "official":
        order = request.form.get("official_order", "100").strip()
        if not (order.isdigit() and 0 <= int(order) <= 9999):
            return action_response(False, "Pin order must be a whole number from 0 to 9999.", redirect_endpoint="admin.feeder_detail", redirect_kwargs={"uuid": uuid})
        flag = bool(request.form.get("official"))
        d.execute("UPDATE feeders SET official=%s, official_order=%s WHERE uuid=%s", (flag, int(order), uuid))
        d.commit()
        msg = ("Marked as a network station and pinned to the top of the public list." if flag else "No longer pinned as a network station.")
        audit("feeder.official" if flag else "feeder.unofficial", target=uuid, detail=f"order {order}")
    elif action in ("ban", "unban"):
        d.execute("UPDATE feeders SET banned=%s WHERE uuid=%s", (action == "ban", uuid))
        d.execute("INSERT INTO feeder_events (feeder_uuid, event, detail) VALUES (%s,%s,%s)", (uuid, action, current_user.username))
        d.commit()
        msg = "Feeder banned and hidden from public pages." if action == "ban" else "Feeder unbanned."
        audit("feeder." + action, target=uuid)
    else:
        abort(400)
    if back:
        return action_response(ok, msg, redirect_endpoint=back)
    return action_response(ok, msg, redirect_endpoint="admin.feeder_detail", redirect_kwargs={"uuid": uuid})


# ---------------------------------------------------------------- embedded tools

def _tools_response(ok, message):
    return action_response(ok, message, redirect_endpoint="admin.tools_page",
                           fragment_template="partials/_tools_list.html", fragment_context={"tools": tl.list_all()})


@bp.route("/tools")
@permission_required("manage_settings")
def tools_page():
    return render_template("admin_tools.html", active="tools", tools=tl.list_all())


@bp.route("/tools/save", methods=["POST"])
@permission_required("manage_settings")
def tool_save():
    f = request.form
    tool_id = f.get("id", type=int)
    values, err = tl.validate(f.get("slug", ""), f.get("name", ""), f.get("url", ""), f.get("sort_order", "100"), f.get("description", ""))
    if err:
        return _tools_response(False, err)
    values["enabled"] = bool(f.get("enabled"))
    d = get_db()
    try:
        if tool_id:
            d.execute("UPDATE embedded_tools SET slug=%s, name=%s, url=%s, sort_order=%s, enabled=%s, description=%s WHERE id=%s",
                      (values["slug"], values["name"], values["url"], values["sort_order"], values["enabled"], values["description"], tool_id))
        else:
            d.execute("INSERT INTO embedded_tools (slug, name, url, sort_order, enabled, description) VALUES (%s,%s,%s,%s,%s,%s)",
                      (values["slug"], values["name"], values["url"], values["sort_order"], values["enabled"], values["description"]))
        d.commit()
    except Exception as e:
        d.rollback()
        if "unique" in str(e).lower():
            return _tools_response(False, "Another tool already uses that short name.")
        raise
    audit("tool.save", target=values["slug"], detail=values["url"])
    return _tools_response(True, f"{values['name']} saved.")


@bp.route("/tools/<int:tool_id>/delete", methods=["POST"])
@permission_required("manage_settings")
def tool_delete(tool_id):
    d = get_db()
    row = d.execute("DELETE FROM embedded_tools WHERE id=%s RETURNING slug, name", (tool_id,)).fetchone()
    d.commit()
    if row is None:
        return _tools_response(False, "That tool no longer exists.")
    audit("tool.delete", target=row["slug"])
    return _tools_response(True, f"{row['name']} deleted.")


# ---------------------------------------------------------------- map layers

def _layers_response(ok, message):
    return action_response(ok, message, redirect_endpoint="admin.layers_page",
                           fragment_template="partials/_layers_list.html", fragment_context={"layers": ml.list_all(), "kinds": ml.KINDS})


@bp.route("/layers")
@permission_required("manage_map_data")
def layers_page():
    return render_template("admin_layers.html", active="layers", layers=ml.list_all(), kinds=ml.KINDS)


@bp.route("/layers/save", methods=["POST"])
@permission_required("manage_map_data")
def layer_save():
    f = request.form
    layer_id = f.get("id", type=int)
    v, err = ml.validate(f)
    if err:
        return _layers_response(False, err)
    d = get_db()
    cols = ["slug", "name", "grp", "kind", "url", "options", "attribution", "opacity", "min_zoom", "max_zoom", "color", "sort_order", "refresh_minutes", "enabled", "default_on", "is_base", "wx_group", "description"]
    try:
        if layer_id:
            d.execute("UPDATE map_layers SET " + ", ".join(f"{c}=%s" for c in cols) + " WHERE id=%s", [v[c] for c in cols] + [layer_id])
        else:
            d.execute(f"INSERT INTO map_layers ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", [v[c] for c in cols])
        d.commit()
    except Exception as e:
        d.rollback()
        if "unique" in str(e).lower():
            return _layers_response(False, "Another layer already uses that short name.")
        raise
    audit("layer.save", target=v["slug"], detail=v["url"][:200])
    return _layers_response(True, f"{v['name']} saved.")


@bp.route("/layers/<int:layer_id>/delete", methods=["POST"])
@permission_required("manage_map_data")
def layer_delete(layer_id):
    d = get_db()
    row = d.execute("DELETE FROM map_layers WHERE id=%s RETURNING slug, name", (layer_id,)).fetchone()
    d.commit()
    if not row:
        return _layers_response(False, "That layer no longer exists.")
    audit("layer.delete", target=row["slug"])
    return _layers_response(True, f"{row['name']} deleted.")


# ---------------------------------------------------------------- site contacts

def _contacts_response(ok, message):
    return action_response(ok, message, redirect_endpoint="admin.contacts_page",
                           fragment_template="partials/_contacts_list.html", fragment_context={"contacts": _all_contacts()})


def _all_contacts():
    return get_db().execute("SELECT * FROM site_contacts ORDER BY sort_order, lower(name)").fetchall()


@bp.route("/contacts")
@permission_required("manage_settings")
def contacts_page():
    return render_template("admin_contacts.html", active="contacts", contacts=_all_contacts())


@bp.route("/contacts/save", methods=["POST"])
@permission_required("manage_settings")
def contact_save():
    import re
    from urllib.parse import urlparse
    f = request.form
    name, role, notes = f.get("name", "").strip(), f.get("role", "").strip(), f.get("notes", "").strip()
    email, url = f.get("email", "").strip(), f.get("url", "").strip()
    if not name or len(name) > 80:
        return _contacts_response(False, "Enter a name of up to 80 characters.")
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return _contacts_response(False, "Enter a valid email address, or leave it blank.")
    if url and (urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc):
        return _contacts_response(False, "The web address must start with http:// or https://.")
    if not (email or url or notes):
        return _contacts_response(False, "Add an email address, a web address or a note so visitors have a way to reach this person.")
    try:
        order = int(f.get("sort_order", "100"))
    except ValueError:
        return _contacts_response(False, "Order must be a number.")
    d = get_db()
    cid = f.get("id", type=int)
    vals = (name, role[:120], email, url, notes[:300], order, bool(f.get("shown")))
    if cid:
        d.execute("UPDATE site_contacts SET name=%s, role=%s, email=%s, url=%s, notes=%s, sort_order=%s, shown=%s WHERE id=%s", vals + (cid,))
    else:
        d.execute("INSERT INTO site_contacts (name, role, email, url, notes, sort_order, shown) VALUES (%s,%s,%s,%s,%s,%s,%s)", vals)
    d.commit()
    audit("contact.save", target=name)
    return _contacts_response(True, f"{name} saved.")


@bp.route("/contacts/<int:contact_id>/delete", methods=["POST"])
@permission_required("manage_settings")
def contact_delete(contact_id):
    d = get_db()
    row = d.execute("DELETE FROM site_contacts WHERE id=%s RETURNING name", (contact_id,)).fetchone()
    d.commit()
    if not row:
        return _contacts_response(False, "That contact no longer exists.")
    audit("contact.delete", target=row["name"])
    return _contacts_response(True, f"{row['name']} deleted.")


# ---------------------------------------------------------------- security

@bp.route("/security/sign-out-all", methods=["POST"])
@permission_required("manage_settings")
def sign_out_all():
    from . import security
    security.sign_out_everyone()
    audit("security.sign_out_all", detail="all sessions started before now ended")
    return action_response(True, "Everyone else has been signed out. They must sign in again.", redirect_endpoint="admin.settings_page")


# ---------------------------------------------------------------- updates

def _updater_configured():
    import os
    return bool(os.environ.get("WATCHTOWER_TOKEN"))


@bp.route("/updates")
@permission_required("manage_settings")
def updates():
    from . import build_info
    from .update_check import read_state
    from . import livecontent_views
    return render_template("admin_updates.html", active="updates", info=build_info.read(), update=read_state(), updater=_updater_configured(), **livecontent_views.context())


@bp.route("/updates/check", methods=["POST"])
@permission_required("manage_settings")
def updates_check():
    from .update_check import maybe_check
    state = maybe_check(force=True)
    audit("update.check", detail=str(state.get("latest_stable_version") or state.get("error") or state.get("note")))
    return redirect(url_for("admin.updates"))


def _apply_stack():
    """Ask the hub's stack updater to apply the compose files it carries (new or changed services). Returns a sentence for the message, or ''."""
    import json, os, urllib.request
    token, base = os.environ.get("STACK_TOKEN"), os.environ.get("STACK_URL")
    if not (token and base):
        return ""
    req = urllib.request.Request(base.rstrip("/") + "/apply", method="POST", headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.load(r).get("results", {})
    except Exception:
        return " The stack updater could not be reached, so new services were not checked."
    bad = [k for k, v in res.items() if not v.get("ok")]
    return f" The stack updater could not apply: {', '.join(bad)}." if bad else " Services are up to date."


@bp.route("/updates/now", methods=["POST"])
@permission_required("manage_settings")
def updates_now():
    """Ask Watchtower (the hub's update service) to look for and apply new images right now."""
    import os, urllib.error, urllib.request
    from .settings import cfg_int
    if not _updater_configured():
        return action_response(False, "The update service is not set up on this server.", redirect_endpoint="admin.updates")
    url = os.environ.get("WATCHTOWER_URL", "http://watchtower:8080").rstrip("/") + "/v1/update"
    req = urllib.request.Request(url, method="POST", headers={"Authorization": "Bearer " + os.environ["WATCHTOWER_TOKEN"]})
    audit("update.now")
    stack_note = _apply_stack()
    try:
        with urllib.request.urlopen(req, timeout=cfg_int("update_now_wait_seconds")):
            pass
        return action_response(True, "The update check finished. If a newer version was found it has been installed; the version shown on this page is the one now running." + stack_note, redirect_endpoint="admin.updates")
    except urllib.error.HTTPError as e:
        return action_response(False, f"The update service refused the request (error {e.code}). Check the update token in the portal's .env file.", redirect_endpoint="admin.updates")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return action_response(True, "The update has started and the portal is restarting. Reload this page in a minute to see the new version.", redirect_endpoint="admin.updates")


def nudge_matrix():
    """Blocks, friendships and disabled accounts feed the Matrix server (ignore lists, who may start a chat, locked accounts): ask for a sync soon."""
    from .matrix import nudge_if_on
    nudge_if_on()
