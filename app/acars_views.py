"""ACARS: the address members' receivers send messages to, the public pages, the owner's pages (My ACARS Stations) and the admin page. The logic is in acars.py."""
import re
from datetime import datetime, timezone
from types import SimpleNamespace

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import acars
from . import csrf
from . import stations as st
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .clientip import client_ip
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("acars", __name__)


def _need():
    if not acars.enabled():
        abort(404)


# ------------------------------------------------------------------ messages arrive here

@bp.route("/acars/push/<token>", methods=["POST"])
@csrf.exempt
def push(token):
    """A member's bridge posts what its receiver heard: one JSON object, a list, or one per line. Unknown and switched-off stations look the same."""
    _need()
    row = acars.by_token(token)
    if row is None or row["disabled"]:
        if acars.too_many_bad_tokens(client_ip(), note=True):
            return "slow down", 429
        abort(404)
    if (request.content_length or 0) > 1_000_000:
        return "too large", 413
    items = acars.parse_body(request.get_data(cache=False))
    if not items:
        return jsonify({"ok": False, "error": "no messages"}), 400
    ok, d = acars.forward(row["station_id"], items)
    if not ok:
        return jsonify({"ok": False, "error": d}), 503
    acars.note_report(row["station_id"], d.get("new", 0) + d.get("duplicates", 0))
    return jsonify(d)


# ------------------------------------------------------------------ public pages

def _filters():
    a = request.args
    return {k: (a.get(k) or "").strip()[:60] for k in ("tail", "flight", "label", "station", "mode", "q", "hours")} | {"position": "1" if a.get("position") == "1" else ""}


@bp.route("/acars/")
def acars_page():
    _need()
    f = _filters()
    if not f["hours"]:
        f["hours"] = "24"
    rows, note = acars.messages(f, current_user, cfg_int("acars_page_size"))
    stt = acars.stats()
    return render_template("acars.html", rows=rows, note=note or (stt["note"] if not stt["ok"] else ""), f=f, st=stt["data"], modes=acars.MODES, text_ok=acars.can_see_text(current_user),
                           visibility=cfg("acars_text_visibility"), credit=acars.CREDIT, stations=acars.public_stations(), online=acars.is_online)


@bp.route("/api/acars/messages.json")
def api_messages():
    _need()
    rows, note = acars.messages(_filters(), current_user, min(cfg_int("acars_page_size") * 3, 300))
    resp = jsonify({"messages": rows, "note": note})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/api/acars/aircraft/<key>.json")
def api_aircraft(key):
    """What an aircraft's card shows: its latest messages, by ICAO address, with the tail number as the fallback."""
    if not acars.enabled():
        return jsonify({"messages": []})
    if not re.fullmatch(r"[0-9A-Za-z-]{2,10}", key):
        abort(400)
    tail = (request.args.get("tail") or "").strip().upper()[:10]
    hexid = key.lower() if re.fullmatch(r"[0-9a-fA-F]{6}", key) else ""
    rows = acars.for_aircraft(hexid, tail or ("" if hexid else key), current_user)
    return jsonify({"messages": rows})


@bp.route("/api/acars/positions.json")
def api_positions():
    """Where aircraft last reported themselves by ACARS, ADS-C or HFDL (positions only, public like the rest of the map)."""
    if not acars.enabled():
        return jsonify({"enabled": False, "positions": []})
    mins = cfg_int("acars_position_minutes")
    rows = [{k: r.get(k) for k in ("ts", "tail", "flight", "hex", "lat", "lon", "alt_ft", "label")} for r in acars.positions(mins)]
    resp = jsonify({"enabled": True, "minutes": mins, "positions": rows, "credit": acars.CREDIT})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/api/acars/stats.json")
def api_stats():
    _need()
    s = acars.stats()
    return jsonify(s["data"] or {"note": s["note"]})


@bp.route("/acars/stations/")
def stations_page():
    _need()
    return render_template("acars_stations.html", rows=acars.public_stations(), online=acars.is_online, shown=acars.shown_position, modes=acars.MODES)


@bp.route("/acars/stations/<sid>")
def station_page(sid):
    _need()
    r = acars.get(sid)
    if r is None or r["disabled"] or not (r["public"] or (current_user.is_authenticated and (r["owner_id"] == current_user.id or current_user.has_permission("manage_feeders")))):
        abort(404)
    rows, note = acars.messages({"station": r["station_id"]}, current_user, 50)
    return render_template("acars_station.html", r=r, rows=rows, note=note, online=acars.is_online(r), pos=acars.shown_position(r), text_ok=acars.can_see_text(current_user))


