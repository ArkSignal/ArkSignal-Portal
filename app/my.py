"""The signed-in user's own pages: profile, claiming feeders and editing station details."""
import json
import time
import urllib.request

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from psycopg.errors import UniqueViolation

from .clientip import client_ip
from . import alerts as al
from . import rangestats
from . import build_info
from . import feeders as fd
from . import stations as st
from .action_response import action_response
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("my", __name__, url_prefix="/my")


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        display = request.form.get("display_name", "").strip()[:60] or None
        callsign = request.form.get("callsign", "").strip().upper()
        if callsign and not st.valid_callsign(callsign):
            flash("Enter a valid amateur radio callsign such as KD5VCF, or leave it blank.", "error")
        else:
            d = get_db()
            d.execute("UPDATE users SET display_name=%s, callsign=%s WHERE id=%s", (display, callsign or None, current_user.id))
            from .landing import allowed
            import json
            land = request.form.get("landing", "")
            row = d.execute("SELECT prefs FROM users WHERE id=%s", (current_user.id,)).fetchone()
            try:
                prefs = json.loads(row["prefs"]) if row and row["prefs"] else {}
            except ValueError:
                prefs = {}
            if land in {p for p, _ in allowed(current_user)}:
                prefs["landing"] = land
            else:
                prefs.pop("landing", None)
            d.execute("UPDATE users SET prefs=%s WHERE id=%s", (json.dumps(prefs), current_user.id))
            d.commit()
            audit("user.profile_update", target=current_user.username)
            flash("Profile saved.", "success")
            return redirect(url_for("my.profile"))
    from .landing import allowed, target_for
    import json
    row = get_db().execute("SELECT prefs FROM users WHERE id=%s", (current_user.id,)).fetchone()
    try:
        mine = (json.loads(row["prefs"]) if row and row["prefs"] else {}).get("landing", "")
    except ValueError:
        mine = ""
    return render_template("my_profile.html", delete_enabled=cfg("account_delete_enabled") == "1", landing_choices=allowed(current_user), landing_mine=mine,
                           landing_site=target_for(current_user) if not mine else "")


@bp.route("/profile/email", methods=["POST"])
@login_required
def change_email():
    """Change the sign-in email address. The current password is required, since the address is how an account is recovered."""
    import time
    from .auth import EMAIL_RE
    from .models import User
    new = request.form.get("new_email", "").strip()
    if not current_user.check_password(request.form.get("current_password", "")):
        time.sleep(0.5)    # same small delay as a wrong sign-in
        audit("user.email_change_failed", target=current_user.username, detail="wrong password")
        flash("The current password is not correct.", "error")
    elif not EMAIL_RE.match(new) or len(new) > 254:
        flash("Enter a valid email address.", "error")
    elif new.lower() == (current_user.email or "").lower():
        flash("That is already your email address.", "error")
    elif User.email_taken(new):
        flash("An account with that email address already exists.", "error")
    else:
        old = current_user.email
        d = get_db()
        d.execute("UPDATE users SET email=%s WHERE id=%s", (new, current_user.id))
        d.commit()
        audit("user.email_change", target=current_user.username, detail=f"{old} -> {new}")
        flash("Your email address has been changed.", "success")
    return redirect(url_for("my.profile"))


@bp.route("/profile/delete", methods=["POST"])
@login_required
def delete_account():
    """Delete the signed-in account. Needs the password and the word DELETE; the last administrator cannot be removed."""
    import time
    from . import accounts
    from flask_login import logout_user
    if cfg("account_delete_enabled") != "1":
        abort(404)
    if not current_user.check_password(request.form.get("current_password", "")):
        time.sleep(0.5)
        audit("user.delete_failed", target=current_user.username, detail="wrong password")
        flash("The current password is not correct, so nothing was deleted.", "error")
    elif request.form.get("confirm_text", "").strip() != "DELETE":
        flash("Type DELETE in capital letters to confirm.", "error")
    elif accounts.last_super_admin(current_user):
        flash("You are the last administrator with full access, so this account cannot be deleted. Give another account the Admin role first.", "error")
    else:
        user = current_user._get_current_object()
        name, stations = user.username, get_db().execute("SELECT COUNT(*) AS n FROM feeders WHERE owner_id=%s", (user.id,)).fetchone()["n"]
        logout_user()
        accounts.delete_account(user)
        audit("user.delete", target=name, actor_name=name, detail=f"{stations} station(s) released")
        flash("Your account has been deleted.", "info")
        return redirect(url_for("auth.login"))
    return redirect(url_for("my.profile"))


