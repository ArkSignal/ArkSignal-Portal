"""Weather stations: the public pages, the addresses stations send readings to, the owner's pages and the admin page. The logic is in weather.py."""
import math
import json
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for, flash
from flask_login import current_user, login_required

from . import weather as wx
from . import stations as st
from . import csrf
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .cache import cached
from .clientip import client_ip
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("wx", __name__)
SOURCE_CREDIT = {"ecowitt_cloud": ("Data from Ecowitt", "https://www.ecowitt.net/"), "tempest_cloud": ("Tempest by WeatherFlow", "https://tempestwx.com/")}


def _units():
    return wx.units_for(request.args.get("units") if request.args.get("units") in ("imperial", "metric") else None)


def _need_service():
    if not wx.enabled():
        abort(404)


def _who(r):
    return r["owner_display"] or r["owner_name"] or ""


# ------------------------------------------------------------------ readings arrive here

def _ingest(token, read):
    """Common to every upload address: known token, the service on, a sane rate, a small body, a reading that makes sense. Unknown and switched-off stations look the same."""
    if not wx.enabled():
        abort(404)
    row = wx.by_token(token)
    if row is None or row["disabled"] or row["kind"] not in wx.PUSH_KINDS:
        if wx.too_many_bad_tokens(client_ip(), note=True):   # a run of unknown addresses from one place: stop answering for a while (guessing)
            return "slow down", 429
        abort(404)
    if (request.content_length or 0) > 16384:
        return "too large", 413
    gap = cfg_int("wx_push_min_seconds")
    if gap and row["last_report"] and (datetime.now(timezone.utc) - row["last_report"]).total_seconds() < gap:
        return "slow down", 429
    reading = read()
    if reading is None:
        return "no usable readings", 400
    wx.store(get_db(), row["id"], reading)
    return "success", 200


@bp.route("/wx/push/<token>", methods=["POST", "GET"])
@csrf.exempt
def push(token):
    """An Ecowitt gateway or console set to a custom server (Ecowitt protocol): it posts a form."""
    return _ingest(token, lambda: wx.parse_pws(request.values))


@bp.route("/wx/wu/<token>", methods=["POST", "GET"])
@csrf.exempt
def push_wu(token):
    """The Weather Underground upload format (Ambient, Davis, and many others): the station sends a query. Its own ID and password fields are ignored."""
    return _ingest(token, lambda: wx.parse_pws(request.values))


@bp.route("/wx/relay/<token>", methods=["POST"])
@csrf.exempt
def relay(token):
    """Home Assistant or a script posting a JSON object."""
    return _ingest(token, lambda: wx.parse_relay(request.get_json(silent=True)))


# ------------------------------------------------------------------ public pages

def _summary(stations, units):
    rows = []
    for s in stations:
        la = s["latest"]
        rows.append({"sid": s["sid"], "name": s["name"], "online": s["online"], "approx": s["approx"], "owner": _who(s["row"]), "latest": la,
                     "temp": wx.show("temp_f", la.get("temp_f"), units), "hum": wx.show("hum", la.get("hum"), units), "press": wx.show("press_in", la.get("press_in"), units),
                     "wind": wx.show("wind_mph", la.get("wind_mph"), units), "dir": wx.compass(la.get("wind_dir")), "gust": wx.show("gust_mph", la.get("gust_mph"), units),
                     "rain": wx.show("rain_day_in", la.get("rain_day_in"), units), "last": s["row"]["last_report"], "kind": s["row"]["kind"], "loc": s["loc"]})
    return rows


