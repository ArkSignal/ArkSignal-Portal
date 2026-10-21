"""Drive-time rings around a hospital: how far a vehicle gets in 15, 30, 60 and 90 minutes by road, drawn as outlines on the hospital's map.

OUR OWN ESTIMATE, labeled as one: OSRM (the network's own routing server, OpenStreetMap roads, Settings > Routing) gives the driving time from the hospital to points along rays in every direction; for each
ray the ring passes where the time reaches the limit, and neighboring rays are smoothed. It uses typical road speeds, not traffic, lights, weather or the real route an ambulance would take.
Results are kept per hospital (`hospital_rings`) and recomputed after the number of days in Settings."""
import json
import math
import urllib.error
import urllib.request

from .db import get_db
from .settings import cfg, cfg_int

BEARINGS = 48        # a ray every 7.5 degrees
STEP_KM = 5.0        # a sample every 5 km along each ray
MAX_KM = 200.0       # the farthest sample (90 minutes at highway speed is about 160 km)


def minutes():
    out = []
    for p in (cfg("osrm_ring_minutes") or "").split(","):
        try:
            v = int(p)
            if 1 <= v <= 240 and v not in out:
                out.append(v)
        except ValueError:
            pass
    return sorted(out) or [15, 30, 60, 90]


def _move(lat, lon, bearing_deg, km):
    r = 6371.0088
    b, d = math.radians(bearing_deg), km / r
    la1, lo1 = math.radians(lat), math.radians(lon)
    la2 = math.asin(math.sin(la1) * math.cos(d) + math.cos(la1) * math.sin(d) * math.cos(b))
    lo2 = lo1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(la1), math.cos(d) - math.sin(la1) * math.sin(la2))
    return math.degrees(la2), (math.degrees(lo2) + 540) % 360 - 180


def _table(src, pts):
    """Driving seconds from src to each point (None where there is no road route), through OSRM, in batches."""
    out = []
    base = cfg("osrm_url").rstrip("/")
    for i in range(0, len(pts), 400):
        chunk = pts[i:i + 400]
        coords = ";".join(f"{lo:.5f},{la:.5f}" for la, lo in [src] + chunk)
        url = f"{base}/table/v1/driving/{coords}?sources=0&annotations=duration"
        req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent")})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read(20_000_000).decode("utf-8"))
        if d.get("code") != "Ok":
            raise RuntimeError("OSRM said " + str(d.get("code")))
        out += d["durations"][0][1:]
    return out


def table_both(src, pts):
    """[(seconds, meters)] by road from src to each point (None where there is no road route), through OSRM, in batches."""
    out = []
    base = cfg("osrm_url").rstrip("/")
    for i in range(0, len(pts), 400):
        chunk = pts[i:i + 400]
        coords = ";".join(f"{lo:.5f},{la:.5f}" for la, lo in [src] + chunk)
        req = urllib.request.Request(f"{base}/table/v1/driving/{coords}?sources=0&annotations=duration,distance", headers={"User-Agent": cfg("lookup_user_agent")})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read(20_000_000).decode("utf-8"))
        if d.get("code") != "Ok":
            raise RuntimeError("OSRM said " + str(d.get("code")))
        out += list(zip(d["durations"][0][1:], d["distances"][0][1:]))
    return out


