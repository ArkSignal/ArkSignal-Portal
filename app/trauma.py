"""The trauma system: Arkansas' trauma regions and zones, the hospitals of the trauma system, and the websites and pictures shown on hospital and county pages.

Where the data comes from (all of it is public; credited on the pages):
  * which county is in which trauma region and which ADH trauma zone: data_trauma_ar_counties.json, read from the Arkansas Department of Health's own trauma map (healthy.arkansas.gov);
  * the designated trauma centers and their levels: data_trauma_ar_centers.json, the Department's "Designated Trauma Centers" list (it carries its own date); it is applied to the hospitals
    we keep (hospitals.import_trauma matches by name and city, and the CMS list gives each hospital its address and position);
  * which hospitals the Trauma Map shows (`hospitals.trauma_scope`): every hospital of the home state (with an emergency department, unless Settings says every hospital), every designated trauma
    center in any state, and every hospital of a neighboring state within N minutes by road of the state line (our own estimate from the routing server; a straight-line distance when it is off);
  * a hospital's or county's website and picture: Wikidata (website, picture) and Wikimedia Commons (author and license, shown with the picture), kept in `place_media`; what an administrator
    typed in is never replaced.
Other states are added later by adding their two data files and naming the state in Settings > Hospitals > Trauma Map: Home State."""
import json
import math
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, render_template, request

from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int, set_setting

bp = Blueprint("trauma", __name__)
_DIR = os.path.dirname(os.path.abspath(__file__))

# the colors of the Department's own map: one per region, one per zone, one per trauma level
REGION_COLORS = {"Northwest": "#4a78c2", "Arkansas Valley": "#f0a030", "North Central": "#8a5ec2", "Northeast": "#d9534f", "Metro": "#9aa5b1", "Southeast": "#e0b800", "Southwest": "#3aa655"}
ZONE_COLORS = {1: "#5b7fb5", 2: "#e0883a"}
LEVEL_COLORS = {"I": "#ff3b3b", "II": "#ff8c42", "III": "#f5c542", "IV": "#c9d66b", "": "#cfd6df"}   # the symbols every map uses for hospitals (static/mapshell.css .hico)
LEVEL_NAMES = {"I": "Level I", "II": "Level II", "III": "Level III", "IV": "Level IV", "": "Non-Designated"}


