"""Home page, native live map, and the public data feeds behind them.

/api/aircraft.json is the hub's aircraft.json, trimmed to what the pages need and cached for a second so many
visitors do not each hit the hub. The hub's data is already public on its own map, so this adds no exposure.
"""
import json
import math
from datetime import datetime, timedelta, timezone
import re
import threading
import time
import urllib.request

from flask import Blueprint, abort, redirect, Response, current_app, jsonify, render_template, request
from flask_login import current_user

from . import airports as ap
from . import alerts as al
from . import flightlog, maplayers, rangestats, receivers, typedb
from .cache import cached
from .db import get_db
from . import feeders as fd
from .public import _map_points
from .settings import cfg, cfg_int, get_setting

bp = Blueprint("live", __name__)

_lock = threading.Lock()
_cache = {"at": 0.0, "data": None}
CACHE_SECONDS = 1.0  # protects the hub; not a user-facing setting
EMERGENCY = {"7500", "7600", "7700"}


# extra readsb fields kept for the selected-aircraft details and the optional list columns (short name -> readsb name)
EXTRA_FIELDS = {"ag": "alt_geom", "ias": "ias", "tas": "tas", "mach": "mach", "trk_rate": "track_rate", "roll": "roll",
                "mag": "mag_heading", "tru": "true_heading", "gr": "geom_rate", "qnh": "nav_qnh", "sel_alt": "nav_altitude_mcp",
                "sel_hdg": "nav_heading", "modes": "nav_modes", "nic": "nic", "rc": "rc", "nacp": "nac_p", "nacv": "nac_v",
                "sil": "sil", "ver": "version", "nicb": "nic_baro", "msgs": "messages", "seen": "seen", "wd": "wd", "ws": "ws",
                "oat": "oat", "tat": "tat", "src": "type", "desc": "desc", "op": "ownOp", "year": "year"}


def _type_fields(ac, kinds):
    """Long make and model (tl), type description for the icon (td) and wake class (wtc) from the hub's type table."""
    info = (kinds or {}).get(str(ac.get("t", "")).upper())
    if not info:
        return {}
    return {k: v for k, v in (("tl", info[0]), ("td", info[1]), ("wtc", info[2])) if v}


def _trim(ac, kinds=None):
    alt = ac.get("alt_baro")
    gnd = alt == "ground"
    return {
        "hex": str(ac.get("hex", "")).lower(),
        "flight": (ac.get("flight") or "").strip() or None,
        "r": ac.get("r"), "t": ac.get("t"),
        "alt": 0 if gnd else (alt if isinstance(alt, (int, float)) else None),
        "gnd": gnd, "gs": ac.get("gs"), "track": ac.get("track"),
        "lat": ac.get("lat"), "lon": ac.get("lon"), "cat": ac.get("category"),
        "squawk": ac.get("squawk"), "vr": ac.get("baro_rate", ac.get("geom_rate")),
        "mil": bool(int(ac.get("dbFlags", 0) or 0) & 1), "db": int(ac.get("dbFlags", 0) or 0),
        "emg": str(ac.get("squawk", "")) in EMERGENCY or str(ac.get("emergency", "none")) not in ("none", ""),
        "rssi": ac.get("rssi"), "seen_pos": ac.get("seen_pos"),
        **{short: ac[long] for short, long in EXTRA_FIELDS.items() if ac.get(long) is not None},
        **_type_fields(ac, kinds),
    }


