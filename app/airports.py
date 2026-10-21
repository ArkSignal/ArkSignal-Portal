"""Airport data from OurAirports (public domain): airports, radio frequencies and runways.

Source: https://davidmegginson.github.io/ourairports-data/ (airports.csv, airport-frequencies.csv, runways.csv).
Verified columns: airports id, ident, type, name, latitude_deg, longitude_deg, elevation_ft, iso_country,
iso_region, municipality, scheduled_service, icao_code, iata_code, gps_code, local_code, home_link,
wikipedia_link; frequencies airport_ref, type, description, frequency_mhz; runways airport_ref, length_ft,
width_ft, surface, lighted, closed, le_ident, he_ident, le/he latitude, longitude, heading.
"""
import csv
import io
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from .db import get_db
from .settings import cfg, get_setting, set_setting

KEEP_TYPES = {"large_airport", "medium_airport", "small_airport", "seaplane_base", "heliport"}
TYPE_LABELS = {"large_airport": "Large Airport", "medium_airport": "Medium Airport", "small_airport": "Small Airport",
               "seaplane_base": "Seaplane Base", "heliport": "Heliport"}


def _download(name, timeout=120):
    req = urllib.request.Request(cfg("airports_source_url") + name, headers={"User-Agent": "adsb-portal"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))


def _f(v):
    try:
        return float(v) if v not in ("", None) else None
    except ValueError:
        return None


def _i(v):
    f = _f(v)
    return int(f) if f is not None else None


def import_airports(countries, sources=None):
    """Replace the airport tables with the current data for the given ISO countries. Returns counts.
    `sources` (dict of the three row lists) is for tests; normally the files are downloaded."""
    wanted = {c.strip().upper() for c in countries if c.strip()}
    if not wanted:
        raise ValueError("Choose at least one country code.")
    src = sources or {n: _download(f + ".csv") for n, f in
                      (("airports", "airports"), ("frequencies", "airport-frequencies"), ("runways", "runways"))}
    airports = [a for a in src["airports"] if a["iso_country"] in wanted and a["type"] in KEEP_TYPES]
    if not airports:
        raise ValueError("The downloaded data has no airports for those countries.")
    ids = {a["id"] for a in airports}
    d = get_db()
    d.execute("TRUNCATE airports RESTART IDENTITY CASCADE")
    with d.cursor() as cur:
        cur.executemany(
            "INSERT INTO airports (id, ident, type, name, lat, lon, elevation_ft, country, region, municipality, "
            "scheduled_service, icao_code, iata_code, gps_code, local_code, home_link, wikipedia_link) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(int(a["id"]), a["ident"], a["type"], a["name"], _f(a["latitude_deg"]), _f(a["longitude_deg"]),
              _i(a["elevation_ft"]), a["iso_country"], a["iso_region"], a["municipality"] or None,
              a["scheduled_service"] == "yes", a["icao_code"] or None, a["iata_code"] or None,
              a["gps_code"] or None, a["local_code"] or None, a["home_link"] or None, a["wikipedia_link"] or None)
             for a in airports if _f(a["latitude_deg"]) is not None and _f(a["longitude_deg"]) is not None])
        cur.executemany(
            "INSERT INTO airport_frequencies (airport_id, type, description, mhz) VALUES (%s,%s,%s,%s)",
            [(int(f["airport_ref"]), f["type"], f["description"], _f(f["frequency_mhz"]))
             for f in src["frequencies"] if f["airport_ref"] in ids and _f(f["frequency_mhz"]) is not None])
        cur.executemany(
            "INSERT INTO runways (airport_id, length_ft, width_ft, surface, lighted, closed, le_ident, he_ident, le_heading, he_heading) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(int(r["airport_ref"]), _i(r["length_ft"]), _i(r["width_ft"]), r["surface"] or None, r["lighted"] == "1",
              r["closed"] == "1", r["le_ident"] or None, r["he_ident"] or None, _f(r["le_heading_degT"]), _f(r["he_heading_degT"]))
             for r in src["runways"] if r["airport_ref"] in ids])
    d.commit()
    counts = counts_now()
    set_setting("airports_last_import", datetime.now(timezone.utc).isoformat())
    set_setting("airports_last_counts", f"{counts['airports']} airports, {counts['frequencies']} frequencies, {counts['runways']} runways")
    return counts


def update_due(now, last_iso, attempt_iso, days, hhmm, tz):
    """Whether the automatic update should start now: it is `days` since the last good update, the chosen time of day has
    arrived in the display time zone, and a failed try is not repeated for six hours."""
    if days <= 0:
        return False
    if last_iso and now - datetime.fromisoformat(last_iso) < timedelta(days=days) - timedelta(hours=1):
        return False
    if attempt_iso and now - datetime.fromisoformat(attempt_iso) < timedelta(hours=6):
        return False
    local = now.astimezone(tz)
    h, m = (int(x) for x in hhmm.split(":"))
    return (local.hour, local.minute) >= (h, m)


