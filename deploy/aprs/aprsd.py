"""arksignal-aprs: reads the APRS-IS network (amateur radio's packet position network) around Arkansas and keeps what the site shows: the latest position of each station, a short trail
for those that move, and the weather observations of the stations that send them (CWOP and amateur weather stations). Its own container and its own database.

Terms (DATA_SOURCES_TERMS.md): APRS-IS carries only amateur radio traffic that is public by design (every packet may go out on radio), read here with a read-only login (passcode -1) and a
server-side area filter. Not stored or shown: messages, bulletins, status text, telemetry. Privacy: only the latest position and a short trail (set in the portal), a station can be hidden
by its call sign, and the portal can show a moving station more roughly. aprs.fi is not used (its terms forbid mirroring).
"""
import json
import math
import os
import queue
import re
import socket
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import aprslib
import psycopg
from psycopg.rows import dict_row

DB_URL = os.environ.get("DATABASE_URL", "postgresql://aprs:aprs@aprs-db:5432/aprs")
PORT = int(os.environ.get("PORT", "8480"))
VERSION = "dev"
try:
    t = open("/BUILD_INFO").read()
    VERSION = t.split("version:")[1].split()[0] if "version:" in t else "dev"
except OSError:
    pass

DEFAULTS = {
    "enabled": "1",
    "host": "rotate.aprs2.net",
    "port": "14580",
    "login": "ARKSIG",            # read-only: any name works with passcode -1 (N0CALL is refused by the servers)
    "filter": "r/34.8/-92.4/300",
    "trail_hours": "3",
    "keep_days": "30",
    "window_hours": "24",
    "write_seconds": "30",
}
STATE = {"connected": False, "server": "", "packets": 0, "stored": 0, "since": time.time(), "last": None, "error": "", "reconnects": 0}
_cfg = {}
_cfg_lock = threading.Lock()
Q = queue.Queue(maxsize=20000)


def db():
    return psycopg.connect(DB_URL, row_factory=dict_row, autocommit=True)


def init_db():
    for _ in range(60):
        try:
            with db() as c:
                c.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                c.execute("""CREATE TABLE IF NOT EXISTS stations (call TEXT PRIMARY KEY, symbol_table TEXT NOT NULL DEFAULT '/', symbol TEXT NOT NULL DEFAULT '', lat DOUBLE PRECISION, lon DOUBLE PRECISION, alt_m DOUBLE PRECISION,
                    course INT, speed_kmh DOUBLE PRECISION, comment TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'fixed', via TEXT NOT NULL DEFAULT '', packets INT NOT NULL DEFAULT 0,
                    first_heard TIMESTAMPTZ NOT NULL DEFAULT now(), last_heard TIMESTAMPTZ NOT NULL DEFAULT now(), moved TIMESTAMPTZ)""")
                c.execute("CREATE INDEX IF NOT EXISTS stations_heard ON stations (last_heard DESC)")
                c.execute("CREATE TABLE IF NOT EXISTS trail (call TEXT NOT NULL, ts TIMESTAMPTZ NOT NULL, lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL, PRIMARY KEY (call, ts))")
                c.execute("""CREATE TABLE IF NOT EXISTS weather (call TEXT PRIMARY KEY, ts TIMESTAMPTZ NOT NULL, lat DOUBLE PRECISION, lon DOUBLE PRECISION, temp_c DOUBLE PRECISION, wind_dir INT, wind_ms DOUBLE PRECISION,
                    gust_ms DOUBLE PRECISION, humidity INT, pressure_hpa DOUBLE PRECISION, rain_1h_mm DOUBLE PRECISION, rain_24h_mm DOUBLE PRECISION, rain_mid_mm DOUBLE PRECISION, luminosity INT)""")
                c.execute("CREATE TABLE IF NOT EXISTS hidden (call TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', added TIMESTAMPTZ NOT NULL DEFAULT now())")
            return
        except Exception as e:
            print("waiting for the database:", str(e)[:100], flush=True)
            time.sleep(2)
    raise SystemExit("the database never answered")


def cfg(key):
    with _cfg_lock:
        hit = _cfg.get(key)
        if hit and time.time() - hit[1] < 15:
            return hit[0]
    try:
        with db() as c:
            r = c.execute("SELECT value FROM config WHERE key=%s", (key,)).fetchone()
        v = r["value"] if r else os.environ.get("APRS_" + key.upper(), DEFAULTS[key])
    except Exception:
        v = DEFAULTS[key]
    with _cfg_lock:
        _cfg[key] = (v, time.time())
    return v