@bp.route("/claim", methods=["GET", "POST"])
@login_required
def claim():
    ip = client_ip()
    if request.method == "POST":
        uuid = request.form.get("uuid", "").strip().lower()
        ok, msg = st.claim(uuid, current_user, ip)
        if ok:
            audit("feeder.claim", target=uuid)
            flash(msg, "success")
            return redirect(url_for("my.feeder", uuid=uuid))
        flash(msg, "error")
    return render_template("my.html", feeders=st.my_feeders(current_user.id), candidates=st.claim_candidates(ip), ip=ip, paste_days=cfg_int("claim_paste_days"), claim_open=True)


def _owned_or_404(uuid):
    """The member's own feeder; an administrator who may manage feeders can open anyone's claimed feeder with the same pages (to help someone), recorded in the audit log under their name."""
    f = st.get_owned(uuid, current_user)
    if f is None and current_user.has_permission("manage_feeders"):
        f = get_db().execute("SELECT * FROM feeders WHERE uuid=%s AND owner_id IS NOT NULL", (uuid,)).fetchone()
    if f is None:
        abort(404)
    return f


def _managing(f):
    """The owner's name when an administrator looks at someone else's feeder, else empty."""
    if f["owner_id"] == current_user.id:
        return ""
    u = get_db().execute("SELECT username, display_name FROM users WHERE id=%s", (f["owner_id"],)).fetchone()
    return (u["display_name"] or u["username"]) if u else "another member"


def _station_options(f):
    cs = st.callsign_candidate(current_user.callsign, f["uuid"])
    sid, airport = st.airport_candidate(f["lat"], f["lon"], f["uuid"])
    return {"callsign": cs, "airport": sid, "airport_row": airport}


@bp.route("/feeders/<uuid>")
@login_required
def feeder(uuid):
    f = _owned_or_404(uuid)
    period = request.args.get("period", "24h")
    if period not in fd.PERIODS:
        period = "24h"
    points, uptime = fd.series(uuid, period)
    wxrow = get_db().execute("SELECT id, public, last_report FROM wx_stations WHERE feeder_uuid=%s", (uuid,)).fetchone() if cfg("wx_enabled") == "1" else None
    return render_template("my_feeder.html", managing=_managing(f), wx=wxrow, wx_on=cfg("wx_enabled") == "1", f=f, privacy=st.PRIVACY, setup_fields=st.SETUP_FIELDS,
                           opts=_station_options(f), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"),
                           chart=fd.line_chart(points, period), uptime=uptime, period=period, periods=fd.PERIODS,
                           latest=points[-1] if points else None, events=fd.feeder_events(uuid, 30), mlat=__import__("app.mlat", fromlist=["x"]).station_state(uuid),
                           rng=rangestats.station(uuid, 30), rng_chart=rangestats.polar_svg(rangestats.station(uuid, 30)["sectors"]))


@bp.route("/feeders/<uuid>/save", methods=["POST"])
@login_required
def feeder_save(uuid):
    _owned_or_404(uuid)
    values, err = st.parse_details(request.form)
    if err:
        return action_response(False, err, redirect_endpoint="my.feeder", redirect_kwargs={"uuid": uuid})
    st.save_details(uuid, values)
    audit("feeder.update", target=uuid)
    return action_response(True, "Station details saved.", redirect_endpoint="my.feeder", redirect_kwargs={"uuid": uuid})


