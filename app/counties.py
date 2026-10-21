"""County map: the state's counties as one shape each (US Census Bureau TIGERweb, a federal work in the public domain; credit the Bureau), shaded by a count the visitor picks, every
county opening a county page that lists everything in it.

The shapes are loaded once by an administrator from Admin > Data Updates (job "counties"), kept in the `counties` table as GeoJSON geometry, and served to the map pages as one cached file.
A thing belongs to a county by the point-in-polygon test on its position; stations are placed by their PUBLIC position (hidden ones are left out, a General-privacy one is rounded first),
so a county page never shows more about a station than its own page does."""
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .cache import cached
from .db import get_db
from .settings import cfg, get_setting, set_setting

STATES = {"05": "AR", "29": "MO", "47": "TN", "28": "MS", "22": "LA", "48": "TX", "40": "OK"}
HOME = "05"
_shapes = {"stamp": None, "rows": []}


def slug_of(base, fips):
    s = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return s if fips == HOME else f"{s}-{STATES.get(fips, fips).lower()}"


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def download(fips):
    """The county outlines of one state as GeoJSON from TIGERweb (generalized by the service so the file stays small)."""
    q = {"where": f"STATE='{fips}'", "outFields": "GEOID,NAME,BASENAME,STATE,COUNTY,AREALAND,CENTLAT,CENTLON", "outSR": "4326",
         "maxAllowableOffset": cfg("counties_simplify_degrees"), "geometryPrecision": "4", "f": "geojson"}
    url = cfg("counties_source_url").rstrip("?") + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent")})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read(30_000_000).decode("utf-8"))