@bp.route("/feed/acars")
def feed_acars():
    _need()
    from .public import _feed_context
    base = f"https://{cfg('feed_host') or cfg('main_address') or request.host}"
    return render_template("feed_acars.html", fsub="acars", base=base, bridge=f"{base}/static/acars_bridge.py", visibility=cfg("acars_text_visibility"), keep=cfg("acars_keep_days"), stale=cfg("acars_stale_minutes"), **_feed_context())


# ------------------------------------------------------------------ the owner's pages

def _owned(wid):
    """The member's own station; an administrator who manages feeders opens anyone's with the same pages (to help someone); what they do is in the audit log under their name."""
    r = acars.get_id(wid)
    if r is None or not (r["owner_id"] == current_user.id or current_user.has_permission("manage_feeders")):
        abort(404)
    return r


def _managing(row):
    if row["owner_id"] == current_user.id:
        return ""
    return row["owner_display"] or row["owner_name"] or "another member"


def _address(row):
    return f"https://{cfg('feed_host') or cfg('main_address') or request.host}/acars/push/{acars.push_token(row)}"


def _place(f):
    lat = lon = None
    if f.get("lat", "").strip() or f.get("lon", "").strip():
        lat = st._num(f.get("lat"), -90, 90, "Latitude", True)
        lon = st._num(f.get("lon"), -180, 180, "Longitude", True)
    return lat, lon


@bp.route("/my/acars")
@login_required
def my_acars():
    _need()
    rows = acars.mine(current_user.id)
    return render_template("my_acars.html", rows=rows, online=acars.is_online, privacy=acars.privacy.PRIVACY, full=len(rows) >= cfg_int("acars_max_per_user"), limit=cfg_int("acars_max_per_user"),
                           add_open=request.args.get("add") == "1" or not rows)


@bp.route("/my/acars/add", methods=["POST"])
@login_required
def my_acars_add():
    _need()
    f = request.form
    try:
        lat, lon = _place(f)
    except ValueError as e:
        return action_response(False, str(e), redirect_endpoint="acars.my_acars")
    wid, err = acars.create(current_user.id, f.get("station_id"), f.get("name"), lat, lon, f.get("privacy", "general"), f.get("bands", ""), f.get("hardware", ""), f.get("description", ""), bool(f.get("public")))
    if err:
        return action_response(False, err, redirect_endpoint="acars.my_acars")
    audit("acars.create", target=str(wid))
    flash("ACARS station added.", "success")
    return redirect(url_for("acars.my_acars_station", wid=wid))


@bp.route("/my/acars/<int:wid>")
@login_required
def my_acars_station(wid):
    _need()
    r = _owned(wid)
    return render_template("my_acars_station.html", r=r, addr=_address(r), online=acars.is_online(r), privacy=acars.privacy.PRIVACY, managing=_managing(r), bridge=f"https://{cfg('feed_host') or cfg('main_address') or request.host}/static/acars_bridge.py")


@bp.route("/my/acars/<int:wid>/save", methods=["POST"])
@login_required
def my_acars_save(wid):
    _need()
    r = _owned(wid)
    f = request.form
    try:
        lat, lon = _place(f)
    except ValueError as e:
        return action_response(False, str(e), redirect_endpoint="acars.my_acars_station", redirect_kwargs={"wid": wid})
    if f.get("privacy") not in acars.privacy.PRIVACY:
        return action_response(False, "Choose a location privacy option.", redirect_endpoint="acars.my_acars_station", redirect_kwargs={"wid": wid})
    d = get_db()
    d.execute("UPDATE acars_stations SET name=%s, description=%s, hardware=%s, bands=%s, lat=%s, lon=%s, privacy=%s, public=%s WHERE id=%s",
              (f.get("name", "").strip()[:60], f.get("description", "").strip()[:500], f.get("hardware", "").strip()[:200], f.get("bands", "").strip()[:120], lat, lon, f["privacy"], bool(f.get("public")), wid))
    d.commit()
    audit("acars.update", target=str(wid))
    return action_response(True, "ACARS station saved.", redirect_endpoint="acars.my_acars_station", redirect_kwargs={"wid": wid})


@bp.route("/my/acars/<int:wid>/new-address", methods=["POST"])
@login_required
def my_acars_new_address(wid):
    _need()
    _owned(wid)
    from . import vault
    tok = acars.new_token()
    d = get_db()
    d.execute("UPDATE acars_stations SET token_hash=%s, token_sealed=%s WHERE id=%s", (acars.token_hash(tok), vault.seal(tok), wid))
    d.commit()
    audit("acars.new_address", target=str(wid))
    return action_response(True, "A new upload address was made. The old one no longer works: update your bridge.", redirect_endpoint="acars.my_acars_station", redirect_kwargs={"wid": wid})