def set_cfg(key, value):
    if key not in DEFAULTS:
        raise ValueError("unknown setting " + key)
    with db() as c:
        c.execute("INSERT INTO config (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, str(value)))
    with _cfg_lock:
        _cfg.pop(key, None)


def configured():
    with db() as c:
        return c.execute("SELECT COUNT(*) AS n FROM config").fetchone()["n"] > 0


# ------------------------------------------------------------------ packets

CALL_RE = re.compile(r"^[A-Za-z0-9]{1,2}[0-9][A-Za-z0-9]{0,4}(-[0-9]{1,2})?$|^[A-Za-z0-9-]{3,9}$")
SYMBOL_KIND = {"_": "weather", "#": "digipeater", "&": "gateway", "I": "igate", "r": "repeater", "a": "ambulance", "f": "fire", "!": "police", "'": "mobile", ">": "mobile", "<": "mobile", "k": "mobile", "u": "mobile",
               "b": "mobile", "[": "mobile", "O": "balloon", "^": "aircraft", "X": "mobile", "j": "mobile", "v": "mobile", "Y": "mobile", "s": "mobile", "p": "mobile"}


def kind_of(sym, table, speed, weather):
    if weather and sym == "_":
        return "weather"
    k = SYMBOL_KIND.get(sym or "", "fixed")
    if k == "fixed" and speed and speed > 3:
        return "mobile"
    return k


def parse_line(raw):
    """One APRS-IS line -> a dict of what we keep, or None (comments, messages, bulletins, status, telemetry, anything unreadable are dropped on purpose)."""
    if not raw or raw.startswith("#"):
        return None
    try:
        p = aprslib.parse(raw)
    except Exception:
        return None
    fmt = p.get("format", "")
    if fmt in ("message", "bulletin", "status", "telemetry-message", "telemetry", "capabilities", "beacon", "thirdparty", "raw", "invalid"):
        return None
    lat, lon = p.get("latitude"), p.get("longitude")
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    name = p.get("object_name") or p.get("item_name") or p.get("from") or ""
    name = name.strip().upper()
    if not name or not re.match(r"^[A-Z0-9 _.\-/]{1,20}$", name):
        return None
    wx = p.get("weather") or None
    speed, course = p.get("speed"), p.get("course")
    sym, tab = p.get("symbol") or "", p.get("symbol_table") or "/"
    out = {"call": name, "symbol": sym, "table": tab, "lat": lat, "lon": lon, "alt": p.get("altitude"), "course": course, "speed": speed, "comment": (p.get("comment") or "")[:200],
           "via": ((p.get("path") or [""])[-1] if p.get("path") else "")[:20], "object": bool(p.get("object_name") or p.get("item_name")), "wx": None}
    if wx or sym == "_":
        w = dict(wx or {})
        if sym == "_" and speed is not None and "wind_speed" not in w:
            # in a position packet the wind is carried in the course and speed fields: course is the direction and the speed is miles an hour (aprslib read it as knots and gave km/h)
            w["wind_direction"] = course
            w["wind_speed"] = round(speed / 1.852 * 0.44704, 2)
            out["course"], out["speed"] = None, None
        if w:
            out["wx"] = {"temp_c": w.get("temperature"), "wind_dir": w.get("wind_direction"), "wind_ms": w.get("wind_speed"), "gust_ms": w.get("wind_gust"), "humidity": w.get("humidity"),
                         "pressure_hpa": w.get("pressure"), "rain_1h_mm": w.get("rain_1h"), "rain_24h_mm": w.get("rain_24h"), "rain_mid_mm": w.get("rain_since_midnight"), "luminosity": w.get("luminosity")}
    out["kind"] = "weather" if out["wx"] and not out["object"] else kind_of(sym, tab, out["speed"], bool(out["wx"]))
    if out["object"] and out["kind"] == "mobile":
        out["kind"] = "object"
    return out


# ------------------------------------------------------------------ the reader

def reader():
    backoff = 5
    last_write = {}
    while True:
        if cfg("enabled") != "1":
            STATE["connected"] = False
            time.sleep(30)
            continue
        host, port = cfg("host"), int(cfg("port"))
        try:
            s = socket.create_connection((host, port), timeout=20)
            s.settimeout(60)
            banner = s.recv(200).decode(errors="replace").strip()
            s.sendall(f"user {cfg('login')} pass -1 vers ArkSignal-aprs {VERSION} filter {cfg('filter')}\r\n".encode())
            STATE.update(connected=True, server=banner[:60], error="")
            buf = b""
            backoff = 5
            while True:
                d = s.recv(8192)
                if not d:
                    raise ConnectionError("the server closed the connection")
                buf += d
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    raw = ln.decode("utf-8", "replace").strip()
                    if raw.startswith("# logresp") and "unverified" not in raw and "verified" not in raw:
                        STATE["error"] = raw[:100]
                    if raw.startswith("#"):
                        STATE["last"] = time.time() if "logresp" in raw else STATE["last"]
                        continue
                    STATE["packets"] += 1
                    rec = parse_line(raw)
                    if rec is None:
                        continue
                    now = time.time()
                    key = rec["call"]
                    if rec["wx"] is None and now - last_write.get(key, 0) < float(cfg("write_seconds")):
                        continue
                    last_write[key] = now
                    if len(last_write) > 50000:
                        last_write.clear()
                    try:
                        Q.put_nowait(rec)
                    except queue.Full:
                        pass
        except Exception as e:
            STATE.update(connected=False, error=f"{type(e).__name__}: {str(e)[:100]}")
            STATE["reconnects"] += 1
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


def writer():
    """Takes parsed packets off the queue in small batches and keeps the latest position, the short trail and the latest weather of each station."""
    conn = None
    while True:
        batch = []
        try:
            batch.append(Q.get(timeout=2))
            while len(batch) < 500:
                batch.append(Q.get_nowait())
        except queue.Empty:
            pass
        if not batch:
            continue
        try:
            if conn is None or conn.closed:
                conn = db()
            hidden = {r["call"] for r in conn.execute("SELECT call FROM hidden").fetchall()}
            for r in batch:
                base = re.sub(r"-\d+$", "", r["call"])
                if r["call"] in hidden or base in hidden:
                    continue
                conn.execute("""INSERT INTO stations (call, symbol_table, symbol, lat, lon, alt_m, course, speed_kmh, comment, kind, via, packets, last_heard, moved)
                    VALUES (%(call)s,%(table)s,%(symbol)s,%(lat)s,%(lon)s,%(alt)s,%(course)s,%(speed)s,%(comment)s,%(kind)s,%(via)s,1,now(),NULL)
                    ON CONFLICT (call) DO UPDATE SET symbol_table=EXCLUDED.symbol_table, symbol=EXCLUDED.symbol, alt_m=EXCLUDED.alt_m, course=EXCLUDED.course, speed_kmh=EXCLUDED.speed_kmh, comment=EXCLUDED.comment, kind=EXCLUDED.kind, via=EXCLUDED.via,
                    packets=stations.packets + 1, last_heard=now(),
                    moved = CASE WHEN abs(stations.lat - EXCLUDED.lat) > 0.0005 OR abs(stations.lon - EXCLUDED.lon) > 0.0005 THEN now() ELSE stations.moved END,
                    lat=EXCLUDED.lat, lon=EXCLUDED.lon""", r)
                if r["kind"] in ("mobile", "balloon", "aircraft") or (r["speed"] or 0) > 3:
                    conn.execute("INSERT INTO trail (call, ts, lat, lon) VALUES (%s, date_trunc('second', now()), %s, %s) ON CONFLICT DO NOTHING", (r["call"], r["lat"], r["lon"]))
                if r["wx"]:
                    w = dict(r["wx"], call=r["call"], lat=r["lat"], lon=r["lon"])
                    conn.execute("""INSERT INTO weather (call, ts, lat, lon, temp_c, wind_dir, wind_ms, gust_ms, humidity, pressure_hpa, rain_1h_mm, rain_24h_mm, rain_mid_mm, luminosity)
                        VALUES (%(call)s, now(), %(lat)s, %(lon)s, %(temp_c)s, %(wind_dir)s, %(wind_ms)s, %(gust_ms)s, %(humidity)s, %(pressure_hpa)s, %(rain_1h_mm)s, %(rain_24h_mm)s, %(rain_mid_mm)s, %(luminosity)s)
                        ON CONFLICT (call) DO UPDATE SET ts=now(), lat=EXCLUDED.lat, lon=EXCLUDED.lon, temp_c=COALESCE(EXCLUDED.temp_c, weather.temp_c), wind_dir=COALESCE(EXCLUDED.wind_dir, weather.wind_dir),
                        wind_ms=COALESCE(EXCLUDED.wind_ms, weather.wind_ms), gust_ms=COALESCE(EXCLUDED.gust_ms, weather.gust_ms), humidity=COALESCE(EXCLUDED.humidity, weather.humidity),
                        pressure_hpa=COALESCE(EXCLUDED.pressure_hpa, weather.pressure_hpa), rain_1h_mm=COALESCE(EXCLUDED.rain_1h_mm, weather.rain_1h_mm), rain_24h_mm=COALESCE(EXCLUDED.rain_24h_mm, weather.rain_24h_mm),
                        rain_mid_mm=COALESCE(EXCLUDED.rain_mid_mm, weather.rain_mid_mm), luminosity=COALESCE(EXCLUDED.luminosity, weather.luminosity)""", w)
                STATE["stored"] += 1
        except Exception as e:
            STATE["error"] = "database: " + str(e)[:100]
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            time.sleep(2)


def pruner():
    while True:
        try:
            with db() as c:
                c.execute("DELETE FROM trail WHERE ts < now() - make_interval(hours => %s)", (int(cfg("trail_hours")),))
                c.execute("DELETE FROM stations WHERE last_heard < now() - make_interval(days => %s)", (int(cfg("keep_days")),))
                c.execute("DELETE FROM weather WHERE ts < now() - make_interval(days => %s)", (int(cfg("keep_days")),))
                c.execute("DELETE FROM stations USING hidden h WHERE stations.call = h.call OR regexp_replace(stations.call, '-[0-9]+$', '') = h.call")
                c.execute("DELETE FROM weather USING hidden h WHERE weather.call = h.call OR regexp_replace(weather.call, '-[0-9]+$', '') = h.call")
        except Exception as e:
            print("prune:", e, flush=True)
        time.sleep(600)


# ------------------------------------------------------------------ HTTP

def iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def row(r):
    return {k: iso(v) for k, v in r.items()}


def mine_bbox(q):
    try:
        s, w, n, e = [float(x) for x in (q.get("bbox") or "").split(",")]
        return "AND lat BETWEEN %(s)s AND %(n)s AND lon BETWEEN %(w)s AND %(e)s", {"s": s, "w": w, "n": n, "e": e}
    except ValueError:
        return "", {}


def dbsize():
    """The size of this service's whole database in bytes (shown on the portal's Admin > Usage)."""
    try:
        with db() as c:
            return int(c.execute("SELECT pg_database_size(current_database()) AS s").fetchone()["s"])
    except Exception:
        return None


class H(BaseHTTPRequestHandler):
    server_version = "arksignal-aprs"

    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        p = u.path.rstrip("/")
        try:
            if p in ("", "/health"):
                return self.send({"ok": True, "version": VERSION, "connected": STATE["connected"]})
            if p == "/stats":
                with db() as c:
                    n = c.execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE last_heard > now() - interval '1 hour') AS hour, COUNT(*) FILTER (WHERE last_heard > now() - make_interval(hours => %s)) AS window FROM stations", (int(cfg("window_hours")),)).fetchone()
                    kinds = c.execute("SELECT kind, COUNT(*) AS n FROM stations WHERE last_heard > now() - make_interval(hours => %s) GROUP BY kind ORDER BY n DESC", (int(cfg("window_hours")),)).fetchall()
                    wx = c.execute("SELECT COUNT(*) AS n FROM weather WHERE ts > now() - interval '90 minutes'").fetchone()["n"]
                    hid = c.execute("SELECT COUNT(*) AS n FROM hidden").fetchone()["n"]
                up = max(1, time.time() - STATE["since"])
                return self.send({"db_bytes": dbsize(), "version": VERSION, "stations": n["n"], "heard_hour": n["hour"], "heard_window": n["window"], "kinds": {r["kind"]: r["n"] for r in kinds}, "weather_now": wx, "hidden": hid,
                                  "feed": {"connected": STATE["connected"], "server": STATE["server"], "packets": STATE["packets"], "stored": STATE["stored"], "per_min": round(STATE["packets"] / up * 60, 1), "error": STATE["error"], "reconnects": STATE["reconnects"]},
                                  "config": {k: cfg(k) for k in DEFAULTS}, "configured": configured()})
            if p == "/stations":
                where, args = mine_bbox(q)
                hrs = min(int(q.get("hours") or cfg("window_hours")), 720)
                kind = q.get("kind") or ""
                call = (q.get("q") or "").strip().upper()
                sql = ("SELECT call, symbol_table, symbol, lat, lon, alt_m, course, speed_kmh, comment, kind, via, packets, first_heard, last_heard, moved FROM stations WHERE last_heard > now() - make_interval(hours => %(h)s) " + where
                       + (" AND kind = %(k)s" if kind else "") + (" AND call LIKE %(c)s" if call else "") + " ORDER BY last_heard DESC LIMIT %(l)s")
                with db() as c:
                    rows = c.execute(sql, dict(args, h=hrs, k=kind, c=call + "%", l=min(int(q.get("limit") or 3000), 10000))).fetchall()
                return self.send({"stations": [row(r) for r in rows]})
            m = re.match(r"^/stations/([^/]+)$", p)
            if m:
                call = urllib.parse.unquote(m.group(1)).upper()
                with db() as c:
                    r = c.execute("SELECT * FROM stations WHERE call=%s", (call,)).fetchone()
                    if not r:
                        return self.send({"error": "not found"}, 404)
                    tr = c.execute("SELECT ts, lat, lon FROM trail WHERE call=%s ORDER BY ts", (call,)).fetchall()
                    wx = c.execute("SELECT * FROM weather WHERE call=%s", (call,)).fetchone()
                return self.send({"station": row(r), "trail": [[iso(t["ts"]), t["lat"], t["lon"]] for t in tr], "weather": row(wx) if wx else None})
            if p == "/weather":
                where, args = mine_bbox(q)
                with db() as c:
                    rows = c.execute("SELECT * FROM weather WHERE ts > now() - make_interval(mins => %(m)s) " + where + " ORDER BY ts DESC LIMIT 2000", dict(args, m=int(q.get("minutes") or 90))).fetchall()
                return self.send({"weather": [row(r) for r in rows]})
            if p == "/hidden":
                with db() as c:
                    rows = c.execute("SELECT call, reason, added FROM hidden ORDER BY call").fetchall()
                return self.send({"hidden": [row(r) for r in rows]})
            return self.send({"error": "not found"}, 404)
        except Exception as e:
            return self.send({"error": str(e)[:200]}, 500)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path.rstrip("/")
        b = self.body_json()
        try:
            if p == "/config":
                for k, v in (b or {}).items():
                    set_cfg(k, v)
                return self.send({"ok": True})
            if p == "/hide":
                call = str(b.get("call") or "").strip().upper()
                if not re.match(r"^[A-Z0-9]{1,2}[0-9][A-Z0-9]{1,4}(-[0-9]{1,2})?$", call):
                    return self.send({"error": "not a call sign"}, 400)
                with db() as c:
                    c.execute("INSERT INTO hidden (call, reason) VALUES (%s,%s) ON CONFLICT (call) DO UPDATE SET reason=EXCLUDED.reason", (call, str(b.get("reason") or "")[:200]))
                    c.execute("DELETE FROM stations WHERE call=%s OR regexp_replace(call, '-[0-9]+$', '') = %s", (call, call))
                    c.execute("DELETE FROM weather WHERE call=%s OR regexp_replace(call, '-[0-9]+$', '') = %s", (call, call))
                    c.execute("DELETE FROM trail WHERE call=%s OR regexp_replace(call, '-[0-9]+$', '') = %s", (call, call))
                return self.send({"ok": True})
            if p == "/unhide":
                with db() as c:
                    c.execute("DELETE FROM hidden WHERE call=%s", (str(b.get("call") or "").strip().upper(),))
                return self.send({"ok": True})
            return self.send({"error": "not found"}, 404)
        except Exception as e:
            return self.send({"error": str(e)[:200]}, 500)


def main():
    init_db()
    for fn in (reader, writer, pruner):
        threading.Thread(target=fn, daemon=True).start()
    print(f"arksignal-aprs {VERSION} listening on {PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