def hub_aircraft():
    """(payload dict, error). Cached briefly; computes the hub's message rate from successive fetches."""
    hub = current_app.config.get("HUB_URL", "")
    if not hub:
        return None, "The hub address is not configured."
    with _lock:
        if _cache["data"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["data"], None
        try:
            with urllib.request.urlopen(hub.rstrip("/") + "/data/aircraft.json", timeout=5) as r:
                raw = json.loads(r.read(20_000_000))
        except Exception as e:
            return None, f"{type(e).__name__}"
        kinds = typedb.types(hub, cfg_int("lookup_cache_hours"))
        aircraft = [_trim(a, kinds) for a in raw.get("aircraft", []) if a.get("hex")]
        try:
            from . import lookups   # airline logos of the aircraft now in the air are fetched in the background, so the map never waits for one
            lookups.warm_logos(current_app._get_current_object(), [a["flight"] for a in aircraft if a.get("flight")])
        except Exception:
            pass
        # messages per second over the last minute, from readsb's own statistics (the same for every worker)
        rate = 0.0
        try:
            with urllib.request.urlopen(hub.rstrip("/") + "/data/stats.json", timeout=3) as r:
                one = json.loads(r.read(2_000_000)).get("last1min", {})
            span = (one.get("end") or 0) - (one.get("start") or 0)
            rate = (one.get("messages") or 0) / span if span > 0 else 0.0
        except Exception:
            pass
        now = raw.get("now")
        with_pos = [a for a in aircraft if a["lat"] is not None]
        high = [a for a in aircraft if isinstance(a["alt"], (int, float)) and not a["hex"].startswith("~")]
        high = max(high, key=lambda a: a["alt"]) if high else None
        data = {"now": now, "aircraft": aircraft,
                "totals": {"aircraft": len(aircraft), "with_position": len(with_pos), "messages_per_second": round(rate, 1),
                           "military": sum(1 for a in aircraft if a["mil"]),
                           "highest": ({"hex": high["hex"], "name": high["flight"] or high["r"] or high["hex"].upper(), "alt": high["alt"]} if high else None)}}
        _cache["at"], _cache["data"] = time.time(), data
        return data, None


def _feeder_uuids(param):
    """Station IDs from a comma separated parameter -> (uuids of known public stations, unknown ids)."""
    ids = [p.strip() for p in (param or "").split(",") if p.strip()][:20]
    if not ids:
        return [], []
    rows = get_db().execute("SELECT uuid, upper(station_id) AS sid FROM feeders WHERE upper(station_id) = ANY(%s) AND NOT banned AND owner_id IS NOT NULL", ([i.upper() for i in ids],)).fetchall()
    known = {r["sid"]: r["uuid"] for r in rows}
    return [known[i.upper()] for i in ids if i.upper() in known], [i for i in ids if i.upper() not in known]


@bp.route("/api/mlat/aircraft/<hexid>.json")
def mlat_aircraft(hexid):
    """Counts only (no stations, no coordinates): how the MLAT server sees one aircraft."""
    import re
    from . import mlat
    if not re.fullmatch(r"[0-9a-fA-F~]{6,7}", hexid):
        return jsonify({}), 400
    return jsonify(mlat.aircraft_counts(hexid) or {})


@bp.route("/api/aircraft.json")
def api_aircraft():
    data, err = hub_aircraft()
    if err:
        return jsonify({"error": err}), 503
    data = dict(data, totals=dict(data["totals"], stations_online=fd.network_totals()["online"], stations_online_ar=fd.online_in_state()))
    if request.args.get("feeder"):
        uuids, unknown = _feeder_uuids(request.args["feeder"])
        heard = receivers.aircraft_heard_by(uuids)
        data["aircraft"] = [a for a in data["aircraft"] if a["hex"] in heard]
        data["filter"] = {"available": receivers.available(), "unknown": unknown, "matched": len(data["aircraft"])}
    if current_user.is_authenticated:
        data = _with_watch(data)
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _with_watch(data):
    """Signed-in visitors get the aircraft that match their own alert rules flagged (watch: [rule ids]) and the names of those rules."""
    rules = get_db().execute("SELECT id, name, kind, value, area_lat, area_lon, area_radius_nm, alt_min_ft, alt_max_ft FROM alert_rules WHERE user_id=%s AND enabled",
                             (current_user.id,)).fetchall()
    if not rules:
        return data
    rules = [dict(r) for r in rules]
    out = []
    for a in data["aircraft"]:
        raw = {"hex": a["hex"], "r": a["r"], "flight": a["flight"] or "", "squawk": a["squawk"], "t": a["t"], "dbFlags": 1 if a["mil"] else 0,
               "emergency": "general" if a["emg"] else "none", "lat": a["lat"], "lon": a["lon"], "alt_baro": "ground" if a["gnd"] else a["alt"]}
        w = [r["id"] for r in rules if al.matches(r, raw)]
        out.append(dict(a, watch=w) if w else a)
    return dict(data, aircraft=out, watch_rules={str(r["id"]): r["name"] for r in rules})


@bp.route("/api/coverage.json")
def api_coverage():
    """What each public station can hear: its farthest distance in every direction, drawn around its public (privacy-rounded) location."""
    days = cfg_int("coverage_days")

    def load():
        rows = [r for r in fd.public_feeders(cfg("default_rank_period"), cfg("default_rank_sort")) if fd.public_location(r)]
        secs = rangestats.sectors_for([r["uuid"] for r in rows], days)
        out = []
        for r in rows:
            s = secs.get(r["uuid"]) or []
            if max(s or [0]) <= 0:
                continue
            lat, lon = fd.public_location(r)
            out.append({"id": r["station_id"], "name": r["name"] or r["station_id"], "lat": lat, "lon": lon, "online": bool(r["online"]), "sectors": [round(v, 1) for v in s]})
        return out
    resp = jsonify({"days": days, "stations": cached(("coverage", days), load)})
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


@bp.route("/api/metars.json")
def api_metars():
    """Current airport weather (METAR) inside a map view, from the Aviation Weather Center; kept for a few minutes per area."""
    try:
        s, w, n, e = (float(request.args[k]) for k in ("south", "west", "north", "east"))
    except (KeyError, ValueError):
        return jsonify({"error": "south, west, north, east are required numbers"}), 400
    if cfg("airport_weather_default") not in ("0", "1") or not cfg("metar_api_url").strip():
        return jsonify({"metars": {}})
    s, n, w, e = (math.floor(min(s, n)), math.ceil(max(s, n)), math.floor(min(w, e)), math.ceil(max(w, e)))   # whole degrees, so views share answers
    if (n - s) * (e - w) > 400:
        return jsonify({"metars": {}, "note": "zoom in for airport weather"})
    data = metars_for(s, w, n, e)
    resp = jsonify({"metars": data or {}})
    resp.headers["Cache-Control"] = "public, max-age=120"
    return resp


def metars_for(s, w, n, e):
    """Airport weather (METAR) for a box of whole degrees, kept for Settings > METAR cache minutes; {} when the service is off or does not answer."""
    if cfg("airport_weather_default") not in ("0", "1") or not cfg("metar_api_url").strip():
        return {}
    key = ("metars", s, w, n, e)

    def load():
        url = f"{cfg('metar_api_url').strip()}?bbox={s},{w},{n},{e}&format=json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent")})
            with urllib.request.urlopen(req, timeout=cfg_int("lookup_timeout_seconds") + 4) as r:
                rows = json.loads(r.read(3_000_000))
        except Exception:
            return None
        out = {}
        for m in rows if isinstance(rows, list) else []:
            if not m.get("icaoId"):
                continue
            out[m["icaoId"]] = {"cat": m.get("fltCat"), "raw": m.get("rawOb"), "temp": m.get("temp"), "dewp": m.get("dewp"), "wdir": m.get("wdir"),
                                "wspd": m.get("wspd"), "wgst": m.get("wgst"), "vis": m.get("visib"), "altim": m.get("altim"), "obs": m.get("obsTime"),
                                "clouds": [{"cover": c.get("cover"), "base": c.get("base")} for c in (m.get("clouds") or [])][:4]}
        return out
    return cached(key, load, cfg_int("metar_cache_minutes") * 60)


