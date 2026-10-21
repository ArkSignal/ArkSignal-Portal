"""APRS pages: the overview list, the map, a station's page, the weather list, and the administrators' page (service status, applying settings, the takedown list).
The logic is in aprs.py; the data comes from the APRS service container."""
import re

from flask import Blueprint, abort, jsonify, redirect, render_template, request
from flask_login import current_user

from . import aprs as ap
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("aprs", __name__)

CALL_RE = re.compile(r"^[A-Za-z0-9]{1,2}[0-9][A-Za-z0-9]{1,4}(-[0-9]{1,2})?$")


def _on():
    if cfg("aprs_enabled") != "1":
        abort(404)


@bp.route("/aprs/")
def overview():
    _on()
    a = request.args
    kind = a.get("kind", "") if a.get("kind", "") in ap.KIND_LABELS else ""
    q = re.sub(r"[^A-Za-z0-9*-]", "", a.get("q", ""))[:12].upper()
    hours = min(max(int(a["hours"]), 1), 168) if a.get("hours", "").isdigit() else cfg_int("aprs_window_hours")
    rows = ap.stations_cached(kind, q, hours)
    from . import wxsources
    rows = [dict(r, airport=wxsources.is_airport_call(r.get("call"))) for r in rows]
    if a.get("airports") == "0":   # the airports' own weather, relayed over APRS, left out
        rows = [r for r in rows if not r["airport"]]
    st = ap.stats()
    return render_template("aprs.html", rows=rows[:400], total=len(rows), st=st, args=a, kinds=ap.KIND_LABELS, hours=hours, kind=kind, q=q, credit=ap.CREDIT)


@bp.route("/aprs/weather")
def weather():
    _on()
    from . import wxsources
    rows = [dict(w, airport=wxsources.is_airport_call(w.get("call"))) for w in ap.weather()]
    hide = request.args.get("airports") == "0"
    if hide:
        rows = [w for w in rows if not w["airport"]]
    return render_template("aprs_weather.html", rows=rows, credit=ap.CREDIT, hide_airports=hide)


@bp.route("/aprs/map")
def aprs_map():
    _on()
    from . import maplayers
    return render_template("aprs_map.html", layers=maplayers.for_map(), label_scale=cfg("live_label_scale"), label_bg=cfg("live_label_bg"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"),
                           wx_refresh=cfg_int("weather_refresh_minutes"), kinds=ap.KIND_LABELS, credit=ap.CREDIT, hours=cfg_int("aprs_window_hours"))


@bp.route("/api/aprs/stations.json")
def api_stations():
    _on()
    rows = ap.stations_cached("", "", cfg_int("aprs_window_hours"))
    keep = ("call", "symbol", "symbol_table", "lat", "lon", "course", "speed_kmh", "comment", "kind", "last_heard")
    from . import wxsources
    out = [dict({k: r.get(k) for k in keep}, airport=wxsources.is_airport_call(r.get("call"))) for r in rows]   # airport: the airport's own weather relayed over APRS
    if request.args.get("airports") == "0":
        out = [r for r in out if not r["airport"]]
    return jsonify({"stations": out, "credit": ap.CREDIT})


@bp.route("/api/aprs/weather.json")
def api_weather():
    _on()
    from . import wxsources
    rows = [dict(w, airport=wxsources.is_airport_call(w.get("call"))) for w in ap.weather()]
    if request.args.get("airports") == "0":
        rows = [w for w in rows if not w["airport"]]
    return jsonify({"weather": rows, "credit": ap.CREDIT})


@bp.route("/api/aprs/<path:call>.json")
def api_station(call):
    _on()
    call = call.replace("~", "/")
    if not CALL_RE.match(call) and not re.match(r"^[A-Za-z0-9 _.\-/]{1,20}$", call):
        abort(404)
    d = ap.station(call)
    if d is None:
        abort(404)
    return jsonify(d)


@bp.route("/aprs/<path:call>")
def station(call):
    _on()
    if "/" in call:   # an old address with a slash in it: send it to the one-segment address (147.090/R is 147.090~R)
        return redirect("/aprs/" + call.replace("/", "~"), code=301)
    call = call.replace("~", "/")
    if not re.match(r"^[A-Za-z0-9 _.\-/]{1,20}$", call):
        abort(404)
    d = ap.station(call.upper())
    if d is None:
        abort(404)
    return render_template("aprs_station.html", cfg_callsign=cfg("callsign_lookup_enabled") == "1", s=d["station"], trail=d["trail"], wx=d["weather"], kinds=ap.KIND_LABELS, credit=ap.CREDIT)


# ------------------------------------------------------------------ administrators

@bp.route("/admin/aprs")
@permission_required("manage_map_data")
def admin():
    return render_template("admin_aprs.html", active="aprs", st=ap.stats(), hidden=ap.hidden(), url=cfg("aprs_url"), filter=ap._filter(), enabled=ap.enabled())


@bp.route("/admin/aprs/status.json")
@permission_required("manage_map_data")
def admin_status():
    st = ap.stats()
    return jsonify({"enabled": ap.enabled(), "ok": st["ok"], "note": st["note"], "stats": st["data"]})


@bp.route("/admin/aprs/apply", methods=["POST"])
@permission_required("manage_map_data")
def admin_apply():
    ok, msg = ap.push_config()
    audit("aprs.apply", detail=msg[:150])
    return action_response(ok, msg, redirect_endpoint="aprs.admin")


@bp.route("/admin/aprs/hide", methods=["POST"])
@permission_required("manage_map_data")
def admin_hide():
    """Take a call sign off every APRS page (a takedown: the owner asked, or it should never have been shown). Kept here and sent to the service."""
    call = re.sub(r"\s", "", request.form.get("call", "")).upper()
    if not CALL_RE.match(call):
        return action_response(False, "Enter a call sign, for example W5ABC or W5ABC-9. A call sign without a dash hides every SSID of it.", redirect_endpoint="aprs.admin")
    reason = request.form.get("reason", "").strip()[:200]
    get_db().execute("INSERT INTO aprs_hidden (call, reason, added_by) VALUES (%s,%s,%s) ON CONFLICT (call) DO UPDATE SET reason=EXCLUDED.reason", (call, reason, current_user.id))
    get_db().commit()
    ok, d = ap.call("/hide", "POST", {"call": call, "reason": reason})
    from .cache import _store
    _store.clear()
    audit("aprs.hide", target=call, detail=reason)
    return action_response(True, f"{call} is hidden from every APRS page." + ("" if ok else " The service did not answer; it will be told when you press Apply."), redirect_endpoint="aprs.admin")


@bp.route("/admin/aprs/unhide", methods=["POST"])
@permission_required("manage_map_data")
def admin_unhide():
    call = request.form.get("call", "").strip().upper()
    get_db().execute("DELETE FROM aprs_hidden WHERE call=%s", (call,))
    get_db().commit()
    ap.call("/unhide", "POST", {"call": call})
    audit("aprs.unhide", target=call)
    return action_response(True, f"{call} may be shown again once it is heard.", redirect_endpoint="aprs.admin")