def next_update(last_iso, days, hhmm, tz):
    """Human text for the Airports page."""
    if days <= 0:
        return "Automatic updates are off."
    if not last_iso:
        return "The first automatic update runs at the next scheduled time."
    due = (datetime.fromisoformat(last_iso) + timedelta(days=days)).astimezone(tz)
    h, m = (int(x) for x in hhmm.split(":"))
    return "Next automatic update: " + due.replace(hour=h, minute=m).strftime("%Y-%m-%d %H:%M") + f" ({tz.key})."


def run_scheduled(app):
    """One pass of the scheduler: update the airport data when it is due. Safe to call from every worker."""
    import psycopg
    from zoneinfo import ZoneInfo
    with app.app_context():
        now = datetime.now(timezone.utc)
        tz = ZoneInfo(cfg("timezone"))
        if not update_due(now, get_setting("airports_last_import"), get_setting("airports_last_attempt"),
                          int(cfg("airports_update_days")), cfg("airports_update_time"), tz):
            return False
        with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock_conn:
            if not lock_conn.execute("SELECT pg_try_advisory_lock(727009)").fetchone()[0]:
                return False
            try:
                set_setting("airports_last_attempt", now.isoformat())
                try:
                    counts = import_airports(cfg("airport_countries").split(","))
                    detail = f"{counts['airports']} airports, {counts['frequencies']} frequencies, {counts['runways']} runways"
                    set_setting("airports_last_attempt", "")
                except Exception as e:
                    detail = f"failed: {type(e).__name__}: {e}"[:300]
                get_db().execute("INSERT INTO audit_log (actor_id, actor_name, action, target, detail) VALUES (NULL,'system','airports.auto_update',%s,%s)",
                                 (cfg("airport_countries"), detail))
                get_db().commit()
                return not detail.startswith("failed")
            finally:
                lock_conn.execute("SELECT pg_advisory_unlock(727009)")


def start_scheduler(app):
    def loop():
        time.sleep(60)
        while True:
            try:
                run_scheduled(app)
            except Exception:
                pass
            time.sleep(600)
    threading.Thread(target=loop, name="airports-schedule", daemon=True).start()


def counts_now():
    d = get_db()
    return {"airports": d.execute("SELECT COUNT(*) AS n FROM airports").fetchone()["n"],
            "frequencies": d.execute("SELECT COUNT(*) AS n FROM airport_frequencies").fetchone()["n"],
            "runways": d.execute("SELECT COUNT(*) AS n FROM runways").fetchone()["n"]}


SORTS = {"ident": "ident", "name": "name", "city": "municipality", "type": "type", "elev": "elevation_ft"}


def search(q="", region="", kind="", limit=100, offset=0, sort="", direction="asc"):
    clauses, params = [], []
    if q:
        clauses.append("(name ILIKE %s OR ident ILIKE %s OR municipality ILIKE %s OR icao_code ILIKE %s OR iata_code ILIKE %s OR local_code ILIKE %s)")
        params += [f"%{q}%"] * 6
    if region:
        clauses.append("region = %s")
        params.append(region)
    if kind in TYPE_LABELS:
        clauses.append("type = %s")
        params.append(kind)
    w = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    d = get_db()
    total = d.execute(f"SELECT COUNT(*) AS n FROM airports{w}", params).fetchone()["n"]
    order = (SORTS[sort] + (" DESC" if direction == "desc" else " ASC") + " NULLS LAST, name") if sort in SORTS else "CASE type WHEN 'large_airport' THEN 0 WHEN 'medium_airport' THEN 1 ELSE 2 END, name"
    rows = d.execute(f"SELECT * FROM airports{w} ORDER BY {order} "
                     "LIMIT %s OFFSET %s", params + [limit, offset]).fetchall()
    return rows, total


def regions():
    return [r["region"] for r in get_db().execute("SELECT DISTINCT region FROM airports ORDER BY region").fetchall()]


def get_airport(ident):
    d = get_db()
    a = d.execute("SELECT * FROM airports WHERE lower(ident)=lower(%s) OR lower(icao_code)=lower(%s) OR lower(local_code)=lower(%s) "
                  "ORDER BY id LIMIT 1", (ident, ident, ident)).fetchone()
    if a is None:
        return None, [], []
    freqs = d.execute("SELECT type, description, mhz FROM airport_frequencies WHERE airport_id=%s ORDER BY type, mhz", (a["id"],)).fetchall()
    runs = d.execute("SELECT * FROM runways WHERE airport_id=%s ORDER BY length_ft DESC NULLS LAST", (a["id"],)).fetchall()
    return a, freqs, runs


def nearest(lat, lon, limit=5, kinds=("large_airport", "medium_airport")):
    """Closest airports to a point with distance in statute miles (haversine), nearest first."""
    return get_db().execute(
        "SELECT *, 3958.8 * 2 * asin(sqrt(least(1.0, power(sin(radians(lat - %(lat)s) / 2), 2) + "
        "cos(radians(%(lat)s)) * cos(radians(lat)) * power(sin(radians(lon - %(lon)s) / 2), 2)))) AS miles "
        "FROM airports WHERE type = ANY(%(kinds)s) ORDER BY miles LIMIT %(limit)s",
        {"lat": lat, "lon": lon, "kinds": list(kinds), "limit": limit}).fetchall()


def status():
    return {"counts": counts_now(), "last": get_setting("airports_last_import"),
            "summary": get_setting("airports_last_counts")}