_markers = {"at": 0.0, "body": None}


@bp.route("/api/markers.js")
def api_markers():
    """The classic map's aircraft icon shapes, fetched from the hub's own tar1090 so the live map draws the same icons.
    The file is served by the hub (it is not copied into this project) and kept for an hour."""
    hub = current_app.config.get("HUB_URL", "")
    if not hub:
        return Response("// the hub address is not configured\n", status=503, mimetype="application/javascript")
    with _lock:
        if _markers["body"] is None or time.time() - _markers["at"] > 3600:
            try:
                with urllib.request.urlopen(hub.rstrip("/") + "/", timeout=5) as r:
                    index = r.read(2_000_000).decode("utf-8", "replace")
                name = re.search(r'src="(markers[_0-9a-f]*\.js)"', index)
                if not name:
                    raise ValueError("markers script not found")
                with urllib.request.urlopen(hub.rstrip("/") + "/" + name.group(1), timeout=8) as r:
                    _markers["body"] = r.read(3_000_000).decode("utf-8", "replace")
                _markers["at"] = time.time()
            except Exception:
                return Response("// the hub icon set is not available\n", status=502, mimetype="application/javascript")
    resp = Response(_markers["body"], mimetype="application/javascript")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.route("/api/airports.json")
def api_airports():
    """Airports inside a map view. Which types appear depends on zoom (settings), so the map stays readable."""
    try:
        s, w, n, e = (float(request.args[k]) for k in ("south", "west", "north", "east"))
        z = float(request.args.get("z", 7))
    except (KeyError, ValueError):
        return jsonify({"error": "south, west, north, east and z are required numbers"}), 400
    kinds = []
    if z >= cfg_int("airport_zoom_large"):
        kinds += ["large_airport", "medium_airport"]
    if z >= cfg_int("airport_zoom_small"):
        kinds += ["small_airport", "seaplane_base"]
    if z >= cfg_int("airport_zoom_heliport"):
        kinds += ["heliport"]
    if not kinds:
        return jsonify({"airports": []})
    from .db import get_db
    limit = cfg_int("airport_map_limit")
    # the view is rounded outward to 0.05 degree so nearby map moves share one answer; a new airport import changes the key
    s, n, w, e = (math.floor(min(s, n) * 20) / 20, math.ceil(max(s, n) * 20) / 20, math.floor(min(w, e) * 20) / 20, math.ceil(max(w, e) * 20) / 20)
    key = ("airports", tuple(kinds), s, n, w, e, limit, get_setting("airports_last_import"))

    def load():
        return [dict(r) for r in get_db().execute(
            "SELECT ident, name, type, lat, lon, elevation_ft, municipality, region FROM airports "
            "WHERE type = ANY(%s) AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
            "ORDER BY CASE type WHEN 'large_airport' THEN 0 WHEN 'medium_airport' THEN 1 ELSE 2 END LIMIT %s",
            (kinds, s, n, w, e, limit)).fetchall()]
    resp = jsonify({"airports": cached(key, load)})
    resp.headers["Cache-Control"] = f"public, max-age={cfg_int('public_cache_seconds')}"
    return resp