@bp.route("/my/acars/<int:wid>/delete", methods=["POST"])
@login_required
def my_acars_delete(wid):
    _need()
    r = _owned(wid)
    d = get_db()
    d.execute("DELETE FROM acars_stations WHERE id=%s", (wid,))
    d.commit()
    audit("acars.delete", target=str(wid), detail=r["station_id"])
    flash("ACARS station deleted. Messages it already sent stay in the history.", "success")
    return redirect(url_for("acars.my_acars") if r["owner_id"] == current_user.id else url_for("acars.admin_acars"))


# ------------------------------------------------------------------ admin

@bp.route("/admin/acars")
@permission_required("manage_feeders")
def admin_acars():
    rows = acars.all_stations()
    stt = acars.stats()
    return render_template("admin_acars.html", active="acars", rows=rows, online=acars.is_online, privacy=acars.privacy.PRIVACY, enabled=acars.enabled(), st=stt["data"], note="" if stt["ok"] else stt["note"],
                           hidden=acars.hidden_rows())


@bp.route("/admin/acars/add", methods=["POST"])
@permission_required("manage_feeders")
def admin_acars_add():
    """Add an ACARS station for a member (to help them): created under their account and opened with their own page, where the upload address is ready to copy."""
    f = request.form
    q = f.get("username", "").strip()
    u = get_db().execute("SELECT id, username FROM users WHERE lower(username) = lower(%s) OR lower(email) = lower(%s)", (q, q)).fetchone()
    if u is None:
        return action_response(False, "No member has that user name or email address. Pick one from the list that appears as you type.", redirect_endpoint="acars.admin_acars")
    try:
        lat, lon = _place(f)
    except ValueError as e:
        return action_response(False, str(e), redirect_endpoint="acars.admin_acars")
    wid, err = acars.create(u["id"], f.get("station_id"), f.get("name"), lat, lon, f.get("privacy", "general"), f.get("bands", ""), f.get("hardware", ""), f.get("description", ""), bool(f.get("public")), by_admin=True)
    if err:
        return action_response(False, err, redirect_endpoint="acars.admin_acars")
    audit("acars.create", target=str(wid), detail=f"for {u['username']}, added by an administrator")
    flash(f"ACARS station added for {u['username']}. This is their page: copy the upload address from it.", "success")
    return redirect(url_for("acars.my_acars_station", wid=wid))


@bp.route("/admin/acars/<int:wid>/<action>", methods=["POST"])
@permission_required("manage_feeders")
def admin_acars_action(wid, action):
    d = get_db()
    row = d.execute("SELECT id, station_id FROM acars_stations WHERE id=%s", (wid,)).fetchone()
    if row is None or action not in ("disable", "enable", "delete"):
        abort(404)
    if action == "delete":
        d.execute("DELETE FROM acars_stations WHERE id=%s", (wid,))
    else:
        d.execute("UPDATE acars_stations SET disabled=%s WHERE id=%s", (action == "disable", wid))
    d.commit()
    audit("acars." + action, target=str(wid), detail=row["station_id"])
    return action_response(True, {"disable": "Station switched off.", "enable": "Station switched on.", "delete": "Station deleted."}[action], redirect_endpoint="acars.admin_acars")


@bp.route("/admin/acars/hide", methods=["POST"])
@permission_required("manage_feeders")
def admin_acars_hide():
    """A takedown: every message of this aircraft is deleted and no new ones are kept or shown."""
    tail = request.form.get("tail", "").strip().upper().lstrip(".")
    if not re.fullmatch(r"[A-Z0-9-]{2,10}", tail):
        return action_response(False, "Enter the aircraft's tail number (registration).", redirect_endpoint="acars.admin_acars")
    ok, d = acars.hide_tail(tail, request.form.get("reason", ""), current_user.id)
    audit("acars.hide", target=tail, detail=request.form.get("reason", "")[:200])
    return action_response(True, f"{tail} is hidden." + ("" if ok else " (The service is away: its stored messages are removed when it answers again; the pages already leave them out.)"), redirect_endpoint="acars.admin_acars")


@bp.route("/admin/acars/unhide", methods=["POST"])
@permission_required("manage_feeders")
def admin_acars_unhide():
    acars.unhide_tail(request.form.get("tail", ""))
    audit("acars.unhide", target=request.form.get("tail", "")[:10])
    return action_response(True, "Aircraft shown again (messages already deleted do not come back).", redirect_endpoint="acars.admin_acars")
