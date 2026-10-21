"""Address lookup for Admin > Hospitals: one address, several finders, a list of candidate positions.

Finders (terms in DATA_SOURCES_TERMS.md): the US Census one-line locator (public domain), this network's own Photon (the hub's `photon` service,
OpenStreetMap data, ODbL) and OpenStreetMap Nominatim (only because an administrator pressed Look Up: at most one question a second, a clear
User-Agent, every answer kept). Every answer is kept in `geocode_cache` for Settings > Address Lookup Cache (days), so asking again costs nothing.
A finder that cannot answer is reported by name and never stops the others."""
import json
import time
import urllib.parse
import urllib.request

from .db import get_db
from .settings import cfg, cfg_int, set_setting

FINDERS = [("census", "US Census"), ("photon", "Photon (our own)"), ("osm", "OpenStreetMap Nominatim")]


def _fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2_000_000).decode("utf-8", "replace"))


def _census(q, bias):
    j = _fetch(cfg("census_oneline_url") + "?" + urllib.parse.urlencode({"address": q, "benchmark": "Public_AR_Current", "format": "json"}))
    return [{"label": m.get("matchedAddress", q), "lat": m["coordinates"]["y"], "lon": m["coordinates"]["x"]} for m in j.get("result", {}).get("addressMatches", [])[:5]]


def _photon(q, bias):
    args = {"q": q, "limit": 5, "lang": "en"}
    if bias:
        args.update(lat=bias[0], lon=bias[1])
    j = _fetch(cfg("photon_url").rstrip("/") + "/api?" + urllib.parse.urlencode(args))
    out = []
    for f in j.get("features", []):
        p, (lon, lat) = f.get("properties", {}), f["geometry"]["coordinates"][:2]
        street = " ".join(x for x in (p.get("housenumber"), p.get("street")) if x)
        label = ", ".join(x for x in (p.get("name"), street, p.get("city") or p.get("county"), p.get("state"), p.get("postcode")) if x)
        out.append({"label": label or q, "lat": lat, "lon": lon})
    return out