@bp.route("/api/airports/<ident>.json")
def api_airport(ident):
    a, freqs, runs = ap.get_airport(ident)
    if a is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ident": a["ident"], "name": a["name"], "type": a["type"], "lat": a["lat"], "lon": a["lon"], "elevation_ft": a["elevation_ft"],
                    "municipality": a["municipality"], "region": a["region"],
                    "frequencies": [{"type": f["type"], "description": f["description"], "mhz": f["mhz"]} for f in freqs[:12]],
                    "runways": [{"name": f"{r['le_ident'] or ''}/{r['he_ident'] or ''}", "length_ft": r["length_ft"], "surface": r["surface"]} for r in runs[:6]]})


def _map_settings():
    from .settings import map_area
    return {"area": map_area("live_map_area"), "lat": float(cfg("map_lat")), "lon": float(cfg("map_lon")), "zoom": float(cfg("map_zoom_page")),
            "refresh": cfg_int("live_refresh_seconds"), "mobileRefresh": cfg_int("live_mobile_refresh_seconds"), "mobileMax": cfg_int("live_mobile_max_aircraft"), "trail": cfg_int("live_trail_points"), "iconScale": float(cfg("live_icon_scale")), "trailLabelSeconds": cfg_int("trail_label_seconds"), "trailStale": cfg_int("trail_stale_seconds"), "trailBreak": cfg_int("trail_gap_break_seconds"), "trailLabels": cfg("trail_labels_default") == "1", "lastLeg": cfg("trail_last_leg_default") == "1", "listRows": cfg_int("live_list_rows"), "iconDivide": float(cfg("icon_zoom_divide")), "iconBoost": cfg_int("icon_zoom_boost_percent") / 100, "columns": cfg("live_columns").split(","), "labels": cfg("live_labels"), "units": cfg("live_units"), "labelMinZoom": float(cfg("label_min_zoom")), "logos": bool(cfg("airline_logo_url").strip()) and cfg("lookups_enabled") == "1", "routeDefault": cfg("live_route_default") == "1", "labelScale": cfg("live_label_scale"), "wxRefresh": cfg_int("weather_refresh_minutes"), "labelBg": cfg("live_label_bg"), "signedIn": bool(current_user.is_authenticated), "wxAirports": cfg("airport_weather_default") == "1", "wxStations": cfg("wx_enabled") == "1", "wxHideOffline": cfg("wx_live_hide_offline") == "1", "sondes": cfg("sonde_enabled") == "1", "acars": cfg("acars_enabled") == "1", "sondeLive": cfg_int("sonde_live_seconds"), "sondeHours": cfg_int("sonde_map_hours"), "wxMetric": cfg("wx_map_metric"), "wxLive": cfg_int("wx_live_seconds"), "wxArrows": cfg("wx_map_arrows"), "coverageDays": cfg_int("coverage_days"),
            "tile": cfg("map_tile_url"), "attr": cfg("map_attribution"),
            "ring": cfg_int("radar_ring_nm"), "range": cfg_int("radar_range_nm")}


