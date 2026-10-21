"""An administrator opens a person's account and changes what that person can change themselves: profile, email address, display choices, landing page,
alert webhook and alert rules, two-factor, sign-ins. Never the password: it is stored only as a hash, nothing here reads it, and the only password action
is to set a new temporary one (shown once, and the person must change it at the next sign-in). Every change is written to the audit log."""
import json
import re
import secrets

from flask import Blueprint, abort, render_template, request
from flask_login import current_user

from . import alerts as al
from . import webhooks
from .action_response import action_response
from .audit import audit
from .db import get_db
from .landing import allowed as landing_allowed
from .models import User
from .admin import permission_required
from .settings import cfg, cfg_int
from .vault import seal, unseal

bp = Blueprint("useredit", __name__, url_prefix="/admin/users")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PREF_CHOICES = {"theme": __import__("app.themes", fromlist=["x"]).THEMES.keys() | {"auto", "site", "normal"}, "deco": {"", "0", "1"}, "themeV": {"2"}, **{"u_" + _a: {"", "imperial", "aero", "metric"} for _a in ("aircraft", "weather", "sondes", "aprs", "acars", "mesh", "repeaters", "places", "network", "home")}, "hour": {"auto", "12", "24"}, "tz": {"browser", "site", "utc"}, "pageWidth": {"", "limited", "full"}}


def _target(user_id):
    t = User.get(user_id)
    if t is None:
        abort(404)
    return t


def _blocked(target):
    """Only a Super Admin may change another Super Admin's account: otherwise an ordinary user manager could take over the top account."""
    if target.is_super_admin and not current_user.is_super_admin and target.id != current_user.id:
        return "Only a Super Admin can change a Super Admin's account."
    return None


def _prefs(user_id):
    row = get_db().execute("SELECT prefs FROM users WHERE id=%s", (user_id,)).fetchone()
    try:
        return json.loads(row["prefs"]) if row and row["prefs"] else {}
    except ValueError:
        return {}


def context(target):
    d = get_db()
    u = d.execute("SELECT id, username, email, email_verified, display_name, callsign, disabled, must_change_password, failed_attempts, locked_until, created_at, last_login, "
                  "totp_enabled, webhook_kind, (webhook_url <> '') AS has_webhook, webhook_url, (SELECT COUNT(*) FROM passkeys WHERE user_id = users.id) AS passkey_count, (SELECT COUNT(*) FROM google_links WHERE user_id = users.id) AS google_count FROM users WHERE id=%s", (target.id,)).fetchone()   # never password_hash
    u = dict(u)
    url = unseal(u.pop("webhook_url")) if u["has_webhook"] else ""
    u["webhook_shown"] = (re.sub(r"^(https?://[^/]+)/.*$", r"\1/...", url) if url else "")
    prefs = _prefs(target.id)
    rules = d.execute("SELECT r.*, (SELECT COUNT(*) FROM alert_events e WHERE e.rule_id = r.id) AS event_count FROM alert_rules r WHERE user_id=%s ORDER BY created_at", (target.id,)).fetchall()
    sessions = d.execute("SELECT COUNT(*) AS n FROM user_sessions WHERE user_id=%s AND NOT ended AND last_seen > now() - interval '30 days'", (target.id,)).fetchone()["n"]
    feeders = d.execute("SELECT uuid, station_id, name, online FROM feeders WHERE owner_id=%s ORDER BY first_seen", (target.id,)).fetchall()
    roles = [r["name"] for r in target.roles]
    return {"u": u, "prefs": prefs, "rules": rules, "sessions": sessions, "feeders": feeders, "roles": roles, "kinds": al.KINDS, "needs_value": sorted(al.NEEDS_VALUE),
            "webhook_kinds": webhooks.KINDS, "landing_choices": landing_allowed(target), "can_edit": _blocked(target) is None, "limit": cfg("max_rules_per_user"), "active": "users"}


def _respond(target, ok, message):
    return action_response(ok, message, redirect_endpoint="useredit.detail", redirect_kwargs={"user_id": target.id},
                           fragment_template="partials/_admin_user_body.html", fragment_context=context(target))


@bp.route("/<int:user_id>")
@permission_required("manage_users")
def detail(user_id):
    target = _target(user_id)
    return render_template("admin_user.html", **context(target))


