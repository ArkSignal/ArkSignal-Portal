"""Highways for the maps: Interstates, US highways and state highways of the area, as one map layer ("Highways") on every map that has the Layers list.

Source: the US Census Bureau's TIGERweb Transportation service (public domain; terms in DATA_SOURCES_TERMS.md), loaded by Admin > Data Updates and kept here in `roads`, one record per route
and state with all its line pieces, so the maps never ask the Census at view time. The layer serves GeoJSON from this site (`/api/roads.geojson`)."""
import json
import re
import urllib.parse
import urllib.request

from .db import get_db
from .settings import cfg, get_setting, set_setting

LAYERS = (2, 3)   # TIGERweb: 2 = primary roads (Interstates), 3 = secondary roads (US and state highways)
KIND_NAMES = {"I": "Interstate", "U": "US highway", "S": "State highway"}


def _bounds():
    try:
        s, w, n, e = [float(x) for x in cfg("roads_bounds").split(",")]
        if s < n and w < e:
            return s, w, n, e
    except ValueError:
        pass
    return 32.5, -95.0, 37.2, -88.8


def _fetch(layer, offset):
    s, w, n, e = _bounds()
    q = {"where": "RTTYP IN ('I','U','S')", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope", "inSR": "4326", "spatialRel": "esriSpatialRelIntersects", "outFields": "BASENAME,RTTYP,NAME",
         "returnGeometry": "true", "outSR": "4326", "maxAllowableOffset": "0.0006", "geometryPrecision": "4", "resultRecordCount": "4000", "resultOffset": str(offset), "f": "geojson"}
    url = cfg("roads_source_url").rstrip("/") + f"/{layer}/query?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent")})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read(60_000_000).decode("utf-8"))


def _label(kind, base, state):
    base = re.sub(r"[^A-Za-z0-9 ]", "", base or "").strip()
    if not base:
        return ""
    return {"I": f"I-{base}", "U": f"US {base}", "S": f"{state or 'SH'} {base}"}[kind]


def _collections(pages):
    """The GeoJSON collections to read: the given ones (tests), or every page of both TIGERweb layers."""
    if pages is not None:
        yield from pages
        return
    for layer in LAYERS:
        offset = 0
        while True:
            fc = _fetch(layer, offset)
            yield fc
            n = len(fc.get("features", []))
            if n < 4000:
                break
            offset += n


def import_roads(pages=None):
    """Load the highways of the area. `pages` (a list of GeoJSON collections) is for the tests; otherwise TIGERweb is asked. Returns counts."""
    from . import states
    groups = {}
    segs = 0
    for fc in _collections(pages):
        for f in fc.get("features", []):
            p, g = f.get("properties") or {}, f.get("geometry") or {}
            kind = p.get("RTTYP")
            if kind not in KIND_NAMES or not g.get("coordinates"):
                continue
            lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]] if g["type"] == "LineString" else []
            if not lines or not lines[0]:
                continue
            mid = lines[0][len(lines[0]) // 2]
            st = states.state_of(mid[1], mid[0]) if kind == "S" else ""
            if st == "-":
                st = ""
            key = (kind, str(p.get("BASENAME") or "").strip(), st)
            if not key[1]:
                continue
            groups.setdefault(key, {"name": p.get("NAME") or "", "lines": []})["lines"] += [[[round(x, 4), round(y, 4)] for x, y in ln] for ln in lines]
            segs += 1
    if not groups:
        raise RuntimeError("The Census service returned no highways; nothing was changed.")
    d = get_db()
    d.execute("DELETE FROM roads")
    for (kind, base, st), v in groups.items():
        d.execute("INSERT INTO roads (route, kind, state, name, geometry) VALUES (%s,%s,%s,%s,%s)", (_label(kind, base, st), kind, st, v["name"][:60], json.dumps(v["lines"], separators=(",", ":"))))
    d.commit()
    set_setting("roads_version", str(int(__import__("time").time())))
    c = {k: d.execute("SELECT COUNT(*) AS n FROM roads WHERE kind=%s", (k,)).fetchone()["n"] for k in KIND_NAMES}
    return {"routes": sum(c.values()), "interstates": c["I"], "us": c["U"], "state": c["S"], "segments": segs}


def geojson(kinds="IUS"):
    rows = get_db().execute("SELECT route, kind, state, name, geometry FROM roads WHERE kind = ANY(%s) ORDER BY kind, route", (list(kinds),)).fetchall()
    feats = [{"type": "Feature", "properties": {"ROUTE": r["route"], "KIND": r["kind"], "STATE": r["state"], "NAME": f"{r['route']} ({KIND_NAMES[r['kind']]})"},
              "geometry": {"type": "MultiLineString", "coordinates": json.loads(r["geometry"])}} for r in rows]
    return {"type": "FeatureCollection", "features": feats}


def version():
    return get_setting("roads_version") or "0"
