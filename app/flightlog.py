"""The portal's own log of every aircraft and flight it has heard, so earlier flights can be searched and opened.

The hub's live feed only knows what is in the air now, and its trace files have no callsign index. Every few seconds the portal
looks at the live aircraft and keeps, per aircraft, one row of "flight" history: a flight is one aircraft flying under one callsign
without a gap longer than Settings > Flight History > Start a New Flight After a Gap of (minutes). Rows hold the times, the callsign,
registration and type, where the flight was first and last heard, and the highest altitude and speed. Searching it (Flights page, live
map search) needs no outside service; opening a flight uses the hub's own recorded track for that time.
"""
import json
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from .settings import cfg, cfg_int

LOCK_ID = 727009
RESTORE_GUARD = 727013   # held shared by every write transaction of the flight log; a restore takes it exclusively, so the two never wait on each other's tables (see backup.pause_background)


def _alt(ac):
    a = ac.get("alt_baro")
    if a == "ground":
        return 0
    return int(a) if isinstance(a, (int, float)) else None


def record(conn, aircraft, now, gap_minutes):
    """Fold one look at the live aircraft into the log. Returns (updated, started) counts."""
    seen = [a for a in aircraft if a.get("hex") and not str(a["hex"]).startswith("~")]
    if not seen:
        return 0, 0
    hexes = [str(a["hex"]).lower() for a in seen]
    open_rows = {}
    for r in conn.execute("SELECT DISTINCT ON (hex) id, hex, callsign, last_seen FROM flight_log WHERE hex = ANY(%s) AND last_seen > %s ORDER BY hex, last_seen DESC",
                          (hexes, now - timedelta(minutes=gap_minutes))).fetchall():
        open_rows[r["hex"]] = r
    updated = started = 0
    for a in seen:
        hx = str(a["hex"]).lower()
        cs = (a.get("flight") or "").strip().upper() or None
        reg, typ, desc = a.get("r"), a.get("t"), a.get("desc")
        mil = bool(int(a.get("dbFlags", 0) or 0) & 1)
        lat, lon = a.get("lat"), a.get("lon")
        alt, gs = _alt(a), a.get("gs")
        gs = int(gs) if isinstance(gs, (int, float)) else None
        row = open_rows.get(hx)
        if row and cs and row["callsign"] and cs != row["callsign"]:
            row = None   # a different callsign is a different flight
        pos = 1 if lat is not None else 0
        if row:
            conn.execute(
                "UPDATE flight_log SET last_seen=%s, callsign=COALESCE(callsign, %s), reg=COALESCE(reg, %s), type=COALESCE(type, %s), "
                "start_lat=COALESCE(start_lat, %s), start_lon=COALESCE(start_lon, %s), "
                "end_lat=COALESCE(%s, end_lat), end_lon=COALESCE(%s, end_lon), max_alt=GREATEST(COALESCE(max_alt, 0), COALESCE(%s, 0)), "
                "max_gs=GREATEST(COALESCE(max_gs, 0), COALESCE(%s, 0)), positions=positions+%s, mil=mil OR %s WHERE id=%s",
                (now, cs, reg, typ, lat, lon, lat, lon, alt, gs, pos, mil, row["id"]))
            updated += 1
        else:
            conn.execute(
                "INSERT INTO flight_log (hex, callsign, reg, type, first_seen, last_seen, start_lat, start_lon, end_lat, end_lon, max_alt, max_gs, positions, mil) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (hx, cs, reg, typ, now, now, lat, lon, lat, lon, alt, gs, pos, mil))
            started += 1
        conn.execute(
            "INSERT INTO aircraft_log (hex, reg, type, description, first_seen, last_seen, flights, mil) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (hex) DO UPDATE SET reg=COALESCE(EXCLUDED.reg, aircraft_log.reg), type=COALESCE(EXCLUDED.type, aircraft_log.type), "
            "description=COALESCE(EXCLUDED.description, aircraft_log.description), last_seen=EXCLUDED.last_seen, "
            "flights=aircraft_log.flights + %s, mil=aircraft_log.mil OR EXCLUDED.mil",
            (hx, reg, typ, desc, now, now, 1 if not row else 0, mil, 1 if not row else 0))
    return updated, started


def tick(app):
    hub = app.config.get("HUB_URL", "")
    if not hub:
        return
    with app.app_context():
        if cfg("flightlog_enabled") != "1":
            return
        gap = cfg_int("flightlog_gap_minutes")
    with urllib.request.urlopen(hub.rstrip("/") + "/data/aircraft.json", timeout=6) as r:
        aircraft = json.loads(r.read(20_000_000)).get("aircraft", [])
    url = app.config["DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as lock_conn:
        if not lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
            return
        try:
            with psycopg.connect(url, row_factory=dict_row) as conn:
                conn.execute("SELECT pg_advisory_xact_lock_shared(%s)", (RESTORE_GUARD,))
                record(conn, aircraft, datetime.now(timezone.utc), gap)
                conn.commit()
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))