@bp.route("/<int:user_id>/profile", methods=["POST"])
@permission_required("manage_users")
def profile(user_id):
    target = _target(user_id)
    if (m := _blocked(target)):
        return _respond(target, False, m)
    f = request.form
    display = f.get("display_name", "").strip()[:60] or None
    callsign = f.get("callsign", "").strip().upper()
    email = f.get("email", "").strip()
    from . import stations as st
    if callsign and not st.valid_callsign(callsign):
        return _respond(target, False, "Enter a valid amateur radio callsign such as KD5VCF, or leave it blank.")
    if not EMAIL_RE.match(email) or len(email) > 254:
        return _respond(target, False, "Enter a valid email address.")
    d = get_db()
    if d.execute("SELECT 1 FROM users WHERE lower(email)=lower(%s) AND id<>%s", (email, target.id)).fetchone():
        return _respond(target, False, "Another account already uses that email address.")
    old = d.execute("SELECT email, display_name, callsign, email_verified FROM users WHERE id=%s", (target.id,)).fetchone()
    verified = bool(f.get("email_verified"))
    d.execute("UPDATE users SET display_name=%s, callsign=%s, email=%s, email_verified=%s WHERE id=%s", (display, callsign or None, email, verified, target.id))
    d.commit()
    changes = [x for x in (f"email {old['email']} -> {email}" if old["email"].lower() != email.lower() else "", "display name" if (old["display_name"] or None) != display else "",
                           "callsign" if (old["callsign"] or None) != (callsign or None) else "", "email verified flag" if old["email_verified"] != verified else "") if x]
    audit("user.edit_profile", target=target.username, detail="; ".join(changes) or "no change")
    return _respond(target, True, "Saved." if changes else "No changes to save.")


@bp.route("/<int:user_id>/prefs", methods=["POST"])
@permission_required("manage_users")
def prefs(user_id):
    target = _target(user_id)
    if (m := _blocked(target)):
        return _respond(target, False, m)
    f = request.form
    prefs = _prefs(target.id)
    for k, ok in PREF_CHOICES.items():
        v = f.get(k, "")
        if v == "":
            prefs.pop(k, None)   # a blank choice means "the site default": nothing is stored
        elif v in ok:
            prefs[k] = v
        else:
            return _respond(target, False, f"Choose a valid {k}.")
    land = f.get("landing", "")
    if land and land not in {p for p, _ in landing_allowed(target)}:
        return _respond(target, False, "That landing page is not available to this person.")
    if land:
        prefs["landing"] = land
    else:
        prefs.pop("landing", None)
    d = get_db()
    d.execute("UPDATE users SET prefs=%s WHERE id=%s", (json.dumps(prefs), target.id))
    d.commit()
    audit("user.edit_prefs", target=target.username, detail=", ".join(f"{k}={prefs[k]}" for k in sorted(prefs) if k in ("theme", "hour", "tz", "landing")))
    return _respond(target, True, "Display choices saved.")


@bp.route("/<int:user_id>/webhook", methods=["POST"])
@permission_required("manage_users")
def webhook(user_id):
    target = _target(user_id)
    if (m := _blocked(target)):
        return _respond(target, False, m)
    kind = request.form.get("webhook_kind", "json")
    if kind not in webhooks.KINDS:
        return _respond(target, False, "Choose the kind of service.")
    d = get_db()
    if request.form.get("clear"):
        d.execute("UPDATE users SET webhook_url='', webhook_kind=%s WHERE id=%s", (kind, target.id))
        d.commit()
        audit("user.edit_webhook", target=target.username, detail="cleared")
        return _respond(target, True, "Webhook delivery is off for this person.")
    raw = request.form.get("webhook_url", "").strip()
    if not raw:
        d.execute("UPDATE users SET webhook_kind=%s WHERE id=%s", (kind, target.id))
        d.commit()
        return _respond(target, True, "Kind saved; the address was left as it is.")
    url, err = webhooks.check_url(raw)
    if err:
        return _respond(target, False, err)
    d.execute("UPDATE users SET webhook_url=%s, webhook_kind=%s WHERE id=%s", (seal(url), kind, target.id))
    d.commit()
    audit("user.edit_webhook", target=target.username, detail="address set")
    return _respond(target, True, "Webhook address saved.")


