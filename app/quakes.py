"""Earthquakes for the maps: recent earthquakes in and around Arkansas (the New Madrid zone) from the USGS, as GeoJSON points for the Earthquakes map layer.

Source: the USGS Earthquake Catalog web service (earthquake.usgs.gov/fdsnws/event/1/query), a US Government work in the public domain (see DATA_SOURCES_TERMS.md). The answer is kept a few minutes in memory only."""
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .cache import cached
from .settings import cfg, cfg_int

# the magnitude steps the layer colors by
STEPS = [(4.0, "M4"), (3.0, "M3"), (2.0, "M2"), (-9.0, "M1")]


def step_of(mag):
    for lo, code in STEPS:
        if mag is not None and mag >= lo:
            return code
    return "M1"


def _box():
    """(south, west, north, east) from the setting 'south,west,north,east'; the default covers Arkansas and the New Madrid zone."""
    try:
        s, w, n, e = (float(x) for x in re.split(r"[,\s]+", cfg("quakes_area").strip()))
        if -90 <= s < n <= 90 and -180 <= w < e <= 180:
            return s, w, n, e
    except ValueError:
        pass
    return 32.5, -95.0, 38.5, -88.0


def _build():
    s, w, n, e = _box()
    start = (datetime.now(timezone.utc) - timedelta(days=max(1, cfg_int("quakes_days")))).strftime("%Y-%m-%dT%H:%M:%S")
    q = urllib.parse.urlencode({"format": "geojson", "starttime": start, "minmagnitude": cfg("quakes_min_magnitude") or "1", "minlatitude": s, "maxlatitude": n, "minlongitude": w, "maxlongitude": e,
                                "orderby": "time", "limit": 500})
    req = urllib.request.Request("https://earthquake.usgs.gov/fdsnws/event/1/query?" + q, headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    feats = []
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) < 2 or p.get("mag") is None:
            continue
        t = p.get("time")
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(c[0], 4), round(c[1], 4)]}, "properties": {
            "event": f"M {p['mag']:.1f} earthquake", "headline": p.get("place") or "", "mag": p["mag"], "KIND": step_of(p["mag"]), "depth_km": round(c[2], 1) if len(c) > 2 and c[2] is not None else None,
            "time": datetime.fromtimestamp(t / 1000, timezone.utc).isoformat() if t else "", "link": p.get("url") if str(p.get("url") or "").startswith("https://earthquake.usgs.gov/") else ""}})
    return {"type": "FeatureCollection", "features": feats}


def geojson():
    """Recent earthquakes as a GeoJSON FeatureCollection (empty when the layer is off or the USGS cannot be reached; the last good answer is not kept past its few minutes)."""
    if cfg("quakes_enabled") != "1":
        return {"type": "FeatureCollection", "features": []}
    return cached("quakes", lambda: _build() or {"type": "FeatureCollection", "features": [], "stale": True}, max(60, cfg_int("quakes_cache_seconds")))