def prune(app):
    with app.app_context():
        days = cfg_int("flightlog_days")
    if days <= 0:
        return
    with psycopg.connect(app.config["DATABASE_URL"]) as conn:
        conn.execute("SELECT pg_advisory_xact_lock_shared(%s)", (RESTORE_GUARD,))
        conn.execute("DELETE FROM flight_log WHERE last_seen < now() - make_interval(days => %s)", (days,))
        conn.execute("DELETE FROM aircraft_log WHERE last_seen < now() - make_interval(days => %s)", (days,))
        conn.commit()


def start_flightlog(app):
    def loop():
        time.sleep(20)
        n = 0
        while True:
            try:
                tick(app)
            except Exception:
                pass
            n += 1
            if n % 360 == 0:
                try:
                    prune(app)
                except Exception:
                    pass
            with app.app_context():
                wait = max(2, cfg_int("flightlog_poll_seconds"))
            time.sleep(wait)
    threading.Thread(target=loop, name="flightlog", daemon=True).start()


def _filters(q, day_from, day_to):
    q = "".join(ch for ch in (q or "").upper() if ch.isalnum() or ch == "-")[:12]
    where, args = [], []
    if q:
        like = "%" + q + "%"
        clause = "(upper(callsign) LIKE %s OR upper(replace(reg,'-','')) LIKE %s OR upper(reg) LIKE %s OR upper(type) = %s"
        args += [like, like.replace("-", ""), like, q]
        if len(q) == 6 and all(c in "0123456789ABCDEF" for c in q):
            clause += " OR hex = %s"
            args.append(q.lower())
        where.append(clause + ")")
    if day_from:
        where.append("last_seen >= %s")
        args.append(day_from)
    if day_to:
        where.append("first_seen < %s")
        args.append(day_to)
    return (" WHERE " + " AND ".join(where)) if where else "", args


def count(conn, q="", day_from=None, day_to=None):
    sql, args = _filters(q, day_from, day_to)
    return conn.execute("SELECT COUNT(*) AS n FROM flight_log" + sql, args).fetchone()["n"]


SORTS = {"flight": "callsign", "reg": "reg", "type": "type", "first": "first_seen", "last": "last_seen", "alt": "max_alt", "speed": "max_gs"}


def search(conn, q="", day_from=None, day_to=None, limit=60, offset=0, sort="last", direction="desc"):
    """Earlier flights matching a callsign, registration, aircraft type or hex code (partial is fine), newest first, optionally within dates."""
    where_sql, args = _filters(q, day_from, day_to)
    sql = ("SELECT id, hex, callsign, reg, type, first_seen, last_seen, start_lat, start_lon, end_lat, end_lon, max_alt, max_gs, positions, mil "
           "FROM flight_log" + where_sql + " ORDER BY " + SORTS.get(sort, "last_seen") + (" ASC" if direction == "asc" else " DESC") + " NULLS LAST, last_seen DESC LIMIT %s OFFSET %s")
    return conn.execute(sql, args + [limit, offset]).fetchall()