@bp.route("/")
def home():
    """The ArkSignal overview: what is happening right now and a card for every service."""
    from . import mlat, releasenotes, status
    rows = fd.public_feeders(cfg("default_rank_period"), cfg("default_rank_sort"))
    totals = fd.network_totals()
    mlat_on = cfg("mlat_enabled") == "1"
    try:
        release = next((e for e in releasenotes.entries() if e["version"] != "next"), None)
    except Exception:
        release = None
    from . import arkansas, homemap
    wx_card = None
    if cfg("wx_enabled") == "1":
        from . import weather
        pts = [p for p in weather.map_points() if p["online"] and arkansas.inside(p["lat"], p["lon"])]
        temps = [p["temp_f"] for p in pts if p.get("temp_f") is not None]
        from . import units as _u
        avg = weather.show("temp_f", sum(temps) / len(temps), "metric" if _u.system("weather") == "metric" else "imperial") if temps else None   # the weather card in the weather units, the rest of the home page in its own
        wx_card = {"online": len(pts), "total": len([p for p in weather.map_points() if arkansas.inside(p["lat"], p["lon"])]), "avg": avg}
    sonde_card = None
    if cfg("sonde_enabled") == "1" and cfg("sonde_home_card") == "1":
        from . import sondes
        try:
            sonde_card = sondes.home_card()
        except Exception:
            get_db().rollback()
    from . import services
    from .roadmap import coming_services
    return render_template("overview.html", coming=coming_services(), svc_now=services.collect("home_now"), wx=wx_card, sonde=sonde_card, layers=homemap.layers(), stations=fd.online_in_state(), ar=arkansas.js_config(), named=totals["claimed"], tagline=cfg("site_tagline"),
                           refresh=cfg_int("home_refresh_seconds"), m=_map_settings(), mlat_on=mlat_on, mlat_working=len(mlat.working_uuids()) if mlat_on else 0,
                           status=status.run(False, sources=False), release=release, home_area=[float(x) for x in cfg("home_map_area").split(",")])


@bp.route("/home/now")
def home_now():
    """The service blocks of the card beside the home map (weather, radiosondes, ...), for the page to refresh without a reload."""
    from . import services
    return render_template("partials/_home_now.html", svc_now=services.collect("home_now"))


@bp.route("/aircraft")
def aircraft_page():
    """Aircraft Tracking: the radar, the live numbers and the aircraft tools (this used to be the home page)."""
    rows = fd.public_feeders(cfg("default_rank_period"), cfg("default_rank_sort"))
    return render_template("aircraft.html", points=_map_points(rows), totals=fd.network_totals(),
                           tagline="Live aircraft across Arkansas", refresh=cfg_int("home_refresh_seconds"), m=_map_settings())


