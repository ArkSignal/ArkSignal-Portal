"""Weather Alerts for the maps: the National Weather Service's active warnings, watches and advisories as GeoJSON for the Weather Alerts map layer.

Source: api.weather.gov/alerts/active (US Government work, open data; a User-Agent that identifies the site is required, see DATA_SOURCES_TERMS.md). Most alerts name forecast zones or counties instead of
carrying a shape, so each zone's outline is read once and kept for a week; the alert list itself is kept for a couple of minutes. Nothing is stored in the database."""
import json
import re
import time
import urllib.request

from .cache import cached
from .settings import cfg, cfg_int

ZONES_PER_REFRESH = 80   # outlines read in one refresh; the rest follow on the next one (they are kept once read)

# (code, words that put an alert's event name in this kind): the first match wins; the layer colors by the code
KINDS = [
    ("TOR", ("tornado",)),
    ("SVR", ("severe thunderstorm", "special marine warning", "extreme wind")),
    ("FLD", ("flood", "hydrologic")),
    ("WIN", ("winter", "blizzard", "ice storm", "freeze", "frost", "snow", "sleet", "wind chill")),
    ("HOT", ("heat",)),
    ("FIR", ("fire", "red flag", "smoke")),
    ("WND", ("wind", "dust", "fog")),
]


def kind_of(event):
    e = (event or "").lower()
    for code, words in KINDS:
        if any(w in e for w in words):
            return code
    return "OTH"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _slim_ring(ring, tol=0.01):
    """A ring with points closer than `tol` degrees (about a kilometer) to the last kept point dropped and the rest rounded: zone outlines arrive far more detailed than a map layer needs."""
    out = []
    for x, y in (pt[:2] for pt in ring):
        x, y = round(x, 3), round(y, 3)
        if not out or abs(x - out[-1][0]) >= tol or abs(y - out[-1][1]) >= tol:
            out.append([x, y])
    if len(out) >= 3:
        out.append(out[0])
    return out if len(out) >= 4 else None


def _slim(polys):
    res = []
    for poly in polys:
        rings = [r for r in (_slim_ring(r) for r in poly) if r]
        if rings:
            res.append(rings)
    return res


_zones = {}   # zone address -> (when read, polygons); a failed read is not kept, so the next refresh tries it again


def _zone_shape(url, budget):
    """One forecast zone's or county's outline as a list of GeoJSON polygons (each a list of rings), kept for a week. Returns (polygons, whether a read was spent)."""
    now = time.time()
    hit = _zones.get(url)
    if hit and now - hit[0] < 7 * 86400:
        return hit[1], False
    if budget <= 0:
        return [], False
    try:
        g = (_get(url) or {}).get("geometry") or {}
    except Exception:
        return [], True
    polys = _slim([g["coordinates"]] if g.get("type") == "Polygon" else g["coordinates"] if g.get("type") == "MultiPolygon" else [])
    if len(_zones) > 2000:
        _zones.clear()
    _zones[url] = (now, polys)
    return polys, True


def _short(text, n):
    """Text cut at a word end near `n` characters with an ellipsis; blank for nothing."""
    t = re.sub(r"(?<!\n)\n(?![\n*])", " ", (text or "").strip())   # the Weather Service breaks lines by hand inside a paragraph: keep paragraph and bullet breaks only
    t = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", t))
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",;:.") + "..."


def _build():
    states = re.sub(r"[^A-Za-z,]", "", cfg("wxalerts_states")).upper() or "AR"
    try:
        data = _get("https://api.weather.gov/alerts/active?status=actual&message_type=alert,update&area=" + states)
    except Exception:
        return None
    feats, budget = [], ZONES_PER_REFRESH
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        geom = f.get("geometry")
        if not geom:
            polys = []
            for z in p.get("affectedZones") or []:
                if not re.match(r"^https://api\.weather\.gov/zones/", z):
                    continue
                got, spent = _zone_shape(z, budget)
                budget -= 1 if spent else 0
                polys.extend(got)
            if not polys:
                continue
            geom = {"type": "MultiPolygon", "coordinates": polys}
        feats.append({"type": "Feature", "geometry": geom, "properties": {
            "event": p.get("event") or "", "KIND": kind_of(p.get("event")), "headline": p.get("headline") or p.get("event") or "", "area": _short(p.get("areaDesc"), 140),
            "description": _short(p.get("description"), 900), "instruction": _short(p.get("instruction"), 400),
            "severity": p.get("severity") or "", "ends": p.get("ends") or p.get("expires") or "", "sender": p.get("senderName") or ""}})
    return {"type": "FeatureCollection", "features": feats}



def geojson():
    """The active alerts as a GeoJSON FeatureCollection (empty when the layer is switched off or the Weather Service cannot be reached; the last good answer is kept while it is away)."""
    if cfg("wxalerts_enabled") != "1":
        return {"type": "FeatureCollection", "features": []}
    ttl = max(30, cfg_int("wxalerts_cache_seconds"))
    got = cached("wxalerts", lambda: _build() or {"type": "FeatureCollection", "features": [], "stale": True}, ttl)
    return got