@bp.route("/weather/")
def weather_page():
    _need_service()
    units = _units()
    stations = wx.public_stations()
    rows = _summary(stations, units)
    live = [r for r in rows if r["online"]]

    def best(key, big=True):
        c = [r for r in live if r["latest"].get(key) is not None]
        if not c:
            return None
        r = (max if big else min)(c, key=lambda x: x["latest"][key])
        return r
    extremes = [("Hottest", best("temp_f"), "temp_f"), ("Coldest", best("temp_f", False), "temp_f"), ("Windiest", best("gust_mph"), "gust_mph"), ("Wettest Today", best("rain_day_in"), "rain_day_in")]
    extremes = [(label, r, wx.show(k, r["latest"][k], units)) for label, r, k in extremes if r and (k != "rain_day_in" or r["latest"][k] > 0)]
    return render_template("weather.html", space_on=cfg("spaceweather_enabled") == "1", sonde_on=cfg("sonde_enabled") == "1", wx_live=cfg_int("wx_live_seconds"), rows=rows, live=len(live), extremes=extremes, units=units, wx_metric=cfg("wx_map_metric"), wx_arrows=cfg("wx_map_arrows") == "1",
                           map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"))


@bp.route("/weather/place")
def place_page():
    """Weather for any place a visitor names (ZIP code, city, airport, address) or a point (?lat=&lon=): current conditions worked out from the stations near it, a forecast, history charts and the stations used."""
    _need_service()
    from . import wxplace
    units = _units()
    q = " ".join(request.args.get("q", "").split())[:120]
    place, choices = None, []
    try:
        la, lo = float(request.args.get("lat", "")), float(request.args.get("lon", ""))
        if -90 <= la <= 90 and -180 <= lo <= 180:
            place = {"label": q or f"{la:.3f}, {lo:.3f}", "lat": la, "lon": lo, "kind": "point"}
    except ValueError:
        pass
    if place is None and q:
        choices = wxplace.resolve(q)
        if choices:
            place = choices[0]
    if place is None and not q and current_user.is_authenticated:   # a signed-in member with a feeder of their own opens on it
        row = get_db().execute("SELECT lat, lon, station_id FROM feeders WHERE owner_id=%s AND lat IS NOT NULL AND lon IS NOT NULL AND NOT banned ORDER BY online DESC, last_seen DESC NULLS LAST LIMIT 1", (current_user.id,)).fetchone()
        if row:
            place = {"label": "Your station " + (row["station_id"] or ""), "lat": row["lat"], "lon": row["lon"], "kind": "point"}
    ctx = {"q": q, "place": place, "choices": choices[1:5], "units": units}
    if place:
        radius = cfg_int("wx_place_radius_mi")
        near = wxplace.nearby(place["lat"], place["lon"], radius)
        comb = wxplace.combine(near)
        widened = 0
        if comb["n"] < 2:   # too few reporting stations close by: look further, and say so
            radius2 = min(radius * 2.5, 40)
            near2 = wxplace.nearby(place["lat"], place["lon"], radius2)
            c2 = wxplace.combine(near2)
            if c2["n"] > comb["n"]:
                near, comb, widened, radius = near2, c2, round(radius), round(radius2)
        la_ = wx.Reading(comb["readings"])
        gfs = None
        if cfg("wx_model_forecast") == "1":
            from . import sonde_predict as _sp
            gfs = _sp.gfs_forecast(place["lat"], place["lon"]) if _sp.enabled() else None
        fc = wxplace.forecast(place["lat"], place["lon"])
        ctx.update(radius=radius, widened=widened, near=near[:40], n_live=comb["n"], la=la_, cond=comb["cond"] or wx.condition(la_, place["lat"], place["lon"]) or (wxplace.icon_from_text(fc["periods"][0]["short"], fc["periods"][0]["daytime"]) if fc and fc.get("periods") else None), show=lambda k: wx.show(k, la_.get(k), units),
                   compass=wx.compass(la_.get("wind_dir")), trend=wxplace.trend(place["lat"], place["lon"], radius), forecast=fc, conv=wx.show, gfs=gfs, compass_of=wx.compass)
    return render_template("weather_place.html", **ctx)


@bp.route("/weather/<sid>")
def station_page(sid):
    _need_service()
    row = wx.get_public(sid)
    if row is None:
        abort(404)
    units = _units()
    period = request.args.get("period", "24h")
    if period not in wx.PERIODS:
        period = "24h"
    la = wx.latest_of(row)
    show = lambda k: wx.show(k, la.get(k), units)
    pts = wx.series(row["id"], period)
    imp = units != "metric"
    cvt = {"temp": (lambda v: v) if imp else (lambda v: (v - 32) * 5 / 9), "press": (lambda v: v) if imp else (lambda v: v / 0.0295300), "speed": (lambda v: v) if imp else (lambda v: v * 1.609344),
           "rain": (lambda v: v) if imp else (lambda v: v * 25.4)}
    charts = [
        ("Temperature", wx.chart_svg(pts, [("temp", "var(--acc2)", "Temperature"), ("dew", "var(--acc)", "Dew point")], period, cvt["temp"], unit=wx.show("temp_f", 0, units)[1]), "Temperature and dew point (" + wx.show("temp_f", 0, units)[1] + ")", [("var(--acc2)", "Temperature"), ("var(--acc)", "Dew point")]),
        ("Humidity", wx.chart_svg(pts, [("hum", "#6fb3ff", "Humidity")], period, lambda v: v, unit="%", zero=True), "Relative humidity (%)", []),
        ("Pressure", wx.chart_svg(pts, [("press", "#c79bff", "Pressure")], period, cvt["press"], unit=wx.show("press_in", 0, units)[1]), "Pressure (" + wx.show("press_in", 0, units)[1] + ")", []),
        ("Wind", wx.chart_svg(pts, [("gust", "#ff9f43", "Gust"), ("wind", "var(--acc)", "Wind")], period, cvt["speed"], unit=wx.show("wind_mph", 0, units)[1], zero=True), "Wind and gust (" + wx.show("wind_mph", 0, units)[1] + ")", [("var(--acc)", "Wind"), ("#ff9f43", "Gust")]),
        ("Rain", wx.chart_svg(pts, [("rain", "#4fb3ff", "Rain today")], period, cvt["rain"], unit=wx.show("rain_day_in", 0, units)[1], zero=True, bars=period != "24h"), "Rain so far that day (" + wx.show("rain_day_in", 0, units)[1] + ")", []),
        ("Sun", wx.chart_svg(pts, [("solar", "#ffd24f", "Solar radiation")], period, lambda v: v, unit="W/m²", zero=True), "Solar radiation (W/m²) and UV index", []),
    ]
    if not any(la.get(k) is not None for k in ("solar", "uv")):
        charts = [c for c in charts if c[0] != "Sun"]
    if not any(la.get(k) is not None for k in ("rain_day_in", "rain_rate_in")):
        charts = [c for c in charts if c[0] != "Rain"]
    live_seconds = cfg_int("wx_live_seconds")
    days = wx.daily(row["id"], 30)
    rec = wx.records(row["id"])
    loc = wx.public_loc(row)
    extra = la.get("extra") or {}
    return render_template("weather_station.html", cond=wx.condition(la, row["plat"], row["plon"]), r=row, la=la, show=show, units=units, period=period, periods=wx.PERIODS, charts=charts, days=days, rec=rec, loc=loc,
                           approx=row["pprivacy"] != "exact", online=wx.is_online(row), trend=wx.pressure_trend(row["id"]), compass=wx.compass(la.get("wind_dir")), who=_who(row),
                           credit=SOURCE_CREDIT.get(row["kind"]), extra=extra, live_seconds=live_seconds, kinds=wx.KINDS, conv=wx.show, stale=cfg_int("wx_stale_minutes"), name=row["name"] or row["fname"] or row["sid"],
                           map_zoom=cfg("map_zoom"))


@bp.route("/weather/map")
def weather_map():
    """The full weather map: the stations on a full-window map with the layers, filters and a station list, like the aircraft map."""
    _need_service()
    from . import maplayers
    return render_template("weather_map.html", layers=maplayers.for_map(), wx_units=wx.units_for(request.args.get("units") if request.args.get("units") in ("imperial", "metric") else None),
                           wx_metric=cfg("wx_map_metric"), wx_arrows=cfg("wx_map_arrows") == "1", label_scale=cfg("live_label_scale"), label_bg=cfg("live_label_bg"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"), wx_refresh=cfg_int("weather_refresh_minutes"), wx_live=cfg_int("wx_live_seconds"),
                           stale=cfg_int("wx_stale_minutes"))


@bp.route("/api/weather/<sid>/series.json")
def api_weather_series(sid):
    """A public station's recent readings for small charts (stored, imperial units): ?period=24h, 7d, 30d or 1y."""
    _need_service()
    row = wx.get_public(sid)
    if row is None:
        abort(404)
    period = request.args.get("period", "24h")
    if period not in wx.PERIODS:
        period = "24h"
    pts = [dict(p, t=int(p["t"].timestamp())) for p in wx.series(row["id"], period)]
    resp = jsonify({"station": row["sid"], "period": period, "points": [{k: (None if v is None else round(v, 2) if isinstance(v, float) else v) for k, v in p.items()} for p in pts]})
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


@bp.route("/weather/<sid>/live.json")
def station_live(sid):
    """The two parts of a station page that change with every reading (the status line and the current conditions), as ready-made HTML for the page to swap in."""
    _need_service()
    row = wx.get_public(sid)
    if row is None:
        abort(404)
    units = _units()
    la = wx.latest_of(row)
    loc = wx.public_loc(row)
    ctx = dict(r=row, la=la, show=lambda k: wx.show(k, la.get(k), units), units=units, cond=wx.condition(la, row["plat"], row["plon"]), compass=wx.compass(la.get("wind_dir")), trend=wx.pressure_trend(row["id"]),
               online=wx.is_online(row), who=_who(row), loc=loc, approx=row["pprivacy"] != "exact")
    resp = jsonify({"sub": render_template("partials/_wx_sub.html", **ctx), "now": render_template("partials/_wx_now.html", **ctx), "online": ctx["online"],
                    "last": row["last_report"].isoformat() if row["last_report"] else None})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/api/weather.json")
def api_weather():
    _need_service()
    units = _units()
    from . import wxsources
    chosen = tuple(sorted({s for s in request.args.get("sources", "members").split(",") if s in dict(wxsources.SOURCES)} or {"members"}))
    bbox = None   # ?bbox=south,west,north,east: only what is in that view (the Weather Map asks for its view and again when it moves out of it)
    try:
        b = [float(x) for x in request.args.get("bbox", "").split(",")]
        if len(b) == 4 and -90 <= b[0] < b[2] <= 90 and -180 <= b[1] < b[3] <= 180:
            bbox = (math.floor(b[0]), math.floor(b[1]), math.ceil(b[2]), math.ceil(b[3]))
    except ValueError:
        pass
    if chosen == ("members",) and not bbox:
        stations = cached(("wx_points", units), lambda: wx.map_points(units))
    else:   # members, APRS and airport weather in one list, each station tagged with its source (an airport APRS carries is shown once, as airport weather)
        info = {}
        stations = cached(("wx_points_src", units, chosen, bbox), lambda: wxsources.points(chosen, units, bbox, info), 30) if not bbox else wxsources.points(chosen, units, bbox, info)
        pending = info.get("pending", 0)
    resp = jsonify({"pending": locals().get("pending", 0), "stations": stations, "stale_minutes": cfg_int("wx_stale_minutes"), "sources": [k for k, _l in wxsources.SOURCES if k in chosen],
                    "cluster": {"px": cfg_int("wx_cluster_px"), "max_zoom": cfg_int("wx_cluster_max_zoom")}})
    resp.headers["Cache-Control"] = f"public, max-age={cfg_int('public_cache_seconds')}"
    return resp


@bp.route("/weather/statistics")
def statistics_page():
    """Charts and tables of every weather source with filters: sources, counties, cities, ZIP codes, stations and the period."""
    _need_service()
    from . import wxstats
    return render_template("weather_stats.html", metrics=wxstats.METRICS, periods=wxstats.PERIODS, cover=wxstats.coverage(), enabled=cfg("wx_stats_enabled") == "1")


@bp.route("/api/weather/stats.json")
def api_stats():
    _need_service()
    from . import wxstats
    from .clientip import client_ip
    from .drivetimes import allowed
    if not allowed(client_ip()):
        return jsonify({"error": "slow down"}), 429
    resp = jsonify(cached(("wxstats", tuple(sorted(request.args.items()))), lambda: wxstats.stats(request.args), 30))
    return resp


@bp.route("/api/weather/stats-areas.json")
def api_stats_areas():
    _need_service()
    from . import wxstats
    return jsonify(cached(("wxstats_areas",), wxstats.areas, 120))


@bp.route("/api/weather/stats-stations.json")
def api_stats_stations():
    _need_service()
    from . import wxstats
    q = request.args.get("q", "").strip()[:40]
    return jsonify({"stations": wxstats.find_stations(q) if len(q) >= 2 else []})


@bp.route("/api/weather/<sid>.json")
def api_weather_station(sid):
    """One public station's latest reading in the stored (imperial) units, for other software."""
    _need_service()
    row = wx.get_public(sid)
    if row is None:
        abort(404)
    la = wx.latest_of(row)
    la.pop("extra", None)
    return jsonify({"station": row["sid"], "name": row["name"] or row["fname"] or row["sid"], "online": wx.is_online(row), "last_report": row["last_report"].isoformat() if row["last_report"] else None,
                    "units": "imperial: F, mph, inHg, inches, miles, W/m2", "reading": la})


@bp.route("/feed/weather")
def feed_weather():
    _need_service()
    from .public import _feed_context
    return render_template("feed_weather.html", fsub="weather", push_host=cfg("wx_push_host") or cfg("feed_host") or cfg("main_address"), push_port=cfg("wx_push_port"),
                           relay_base=(f"https://{cfg('feed_host') or cfg('main_address') or request.host}" if not cfg("wx_push_host") else "http://" + cfg("wx_push_host") + ("" if cfg("wx_push_port") == "80" else ":" + cfg("wx_push_port"))),
                           ecowitt_pull=cfg("wx_ecowitt_pull_enabled") == "1", tempest_pull=cfg("wx_tempest_pull_enabled") == "1",
                           ha_repo=cfg("wx_ha_repo_url"), bridge_url=cfg("wx_bridge_url"), **_feed_context())


# ------------------------------------------------------------------ the owner's pages

def _owned(wid):
    """The member's own station; an administrator who may manage feeders can open anyone's with the same pages (to help someone); what they do is in the audit log under their name."""
    r = wx.get_owned(wid, current_user)
    if r is None and current_user.has_permission("manage_weather"):
        r = get_db().execute(wx.SELECT + "WHERE w.id=%s", (wid,)).fetchone()
    if r is None:
        abort(404)
    return r


def _managing(row):
    """The owner's name when an administrator looks at someone else's station, else empty."""
    if row["owner_id"] == current_user.id:
        return ""
    return _who(row) or "another member"


def _address(row):
    """What the owner types into the gateway or script, from the token and the host name and port in Settings."""
    host = cfg("wx_push_host") or cfg("feed_host") or cfg("main_address") or request.host
    port = cfg("wx_push_port")
    tok = wx.push_token(row)
    base = f"http://{host}" + ("" if port == "80" else f":{port}")
    # Home Assistant, the Tempest bridge and scripts can use https, which every proper site answers (port 80 usually only redirects to it, and a redirected post can be refused):
    # when no separate plain-http name is set in Settings, the relay address shown is the https one. Gateways that only speak plain http keep the http host, path and port.
    secure = f"https://{cfg('feed_host') or cfg('main_address') or request.host}" if not cfg("wx_push_host") else base
    return {"host": host, "port": port, "token": tok, "ecowitt_path": f"/wx/push/{tok}", "wu_path": f"/wx/wu/{tok}?", "relay_url": f"{secure}/wx/relay/{tok}", "ecowitt_url": f"{base}/wx/push/{tok}"}


def _cloud_fields(kind, form):
    if kind == "ecowitt_cloud":
        sec = {k: form.get(k, "").strip()[:80] for k in ("application_key", "api_key", "mac")}
        if not all(sec.values()):
            return None, "Enter the application key, the API key and the device's MAC address from your Ecowitt.net account."
        return sec, None
    if kind == "tempest_cloud":
        sec = {"station_id": form.get("tempest_station", "").strip()[:20], "token": form.get("token", "").strip()[:80]}
        if not sec["station_id"].isdigit() or not sec["token"]:
            return None, "Enter your Tempest station number and your personal access token."
        return sec, None
    return None, None


@bp.route("/my/weather")
@login_required
def my_weather():
    rows = wx.mine(current_user.id)
    feeders = [f for f in st.my_feeders(current_user.id) if not any(r["feeder_uuid"] == f["uuid"] for r in rows)]
    return render_template("my_weather.html", rows=rows, feeders=feeders, kinds=wx.KINDS, pull={"ecowitt_cloud": cfg("wx_ecowitt_pull_enabled") == "1", "tempest_cloud": cfg("wx_tempest_pull_enabled") == "1"},
                           privacy=wx.PRIVACY, online=wx.is_online, want=request.args.get("feeder", ""), full=len(rows) >= cfg_int("wx_max_per_user"), limit=cfg_int("wx_max_per_user"))


@bp.route("/my/weather/add", methods=["POST"])
@login_required
def my_weather_add():
    f = request.form
    kind = f.get("kind", "")
    feeder_uuid = f.get("feeder", "").strip().lower() or None
    sec, err = _cloud_fields(kind, f)
    if err:
        return action_response(False, err, redirect_endpoint="wx.my_weather")
    lat = lon = None
    privacy = f.get("privacy", "general")
    if not feeder_uuid:
        try:
            lat = st._num(f.get("lat"), -90, 90, "Latitude", True)
            lon = st._num(f.get("lon"), -180, 180, "Longitude", True)
        except ValueError as e:
            return action_response(False, str(e), redirect_endpoint="wx.my_weather")
    wid, err = wx.create(current_user, kind, f.get("name", ""), feeder_uuid, f.get("station_id"), lat, lon, privacy, f.get("hardware", ""), f.get("description", ""), bool(f.get("public")), sec)
    if err:
        return action_response(False, err, redirect_endpoint="wx.my_weather")
    audit("wx.create", target=str(wid), detail=kind)
    flash("Weather station added.", "success")
    return redirect(url_for("wx.my_weather_station", wid=wid))


@bp.route("/my/weather/<int:wid>")
@login_required
def my_weather_station(wid):
    row = _owned(wid)
    la = wx.latest_of(row)
    from . import aprspush
    return render_template("my_weather_station.html", aprs=aprspush.context(row), r=row, la=la, addr=_address(row) if row["kind"] in wx.PUSH_KINDS else None, ha_repo=cfg("wx_ha_repo_url"), bridge_url=cfg("wx_bridge_url"), kinds=wx.KINDS, privacy=wx.PRIVACY, online=wx.is_online(row),
                           sec=wx.cloud_secrets(row) if row["kind"] in wx.CLOUD_KINDS else {}, show=lambda k: wx.show(k, la.get(k), wx.units_for()), pull_on=row["kind"] in wx.CLOUD_KINDS and cfg("wx_%s_pull_enabled" % row["kind"].split("_")[0]) == "1",
                           credit=SOURCE_CREDIT.get(row["kind"]), poll=cfg_int("wx_poll_minutes"), stale=cfg_int("wx_stale_minutes"), managing=_managing(row))


@bp.route("/my/weather/<int:wid>/save", methods=["POST"])
@login_required
def my_weather_save(wid):
    row = _owned(wid)
    f = request.form
    vals = {"name": f.get("name", "").strip()[:60], "description": f.get("description", "").strip()[:500], "hardware": f.get("hardware", "").strip()[:200], "public": bool(f.get("public"))}
    if row["feeder_uuid"] is None:
        try:
            vals["lat"] = st._num(f.get("lat"), -90, 90, "Latitude", True)
            vals["lon"] = st._num(f.get("lon"), -180, 180, "Longitude", True)
        except ValueError as e:
            return action_response(False, str(e), redirect_endpoint="wx.my_weather_station", redirect_kwargs={"wid": wid})
        if f.get("privacy") not in wx.PRIVACY:
            return action_response(False, "Choose a location privacy option.", redirect_endpoint="wx.my_weather_station", redirect_kwargs={"wid": wid})
        vals["privacy"] = f["privacy"]
    d = get_db()
    if row["kind"] in wx.CLOUD_KINDS and any(f.get(k) for k in ("application_key", "api_key", "mac", "token", "tempest_station")):
        sec, err = _cloud_fields(row["kind"], f)
        if err:
            return action_response(False, err, redirect_endpoint="wx.my_weather_station", redirect_kwargs={"wid": wid})
        from . import vault
        vals["secrets"] = vault.seal(json.dumps(sec))
        vals["fail_count"] = 0
        vals["last_poll"] = None
    d.execute("UPDATE wx_stations SET " + ", ".join(f"{k}=%s" for k in vals) + " WHERE id=%s", list(vals.values()) + [wid])
    d.commit()
    audit("wx.update", target=str(wid))
    return action_response(True, "Weather station saved.", redirect_endpoint="wx.my_weather_station", redirect_kwargs={"wid": wid})


@bp.route("/my/weather/<int:wid>/new-address", methods=["POST"])
@login_required
def my_weather_new_address(wid):
    row = _owned(wid)
    if row["kind"] not in wx.PUSH_KINDS:
        abort(400)
    from . import vault
    tok = wx.new_token()
    d = get_db()
    d.execute("UPDATE wx_stations SET token_hash=%s, token_sealed=%s WHERE id=%s", (wx.token_hash(tok), vault.seal(tok), wid))
    d.commit()
    audit("wx.new_address", target=str(wid))
    return action_response(True, "A new upload address was made. The old one no longer works: update your station.", redirect_endpoint="wx.my_weather_station", redirect_kwargs={"wid": wid})


@bp.route("/my/weather/<int:wid>/pull", methods=["POST"])
@login_required
def my_weather_pull(wid):
    row = _owned(wid)
    if row["kind"] not in wx.CLOUD_KINDS:
        abort(400)
    ok, msg = wx.pull(row)
    return action_response(ok, ("Reading received from your account." if ok else "Could not read your account: " + msg), redirect_endpoint="wx.my_weather_station", redirect_kwargs={"wid": wid})


@bp.route("/my/weather/<int:wid>/delete", methods=["POST"])
@login_required
def my_weather_delete(wid):
    row = _owned(wid)
    d = get_db()
    d.execute("DELETE FROM wx_stations WHERE id=%s", (wid,))
    d.commit()
    audit("wx.delete", target=str(wid))
    flash("Weather station deleted, with its readings.", "success")
    return redirect(url_for("wx.my_weather") if row["owner_id"] == current_user.id else url_for("wx.admin_weather"))


# ------------------------------------------------------------------ admin

@bp.route("/admin/weather")
@permission_required("manage_weather")
def admin_weather():
    rows = wx.all_stations()
    now = datetime.now(timezone.utc)
    return render_template("admin_weather.html", active="weather", pull={"ecowitt_cloud": cfg("wx_ecowitt_pull_enabled") == "1", "tempest_cloud": cfg("wx_tempest_pull_enabled") == "1"}, privacy=wx.PRIVACY, rows=rows, online=lambda r: wx.is_online(r, now), kinds=wx.KINDS, counts=wx.counts(), kinds_n={k: sum(1 for r in rows if r["kind"] == k) for k in wx.KINDS},
                           enabled=wx.enabled())


@bp.route("/admin/weather/add", methods=["POST"])
@permission_required("manage_weather")
def admin_weather_add():
    """Add a weather station for a member (to help them): it is created under their account and opened with their own page, where the upload address is ready to copy."""
    f = request.form
    from types import SimpleNamespace
    q = f.get("username", "").strip()
    u = get_db().execute("SELECT id, username FROM users WHERE lower(username) = lower(%s) OR lower(email) = lower(%s)", (q, q)).fetchone()
    if u is None:
        return action_response(False, "No member has that user name or email address. Pick one from the list that appears as you type.", redirect_endpoint="wx.admin_weather")
    kind = f.get("kind", "")
    feeder_uuid = f.get("feeder", "").strip().lower() or None
    sec, err = _cloud_fields(kind, f)
    if err:
        return action_response(False, err, redirect_endpoint="wx.admin_weather")
    lat = lon = None
    if not feeder_uuid:
        try:
            lat = st._num(f.get("lat"), -90, 90, "Latitude", True)
            lon = st._num(f.get("lon"), -180, 180, "Longitude", True)
        except ValueError as e:
            return action_response(False, str(e), redirect_endpoint="wx.admin_weather")
    wid, err = wx.create(SimpleNamespace(id=u["id"]), kind, f.get("name", ""), feeder_uuid, f.get("station_id"), lat, lon, f.get("privacy", "general"), f.get("hardware", ""), f.get("description", ""), bool(f.get("public")), sec, by_admin=True)
    if err:
        return action_response(False, err, redirect_endpoint="wx.admin_weather")
    audit("wx.create", target=str(wid), detail=f"{kind} for {u['username']}{' on feeder ' + feeder_uuid if feeder_uuid else ''}, added by an administrator")
    flash(f"Weather station added for {u['username']}. This is their page: copy the upload address from it and give it to them, or set up their gateway with it.", "success")
    return redirect(url_for("wx.my_weather_station", wid=wid))


@bp.route("/admin/weather/<int:wid>/<action>", methods=["POST"])
@permission_required("manage_weather")
def admin_weather_action(wid, action):
    d = get_db()
    row = d.execute("SELECT id, COALESCE(station_id, '') AS sid FROM wx_stations WHERE id=%s", (wid,)).fetchone()
    if row is None or action not in ("disable", "enable", "delete"):
        abort(404)
    if action == "delete":
        d.execute("DELETE FROM wx_stations WHERE id=%s", (wid,))
    else:
        d.execute("UPDATE wx_stations SET disabled=%s WHERE id=%s", (action == "disable", wid))
    d.commit()
    audit("wx." + action, target=str(wid), detail=row["sid"])
    return action_response(True, {"disable": "Station switched off.", "enable": "Station switched on.", "delete": "Station deleted."}[action], redirect_endpoint="wx.admin_weather")
