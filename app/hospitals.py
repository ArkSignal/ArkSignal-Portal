"""Hospitals: the CMS Hospital General Information list (US government, public domain), located with the Census Bureau's free batch geocoder,
with trauma level, services and the linked heliport kept by an administrator (CMS does not publish trauma designations).

Source: the dataset 'xubh-q36u' on data.cms.gov; its download address changes with every update, so it is looked up in the catalogue first.
Geocoder: https://geocoding.geo.census.gov/geocoder/locations/addressbatch (up to 10,000 addresses per file, no key).
A re-import never overwrites what an administrator entered (trauma level, services, heliport link, position set by hand)."""
import csv
import io
import json
import math
import urllib.request
import uuid
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, render_template, request

from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int, get_setting, set_setting

bp = Blueprint("hospitals", __name__)
TRAUMA = [("", "Not designated or not known"), ("I", "Level I"), ("II", "Level II"), ("III", "Level III"), ("IV", "Level IV"), ("V", "Level V")]
SERVICES = [("stroke", "Stroke centre"), ("neuro", "Neurosurgery"), ("cardiac", "Cardiac care and heart attack (STEMI)"), ("obstetric", "Obstetrics"), ("nicu", "Newborn intensive care (NICU)"),
            ("pediatric", "Children's care"), ("burn", "Burn centre"), ("ortho", "Orthopedics and spine"), ("oncology", "Cancer care"), ("psychiatric", "Psychiatric"),
            ("surgery", "General surgeon"), ("icu", "Trauma intensive care (ICU)"), ("ct", "CT scanner"), ("vascular", "Vascular surgery"), ("ir", "Interventional radiology"),
            ("facial", "Facial injuries"), ("eye", "Eye injuries")]
SERVICE_WORDS = {"stroke": "stroke", "neuro": "neuro", "neurosurgery": "neuro", "neurology": "neuro", "cardiac": "cardiac", "stemi": "cardiac", "heart": "cardiac", "cath": "cardiac",
                 "ob": "obstetric", "obstetric": "obstetric", "obstetrics": "obstetric", "maternity": "obstetric", "labor": "obstetric", "nicu": "nicu", "neonatal": "nicu",
                 "pediatric": "pediatric", "pediatrics": "pediatric", "peds": "pediatric", "children": "pediatric", "burn": "burn", "ortho": "ortho", "orthopedic": "ortho",
                 "orthopedics": "ortho", "spine": "ortho", "surgery": "surgery", "surgeon": "surgery", "icu": "icu", "ct": "ct", "vascular": "vascular", "ir": "ir",
                 "interventional": "ir", "facial": "facial", "eye": "eye", "oncology": "oncology", "cancer": "oncology", "psychiatric": "psychiatric", "behavioral": "psychiatric"}


def parse_services(text):
    """'Stroke; OB, NICU' -> (['stroke', 'obstetric', 'nicu'], ['words we did not know'])."""
    import re
    keep, unknown = [], []
    for w in re.split(r"[;,/|]+", (text or "").lower()):
        w = w.strip()
        if not w:
            continue
        k = SERVICE_WORDS.get(w) or SERVICE_WORDS.get(w.split(" ")[0])
        if k and k not in keep:
            keep.append(k)
        elif not k:
            unknown.append(w)
    return keep, unknown
CATALOG = "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/"


def _get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent")})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(80_000_000)


def download_rows():
    meta = json.loads(_get(CATALOG + cfg("hospitals_dataset_id"), 30))
    url = meta["distribution"][0]["downloadURL"]
    return list(csv.DictReader(io.StringIO(_get(url).decode("utf-8-sig"))))