@bp.route("/live")
def live_map():
    focus = [x.strip() for x in request.args.get("feeder", "").split(",") if _ID_RE.match(x.strip())][:20]
    return _render_live(focus)


_ID_RE = re.compile(r"^[A-Za-z0-9-]{2,20}$")


def _render_live(focus, station=None):
    rows = fd.public_feeders(cfg("default_rank_period"), cfg("default_rank_sort"))
    cfg_js = _map_settings()
    cfg_js.update({"airport_zoom_large": cfg_int("airport_zoom_large"), "airport_zoom_small": cfg_int("airport_zoom_small"),
                   "airport_zoom_heliport": cfg_int("airport_zoom_heliport"), "radius_m": int(0.5 * 10 ** -cfg_int("general_precision_decimals") * 111320),
                   "feederFilter": cfg("hub_beast_enabled") == "1", "focus": focus})
    if station:
        loc = fd.public_location(station)
        if loc:
            cfg_js.update({"lat": loc[0], "lon": loc[1], "zoom": cfg_js["zoom"] + 1})
    from .setting_specs import ICON_CHOICES, LABEL_CHOICES, LIVE_COLUMNS, UNIT_CHOICES
    from .public import HEAT_DURATIONS, HEAT_ENDS
    cfg_js.update({"replaySpeed": cfg_int("replay_default_speed"), "persistMinutes": cfg_int("live_persist_minutes"), "acars": cfg("acars_enabled") == "1", "replayStart": cfg_int("replay_start_minutes"), "heatDuration": cfg("heat_default_duration"), "heatMaxDays": cfg_int("heat_max_days")})
    return render_template("live.html", heat_durations=HEAT_DURATIONS, heat_ends=HEAT_ENDS, points=_map_points(rows), m=cfg_js, label_choices=LABEL_CHOICES, layers=maplayers.for_map(),
                           column_names=dict(LIVE_COLUMNS), icon_choices=ICON_CHOICES, unit_choices=UNIT_CHOICES, station=station,
                           stations=[{"id": r["station_id"], "name": r["name"] or "", "online": bool(r["online"])} for r in rows])


@bp.route("/<station_id>")
def station_map(station_id):
    """adsb.example.org/kd5vcf: the live map showing only the aircraft that station is hearing. Every fixed page of the site
    wins over this route, so a station ID can never hide a page."""
    if not _ID_RE.match(station_id):
        abort(404)
    f = fd.public_feeder(station_id)
    if f is None:
        abort(404)
    return _render_live([f["station_id"]], station=f)


def _hub_json(path, limit=8_000_000):
    hub = current_app.config.get("HUB_URL", "")
    if not hub:
        return None
    try:
        raw = typedb._get(hub.rstrip("/") + path, limit)
        return json.loads(raw)
    except Exception:
        return None


def merge_trace(files, gap_seconds=0):
    """Hub trace files -> (meta, points, leg start indexes). readsb marks a new leg (flag bit 2) when an aircraft lands or goes quiet for a while, but an
    aircraft that lands out of our coverage and flies again hours later never says so, so a silence longer than `gap_seconds` (the same gap that
    starts a new flight in the flight log; 0 turns it off) also starts a new leg: otherwise two flights draw as one path with a straight line between."""
    merged, meta = {}, {}
    for d in files:
        if not d or not d.get("trace"):
            continue
        meta = meta or {"reg": d.get("r"), "type": d.get("t")}
        base = d.get("timestamp", 0)
        for p in d["trace"]:
            if p[1] is None or p[2] is None:
                continue
            merged[round(base + p[0], 1)] = p
    pts, starts = [], []
    for t in sorted(merged):
        p = merged[t]
        if pts and t - pts[-1][0] < 0.5:
            continue
        if (p[6] is not None and int(p[6]) & 2) or (gap_seconds and pts and t - pts[-1][0] > gap_seconds):   # flag bit 2: a new flight leg starts at this point
            if not starts or starts[-1] != len(pts):
                starts.append(len(pts))
        pts.append([t, p[1], p[2], 0 if p[3] == "ground" else p[3], p[4], p[5], p[7]])
    return meta, pts, starts


