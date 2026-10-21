"""Per-station range: how far away each station hears aircraft, overall and by compass direction.

The hub's own range figures are useless for a hub without an antenna, but the portal knows two things readsb does not publish:
which stations received a position message from an aircraft (the receiver feed, see receivers.py) and where each station is
(its owner's private coordinates). Every few seconds the portal pairs the two: for each station and each aircraft it just
received a position message from, the distance and bearing from the station to the aircraft's reported position is a range
sample. The farthest sample per station, day and 10-degree sector is kept (36 sectors). Nothing but distances is ever shown
publicly; the station's coordinates stay private.
"""
import math
import threading

from markupsafe import Markup

from .db import get_db

SECTORS = 36            # 10 degrees each, sector 0 centred on north
EARTH_NM = 3440.065

_lock = threading.Lock()
_acc = {}               # (station uuid, day, sector) -> (nautical miles, altitude ft)


def distance_bearing(lat1, lon1, lat2, lon2):
    """(great-circle distance in nautical miles, initial bearing in degrees from north)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    nm = EARTH_NM * 2 * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return nm, (math.degrees(math.atan2(y, x)) + 360) % 360


def sector_of(bearing):
    return int(((bearing + 5) % 360) // 10)


def add_sample(uuid, day, bearing, nm, alt):
    key = (uuid, day, sector_of(bearing))
    with _lock:
        old = _acc.get(key)
        if old is None or nm > old[0]:
            _acc[key] = (nm, alt)


def take_samples():
    with _lock:
        out = list(_acc.items())
        _acc.clear()
    return out


def save(conn, samples):
    """Keep the farthest sample per station, day and sector."""
    for (uuid, day, sector), (nm, alt) in samples:
        conn.execute(
            "INSERT INTO feeder_range (feeder_uuid, day, sector, max_nm, max_alt) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (feeder_uuid, day, sector) DO UPDATE SET "
            "max_alt = CASE WHEN EXCLUDED.max_nm > feeder_range.max_nm THEN EXCLUDED.max_alt ELSE feeder_range.max_alt END, "
            "max_nm = GREATEST(feeder_range.max_nm, EXCLUDED.max_nm)", (uuid, day, sector, round(nm, 1), alt))
    conn.commit()


def prune(conn, keep_days):
    conn.execute("DELETE FROM feeder_range WHERE day < (now() AT TIME ZONE 'utc')::date - %s", (keep_days,))
    conn.commit()


# ---------------------------------------------------------------- reading

def summary(days=30):
    """{station uuid: farthest range in nautical miles over the last `days` days}."""
    rows = get_db().execute(
        "SELECT feeder_uuid, MAX(max_nm) AS nm FROM feeder_range WHERE day >= (now() AT TIME ZONE 'utc')::date - %s GROUP BY feeder_uuid",
        (days - 1,)).fetchall()
    return {r["feeder_uuid"]: float(r["nm"]) for r in rows}


def sectors_for(uuids, days=30):
    """{station uuid: [36 farthest distances in nautical miles, one per 10 degree sector]} over the last `days` days."""
    out = {u: [0.0] * SECTORS for u in uuids}
    if not out:
        return out
    for r in get_db().execute(
            "SELECT feeder_uuid, sector, MAX(max_nm) AS nm FROM feeder_range WHERE feeder_uuid = ANY(%s) AND day >= (now() AT TIME ZONE 'utc')::date - %s GROUP BY feeder_uuid, sector",
            (list(out), days - 1)).fetchall():
        out[r["feeder_uuid"]][r["sector"]] = float(r["nm"])
    return out


def today_by_station():
    rows = get_db().execute("SELECT feeder_uuid, MAX(max_nm) AS nm FROM feeder_range WHERE day = (now() AT TIME ZONE 'utc')::date GROUP BY feeder_uuid").fetchall()
    return {r["feeder_uuid"]: float(r["nm"]) for r in rows}


def station(uuid, days=30):
    """Range figures for one station: farthest in the period, farthest ever kept, farthest today, and the 36 sector maxima."""
    d = get_db()
    secs = [0.0] * SECTORS
    for r in d.execute("SELECT sector, MAX(max_nm) AS nm FROM feeder_range WHERE feeder_uuid=%s AND day >= (now() AT TIME ZONE 'utc')::date - %s GROUP BY sector",
                       (uuid, days - 1)).fetchall():
        secs[r["sector"]] = float(r["nm"])
    allr = d.execute("SELECT MAX(max_nm) AS nm FROM feeder_range WHERE feeder_uuid=%s", (uuid,)).fetchone()["nm"]
    today = d.execute("SELECT MAX(max_nm) AS nm FROM feeder_range WHERE feeder_uuid=%s AND day = (now() AT TIME ZONE 'utc')::date", (uuid,)).fetchone()["nm"]
    return {"period_max": max(secs), "all_max": float(allr or 0), "today_max": float(today or 0), "sectors": secs}


def polar_svg(sectors, size=360):
    """A radar-style chart of the farthest range in each direction (the numbers are nautical miles; the rings are labelled in the viewer's own units). Server-rendered inline SVG, no scripts."""
    from . import units as _units
    f, name = _units._SETS[_units.system()]["nm"]
    top = max(sectors) * f if sectors else 0
    if top <= 0:
        return Markup('<p class="dim">No range data yet. It builds up as the station hears aircraft with a known station location.</p>')
    step = 50 if top > 100 else 25 if top > 40 else 10
    maxr = math.ceil(top / step) * step
    c, rad = size / 2, size / 2 - 28
    parts = [f'<svg viewBox="0 0 {size} {size}" width="100%" style="max-width:{size}px" role="img" aria-label="Range by direction">']
    for n in range(step, maxr + 1, step):
        r = rad * n / maxr
        parts.append(f'<circle cx="{c}" cy="{c}" r="{r:.1f}" fill="none" stroke="var(--line)"/>'
                     f'<text x="{c + 3}" y="{c - r - 2:.1f}" fill="var(--dim)" font-size="10">{n} {name}</text>')
    for label, ang in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
        x, y = c + (rad + 14) * math.sin(math.radians(ang)), c - (rad + 14) * math.cos(math.radians(ang)) + 4
        parts.append(f'<line x1="{c}" y1="{c}" x2="{c + rad * math.sin(math.radians(ang)):.1f}" y2="{c - rad * math.cos(math.radians(ang)):.1f}" stroke="var(--line)"/>'
                     f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" fill="var(--dim)" font-size="11">{label}</text>')
    pts = []
    for i in range(SECTORS):
        r = rad * sectors[i] * f / maxr
        a = math.radians(i * 10)
        pts.append(f"{c + r * math.sin(a):.1f},{c - r * math.cos(a):.1f}")
    parts.append(f'<polygon points="{" ".join(pts)}" fill="rgba(62,230,168,.18)" stroke="var(--acc)" stroke-width="1.6" stroke-linejoin="round"/></svg>')
    return Markup("".join(parts))