def geocode(addresses):
    """{id: (lat, lon)} for the addresses the Census geocoder can match. `addresses` is [(id, street, city, state, zip)]."""
    if not addresses:
        return {}
    buf = io.StringIO()
    csv.writer(buf).writerows(addresses)
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="benchmark"\r\n\r\nPublic_AR_Current\r\n',
             f'--{boundary}\r\nContent-Disposition: form-data; name="addressFile"; filename="a.csv"\r\nContent-Type: text/csv\r\n\r\n{buf.getvalue()}\r\n',
             f"--{boundary}--\r\n"]
    req = urllib.request.Request(cfg("geocoder_url"), data="".join(parts).encode("utf-8"),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": cfg("lookup_user_agent")})
    with urllib.request.urlopen(req, timeout=300) as r:
        text = r.read(20_000_000).decode("utf-8", "replace")
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 6 and row[2] == "Match" and "," in row[5]:
            lon, lat = row[5].split(",")
            out[row[0]] = (float(lat), float(lon))
    return out


def passes_filter(r):
    """Whether a CMS row is a facility we want: its type is ticked, its name has none of the left-out words, and (if asked) it has an emergency department."""
    types = {t.strip() for t in cfg("hospital_types").split(",") if t.strip()}
    if types and r["Hospital Type"] not in types:
        return False
    name = r["Facility Name"].upper()
    if any(w in name for w in (x.strip().upper() for x in cfg("hospital_exclude_words").split(",")) if w):
        return False
    return not (cfg("hospital_emergency_only") == "1" and r["Emergency Services"] != "Yes")


def apply_filter():
    """Remove loaded facilities that no longer pass the filter, except ones an administrator edited or added by hand. Returns how many were removed."""
    d = get_db()
    types = {t.strip() for t in cfg("hospital_types").split(",") if t.strip()}
    words = [x.strip().upper() for x in cfg("hospital_exclude_words").split(",") if x.strip()]
    gone = []
    for h in d.execute("SELECT facility_id, name, htype, emergency FROM hospitals WHERE trauma = '' AND NOT heliport_set AND pos_source NOT IN ('manual', 'list', 'osm', 'verified') AND facility_id NOT LIKE 'imp-%'").fetchall():
        bad = (types and h["htype"] not in types) or any(w in h["name"].upper() for w in words) or (cfg("hospital_emergency_only") == "1" and not h["emergency"])
        if bad:
            gone.append(h["facility_id"])
    if gone:
        d.execute("DELETE FROM hospitals WHERE facility_id = ANY(%s)", (gone,))
        d.commit()
    return len(gone)


ACRONYMS = {"Chi": "CHI", "Va": "VA", "Uams": "UAMS", "Llc": "LLC", "Lp": "LP", "Llp": "LLP", "Hma": "HMA", "Hcs": "HCS", "Ii": "II", "Iii": "III", "Iv": "IV", "Ut": "UT", "Ou": "OU",
            "Osu": "OSU", "Umc": "UMC", "Ahs": "AHS", "Er": "ER", "Icu": "ICU", "Nw": "NW", "Ne": "NE", "Sw": "SW", "Se": "SE", "Of": "of", "And": "and", "The": "The"}


def tidy(name):
    """CMS gives names in capitals: Title Case, with the usual abbreviations put back (CHI, VA, UAMS, LLC)."""
    import re
    if not name.isupper():
        return name
    out = re.sub(r"[A-Za-z']+", lambda m: ACRONYMS.get(m.group(0).capitalize(), m.group(0).capitalize()), name)
    return out[0].upper() + out[1:] if out else out


def street_variants(street):
    """The same street written the ways the Census geocoder is most likely to know: no suite or box, numbers as digits, no #."""
    import re
    s = re.sub(r",.*$", "", street or "").strip()
    s = re.sub(r"\b(suite|ste|unit|bldg|building|floor|fl|po box)\b.*$", "", s, flags=re.I).strip()
    words = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
    first = s.split(" ", 1)[0].lower().strip("#")
    if first in words:
        s = words[first] + (" " + s.split(" ", 1)[1] if " " in s else "")
    s = s.lstrip("#")
    return [v for v in dict.fromkeys([s]) if v and v != (street or "")]


def heliport_position(d, name, city, state):
    """A hospital the geocoder cannot find may still have a helipad in the airport data named after it: use that (it is on the campus). None when unsure."""
    best, tie = None, False
    t = _tokens(name) - {"heliport", "helipad", "helistop"}
    if not t:
        return None
    for a in d.execute("SELECT name, lat, lon FROM airports WHERE type='heliport' AND municipality ILIKE %s AND region = %s", (city, "US-" + state)).fetchall():
        ta = _tokens(a["name"]) - {"heliport", "helipad", "helistop"}
        if not ta:
            continue
        s = len(t & ta) / min(len(t), len(ta)) - 0.01 * abs(len(t) - len(ta))
        if s >= 0.6 and (best is None or s > best[0] + 0.05):
            best, tie = (s, a["lat"], a["lon"]), False
        elif best is not None and abs(s - best[0]) <= 0.05 and s >= 0.6:
            tie = True
    return None if best is None or tie else (best[1], best[2])


def import_hospitals(rows=None, geocoder=None, states=None):
    """Add or refresh hospitals for the chosen states. `rows` and `geocoder` are for tests; normally both come from the outside services."""
    states = {s.strip().upper() for s in (states or cfg("hospital_states")).split(",") if s.strip()}
    if not states:
        raise ValueError("Choose at least one state.")
    rows = [r for r in (rows if rows is not None else download_rows()) if r["State"] in states and passes_filter(r)]
    if not rows:
        raise ValueError("The downloaded list has no hospitals for those states and types.")
    d = get_db()
    have = {r["facility_id"]: r for r in d.execute("SELECT facility_id, address, city, zip, lat, lon, pos_source FROM hospitals").fetchall()}
    need = [(r["Facility ID"], r["Address"], r["City/Town"], r["State"], r["ZIP Code"]) for r in rows
            if r["Facility ID"] not in have or have[r["Facility ID"]]["lat"] is None or have[r["Facility ID"]]["address"] != r["Address"] or have[r["Facility ID"]]["pos_source"] in ("zip", "")]
    found = (geocoder or geocode)(need)
    retry = [(fid, v, city, st, z) for fid, street, city, st, z in need if fid not in found for v in street_variants(street)[:1]]
    if retry:
        try:
            found.update((geocoder or geocode)(retry))
        except Exception:
            pass
    if not d.execute("SELECT 1 FROM places LIMIT 1").fetchone():
        try:   # a hospital the geocoder cannot find falls back to its ZIP area or town: that needs the places data
            from . import places
            places.import_places()
        except Exception:
            d.rollback()
    zips = {r["name"]: (r["lat"], r["lon"]) for r in d.execute("SELECT name, lat, lon FROM places WHERE kind='zip'").fetchall()}
    cities = {(p["name"].lower(), p["state"]): (p["lat"], p["lon"]) for p in d.execute("SELECT name, state, lat, lon FROM places WHERE kind='place'").fetchall()}
    added = updated = 0
    for r in rows:
        fid = r["Facility ID"]
        old = have.get(fid)
        lat = lon = None
        src = ""
        if old and old["pos_source"] in ("manual", "list", "osm", "verified"):
            lat, lon, src = old["lat"], old["lon"], old["pos_source"]
        elif fid in found:
            (lat, lon), src = found[fid], "census"
        elif old and old["lat"] is not None and old["address"] == r["Address"] and old["pos_source"] not in ("zip", ""):
            lat, lon, src = old["lat"], old["lon"], old["pos_source"]
        elif (hp := heliport_position(d, r["Facility Name"], r["City/Town"].title(), r["State"])) is not None:
            (lat, lon), src = hp, "heliport"
        elif r["ZIP Code"][:5] in zips:
            (lat, lon), src = zips[r["ZIP Code"][:5]], "zip"
        elif (r["City/Town"].lower(), r["State"]) in cities:
            (lat, lon), src = cities[(r["City/Town"].lower(), r["State"])], "zip"   # the middle of the town: shown as approximate
        vals = (fid, tidy(r["Facility Name"]), r["Address"].title(), r["City/Town"].title(), r["State"], r["ZIP Code"][:5],
                r["County/Parish"].title(), r["Telephone Number"], r["Hospital Type"], r["Hospital Ownership"], r["Emergency Services"] == "Yes",
                int(r["Hospital overall rating"]) if r["Hospital overall rating"].isdigit() else None, lat, lon, src)
        if old is None:
            d.execute("INSERT INTO hospitals (facility_id, name, address, city, state, zip, county, phone, htype, ownership, emergency, rating, lat, lon, pos_source) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", vals)
            added += 1
        else:
            d.execute("UPDATE hospitals SET name=%s, address=%s, city=%s, state=%s, zip=%s, county=%s, phone=%s, htype=%s, ownership=%s, emergency=%s, rating=%s, lat=%s, lon=%s, pos_source=%s, updated=now() "
                      "WHERE facility_id=%s", vals[1:] + (fid,))
            updated += 1
    d.commit()
    removed = apply_filter()
    link_heliports()
    set_setting("hospitals_last_import", datetime.now(timezone.utc).isoformat())
    set_setting("hospitals_last_counts", f"{added} added, {updated} refreshed, {removed} removed by the type filter, {len(found)} positions from the Census geocoder")
    return {"added": added, "updated": updated, "removed": removed, "geocoded": len(found), "total": counts()["total"]}


STOP = {"the", "of", "and", "at", "in", "for", "health", "system", "hospital", "medical", "center", "centre", "regional", "memorial", "community", "inc", "llc", "st", "saint", "mc", "campus"}


def _tokens(name):
    import re
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and ").replace("'", "").replace("’", ""))
    return {w for w in words if w not in STOP and len(w) > 1}


def _similar(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    h = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def parse_level(text):
    """'I', '1', 'Level II', 'III' -> '1'..'5' as I..V; blank, '0', 'none', 'not designated' -> ''. Returns None when it cannot be read."""
    t = (text or "").strip().upper().replace("LEVEL", "").replace(".", "").strip()
    if t in ("", "0", "NONE", "N/A", "NA", "NOT DESIGNATED", "NON-DESIGNATED", "-"):
        return ""
    roman = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V", "I": "I", "II": "II", "III": "III", "IV": "IV", "V": "V"}
    return roman.get(t)


def import_trauma(text, apply=True):
    """Match a list of trauma-designated hospitals (CSV: name, city, state, level, designated, lat, lon, source) to the hospitals we have and set their level.
    A hospital is matched by position (within the match distance) and a similar name, or by the same city and a very similar name. A row that matches nothing
    is added as a new hospital (freestanding emergency rooms, VA sites and others the CMS list lacks). With apply=False nothing is written and the result says what would happen."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows:
        raise ValueError("The file has no rows.")
    head = {k.strip().lower() for k in rows[0].keys() if k}
    if "name" not in head or not ({"level", "services"} & head):
        raise ValueError("The first line must name the columns: name, ccn, city, state, level, designated, lat, lon, services, source (name is required, and at least one of level and services).")
    has_level, has_services = "level" in head, "services" in head
    d = get_db()
    allh = [dict(h) for h in d.execute("SELECT facility_id, name, city, state, lat, lon, trauma, services, pos_source FROM hospitals").fetchall()]
    by_id = {h["facility_id"].upper(): h for h in allh}
    km = float(cfg("hospital_match_km"))
    out = {"matched": 0, "added": 0, "changed": 0, "unreadable": [], "added_names": [], "matched_pairs": [], "unknown_services": []}
    used = set()
    for n, r in enumerate(rows, 2):
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        name, city, state = r.get("name", ""), r.get("city", ""), r.get("state", "").upper()
        if "," in city and not state:
            city, state = [x.strip() for x in city.rsplit(",", 1)]
            state = state.upper()
        level = parse_level(r.get("level", "")) if has_level else ""
        svc, unknown_svc = parse_services(r.get("services", "")) if has_services else ([], [])
        if unknown_svc:
            out.setdefault("unknown_services", []).extend(unknown_svc)
        if not name or level is None:
            out["unreadable"].append(f"line {n}: {name or '(no name)'} (level '{r.get('level', '')}')")
            continue
        try:
            lat, lon = (float(r["lat"]), float(r["lon"])) if r.get("lat") and r.get("lon") else (None, None)
        except ValueError:
            lat = lon = None
        best, score = None, 0.0
        ccn = (r.get("ccn") or r.get("facility_id") or "").upper()
        if ccn and ccn in by_id and by_id[ccn]["facility_id"] not in used:
            best, score = by_id[ccn], 9.0   # the CMS certification number is exact: no guessing from names
        for h in ([] if best else allh):
            if h["facility_id"] in used:
                continue
            sim = _similar(name, h["name"])
            near = lat is not None and h["lat"] is not None and _km(lat, lon, h["lat"], h["lon"]) <= km
            same_city = state and h["state"] == state and city and h["city"].lower() == city.lower()
            s = (sim + (0.5 if near else 0)) if (near and sim >= 0.34) or (same_city and sim >= 0.6) or sim >= 0.99 and h["state"] == state else 0
            if s > score:
                best, score = h, s
        if best is None and lat is not None and city:
            # names differ (CoxHealth and Cox Medical Center South): accept the one unused hospital in the same city that is close by
            close = [h for h in allh if h["facility_id"] not in used and h["state"] == state and h["city"].lower() == city.lower() and h["lat"] is not None
                     and _km(lat, lon, h["lat"], h["lon"]) <= min(km, 1.5)]
            if len(close) == 1:
                best = close[0]
        if best is not None:
            used.add(best["facility_id"]); out["matched"] += 1
            if apply and lat is not None and best["pos_source"] in ("zip", ""):   # only a guess so far: the list's own position is better
                d.execute("UPDATE hospitals SET lat=%s, lon=%s, pos_source='list' WHERE facility_id=%s", (lat, lon, best["facility_id"]))
                out["moved"] = out.get("moved", 0) + 1
            out["matched_pairs"].append(f"{name} = {best['name']}")
            if has_level and best["trauma"] != level:
                out["changed"] += 1
            if apply:
                if has_level:
                    d.execute("UPDATE hospitals SET trauma=%s, trauma_date=%s, trauma_source=%s, emergency = emergency OR %s WHERE facility_id=%s",
                              (level, r.get("designated", "")[:40], r.get("source", "")[:200], bool(level), best["facility_id"]))
                if has_services and svc:   # the file adds to the services on record and never removes one an administrator chose
                    merged = [x for x in best["services"].split(",") if x] + [x for x in svc if x not in best["services"].split(",")]
                    best["services"] = ",".join(merged)
                    d.execute("UPDATE hospitals SET services=%s WHERE facility_id=%s", (best["services"], best["facility_id"]))
        elif lat is not None and state:
            out["added"] += 1; out["added_names"].append(f"{name} ({city}, {state})")
            if apply:
                import re
                fid = "imp-" + re.sub(r"[^a-z0-9]+", "-", f"{state}-{name}".lower()).strip("-")[:60]
                d.execute("INSERT INTO hospitals (facility_id, name, city, state, htype, emergency, lat, lon, pos_source, trauma, trauma_date, trauma_source, services) "
                          "VALUES (%s,%s,%s,%s,'Added from a list',%s,%s,%s,'manual',%s,%s,%s,%s) ON CONFLICT (facility_id) DO UPDATE SET trauma=EXCLUDED.trauma, "
                          "trauma_date=EXCLUDED.trauma_date, trauma_source=EXCLUDED.trauma_source, services=CASE WHEN EXCLUDED.services <> '' THEN EXCLUDED.services ELSE hospitals.services END",
                          (fid, name, city, state, bool(level), lat, lon, level, r.get("designated", "")[:40], r.get("source", "")[:200], ",".join(svc)))
        else:
            out["unreadable"].append(f"line {n}: {name} matches no hospital and has no position to add it with")
    if apply:
        d.commit()
        link_heliports()
        set_setting("hospitals_last_trauma", datetime.now(timezone.utc).isoformat())
    else:
        d.rollback()
    return out


def trauma_template():
    return "name,city,state,level,designated,lat,lon,source\nExample Regional Medical Center,Little Rock,AR,II,March 2025,34.7500,-92.2800,Arkansas Department of Health\n"


def locate_osm(h):
    """Ask OpenStreetMap's place finder for one hospital (a person pressed the button, one question, answer kept). Accepts only an answer within 30 km of
    the guess we already have. Returns (lat, lon) or None."""
    import time
    import urllib.parse
    q = f"{h['name']}, {h['address']}, {h['city']}, {h['state']} {h['zip']}".replace(" ,", ",")
    url = cfg("nominatim_url") + "?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "us"})
    try:
        hits = json.loads(_get(url, 30))
    except Exception:
        hits = []
    if not hits and h["address"]:   # the building may be known by name only
        time.sleep(1.1)
        url = cfg("nominatim_url") + "?" + urllib.parse.urlencode({"q": f"{h['name']}, {h['city']}, {h['state']}", "format": "jsonv2", "limit": 1, "countrycodes": "us"})
        try:
            hits = json.loads(_get(url, 30))
        except Exception:
            hits = []
    if not hits:
        return None
    lat, lon = float(hits[0]["lat"]), float(hits[0]["lon"])
    if h["lat"] is not None and _km(lat, lon, h["lat"], h["lon"]) > 30:
        return None
    return lat, lon


def locate_approximate(limit=12, only=None):
    """Locate hospitals whose position is a guess. One question a second. Returns (done, failed, left)."""
    import time
    d = get_db()
    rows = d.execute("SELECT * FROM hospitals WHERE pos_source IN ('zip', '') " + ("AND facility_id=%s " if only else "") + "ORDER BY facility_id", (only,) if only else ()).fetchall()
    done = failed = 0
    for h in rows[:limit]:
        pos = locate_osm(dict(h))
        if pos:
            d.execute("UPDATE hospitals SET lat=%s, lon=%s, pos_source='osm' WHERE facility_id=%s", (pos[0], pos[1], h["facility_id"]))
            done += 1
        else:
            d.execute("UPDATE hospitals SET pos_source='zip' WHERE facility_id=%s", (h["facility_id"],))
            failed += 1
        d.commit()
        time.sleep(1.1)
    return done, failed, max(0, len(rows) - limit)


def link_heliports():
    """Link each hospital that has no heliport yet to a heliport within the distance set in Settings (nearest wins). Hand-set links are never changed."""
    d = get_db()
    km = float(cfg("hospital_heliport_km"))
    for h in d.execute("SELECT facility_id, lat, lon FROM hospitals WHERE heliport_ident = '' AND NOT heliport_set AND lat IS NOT NULL").fetchall():
        a = d.execute("SELECT ident, 6371 * 2 * asin(sqrt(least(1.0, power(sin(radians(lat - %(la)s) / 2), 2) + cos(radians(%(la)s)) * cos(radians(lat)) * power(sin(radians(lon - %(lo)s) / 2), 2)))) AS km "
                      "FROM airports WHERE type = 'heliport' AND lat BETWEEN %(la)s - 0.05 AND %(la)s + 0.05 AND lon BETWEEN %(lo)s - 0.06 AND %(lo)s + 0.06 ORDER BY km LIMIT 1",
                      {"la": h["lat"], "lo": h["lon"]}).fetchone()
        if a and a["km"] <= km:
            d.execute("UPDATE hospitals SET heliport_ident=%s WHERE facility_id=%s", (a["ident"], h["facility_id"]))
    d.commit()


def counts():
    r = get_db().execute("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE emergency) AS emergency, COUNT(*) FILTER (WHERE trauma <> '') AS trauma, "
                         "COUNT(*) FILTER (WHERE heliport_ident <> '') AS heliports, COUNT(*) FILTER (WHERE lat IS NULL) AS unplaced, COUNT(*) FILTER (WHERE pos_source IN ('zip', '')) AS approx FROM hospitals").fetchone()
    return dict(r)


ISSUES = [("address", "Address missing or incomplete"), ("position", "Position approximate or not confirmed"), ("phone", "No phone number"),
          ("no_heliport", "Trauma centre with no heliport linked"), ("far_heliport", "Heliport far from the hospital"), ("bad_heliport", "Heliport not found in the airport data")]


def _hosp_query():
    """Every hospital with its distance to the linked heliport and the list of data issues it has (one definition for the flag, the filter and the counts)."""
    warn = float(cfg("hospital_heliport_warn_km"))
    return (
        "(SELECT s.*, ARRAY_REMOVE(ARRAY["
        "CASE WHEN s.address = '' OR s.city = '' OR s.state = '' OR s.zip !~ '^[0-9]{5}' THEN 'address' END, "
        "CASE WHEN s.lat IS NULL OR s.pos_source IN ('zip', '') OR s.pos_check IN ('single', 'conflict', 'none') THEN 'position' END, "
        "CASE WHEN s.phone = '' THEN 'phone' END, "
        "CASE WHEN s.trauma <> '' AND s.heliport_ident = '' THEN 'no_heliport' END, "
        "CASE WHEN s.heliport_ident <> '' AND NOT s.heli_found THEN 'bad_heliport' END, "
        f"CASE WHEN s.heli_km > {warn!r} THEN 'far_heliport' END], NULL) AS issues FROM "
        "(SELECT h.*, a.id IS NOT NULL AS heli_found, CASE WHEN a.id IS NOT NULL AND h.lat IS NOT NULL THEN "
        "6371 * 2 * asin(sqrt(least(1.0, power(sin(radians(a.lat - h.lat) / 2), 2) + cos(radians(h.lat)) * cos(radians(a.lat)) * power(sin(radians(a.lon - h.lon) / 2), 2)))) END AS heli_km "
        "FROM hospitals h LEFT JOIN LATERAL (SELECT id, lat, lon FROM airports WHERE h.heliport_ident <> '' AND lower(ident) = lower(h.heliport_ident) ORDER BY (type = 'heliport') DESC LIMIT 1) a ON TRUE) s) hq")


def issue_counts():
    rows = get_db().execute(f"SELECT i AS key, COUNT(*) AS n FROM {_hosp_query()}, unnest(issues) AS i GROUP BY i").fetchall()
    out = {k: 0 for k, _ in ISSUES}
    out.update({r["key"]: r["n"] for r in rows})
    out["any"] = get_db().execute(f"SELECT COUNT(*) AS n FROM {_hosp_query()} WHERE cardinality(issues) > 0").fetchone()["n"]
    return out


def heliport_distance_km(fid):
    r = get_db().execute(f"SELECT heli_km FROM {_hosp_query()} WHERE facility_id=%s", (fid,)).fetchone()
    return r["heli_km"] if r else None


def check_counts():
    r = get_db().execute("SELECT COUNT(*) FILTER (WHERE pos_check='agreed') AS agreed, COUNT(*) FILTER (WHERE pos_check='manual' OR pos_source='manual') AS manual, "
                         "COUNT(*) FILTER (WHERE pos_check IN ('single', 'conflict', 'none')) AS review, COUNT(*) FILTER (WHERE pos_check='conflict') AS conflict, "
                         "COUNT(*) FILTER (WHERE pos_check='single') AS single, COUNT(*) FILTER (WHERE pos_check='none') AS none, COUNT(*) FILTER (WHERE pos_check='' AND pos_source<>'manual') AS unchecked FROM hospitals").fetchone()
    return dict(r)


def status():
    return {"issue_counts": issue_counts(), "issues": ISSUES, "checks": check_counts(), "check_last": get_setting("hospverify_last_import"), "check_result": get_setting("hospverify_last_result"), "check_running": bool(get_setting("hospverify_running")),
            "check_progress": get_setting("hospverify_progress"), "counts": counts(), "last": get_setting("hospitals_last_import"), "summary": get_setting("hospitals_last_counts"), "last_trauma": get_setting("hospitals_last_trauma")}


def listing(q="", state="", trauma="", emergency="", limit=60, offset=0, sort="name", direction="asc", service="", check="", issue=""):
    where, args = [], []
    if issue == "any":
        where.append("cardinality(issues) > 0")
    elif issue in dict(ISSUES):
        where.append("%s = ANY(issues)"); args.append(issue)
    if check == "review":
        where.append("pos_check IN ('single', 'conflict', 'none')")
    elif check in ("agreed", "single", "conflict", "none"):
        where.append("pos_check=%s"); args.append(check)
    if service in dict(SERVICES):
        where.append("(%s = ANY(string_to_array(services, ',')))"); args.append(service)
    if q:
        where.append("(name ILIKE %s OR city ILIKE %s OR county ILIKE %s OR zip LIKE %s)"); args += [f"%{q}%", f"%{q}%", f"%{q}%", f"{q}%"]
    if state:
        where.append("state=%s"); args.append(state.upper())
    if trauma == "any":
        where.append("trauma <> ''")
    elif trauma in ("I", "II", "III", "IV", "V"):
        where.append("trauma=%s"); args.append(trauma)
    if emergency == "1":
        where.append("emergency")
    w = (" WHERE " + " AND ".join(where)) if where else ""
    col = {"name": "name", "city": "city", "state": "state", "type": "htype", "rating": "rating",
           "trauma": "CASE trauma WHEN 'I' THEN 1 WHEN 'II' THEN 2 WHEN 'III' THEN 3 WHEN 'IV' THEN 4 WHEN 'V' THEN 5 END"}.get(sort, "name")   # levels in order, no level last
    d = get_db()
    total = d.execute(f"SELECT COUNT(*) AS n FROM {_hosp_query()}{w}", args).fetchone()["n"]
    rows = d.execute(f"SELECT * FROM {_hosp_query()}{w} ORDER BY {col} {'DESC' if direction == 'desc' else 'ASC'} NULLS LAST, name LIMIT %s OFFSET %s", args + [limit, offset]).fetchall()
    return rows, total


def get(fid):
    return get_db().execute("SELECT * FROM hospitals WHERE facility_id=%s", (fid,)).fetchone()


def save_edit(fid, trauma, services, heliport, notes, lat=None, lon=None):
    """An administrator's entries; the import leaves them alone from then on."""
    if trauma not in dict(TRAUMA):
        return "Choose a valid trauma level."
    keep = [s for s, _ in SERVICES if s in services]
    d = get_db()
    if heliport:
        if d.execute("SELECT 1 FROM airports WHERE lower(ident)=lower(%s) AND type='heliport'", (heliport,)).fetchone() is None:
            return f"{heliport} is not a heliport in the airport data."
        heliport = d.execute("SELECT ident FROM airports WHERE lower(ident)=lower(%s)", (heliport,)).fetchone()["ident"]
    was = d.execute("SELECT trauma FROM hospitals WHERE facility_id=%s", (fid,)).fetchone()
    d.execute("UPDATE hospitals SET trauma=%s, services=%s, heliport_ident=%s, heliport_set=TRUE, notes=%s WHERE facility_id=%s", (trauma, ",".join(keep), heliport, notes[:500], fid))
    if was is not None and was["trauma"] != trauma:   # a level an administrator chose is marked, and the state's list leaves it alone
        d.execute("UPDATE hospitals SET trauma_source='Administrator' WHERE facility_id=%s", (fid,))
    if lat is not None and lon is not None:
        d.execute("UPDATE hospitals SET lat=%s, lon=%s, pos_source='manual', pos_check='manual', pos_checked=now() WHERE facility_id=%s", (lat, lon, fid))   # edited by a person: it no longer needs review
    d.commit()
    return ""


def map_points(south, west, north, east, limit=600):
    rows = get_db().execute("SELECT * FROM hospitals WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s ORDER BY (trauma <> '') DESC, emergency DESC, facility_id LIMIT %s",
                            (south, north, west, east, limit)).fetchall()
    seen, out = {}, []
    for r in rows:
        lat, lon = r["lat"], r["lon"]
        if r["pos_source"] == "zip":   # several hospitals guessed at the same middle of a ZIP area or town: a small ring so every one can be clicked
            k = (round(lat, 4), round(lon, 4))
            n = seen.get(k, 0); seen[k] = n + 1
            if n:
                import math
                ang = n * 2.399963; rad = 0.0016 * math.sqrt(n)
                lat, lon = lat + rad * math.cos(ang), lon + rad * math.sin(ang) / max(0.2, math.cos(math.radians(lat)))
        out.append({"id": r["facility_id"], "name": r["name"], "city": r["city"], "state": r["state"], "lat": lat, "lon": lon, "trauma": r["trauma"], "emergency": r["emergency"],
                    "heliport": r["heliport_ident"], "approx": r["pos_source"] == "zip"})
    return out


@bp.route("/hospitals/")
def hospitals_page():
    q, state, trauma, emergency = request.args.get("q", "").strip()[:40], request.args.get("state", ""), request.args.get("trauma", ""), request.args.get("emergency", "")
    service = request.args.get("service", "")
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per = cfg_int("page_size")
    sort = request.args.get("sort", "name"); direction = "desc" if request.args.get("dir") == "desc" else "asc"
    rows, total = listing(q, state, trauma, emergency, per, (page - 1) * per, sort, direction, service)
    states = [r["state"] for r in get_db().execute("SELECT DISTINCT state FROM hospitals ORDER BY state").fetchall()]
    args = {k: v for k, v in request.args.items() if k != "page" and v}
    return render_template("hospitals.html", rows=rows, q=q, state=state, trauma=trauma, emergency=emergency, states=states, trauma_levels=TRAUMA, service=service, services=SERVICES,
                           sort=sort, direction=direction, loaded=counts()["total"] > 0,
                           pager={"page": page, "pages": max(1, -(-total // per)), "total": total, "args": args})


@bp.route("/hospitals/<fid>")
def hospital_page(fid):
    h = get(fid)
    if h is None:
        abort(404)
    heli = None
    if h["heliport_ident"]:
        heli = get_db().execute("SELECT ident, name, lat, lon FROM airports WHERE ident=%s", (h["heliport_ident"],)).fetchone()
    from . import trauma as tr
    from . import counties as co
    ct = tr.county_trauma((h["county"] or "").title(), h["state"]) if h["county"] else None
    cslug = next((c["slug"] for c in co.shapes() if c["state"] == h["state"] and c["base"].lower() == (h["county"] or "").lower()), "") if h["county"] else ""
    return render_template("hospital.html", h=h, heli=heli, services=dict(SERVICES), trauma=dict(TRAUMA), osrm_on=cfg("osrm_enabled") == "1" and h["lat"] is not None and h["pos_source"] != "zip",
                           media=tr.media_for("hospital", h["facility_id"]), ct=ct, county_slug=cslug, in_trauma_map=bool(h["trauma_scope"]), border_min=h["border_min"], border_minutes=cfg("trauma_border_minutes"))


_rings_lock = __import__("threading").Lock()


@bp.route("/api/hospitals/<fid>/rings.json")
def api_rings(fid):
    """Drive-time rings around one hospital (our own estimate from the routing server), worked out once and kept. One computation runs at a time."""
    from . import driveranges
    if get(fid) is None:
        abort(404)
    if cfg("osrm_enabled") != "1":
        return jsonify({"enabled": False, "rings": {}, "note": ""})
    have, _when = driveranges.stored(fid)
    want = driveranges.minutes()
    if have and set(want) <= set(have):   # kept rings are served as they are (the scheduled job and the cache days keep them fresh); a hospital without them, or without one of the wanted times (the times were changed), asks the routing server
        return jsonify({"enabled": True, "rings": {str(m): have[m] for m in want}, "note": ""})
    if not _rings_lock.acquire(blocking=False):
        return jsonify({"enabled": True, "busy": True, "rings": {}, "note": "The rings are being worked out; try again in a few seconds."})
    try:
        rings, note = driveranges.rings_for(fid)
    finally:
        _rings_lock.release()
    return jsonify({"enabled": True, "rings": {str(m): p for m, p in (rings or {}).items()}, "note": note})


@bp.route("/api/hospitals.json")
def api_hospitals():
    try:
        s, w, n, e = (float(request.args[k]) for k in ("south", "west", "north", "east"))
    except (KeyError, ValueError):
        abort(400)
    key = ("hospitals", round(s, 1), round(w, 1), round(n, 1), round(e, 1))
    resp = jsonify({"hospitals": cached(key, lambda: map_points(s, w, n, e))})
    resp.headers["Cache-Control"] = f"public, max-age={cfg_int('public_cache_seconds')}"
    return resp
