"""Is a place inside Arkansas? Used to keep the home page's "right now" numbers, cards and map to the state when a station or an aircraft appears just over the border or far away.
The state outline is the one the page background uses (ar_outline.json, US Census, generalized); a few miles of margin (Settings > Public Pages) keep stations near the line, whose
positions are rounded for privacy, from dropping out. A place with no position at all cannot be judged and is kept."""
import json
import math
import os

from .settings import cfg, cfg_int

_poly = None


def polygon():
    """[(lon, lat), ...] of the state outline."""
    global _poly
    if _poly is None:
        with open(os.path.join(os.path.dirname(__file__), "ar_outline.json"), encoding="utf-8") as f:
            _poly = [(float(x), float(y)) for x, y in json.load(f)]
    return _poly


def _inside(lat, lon, poly):
    c = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def _edge_miles(lat, lon, poly):
    """Distance in miles from a point to the nearest part of the outline (flat-earth approximation, plenty for a margin of a few miles)."""
    kx, ky = 69.17 * math.cos(math.radians(lat)), 69.0
    best = 1e9
    for i in range(len(poly) - 1):
        ax, ay = (poly[i][0] - lon) * kx, (poly[i][1] - lat) * ky
        bx, by = (poly[i + 1][0] - lon) * kx, (poly[i + 1][1] - lat) * ky
        dx, dy = bx - ax, by - ay
        t = 0 if dx == dy == 0 else max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def contains(lat, lon, margin_miles=0):
    """True for a point in the state or within `margin_miles` of its edge."""
    if lat is None or lon is None:
        return True
    p = polygon()
    if _inside(lat, lon, p):
        return True
    if margin_miles <= 0 or not (32 < lat < 38 and -97 < lon < -88):   # nothing far away is near the edge
        return False
    return _edge_miles(lat, lon, p) <= margin_miles


def limited():
    return cfg("home_limit_ar") == "1"


def inside(lat, lon):
    """The home page's test: always true when the limit is switched off."""
    return (not limited()) or contains(lat, lon, cfg_int("home_limit_buffer_miles"))


def js_config():
    """What the home page's script needs to apply the same test to the aircraft: the switch, the margin and a lighter copy of the outline."""
    p = polygon()
    return {"on": limited(), "margin": cfg_int("home_limit_buffer_miles"), "poly": [[round(x, 2), round(y, 2)] for x, y in p[::3]] if limited() else []}
