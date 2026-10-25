from flask import Blueprint, abort, jsonify, redirect, render_template, request
from flask_login import current_user

from . import rangestats
from .clientip import client_ip, is_private
from .db import get_db
from . import airports as ap
from . import feeders as fd
from . import privacy
from .settings import cfg, cfg_int

bp = Blueprint("public", __name__)


def _map_points(rows):
    """Public map points: coordinates come only from fd.public_location, so privacy is always applied."""
    pts = []
    for r in rows:
        loc = fd.public_location(r)
        if loc:
            pts.append({"id": r["station_id"], "name": r["name"] or r["station_id"], "lat": loc[0], "lon": loc[1],
                        "online": bool(r["online"]), "approx": (r["privacy"] or "general") != "exact", "radius_m": privacy.radius_m(r["privacy"] or "general")})
    return pts


# the pages a kiosk can show: (address, name, seconds, the setting that must be on or None)
KIOSK_PAGES = [("/live", "Aircraft Map", 45, None), ("/weather/map", "Weather Map", 45, "wx_enabled"), ("/weather/", "Weather Overview", 30, "wx_enabled"), ("/sondes/map", "Radiosonde Map", 45, "sonde_enabled"),
               ("/counties/", "County Map", 30, None), ("/trauma/", "Trauma Map", 45, None), ("/trauma/?air=1", "Trauma Map with Air Ambulances", 45, None), ("/aprs/map", "APRS Map", 40, "aprs_enabled"),
               ("/repeaters/map", "Repeater Map", 30, "repeater_enabled"), ("/coverage", "Coverage Map", 30, None), ("/radio/space-weather", "Space Weather", 20, "spaceweather_enabled"),
               ("/feeders/", "Feeders", 30, None), ("/status", "Service Status", 20, None)]


def _kiosk_views(text):
    """'/live|45, /weather/map' as [{path, secs}]: only addresses on this site."""
    out = []
    for part in (text or "").split(","):
        path, _bar, secs = part.partition("|")
        path = path.strip()
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            continue
        out.append({"path": path, "secs": int(secs) if secs.strip().isdigit() else 0})
    return out


@bp.route("/kiosk")
def kiosk():
    """The Kiosk Display: pages shown one after another full screen without menus, for a wall screen. What it shows, for how long, the presets and a single-page mode are chosen on the page itself
    (kept in the browser and in the address); the site's default pages and presets are Settings > Kiosk Display."""
    if cfg("kiosk_enabled") != "1":
        abort(404)
    catalog = [{"path": p, "name": n, "secs": s} for p, n, s, key in KIOSK_PAGES if key is None or cfg(key) == "1"]
    presets = []
    for line in cfg("kiosk_presets").splitlines():
        name, eq, rest = line.partition("=")
        views = _kiosk_views(rest)
        if eq and name.strip() and views:
            presets.append({"name": name.strip(), "views": views})
    views = []
    for line in cfg("kiosk_views").splitlines():
        path, _bar, _name = line.partition("|")
        path = path.strip()
        if path.startswith("/") and not path.startswith("//") and "://" not in path:
            views.append({"path": path, "secs": 0})
    return render_template("kiosk.html", idle_seconds=cfg_int("kiosk_idle_seconds"), clock={"show": cfg("kiosk_clock"), "pos": cfg("kiosk_clock_position"), "size": cfg("kiosk_clock_size"), "seconds": cfg("kiosk_clock_seconds") == "1"}, catalog=catalog, presets=presets, views=views or [{"path": "/live", "secs": 0}], seconds=cfg_int("kiosk_seconds"), reload_minutes=cfg_int("kiosk_reload_minutes"))


