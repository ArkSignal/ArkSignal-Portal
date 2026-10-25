"""Downloads of flight data for signed-in members: the flights Flight History shows (CSV, KML, GeoJSON) and the track of one flight (CSV, GPX, KML, GeoJSON).

The history downloads carry what the portal's own log keeps for each flight: callsign, registration, ICAO address, type, when it was first and last heard, the place it was first and last heard, the highest
altitude and speed and how many positions were seen. The line in KML and GeoJSON runs straight between the first and last place heard; it is not the path flown. The path of one flight comes from the hub's own
trace files (the track the Flight Progress page draws), as every position with its time, altitude, speed and heading. Members only, limited per member per day and per download (Settings > Flight History),
audit-logged and counted in `export_log`. The positions are this network's own receivers' data."""
import csv
import io
import json
import re
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from flask import Blueprint, Response, abort, request
from flask_login import current_user, login_required

from . import flightlog
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("flightexport", __name__)
HIST_FORMATS = {"csv": "text/csv", "geojson": "application/geo+json", "kml": "application/vnd.google-earth.kml+xml"}
TRACK_FORMATS = dict(HIST_FORMATS, gpx="application/gpx+xml")
CREDIT = "ArkSignal, the positions were heard by this network's own receivers"


def on():
    return cfg("export_enabled") == "1" and cfg("flightlog_enabled") == "1"


def _left():
    if not current_user.is_authenticated:
        return 0
    n = get_db().execute("SELECT COUNT(*) AS n FROM export_log WHERE user_id=%s AND ts > now() - interval '24 hours'", (current_user.id,)).fetchone()["n"]
    return max(0, cfg_int("export_per_day") - n)


def box():
    return {"on": on(), "signed_in": current_user.is_authenticated, "max": cfg_int("export_max_flights"), "per_day": cfg_int("export_per_day"), "left": _left()}


def _gate():
    """Refuse a download that is off or over the person's daily limit (a plain sentence), else None."""
    if not on():
        abort(404)
    if _left() <= 0:
        return Response(f"You have used your {cfg_int('export_per_day')} downloads for the last 24 hours. Try again later.", status=429, mimetype="text/plain")
    return None


def _log(kind, rows, detail):
    d = get_db()
    d.execute("INSERT INTO export_log (user_id, kind, rows, detail) VALUES (%s,%s,%s,%s)", (current_user.id, kind, rows, detail[:200]))
    d.execute("DELETE FROM export_log WHERE ts < now() - interval '7 days'")
    d.commit()
    audit("export." + kind, detail=f"{rows} rows: {detail}"[:250])


def _iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""