def _osm(q, bias):
    j = _fetch(cfg("nominatim_url") + "?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 5, "countrycodes": "us"}))
    time.sleep(1.1)   # the usage policy allows one question a second
    return [{"label": h.get("display_name", q), "lat": float(h["lat"]), "lon": float(h["lon"])} for h in j]


_RUN = {"census": _census, "photon": _photon, "osm": _osm}


def lookup(q, bias=None, finders=None):
    """Ask each finder (all of them by default) about one address. Returns (candidates, errors): candidates carry the finder's name, errors say which finder failed and why."""
    d = get_db()
    q = " ".join((q or "").split())[:300]
    cands, errors = [], {}
    if not q:
        return cands, {"all": "Type an address first."}
    for key, label in FINDERS:
        if finders and key not in finders:
            continue
        row = d.execute("SELECT answer FROM geocode_cache WHERE finder=%s AND query=%s AND fetched > now() - make_interval(days => %s)", (key, q.lower(), cfg_int("lookup_cache_days"))).fetchone()
        try:
            if row:
                hits = json.loads(row["answer"])
            else:
                hits = _RUN[key](q, bias)
                d.execute("INSERT INTO geocode_cache (finder, query, answer, fetched) VALUES (%s,%s,%s,now()) ON CONFLICT (finder, query) DO UPDATE SET answer=EXCLUDED.answer, fetched=now()",
                          (key, q.lower(), json.dumps(hits)))
                d.commit()
        except Exception as e:
            d.rollback()
            errors[key] = ("not ready yet (its index may still be downloading)" if key == "photon" else "did not answer") + f" ({type(e).__name__})"
            continue
        for h in hits:
            cands.append({"finder": key, "finder_name": label, "label": h["label"], "lat": round(float(h["lat"]), 6), "lon": round(float(h["lon"]), 6)})
    return cands, errors


def hospital_address(h):
    """The CMS address as one line, to pre-fill the box."""
    return ", ".join(x for x in (h["address"], h["city"], f"{h['state']} {h['zip']}".strip()) if x)


def _near_enough(h, c, km=40):
    """A candidate far from the position we already have (a different town with the same street name) is not believed."""
    from .hospitals import _km
    return h["lat"] is None or _km(h["lat"], h["lon"], c["lat"], c["lon"]) <= km


def check_hospital(h):
    """Ask the Census and our own Photon about one hospital's address. Returns (status, position or None):
    'agreed' (both place it within the setting's distance: the position is confirmed), 'single' (only one finder knew it), 'conflict' (they disagree), 'none' (neither knew it),
    'error' (neither could be asked). Nothing is changed here."""
    from .hospitals import _km
    q = hospital_address(h)
    bias = (h["lat"], h["lon"]) if h["lat"] is not None else None
    cands, errors = lookup(q, bias, ["census", "photon"])
    if not any(c["finder"] == "photon" for c in cands) and "photon" not in errors:
        more, _ = lookup(f"{h['name']}, {h['city']}, {h['state']}", bias, ["photon"])   # the building may be known by name
        cands += more
    cands = [c for c in cands if _near_enough(h, c)]
    best = {}
    for c in cands:
        best.setdefault(c["finder"], c)
    if not best:
        return ("error" if len(errors) >= 2 else "none"), None
    if len(best) == 1:
        return "single", None
    a, b = best["census"], best["photon"]
    if _km(a["lat"], a["lon"], b["lat"], b["lon"]) * 1000 <= cfg_int("verify_agree_meters"):
        return "agreed", (b["lat"], b["lon"])   # Photon places the building itself; the Census interpolates along the street
    return "conflict", None


def apply_check(d, h):
    """Check one hospital and store the outcome (an agreed position becomes 'verified'; a position set by hand is left alone). Returns the status."""
    if h["pos_source"] == "manual":
        d.execute("UPDATE hospitals SET pos_check='manual', pos_checked=now() WHERE facility_id=%s", (h["facility_id"],))
        d.commit()
        return "manual"
    status, pos = check_hospital(h)
    if status == "agreed":
        d.execute("UPDATE hospitals SET lat=%s, lon=%s, pos_source='verified', pos_check='agreed', pos_checked=now() WHERE facility_id=%s", (pos[0], pos[1], h["facility_id"]))
    elif status != "error":
        d.execute("UPDATE hospitals SET pos_check=%s, pos_checked=now() WHERE facility_id=%s", (status, h["facility_id"]))
    d.commit()
    return status


def check_all():
    """Check every hospital (not those with a position set by hand). Agreed ones are stored as verified; the rest are marked for review. Returns a summary sentence."""
    d = get_db()
    rows = d.execute("SELECT * FROM hospitals WHERE pos_source<>'manual' ORDER BY facility_id").fetchall()
    d.execute("UPDATE hospitals SET pos_check='manual', pos_checked=now() WHERE pos_source='manual'")
    d.commit()
    tally = {"agreed": 0, "single": 0, "conflict": 0, "none": 0, "error": 0}
    in_a_row = 0
    for n, h in enumerate(rows, 1):
        status = apply_check(d, dict(h))
        tally[status] += 1
        in_a_row = in_a_row + 1 if status == "error" else 0
        if n % 20 == 0:
            set_setting("hospverify_progress", f"{n} of {len(rows)} checked")
        if in_a_row >= 15:
            set_setting("hospverify_progress", "")
            raise RuntimeError("Neither the Census nor Photon answered 15 times in a row; stopped. Check that Photon is running (Settings > Photon Address Search Address) and try again.")
    set_setting("hospverify_progress", "")
    from .hospitals import link_heliports
    link_heliports()
    return (f"{tally['agreed']} confirmed, {tally['single'] + tally['conflict'] + tally['none']} need review "
            f"({tally['conflict']} the finders disagree, {tally['single']} only one finder knew it, {tally['none']} neither), {tally['error']} could not be asked")