@bp.route("/feeders/")
def feeders():
    period = request.args.get("period", cfg("default_rank_period"))
    sort = request.args.get("sort", cfg("default_rank_sort"))
    if period not in fd.PUBLIC_PERIODS:
        period = cfg("default_rank_period")
    if sort not in fd.SORTS:
        sort = cfg("default_rank_sort")
    rows = fd.public_feeders(period, sort)
    from . import mlat
    mlat_ok = mlat.working_uuids()
    for r in rows:
        r["mlat"] = r.get("uuid") in mlat_ok
    from . import achievements as ach
    badges = ach.for_all()
    holders = {}
    for r in rows:
        for code, label in badges.get(r["uuid"], []):
            holders.setdefault(code, []).append(r["station_id"])
    return render_template("feeders.html", badges=badges, achv_defs=ach.definitions() if cfg("achv_enabled") == "1" else [], achv_holders=holders, rows=rows, totals=fd.network_totals(), period=period, sort=sort, badge=cfg("official_badge_text"),
                           periods=fd.PUBLIC_PERIODS, sorts=fd.SORTS, points=_map_points(rows),
                           map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"))


@bp.route("/feeders/<station_id>")
def feeder_public(station_id):
    f = fd.public_feeder(station_id)
    if f is None:
        abort(404)
    period = request.args.get("period", "24h")
    if period not in fd.PERIODS:
        period = "24h"
    points, uptime = fd.series(f["uuid"], period)
    loc = fd.public_location(f)
    from . import mlat
    from . import weather as _wx
    wxrow = _wx.get_public(f["station_id"]) if cfg("wx_enabled") == "1" else None
    from . import achievements as ach
    mine = ach.for_all().get(f["uuid"], [])
    return render_template("feeder_public.html", achv=mine, achv_defs={c: t for c, _l, t in ach.definitions()}, wx=wxrow, mlat_ok=f["uuid"] in mlat.working_uuids(), f=f, loc=loc, badge=cfg("official_badge_text"), approx=(f["privacy"] or "general") != "exact", radius_m=privacy.radius_m(f["privacy"] or "general"),
                           chart=fd.line_chart(points, period), uptime=uptime, period=period, periods=fd.PERIODS,
                           latest=points[-1] if points else None, rng=rangestats.station(f["uuid"], 30), rng_chart=rangestats.polar_svg(rangestats.station(f['uuid'], 30)['sectors']), setup_fields=__import__("app.stations", fromlist=["x"]).SETUP_FIELDS,
                           map_zoom=cfg("map_zoom"))


@bp.route("/api/feeders.json")
def api_feeders():
    """Public network data. Locations have the owner's privacy choice applied; exact coordinates are never included."""
    rows = fd.public_feeders("24h", "positions")
    tot = fd.network_totals()
    out = []
    from . import mlat
    mlat_ok = mlat.working_uuids()
    for r in rows:
        loc = fd.public_location(r)
        out.append({"station_id": r["station_id"], "name": r["name"], "online": bool(r["online"]), "official": bool(r["official"]),
                    "website": r["website"] if r["website_public"] else None,
                    "owner": r["owner_display"] or r["owner_name"], "callsign": r["owner_callsign"],
                    "lat": loc[0] if loc else None, "lon": loc[1] if loc else None,
                    "location_precision": None if not loc else ("exact" if r["privacy"] == "exact" else "approximate"),
                    "positions_per_day_24h": round(r["positions_per_day"]), "uptime_24h": round(r["uptime"], 1),
                    "max_range_nm_30d": round(r["max_range_30d"]), "mlat": r["uuid"] in mlat_ok})
    return jsonify({"totals": {"feeders_online": tot["online"], "feeders_claimed": tot["claimed"], "feeders_seen": tot["total"],
                               "messages_per_second": round(tot["msgs"], 1), "positions_per_second": round(tot["pos"], 1)},
                    "feeders": out})


@bp.route("/search")
def search():
    """One search box for the overview: sends the visitor to the best matching page."""
    import re as _re
    q = request.args.get("q", "").strip()[:40]
    if not q:
        return redirect("/")
    if _re.match(r"^[0-9a-fA-F]{6}$", q):   # an aircraft address is one aircraft: straight to it
        return redirect("/flight/" + q.lower())
    from . import onesearch
    groups = onesearch.search_all(q)
    total = sum(len(items) for _h, items in groups)
    if total == 1 and groups[0][1][0]["href"]:
        return redirect(groups[0][1][0]["href"])
    return render_template("search_results.html", q=q, groups=groups, total=total, qq=__import__("urllib.parse", fromlist=["quote"]).quote(q))


@bp.route("/api/search.json")
def api_search():
    """The one search of every map: airports, places, hospitals, repeaters, APRS stations, balloons and counties that match the text (public things only)."""
    from . import onesearch
    return jsonify({"results": onesearch.search(request.args.get("q", ""))})


@bp.route("/api/weather/alerts.geojson")
def api_weather_alerts():
    """The Weather Alerts map layer: the National Weather Service's active alerts as shapes (app/wxalerts.py)."""
    from flask import Response
    import json as _json
    from . import wxalerts
    resp = Response(_json.dumps(wxalerts.geojson(), separators=(",", ":")), mimetype="application/geo+json")
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


@bp.route("/api/quakes.geojson")
def api_quakes():
    """The Earthquakes map layer: recent earthquakes from the USGS as points (app/quakes.py)."""
    from flask import Response
    import json as _json
    from . import quakes
    resp = Response(_json.dumps(quakes.geojson(), separators=(",", ":")), mimetype="application/geo+json")
    resp.headers["Cache-Control"] = "public, max-age=120"
    return resp


@bp.route("/api/rivers.geojson")
def api_rivers():
    """The River Gauges map layer: river and lake gauges with their flood category (app/rivers.py)."""
    from flask import Response
    import json as _json
    from . import rivers
    resp = Response(_json.dumps(rivers.geojson(), separators=(",", ":")), mimetype="application/geo+json")
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@bp.route("/api/roads.geojson")
def api_roads():
    """The Highways map layer: Interstates, US and state highways kept by this site (loaded from the Census by Data Updates)."""
    import json as _json
    import re as _re
    from flask import Response
    from . import roads
    from .cache import cached
    kinds = _re.sub(r"[^IUS]", "", request.args.get("kinds", "IUS")) or "IUS"
    ver = roads.version()
    body = cached(("roads", kinds, ver), lambda: _json.dumps(roads.geojson(kinds), separators=(",", ":")), 3600)
    resp = Response(body, mimetype="application/geo+json")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    resp.headers["ETag"] = f'"{ver}-{kinds}"'
    return resp.make_conditional(request)


@bp.route("/coverage")
def coverage():
    from . import maplayers
    return render_template("coverage.html", layers=maplayers.for_map(), label_scale=cfg("live_label_scale"), label_bg=cfg("live_label_bg"), wx_refresh=cfg_int("weather_refresh_minutes"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"), coverage_days=cfg_int("coverage_days"),
                           mlat_on=cfg("mlat_enabled") == "1", uat_on=cfg("uat_enabled") == "1", wx_on=cfg("wx_enabled") == "1", wx_live=cfg_int("wx_live_seconds"), sonde_on=cfg("sonde_enabled") == "1", sonde_live=cfg_int("sonde_live_seconds"))


@bp.route("/mlat/")
def mlat_network():
    from . import mlat
    return render_template("mlat_network.html", net=mlat.network(), enabled=cfg("mlat_enabled") == "1",
                           map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"))


@bp.route("/api/mlat/network.json")
def api_mlat_network():
    from . import mlat
    return jsonify(mlat.network() or {"stations": [], "working": 0})


def _feed_context(**extra):
    return dict(feed_host=cfg("feed_host"), feed_port=cfg("feed_port"), lookback=cfg_int("check_feeding_hours"),
                mlat_enabled=cfg("mlat_enabled") == "1", mlat_port=cfg("mlat_port"), uat_enabled=cfg("uat_enabled") == "1", wx_enabled=cfg("wx_enabled") == "1", sonde_enabled=cfg("sonde_enabled") == "1", acars_enabled=cfg("acars_enabled") == "1", mesh_enabled=cfg("mesh_enabled") == "1", **extra)


@bp.route("/feed/")
def feed():
    """The overview of everything a member can feed the network: the aircraft receiver and each of the other services, with what each needs and where its own page is."""
    if request.args.get("check") == "1":
        return redirect("/feed/check?check=1", code=302)
    from . import services
    others = []
    for s in services.enabled():
        if s.get("feed"):
            others.append({"name": s["feed"][0], "href": s["feed"][1], "text": (s.get("about") or ("", "", ""))[2]})
    return render_template("feed_overview.html", fsub="overview", others=others, **_feed_context())


@bp.route("/feed/adsb")
def feed_adsb():
    return render_template("feed.html", fsub="adsb", **_feed_context())


@bp.route("/feed/mlat")
def feed_mlat():
    if cfg("mlat_enabled") != "1":
        abort(404)
    return render_template("feed_mlat.html", fsub="mlat", **_feed_context())


@bp.route("/feed/uat")
def feed_uat():
    if cfg("uat_enabled") != "1":
        abort(404)
    return render_template("feed_uat.html", fsub="uat", **_feed_context())


@bp.route("/feed/check")
def feed_check():
    check = request.args.get("check") == "1"
    ip = client_ip()
    found = []
    if check:
        from . import mlat
        for r in fd.feeders_for_ip(ip, cfg_int("check_feeding_hours")):
            r = dict(r)
            full = r.pop("full_uuid", None)   # the real UUID is used here only, to look up MLAT; the page never gets it for a claimed station
            r["mlat"] = mlat.station_state(full)
            found.append(r)
    wx_rows = []
    if check and cfg("wx_enabled") == "1" and current_user.is_authenticated:
        from . import weather as wx
        for r in get_db().execute(wx.SELECT + "WHERE w.owner_id = %s ORDER BY w.id", (current_user.id,)).fetchall():
            wx_rows.append({"id": r["id"], "sid": r["sid"], "name": r["name"], "kind": wx.KINDS[r["kind"]], "online": wx.is_online(r) and not r["disabled"], "disabled": r["disabled"],
                            "last_report": r["last_report"], "error": r["last_error"], "pull": r["kind"] in wx.CLOUD_KINDS})
    sat_rows = []
    if check and cfg("satimg_enabled") == "1" and current_user.is_authenticated:
        sat_rows = get_db().execute("SELECT r.id, r.name, r.kind, r.enabled, r.last_image_at, (SELECT COUNT(*) FROM sat_images i WHERE i.receiver_id = r.id) AS n FROM sat_receivers r WHERE r.owner_id = %s ORDER BY lower(r.name)", (current_user.id,)).fetchall()
    rid_rows = []
    if check and cfg("rid_enabled") == "1" and current_user.is_authenticated:
        rid_rows = get_db().execute("SELECT id, name, enabled, last_report, msgs FROM rid_stations WHERE owner_id = %s ORDER BY lower(name)", (current_user.id,)).fetchall()
    ais_rows = []
    if check and cfg("ais_enabled") == "1" and current_user.is_authenticated:
        ais_rows = get_db().execute("SELECT id, name, enabled, last_report, msgs FROM ais_stations WHERE owner_id = %s ORDER BY lower(name)", (current_user.id,)).fetchall()
    return render_template("feed_check.html", fsub="check", check=check, ip=ip, found=found, private_ip=is_private(ip), wx_rows=wx_rows, sat_rows=sat_rows, ais_rows=ais_rows, rid_rows=rid_rows, rid_enabled=cfg("rid_enabled") == "1", ais_enabled=cfg("ais_enabled") == "1", satimg_enabled=cfg("satimg_enabled") == "1", **_feed_context())


@bp.route("/about")
def about():
    from . import build_info
    built = build_info.read()["built"]
    from . import services
    return render_template("about.html", service_about=[s["about"] for s in services.enabled() if s.get("about") and (current_user.is_authenticated or not s["about"][0].startswith(("/my/", "/chat", "/admin")))], intro=cfg("about_intro"), team=cfg("about_team"), tagline=cfg("site_tagline"), built=built[:10] if built[:4].isdigit() else None,
                           feed_host=cfg("feed_host"), license_note=cfg("map_attribution"))


@bp.route("/contact")
def contact():
    rows = get_db().execute("SELECT * FROM site_contacts WHERE shown ORDER BY sort_order, lower(name)").fetchall()
    return render_template("contact.html", contacts=rows, intro=cfg("contact_intro"))


@bp.route("/healthz")
def healthz():
    return "ok"


@bp.route("/airports/")
def airports():
    q = request.args.get("q", "").strip()
    region = request.args.get("region", "")
    kind = request.args.get("kind", "")
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per = cfg_int("page_size")
    sort = request.args.get("sort", "") if request.args.get("sort") in ap.SORTS else ""
    direction = "desc" if request.args.get("dir") == "desc" else "asc"
    rows, total = ap.search(q, region, kind, per, (page - 1) * per, sort, direction)
    args = {k: v for k, v in request.args.items() if k != "page" and v}
    return render_template("airports.html", rows=rows, sort=sort, direction=direction, q=q, region=region, kind=kind, regions=ap.regions(),
                           kinds=ap.TYPE_LABELS, pager={"page": page, "pages": max(1, -(-total // per)), "total": total, "args": args})


@bp.route("/airports/<ident>")
def airport(ident):
    a, freqs, runs = ap.get_airport(ident)
    if a is None:
        abort(404)
    return render_template("airport.html", a=a, freqs=freqs, runs=runs, kinds=ap.TYPE_LABELS)


HEAT_DURATIONS = [("1", "1 hour"), ("3", "3 hours"), ("6", "6 hours"), ("12", "12 hours"), ("24", "24 hours"), ("48", "2 days"),
                  ("72", "3 days"), ("168", "7 days"), ("336", "14 days"), ("720", "30 days")]
HEAT_ENDS = [("0", "Now"), ("24", "1 day ago"), ("48", "2 days ago"), ("168", "7 days ago"), ("336", "14 days ago"), ("720", "30 days ago")]


def _fill(url):
    """Fill the map placeholders in a tool address from the Map Defaults in Admin > Settings."""
    for key, value in (("{lat}", cfg("map_lat")), ("{lon}", cfg("map_lon")), ("{zoom}", cfg("map_zoom")), ("{zoom_page}", cfg("map_zoom_page"))):
        url = url.replace(key, value)
    return url


@bp.route("/view/<slug>")
def view_tool(slug):
    from . import tools
    t = tools.get(slug)
    if t is None and slug == "map":   # the embedded tar1090 map is gone: an old link or bookmark opens the live map (on the aircraft it asked for)
        icao = request.args.get("icao", "")
        return redirect("/live" + ("?icao=" + icao.lower() if __import__("re").match(r"^[0-9a-fA-F]{6}$", icao) else ""), code=301)
    if t is None:
        abort(404)
    if t["slug"] in ("planefence", "plane-alert") and not current_user.is_authenticated:   # heavy pages: members only
        return redirect("/my/login?next=" + request.path)
    if t["slug"] in ("heatmap", "replay") and cfg("history_native") == "1" and request.args.get("classic") != "1":
        # the portal's own Replay and Heat Map on the live map; ?classic=1 (or Settings > Public Pages) brings back the embedded tar1090 pages
        q = {"heatmap": "heat=1", "replay": "replay=1"}[t["slug"]]
        if t["slug"] == "heatmap" and request.args.get("duration"):
            q += "&heatHours=" + request.args["duration"] + ("&heatEnd=" + request.args["end"] if request.args.get("end") else "")
        if __import__("re").match(r"^[0-9a-fA-F]{6}$", request.args.get("icao", "")):
            q += "&icao=" + request.args["icao"].lower()
        return redirect("/live?" + q)
    src = _fill(t["url"])
    icao = request.args.get("icao", "")
    if __import__("re").match(r"^[0-9a-fA-F]{6}$", icao) and src.startswith("/map/"):
        src += ("&" if "?" in src else "?") + "icao=" + icao.lower()
    st = request.args.get("station", "")
    if st and __import__("re").match(r"^[A-Za-z0-9-]{2,20}$", st) and "/grafana/d/" in src.split("?")[0]:   # a Grafana dashboard with a Station selector
        src += ("&" if "?" in src else "?") + "var-station=" + st
    heat = None
    path, _, query = src.partition("?")
    tokens = query.split("&") if query else []
    if "heatmap" in [tk.split("=")[0] for tk in tokens]:
        # tar1090's heatmap shows a window of time: heatDuration hours, ending heatEnd hours ago
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(cfg("timezone"))
        now = datetime.now(timezone.utc)
        error = ""
        shown_override = None
        duration, end = request.args.get("duration", cfg("heat_default_duration")), request.args.get("end", "0")
        if duration not in dict(HEAT_DURATIONS):
            duration = cfg("heat_default_duration")
        if end not in dict(HEAT_ENDS):
            end = "0"
        hours, ago = float(duration), float(end)
        custom = False
        f_from, f_to = request.args.get("from", ""), request.args.get("to", "")
        if f_from and f_to:
            try:
                s_dt = datetime.fromisoformat(f_from).replace(tzinfo=tz)
                e_dt = datetime.fromisoformat(f_to).replace(tzinfo=tz)
                span = (e_dt - s_dt).total_seconds() / 3600
                if span < 0.5:
                    error = "The end must be at least 30 minutes after the start."
                elif span > cfg_int("heat_max_days") * 24:
                    error = f"The longest range is {cfg_int('heat_max_days')} days."
                elif e_dt.astimezone(timezone.utc) > now + timedelta(minutes=5):
                    error = "The end cannot be in the future."
                else:
                    hours, ago, custom = round(span, 2), round(max(0.0, (now - e_dt.astimezone(timezone.utc)).total_seconds() / 3600), 2), True
                    shown_override = (s_dt, e_dt)
            except ValueError:
                error = "Enter the start and end as dates and times."
        tokens = [tk for tk in tokens if tk.split("=")[0] not in ("heatDuration", "heatEnd")]
        tokens += [f"heatDuration={hours:g}", f"heatEnd={ago:g}"]
        src = path + "?" + "&".join(tokens)
        shown_to = (now - timedelta(hours=ago)).astimezone(tz)
        shown_from = shown_to - timedelta(hours=hours)
        if shown_override:  # show back exactly what was typed; the rounded offsets can be a few seconds off
            shown_from, shown_to = shown_override
        heat = {"duration": duration, "end": end, "durations": HEAT_DURATIONS, "ends": HEAT_ENDS, "custom": custom, "error": error,
                "from": shown_from.strftime("%Y-%m-%dT%H:%M"), "to": shown_to.strftime("%Y-%m-%dT%H:%M"),
                "tz": cfg("timezone"), "max_days": cfg_int("heat_max_days")}
    return render_template("view_tool.html", tool=t, src=src, heat=heat)


_ICONS = {"favicon.ico": "image/x-icon", "favicon.svg": "image/svg+xml", "favicon-16.png": "image/png", "favicon-32.png": "image/png",
          "apple-touch-icon.png": "image/png", "icon-192.png": "image/png", "icon-512.png": "image/png"}


def _icon_view(name, mime):
    def view():
        import os
        from flask import current_app, send_from_directory
        return send_from_directory(os.path.join(current_app.root_path, "static", "icons"), name, mimetype=mime, max_age=86400)
    return view


for _name, _mime in _ICONS.items():
    bp.add_url_rule("/" + _name, "icon_" + _name.replace(".", "_").replace("-", "_"), _icon_view(_name, _mime))


@bp.route("/site.webmanifest")
def webmanifest():
    resp = jsonify({"name": cfg("site_name"), "short_name": cfg("site_name"), "start_url": "/", "display": "standalone", "background_color": "#070d17", "theme_color": "#070d17",
                    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"}, {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}]})
    resp.headers["Content-Type"] = "application/manifest+json"
    return resp