def _stamp(sec):
    return datetime.fromtimestamp(sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _name(f):
    return f["callsign"] or f["reg"] or f["hex"]


# ------------------------------------------------------------------ the flights of Flight History

@bp.route("/history/export.<fmt>")
@login_required
def history(fmt):
    if fmt not in HIST_FORMATS:
        abort(404)
    blocked = _gate()
    if blocked is not None:
        return blocked
    from .live import history_period
    q, start, stop, _dur, _end, _custom, error, _tz = history_period(request.args)
    if error:
        return Response(error, status=400, mimetype="text/plain")
    d = get_db()
    sort = request.args.get("sort", "last") if request.args.get("sort") in flightlog.SORTS else "last"
    direction = "asc" if request.args.get("dir") == "asc" else "desc"
    cap = cfg_int("export_max_flights")
    total = flightlog.count(d, q, start, stop)
    rows = flightlog.search(d, q, start, stop, cap, 0, sort, direction)
    truncated = total > len(rows)
    note = f"Only the first {len(rows)} of {total} flights are included (the limit is set by the site)." if truncated else ""
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["callsign", "registration", "icao_hex", "aircraft_type", "military", "first_heard_utc", "last_heard_utc", "first_lat", "first_lon", "last_lat", "last_lon", "max_altitude_ft", "max_ground_speed_kt", "positions_seen"])
        for f in rows:
            w.writerow([f["callsign"] or "", f["reg"] or "", f["hex"], f["type"] or "", "yes" if f["mil"] else "", _iso(f["first_seen"]), _iso(f["last_seen"]), f["start_lat"], f["start_lon"], f["end_lat"], f["end_lon"], f["max_alt"], f["max_gs"], f["positions"]])
        buf.write("# " + CREDIT + (". " + note if note else "") + "\n")
        body = buf.getvalue()
    elif fmt == "geojson":
        feats = []
        for f in rows:
            props = {"callsign": f["callsign"], "registration": f["reg"], "icao_hex": f["hex"], "aircraft_type": f["type"], "military": bool(f["mil"]), "first_heard_utc": _iso(f["first_seen"]), "last_heard_utc": _iso(f["last_seen"]),
                     "max_altitude_ft": f["max_alt"], "max_ground_speed_kt": f["max_gs"], "positions_seen": f["positions"]}
            a = [f["start_lon"], f["start_lat"]] if f["start_lat"] is not None else None
            b = [f["end_lon"], f["end_lat"]] if f["end_lat"] is not None else None
            geom = {"type": "LineString", "coordinates": [a, b]} if a and b and a != b else ({"type": "Point", "coordinates": a or b} if (a or b) else None)
            feats.append({"type": "Feature", "properties": props, "geometry": geom})
        body = json.dumps({"type": "FeatureCollection", "features": feats, "properties": {"credit": CREDIT, "note": "A line runs straight between the first and the last place a flight was heard; it is not the path flown.", "truncated": truncated, "total": total}})
    else:
        pm = []
        for f in rows:
            a = (f["start_lon"], f["start_lat"]) if f["start_lat"] is not None else None
            b = (f["end_lon"], f["end_lat"]) if f["end_lat"] is not None else None
            desc = f"{f['reg'] or ''} {f['type'] or ''} {f['hex']}, {_iso(f['first_seen'])} to {_iso(f['last_seen'])}, up to {f['max_alt'] if f['max_alt'] is not None else '?'} ft, {f['max_gs'] if f['max_gs'] is not None else '?'} kt".strip()
            geom = ""
            if a and b and a != b:
                geom = f"<LineString><tessellate>1</tessellate><coordinates>{a[0]},{a[1]},0 {b[0]},{b[1]},0</coordinates></LineString>"
            elif a or b:
                p = a or b
                geom = f"<Point><coordinates>{p[0]},{p[1]},0</coordinates></Point>"
            pm.append(f"<Placemark><name>{escape(_name(f))}</name><description>{escape(desc)}</description>{geom}</Placemark>")
        body = ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Flight History</name>'
                f"<description>{escape(CREDIT + '. A line runs straight between the first and last place a flight was heard; it is not the path flown.' + (' ' + note if note else ''))}</description>" + "".join(pm) + "</Document></kml>\n")
    _log("flights", len(rows), f"{fmt}, q={q or '-'}, {start:%Y-%m-%d %H:%M} to {stop:%Y-%m-%d %H:%M}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(body, mimetype=HIST_FORMATS[fmt], headers={"Content-Disposition": f'attachment; filename="flight-history-{stamp}.{fmt}"', "X-Export-Rows": str(len(rows)), "X-Export-Total": str(total), "Cache-Control": "no-store"})


# ------------------------------------------------------------------ the track of one flight

@bp.route("/flight/<hex_>/track.<fmt>")
@login_required
def track(hex_, fmt):
    hex_ = hex_.lower()
    if fmt not in TRACK_FORMATS or not re.fullmatch(r"[0-9a-f]{6}", hex_):
        abort(404)
    blocked = _gate()
    if blocked is not None:
        return blocked
    try:
        at = float(request.args["at"]) if request.args.get("at") else None
    except ValueError:
        at = None
    from .live import trace_data
    t = trace_data(hex_, at)
    pts = (t.get("points") or [])[t.get("leg") or 0: t.get("end") or None]
    if not pts:
        abort(404)
    name = (t.get("flight") or t.get("r") or hex_).strip() or hex_
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time_utc", "latitude", "longitude", "altitude_ft", "ground_speed_kt", "track_deg", "vertical_rate_fpm"])
        for p in pts:
            w.writerow([_stamp(p[0]), p[1], p[2], p[3], p[4], p[5], p[6] if len(p) > 6 else ""])
        buf.write("# " + CREDIT + "\n")
        body = buf.getvalue()
    elif fmt == "geojson":
        feat = {"type": "Feature", "properties": {"icao_hex": hex_, "name": name, "start_utc": _stamp(pts[0][0]), "end_utc": _stamp(pts[-1][0]), "credit": CREDIT},
                "geometry": {"type": "LineString", "coordinates": [[p[2], p[1], round((p[3] or 0) * 0.3048, 1)] for p in pts]}}
        body = json.dumps({"type": "FeatureCollection", "features": [feat]})
    elif fmt == "kml":
        coords = " ".join(f"{p[2]},{p[1]},{round((p[3] or 0) * 0.3048, 1)}" for p in pts)
        body = ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>' + escape(name) + '</name><description>' + escape(CREDIT) + '</description>'
                f'<Placemark><name>{escape(name)}</name><LineString><altitudeMode>absolute</altitudeMode><coordinates>{coords}</coordinates></LineString></Placemark></Document></kml>\n')
    else:
        seg = "".join(f'<trkpt lat="{p[1]}" lon="{p[2]}"><ele>{round((p[3] or 0) * 0.3048, 1)}</ele><time>{_stamp(p[0])}</time></trkpt>' for p in pts)
        body = ('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="ArkSignal" xmlns="http://www.topografix.com/GPX/1/1"><metadata><desc>' + escape(CREDIT) + f'</desc></metadata><trk><name>{escape(name)}</name><trkseg>{seg}</trkseg></trk></gpx>\n')
    _log("track", len(pts), f"{fmt}, {hex_}")
    return Response(body, mimetype=TRACK_FORMATS[fmt], headers={"Content-Disposition": f'attachment; filename="{hex_}-{_stamp(pts[0][0])[:10]}.{fmt}"', "X-Export-Rows": str(len(pts)), "Cache-Control": "no-store"})
