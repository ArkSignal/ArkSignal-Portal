"""Which feeder heard which aircraft.

readsb's aircraft.json does not say which receiver an aircraft came from, and its API's receiver filter is not built into the
hub image. But the hub's "beast reduce plus" output (the port that carries --net-receiver-id) does: every message is preceded by
a 0x1a 0xe3 frame holding the first 8 bytes of the sending feeder's UUID. This module keeps a connection to that port, reads the
aircraft address out of each Mode S message, and remembers for a short time which receivers heard which aircraft. The live map and
the station pages use it to show only the aircraft one or more chosen feeders are hearing.

Every web worker runs its own reader (the state is in memory and small), so any worker can answer a filtered request.
"""
import socket
import threading
import time
from urllib.parse import urlparse

from .settings import cfg, cfg_int

_lock = threading.Lock()
_seen = {}          # receiver id (16 hex chars) -> {aircraft address (6 hex chars): last time heard}
_seen_pos = {}      # same, but only for messages that carry a position (used for range)
_state = {"connected": False, "last_data": 0.0, "error": "", "messages": 0}

BEAST_LENGTH = {0x31: 9, 0x32: 14, 0x33: 21, 0xE3: 8}   # bytes that follow the type byte
ESC = 0x1A


def receiver_key(uuid):
    """The 16 hex characters readsb sends for a feeder: the first half of its UUID."""
    return (uuid or "").replace("-", "").lower()[:16]


class Parser:
    """Incremental decoder for the Beast byte stream (0x1a escapes, doubled 0x1a inside data)."""

    def __init__(self):
        self.buf = bytearray()
        self.receiver = None

    def feed(self, data):
        """Yield (receiver id, aircraft address, carries a position) for every message that carries an address."""
        self.buf += data
        out, buf, i, n = [], self.buf, 0, len(self.buf)
        while i < n:
            if buf[i] != ESC:
                i += 1
                continue
            if i + 1 >= n:
                break
            t = buf[i + 1]
            need = BEAST_LENGTH.get(t)
            if need is None:                       # not a frame start (for example a stray doubled escape)
                i += 1 if t != ESC else 2
                continue
            j, body, waiting = i + 2, bytearray(), False
            while len(body) < need and j < n:      # collect `need` data bytes, undoing the 0x1a doubling
                if buf[j] == ESC:
                    if j + 1 >= n:
                        waiting = True             # cannot tell yet whether this is a doubled escape
                        break
                    if buf[j + 1] != ESC:          # a new frame started early: this one was cut short
                        break
                    j += 1
                body.append(buf[j])
                j += 1
            if len(body) < need:
                if waiting or j >= n:
                    break                          # wait for more data
                i = j                              # damaged frame: resynchronise at the next escape
                continue
            if t == 0xE3:
                self.receiver = bytes(body).hex()
            elif t in (0x32, 0x33) and self.receiver:
                msg = body[7:]
                df = msg[0] >> 3
                if df in (11, 17, 18) and len(msg) >= 4:   # the address is in the clear for these formats
                    out.append((self.receiver, msg[1:4].hex(), df in (17, 18) and len(msg) > 4 and (9 <= msg[4] >> 3 <= 18 or 20 <= msg[4] >> 3 <= 22)))
            i = j
        del buf[:i]
        return out


def _remember(pairs, now):
    with _lock:
        for rid, icao, has_pos in pairs:
            _seen.setdefault(rid, {})[icao] = now
            if has_pos:
                _seen_pos.setdefault(rid, {})[icao] = now
        _state["messages"] += len(pairs)
        _state["last_data"] = now


def _prune(now, keep):
    with _lock:
        for rid in list(_seen_pos):
            d = _seen_pos[rid]
            for icao in [a for a, t in d.items() if now - t > keep]:
                del d[icao]
            if not d:
                del _seen_pos[rid]
        for rid in list(_seen):
            d = _seen[rid]
            for icao in [a for a, t in d.items() if now - t > keep]:
                del d[icao]
            if not d:
                del _seen[rid]


def aircraft_heard_by(uuids):
    """Set of aircraft addresses heard recently by any of the given feeders (UUIDs)."""
    keep = cfg_int("receiver_seen_seconds")
    now = time.time()
    found = set()
    with _lock:
        for u in uuids:
            for icao, t in _seen.get(receiver_key(u), {}).items():
                if now - t <= keep:
                    found.add(icao)
    return found


def feeders_hearing(icao):
    """Receiver ids (16 hex chars) that heard this aircraft recently."""
    keep = cfg_int("receiver_seen_seconds")
    now = time.time()
    with _lock:
        return [rid for rid, d in _seen.items() if now - d.get(icao, 0) <= keep]


