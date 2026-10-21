"""Polls the hub's readsb clients.json and records every feeder's status and statistics.

clients.json row format (readsb, verified against source):
  [receiverId (full UUID), "host port N", avg kbit/s, connection seconds, avg messages/s,
   avg positions/s, reduce_signal, recent RTT ms, total positions]
The message and position rates are averages since the connection started, so the collector derives
the current rate from the change between polls.
"""
import json
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from .settings import cfg_int, get_setting, set_setting

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
INTERNAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def fetch_clients(hub_url, timeout=5):
    with urllib.request.urlopen(hub_url.rstrip("/") + "/data/clients.json", timeout=timeout) as r:
        return json.loads(r.read(5_000_000))


def parse_clients(doc):
    """Real feeders only: full UUIDs, not the hub's own internal connections. One row per UUID (newest connection)."""
    found = {}
    for row in doc.get("clients", []):
        try:
            uuid, hostport, kbit, conn, msgs, pos, _reduce, rtt, positions = row[:9]
        except ValueError:
            continue
        host = str(hostport).strip().rsplit(" port ", 1)[0].strip()
        if not UUID_RE.match(str(uuid)) or str(uuid).endswith("-0000-000000000000") or host in INTERNAL_HOSTS:
            continue
        rec = {"uuid": uuid, "ip": host, "kbit": float(kbit), "conn": int(conn), "msgs": float(msgs),
               "pos": float(pos), "rtt": float(rtt), "positions": int(positions)}
        if uuid not in found or rec["conn"] < found[uuid]["conn"]:
            found[uuid] = rec
    return list(found.values())