def compute(lat, lon, mins=None, table=None):
    """{minutes: [[lat, lon], ...]} rings for a position. `table` is the routing function (the tests give a made-up one)."""
    mins = mins or minutes()
    table = table or _table
    ks = [k for k in range(1, int(MAX_KM / STEP_KM) + 1)]
    pts = []
    for bi in range(BEARINGS):
        b = bi * 360.0 / BEARINGS
        pts += [_move(lat, lon, b, k * STEP_KM) for k in ks]
    dur = table((lat, lon), pts)
    rings = {}
    for m in mins:
        limit = m * 60.0
        radii = []
        for bi in range(BEARINGS):
            row = dur[bi * len(ks):(bi + 1) * len(ks)]
            reach = 0.0   # the last distance, along the ray, still inside the limit (a stray far point past a gap is ignored: three misses in a row end the ray)
            miss = 0
            prev_k, prev_d = 0, 0.0
            for k, d in zip(ks, row):
                if d is not None and d <= limit:
                    reach, miss = k * STEP_KM, 0
                    prev_k, prev_d = k, d
                else:
                    miss += 1
                    if d is not None and prev_k and d > limit and miss == 1 and k * STEP_KM - reach <= STEP_KM * 1.01:
                        frac = (limit - prev_d) / max(1.0, d - prev_d)   # between the last point inside and the first outside
                        reach = prev_k * STEP_KM + frac * STEP_KM
                    if miss >= 3:
                        break
            radii.append(max(reach, STEP_KM / 2))
        sm = [sorted([radii[(i - 1) % BEARINGS], radii[i], radii[(i + 1) % BEARINGS]])[1] for i in range(BEARINGS)]   # median of three: no spikes
        sm = [(sm[i] * 2 + sm[(i - 1) % BEARINGS] + sm[(i + 1) % BEARINGS]) / 4 for i in range(BEARINGS)]
        rings[m] = [[round(x, 4) for x in _move(lat, lon, bi * 360.0 / BEARINGS, sm[bi])] for bi in range(BEARINGS)]
    return rings


def stored(fid):
    rows = get_db().execute("SELECT minutes, poly, computed FROM hospital_rings WHERE facility_id=%s ORDER BY minutes", (fid,)).fetchall()
    return {r["minutes"]: json.loads(r["poly"]) for r in rows}, (max(r["computed"] for r in rows) if rows else None)


def rings_for(fid, force=False):
    """(rings or None, note). Uses the kept rings while they are fresh; otherwise asks OSRM (a few seconds) and keeps the answer."""
    from datetime import datetime, timedelta, timezone
    if cfg("osrm_enabled") != "1":
        return None, "Drive-time rings are switched off in Settings."
    d = get_db()
    h = d.execute("SELECT lat, lon FROM hospitals WHERE facility_id=%s", (fid,)).fetchone()
    if h is None or h["lat"] is None:
        return None, "This hospital has no position."
    have, when = stored(fid)
    want = minutes()
    if have and not force and set(want) <= set(have) and when and datetime.now(timezone.utc) - when < timedelta(days=cfg_int("osrm_ring_cache_days")):
        return {m: have[m] for m in want}, ""
    try:
        rings = compute(h["lat"], h["lon"], want)
    except (urllib.error.URLError, RuntimeError, ValueError, OSError) as e:
        return ({m: have[m] for m in want if m in have} or None), "The routing server did not answer, so these rings could not be worked out (" + type(e).__name__ + ")."
    for m, poly in rings.items():
        d.execute("INSERT INTO hospital_rings (facility_id, minutes, poly, computed) VALUES (%s,%s,%s,now()) ON CONFLICT (facility_id, minutes) DO UPDATE SET poly=EXCLUDED.poly, computed=now()", (fid, m, json.dumps(poly)))
    d.commit()
    return rings, ""


def compute_all():
    """The scheduled job: rings for every hospital with an emergency department or a trauma level in the states chosen in Settings. Returns a summary."""
    from . import states
    want = states.chosen(cfg("osrm_ring_states"))
    d = get_db()
    rows = d.execute("SELECT facility_id FROM hospitals WHERE lat IS NOT NULL AND (emergency OR trauma <> '') AND state = ANY(%s) ORDER BY facility_id", (want,)).fetchall()
    ok = failed = 0
    for r in rows:
        rings, note = rings_for(r["facility_id"], force=True)
        if rings and not note:
            ok += 1
        else:
            failed += 1
            if failed >= 5 and not ok:
                raise RuntimeError(note or "the routing server did not answer")
    return {"hospitals": len(rows), "ok": ok, "failed": failed}
