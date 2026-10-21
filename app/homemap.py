"""The home page map: every kind of station the network knows, one layer per service. The ADS-B feeders are drawn here; every other service adds its layer in app/services.py (home_layers) and the home page draws it with a legend entry and a switch, with no change to the page.
Each layer: {key, label, color, points: [{id, name, lat, lon, online, approx, href}]}. Positions come only from the privacy-aware public location."""
from . import feeders as fd
from .public import _map_points
from .settings import cfg


def layers():
    rows = fd.public_feeders(cfg("default_rank_period"), cfg("default_rank_sort"))
    from . import arkansas
    pts = [p for p in _map_points(rows) if arkansas.inside(p["lat"], p["lon"])]
    for p in pts:
        p["href"] = "/feeders/" + str(p["id"]) if p.get("id") else "/feeders/"
    out = [{"key": "adsb", "label": "ADS-B Feeders", "color": "#4fb3ff", "points": pts}]
    from . import services
    out += services.collect("home_layers")   # weather stations, balloon receiving stations and launch sites, and whatever service comes next
    return out