def record(conn, rows, now, offline_after):
    """Apply one poll's rows to the database. `conn` is a psycopg connection with dict rows."""
    for r in rows:
        f = conn.execute("SELECT * FROM feeders WHERE uuid=%s", (r["uuid"],)).fetchone()
        since = now - timedelta(seconds=r["conn"])
        if f is None:
            conn.execute("INSERT INTO feeders (uuid, first_seen, last_seen, connected_since, last_ip, online) "
                         "VALUES (%s,%s,%s,%s,%s,TRUE)", (r["uuid"], now, now, since, r["ip"]))
            conn.execute("INSERT INTO feeder_events (feeder_uuid, ts, event, detail) VALUES (%s,%s,'first_seen',%s)",
                         (r["uuid"], now, r["ip"]))
            conn.execute("INSERT INTO feeder_events (feeder_uuid, ts, event, detail) VALUES (%s,%s,'connect',%s)",
                         (r["uuid"], now, r["ip"]))
        else:
            if not f["online"]:
                conn.execute("INSERT INTO feeder_events (feeder_uuid, ts, event, detail) VALUES (%s,%s,'connect',%s)",
                             (r["uuid"], now, r["ip"]))
                conn.execute("UPDATE feeders SET connected_since=%s WHERE uuid=%s", (since, r["uuid"]))
            if f["last_ip"] and f["last_ip"] != r["ip"]:
                conn.execute("INSERT INTO feeder_events (feeder_uuid, ts, event, detail) VALUES (%s,%s,'ip_change',%s)",
                             (r["uuid"], now, f"{f['last_ip']} -> {r['ip']}"))
            conn.execute("UPDATE feeders SET last_seen=%s, last_ip=%s, online=TRUE WHERE uuid=%s", (now, r["ip"], r["uuid"]))

        prev = conn.execute("SELECT ts, conn_secs, positions, msg_total FROM feeder_samples WHERE feeder_uuid=%s "
                            "ORDER BY ts DESC LIMIT 1", (r["uuid"],)).fetchone()
        msg_total = int(round(r["msgs"] * r["conn"]))
        msg_rate, pos_rate = r["msgs"], r["pos"]  # first sample of a connection: averages since connect
        if prev and prev["conn_secs"] is not None and r["conn"] > prev["conn_secs"]:
            dt = (now - prev["ts"]).total_seconds()
            if dt > 0:  # same connection: rate over the interval since the previous poll
                pos_rate = max(0.0, (r["positions"] - (prev["positions"] or 0)) / dt)
                msg_rate = max(0.0, (msg_total - (prev["msg_total"] or 0)) / dt)
        conn.execute("INSERT INTO feeder_samples (feeder_uuid, ts, msg_per_s, pos_per_s, kbit_per_s, rtt_ms, conn_secs, positions, msg_total, ip) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (r["uuid"], now, msg_rate, pos_rate, r["kbit"], r["rtt"], r["conn"], r["positions"], msg_total, r["ip"]))
    # feeders absent for a while go offline
    gone = conn.execute("UPDATE feeders SET online=FALSE WHERE online AND last_seen < %s RETURNING uuid, last_ip",
                        (now - offline_after,)).fetchall()
    for g in gone:
        conn.execute("INSERT INTO feeder_events (feeder_uuid, ts, event, detail) VALUES (%s,%s,'disconnect',%s)",
                     (g["uuid"], now, g["last_ip"]))
    return len(rows), len(gone)


def tick(app):
    """One poll of the hub. Only one worker runs it at a time (advisory lock)."""
    hub = app.config.get("HUB_URL", "")
    if not hub:
        return
    url = app.config["DATABASE_URL"]
    with app.app_context():
        offline_after = timedelta(seconds=cfg_int("offline_after_seconds"))
    with psycopg.connect(url, autocommit=True) as lock_conn:
        if not lock_conn.execute("SELECT pg_try_advisory_lock(727005)").fetchone()[0]:
            return
        try:
            now = datetime.now(timezone.utc)
            try:
                rows = parse_clients(fetch_clients(hub))
                error = ""
            except Exception as e:  # hub unreachable: record it, keep going
                rows, error = None, f"{type(e).__name__}: {e}"[:200]
            with psycopg.connect(url, row_factory=dict_row) as conn:
                if rows is not None:
                    record(conn, rows, now, offline_after)
                    conn.execute("INSERT INTO settings (key, value) VALUES ('collector_last_ok', %s) "
                                 "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (now.isoformat(),))
                conn.execute("INSERT INTO settings (key, value) VALUES ('collector_last_error', %s) "
                             "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (error,))
                conn.commit()
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(727005)")


def rollup_daily(app):
    """Once a day: samples older than the retention window are averaged into hourly rows, then deleted."""
    with app.app_context():
        today = datetime.now(timezone.utc).date().isoformat()
        if get_setting("collector_last_rollup") == today:
            return
        days = max(1, cfg_int("sample_retention_days"))
        with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock_conn:
            if not lock_conn.execute("SELECT pg_try_advisory_lock(727006)").fetchone()[0]:
                return
            try:
                with psycopg.connect(app.config["DATABASE_URL"]) as conn:
                    conn.execute(
                        "INSERT INTO feeder_hourly (feeder_uuid, hour, samples, msg_per_s, pos_per_s) "
                        "SELECT feeder_uuid, date_trunc('hour', ts), COUNT(*), AVG(msg_per_s), AVG(pos_per_s) "
                        "FROM feeder_samples WHERE ts < now() - make_interval(days => %s) "
                        "GROUP BY feeder_uuid, date_trunc('hour', ts) "
                        "ON CONFLICT (feeder_uuid, hour) DO NOTHING", (days,))
                    conn.execute("DELETE FROM feeder_samples WHERE ts < now() - make_interval(days => %s)", (days,))
                    conn.commit()
                set_setting("collector_last_rollup", today)
            finally:
                lock_conn.execute("SELECT pg_advisory_unlock(727006)")


def start_collector(app):
    def loop():
        time.sleep(10)
        while True:
            for job in (tick, rollup_daily):
                try:
                    job(app)
                except Exception:
                    pass
            with app.app_context():
                wait = cfg_int("collector_poll_seconds")
            time.sleep(wait)
    threading.Thread(target=loop, name="collector", daemon=True).start()
