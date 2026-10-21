"""Drive times from any place on a map: click a point (or search a city or an address) and the emergency and trauma hospitals nearest by straight line are timed by road through the
network's own routing server (OSRM, Settings > Routing), then listed by minutes. OUR OWN ESTIMATE from typical road speeds, no traffic or lights, not an ambulance's real route.

Order: by drive time; hospitals whose times are within Settings > "Drive Times Counted As The Same" of the quickest of their group are put in order of trauma level (I first), then of the number of
services, then of the minute. The rings around the point come from `driveranges.compute`. Answers are kept in memory by rounded position, and each visitor (by address) may ask only so often."""
import threading
import time
import urllib.error

from flask import Blueprint, jsonify, request

from . import driveranges
from .cache import cached
from .clientip import client_ip
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("drivetimes", __name__)
TRAUMA_RANK = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
_hits = {}
_hits_lock = threading.Lock()
_rings_lock = threading.Lock()


def _hospitals():
    return cached(("drive_hospitals",), lambda: [dict(r) for r in get_db().execute(
        "SELECT facility_id, name, city, state, lat, lon, trauma, emergency, services, heliport_ident, pos_source FROM hospitals WHERE lat IS NOT NULL AND (emergency OR trauma <> '')").fetchall()], seconds=600)


def allowed(ip, now=None):
    """True while this visitor is under the per-minute limit (Settings > Routing); a use is counted."""
    now = now or time.time()
    limit = cfg_int("drivetimes_per_minute")
    with _hits_lock:
        if len(_hits) > 5000:
            for k in [k for k, v in _hits.items() if not v or v[-1] < now - 60]:
                _hits.pop(k, None)
        v = [t for t in _hits.get(ip, []) if t > now - 60]
        if len(v) >= limit:
            _hits[ip] = v
            return False
        v.append(now)
        _hits[ip] = v
        return True


def _nservices(v):
    return len(v) if isinstance(v, (list, tuple)) else len([x for x in (v or "").split(",") if x])


def rank(rows, tie_minutes):
    """rows: dicts with `seconds` (None = no road route), `trauma` and `services` (comma text). Returns them in drive-time order with near-ties ordered by trauma level, then services."""
    ok = sorted([r for r in rows if r.get("seconds") is not None], key=lambda r: r["seconds"])
    out, i = [], 0
    tie = tie_minutes * 60
    while i < len(ok):
        j = i
        while j < len(ok) and ok[j]["seconds"] <= ok[i]["seconds"] + tie:
            j += 1
        group = ok[i:j]
        group.sort(key=lambda r: (TRAUMA_RANK.get(r.get("trauma") or "", 9), -_nservices(r.get("services")), r["seconds"]))
        out += group
        i = j
    return out + [r for r in rows if r.get("seconds") is None]


def times(lat, lon, table=None):
    """The hospitals timed from a point, ranked. Returns a list of dicts (id, name, city, state, trauma, emergency, services, minutes, km, lat, lon, approx)."""
    from .hospitals import _km
    near = sorted(_hospitals(), key=lambda h: _km(lat, lon, h["lat"], h["lon"]))[:cfg_int("drivetimes_hospitals")]
    pts = [(h["lat"], h["lon"]) for h in near]
    both = [(s, None) for s in table((lat, lon), pts)] if table else driveranges.table_both((lat, lon), pts)   # (seconds, road meters); a made-up table gives seconds only
    rows = []
    for h, (s, road_m) in zip(near, both):
        rows.append({"id": h["facility_id"], "name": h["name"], "city": h["city"], "state": h["state"], "lat": h["lat"], "lon": h["lon"], "trauma": h["trauma"], "emergency": h["emergency"],
                     "services": [x for x in h["services"].split(",") if x], "heliport": h["heliport_ident"], "approx": h["pos_source"] == "zip", "seconds": s,
                     "minutes": None if s is None else round(s / 60.0, 1), "km": round(_km(lat, lon, h["lat"], h["lon"]), 1), "road_km": None if road_m is None else round(road_m / 1000.0, 1)})
    ranked = rank(rows, cfg_int("drivetimes_tie_minutes"))
    for r in ranked:
        r.pop("seconds", None)
    return ranked


def _arg_pos():
    try:
        lat, lon = float(request.args.get("lat", "")), float(request.args.get("lon", ""))
    except ValueError:
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


@bp.route("/api/drivetimes.json")
def api_drivetimes():
    """?lat&lon[&rings=1]: the ranked hospitals, and with rings=1 the drive-time rings around the point (slower: a few seconds, then kept)."""
    if cfg("osrm_enabled") != "1":
        return jsonify({"enabled": False, "hospitals": [], "rings": {}, "note": "Drive times are switched off in Settings."})
    pos = _arg_pos()
    if pos is None:
        return jsonify({"error": "lat and lon are needed"}), 400
    if not allowed(client_ip()):
        return jsonify({"error": "slow down", "note": "Too many questions just now; wait a moment."}), 429
    lat, lon = round(pos[0], 3), round(pos[1], 3)   # about 100 m: the same answer for clicks close together
    out = {"enabled": True, "lat": lat, "lon": lon, "hospitals": [], "rings": {}, "note": ""}
    try:
        out["hospitals"] = cached(("drivetimes", lat, lon), lambda: times(lat, lon), seconds=3600)
        if request.args.get("rings") == "1":
            key = ("driverings", lat, lon, tuple(driveranges.minutes()))
            if _rings_lock.acquire(timeout=20):
                try:
                    out["rings"] = {str(m): p for m, p in cached(key, lambda: driveranges.compute(lat, lon), seconds=86400).items()}
                finally:
                    _rings_lock.release()
            else:
                out["note"] = "The rings are busy being worked out for someone else; try again in a moment."
    except (urllib.error.URLError, RuntimeError, ValueError, OSError) as e:
        out["note"] = "The routing server did not answer, so drive times could not be worked out (" + type(e).__name__ + ")."
    return jsonify(out)


@bp.route("/api/geocode.json")
def api_geocode():
    """?q=: places (cities, ZIP areas) from the Census gazetteer first, then addresses from the Census locator and our own Photon (never the public Nominatim: that one is for an administrator's button)."""
    q = " ".join(request.args.get("q", "").split())[:200]
    if len(q) < 2:
        return jsonify({"results": []})
    if not allowed(client_ip()):
        return jsonify({"error": "slow down", "results": []}), 429
    from . import addrlookup, places
    out = [{"label": r["label"], "lat": r["lat"], "lon": r["lon"], "kind": r["kind"]} for r in places.search(q, limit=6) if r["kind"] != "county"]
    looks_like_address = any(c.isdigit() for c in q) or "," in q
    if looks_like_address or len(out) < 3:
        cands, _errs = addrlookup.lookup(q, (float(cfg("map_lat")), float(cfg("map_lon"))), ["census", "photon"])
        seen = set()
        for c in cands:
            k = (round(c["lat"], 3), round(c["lon"], 3))
            if k not in seen:
                seen.add(k)
                out.append({"label": c["label"], "lat": c["lat"], "lon": c["lon"], "kind": "address"})
    return jsonify({"results": out[:10]})
