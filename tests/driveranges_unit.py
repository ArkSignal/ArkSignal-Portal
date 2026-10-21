"""In-container checks of driveranges.py with a made-up routing function (so no routing server is needed): rings at the right distances for a steady speed, a ring pulled in where a direction has
no road, a stray far point past a gap ignored, smoothing, the ring times setting, and the keeping of results.
docker cp this file to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/driveranges_unit.py"""
import math
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app
from app import driveranges as dr
from app.settings import set_setting

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

LA, LO = 34.75, -92.32
def km_from(p):
    return math.hypot((p[1] - LO) * math.cos(math.radians(LA)) * 111.195, (p[0] - LA) * 111.195)
def radii(poly):
    return [km_from(p) for p in poly]

def steady(kmh):
    return lambda src, pts: [km_from(p) / kmh * 3600 for p in pts]

with app.app_context():
    from app.db import get_db
    d = get_db()
    set_setting("osrm_ring_minutes", "15,30,60")
    check("the ring times come from the setting, sorted and without nonsense", dr.minutes() == [15, 30, 60])
    set_setting("osrm_ring_minutes", "60, 15,x,15,999,30")
    check("a messy setting is cleaned", dr.minutes() == [15, 30, 60], dr.minutes())
    set_setting("osrm_ring_minutes", "15,30,60")

    rings = dr.compute(LA, LO, [15, 30, 60], table=steady(60))
    check("one ring per time, each a closed outline of 48 points", sorted(rings) == [15, 30, 60] and all(len(p) == dr.BEARINGS for p in rings.values()))
    for m in (15, 30, 60):
        r = radii(rings[m])
        check(f"at a steady 60 km/h the {m} minute ring is about {m} km out in every direction", all(abs(x - m) < 3.5 for x in r), (round(min(r), 1), round(max(r), 1)))
    check("the rings nest: 60 outside 30 outside 15 at every bearing", all(a < b < c for a, b, c in zip(radii(rings[15]), radii(rings[30]), radii(rings[60]))))

    # no road to the east (bearings 60 to 120 degrees): nothing reachable past 6 km there
    def walled(src, pts):
        out = []
        for p in pts:
            km = km_from(p)
            b = (math.degrees(math.atan2((p[1] - LO) * math.cos(math.radians(LA)), p[0] - LA)) + 360) % 360
            out.append(None if (60 <= b <= 120 and km > 6) else km / 60 * 3600)
        return out
    rw = radii(dr.compute(LA, LO, [30], table=walled)[30])
    east = [rw[i] for i in range(dr.BEARINGS) if 75 <= i * 7.5 <= 105]
    west = [rw[i] for i in range(dr.BEARINGS) if 255 <= i * 7.5 <= 285]
    check("the ring is pulled in where there is no road, and unchanged elsewhere", max(east) < 12 and min(west) > 25, (max(east), min(west)))

    # a stray far point past a gap does not stretch the ring: unreachable from 20 km to 100 km, then one reachable point at 104 km
    def stray(src, pts):
        out = []
        for p in pts:
            km = km_from(p)
            out.append(km / 60 * 3600 if (km < 20 or abs(km - 104) < 2) else None)
        return out
    rs = radii(dr.compute(LA, LO, [60], table=stray)[60])
    check("a lone reachable point beyond a gap is ignored (three misses in a row end the ray)", max(rs) < 30, round(max(rs), 1))

    # the keeping of results
    fid = d.execute("SELECT facility_id FROM hospitals WHERE lat IS NOT NULL LIMIT 1").fetchone()
    if fid:
        fid = fid["facility_id"]
        d.execute("DELETE FROM hospital_rings WHERE facility_id=%s", (fid,)); d.commit()
        real = dr._table
        calls = []
        def fake(src, pts):
            calls.append(1)
            return [km_from_h(src, p) / 50 * 3600 for p in pts]
        def km_from_h(src, p):
            return math.hypot((p[1] - src[1]) * math.cos(math.radians(src[0])) * 111.195, (p[0] - src[0]) * 111.195)
        dr._table = fake
        try:
            r1, note1 = dr.rings_for(fid)
            r2, note2 = dr.rings_for(fid)
            have, when = dr.stored(fid)
            check("rings are worked out once and kept", r1 and len(r1) == 3 and sorted(have) == [15, 30, 60] and when is not None and len(calls) == 1 and not note1 and not note2, (len(calls), note1))
            dr.rings_for(fid, force=True)
            check("a forced run works them out again", len(calls) == 2)
            def broken(src, pts):
                raise OSError("down")
            dr._table = broken
            r3, note3 = dr.rings_for(fid, force=True)
            check("with the routing server down the kept rings are still given, with a plain note", r3 and len(r3) == 3 and "did not answer" in note3, note3)
            d.execute("DELETE FROM hospital_rings WHERE facility_id=%s", (fid,)); d.commit()
            r4, note4 = dr.rings_for(fid)
            check("and with nothing kept there are no rings and the same note", r4 is None and "did not answer" in note4, note4)
        finally:
            dr._table = real
            d.execute("DELETE FROM hospital_rings WHERE facility_id=%s", (fid,)); d.commit()
    set_setting("osrm_enabled", "0")
    check("switched off, no rings are given", dr.rings_for("anything")[0] is None)
    d.execute("DELETE FROM settings WHERE key IN ('osrm_enabled', 'osrm_ring_minutes')"); d.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
