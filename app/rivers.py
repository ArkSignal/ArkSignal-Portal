"""River and lake gauges for the maps: the National Water Prediction Service's gauges (observed stage and flow with the flood category) in and around Arkansas, as GeoJSON points for the River Gauges layer.

Source: api.water.noaa.gov/nwps/v1/gauges (NOAA/NWS, a US Government work in the public domain; a User-Agent that identifies the site, see DATA_SOURCES_TERMS.md). The answer is kept a few minutes in memory only."""
import json
import re
import urllib.parse
import urllib.request

from .cache import cached
from .settings import cfg, cfg_int

# NWPS flood category -> the step the layer colors by
CATEGORIES = {"no_flooding": "OK", "action": "ACT", "minor": "MIN", "moderate": "MOD", "major": "MAJ"}
LABELS = {"OK": "no flooding", "ACT": "action stage", "MIN": "minor flooding", "MOD": "moderate flooding", "MAJ": "major flooding"}
SIZES = {"OK": 8, "ACT": 10, "MIN": 11, "MOD": 12, "MAJ": 13}   # big enough to find and tap on a phone


def _box():
    """(south, west, north, east) from the setting; the default covers Arkansas and its neighbors' rivers."""
    try:
        s, w, n, e = (float(x) for x in re.split(r"[,\s]+", cfg("rivers_area").strip()))
        if -90 <= s < n <= 90 and -180 <= w < e <= 180:
            return s, w, n, e
    except ValueError:
        pass
    return 32.5, -95.0, 38.5, -88.0


def _num(v, unit):
    return f"{v:g} {unit}" if isinstance(v, (int, float)) else ""


def _build():
    s, w, n, e = _box()
    q = urllib.parse.urlencode({"bbox.xmin": w, "bbox.ymin": s, "bbox.xmax": e, "bbox.ymax": n, "srid": "EPSG_4326"})
    req = urllib.request.Request("https://api.water.noaa.gov/nwps/v1/gauges?" + q, headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    feats = []
    for gz in data.get("gauges") or []:
        ob = ((gz.get("status") or {}).get("observed")) or {}
        cat = CATEGORIES.get(ob.get("floodCategory"))
        if cat is None or gz.get("latitude") is None or gz.get("longitude") is None or not isinstance(ob.get("primary"), (int, float)) or ob["primary"] < -900:
            continue
        lid = re.sub(r"[^A-Za-z0-9]", "", gz.get("lid") or "")
        flow = _num(ob.get("secondary"), ob.get("secondaryUnit") or "") if isinstance(ob.get("secondary"), (int, float)) and ob["secondary"] > -900 else ""
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(gz["longitude"], 4), round(gz["latitude"], 4)]}, "properties": {
            "event": gz.get("name") or lid, "KIND": cat, "icon": "gauge", "size": SIZES[cat], "stage": ob["primary"], "unit": ob.get("primaryUnit") or "",
            "headline": f"Stage {_num(ob['primary'], ob.get('primaryUnit') or '')}" + (f", flow {flow}" if flow else "") + f" ({LABELS[cat]})", "state": (gz.get("state") or {}).get("abbreviation") or "",
            "time": ob.get("validTime") or "", "link": ("https://water.noaa.gov/gauges/" + lid) if lid else ""}})
    return {"type": "FeatureCollection", "features": feats}


def geojson():
    """The gauges as a GeoJSON FeatureCollection (empty when the layer is off or the service cannot be reached)."""
    if cfg("rivers_enabled") != "1":
        return {"type": "FeatureCollection", "features": []}
    return cached("rivers", lambda: _build() or {"type": "FeatureCollection", "features": [], "stale": True}, max(60, cfg_int("rivers_cache_seconds")))
