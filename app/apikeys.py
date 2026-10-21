"""API keys: a documented, versioned public API (`/api/v1/...`) for people who build things on the network's data, with a key per project and fair limits.

A member makes keys on My Account > API Keys (a few each); administrators see, limit, revoke and make keys for members on Admin > API Keys. A key is shown once when it is made: only a hash is kept. A request
sends it as `Authorization: Bearer <key>` (or `X-API-Key`), is counted per minute and per day, and answers 429 with Retry-After when a limit is passed. The v1 endpoints are thin wrappers of the data the site's own
pages already publish, so there is one source of truth; what a key may do is read public data only."""
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("apikeys", __name__)

PREFIX = "ask_"
_state = {"pruned": 0.0}

ENDPOINTS = [
    ("me", "/api/v1/me", "Your key", "The name, limits and today's use of the key you are asking with."),
    ("aircraft", "/api/v1/aircraft", "Aircraft now", "Every aircraft the network hears right now, with position, altitude, speed, type and registration."),
    ("feeders", "/api/v1/feeders", "Feeders", "The named receiving stations, their status, approximate position as their owners chose, uptime and range."),
    ("weather", "/api/v1/weather", "Weather stations", "The latest readings of members' weather stations."),
    ("sondes", "/api/v1/sondes", "Radiosonde flights", "Recent weather balloon flights with their tracks, status and where they were launched (data from SondeHub: keep the credit it comes with)."),
    ("repeaters", "/api/v1/repeaters", "Repeaters", "The amateur radio repeater directory (positions, frequencies, offsets, tones, modes)."),
]
SWITCHABLE = [e for e in ENDPOINTS if e[0] != "me"]   # an administrator can turn these off one by one (Admin > API Keys > Endpoints); a key's own page is always there


def endpoint_on(key):
    return key is None or key == "me" or cfg("api_ep_" + key) == "1"


def endpoint_states():
    return [{"key": k, "path": p, "name": n, "about": t, "on": endpoint_on(k), "switchable": k != "me"} for k, p, n, t in ENDPOINTS]