def _data(name):
    def load():
        with open(os.path.join(_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    return cached(("trauma_file", name), load, seconds=86400)


def home_state():
    return (cfg("trauma_hospital_states").split(",")[0].strip() or "AR").upper()


def county_table():
    """{'Pulaski': {'region': 'Metro', 'zone': 1}, ...} for the home state (empty for a state without data yet)."""
    if home_state() != "AR":
        return {}
    return _data("data_trauma_ar_counties.json")["counties"]


def county_trauma(base, state="AR"):
    """{'region', 'zone'} of a county by its plain name ('Pulaski'; 'Pulaski County' also works), or None."""
    if state != home_state():
        return None
    want = (base or "").replace(" County", "").replace(".", "").strip().lower()
    for k, v in county_table().items():
        if k.lower().replace(".", "") == want:
            return v
    return None


def regions():
    """[{'key', 'name', 'color', 'counties': [...]}] in the order the Department lists them, with where to put the region's name on the map."""
    from . import counties as co
    pts = {c["base"]: c for c in co.shapes() if c["state"] == home_state()}
    out = []
    for name in ["Northwest", "North Central", "Northeast", "Arkansas Valley", "Metro", "Southeast", "Southwest"]:
        cs = sorted(k for k, v in county_table().items() if v["region"] == name)
        if cs:
            out.append({"key": name, "name": name + " Region", "color": REGION_COLORS[name], "counties": cs, **_middle([pts[c] for c in cs if c in pts])})
    return out


def zones():
    from . import counties as co
    pts = {c["base"]: c for c in co.shapes() if c["state"] == home_state()}
    out = []
    for z in (1, 2):
        cs = sorted(k for k, v in county_table().items() if v["zone"] == z)
        if cs:
            out.append({"key": z, "name": f"ADH Trauma Zone {z}", "color": ZONE_COLORS[z], "counties": cs, **_middle([pts[c] for c in cs if c in pts])})
    return out


def _middle(cs):
    """Where a label for a group of counties goes: the middle of their centers, weighted by size."""
    w = [max(c["area"], 1.0) for c in cs]
    if not cs:
        return {"lat": None, "lon": None}
    t = sum(w)
    return {"lat": round(sum(c["lat"] * x for c, x in zip(cs, w)) / t, 4), "lon": round(sum(c["lon"] * x for c, x in zip(cs, w)) / t, 4)}


def county_assignments():
    """{geoid: {'region', 'zone', 'slug', 'website', 'photo', 'credit', 'creditPage'}} for the county shapes we keep."""
    from . import counties as co
    media = {r["key"]: r for r in get_db().execute("SELECT key, website, photo_url, photo_credit, photo_page FROM place_media WHERE kind='county'").fetchall()}
    out = {}
    for c in co.shapes():
        t = county_trauma(c["base"], c["state"])
        if t:
            m = media.get(c["slug"])
            out[c["geoid"]] = {**t, "slug": c["slug"], "website": m["website"] if m else "", "photo": m["photo_url"] if m else "", "credit": m["photo_credit"] if m else "", "creditPage": m["photo_page"] if m else ""}
    return out


# ---------------------------------------------------------------------------------- the designated trauma centers
SOURCE = "Arkansas Department of Health, Designated Trauma Centers"


def import_centers():
    """Apply the Department's list of designated trauma centers to the hospitals we keep. The list is the authority for the home state: every level set earlier in the state (or from this list) is
    cleared first, then each center is found by its CMS certification number (the list in the data file carries it where the hospital is in the CMS list), else by name and city; a center the CMS list
    does not have is added and placed with OpenStreetMap's place finder. Returns {'matched', 'added', 'placed', 'unplaced': [names], 'unreadable': [names]}."""
    from . import hospitals as hp
    d = get_db()
    home = home_state()
    out = {"matched": 0, "added": 0, "placed": 0, "unplaced": [], "unreadable": []}
    d.execute("UPDATE hospitals SET trauma='', trauma_date='', trauma_source='' WHERE (state=%s OR trauma_source LIKE %s) AND trauma <> '' AND trauma_source <> 'Administrator'", (home, SOURCE + "%"))
    d.commit()
    have = {h["facility_id"]: dict(h) for h in d.execute("SELECT facility_id, name, city, state, address, zip, lat, lon, services FROM hospitals WHERE trauma_source <> 'Administrator'").fetchall()}   # a level an administrator chose stays
    used = set()
    for r in _data("data_trauma_ar_centers.json")["centers"]:
        name = r["name"].replace("(ACH)", "").replace("(UAMS)", "").replace("(JRMC)", "").strip()
        h = have.get(r.get("ccn", "")) if r.get("ccn") else None
        if h is None:
            best, score = None, 0.0
            for x in have.values():
                if x["facility_id"] in used or x["state"] != r["state"] or x["city"].lower() != r["city"].lower():
                    continue
                sim = hp._similar(name, x["name"])
                if sim >= 0.6 and sim > score:
                    best, score = x, sim
            h = best
        peds = "Children" in name
        if h is not None:
            used.add(h["facility_id"])
            svc = [x for x in (h["services"] or "").split(",") if x]
            if peds and "pediatric" not in svc:
                svc.append("pediatric")
            d.execute("UPDATE hospitals SET trauma=%s, trauma_date=%s, trauma_source=%s, emergency=TRUE, services=%s WHERE facility_id=%s", (r["level"], r["designated"], SOURCE, ",".join(svc), h["facility_id"]))
            out["matched"] += 1
            continue
        fid = "imp-" + re.sub(r"[^a-z0-9]+", "-", f"{r['state']}-{name}".lower()).strip("-")[:60]
        d.execute("INSERT INTO hospitals (facility_id, name, city, state, htype, emergency, pos_source, trauma, trauma_date, trauma_source, services) VALUES (%s,%s,%s,%s,'Added from the trauma list',TRUE,'',%s,%s,%s,%s) "
                  "ON CONFLICT (facility_id) DO UPDATE SET trauma=EXCLUDED.trauma, trauma_date=EXCLUDED.trauma_date, trauma_source=EXCLUDED.trauma_source",
                  (fid, name, r["city"], r["state"], r["level"], r["designated"], SOURCE, "pediatric" if peds else ""))
        out["added"] += 1
        row = d.execute("SELECT * FROM hospitals WHERE facility_id=%s", (fid,)).fetchone()
        d.commit()   # the place finder is a slow outside call: no transaction open while it answers
        if row["lat"] is None:
            try:
                pos = hp.locate_osm(dict(row))
            except Exception:
                pos = None
            if pos:
                d.execute("UPDATE hospitals SET lat=%s, lon=%s, pos_source='osm' WHERE facility_id=%s", (pos[0], pos[1], fid))
                out["placed"] += 1
            else:
                out["unplaced"].append(name)
        d.commit()
    d.commit()
    hp.link_heliports()
    set_setting("hospitals_last_trauma", datetime.now(timezone.utc).isoformat())
    return out


# ---------------------------------------------------------------------------------- which hospitals the Trauma Map shows
def _outline():
    return _data("ar_outline.json")


def border_points(step_km=4.0):
    """Points along the Arkansas state line, about every `step_km`."""
    ring = _outline()
    out = []
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        d = _km(y1, x1, y2, x2)
        n = max(1, int(d // step_km))
        for i in range(n):
            out.append((y1 + (y2 - y1) * i / n, x1 + (x2 - x1) * i / n))
    return out


def _km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    h = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def _near_border(lat, lon, pts, k=24):
    """The k nearest points of the state line and the straight-line distance to the nearest (km)."""
    ranked = sorted(((_km(lat, lon, a, b), (a, b)) for a, b in pts), key=lambda t: t[0])[:k]
    return ranked[0][0], [p for _d, p in ranked]


def compute_scope():
    """Work out `hospitals.trauma_scope` and `border_min` for every hospital; returns the counts."""
    d = get_db()
    home = home_state()
    emergency_only = cfg("trauma_emergency_only") == "1"
    minutes = cfg_int("trauma_border_minutes")
    fallback_km = float(cfg("trauma_border_fallback_km") or 0)
    d.execute("UPDATE hospitals SET trauma_scope='', border_min=NULL")
    d.execute("UPDATE hospitals SET trauma_scope='state' WHERE state=%s AND (NOT %s OR emergency OR trauma <> '')", (home, emergency_only))
    d.execute("UPDATE hospitals SET trauma_scope='system' WHERE trauma_scope='' AND trauma <> ''")
    d.commit()   # never hold a transaction open across the routing server's answers: it would block backups, restores and the pages that write
    out = {"state": 0, "system": 0, "border": 0, "routed": 0, "straight": 0}
    if minutes > 0 and home == "AR":
        pts = border_points()
        cand = d.execute("SELECT facility_id, lat, lon FROM hospitals WHERE trauma_scope='' AND emergency AND lat IS NOT NULL AND state<>%s", (home,)).fetchall()
        osrm_ok, fails = cfg("osrm_enabled") == "1", 0
        limit_km = minutes * 1.9   # nothing is faster than about 114 km/h: a hospital farther than this in a straight line cannot be within the time
        for h in cand:
            straight, near = _near_border(h["lat"], h["lon"], pts)
            if straight > max(limit_km, fallback_km):
                continue
            mins = None
            if osrm_ok and fails < 3:   # three failures in a row: the routing server is not answering, the rest use the straight line
                try:
                    from . import driveranges
                    secs = [s for s in driveranges._table((h["lat"], h["lon"]), near) if s is not None]
                    mins = round(min(secs) / 60.0, 1) if secs else None
                    fails = 0
                except Exception:
                    mins, fails = None, fails + 1
            if mins is not None:
                out["routed"] += 1
                if mins <= minutes:
                    d.execute("UPDATE hospitals SET trauma_scope='border', border_min=%s WHERE facility_id=%s", (mins, h["facility_id"]))
                    d.commit()
            elif straight <= fallback_km:
                out["straight"] += 1
                d.execute("UPDATE hospitals SET trauma_scope='border', border_min=NULL WHERE facility_id=%s", (h["facility_id"],))
                d.commit()
    d.commit()
    for r in d.execute("SELECT trauma_scope, COUNT(*) AS n FROM hospitals WHERE trauma_scope<>'' GROUP BY trauma_scope").fetchall():
        out[r["trauma_scope"]] = r["n"]
    set_setting("trauma_last_scope", json.dumps(out))
    return out


def run_system_job():
    r = import_centers()
    s = compute_scope()
    return (f"{r['matched']} designated trauma centers matched" + (f", {r['added']} added ({r['placed']} placed on the map)" if r["added"] else "") + (f", not placed: {', '.join(r['unplaced'])}" if r["unplaced"] else "")
            + f"; the Trauma Map shows {s['state']} in the state, {s['system']} designated elsewhere and {s['border']} near the state line")


# ---------------------------------------------------------------------------------- websites and pictures
def _get(url, data=None, timeout=90):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/sparql-results+json, application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(30_000_000).decode("utf-8")


def _sparql(q):
    url = cfg("trauma_wikidata_url").rstrip("?") + "?" + urllib.parse.urlencode({"query": q, "format": "json"})
    return json.loads(_get(url))["results"]["bindings"]


def _file_name(uri):
    return urllib.parse.unquote(uri.rsplit("/", 1)[-1])


def _credit(names):
    """{file name: (credit text, file page)} for pictures on Wikimedia Commons: the author and the license."""
    out = {}
    base = cfg("trauma_commons_url").rstrip("?")
    names = list(names)
    for i in range(0, len(names), 40):
        chunk = names[i:i + 40]
        q = {"action": "query", "titles": "|".join("File:" + n for n in chunk), "prop": "imageinfo", "iiprop": "extmetadata|url", "format": "json", "formatversion": "2"}
        try:
            d = json.loads(_get(base + "?" + urllib.parse.urlencode(q)))
        except Exception:
            continue
        for p in d.get("query", {}).get("pages", []):
            ii = (p.get("imageinfo") or [{}])[0]
            md = ii.get("extmetadata") or {}
            artist = re.sub(r"<[^>]+>", "", (md.get("Artist") or {}).get("value", "")).strip() or "Wikimedia Commons contributor"
            lic = (md.get("LicenseShortName") or {}).get("value", "")
            title = p.get("title", "")[5:].replace(" ", "_")
            out[title] = (f"{artist[:120]}" + (f", {lic}" if lic else ""), ii.get("descriptionurl", ""))
    return out


def _save_media(kind, key, website, image, source):
    """Keep what was found, but never over an administrator's own entries; returns True when something was written."""
    d = get_db()
    cur = d.execute("SELECT edited, website, photo_url FROM place_media WHERE kind=%s AND key=%s", (kind, key)).fetchone()
    if cur and cur["edited"]:
        return False
    website = website or (cur["website"] if cur else "")
    image = image or ((cur["photo_url"], "", "") if cur and cur["photo_url"] else ("", "", ""))
    d.execute("INSERT INTO place_media (kind, key, website, photo_url, photo_credit, photo_page, source, checked) VALUES (%s,%s,%s,%s,%s,%s,%s,now()) "
              "ON CONFLICT (kind, key) DO UPDATE SET website=EXCLUDED.website, photo_url=EXCLUDED.photo_url, photo_credit=EXCLUDED.photo_credit, photo_page=EXCLUDED.photo_page, source=EXCLUDED.source, checked=now() "
              "WHERE NOT place_media.edited", (kind, key, website, image[0], image[1], image[2], source))
    return True


def import_media():
    """Look up the website and a picture of every county of the home state and of every hospital the Trauma Map shows, in Wikidata."""
    from . import hospitals as hp
    d = get_db()
    home = home_state()
    found = {"counties": 0, "hospitals": 0, "pictures": 0}
    fips = {"AR": "05"}.get(home, "")
    rows = []
    if fips:
        rows = _sparql('SELECT ?c ?code ?site ?img WHERE { ?c wdt:P882 ?code . FILTER(STRSTARTS(?code, "%s")) OPTIONAL { ?c wdt:P856 ?site } OPTIONAL { ?c wdt:P18 ?img } }' % fips)
    hosp = [dict(h) for h in d.execute("SELECT facility_id, name, lat, lon, state FROM hospitals WHERE trauma_scope<>'' AND lat IS NOT NULL").fetchall()]
    hrows = []
    if hosp:
        s, n = min(h["lat"] for h in hosp) - .05, max(h["lat"] for h in hosp) + .05
        w, e = min(h["lon"] for h in hosp) - .05, max(h["lon"] for h in hosp) + .05
        hrows = _sparql('SELECT ?h ?label ?loc ?site ?img WHERE { SERVICE wikibase:box { ?h wdt:P625 ?loc . bd:serviceParam wikibase:cornerSouthWest "Point(%s %s)"^^geo:wktLiteral . '
                        'bd:serviceParam wikibase:cornerNorthEast "Point(%s %s)"^^geo:wktLiteral } ?h wdt:P31/wdt:P279* wd:Q16917 . OPTIONAL { ?h wdt:P856 ?site } OPTIONAL { ?h wdt:P18 ?img } '
                        'SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . ?h rdfs:label ?label } }' % (w, s, e, n))
    names = set()
    for r in rows:
        if r.get("img"):
            names.add(_file_name(r["img"]["value"]))
    for r in hrows:
        if r.get("img"):
            names.add(_file_name(r["img"]["value"]))
    credits = _credit(names)

    def image_of(r):
        if not r.get("img"):
            return None
        n = _file_name(r["img"]["value"]).replace(" ", "_")
        c = credits.get(n)
        if not c:
            return None
        return ("https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(n) + "?width=800", c[0], c[1])
    geo = {c["geoid"]: c for c in d.execute("SELECT geoid, slug FROM counties").fetchall()}
    for r in rows:
        gid = r["code"]["value"]
        if gid in geo and (r.get("site") or r.get("img")):
            img = image_of(r)
            if _save_media("county", geo[gid]["slug"], r["site"]["value"] if r.get("site") else "", img, "Wikidata"):
                found["counties"] += 1
                found["pictures"] += 1 if img else 0
    pts = []
    for r in hrows:
        m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", r["loc"]["value"])
        if m:
            pts.append((float(m.group(2)), float(m.group(1)), r))
    for h in hosp:
        best, score = None, 0.0
        for lat, lon, r in pts:
            dist = _km(h["lat"], h["lon"], lat, lon)
            sim = hp._similar(h["name"], r["label"]["value"]) if r.get("label") else 0
            sc = sim + (0.5 if dist <= 1.0 else 0)
            if dist <= 3.0 and sim >= 0.5 and sc > score:
                best, score = r, sc
        if best and (best.get("site") or best.get("img")):
            img = image_of(best)
            if _save_media("hospital", h["facility_id"], best["site"]["value"] if best.get("site") else "", img, "Wikidata"):
                found["hospitals"] += 1
                found["pictures"] += 1 if img else 0
    d.commit()   # what Wikidata gave is kept before the slower OpenStreetMap queries start
    # hospitals Wikidata knows no website for: the website tag of the hospital on OpenStreetMap (one query, matched by name and distance)
    left = [h for h in hosp if not media_for("hospital", h["facility_id"])["website"]]
    if left and cfg("trauma_overpass_url"):
        import time
        els, tiles, began = [], {}, time.time()
        for h in left:   # small queries, one per degree square that holds a hospital: the public server answers those, and refuses one big one
            tiles.setdefault((math.floor(h["lat"]), math.floor(h["lon"])), []).append(h)
        for (la, lo) in tiles:
            if time.time() - began > 240:   # the public server is busy: what it did answer is used, the rest waits for the next run
                break
            q = ('[out:json][timeout:60];(nwr["amenity"="hospital"]["website"](%s,%s,%s,%s);nwr["amenity"="hospital"]["contact:website"](%s,%s,%s,%s););out center tags;' % ((la - .03, lo - .03, la + 1.03, lo + 1.03) * 2))
            for _try in range(2):
                try:
                    els += json.loads(_get(cfg("trauma_overpass_url"), data=urllib.parse.urlencode({"data": q}).encode(), timeout=45)).get("elements", [])
                    break
                except Exception:
                    time.sleep(4)
            time.sleep(2)
        osm = []
        for el in els:
            c = el.get("center") or el
            tg = el.get("tags") or {}
            site = tg.get("website") or tg.get("contact:website") or ""
            if c.get("lat") is not None and re.match(r"^https?://", site):
                osm.append((c["lat"], c["lon"], tg.get("name", ""), site))
        for h in left:
            best, score = None, 0.0
            for lat, lon, name, site in osm:
                dist = _km(h["lat"], h["lon"], lat, lon)
                sim = hp._similar(h["name"], name)
                if dist <= 0.6 and sim >= 0.4 and sim + (0.5 if dist <= 0.2 else 0) > score:
                    best, score = site, sim + (0.5 if dist <= 0.2 else 0)
            if best and _save_media("hospital", h["facility_id"], best, None, "OpenStreetMap"):
                found["hospitals"] += 1
    d.commit()
    return found


def run_media_job():
    c = import_media()
    return f"{c['counties']} counties and {c['hospitals']} hospitals have a website or picture ({c['pictures']} pictures)"


def media_for(kind, key):
    r = get_db().execute("SELECT website, photo_url, photo_credit, photo_page FROM place_media WHERE kind=%s AND key=%s", (kind, key)).fetchone()
    return dict(r) if r else {"website": "", "photo_url": "", "photo_credit": "", "photo_page": ""}


def save_media(kind, key, website, photo_url, credit):
    """An administrator's own website, picture and credit for a hospital or county (kept over every lookup); all three blank hands it back to the lookup."""
    website, photo_url, credit = (website or "").strip(), (photo_url or "").strip(), (credit or "").strip()[:200]
    for v, what in ((website, "website"), (photo_url, "picture address")):
        if v and not re.match(r"^https?://[^\s]+$", v):
            return f"The {what} must start with http:// or https://."
    d = get_db()
    if not (website or photo_url or credit):
        d.execute("DELETE FROM place_media WHERE kind=%s AND key=%s AND edited", (kind, key))
    else:
        d.execute("INSERT INTO place_media (kind, key, website, photo_url, photo_credit, photo_page, source, edited, checked) VALUES (%s,%s,%s,%s,%s,'','Administrator',TRUE,now()) "
                  "ON CONFLICT (kind, key) DO UPDATE SET website=EXCLUDED.website, photo_url=EXCLUDED.photo_url, photo_credit=EXCLUDED.photo_credit, photo_page='', source='Administrator', edited=TRUE, checked=now()",
                  (kind, key, website, photo_url, credit))
    d.commit()
    return ""


# ---------------------------------------------------------------------------------- what the Trauma Map reads
def hospitals_for_map():
    """The hospitals the Trauma Map shows, each with everything its popup needs."""
    d = get_db()
    rows = d.execute("SELECT h.facility_id, h.name, h.address, h.city, h.state, h.zip, h.county, h.phone, h.emergency, h.trauma, h.trauma_date, h.services, h.lat, h.lon, h.pos_source, h.heliport_ident, "
                     "h.trauma_scope, h.border_min, m.website, m.photo_url, m.photo_credit, m.photo_page FROM hospitals h LEFT JOIN place_media m ON m.kind='hospital' AND m.key=h.facility_id "
                     "WHERE h.trauma_scope <> '' AND h.lat IS NOT NULL ORDER BY h.name").fetchall()
    from . import counties as co
    out = []
    for r in rows:
        here = co.locate(r["lat"], r["lon"])   # the county the hospital stands in (its position is better than the county text of the list)
        cname = here["base"] if here else r["county"].title()
        ct = county_trauma(cname, r["state"]) if cname else None
        addr = ", ".join(x for x in [r["address"], r["city"], (r["state"] + " " + r["zip"]).strip()] if x)
        out.append({"id": r["facility_id"], "name": r["name"], "address": addr, "city": r["city"], "state": r["state"], "county": cname, "phone": r["phone"],
                    "lat": r["lat"], "lon": r["lon"], "level": r["trauma"], "levelName": LEVEL_NAMES.get(r["trauma"], "Non-Designated"), "peds": "pediatric" in (r["services"] or "").split(","),
                    "emergency": r["emergency"], "scope": r["trauma_scope"], "borderMin": r["border_min"], "region": ct["region"] if ct else "", "zone": ct["zone"] if ct else None,
                    "website": r["website"] or "", "photo": r["photo_url"] or "", "credit": r["photo_credit"] or "", "creditPage": r["photo_page"] or "", "href": "/hospitals/" + r["facility_id"],
                    "approx": r["pos_source"] in ("zip", "")})
    return out


def map_payload():
    return {"state": home_state(), "regions": regions(), "zones": zones(), "counties": county_assignments(), "hospitals": hospitals_for_map(),
            "levels": [{"key": k, "name": LEVEL_NAMES[k], "color": LEVEL_COLORS[k]} for k in ("I", "II", "III", "IV", "")], "borderMinutes": cfg_int("trauma_border_minutes"),
            "source": "Arkansas Department of Health (trauma regions, zones and designated centers); hospitals: CMS; pictures: Wikimedia Commons"}


@bp.route("/trauma/")
def trauma_map():
    from . import maplayers
    return render_template("trauma_map.html", layers=maplayers.for_map(), label_scale=cfg("live_label_scale"), label_bg=cfg("live_label_bg"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"),
                           border_minutes=cfg_int("trauma_border_minutes"), state=home_state())


@bp.route("/trauma")
def trauma_redirect():
    from flask import redirect
    return redirect("/trauma/", code=301)


@bp.route("/api/trauma/data.json")
def api_data():
    resp = jsonify(cached(("trauma_payload", home_state()), map_payload, seconds=15))
    resp.headers["Cache-Control"] = "public, max-age=15"
    return resp
