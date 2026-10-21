"""arksignal-acars: keeps the ACARS, VDL2 and HFDL messages that members' receivers send (through the portal, which checks each station's token): de-duplicated across stations,
kept for a set number of days, matched to aircraft by tail number and ICAO address. Its own container and its own database, so it can move to another host.

Terms (DATA_SOURCES_TERMS.md): only our members' own receivers are a source. The messages are on open, public airwaves; Marty decided on 2026-10-14 that the text is public, and the portal can
narrow that with a setting. Nothing is read from outside aggregators. Input: acars_router / acarsdec / dumpvdl2 / dumphfdl JSON, one object per message.
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
from psycopg.rows import dict_row

DB_URL = os.environ.get("DATABASE_URL", "postgresql://acars:acars@acars-db:5432/acars")
PORT = int(os.environ.get("PORT", "8481"))
VERSION = "dev"
try:
    t = open("/BUILD_INFO").read()
    VERSION = t.split("version:")[1].split()[0] if "version:" in t else "dev"
except OSError:
    pass

DEFAULTS = {"keep_days": "14", "max_text": "4000", "dedupe_seconds": "120"}
STATE = {"since": time.time(), "received": 0, "stored": 0, "duplicates": 0, "rejected": 0}
_cfg = {}
_lock = threading.Lock()

# what a label means (ARINC 620 and common airline use); anything else shows as the label itself
LABELS = {"_d": "Command or response (ground station)", "5Z": "Airline designated", "5U": "Weather request", "80": "Airline designated (terminal)", "81": "Airline designated (terminal)", "83": "Airline designated (terminal)",
          "H1": "Message to or from the flight management system", "H2": "Weather report or request", "10": "Departure and arrival report", "11": "Departure and arrival report", "12": "Departure and arrival report",
          "13": "Departure and arrival report", "14": "Departure and arrival report", "15": "Departure and arrival report", "16": "Departure and arrival report", "17": "Departure and arrival report",
          "20": "Position report", "21": "Position report", "22": "Position report", "23": "Position report", "24": "Position report", "25": "Position report", "26": "Position report", "27": "Position report",
          "2Z": "Airline designated", "30": "Free text", "33": "Position report", "3M": "Airline designated", "4A": "Airline designated", "4M": "Airline designated", "4N": "Airline designated",
          "A1": "Free text", "A6": "Airline designated", "B1": "Departure clearance request", "B2": "Departure clearance", "B3": "Weather request", "B6": "Airline designated", "C1": "Operational uplink",
          "CA": "Controller to pilot data link (CPDLC)", "CR": "CPDLC", "CC": "CPDLC", "F3": "Free text", "F5": "Airline designated", "H3": "Message", "M1": "Departure and arrival report", "MA": "Message",
          "MD": "Maintenance", "Q0": "Link test", "Q1": "Departure and arrival report (OOOI: out, off, on, in)", "Q2": "Estimated time of arrival", "Q3": "Pushback", "Q4": "Departure and arrival report",
          "Q5": "Departure and arrival report", "Q6": "Departure and arrival report", "Q7": "Departure and arrival report", "QA": "Departure and arrival report", "QB": "Departure and arrival report",
          "QC": "Departure and arrival report", "QD": "Departure and arrival report", "QE": "Departure and arrival report", "QF": "Departure and arrival report", "QG": "Departure and arrival report",
          "QH": "Departure and arrival report", "QK": "Departure and arrival report", "QM": "Departure and arrival report", "QN": "Departure and arrival report", "QP": "Departure and arrival report",
          "QQ": "Departure and arrival report", "QR": "Departure and arrival report", "QS": "Departure and arrival report", "QT": "Departure and arrival report", "RA": "Free text",
          "SA": "Media advisory (which radio links are in use)", "SQ": "Ground station squitter (identification)", "SB": "Media advisory", "SC": "Media advisory", "XA": "Free text",
          "RB": "Airline designated", "AA": "Free text"}


def db():
    return psycopg.connect(DB_URL, row_factory=dict_row, autocommit=True)


def init_db():
    for _ in range(60):
        try:
            with db() as c:
                c.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                c.execute("""CREATE TABLE IF NOT EXISTS messages (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, ts TIMESTAMPTZ NOT NULL, station TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'acars',
                    freq DOUBLE PRECISION, level DOUBLE PRECISION, label TEXT NOT NULL DEFAULT '', tail TEXT NOT NULL DEFAULT '', flight TEXT NOT NULL DEFAULT '', hex TEXT NOT NULL DEFAULT '',
                    block_id TEXT NOT NULL DEFAULT '', msgno TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '', lat DOUBLE PRECISION, lon DOUBLE PRECISION, alt_ft INT,
                    heard_by INT NOT NULL DEFAULT 1, hash TEXT NOT NULL)""")
                c.execute("CREATE INDEX IF NOT EXISTS messages_ts ON messages (ts DESC)")
                c.execute("CREATE INDEX IF NOT EXISTS messages_tail ON messages (tail, ts DESC)")
                c.execute("CREATE INDEX IF NOT EXISTS messages_hex ON messages (hex, ts DESC)")
                c.execute("CREATE INDEX IF NOT EXISTS messages_hash ON messages (hash, ts DESC)")
                c.execute("CREATE TABLE IF NOT EXISTS station_counts (station TEXT PRIMARY KEY, messages BIGINT NOT NULL DEFAULT 0, last_heard TIMESTAMPTZ)")
            return
        except Exception as e:
            print("waiting for the database:", str(e)[:100], flush=True)
            time.sleep(2)
    raise SystemExit("the database never answered")


def cfg(key):
    with _lock:
        hit = _cfg.get(key)
        if hit and time.time() - hit[1] < 15:
            return hit[0]
    try:
        with db() as c:
            r = c.execute("SELECT value FROM config WHERE key=%s", (key,)).fetchone()
        v = r["value"] if r else DEFAULTS[key]
    except Exception:
        v = DEFAULTS[key]
    with _lock:
        _cfg[key] = (v, time.time())
    return v


def set_cfg(key, value):
    if key not in DEFAULTS:
        raise ValueError("unknown setting " + key)
    with db() as c:
        c.execute("INSERT INTO config (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, str(value)))
    with _lock:
        _cfg.pop(key, None)


# ------------------------------------------------------------------ messages

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _when(v):
    n = _num(v)
    if n is None or n <= 0:
        return datetime.now(timezone.utc)
    if n > 1e12:
        n /= 1000
    d = datetime.fromtimestamp(n, timezone.utc)
    return d if abs((d - datetime.now(timezone.utc)).total_seconds()) < 7 * 86400 else datetime.now(timezone.utc)


POS_RE = re.compile(r"\b([NS])\s?(\d{2})(\d{2}\.?\d*)\s?([EW])\s?(\d{3})(\d{2}\.?\d*)\b")


def position_in(text):
    """A latitude and longitude written as N34123W092456 (degrees and minutes) in a position report, or None."""
    m = POS_RE.search(text or "")
    if not m:
        return None
    def minutes(s):   # N34123 is 34 degrees 12.3 minutes: two digits of minutes, the rest tenths
        return float(s) if "." in s else float(s[:2] + "." + s[2:])
    try:
        lat = int(m.group(2)) + minutes(m.group(3)) / 60
        lon = int(m.group(5)) + minutes(m.group(6)) / 60
    except ValueError:
        return None
    lat, lon = (-lat if m.group(1) == "S" else lat), (-lon if m.group(4) == "W" else lon)
    return (round(lat, 5), round(lon, 5)) if abs(lat) <= 90 and abs(lon) <= 180 else None


def _dicts(o, depth=0):
    """Every dict inside o (and o itself), to six levels down."""
    if depth > 6:
        return
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _dicts(v, depth + 1)
    elif isinstance(o, list):
        for v in o[:50]:
            yield from _dicts(v, depth + 1)


def structured(raw):
    """What the nested output of dumphfdl (HFDL position and performance data), dumpvdl2 and JAERO (Inmarsat ADS-C) says about where an aircraft is and who it is, wherever it sits in the object:
    (lat, lon, alt_ft, flight, hex). Any field may be None. The first dict with a numeric latitude and longitude is the position; an ICAO address is six hex digits under icao, icao_address or aes."""
    lat = lon = alt = None
    flight, hexid = "", ""
    for d in _dicts(raw):
        la, lo = _num(d.get("lat")), _num(d.get("lon"))
        if lat is None and la is not None and lo is not None and abs(la) <= 90 and abs(lo) <= 180 and (la or lo):
            lat, lon = round(la, 5), round(lo, 5)
            a = _num(d.get("alt") if d.get("alt") is not None else d.get("altitude"))
            alt = a if a is not None and -2000 <= a <= 70000 else None
        for k in ("icao", "icao_address", "aes"):
            v = d.get(k)
            if not hexid and isinstance(v, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", v):
                hexid = v.lower()
            elif not hexid and isinstance(v, int) and 0 < v < 1 << 24:
                hexid = "%06x" % v
        for k in ("flight_id", "flight"):
            v = d.get(k)
            if not flight and isinstance(v, str) and 2 <= len(v.strip()) <= 8:
                flight = v.strip().upper()
    return lat, lon, alt, flight, hexid


def normalize(raw, station):
    """One router or decoder object -> a dict of what we keep, or None. Accepts acars_router (flat), acarsdec, dumpvdl2 ('vdl2.avlc.acars') and dumphfdl ('hfdl.lpdu.hfnpdu.acars')."""
    if not isinstance(raw, dict):
        return None
    a = raw
    mode = str(raw.get("mode") or "").strip()
    for path in (("vdl2", "avlc", "acars"), ("hfdl", "lpdu", "hfnpdu", "acars")):
        cur = raw
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if isinstance(cur, dict):
            a = dict(cur, **{k: v for k, v in raw.items() if k in ("timestamp", "freq")})
            if path[0] == "vdl2" and "freq" not in a and isinstance(raw.get("vdl2"), dict):
                a["freq"] = raw["vdl2"].get("freq")
            mode = "vdl2" if path[0] == "vdl2" else "hfdl"
    text = str(a.get("text") or a.get("msg_text") or "")[: int(cfg("max_text"))]
    tail = str(a.get("tail") or a.get("reg") or "").strip().upper().lstrip(".")
    flight = str(a.get("flight") or "").strip().upper()
    label = str(a.get("label") or "").strip()[:3]
    icao = a.get("icao") if a.get("icao") is not None else raw.get("icao")
    hexid = ""
    if isinstance(icao, int) or (isinstance(icao, str) and icao.isdigit()):
        hexid = "%06x" % int(icao)
    elif isinstance(icao, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", icao):
        hexid = icao.lower()
    s_lat, s_lon, s_alt, s_flight, s_hex = structured(raw)   # a position or an identity carried in nested fields (HFDL, VDL2 and JAERO ADS-C)
    flight, hexid = flight or s_flight, hexid or s_hex
    if not (text or tail or flight or label or (s_lat is not None and hexid)):
        return None
    freq = _num(a.get("freq") or raw.get("freq"))
    if freq and freq > 1e5:
        freq /= 1e6
    if not mode:
        mode = "vdl2" if raw.get("vdl2") else "hfdl" if raw.get("hfdl") else "inmarsat" if (raw.get("isu") or str((raw.get("app") or {}).get("name", "")).lower() == "jaero") else "iridium" if raw.get("iridium") else "acars"
    if not tail and not hexid and not flight:
        return None
    lat, lon, alt = _num(a.get("lat")), _num(a.get("lon")), _num(a.get("alt"))
    if (lat is None or lon is None) and s_lat is not None:
        lat, lon, alt = s_lat, s_lon, s_alt if alt is None else alt
    if lat is None or lon is None:
        p = position_in(text) if label in ("20", "21", "22", "23", "24", "25", "26", "27", "33", "H1", "4N", "4M", "5Z", "Q7", "") or "POS" in text.upper() else None
        lat, lon = p if p else (None, None)
    ts = _when(a.get("timestamp") or raw.get("timestamp") or a.get("t"))
    h = hashlib.sha1("|".join([tail, flight, label, str(a.get("block_id") or ""), str(a.get("msgno") or a.get("msg_num") or a.get("message_number") or ""), text]).encode()).hexdigest()
    return {"ts": ts, "station": station, "mode": mode[:8], "freq": round(freq, 4) if freq else None, "level": _num(a.get("level") or raw.get("level")), "label": label, "tail": tail, "flight": flight,
            "hex": hexid, "block_id": str(a.get("block_id") or a.get("blk_id") or "")[:2], "msgno": str(a.get("msgno") or a.get("msg_num") or a.get("message_number") or "")[:8], "text": text, "lat": lat, "lon": lon,
            "alt_ft": int(alt) if alt is not None else None, "hash": h}


def store(station, items):
    """Keeps the new messages; a message already stored (the same text on the same aircraft within the de-duplication window, heard by another station) only raises its heard-by count."""
    new = dup = bad = 0
    with db() as c:
        for raw in items[:500]:
            m = normalize(raw, station)
            STATE["received"] += 1
            if not m:
                bad += 1
                STATE["rejected"] += 1
                continue
            hit = c.execute("SELECT id, station FROM messages WHERE hash=%s AND ts > %s - make_interval(secs => %s) LIMIT 1", (m["hash"], m["ts"], int(cfg("dedupe_seconds")))).fetchone()
            if hit:
                c.execute("UPDATE messages SET heard_by = heard_by + 1 WHERE id=%s AND station <> %s", (hit["id"], station))
                dup += 1
                STATE["duplicates"] += 1
            else:
                c.execute("INSERT INTO messages (ts, station, mode, freq, level, label, tail, flight, hex, block_id, msgno, text, lat, lon, alt_ft, hash) VALUES (%(ts)s,%(station)s,%(mode)s,%(freq)s,%(level)s,%(label)s,%(tail)s,%(flight)s,%(hex)s,%(block_id)s,%(msgno)s,%(text)s,%(lat)s,%(lon)s,%(alt_ft)s,%(hash)s)", m)
                new += 1
                STATE["stored"] += 1
            c.execute("INSERT INTO station_counts (station, messages, last_heard) VALUES (%s,1,now()) ON CONFLICT (station) DO UPDATE SET messages = station_counts.messages + 1, last_heard = now()", (station,))
    return {"new": new, "duplicates": dup, "rejected": bad}


def pruner():
    while True:
        time.sleep(3600)
        try:
            with db() as c:
                c.execute("DELETE FROM messages WHERE ts < now() - make_interval(days => %s)", (int(cfg("keep_days")),))
        except Exception as e:
            print("prune failed:", str(e)[:100], flush=True)


def iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def row(r):
    out = {k: iso(v) for k, v in r.items() if k != "hash"}
    if "label" in out:
        out["label_text"] = LABELS.get(out["label"], "")
    return out


def dbsize():
    """The size of this service's whole database in bytes (shown on the portal's Admin > Usage)."""
    try:
        with db() as c:
            return int(c.execute("SELECT pg_database_size(current_database()) AS s").fetchone()["s"])
    except Exception:
        return None


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

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
                return self.send({"ok": True, "version": VERSION})
            if p == "/stats":
                with db() as c:
                    n = c.execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE ts > now() - interval '1 hour') AS hour, COUNT(*) FILTER (WHERE ts > now() - interval '24 hours') AS day, MAX(ts) AS last FROM messages").fetchone()
                    st = c.execute("SELECT station, messages, last_heard FROM station_counts ORDER BY messages DESC").fetchall()
                    modes = c.execute("SELECT mode, COUNT(*) AS n FROM messages WHERE ts > now() - interval '24 hours' GROUP BY mode ORDER BY n DESC").fetchall()
                    labels = c.execute("SELECT label, COUNT(*) AS n FROM messages WHERE ts > now() - interval '24 hours' AND label <> '' GROUP BY label ORDER BY n DESC LIMIT 12").fetchall()
                    hourly = c.execute("SELECT date_trunc('hour', ts) AS h, COUNT(*) AS n FROM messages WHERE ts > now() - interval '48 hours' GROUP BY 1 ORDER BY 1").fetchall()
                return self.send({"db_bytes": dbsize(), "version": VERSION, "messages": n["n"], "hour": n["hour"], "day": n["day"], "last": iso(n["last"]), "stations": [row(r) for r in st],
                                  "modes": {r["mode"]: r["n"] for r in modes}, "labels": [{"label": r["label"], "text": LABELS.get(r["label"], ""), "n": r["n"]} for r in labels],
                                  "hourly": [[iso(r["h"]), r["n"]] for r in hourly], "feed": dict(STATE, per_min=round(STATE["stored"] / max(1, (time.time() - STATE["since"]) / 60), 2)),
                                  "config": {k: cfg(k) for k in DEFAULTS}})
            if p == "/messages":
                where, args = ["true"], {"l": min(int(q.get("limit") or 100), 500), "o": max(0, int(q.get("offset") or 0))}
                for key, col in (("tail", "tail"), ("flight", "flight"), ("label", "label"), ("station", "station"), ("mode", "mode"), ("hex", "hex")):
                    if q.get(key):
                        where.append(f"upper({col}) = upper(%({key})s)")
                        args[key] = q[key].strip()
                if q.get("q"):
                    where.append("(text ILIKE %(q)s OR tail ILIKE %(q)s OR flight ILIKE %(q)s)")
                    args["q"] = "%" + q["q"].strip() + "%"
                if q.get("hours"):
                    where.append("ts > now() - make_interval(hours => %(h)s)")
                    args["h"] = min(int(q["hours"]), 24 * 90)
                if q.get("position") == "1":
                    where.append("lat IS NOT NULL")
                with db() as c:
                    rows = c.execute("SELECT * FROM messages WHERE " + " AND ".join(where) + " ORDER BY ts DESC LIMIT %(l)s OFFSET %(o)s", args).fetchall()
                return self.send({"messages": [row(r) for r in rows]})
            m = re.match(r"^/aircraft/([0-9a-fA-F]{6}|[A-Za-z0-9-]{2,10})$", p)
            if m:
                key = m.group(1)
                with db() as c:
                    rows = c.execute("SELECT * FROM messages WHERE (hex = lower(%(k)s) OR tail = upper(%(k)s)) ORDER BY ts DESC LIMIT %(l)s", {"k": key, "l": min(int(q.get("limit") or 30), 200)}).fetchall()
                return self.send({"messages": [row(r) for r in rows]})
            if p == "/positions":
                with db() as c:
                    rows = c.execute("SELECT DISTINCT ON (COALESCE(NULLIF(hex,''), tail)) ts, tail, flight, hex, lat, lon, alt_ft, label FROM messages WHERE lat IS NOT NULL AND ts > now() - make_interval(mins => %s) ORDER BY COALESCE(NULLIF(hex,''), tail), ts DESC", (min(int(q.get("minutes") or 60), 1440),)).fetchall()
                return self.send({"positions": [row(r) for r in rows]})
            return self.send({"error": "not found"}, 404)
        except Exception as e:
            return self.send({"error": str(e)[:200]}, 500)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path.rstrip("/")
        b = self.body_json()
        try:
            if p == "/ingest":
                station = re.sub(r"[^A-Za-z0-9._-]", "", str(b.get("station") or ""))[:40]
                if not station:
                    return self.send({"error": "no station"}, 400)
                items = b.get("messages")
                if items is None:
                    items = [b.get("message")] if b.get("message") else []
                return self.send(dict(ok=True, **store(station, items)))
            if p == "/config":
                for k, v in (b or {}).items():
                    set_cfg(k, v)
                return self.send({"ok": True})
            if p == "/delete":   # a takedown: every message of an aircraft (tail) or of a station
                with db() as c:
                    if b.get("tail"):
                        n = c.execute("DELETE FROM messages WHERE tail = upper(%s)", (str(b["tail"]),)).rowcount
                    elif b.get("station"):
                        n = c.execute("DELETE FROM messages WHERE station = %s", (str(b["station"]),)).rowcount
                    else:
                        return self.send({"error": "say a tail or a station"}, 400)
                return self.send({"ok": True, "deleted": n})
            return self.send({"error": "not found"}, 404)
        except Exception as e:
            return self.send({"error": str(e)[:200]}, 500)


def main():
    init_db()
    threading.Thread(target=pruner, daemon=True).start()
    print(f"arksignal-acars {VERSION} listening on {PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
