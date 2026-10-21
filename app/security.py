"""Sign-in security policy: how long a session may last, automatic sign-out after inactivity, password rules and password age.

All limits are settings (Admin > Settings > Security). Administrators can have shorter limits than everyone else. A session remembers
when it began (`iat`) and when its owner last did something (`last`); background requests the page makes by itself (live data, the
alert counter) do not count as activity, so a map left open does not keep a session alive.
"""
import re
import secrets
import time

from flask import flash, jsonify, redirect, request, session, url_for
from flask_login import current_user, logout_user

from .settings import cfg, cfg_int, get_setting

# requests a page makes on its own; they are checked for expiry but never extend the session
PASSIVE_PREFIXES = ("/api/", "/metrics", "/healthz", "/background.svg", "/my/alerts/count.json")


def now():
    return time.time()


def password_error(pw):
    """None when the password meets the rules, otherwise the sentence to show."""
    n = cfg_int("min_password_length")
    if len(pw) < n:
        return f"The password must be at least {n} characters."
    if cfg("password_require_mix") == "1" and not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        return "The password must contain both letters and numbers."
    return None


def password_rules_text():
    text = f"At least {cfg_int('min_password_length')} characters"
    return text + (", with letters and numbers." if cfg("password_require_mix") == "1" else ".")


def limits(user):
    """(longest session in hours, inactivity limit in minutes) for this person; 0 means no limit."""
    max_h, idle = cfg_int("session_hours"), cfg_int("idle_timeout_minutes")
    if user.has_admin_access:
        a_h, a_idle = cfg_int("admin_session_hours"), cfg_int("admin_idle_timeout_minutes")
        if a_h > 0:
            max_h = min(max_h, a_h) if max_h else a_h
        if a_idle > 0:
            idle = min(idle, a_idle) if idle else a_idle
    return max_h, idle


def stamp_login():
    session["iat"] = session["last"] = now()


def describe_ua(ua):
    """A short, human description of a browser: 'Chrome on Windows'."""
    ua = ua or ""
    browser = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")) if k in ua), "Browser")
    system = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"), ("Mac OS X", "macOS"), ("CrOS", "ChromeOS"), ("Linux", "Linux")) if k in ua), "unknown system")
    return f"{browser} on {system}"


def new_session(user_id):
    """Record a sign-in so the person can see and end it later (My Account > Sign-Ins)."""
    from .clientip import client_ip
    from .db import get_db
    sid = secrets.token_urlsafe(16)
    d = get_db()
    d.execute("INSERT INTO user_sessions (sid, user_id, ip, ua) VALUES (%s,%s,%s,%s)", (sid, user_id, client_ip(), (request.headers.get("User-Agent") or "")[:300]))
    d.execute("DELETE FROM user_sessions WHERE user_id=%s AND last_seen < now() - make_interval(days => %s)", (user_id, cfg_int("session_keep_days")))
    d.commit()
    session["sid"] = sid
    return sid


def _check_session():
    """False when this sign-in was ended from another device. Keeps last_seen fresh (at most once a minute)."""
    from .db import get_db
    sid = session.get("sid")
    d = get_db()
    if not sid:   # a sign-in from before this list existed: start tracking it now
        new_session(current_user.id)
        return True
    row = d.execute("SELECT ended FROM user_sessions WHERE sid=%s AND user_id=%s", (sid, current_user.id)).fetchone()
    if row is None:
        new_session(current_user.id)   # the list was cleaned up or the session predates it: carry on tracked
        return True
    if row["ended"]:
        return False
    if now() - session.get("seen_db", 0) > 60 and not _is_passive():
        d.execute("UPDATE user_sessions SET last_seen=now() WHERE sid=%s", (sid,))
        d.commit()
        session["seen_db"] = now()
    return True


def end_session(sid=None):
    from .db import get_db
    d = get_db()
    d.execute("UPDATE user_sessions SET ended=TRUE WHERE sid=%s AND user_id=%s", (sid or session.get("sid"), current_user.id))
    d.commit()


def ip_blocked(ip):
    """True when too many sign-ins from this address have failed recently (any account)."""
    from .db import get_db
    limit, window = cfg_int("signin_ip_failures"), cfg_int("signin_ip_window_minutes")
    if not limit or not ip:
        return False
    n = get_db().execute("SELECT COUNT(*) AS n FROM login_events WHERE ip=%s AND NOT success AND ts > now() - make_interval(mins => %s)", (ip, window)).fetchone()["n"]
    return n >= limit


def apply_headers(resp):
    """Browser security headers (Settings > Security). Added to every response."""
    if cfg("security_headers") != "1":
        return resp
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Content-Security-Policy", "frame-ancestors 'self'; base-uri 'self'; object-src 'none'; form-action 'self'")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(self), payment=()")
    if request.is_secure or request.headers.get("X-Forwarded-Proto", "") == "https":
        h.setdefault("Strict-Transport-Security", "max-age=15552000")
    return resp


def sign_out_everyone():
    """End every session started before this moment (the person asking keeps theirs)."""
    from .settings import set_setting
    t = now()
    set_setting("sessions_valid_after", repr(t))
    session["iat"] = session["last"] = t + 0.001


def _is_passive():
    return request.path.startswith(PASSIVE_PREFIXES)


def _expire(reason):
    logout_user()
    session.clear()
    if _is_passive() or (request.method != "GET" and request.headers.get("X-Requested-With") == "XMLHttpRequest"):
        resp = jsonify({"ok": False, "message": reason, "signed_out": True})
        resp.status_code = 401
        return resp
    flash(reason, "info")
    return redirect(url_for("auth.login", next=request.full_path.rstrip("?")) if request.method == "GET" else url_for("auth.login"))


def enforce():
    """Runs before every request. Returns a response when the session has to end, otherwise None."""
    if request.endpoint == "static" or not current_user.is_authenticated:
        return None
    t = now()
    iat, last = session.get("iat"), session.get("last")
    if iat is None:                   # a session that began before these limits existed starts counting now
        session["iat"] = session["last"] = t
        return None
    max_h, idle = limits(current_user)
    if not _check_session():
        return _expire("This sign-in was ended from another device. Please sign in again.")
    if iat < float(get_setting("sessions_valid_after", "0") or 0):
        return _expire("An administrator ended all sign-ins. Please sign in again.")
    if max_h and t - iat > max_h * 3600:
        return _expire(f"Your sign-in reached its {max_h}-hour limit. Please sign in again.")
    if idle and t - (last or iat) > idle * 60:
        return _expire(f"You were signed out after {idle} minutes without activity.")
    if (cfg("require_2fa_admins") == "1" and not _is_passive() and current_user.has_admin_access and not current_user.row.get("totp_enabled")
            and request.endpoint not in ("my.two_factor", "auth.logout", "auth.keepalive", "static", "background.background_svg")):
        flash("Administrators must use two-factor sign-in. Please set it up to continue.", "info")
        return redirect(url_for("my.two_factor"))
    age_limit = cfg_int("password_max_age_days")
    if age_limit and not _is_passive() and request.endpoint not in ("auth.change_password", "auth.logout", "auth.keepalive"):
        changed = current_user.row.get("password_changed_at")
        if changed is not None and t - changed.timestamp() > age_limit * 86400:
            flash(f"Your password is older than {age_limit} days. Please choose a new one.", "info")
            return redirect(url_for("auth.change_password"))
    if not _is_passive():
        session["last"] = t
    return None