def import_counties(sources=None):
    """Replace the counties of the configured states. `sources` ({fips: geojson dict}) is for tests; normally the service is asked."""
    wanted = [s.strip() for s in cfg("counties_states").split(",") if s.strip() in STATES] or [HOME]
    rows = []
    for fips in wanted:
        gj = (sources or {}).get(fips) or download(fips)
        for f in gj.get("features", []):
            p, g = f.get("properties") or {}, f.get("geometry")
            geoid = str(p.get("GEOID") or "")
            if not geoid or not g or g.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            base = p.get("BASENAME") or str(p.get("NAME", "")).replace(" County", "")
            rows.append((geoid, p.get("NAME") or base, base, str(p.get("STATE") or fips), slug_of(base, str(p.get("STATE") or fips)), _num(p.get("CENTLAT")), _num(p.get("CENTLON")),
                         (_num(p.get("AREALAND")) or 0) / 2589988.11, json.dumps(g, separators=(",", ":"))))
    if not rows:
        raise ValueError("The service returned no counties.")
    d = get_db()
    d.execute("TRUNCATE counties")
    with d.cursor() as cur:
        cur.executemany("INSERT INTO counties (geoid, name, base, state_fips, slug, lat, lon, area_sqmi, geometry) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    d.commit()
    now = datetime.now(timezone.utc).isoformat()
    set_setting("counties_last_import", now)
    set_setting("counties_last_counts", f"{len(rows)} counties")
    _shapes["stamp"] = None
    return {"counties": len(rows)}


def _rings(g):
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    return polys


def _bbox(g):
    xs, ys = [], []
    for poly in _rings(g):
        for x, y in poly[0]:
            xs.append(x); ys.append(y)
    return min(ys), min(xs), max(ys), max(xs)


def _in_ring(lat, lon, ring):
    c = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def shapes():
    """Every county with its geometry, kept in this process until the next import."""
    stamp = get_setting("counties_last_import") or ""
    if _shapes["stamp"] != stamp or not _shapes["rows"]:
        rows = []
        for r in get_db().execute("SELECT * FROM counties ORDER BY state_fips, name").fetchall():
            g = json.loads(r["geometry"])
            rows.append({"geoid": r["geoid"], "name": r["name"], "base": r["base"], "state": STATES.get(r["state_fips"], r["state_fips"]), "slug": r["slug"], "lat": r["lat"], "lon": r["lon"],
                         "area": r["area_sqmi"], "geometry": g, "box": _bbox(g)})
        _shapes.update(stamp=stamp, rows=rows)
    return _shapes["rows"]


def available():
    return bool(shapes())


def locate(lat, lon):
    """The county a position is in (a dict from shapes()), or None."""
    if lat is None or lon is None:
        return None
    for c in shapes():
        s, w, n, e = c["box"]
        if not (s <= lat <= n and w <= lon <= e):
            continue
        for poly in _rings(c["geometry"]):
            if _in_ring(lat, lon, poly[0]) and not any(_in_ring(lat, lon, hole) for hole in poly[1:]):
                return c
    return None


def get(key):
    key = (key or "").lower()
    for c in shapes():
        if key in (c["geoid"], c["slug"]):
            return c
    return None


# ------------------------------------------------------------------ what is in each county

METRICS = [("feeders", "Feeders"), ("weather", "Weather Stations"), ("hospitals", "Hospitals"), ("trauma", "Trauma Centers"), ("airports", "Airports"), ("sonde_sites", "Balloon Launch Sites"),
           ("sonde_stations", "Sonde Receiving Stations"), ("balloons", "Balloon Flights (Last 30 Days)")]

# What can be drawn on the county map as points, in the order of the layer list: (kind, label, color, what the dot means). A new service (repeaters, mesh nodes, ...) adds its kind here and
# a loader to _items() below; it then appears in the county counts, on county pages (if the page lists it) and as a switch on the county map, with no change to the map page itself.
OVERLAYS = [("feeders", "ADS-B Feeders", "#4fb3ff", "Stations feeding aircraft data, at the position their owners show"),
            ("weather", "Weather Stations", "#ffb454", "Members' weather stations"),
            ("hospitals", "Hospitals", "#ff6b6b", "Hospitals; the number is the trauma level"),
            ("airports", "Airports", "#c9d66b", "Airports and airfields"),
            ("sonde_sites", "Balloon Launch Sites", "#8fb8ff", "Where weather balloons go up"),
            ("sonde_stations", "Sonde Receiving Stations", "#c58bff", "Stations that pick up weather balloons, at the position their owners show"),
            ("balloons", "Balloon Landings (Last 30 Days)", "#ff8cd9", "Where recent balloons came down")]


def _feeder_items():
    from . import feeders as fd
    out = []
    for f in fd.public_feeders():
        loc = fd.public_location(f)
        if loc:
            out.append({"id": f["station_id"], "name": f["name"] or f["station_id"], "lat": loc[0], "lon": loc[1], "online": bool(f["online"]), "href": "/feeders/" + f["station_id"],
                        "approx": f["privacy"] != "exact"})
    return out


def _weather_items():
    from . import weather as wx
    if cfg("wx_enabled") != "1":
        return []
    out = []
    for s in wx.public_stations():
        if s["loc"]:
            la = s["latest"]
            out.append({"id": s["sid"], "name": s["name"], "lat": s["loc"][0], "lon": s["loc"][1], "online": s["online"], "href": "/weather/" + s["sid"], "approx": s["approx"],
                        "temp_f": la.get("temp_f"), "hum": la.get("hum"), "wind_mph": la.get("wind_mph"), "wind_dir": la.get("wind_dir")})
    return out


def _hospital_items():
    """The same points the aircraft map shows (position, trauma level, emergency, heliport, approximate), so a hospital looks and links the same on every map."""
    from . import hospitals as hp
    states = set(STATES.values())
    return [dict(h, href="/hospitals/" + h["id"]) for h in hp.map_points(-90, -180, 90, 180, limit=100000) if h["state"] in states]


def _airport_items():
    return [{"id": r["ident"], "name": r["name"], "city": r["municipality"], "lat": r["lat"], "lon": r["lon"], "type": r["type"], "href": "/airports/" + r["ident"]}
            for r in get_db().execute("SELECT ident, name, municipality, lat, lon, type FROM airports WHERE region = ANY(%s) AND type IN ('large_airport','medium_airport','small_airport') ORDER BY ident",
                                      (["US-" + s for s in STATES.values()],)).fetchall()]


def _sonde_site_items():
    try:
        from . import sondes
        return sondes.site_items()
    except Exception:
        get_db().rollback()
        return []


def _balloon_items():
    try:
        from . import sondes
        return sondes.landing_items(30)
    except Exception:
        get_db().rollback()
        return []


def _sonde_station_items():
    try:
        from . import sondes
        return [{"id": s["id"], "name": s["id"], "lat": s["lat"], "lon": s["lon"], "href": s["href"], "approx": s["approx"], "reach_km": s["reach_km"]} for s in sondes.map_stations()] if cfg("sonde_enabled") == "1" else []
    except Exception:
        get_db().rollback()
        return []


def _items():
    return {"feeders": _feeder_items(), "weather": _weather_items(), "hospitals": _hospital_items(), "airports": _airport_items(), "sonde_sites": _sonde_site_items(), "sonde_stations": _sonde_station_items(),
            "balloons": _balloon_items()}


def overlay_points():
    """Every kind of point for the county map's switches: [{key, label, color, note, items}] (only kinds that have something), cached briefly like the counts."""
    def build():
        items = _items()
        return [{"key": k, "label": lab, "color": col, "note": note, "items": items.get(k, [])} for k, lab, col, note in OVERLAYS if items.get(k)]
    return cached(("county_overlays", get_setting("counties_last_import") or ""), build)


def _build_index():
    idx = {c["geoid"]: {k: [] for k in ("feeders", "weather", "hospitals", "airports", "sonde_sites", "sonde_stations", "balloons")} for c in shapes()}
    for kind, items in _items().items():
        for it in items:
            c = locate(it["lat"], it["lon"])
            if c:
                idx[c["geoid"]][kind].append(it)
    return idx


def index():
    """{geoid: {kind: [items]}}, cached briefly (Settings > Public Data Cache)."""
    return cached(("county_index", get_setting("counties_last_import") or ""), _build_index)


def counts():
    out = {}
    for geoid, kinds in index().items():
        c = {k: len(v) for k, v in kinds.items()}
        c["trauma"] = sum(1 for h in kinds["hospitals"] if h.get("trauma"))
        out[geoid] = c
    return out


def page_data(c):
    kinds = index().get(c["geoid"], {})
    return kinds


def status():
    return {"count": len(shapes()), "last": get_setting("counties_last_import"), "summary": get_setting("counties_last_counts")}