@bp.route("/api/trace/<hex_>.json")
def api_trace(hex_):
    """An aircraft's recent track from the hub's own trace files (the ones tar1090 reads), trimmed to the current leg.
    Points: [time (epoch seconds), lat, lon, altitude ft, speed kt, track, vertical rate]; `leg` is where the current flight starts."""
    hex_ = hex_.lower()
    if not re.fullmatch(r"[0-9a-f]{6}", hex_):
        return jsonify({"error": "bad address"}), 400

    try:
        at = float(request.args["at"]) if request.args.get("at") else None
    except ValueError:
        at = None

    def merge(files):
        return merge_trace(files, cfg_int("flightlog_gap_minutes") * 60)

    def load():
        # like tar1090: the full trace is written to disk only now and then, the recent trace is kept up to date, so use both
        # (older points from the full file, newer from the recent one) or the track stops short of the aircraft
        meta, pts, starts = merge([_hub_json(f"/data/traces/{hex_[-2:]}/trace_{kind}_{hex_}.json") for kind in ("full", "recent")])
        if at is not None and not (pts and pts[0][0] - 60 <= at <= pts[-1][0] + 60):
            # an earlier day: the hub keeps each finished UTC day under globe_history (what tar1090's replay reads)
            day = datetime.fromtimestamp(at, timezone.utc)
            meta2, pts2, starts2 = merge([_hub_json(f"/globe_history/{day:%Y/%m/%d}/traces/{hex_[-2:]}/trace_full_{hex_}.json")])
            if pts2:
                meta, pts, starts = meta2, pts2, starts2
        # the leg to show: the one containing `at`, else the latest
        bounds = starts or [0]
        if bounds[0] != 0:
            bounds = [0] + bounds
        pick = len(bounds) - 1
        if at is not None:
            pick = max([i for i, b in enumerate(bounds) if b < len(pts) and pts[b][0] <= at] or [0])
        out = dict(meta, points=pts, leg=bounds[pick] if starts or at is not None else 0)
        if at is not None:
            out["end"] = bounds[pick + 1] if pick + 1 < len(bounds) else len(pts)
        return out
    resp = jsonify(cached(("trace", hex_, at), load, 10))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/api/history/flights.json")
def api_history():
    """Earlier flights from the portal's own log: ?q= callsign, registration, type or hex (partial is fine), ?from= and ?to= (YYYY-MM-DD, UTC)."""
    q = request.args.get("q", "")[:20]

    def day(name, add=0):
        v = request.args.get(name, "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            return None
        try:
            return datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=add)
        except ValueError:
            return None
    d_from, d_to = day("from"), day("to", 1)
    try:
        limit, offset = min(100, max(1, int(request.args.get("limit", 40)))), max(0, int(request.args.get("offset", 0)))
    except ValueError:
        limit, offset = 40, 0

    def load():
        rows = flightlog.search(get_db(), q, d_from, d_to, limit, offset)
        first = get_db().execute("SELECT MIN(first_seen) AS t FROM flight_log").fetchone()["t"]
        out = []
        for r in rows:
            out.append({"id": r["id"], "hex": r["hex"], "callsign": r["callsign"], "reg": r["reg"], "type": r["type"], "mil": r["mil"],
                        "first": r["first_seen"].timestamp(), "last": r["last_seen"].timestamp(), "max_alt": r["max_alt"], "max_gs": r["max_gs"],
                        "start": [r["start_lat"], r["start_lon"]] if r["start_lat"] is not None else None,
                        "end": [r["end_lat"], r["end_lon"]] if r["end_lat"] is not None else None})
        return {"flights": out, "since": first.timestamp() if first else None, "enabled": cfg("flightlog_enabled") == "1", "limit": limit}
    resp = jsonify(cached(("history", q, d_from, d_to, limit, offset), load, 20))
    resp.headers["Cache-Control"] = "no-store"
    return resp


