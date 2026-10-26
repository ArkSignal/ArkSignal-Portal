"""Places for map search: cities and towns, ZIP code areas and counties from the US Census Bureau Gazetteer files (a federal work, public domain; credit the Bureau).

Files (one zip each, tab-separated text): <base>/<year>_Gazetteer/<year>_Gaz_place_national.zip, _zcta_national.zip, _counties_national.zip.
Columns used: place and county USPS, GEOID, NAME, ALAND_SQMI, INTPTLAT, INTPTLONG; ZIP areas GEOID, ALAND_SQMI, INTPTLAT, INTPTLONG.
Only the name, state, land area and an internal point are kept. Replaced as a whole on every update."""
import io
import math
import re
import urllib.request
import zipfile
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int, get_setting, set_setting

bp = Blueprint("places", __name__)
KINDS = {"place": "place", "zcta": "zip", "counties": "county"}
SUFFIX = re.compile(r"\s+(city|town|village|borough|municipality|CDP|township|comunidad|zona urbana|urban county|consolidated government|metro government|unified government|city and borough)(\s*\(balance\))?$", re.I)


def _f(v):
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def _rows(text):
    lines = text.splitlines()
    head = [h.strip() for h in lines[0].split("\t")]
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split("\t")]
        if len(cells) >= len(head):
            yield dict(zip(head, cells))


def _download(kind):
    year = cfg("places_year")
    url = cfg("places_base_url").rstrip("/") + f"/{year}_Gazetteer/{year}_Gaz_{kind}_national.zip"
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent")})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read(60_000_000)
    z = zipfile.ZipFile(io.BytesIO(raw))
    return z.read(z.namelist()[0]).decode("latin-1")


def import_places(sources=None):
    """Replace the places table. `sources` ({"place": text, "zcta": text, "counties": text}) is for tests; normally the files are downloaded."""
    src = sources or {k: _download(k) for k in KINDS}
    out = []
    for r in _rows(src["place"]):
        lat, lon = _f(r.get("INTPTLAT")), _f(r.get("INTPTLONG"))
        if lat is not None and lon is not None:
            out.append(("place", SUFFIX.sub("", r["NAME"]), r.get("USPS", ""), r.get("GEOID", ""), lat, lon, _f(r.get("ALAND_SQMI"))))
    for r in _rows(src["counties"]):
        lat, lon = _f(r.get("INTPTLAT")), _f(r.get("INTPTLONG"))
        if lat is not None and lon is not None:
            out.append(("county", r["NAME"], r.get("USPS", ""), r.get("GEOID", ""), lat, lon, _f(r.get("ALAND_SQMI"))))
    for r in _rows(src["zcta"]):
        lat, lon = _f(r.get("INTPTLAT")), _f(r.get("INTPTLONG"))
        if lat is not None and lon is not None:
            out.append(("zip", r["GEOID"], "", r["GEOID"], lat, lon, _f(r.get("ALAND_SQMI"))))
    if not out:
        raise ValueError("The downloaded files had no places in them.")
    d = get_db()
    d.execute("TRUNCATE places RESTART IDENTITY")
    with d.cursor() as cur:
        cur.executemany("INSERT INTO places (kind, name, state, geoid, lat, lon, land_sqmi) VALUES (%s,%s,%s,%s,%s,%s,%s)", out)
    d.commit()
    c = counts()
    set_setting("places_last_import", datetime.now(timezone.utc).isoformat())
    set_setting("places_last_counts", f"{c['place']} cities and towns, {c['zip']} ZIP areas, {c['county']} counties")
    return c


def counts():
    rows = get_db().execute("SELECT kind, COUNT(*) AS n FROM places GROUP BY kind").fetchall()
    out = {"place": 0, "zip": 0, "county": 0}
    out.update({r["kind"]: r["n"] for r in rows})
    return out


def status():
    return {"counts": counts(), "last": get_setting("places_last_import"), "summary": get_setting("places_last_counts")}


def _km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def search(q, lat=None, lon=None, limit=8):
    """Places whose name starts with the text (or ZIPs starting with the digits). 'Little Rock, AR' narrows to a state. Home states first, then nearest to the map centre."""
    q = (q or "").strip()[:60]
    if len(q) < 2:
        return []
    state = ""
    if "," in q:   # "Little Rock, AR": a trailing two-letter state, found without a backtracking pattern
        head, _, tail = q.rpartition(",")
        tail = tail.strip()
        if len(head.strip()) >= 2 and len(tail) == 2 and tail.isalpha() and tail.isascii():
            q, state = head.strip(), tail.upper()
    d = get_db()
    if q.isdigit():
        rows = d.execute("SELECT * FROM places WHERE kind='zip' AND name LIKE %s ORDER BY name LIMIT 40", (q + "%",)).fetchall()
    else:
        like = q.replace("%", "").replace("_", "") + "%"
        rows = d.execute("SELECT * FROM places WHERE kind IN ('place','county') AND name ILIKE %s" + (" AND state=%s" if state else "") + " LIMIT 400",
                         (like, state) if state else (like,)).fetchall()
    home = {s.strip().upper() for s in cfg("places_home_states").split(",") if s.strip()}
    cx, cy = (lat, lon) if lat is not None and lon is not None else (cfg("map_lat"), cfg("map_lon"))
    cx, cy = float(cx), float(cy)

    def rank(r):
        exact = 0 if r["name"].lower() == q.lower() else 1
        return (0 if (r["state"] in home or r["kind"] == "zip") else 1, exact, 0 if r["kind"] == "place" else 1, _km(cx, cy, r["lat"], r["lon"]))
    out = []
    for r in sorted(rows, key=rank)[:limit]:
        label = {"zip": f"ZIP {r['name']}", "county": f"{r['name']}, {r['state']}", "place": f"{r['name']}, {r['state']}"}[r["kind"]]
        out.append({"label": label, "kind": r["kind"], "lat": round(r["lat"], 5), "lon": round(r["lon"], 5),
                    "zoom": {"zip": 12, "place": 12 if (r["land_sqmi"] or 0) < 60 else 11, "county": 10}[r["kind"]]})
    return out


@bp.route("/api/places/search.json")
def api_search():
    q = request.args.get("q", "")
    lat, lon = request.args.get("lat", type=float), request.args.get("lon", type=float)
    key = ("places", q.lower(), round(lat, 1) if lat is not None else None, round(lon, 1) if lon is not None else None)
    resp = jsonify({"results": cached(key, lambda: search(q, lat, lon))})
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp
