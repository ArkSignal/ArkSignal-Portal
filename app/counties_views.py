"""County map and county pages: the public pages and the data files they read. The logic is in counties.py."""
import hashlib
import json

from flask import Blueprint, abort, jsonify, render_template, request

from . import counties as co
from .cache import cached
from .settings import cfg, cfg_int, get_setting

bp = Blueprint("counties", __name__)

METRIC_KEYS = [k for k, _l in co.METRICS]


def _stamp():
    return get_setting("counties_last_import") or ""


@bp.route("/counties/")
def county_map():
    from . import maplayers
    return render_template("counties_map.html", layers=maplayers.for_map(), metrics=co.METRICS, default_metric=cfg("counties_default_metric"), county_hover=cfg("county_hover"), available=co.available(),
                           label_scale=cfg("live_label_scale"), label_bg=cfg("live_label_bg"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"), wx_refresh=cfg_int("weather_refresh_minutes"))


@bp.route("/api/counties/shapes.json")
def api_shapes():
    """Every county's outline and centre, once; the browser keeps it until the next import (the address carries a version)."""
    def build():
        rows = [{"geoid": c["geoid"], "name": c["name"], "base": c["base"], "slug": c["slug"], "state": c["state"], "lat": c["lat"], "lon": c["lon"], "area": round(c["area"], 1), "geometry": c["geometry"]}
                for c in co.shapes()]
        return json.dumps({"counties": rows, "version": _stamp(), "source": "US Census Bureau TIGERweb"}, separators=(",", ":"))
    body = cached(("county_shapes", _stamp()), build, seconds=3600)
    etag = hashlib.md5(body.encode(), usedforsecurity=False).hexdigest()
    if request.headers.get("If-None-Match") == etag:
        return "", 304
    resp = app_json(body)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


def app_json(body):
    from flask import Response
    return Response(body, mimetype="application/json")


@bp.route("/api/counties/counts.json")
def api_counts():
    resp = jsonify({"counts": co.counts(), "metrics": co.METRICS})
    resp.headers["Cache-Control"] = "public, max-age=30"
    return resp


@bp.route("/api/counties/points.json")
def api_points():
    """Every kind of point the county map can show (hospitals, airports, stations, launch sites, ...), each with its color and what it means."""
    resp = jsonify({"layers": co.overlay_points()})
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


@bp.route("/api/counties/<key>.json")
def api_county(key):
    c = co.get(key)
    if not c:
        abort(404)
    kinds = co.page_data(c)
    return jsonify({"geoid": c["geoid"], "name": c["name"], "slug": c["slug"], "state": c["state"], "area": round(c["area"], 1), "items": kinds})


@bp.route("/counties/<key>")
def county_page(key):
    c = co.get(key)
    if not c:
        abort(404)
    kinds = co.page_data(c)
    counts = {k: len(v) for k, v in kinds.items()}
    counts["trauma"] = sum(1 for h in kinds["hospitals"] if h.get("trauma"))
    nearby = []
    for o in co.shapes():
        if o["geoid"] != c["geoid"] and o["box"][0] <= c["box"][2] + 0.01 and o["box"][2] >= c["box"][0] - 0.01 and o["box"][1] <= c["box"][3] + 0.01 and o["box"][3] >= c["box"][1] - 0.01:
            nearby.append(o)
    from . import trauma as tr
    return render_template("county.html", c=c, kinds=kinds, ct=tr.county_trauma(c["base"], c["state"]), media=tr.media_for("county", c["slug"]), counts=counts, metrics=co.METRICS, nearby=nearby, map_zoom=cfg("map_zoom"), tile_url=cfg("map_tile_url"), tile_attr=cfg("map_attribution"),
                           wx_on=cfg("wx_enabled") == "1", sonde_on=cfg("sonde_enabled") == "1")