_HIST_DURATIONS = [("1", "1 hour"), ("6", "6 hours"), ("24", "24 hours"), ("72", "3 days"), ("168", "7 days"), ("336", "14 days"), ("720", "30 days"), ("2160", "90 days"), ("8760", "1 year")]
_HIST_ENDS = [("0", "Now"), ("24", "1 day ago"), ("72", "3 days ago"), ("168", "7 days ago"), ("720", "30 days ago"), ("2160", "90 days ago")]


@bp.route("/history/")
def flight_history():
    """Earlier flights from the portal's own log, for a period chosen like the heatmap's: the last N hours ending some time ago, or exact dates and times."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(cfg("timezone"))
    now = datetime.now(timezone.utc)
    q = request.args.get("q", "").strip()[:20]
    duration, end = request.args.get("duration", "24"), request.args.get("end", "0")
    if duration not in dict(_HIST_DURATIONS):
        duration = "24"
    if end not in dict(_HIST_ENDS):
        end = "0"
    start = now - timedelta(hours=float(duration) + float(end))
    stop = now - timedelta(hours=float(end))
    custom, error = False, ""
    f_from, f_to = request.args.get("from", ""), request.args.get("to", "")
    if f_from and f_to:
        try:
            s_dt = datetime.fromisoformat(f_from).replace(tzinfo=tz)
            e_dt = datetime.fromisoformat(f_to).replace(tzinfo=tz)
            if e_dt <= s_dt:
                error = "The end must be after the start."
            else:
                start, stop, custom = s_dt.astimezone(timezone.utc), e_dt.astimezone(timezone.utc), True
        except ValueError:
            error = "Enter the start and end as dates and times."
    page = max(1, request.args.get("page", 1, type=int) or 1)
    size = cfg_int("page_size")
    d = get_db()
    total = flightlog.count(d, q, start, stop)
    sort = request.args.get("sort", "last") if request.args.get("sort") in flightlog.SORTS else "last"
    direction = "asc" if request.args.get("dir") == "asc" else "desc"
    rows = flightlog.search(d, q, start, stop, size, (page - 1) * size, sort, direction)
    first = d.execute("SELECT MIN(first_seen) AS t FROM flight_log").fetchone()["t"]
    pager = {"page": page, "pages": max(1, -(-total // size)), "total": total, "args": {k: v for k, v in request.args.items() if k != "page"}}
    return render_template("history.html", rows=rows, pager=pager, sort=sort, direction=direction, q=q, durations=_HIST_DURATIONS, ends=_HIST_ENDS, duration=duration, end=end, custom=custom, error=error,
                           shown_from=start.astimezone(tz).strftime("%Y-%m-%dT%H:%M"), shown_to=stop.astimezone(tz).strftime("%Y-%m-%dT%H:%M"), tz=cfg("timezone"),
                           since=first, enabled=cfg("flightlog_enabled") == "1", days=cfg_int("flightlog_days"))


@bp.route("/flight/")
def flight_search():
    return render_template("flight_search.html", q=request.args.get("q", "")[:20])


@bp.route("/cockpit/<hex_>")
def cockpit_moved(hex_):
    return redirect("/skyview/" + hex_, code=301)


@bp.route("/skyview/<hex_>")
def skyview(hex_):
    """Sky View: a live 3D view from an aircraft over real terrain, with the other aircraft in the sky around it."""
    hex_ = hex_.lower()
    if not re.fullmatch(r"[0-9a-f]{6}", hex_):
        abort(404)
    c = {"lib": cfg("maplibre_base_url"), "dem": cfg("skyview_dem_url"), "imagery": cfg("skyview_imagery_url"), "street": cfg("map_tile_url"),
         "exag": float(cfg("skyview_exaggeration")), "refresh": cfg_int("live_refresh_seconds")}
    return render_template("skyview.html", hex=hex_, c=c, m=_map_settings())


@bp.route("/flight/<hex_>")
def flight(hex_):
    """Flight progress: route, distance flown and to go, elapsed and remaining time, track and profile charts."""
    hex_ = hex_.lower()
    if not re.fullmatch(r"[0-9a-f]{6}", hex_):
        abort(404)
    return render_template("flight.html", hex=hex_, m=_map_settings())