@bp.route("/feeders/<uuid>/station-id", methods=["POST"])
@login_required
def feeder_station_id(uuid):
    f = _owned_or_404(uuid)
    kind = request.form.get("kind")
    opts = _station_options(f)
    if kind == "callsign":
        sid = opts["callsign"]
        if not sid:
            flash("Add your amateur radio callsign to your profile first.", "error")
            return redirect(url_for("my.feeder", uuid=uuid))
    elif kind == "airport":
        sid = opts["airport"]
        if not sid:
            flash("Save the station's location first so the nearest airport can be found.", "error")
            return redirect(url_for("my.feeder", uuid=uuid))
    else:
        abort(400)
    try:
        ok, msg = st.set_station_id(uuid, sid, kind)
    except UniqueViolation:
        get_db().rollback()
        ok, msg = False, "That station ID was just taken. Please try again."
    if ok:
        audit("feeder.station_id", target=uuid, detail=sid)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("my.feeder", uuid=uuid))


@bp.route("/feeders/<uuid>/release", methods=["POST"])
@login_required
def feeder_release(uuid):
    f = _owned_or_404(uuid)
    if st.release(uuid, current_user, force=f["owner_id"] != current_user.id):
        audit("feeder.release", target=uuid)
        flash("Feeder released. It can be claimed again by its owner at any time.", "success")
    return redirect(url_for("auth.dashboard") if f["owner_id"] == current_user.id else url_for("admin.feeders_list"))