def hash_key(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def make_key():
    """A new key as (key, prefix, hash). The key is shown once."""
    key = PREFIX + secrets.token_urlsafe(24)
    return key, key[:10], hash_key(key)


def limits_for(row):
    return (row["rate_minute"] or cfg_int("api_rate_minute"), row["rate_day"] or cfg_int("api_rate_day"))


def create_key(user_id, name, note="", by=""):
    key, prefix, h = make_key()
    d = get_db()
    kid = d.execute("INSERT INTO api_keys (user_id, name, note, prefix, key_hash, created_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id", (user_id, name, note, prefix, h, by)).fetchone()["id"]
    d.commit()
    return key, kid


def keys_for(user_id):
    return get_db().execute(
        "SELECT k.*, COALESCE((SELECT calls FROM api_key_daily WHERE key_id = k.id AND day = current_date), 0) AS today, "
        "COALESCE((SELECT SUM(calls) FROM api_key_daily WHERE key_id = k.id), 0) AS total FROM api_keys k WHERE k.user_id = %s ORDER BY k.created_at DESC", (user_id,)).fetchall()


def _count(kid):
    """Count one request of the key; returns (calls this minute, calls today)."""
    d = get_db()
    minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    m = d.execute("INSERT INTO api_key_minute (key_id, bucket, calls) VALUES (%s,%s,1) ON CONFLICT (key_id, bucket) DO UPDATE SET calls = api_key_minute.calls + 1 RETURNING calls", (kid, minute)).fetchone()["calls"]
    day = d.execute("INSERT INTO api_key_daily (key_id, day, calls) VALUES (%s, current_date, 1) ON CONFLICT (key_id, day) DO UPDATE SET calls = api_key_daily.calls + 1 RETURNING calls", (kid,)).fetchone()["calls"]
    d.execute("UPDATE api_keys SET last_used = now() WHERE id = %s AND (last_used IS NULL OR last_used < now() - interval '1 minute')", (kid,))
    if time.time() - _state["pruned"] > 3600:
        _state["pruned"] = time.time()
        d.execute("DELETE FROM api_key_minute WHERE bucket < now() - interval '2 days'")
    d.commit()
    return m, day


def _err(status, code, message, **headers):
    resp = jsonify({"error": code, "message": message})
    resp.status_code = status
    for k, v in headers.items():
        resp.headers[k.replace("_", "-")] = str(v)
    return resp


def keyed(ep=None):
    """A v1 endpoint (`ep` is its name in ENDPOINTS): needs a good key, counts the request, enforces the limits and tells the caller where they stand. An endpoint an administrator switched off
    answers 403 and is not counted."""
    def deco(view):
        return _keyed(view, ep)
    return deco


def _keyed(view, ep):
    @wraps(view)
    def wrapper(*a, **kw):
        if cfg("api_enabled") != "1":
            abort(404)
        raw = ""
        auth = request.headers.get("Authorization", "")
        if auth[:7].lower() == "bearer ":
            raw = auth[7:].strip()
        raw = raw or request.headers.get("X-API-Key", "").strip()
        if not raw.startswith(PREFIX):
            return _err(401, "key_required", "Send your API key as 'Authorization: Bearer <key>'. Keys are made on My Account > API Keys.", WWW_Authenticate="Bearer")
        row = get_db().execute("SELECT k.*, u.disabled AS user_disabled FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.key_hash = %s", (hash_key(raw),)).fetchone()
        if row is None or row["revoked"] or row["user_disabled"]:
            return _err(401, "invalid_key", "That key is not valid (it may have been revoked).", WWW_Authenticate="Bearer")
        from . import access
        from .models import User
        owner = User.get(row["user_id"])
        if owner is None or not access.allowed("api", owner) or (ep in ("weather", "sondes", "repeaters") and not access.allowed(ep, owner)):
            return _err(403, "not_allowed", "This site limits that part of the API to some members, and the key's owner is not one of them.")
        if not endpoint_on(ep):
            return _err(403, "endpoint_disabled", "This part of the API is switched off on this site. /api/v1/ lists what is available.")
        per_min, per_day = limits_for(row)
        m, day = _count(row["id"])
        hdr = {"X_RateLimit_Limit": per_min, "X_RateLimit_Remaining": max(0, per_min - m), "X_RateLimit_Day_Limit": per_day, "X_RateLimit_Day_Remaining": max(0, per_day - day)}
        if m > per_min:
            wait = 60 - datetime.now(timezone.utc).second
            return _err(429, "rate_limited", f"More than {per_min} requests this minute. Try again in {wait} seconds.", Retry_After=wait, **hdr)
        if day > per_day:
            return _err(429, "daily_limit", f"More than {per_day} requests today. The count restarts at midnight UTC.", Retry_After=3600, **hdr)
        request.api_key = row
        resp = view(*a, **kw)
        if not hasattr(resp, "headers"):
            resp = jsonify(resp)
        for k, v in hdr.items():
            resp.headers[k.replace("_", "-")] = str(v)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    return wrapper


# ------------------------------------------------------------------ the v1 API

@bp.route("/api/v1/")
@keyed()
def v1_index():
    return jsonify({"version": "v1", "endpoints": [{"path": e["path"], "name": e["name"], "about": e["about"], "enabled": True} for e in endpoint_states() if e["on"]], "docs": "/api/docs"})


@bp.route("/api/v1/me")
@keyed("me")
def v1_me():
    row = request.api_key
    per_min, per_day = limits_for(row)
    today = get_db().execute("SELECT calls FROM api_key_daily WHERE key_id = %s AND day = current_date", (row["id"],)).fetchone()
    return jsonify({"name": row["name"], "prefix": row["prefix"], "created": row["created_at"].isoformat(), "limit_per_minute": per_min, "limit_per_day": per_day, "calls_today": today["calls"] if today else 0})


@bp.route("/api/v1/aircraft")
@keyed("aircraft")
def v1_aircraft():
    from . import live
    return live.api_aircraft()


@bp.route("/api/v1/feeders")
@keyed("feeders")
def v1_feeders():
    from . import public
    return public.api_feeders()


@bp.route("/api/v1/weather")
@keyed("weather")
def v1_weather():
    from . import weather_views
    return weather_views.api_weather()


@bp.route("/api/v1/sondes")
@keyed("sondes")
def v1_sondes():
    from . import sondes_views
    return sondes_views.api_map()


@bp.route("/api/v1/repeaters")
@keyed("repeaters")
def v1_repeaters():
    from . import repeaters_views
    return repeaters_views.api_map()


# ------------------------------------------------------------------ the public documentation

@bp.route("/api/docs")
def docs():
    if cfg("api_enabled") != "1":
        abort(404)
    on = [e for e in endpoint_states() if e["on"]]   # an endpoint an administrator switched off is not shown at all (Admin > API Keys > Endpoints still lists every one)
    data = [e["path"] for e in on if e["key"] != "me"]
    return render_template("api_docs.html", endpoints=on, ex1=(data or ["/api/v1/me"])[0], ex2=(data[1:2] or data or ["/api/v1/me"])[0], member_keys=cfg("api_member_keys") == "1", per_min=cfg_int("api_rate_minute"), per_day=cfg_int("api_rate_day"), per_user=cfg_int("api_keys_per_user"))


# ------------------------------------------------------------------ My Account > API Keys

def _my_ctx(new_key=None, note=""):
    return {"keys": keys_for(current_user.id), "can_make": cfg("api_member_keys") == "1", "per_user": cfg_int("api_keys_per_user"), "per_min": cfg_int("api_rate_minute"), "per_day": cfg_int("api_rate_day"), "new_key": new_key, "new_note": note}


def _clean_name(text):
    return " ".join((text or "").split())[:60]


@bp.route("/my/api-keys")
@login_required
def my_keys():
    if cfg("api_enabled") != "1":
        abort(404)
    return render_template("my_api_keys.html", **_my_ctx())


def _my_response(ok, message, new_key=None, note=""):
    if new_key and request.headers.get("X-Requested-With") != "XMLHttpRequest":
        from flask import flash
        flash(message, "success")
        return render_template("my_api_keys.html", **_my_ctx(new_key, note))
    return action_response(ok, message, redirect_endpoint="apikeys.my_keys", fragment_template="partials/_api_keys_list.html", fragment_context=_my_ctx(new_key, note))


@bp.route("/my/api-keys/create", methods=["POST"])
@login_required
def my_key_create():
    if cfg("api_enabled") != "1":
        abort(404)
    if cfg("api_member_keys") != "1":
        return _my_response(False, "On this site an administrator makes the API keys. Ask through Contact and say what the key is for.")
    name = _clean_name(request.form.get("name"))
    if not name:
        return _my_response(False, "Give the key a name (what it is for), up to 60 characters.")
    live = [k for k in keys_for(current_user.id) if not k["revoked"]]
    if len(live) >= cfg_int("api_keys_per_user"):
        return _my_response(False, f"You can have {cfg_int('api_keys_per_user')} working keys. Revoke one to make another.")
    key, kid = create_key(current_user.id, name, (request.form.get("note") or "").strip()[:200], by=current_user.username)
    audit("apikey.create", target=name, detail=f"key {key[:10]}")
    return _my_response(True, "Your key is made. Copy it now: it is shown only this once.", new_key=key, note=name)


@bp.route("/my/api-keys/<int:kid>/revoke", methods=["POST"])
@login_required
def my_key_revoke(kid):
    d = get_db()
    row = d.execute("SELECT name, prefix FROM api_keys WHERE id=%s AND user_id=%s", (kid, current_user.id)).fetchone()
    if row is None:
        abort(404)
    d.execute("UPDATE api_keys SET revoked=TRUE, revoked_at=now(), revoked_by=%s WHERE id=%s", (current_user.username, kid))
    d.commit()
    audit("apikey.revoke", target=row["name"], detail=f"key {row['prefix']}")
    return _my_response(True, f"{row['name']} no longer works.")


@bp.route("/my/api-keys/<int:kid>/rename", methods=["POST"])
@login_required
def my_key_rename(kid):
    name = _clean_name(request.form.get("name"))
    d = get_db()
    row = d.execute("SELECT name FROM api_keys WHERE id=%s AND user_id=%s", (kid, current_user.id)).fetchone()
    if row is None:
        abort(404)
    if not name:
        return _my_response(False, "Give the key a name.")
    d.execute("UPDATE api_keys SET name=%s WHERE id=%s", (name, kid))
    d.commit()
    audit("apikey.rename", target=name, detail=f"was {row['name']}")
    return _my_response(True, "Renamed.")


# ------------------------------------------------------------------ Admin > API Keys

def _admin_ctx(new_key=None, note="", member=""):
    rows = get_db().execute(
        "SELECT k.*, u.username, u.display_name, u.callsign, u.disabled AS user_disabled, "
        "COALESCE((SELECT calls FROM api_key_daily WHERE key_id = k.id AND day = current_date), 0) AS today, COALESCE((SELECT SUM(calls) FROM api_key_daily WHERE key_id = k.id), 0) AS total "
        "FROM api_keys k JOIN users u ON u.id = k.user_id ORDER BY k.revoked, k.last_used DESC NULLS LAST, k.created_at DESC").fetchall()
    return {"rows": rows, "per_min": cfg_int("api_rate_minute"), "per_day": cfg_int("api_rate_day"), "new_key": new_key, "new_note": note, "new_member": member, "enabled": cfg("api_enabled") == "1", "endpoints": endpoint_states()}


def _admin_response(ok, message, new_key=None, note="", member=""):
    if new_key and request.headers.get("X-Requested-With") != "XMLHttpRequest":
        from flask import flash
        flash(message, "success")
        return render_template("admin_api_keys.html", active="apikeys", **_admin_ctx(new_key, note, member))
    return action_response(ok, message, redirect_endpoint="apikeys.admin_keys", fragment_template="partials/_admin_api_keys.html", fragment_context=_admin_ctx(new_key, note, member))


@bp.route("/admin/api-keys")
@permission_required("manage_api_keys")
def admin_keys():
    return render_template("admin_api_keys.html", active="apikeys", **_admin_ctx())


@bp.route("/admin/api-keys/create", methods=["POST"])
@permission_required("manage_api_keys")
def admin_key_create():
    """Make a key for a member (to help them get started): shown once, to the administrator, to pass on."""
    d = get_db()
    who = (request.form.get("username") or "").strip()
    u = d.execute("SELECT id, username FROM users WHERE lower(username)=lower(%s) OR lower(email)=lower(%s)", (who, who)).fetchone()
    if u is None:
        return _admin_response(False, "No member has that user name or email address.")
    name = _clean_name(request.form.get("name"))
    if not name:
        return _admin_response(False, "Give the key a name (what it is for), up to 60 characters.")
    key, kid = create_key(u["id"], name, (request.form.get("note") or "").strip()[:200], by=current_user.username)
    audit("apikey.admin_create", target=u["username"], detail=f"{name}, key {key[:10]}")
    return _admin_response(True, f"A key is made for {u['username']}. Copy it now: it is shown only this once.", new_key=key, note=name, member=u["username"])


@bp.route("/admin/api-keys/<int:kid>/revoke", methods=["POST"])
@permission_required("manage_api_keys")
def admin_key_revoke(kid):
    d = get_db()
    row = d.execute("SELECT k.name, k.prefix, k.revoked, u.username FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.id=%s", (kid,)).fetchone()
    if row is None:
        abort(404)
    restore = row["revoked"]
    if restore:
        d.execute("UPDATE api_keys SET revoked=FALSE, revoked_at=NULL, revoked_by='' WHERE id=%s", (kid,))
    else:
        d.execute("UPDATE api_keys SET revoked=TRUE, revoked_at=now(), revoked_by=%s WHERE id=%s", (current_user.username, kid))
    d.commit()
    audit("apikey.admin_restore" if restore else "apikey.admin_revoke", target=row["username"], detail=f"{row['name']}, key {row['prefix']}")
    return _admin_response(True, f"{row['name']} ({row['username']}) works again." if restore else f"{row['name']} ({row['username']}) is revoked.")


@bp.route("/admin/api-keys/<int:kid>/limits", methods=["POST"])
@permission_required("manage_api_keys")
def admin_key_limits(kid):
    """A key's own limits (blank = the site's)."""
    d = get_db()
    row = d.execute("SELECT k.name, u.username FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.id=%s", (kid,)).fetchone()
    if row is None:
        abort(404)
    vals = []
    for f, hi in (("rate_minute", 100000), ("rate_day", 100000000)):
        t = (request.form.get(f) or "").strip()
        if t == "":
            vals.append(None)
        elif t.isdigit() and 1 <= int(t) <= hi:
            vals.append(int(t))
        else:
            return _admin_response(False, f"A limit is a whole number from 1 to {hi:,}, or blank for the site's limit.")
    d.execute("UPDATE api_keys SET rate_minute=%s, rate_day=%s WHERE id=%s", (vals[0], vals[1], kid))
    d.commit()
    audit("apikey.admin_limits", target=row["username"], detail=f"{row['name']}: {vals[0] or 'site'} per minute, {vals[1] or 'site'} per day")
    return _admin_response(True, f"Limits saved for {row['name']}.")


@bp.route("/admin/api-keys/endpoints", methods=["POST"])
@permission_required("manage_api_keys")
def admin_endpoints():
    """Which parts of the API answer: a ticked box is on. Keys and their limits stay as they are; a part that is off answers 403 and is not counted."""
    from .settings import set_setting
    changed = []
    for k, _p, n, _t in SWITCHABLE:
        want = "1" if request.form.get("ep_" + k) else "0"
        if cfg("api_ep_" + k) != want:
            set_setting("api_ep_" + k, want)
            changed.append(f"{n} {'on' if want == '1' else 'off'}")
    if changed:
        audit("apikey.endpoints", detail=", ".join(changed))
    return _admin_response(True, ("Saved: " + ", ".join(changed) + ".") if changed else "Nothing changed.")