def status():
    with _lock:
        age = time.time() - _state["last_data"] if _state["last_data"] else None
        return {"connected": _state["connected"], "receivers": len(_seen), "seconds_since_data": age, "error": _state["error"]}


def available():
    """True when the receiver feed is connected and has delivered data recently."""
    s = status()
    return bool(s["connected"] and s["seconds_since_data"] is not None and s["seconds_since_data"] < 30)


def _target(app):
    """(host, port) of the hub's receiver feed, or None when switched off."""
    with app.app_context():
        if cfg("hub_beast_enabled") != "1":
            return None
        host = cfg("hub_beast_host").strip() or (urlparse(app.config.get("HUB_URL", "")).hostname or "")
        return (host, cfg_int("hub_beast_port")) if host else None


def start_receivers(app):
    def loop():
        time.sleep(5)
        while True:
            target = _target(app)
            if not target:
                time.sleep(15)
                continue
            parser = Parser()
            try:
                with socket.create_connection(target, timeout=10) as s:
                    s.settimeout(30)
                    _state.update(connected=True, error="")
                    last_prune = time.time()
                    while True:
                        data = s.recv(65536)
                        if not data:
                            break
                        now = time.time()
                        pairs = parser.feed(data)
                        if pairs:
                            _remember(pairs, now)
                        if now - last_prune > 10:
                            with app.app_context():
                                _prune(now, cfg_int("receiver_seen_seconds") * 2)
                            last_prune = now
            except Exception as e:
                _state["error"] = f"{type(e).__name__}"
            _state["connected"] = False
            with app.app_context():
                wait = cfg_int("receiver_retry_seconds")
            time.sleep(wait)
    threading.Thread(target=loop, name="receivers", daemon=True).start()
    start_range_sampler(app)


# ---------------------------------------------------------------- range sampling (see rangestats.py)

def _recent_positions(now, window):
    with _lock:
        return {rid: [icao for icao, t in d.items() if now - t <= window] for rid, d in _seen_pos.items()}


def sample_ranges(locations, aircraft, now=None, window=5, max_nm=450):
    """Pair what each station just heard with where the aircraft are. `locations`: {receiver id: (uuid, lat, lon)};
    `aircraft`: {hex: (lat, lon, altitude)}. Returns the number of samples taken."""
    from datetime import datetime, timezone
    from . import rangestats
    now = now or time.time()
    day = datetime.fromtimestamp(now, timezone.utc).date()
    n = 0
    for rid, icaos in _recent_positions(now, window).items():
        loc = locations.get(rid)
        if not loc:
            continue
        uuid, lat, lon = loc
        for icao in icaos:
            a = aircraft.get(icao)
            if not a:
                continue
            nm, bearing = rangestats.distance_bearing(lat, lon, a[0], a[1])
            if 0.5 < nm <= max_nm:
                rangestats.add_sample(uuid, day, bearing, nm, a[2])
                n += 1
    return n


def start_range_sampler(app):
    import psycopg
    from . import rangestats

    def loop():
        time.sleep(20)
        locations, loc_at, flushed_at, pruned_at = {}, 0.0, time.time(), 0.0
        while True:
            try:
                from .live import hub_aircraft
                with app.app_context():
                    now = time.time()
                    if now - loc_at > 60:       # station locations change rarely
                        from .db import get_db
                        locations = {receiver_key(r["uuid"]): (r["uuid"], r["lat"], r["lon"]) for r in get_db().execute(
                            "SELECT uuid, lat, lon FROM feeders WHERE lat IS NOT NULL AND lon IS NOT NULL AND NOT banned").fetchall()}
                        loc_at = now
                    data, _err = hub_aircraft()
                    if data and locations and available():
                        aircraft = {a["hex"]: (a["lat"], a["lon"], a.get("alt")) for a in data["aircraft"]
                                    if a.get("lat") is not None and (a.get("seen_pos") is None or a["seen_pos"] < 10)}
                        sample_ranges(locations, aircraft, now, cfg_int("range_position_seconds"), cfg_int("range_max_nm"))
                    if now - flushed_at >= 60:
                        flushed_at = now
                        samples = rangestats.take_samples()      # every worker measures; only one writes
                        with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock_conn:
                            if samples and lock_conn.execute("SELECT pg_try_advisory_lock(727010)").fetchone()[0]:
                                try:
                                    with psycopg.connect(app.config["DATABASE_URL"]) as conn:
                                        rangestats.save(conn, samples)
                                        if now - pruned_at > 86400:
                                            rangestats.prune(conn, cfg_int("range_history_days"))
                                            pruned_at = now
                                finally:
                                    lock_conn.execute("SELECT pg_advisory_unlock(727010)")
                    wait = cfg_int("range_sample_seconds")
            except Exception:
                wait = 5
            time.sleep(wait)
    threading.Thread(target=loop, name="range-sampler", daemon=True).start()