@bp.route("/elevation")
@login_required
def elevation():
    """Ground elevation for a point, from Open-Meteo's free elevation API."""
    try:
        lat, lon = float(request.args["lat"]), float(request.args["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except (KeyError, ValueError):
        return jsonify(ok=False, message="Enter a valid latitude and longitude first."), 400
    try:
        url = f"{cfg('elevation_api_url')}?latitude={lat:.5f}&longitude={lon:.5f}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "adsb-portal"}), timeout=6) as r:
            meters = json.loads(r.read(10000))["elevation"][0]
    except Exception:
        return jsonify(ok=False, message="The elevation service did not respond. Enter the ground elevation by hand."), 502
    return jsonify(ok=True, feet=round(meters * 3.28084), meters=meters)


@bp.route("/about")
def about_moved():
    """The About page is public now (/about); old bookmarks land there."""
    return redirect("/about", code=301)


# ---------------------------------------------------------------- alerts

def _alerts_context():
    d = get_db()
    rules = d.execute("SELECT r.*, (SELECT COUNT(*) FROM alert_events e WHERE e.rule_id = r.id) AS event_count "
                      "FROM alert_rules r WHERE user_id=%s ORDER BY created_at", (current_user.id,)).fetchall()
    events = d.execute("SELECT e.*, r.name AS rule_name FROM alert_events e JOIN alert_rules r ON r.id = e.rule_id "
                       "WHERE e.user_id=%s ORDER BY e.ts DESC LIMIT 50", (current_user.id,)).fetchall()
    from . import webhooks
    from .vault import unseal
    wh = d.execute("SELECT webhook_url, webhook_kind FROM users WHERE id=%s", (current_user.id,)).fetchone()
    wh = {"webhook_url": unseal(wh["webhook_url"]), "webhook_kind": wh["webhook_kind"]}
    return {"rules": rules, "events": events, "kinds": al.KINDS, "needs_value": sorted(al.NEEDS_VALUE),
            "webhook": wh, "webhook_kinds": webhooks.KINDS, "cfg_limits": {"webhook_hour": cfg("webhook_per_hour"), "webhook_day": cfg("webhook_per_day")},
            "limit": cfg("max_rules_per_user"), "default_cooldown": cfg_int("alert_default_cooldown_minutes")}


def _alerts_response(ok, message):
    return action_response(ok, message, redirect_endpoint="my.alerts",
                           fragment_template="partials/_alerts_list.html", fragment_context=_alerts_context())


@bp.route("/alerts")
@login_required
def alerts():
    from .mailer import smtp_configured
    hour, day, waiting = al.email_usage(get_db(), current_user.id)
    limits = {"per_hour": cfg_int("alert_email_per_hour"), "per_day": cfg_int("alert_email_per_day"),
              "combine": cfg_int("alert_email_combine_minutes"), "hour": hour, "day": day, "waiting": waiting}
    d = get_db()
    since = d.execute("SELECT alerts_seen_at FROM users WHERE id=%s", (current_user.id,)).fetchone()["alerts_seen_at"]
    d.execute("UPDATE users SET alerts_seen_at=now() WHERE id=%s", (current_user.id,))
    d.commit()
    return render_template("my_alerts.html", email_ready=smtp_configured(), limits=limits, since=since, **_alerts_context())


@bp.route("/alerts/count.json")
@login_required
def alerts_count():
    n = get_db().execute("SELECT COUNT(*) AS n FROM alert_events e JOIN users u ON u.id = e.user_id WHERE e.user_id=%s AND e.ts > u.alerts_seen_at",
                         (current_user.id,)).fetchone()["n"]
    resp = jsonify({"unseen": n})
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _sessions_context():
    from flask import session
    from . import security
    rows = get_db().execute("SELECT * FROM user_sessions WHERE user_id=%s ORDER BY ended, last_seen DESC LIMIT 40", (current_user.id,)).fetchall()
    max_h, idle = security.limits(current_user)
    out = []
    for r in rows:
        out.append(dict(r, device=security.describe_ua(r["ua"]), current=(r["sid"] == session.get("sid"))))
    return {"sessions": out, "max_h": max_h, "idle": idle}


def _two_factor_context(**extra):
    from . import totp
    d = get_db()
    row = d.execute("SELECT totp_enabled FROM users WHERE id=%s", (current_user.id,)).fetchone()
    left = d.execute("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id=%s AND used_at IS NULL", (current_user.id,)).fetchone()["n"]
    return dict({"enabled": row["totp_enabled"], "codes_left": left, "required": cfg("require_2fa_admins") == "1" and current_user.has_admin_access}, **extra)


@bp.route("/two-factor", methods=["GET", "POST"])
@login_required
def two_factor():
    """Set up, check and turn off sign-in with an authenticator app."""
    from . import totp
    from flask import session
    d = get_db()
    ctx = _two_factor_context()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "start" and not ctx["enabled"]:
            session["pending_totp"] = {"s": totp.new_secret(), "at": time.time()}
            return redirect(url_for("my.two_factor"))
        if action == "confirm" and not ctx["enabled"]:
            pend = session.get("pending_totp") or {}
            secret = pend.get("s") if time.time() - pend.get("at", 0) < 900 else None
            step = totp.verify(secret, request.form.get("code", "")) if secret else None
            if step is None:
                flash("That code is not right. Check the time on your phone and try again.", "error")
                return redirect(url_for("my.two_factor"))
            codes = totp.new_recovery_codes()
            from .vault import seal
            d.execute("UPDATE users SET totp_secret=%s, totp_enabled=TRUE, totp_last=%s WHERE id=%s", (seal(secret), step, current_user.id))
            d.execute("DELETE FROM recovery_codes WHERE user_id=%s", (current_user.id,))
            for c in codes:
                d.execute("INSERT INTO recovery_codes (user_id, code_hash) VALUES (%s,%s)", (current_user.id, totp.hash_code(c)))
            d.commit()
            session.pop("pending_totp", None)
            audit("two_factor.enable", target=current_user.username)
            return render_template("my_two_factor.html", **_two_factor_context(new_codes=codes))
        if action == "new_codes" and ctx["enabled"]:
            if not current_user.check_password(request.form.get("password", "")):
                flash("Your password is not right.", "error")
                return redirect(url_for("my.two_factor"))
            codes = totp.new_recovery_codes()
            d.execute("DELETE FROM recovery_codes WHERE user_id=%s", (current_user.id,))
            for c in codes:
                d.execute("INSERT INTO recovery_codes (user_id, code_hash) VALUES (%s,%s)", (current_user.id, totp.hash_code(c)))
            d.commit()
            audit("two_factor.new_codes", target=current_user.username)
            return render_template("my_two_factor.html", **_two_factor_context(new_codes=codes))
        if action == "disable" and ctx["enabled"]:
            if ctx["required"]:
                flash("Administrators are required to use two-factor sign-in on this site, so it cannot be turned off.", "error")
                return redirect(url_for("my.two_factor"))
            if not current_user.check_password(request.form.get("password", "")):
                flash("Your password is not right.", "error")
                return redirect(url_for("my.two_factor"))
            d.execute("UPDATE users SET totp_secret='', totp_enabled=FALSE, totp_last=0 WHERE id=%s", (current_user.id,))
            d.execute("DELETE FROM recovery_codes WHERE user_id=%s", (current_user.id,))
            d.commit()
            audit("two_factor.disable", target=current_user.username)
            flash("Two-factor sign-in is off.", "success")
            return redirect(url_for("my.two_factor"))
        return redirect(url_for("my.two_factor"))
    pend = session.get("pending_totp") or {}
    secret = pend.get("s") if time.time() - pend.get("at", 0) < 900 else None
    if secret and not ctx["enabled"]:
        ctx.update(secret=secret, secret_grouped=totp.grouped(secret), otpauth=totp.uri(secret, current_user.username, cfg("site_name")))
    return render_template("my_two_factor.html", **ctx)


@bp.route("/sessions")
@login_required
def sessions():
    return render_template("my_sessions.html", **_sessions_context())


def _sessions_response(ok, message):
    return action_response(ok, message, redirect_endpoint="my.sessions", fragment_template="partials/_sessions_list.html", fragment_context=_sessions_context())


@bp.route("/sessions/<sid>/end", methods=["POST"])
@login_required
def session_end(sid):
    from flask import session
    from . import security
    if sid == session.get("sid"):
        return _sessions_response(False, "That is the sign-in you are using now. Use Sign Out to end it.")
    d = get_db()
    row = d.execute("SELECT 1 FROM user_sessions WHERE sid=%s AND user_id=%s", (sid, current_user.id)).fetchone()
    if not row:
        abort(404)
    security.end_session(sid)
    audit("session.end", detail="ended one sign-in")
    return _sessions_response(True, "That device has been signed out.")


@bp.route("/sessions/end-others", methods=["POST"])
@login_required
def sessions_end_others():
    from flask import session
    d = get_db()
    n = d.execute("UPDATE user_sessions SET ended=TRUE WHERE user_id=%s AND sid <> %s AND NOT ended", (current_user.id, session.get("sid", ""))).rowcount
    d.commit()
    audit("session.end_others", detail=f"{n} sign-ins ended")
    return _sessions_response(True, f"Signed out {n} other device{'' if n == 1 else 's'}.")


@bp.route("/alerts/webhook", methods=["POST"])
@login_required
def alert_webhook_save():
    """Where this person's webhook alerts go (empty address turns webhook delivery off)."""
    from . import webhooks
    kind = request.form.get("webhook_kind", "json")
    if kind not in webhooks.KINDS:
        return action_response(False, "Choose the kind of service.", redirect_endpoint="my.alerts")
    url, err = webhooks.check_url(request.form.get("webhook_url", ""))
    if err:
        return action_response(False, err, redirect_endpoint="my.alerts")
    d = get_db()
    from .vault import seal
    d.execute("UPDATE users SET webhook_url=%s, webhook_kind=%s WHERE id=%s", (seal(url), kind, current_user.id))
    d.commit()
    audit("alerts.webhook", detail=("set" if url else "cleared"))
    return action_response(True, "Webhook address saved. Use Send a Test to see it arrive." if url else "Webhook delivery is off.", redirect_endpoint="my.alerts")


@bp.route("/alerts/webhook/test", methods=["POST"])
@login_required
def alert_webhook_test():
    from . import webhooks
    r = get_db().execute("SELECT webhook_url, webhook_kind FROM users WHERE id=%s", (current_user.id,)).fetchone()
    if not r["webhook_url"]:
        return action_response(False, "Save a webhook address first.", redirect_endpoint="my.alerts")
    site = cfg("site_name")
    text = f"Test message from {site}: your aircraft alert webhook is working."
    from .vault import unseal
    ok, msg = webhooks.send(unseal(r["webhook_url"]), r["webhook_kind"], text, [], site)
    return action_response(ok, ("Sent. " if ok else "Not delivered. ") + msg, redirect_endpoint="my.alerts")


@bp.route("/alerts/save", methods=["POST"])
@login_required
def alert_save():
    values, err = al.validate_rule(request.form, cfg_int("alert_default_cooldown_minutes"))
    if err:
        return _alerts_response(False, err)
    d = get_db()
    rule_id = request.form.get("id", type=int)
    cols = list(values)
    if rule_id:
        owned = d.execute("SELECT 1 FROM alert_rules WHERE id=%s AND user_id=%s", (rule_id, current_user.id)).fetchone()
        if not owned:
            abort(404)
        d.execute("UPDATE alert_rules SET " + ", ".join(f"{c}=%s" for c in cols) + " WHERE id=%s", [values[c] for c in cols] + [rule_id])
    else:
        limit = int(cfg("max_rules_per_user"))
        if d.execute("SELECT COUNT(*) AS n FROM alert_rules WHERE user_id=%s", (current_user.id,)).fetchone()["n"] >= limit:
            return _alerts_response(False, f"You can have up to {limit} alert rules. Delete one to add another.")
        d.execute("INSERT INTO alert_rules (user_id, " + ", ".join(cols) + ") VALUES (%s, " + ", ".join(["%s"] * len(cols)) + ")",
                  [current_user.id] + [values[c] for c in cols])
    d.commit()
    return _alerts_response(True, f"Alert \"{values['name']}\" saved.")


@bp.route("/alerts/<int:rule_id>/toggle", methods=["POST"])
@login_required
def alert_toggle(rule_id):
    d = get_db()
    row = d.execute("UPDATE alert_rules SET enabled = NOT enabled WHERE id=%s AND user_id=%s RETURNING name, enabled",
                    (rule_id, current_user.id)).fetchone()
    d.commit()
    if row is None:
        abort(404)
    return _alerts_response(True, f"Alert \"{row['name']}\" is now {'on' if row['enabled'] else 'off'}.")


@bp.route("/alerts/<int:rule_id>/delete", methods=["POST"])
@login_required
def alert_delete(rule_id):
    d = get_db()
    row = d.execute("DELETE FROM alert_rules WHERE id=%s AND user_id=%s RETURNING name", (rule_id, current_user.id)).fetchone()
    d.commit()
    if row is None:
        abort(404)
    return _alerts_response(True, f"Alert \"{row['name']}\" deleted.")


MAP_VIEW_PATHS = ("/live", "/weather/map", "/sondes/map", "/counties", "/trauma", "/repeaters/map", "/aprs/map", "/coverage")


def _views(prefs):
    v = prefs.get("mapViews")
    return v if isinstance(v, list) else []


def _clean_view(data):
    """(view, error) from a posted saved view: a name, an address on one of the map pages, and the map's layer choices."""
    import re
    name = str(data.get("name", "")).strip()[:40]
    url = str(data.get("url", ""))
    if not name:
        return None, "Give the view a name."
    path = url.split("?")[0].rstrip("/") or "/"
    if not url.startswith("/") or url.startswith("//") or path not in MAP_VIEW_PATHS or len(url) > 1500 or re.search(r"[\x00-\x1f<>\"]", url):
        return None, "That is not a view of one of the maps."
    layers = str(data.get("layers", ""))[:2000]
    return {"name": name, "url": url, "layers": layers}, None


@bp.route("/display")
@login_required
def display_page():
    """Display and Maps: every Display menu choice and the saved map views, in My Account."""
    return render_template("my_display.html")


@bp.route("/map-views")
@login_required
def map_views():
    import json
    row = get_db().execute("SELECT prefs FROM users WHERE id=%s", (current_user.id,)).fetchone()
    try:
        prefs = json.loads(row["prefs"]) if row and row["prefs"] else {}
    except ValueError:
        prefs = {}
    return jsonify({"views": _views(prefs)})


def _save_views(mutate):
    import json
    d = get_db()
    row = d.execute("SELECT prefs FROM users WHERE id=%s", (current_user.id,)).fetchone()
    try:
        prefs = json.loads(row["prefs"]) if row and row["prefs"] else {}
    except ValueError:
        prefs = {}
    views = mutate(list(_views(prefs)))
    prefs["mapViews"] = views[-25:]
    d.execute("UPDATE users SET prefs=%s WHERE id=%s", (json.dumps(prefs), current_user.id))
    d.commit()
    return prefs["mapViews"]


@bp.route("/map-views", methods=["POST"])
@login_required
def map_views_save():
    v, err = _clean_view(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    same = lambda x: x.get("name") == v["name"] and str(x.get("url", "")).split("?")[0] == v["url"].split("?")[0]
    return jsonify({"views": _save_views(lambda vs: [x for x in vs if not same(x)] + [v])})


@bp.route("/map-views/delete", methods=["POST"])
@login_required
def map_views_delete():
    data = request.get_json(silent=True) or {}
    name, url = str(data.get("name", "")), str(data.get("url", "")).split("?")[0]
    return jsonify({"views": _save_views(lambda vs: [x for x in vs if not (x.get("name") == name and str(x.get("url", "")).split("?")[0] == url)])})


PREF_CHOICES = {"units": {"", "imperial", "aero", "metric"}, "theme": __import__("app.themes", fromlist=["x"]).THEMES.keys() | {"auto", "site", "normal"}, "deco": {"", "0", "1"}, "themeV": {"2"}, **{"u_" + _a: {"", "imperial", "aero", "metric"} for _a in ("aircraft", "weather", "sondes", "aprs", "acars", "mesh", "repeaters", "places", "network", "home")}, "hour": {"auto", "12", "24"}, "tz": {"browser", "site", "utc"},
                "pageWidth": {"", "limited", "full"}, "trailLabels": {"", "0", "1"}, "lastLeg": {"", "0", "1"}, "focusOnly": {"", "0", "1"}, "route": {"", "0", "1"}, "iconScale": {"", "0.8", "1", "1.3", "1.7", "2.2"}}


@bp.route("/prefs", methods=["POST"])
@login_required
def save_prefs():
    """Display preferences, saved with the account so they follow the user to every device."""
    import json
    data = request.get_json(silent=True) or {}
    d = get_db()
    row = d.execute("SELECT prefs FROM users WHERE id=%s", (current_user.id,)).fetchone()
    try:
        prefs = json.loads(row["prefs"]) if row and row["prefs"] else {}
    except ValueError:
        prefs = {}
    for k, v in data.items():
        if (k in ("pageWidth", "units") or k.startswith("u_")) and str(v) == "":
            prefs.pop(k, None)   # the site default: nothing stored
        elif k in PREF_CHOICES and str(v) in PREF_CHOICES[k]:
            prefs[k] = str(v)
    d.execute("UPDATE users SET prefs=%s WHERE id=%s", (json.dumps(prefs), current_user.id))
    d.commit()
    return jsonify(prefs)
