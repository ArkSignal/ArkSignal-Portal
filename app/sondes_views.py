"""Radiosondes and weather balloons: the public pages and data files, the owner's pages for claiming receiving stations, and the admin page. The logic is in sondes.py."""
import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, url_for, flash
from flask_login import current_user, login_required

from . import csrf
from . import sondes as sd
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .cache import cached
from .clientip import client_ip
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("sondes", __name__)
from .privacy import PRIVACY   # the one list of location choices: Exact, Near, Close, General, Wide, Hidden
CREDIT = "Telemetry: SondeHub, Creative Commons BY-SA 2.0"
HOURS = [(1, "1 hour"), (2, "2 hours"), (3, "3 hours"), (6, "6 hours"), (12, "12 hours"), (24, "24 hours"), (48, "2 days"), (72, "3 days"), (168, "7 days"), (720, "30 days"), (8760, "1 year")]


def _need():
    if not sd.enabled():
        abort(404)


def _ctx(**kw):
    return dict(sonde_live=cfg_int("sonde_live_seconds"), credit=CREDIT, **kw)


def _hours(default=None):
    try:
        h = int(request.args.get("hours", default or cfg_int("sonde_map_hours")))
    except ValueError:
        h = cfg_int("sonde_map_hours")
    return min(max(h, 1), 8760)


def _map_hours():
    """The map's live windows stop at Settings > Radiosondes > Radiosonde Map Longest Window (48 hours); older flights are history with dates."""
    return min(_hours(), max(1, cfg_int("sonde_map_max_hours")))


def _history_range():
    """(since, until, note) from ?from=YYYY-MM-DD&to=YYYY-MM-DD in the site's time zone, limited to Settings > Radiosonde Map History Range Limit; (None, None, "") when no dates were asked for."""
    from datetime import date, time as dtime
    from zoneinfo import ZoneInfo
    try:
        a = date.fromisoformat(request.args.get("from", ""))
    except ValueError:
        return None, None, ""
    try:
        b = date.fromisoformat(request.args.get("to", "")) 
    except ValueError:
        b = a
    if b < a:
        a, b = b, a
    note = ""
    most = max(1, cfg_int("sonde_history_max_days"))
    if (b - a).days + 1 > most:
        a = b - timedelta(days=most - 1)
        note = f"The range was cut to {most} days (Settings > Radiosondes > Radiosonde Map History Range Limit); showing {a.isoformat()} to {b.isoformat()}."
    try:
        tz = ZoneInfo(cfg("timezone"))
    except Exception:
        tz = timezone.utc
    return datetime.combine(a, dtime.min, tzinfo=tz), datetime.combine(b + timedelta(days=1), dtime.min, tzinfo=tz), note


# ------------------------------------------------------------------ public pages

@bp.route("/sondes/")
def dashboard():
    _need()
    from . import states as st
    codes = st.chosen(cfg("sonde_overview_states"))   # Settings > Radiosondes > Radiosondes Overview Shows (Arkansas only by default)
    aloft = sd.flights(hours=48, status="aloft", limit=50, states=codes)[1]
    recent = sd.flights(hours=168, limit=15, states=codes)[1]
    phases = [sd.phase(r) for r in aloft]
    sites = [x for x in sd.map_sites(nws_only=True) if st.state_of(x["lat"], x["lon"]) in codes]
    return render_template("sondes.html", aloft=aloft, recent=recent, climbing=phases.count("climbing"), falling=phases.count("falling"), counts=sd.counts(codes), sites=sites, shown_states=[st.NAMES[c] for c in codes], feed=sd.feed_stats(), alive=sd.feed_alive(), **_ctx())