@bp.route("/<int:user_id>/alerts/save", methods=["POST"])
@permission_required("manage_users")
def alert_save(user_id):
    target = _target(user_id)
    if (m := _blocked(target)):
        return _respond(target, False, m)
    values, err = al.validate_rule(request.form, cfg_int("alert_default_cooldown_minutes"))
    if err:
        return _respond(target, False, err)
    d = get_db()
    rule_id = request.form.get("id", type=int)
    cols = list(values)
    if rule_id:
        if not d.execute("SELECT 1 FROM alert_rules WHERE id=%s AND user_id=%s", (rule_id, target.id)).fetchone():
            abort(404)
        d.execute("UPDATE alert_rules SET " + ", ".join(f"{c}=%s" for c in cols) + " WHERE id=%s", [values[c] for c in cols] + [rule_id])
    else:
        if d.execute("SELECT COUNT(*) AS n FROM alert_rules WHERE user_id=%s", (target.id,)).fetchone()["n"] >= int(cfg("max_rules_per_user")):
            return _respond(target, False, f"The limit is {cfg('max_rules_per_user')} alert rules per person.")
        d.execute("INSERT INTO alert_rules (user_id, " + ", ".join(cols) + ") VALUES (%s, " + ", ".join(["%s"] * len(cols)) + ")", [target.id] + [values[c] for c in cols])
    d.commit()
    audit("user.edit_alert", target=target.username, detail=f"saved \"{values['name']}\"")
    return _respond(target, True, f"Alert \"{values['name']}\" saved.")


@bp.route("/<int:user_id>/alerts/<int:rule_id>/<action>", methods=["POST"])
@permission_required("manage_users")
def alert_action(user_id, rule_id, action):
    target = _target(user_id)
    if (m := _blocked(target)):
        return _respond(target, False, m)
    d = get_db()
    if action == "toggle":
        row = d.execute("UPDATE alert_rules SET enabled = NOT enabled WHERE id=%s AND user_id=%s RETURNING name, enabled", (rule_id, target.id)).fetchone()
        msg = f"Alert \"{row['name']}\" is now {'on' if row and row['enabled'] else 'off'}." if row else ""
    elif action == "delete":
        row = d.execute("DELETE FROM alert_rules WHERE id=%s AND user_id=%s RETURNING name", (rule_id, target.id)).fetchone()
        msg = f"Alert \"{row['name']}\" deleted." if row else ""
    else:
        abort(404)
    if row is None:
        abort(404)
    d.commit()
    audit("user.edit_alert", target=target.username, detail=f"{action} \"{row['name']}\"")
    return _respond(target, True, msg)


@bp.route("/<int:user_id>/security", methods=["POST"])
@permission_required("manage_users")
def security(user_id):
    target = _target(user_id)
    if (m := _blocked(target)):
        return _respond(target, False, m)
    action = request.form.get("action")
    d = get_db()
    if action == "temp_password":
        if target.id == current_user.id:
            return _respond(target, False, "Change your own password under My Account > Password.")
        temp = secrets.token_urlsafe(12).replace("-", "x").replace("_", "y")
        target.set_password(temp, must_change=True)
        audit("user.reset_password", target=target.username)
        return _respond(target, True, f"New temporary password for {target.username}: {temp}. It is shown only once, and they must change it at their next sign-in. Their old password is gone and was never shown.")
    if action == "force_change":
        d.execute("UPDATE users SET must_change_password=TRUE WHERE id=%s", (target.id,))
        d.commit()
        audit("user.force_password_change", target=target.username)
        return _respond(target, True, "They must choose a new password at their next sign-in.")
    if action == "unlock":
        d.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=%s", (target.id,))
        d.commit()
        audit("user.unlock", target=target.username)
        return _respond(target, True, "The account is unlocked.")
    if action == "reset_2fa":
        d.execute("UPDATE users SET totp_secret='', totp_enabled=FALSE, totp_last=0 WHERE id=%s", (target.id,))
        d.execute("DELETE FROM recovery_codes WHERE user_id=%s", (target.id,))
        d.commit()
        audit("user.reset_2fa", target=target.username)
        return _respond(target, True, "Two-factor sign-in is off. They can set it up again under My Account > Two-Factor.")
    if action == "remove_passkeys":
        n = d.execute("DELETE FROM passkeys WHERE user_id=%s", (target.id,)).rowcount
        d.commit()
        audit("user.remove_passkeys", target=target.username, detail=f"{n} passkeys removed")
        return _respond(target, True, f"{n} passkey{'' if n == 1 else 's'} removed. They can add new ones under My Account > Passkeys.")
    if action == "remove_google":
        n = d.execute("DELETE FROM google_links WHERE user_id=%s", (target.id,)).rowcount
        d.commit()
        audit("user.remove_google", target=target.username, detail=f"{n} Google link removed")
        return _respond(target, True, "The Google link is removed." if n else "No Google account was linked.")
    if action == "sign_out_all":
        n = d.execute("UPDATE user_sessions SET ended=TRUE WHERE user_id=%s AND NOT ended", (target.id,)).rowcount
        d.commit()
        audit("user.sign_out_all", target=target.username, detail=f"{n} sign-ins ended")
        return _respond(target, True, f"Ended {n} sign-in{'' if n == 1 else 's'}.")
    abort(400)