@bp.route("/sondes/map")
def sonde_map():
    _need()
    from . import maplayers
    from . import sonde_reports as sr
    return render_template("sondes_map.html", chase_share=sr.mode("sonde_chase_mode"), chase_name=sr.chase_name(current_user) if current_user.is_authenticated else "", chase_hours=cfg_int("sonde_chase_max_hours"),
                           chase_seconds=max(5, cfg_int("sonde_chase_min_seconds")), layers=maplayers.for_map(), label_scale=cfg("live_label_scale"), label_bg=cfg("live_label_bg"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"),
                           map_zoom=cfg("map_zoom"), wx_refresh=cfg_int("weather_refresh_minutes"), hours=min(cfg_int("sonde_map_hours"), max(1, cfg_int("sonde_map_max_hours"))), hours_choices=[h for h in HOURS if h[0] <= max(1, cfg_int("sonde_map_max_hours"))], history_max_days=cfg_int("sonde_history_max_days"), **_ctx())


@bp.route("/sondes/flights")
def flight_list():
    _need()
    a = request.args
    try:
        page = max(1, int(a.get("page", 1)))
    except ValueError:
        page = 1
    per = 40
    hours = int(a.get("hours", 168)) if a.get("hours", "168").isdigit() else 168
    total, rows = sd.flights(hours=hours or None, agency=a.get("agency", ""), status=a.get("status", ""), q=a.get("q", "").strip(), kind=a.get("kind", ""), typ=a.get("type", ""), site=a.get("site", ""), station=a.get("station", ""), recovery=a.get("recovery", ""),
                             limit=per, offset=(page - 1) * per, sort=a.get("sort", "last"), direction=a.get("dir", "desc"))
    types = [r["type"] for r in get_db().execute("SELECT DISTINCT type FROM sondes WHERE type <> '' ORDER BY type").fetchall()]
    sites = [dict(r) for r in get_db().execute("SELECT DISTINCT site_id, site_name FROM sondes WHERE site_id <> '' ORDER BY site_name").fetchall()]
    heard = [r["callsign"] for r in get_db().execute("SELECT DISTINCT callsign FROM sonde_heard WHERE callsign <> '' ORDER BY callsign").fetchall()]
    if a.get("station") and a["station"] not in heard:
        heard.append(a["station"])   # a station named in a link stays selected even when nobody has heard a balloon from it yet
    return render_template("sondes_flights.html", heard=heard, rows=rows, total=total, page=page, pages=max(1, -(-total // per)), args=a, hours=hours, hours_choices=HOURS + [(0, "All time")], types=types, sites=sites, archive_on=cfg("sonde_archive_enabled") == "1", **_ctx())


def _is_admin():
    return current_user.is_authenticated and current_user.has_permission("manage_sondes")


def heard_list(row, latest=None):
    """The stations that heard a flight, with their latest signal. The azimuth, elevation and range from a station are given only where the station's own position may be known to the
    viewer: its owner, an administrator, or anyone when the owner chose Exact (the same rule as the station's position itself)."""
    now = datetime.now(timezone.utc)
    out = []
    for h in sd.heard_by(row["serial"]):
        sees = h["lat"] is not None and (h["privacy"] == "exact" or (current_user.is_authenticated and (h["owner_id"] == current_user.id or _is_admin())))
        look = None
        if sees and latest and latest["lat"] is not None:
            az, el, rng = sd.look_angles(h["lat"], h["lon"], h["st_alt"], latest["lat"], latest["lon"], latest["alt"])
            look = {"az": round(az), "el": round(el, 1), "range_km": round(rng, 1)}
        out.append({"callsign": h["callsign"], "packets": h["packets"], "first": h["first_ts"].isoformat() if h["first_ts"] else None, "last": h["last_ts"].isoformat() if h["last_ts"] else None,
                    "age": int((now - h["last_ts"]).total_seconds()) if h["last_ts"] else None, "best_snr": h["best_snr"], "snr": h["last_snr"], "freq": h["last_freq"], "claimed": h["claim_status"] == "claimed", "look": look})
    return out


def latest_point(row):
    p = get_db().execute("SELECT * FROM sonde_points WHERE serial=%s ORDER BY ts DESC LIMIT 1", (row["serial"],)).fetchone()
    if p is None:
        return None
    return {"time": p["ts"].isoformat(), "frame": p["frame"], "lat": p["lat"], "lon": p["lon"], "alt": p["alt"], "vel_v": p["vel_v"], "vel_h": p["vel_h"], "heading": p["heading"], "temp": p["temp"], "hum": p["hum"],
            "press": p["press"], "sats": p["sats"], "batt": p["batt"], "freq": p["freq"], "snr": p["snr"], "age": int((datetime.now(timezone.utc) - p["ts"]).total_seconds())}


def recovery_dict(row):
    recs = sd.recoveries_for(row["serial"])
    return [{"recovered": r["recovered"], "planned": r["planned"], "state": "recovered" if r["recovered"] else "planned" if r["planned"] else "notfound", "by": r["recovered_by"], "note": r["description"], "time": r["ts"].isoformat(), "lat": r["lat"], "lon": r["lon"], "software": r["software"]} for r in recs]


@bp.route("/sondes/<serial>")
def flight_page(serial):
    _need()
    row = sd.get(serial)
    if row is None:
        abort(404)
    st = sd.summary(row)
    latest = latest_point(row)
    from . import sonde_reports as sr
    rep = {"mode": sr.mode("sonde_report_mode"), "by": sr.default_name(current_user) if current_user.is_authenticated else "",
           "lat": row["landing_lat"] if row["landing_lat"] is not None else row["last_lat"], "lon": row["landing_lon"] if row["landing_lon"] is not None else row["last_lon"],
           "alt": round(row["landing_alt"]) if row["landing_alt"] is not None else (round(row["last_alt"]) if row["last_alt"] is not None else None)}
    return render_template("sonde.html", rep=rep, f=row, summ=st, heard=heard_list(row, latest), latest=latest, recs=recovery_dict(row), phase=sd.phase(row), phase_label=sd.phase_label(row), **_ctx())


def _track_dict(row):
    pts = sd.track(row["serial"])
    cols = ["t", "lat", "lon", "alt", "vv", "vh", "hdg", "temp", "hum", "press", "sats", "batt", "snr"]
    data = {c: [] for c in cols}
    for p in pts:
        data["t"].append(int(p["ts"].timestamp())); data["lat"].append(round(p["lat"], 5)); data["lon"].append(round(p["lon"], 5))
        data["alt"].append(None if p["alt"] is None else round(p["alt"], 1)); data["vv"].append(p["vel_v"]); data["vh"].append(p["vel_h"]); data["hdg"].append(p["heading"])
        data["temp"].append(p["temp"]); data["hum"].append(p["hum"]); data["press"].append(p["press"]); data["sats"].append(p["sats"]); data["batt"].append(p["batt"]); data["snr"].append(p["snr"])
    return data


@bp.route("/api/sondes/<serial>/track.json")
def api_track(serial):
    _need()
    row = sd.get(serial)
    if row is None:
        abort(404)
    latest = latest_point(row)
    out = {"serial": row["serial"], "status": row["status"], "phase": sd.phase(row), "label": sd.phase_label(row), "type": row["type"], "agency": row["agency"], "site": row["site_name"], "track": _track_dict(row), "credit": CREDIT,
           "last": row["last_seen"].isoformat(), "pred": [row["pred_lat"], row["pred_lon"]] if row["pred_lat"] is not None and row["status"] == "aloft" else None,
           "pred_path": json.loads(row["pred_path"]) if row["pred_path"] and row["status"] == "aloft" else None,
           "launch": [row["launch_lat"], row["launch_lon"]] if row["launch_lat"] is not None else None, "landing": [row["landing_lat"], row["landing_lon"]] if row["landing_lat"] is not None else None,
           "burst": [row["max_lat"], row["max_lon"], row["max_alt"]] if row["max_lat"] is not None and sd.phase(row) in ("falling", "landed", "lost") else None,
           "max_alt": row["max_alt"], "latest": latest, "heard": heard_list(row, latest), "recoveries": recovery_dict(row), "frames": row["frames"], "frequency": row["frequency"], "summary": sd.summary(row)}
    resp = jsonify(out)
    resp.headers["Cache-Control"] = "private, max-age=3"
    return resp


@bp.route("/sondes/<serial>/track.csv")
def track_csv(serial):
    _need()
    row = sd.get(serial)
    if row is None:
        abort(404)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time_utc", "latitude", "longitude", "altitude_m", "vertical_speed_ms", "horizontal_speed_ms", "heading_deg", "temperature_c", "humidity_percent", "pressure_hpa", "satellites", "battery_v", "snr_db", "heard_by"])
    for p in sd.track(row["serial"], max_points=1000000):
        w.writerow([p["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"), p["lat"], p["lon"], p["alt"], p["vel_v"], p["vel_h"], p["heading"], p["temp"], p["hum"], p["press"], p["sats"], p["batt"], p["snr"], p["uploader"]])
    buf.write("# " + CREDIT + " (https://sondehub.org)\n")
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{row["serial"]}.csv"'})


@bp.route("/sondes/<serial>/track.geojson")
def track_geojson(serial):
    _need()
    row = sd.get(serial)
    if row is None:
        abort(404)
    pts = sd.track(row["serial"], max_points=1000000)
    feat = {"type": "Feature", "properties": {"serial": row["serial"], "type": row["type"], "agency": row["agency"], "site": row["site_name"], "license": CREDIT},
            "geometry": {"type": "LineString", "coordinates": [[p["lon"], p["lat"], p["alt"] or 0] for p in pts]}}
    return Response(json.dumps({"type": "FeatureCollection", "features": [feat]}), mimetype="application/geo+json", headers={"Content-Disposition": f'attachment; filename="{row["serial"]}.geojson"'})


@bp.route("/sondes/sites/<site_id>")
def site_page(site_id):
    _need()
    s = next((x for x in sd.sites() if x["site_id"] == site_id), None)
    if s is None:
        abort(404)
    total, rows = sd.flights(site=site_id, limit=30, hours=None)
    return render_template("sonde_site.html", s=s, rows=rows, total=total, **_ctx())


@bp.route("/sondes/stations/")
def station_list():
    _need()
    rows = []
    for s in get_db().execute("SELECT s.*, u.username AS owner_name, u.display_name AS owner_display, "
                              "(SELECT COUNT(*) FROM sonde_heard h WHERE lower(h.callsign) = lower(s.callsign)) AS flights FROM sonde_stations s LEFT JOIN users u ON u.id = s.owner_id "
                              "WHERE s.public AND NOT s.disabled ORDER BY s.last_seen DESC NULLS LAST, s.callsign").fetchall():
        rows.append({"row": s, "loc": sd.public_position(s)})
    return render_template("sonde_stations.html", rows=rows, **_ctx())


@bp.route("/sondes/stations/<path:callsign>")
def station_page(callsign):
    _need()
    if "/" in callsign:   # an address with a slash in it: sent on to the one-segment form (KFS/SE is KFS~SE)
        return redirect("/sondes/stations/" + callsign.replace("/", "~"), code=301)
    s = sd.station(callsign.replace("~", "/"))
    if s is None or s["disabled"] or not s["public"]:
        abort(404)
    flights = sd.flights(station=s["callsign"], limit=40, hours=None)[1]
    owner = (s["owner_display"] or s["owner_name"] or "") if s["claim_status"] == "claimed" else ""
    return render_template("sonde_station.html", s=s, loc=sd.public_position(s), flights=flights, owner=owner, approx=s["privacy"] != "exact", radius_m=__import__("app.privacy", fromlist=["x"]).radius_m(s["privacy"] or "general"), **_ctx())


# ------------------------------------------------------------------ data for the maps

@bp.route("/api/sondes/map.json")
def api_map():
    _need()
    hours = _map_hours()
    agency = request.args.get("agency", "")
    kind = request.args.get("kind", "")
    q = request.args.get("q", "").strip()
    since, until, note = _history_range()
    one = request.args.get("serial", "").strip()[:40]   # "Show on Radiosonde Map" from a flight's page: that one flight, whatever its age
    site = re.sub(r"[^A-Za-z0-9_-]", "", request.args.get("site", ""))[:12]   # the flights of one launch site (its flyout's "past flights")
    key = ("sonde_map", hours, agency, kind, q, since, until, one, site)
    flights = cached(key, lambda: sd.map_flights(hours, agency, kind, q, since, until, one, site), seconds=min(5, cfg_int("sonde_live_seconds")) if since is None else 60)
    out = {"flights": flights, "credit": CREDIT, "now": datetime.now(timezone.utc).isoformat(), "history": since is not None, "note": note}
    if since is not None:
        out["range"] = [since.date().isoformat(), (until - timedelta(days=1)).date().isoformat()]
    if request.args.get("static", "1") != "0":
        out["stations"] = sd.map_stations()   # not cached: an owner's privacy choice must show at once
        out["sites"] = cached(("sonde_map_sites",), lambda: sd.map_sites(), seconds=300)
    resp = jsonify(out)
    resp.headers["Cache-Control"] = "public, max-age=5"
    return resp


@bp.route("/api/sondes/stations.json")
def api_stations():
    """The public receiving stations (positions as their owners chose, with how far each has heard a balloon), for the coverage map."""
    _need()
    resp = jsonify({"stations": sd.map_stations(), "credit": CREDIT})
    resp.headers["Cache-Control"] = "public, max-age=30"
    return resp


@bp.route("/api/sondes/search.json")
def api_search():
    _need()
    q = request.args.get("q", "")
    resp = jsonify({"results": cached(("sonde_search", q.lower()), lambda: sd.search(q), seconds=20)})
    resp.headers["Cache-Control"] = "public, max-age=20"
    return resp


# ------------------------------------------------------------------ the owner's pages

@bp.route("/my/sondes")
@login_required
def my_sondes():
    claimed = sd.auto_claim_for_user(current_user)
    if claimed:
        flash("Claimed for you, because the call sign matches your profile: " + ", ".join(claimed) + ".", "success")
    rows = sd.my_stations(current_user.id)
    return render_template("my_sondes.html", rows=rows, full=len(rows) >= cfg_int("sonde_max_per_user"), limit=cfg_int("sonde_max_per_user"), mode=cfg("sonde_claim_mode"), my_call=getattr(current_user, "callsign", "") or "",
                           enabled=sd.enabled(), auto=cfg("sonde_auto_claim") == "1")


@bp.route("/my/sondes/claim", methods=["POST"])
@login_required
def my_sondes_claim():
    status, msg = sd.claim(current_user, request.form.get("callsign", ""))
    if status:
        audit("sondes.claim", target=request.form.get("callsign", "")[:40], detail=status)
    return action_response(bool(status), msg, redirect_endpoint="sondes.my_sondes")


def _owned(sid):
    """The member's own station; an administrator who may manage feeders can open anyone's with the same pages (to help someone), and what they do is in the audit log under their name."""
    d = get_db()
    r = d.execute("SELECT * FROM sonde_stations WHERE id=%s AND owner_id=%s", (sid, current_user.id)).fetchone()
    if r is None and _is_admin():
        r = d.execute("SELECT * FROM sonde_stations WHERE id=%s AND owner_id IS NOT NULL", (sid,)).fetchone()
    if r is None:
        abort(404)
    return r


def _managing(r):
    """The owner's name when an administrator is looking at someone else's station, else empty (the page then shows a banner)."""
    if r["owner_id"] == current_user.id:
        return ""
    u = get_db().execute("SELECT username, display_name FROM users WHERE id=%s", (r["owner_id"],)).fetchone()
    return (u["display_name"] or u["username"]) if u else "another member"


@bp.route("/my/sondes/<int:sid>")
@login_required
def my_sonde_station(sid):
    r = _owned(sid)
    flights = sd.flights(station=r["callsign"], limit=15, hours=None)[1]
    base = _push_base()
    return render_template("my_sonde_station.html", r=r, push=(f"{base}/sondes/push/{sd.push_token_of(r)}" if r["push_hash"] else ""), bridge=f"{base}/static/sonde_bridge.py", privacy=PRIVACY, flights=flights, loc=sd.public_position(r), managing=_managing(r), **_ctx())


def _push_base():
    return f"https://{cfg('feed_host') or cfg('main_address') or request.host}"


@bp.route("/sondes/push/<token>", methods=["POST"])
@csrf.exempt
def push(token):
    """A claimed station's bridge posts what auto_rx heard: one JSON object, a list, or one per line (auto_rx's payload summary). Unknown, unclaimed and switched-off stations look the same."""
    _need()
    from . import acars
    row = sd.station_by_push_token(token)
    if row is None or row["disabled"] or row["claim_status"] != "claimed":
        if acars.too_many_bad_tokens(client_ip(), note=True):
            return "slow down", 429
        abort(404)
    if (request.content_length or 0) > 1_000_000:
        return "too large", 413
    raw = request.get_data(cache=False).decode("utf-8", "replace").strip()
    try:
        data = json.loads(raw)
        items = data if isinstance(data, list) else [data]
    except ValueError:
        items = []
        for ln in raw.splitlines():
            try:
                items.append(json.loads(ln))
            except ValueError:
                pass
    if not items:
        return jsonify({"ok": False, "error": "no frames"}), 400
    n, bad = sd.push_frames(row, items)
    return jsonify({"ok": True, "accepted": n, "ignored": bad})


@bp.route("/my/sondes/<int:sid>/push-address", methods=["POST"])
@login_required
def my_sonde_push_address(sid):
    r = _owned(sid)
    if r["claim_status"] != "claimed":
        return action_response(False, "Claim the station first: the upload address belongs to a claimed station.", redirect_endpoint="sondes.my_sonde_station", redirect_kwargs={"sid": sid})
    sd.make_push_token(sid)
    audit("sondes.push_address", target=r["callsign"])
    return action_response(True, "Upload address made." if not r["push_hash"] else "A new upload address was made. The old one no longer works: update your bridge.", redirect_endpoint="sondes.my_sonde_station", redirect_kwargs={"sid": sid})


@bp.route("/my/sondes/<int:sid>/save", methods=["POST"])
@login_required
def my_sonde_save(sid):
    r = _owned(sid)
    f = request.form
    vals = {"display_name": f.get("display_name", "").strip()[:60], "notes": f.get("notes", "").strip()[:500]}
    if r["claim_status"] == "claimed":   # privacy and listing are the owner's choice once the station is theirs (before that the form does not offer them)
        if f.get("privacy") not in PRIVACY:
            return action_response(False, "Choose a location privacy option.", redirect_endpoint="sondes.my_sonde_station", redirect_kwargs={"sid": sid})
        vals.update(privacy=f["privacy"], public=bool(f.get("public")))
    d = get_db()
    d.execute("UPDATE sonde_stations SET " + ", ".join(f"{k}=%s" for k in vals) + " WHERE id=%s", list(vals.values()) + [sid])
    d.commit()
    audit("sondes.station_update", target=r["callsign"])
    return action_response(True, "Station saved.", redirect_endpoint="sondes.my_sonde_station", redirect_kwargs={"sid": sid})


@bp.route("/my/sondes/<int:sid>/release", methods=["POST"])
@login_required
def my_sonde_release(sid):
    r = _owned(sid)
    d = get_db()
    d.execute("UPDATE sonde_stations SET owner_id=NULL, claim_status='unclaimed', claim_code='', claimed_at=NULL, display_name='', notes='', privacy=%s, public=TRUE WHERE id=%s", (cfg("sonde_station_privacy"), sid))
    d.commit()
    audit("sondes.release", target=r["callsign"])
    flash("Station released. It stays in the list with its position shown the default way.", "success")
    return redirect(url_for("sondes.my_sondes") if r["owner_id"] == current_user.id else url_for("sondes.admin_sondes"))


# ------------------------------------------------------------------ admin

@bp.route("/admin/sondes")
@permission_required("manage_sondes")
def admin_sondes():
    d = get_db()
    pending = d.execute("SELECT s.*, u.username AS owner_name, u.callsign AS owner_callsign FROM sonde_stations s JOIN users u ON u.id = s.owner_id WHERE s.claim_status='pending' ORDER BY s.callsign").fetchall()
    claimed = d.execute("SELECT s.*, u.username AS owner_name, u.callsign AS owner_callsign FROM sonde_stations s JOIN users u ON u.id = s.owner_id WHERE s.claim_status='claimed' ORDER BY s.callsign").fetchall()
    stations = d.execute("SELECT * FROM sonde_stations WHERE owner_id IS NULL ORDER BY last_seen DESC NULLS LAST, callsign LIMIT 200").fetchall()
    pts = d.execute("SELECT COUNT(*) AS n FROM sonde_points").fetchone()["n"]
    return render_template("admin_sondes.html", active="sondes", privacy=PRIVACY, default_privacy=cfg("sonde_station_privacy"), pending=pending, claimed=claimed, stations=stations, counts=sd.counts(), feed=sd.feed_stats(), alive=sd.feed_alive(), points=pts,
                           enabled=sd.enabled(), sites_summary=cfg_value("sonde_sites_summary"), live=cfg("sonde_live_enabled") == "1", predict_url=cfg("predict_url"))


@bp.route("/admin/sondes/history/status.json")
@permission_required("manage_sondes")
def admin_history_status():
    from . import sonde_history as sh
    c = sh.counts(get_db())
    p = sh.progress()
    return jsonify({"enabled": cfg("sonde_history_enabled") == "1", "sites": sh.sites(), "since": cfg("sonde_history_since"), "counts": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in c.items()}, "progress": p})


@bp.route("/admin/sondes/history/<action>", methods=["POST"])
@permission_required("manage_sondes")
def admin_history_action(action):
    """Switch the historical flights job on or off (the same as the setting)."""
    from .settings import set_setting
    if action not in ("start", "stop"):
        abort(404)
    set_setting("sonde_history_enabled", "1" if action == "start" else "0")
    audit("sondes.history_" + action)
    return action_response(True, "The historical flights job is on: it works in the background, newest month first." if action == "start" else "The historical flights job is off; it stops after the flight it is on.", redirect_endpoint="sondes.admin_sondes")


@bp.route("/admin/sondes/predict/status.json")
@permission_required("manage_sondes")
def admin_predict_status():
    from . import sonde_predict as sp
    sp_stats = sp.stats()
    return jsonify({"enabled": sp.enabled(), "ok": sp_stats["ok"], "note": sp_stats["note"], "stats": sp_stats["data"]})


@bp.route("/admin/sondes/predict/<action>", methods=["POST"])
@permission_required("manage_sondes")
def admin_predict_action(action):
    """Apply the prediction settings to the service, or start or stop its archive import."""
    from . import sonde_predict as sp
    if action == "apply":
        ok, msg = sp.push_config()
        text = "Settings sent to the prediction service." if ok else msg
    elif action == "start":
        ok, d = sp.start_import()
        text = ("The archive import started." if d.get("started") else "The archive import is already running.") if ok else d
    elif action == "stop":
        ok, d = sp.stop_import()
        text = "The archive import will stop after the flight it is on." if ok else d
    elif action == "gfs":
        ok, d = sp.gfs_refresh(True)
        text = ("Reading the newest GFS cycle now: it takes a few minutes." if d.get("started") else "The service is already reading a cycle.") if ok else d
    elif action == "push":
        ok, d = sp.push_recent(get_db())
        text = f"Sent the flights recorded here to the service ({d.get('stored', 0)} stored)." if ok else d
    else:
        abort(404)
    audit("sondes.predict_" + action, detail=str(text)[:150])
    return action_response(ok, text, redirect_endpoint="sondes.admin_sondes")


@bp.route("/api/sondes/predictions.json")
def api_predictions():
    """Forecast landing areas of the next launches (the Radiosonde Map layer and the forecast cards), from the prediction service."""
    _need()
    from . import sonde_predict as sp
    from datetime import timedelta
    if not sp.enabled():
        return jsonify({"enabled": False, "predictions": [], "note": ""})
    method = sp.clean_method(request.args.get("method"))
    f = sp.forecast(method)
    names = sp.site_names()
    cut = datetime.now(timezone.utc) + timedelta(hours=cfg_int("predict_map_hours"))
    out = []
    for p in f["predictions"]:
        try:
            when = datetime.fromisoformat(p["launch"])
        except ValueError:
            continue
        if when <= cut:
            out.append(dict(p, name=names.get(p["site"], p["site"])))
    avail = [m for m in (f.get("methods") or ["statistical"]) if m in sp.METHOD_NAMES]
    return jsonify({"enabled": True, "predictions": out, "scores": f["scores"], "note": f["note"], "credit": CREDIT, "method": method, "methods": [{"id": m, "name": sp.METHOD_NAMES[m]} for m in avail],
                    "default_method": sp.clean_method("")})


@bp.route("/sondes/statistics")
def statistics_page():
    """Flights, burst heights, flight times and receiving stations with filters: a flight, a station, a launch site, the source, the status and dates."""
    _need()
    from . import sondestats
    return render_template("sondes_stats.html", sources=sondestats.SOURCES, **_ctx())


@bp.route("/api/sondes/stats.json")
def api_stats():
    _need()
    from . import sondestats
    from .clientip import client_ip
    from .drivetimes import allowed
    if not allowed(client_ip()):
        return jsonify({"error": "slow down"}), 429
    return jsonify(cached(("sondestats", tuple(sorted(request.args.items()))), lambda: sondestats.stats(request.args), 30))


@bp.route("/api/sondes/stats-find.json")
def api_stats_find():
    _need()
    from . import sondestats
    q = request.args.get("q", "").strip()[:30]
    return jsonify(sondestats.find(q) if len(q) >= 2 else {"flights": [], "stations": [], "sites": []})


@bp.route("/api/sondes/forecast.json")
def api_forecast_at():
    """?site=<id>&when=<ISO date and time>: where a balloon launched from that site at that time should fly and land (the prediction service's method, kept ten minutes)."""
    _need()
    from . import sonde_predict as sp
    from .clientip import client_ip
    from .drivetimes import allowed
    if not sp.enabled():
        return jsonify({"enabled": False, "prediction": None, "note": ""})
    if not allowed(client_ip()):
        return jsonify({"enabled": True, "prediction": None, "note": "Too many questions just now; wait a moment."}), 429
    method = sp.clean_method(request.args.get("method"))
    r = sp.forecast_at(request.args.get("site", "").strip(), request.args.get("when", "").strip()[:32], method)
    p = r.get("prediction")
    if p:
        p = dict(p, name=sp.site_names().get(p["site"], p["site"]))
    return jsonify({"enabled": True, "prediction": p, "note": r.get("note", ""), "method": method, "method_name": sp.METHOD_NAMES[method]})


def cfg_value(k):
    from .settings import get_setting
    return get_setting(k) or ""


@bp.route("/admin/sondes/assign", methods=["POST"])
@permission_required("manage_sondes")
def admin_sonde_assign():
    """Give a station to a member, or start the claim for them."""
    f = request.form
    mode = "claim" if f.get("mode") == "claim" else "assign"
    if f.get("privacy") and f["privacy"] not in PRIVACY:
        return action_response(False, "Choose a location privacy option.", redirect_endpoint="sondes.admin_sondes")
    details = {"display_name": f.get("display_name", "").strip()[:60], "notes": f.get("notes", "").strip()[:500]}
    if f.get("privacy"):
        details.update(privacy=f["privacy"], public=bool(f.get("public")))
    ok, msg, row = sd.assign(f.get("callsign", ""), f.get("username", ""), mode, details)
    if ok:
        audit("sondes." + mode, target=row["callsign"], detail="for " + f.get("username", "").strip()[:60])
    return action_response(ok, msg, redirect_endpoint="sondes.admin_sondes")


@bp.route("/admin/sondes/<int:sid>/<action>", methods=["POST"])
@permission_required("manage_sondes")
def admin_sonde_action(sid, action):
    d = get_db()
    r = d.execute("SELECT * FROM sonde_stations WHERE id=%s", (sid,)).fetchone()
    if r is None or action not in ("approve", "reject", "disable", "enable", "release"):
        abort(404)
    if action == "approve":
        d.execute("UPDATE sonde_stations SET claim_status='claimed', claimed_at=now() WHERE id=%s AND owner_id IS NOT NULL", (sid,))
    elif action in ("reject", "release"):
        d.execute("UPDATE sonde_stations SET owner_id=NULL, claim_status='unclaimed', claim_code='', claimed_at=NULL WHERE id=%s", (sid,))
    else:
        d.execute("UPDATE sonde_stations SET disabled=%s WHERE id=%s", (action == "disable", sid))
    d.commit()
    audit("sondes." + action, target=r["callsign"])
    return action_response(True, {"approve": "Claim approved.", "reject": "Claim rejected.", "release": "Station released from its owner.", "disable": "Station hidden from the public pages.", "enable": "Station shown again."}[action],
                           redirect_endpoint="sondes.admin_sondes")


# ------------------------------------------------------------------ recovery reports and chase mode (signed-in members; sonde_reports.py)

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@bp.route("/sondes/load-serial", methods=["POST"])
@login_required
def load_serial():
    """A signed-in member loads one flight by serial from SondeHub's public archive (Settings > Radiosondes). One request per click, limited per member per hour."""
    _need()
    from . import sonde_archive as sa
    d = get_db()
    if not sa.enabled():
        return action_response(False, "Loading flights from the archive is switched off.", redirect_endpoint="sondes.flight_list")
    n = d.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action='sondes.archive_load' AND actor_id=%s AND ts > now() - interval '1 hour'", (current_user.id,)).fetchone()["n"]
    if n >= cfg_int("sonde_archive_per_hour"):
        return action_response(False, "You have loaded a lot of flights this hour. Try again later.", redirect_endpoint="sondes.flight_list")
    ok, msg, serial = sa.load_serial(d, request.form.get("serial", ""))
    audit("sondes.archive_load", target=serial[:40], detail=("ok: " if ok else "no: ") + msg[:120])
    if ok and sd.get(serial):
        return action_response(True, msg, redirect_endpoint="sondes.flight_page", redirect_kwargs={"serial": serial})
    return action_response(ok, msg, redirect_endpoint="sondes.flight_list")


@bp.route("/sondes/<serial>/recovery", methods=["POST"])
@login_required
def report_recovery(serial):
    """A member's recovery report: sent to SondeHub in the tracker's own format (Settings > Radiosondes > Members Report Recoveries to SondeHub) and kept here."""
    _need()
    from . import sonde_reports as sr
    f = request.form
    yes = ("1", "true", "on", "yes")
    ok, msg = sr.report_recovery(current_user, serial, f.get("recovered") in yes, f.get("planned") in yes, f.get("by"), f.get("note"), _num(f.get("lat")), _num(f.get("lon")), _num(f.get("alt")))
    if ok:
        audit("sondes.recovery_report", target=serial, detail=sr.state_for(serial) or "")
    return action_response(ok, msg, redirect_endpoint="sondes.flight_page", redirect_kwargs={"serial": serial})


@bp.route("/api/sondes/chase.json")
def api_chase():
    """Chase cars on SondeHub's feed, for signed-in members only (chase mode on the Radiosonde Map)."""
    _need()
    if not current_user.is_authenticated:
        return jsonify({"error": "Sign in to use chase mode."}), 401
    from . import sonde_reports as sr
    resp = jsonify({"cars": sr.chase_cars(), "share": sr.mode("sonde_chase_mode"), "name": sr.chase_name(current_user)})
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.route("/my/sondes/chase/position", methods=["POST"])
@login_required
def chase_position():
    """The member's car position, while they have Share My Position switched on in chase mode: sent to SondeHub as CALLSIGN_chase."""
    _need()
    from . import sonde_reports as sr
    ok, msg = sr.share_position(current_user, _num(request.form.get("lat")), _num(request.form.get("lon")), _num(request.form.get("alt")))
    if ok and msg.startswith("Position shared"):
        audit("sondes.chase_share", target=sr.chase_name(current_user))
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


# ------------------------------------------------------------------ how to feed

@bp.route("/feed/sondes")
def feed_sondes():
    _need()
    from .public import _feed_context
    return render_template("feed_sondes.html", fsub="sondes", **_feed_context())


@bp.record_once
def _template_globals(state):
    state.app.jinja_env.globals.update(sonde_phase=sd.phase, sonde_phase_label=sd.phase_label)
