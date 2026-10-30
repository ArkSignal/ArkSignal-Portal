"""Which stations send UAT (978 MHz) to the hub.

A station with a 978 MHz radio (dump978) sends its UAT aircraft through the ordinary feed line, so the hub's own lists do not say who they came from. But readsb marks every message that came from
a UAT decoder with a fixed marker in the place of the Beast time stamp (FF 00 4D 4C 41 55, the same trick MLAT results use with ...54; written by the UAT converter, uat2esnt), and the hub's receiver feed
(receivers.py) names the sending station in front of every message. So a message with that marker, after a station's receiver id, is that station's UAT. The receiver feed keeps the last time and the
aircraft each station delivered this way; this module writes them down every half minute (feeder_uat) so every page can answer "is this station sending UAT" without keeping a connection.

What it can and cannot say: a station that recently delivered UAT is plainly feeding it. A station with none may have no 978 MHz radio, or may have one with no UAT aircraft in range. Only the first
statement is ever made as fact; the second is worded as "none came through". Nothing here shows where a station is."""
import threading
import time
from datetime import datetime, timedelta, timezone

from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int

MARKER = bytes.fromhex("ff004d4c4155")
_lock = threading.Lock()
_seen = {}   # receiver id (16 hex characters) -> {aircraft address: last time a UAT message came}


def note(hits, now):
    """hits: [(receiver id, aircraft address)] from the receiver feed."""
    if not hits:
        return
    with _lock:
        for rid, icao in hits:
            _seen.setdefault(rid, {})[icao] = now


def flush(now):
    """Write what each station delivered in the last hour (needs an app context): its newest time, its first time and how many aircraft."""
    keep = 3600
    with _lock:
        for rid in list(_seen):
            d = _seen[rid]
            for icao in [a for a, t in d.items() if now - t > keep]:
                del d[icao]
            if not d:
                del _seen[rid]
        snap = {rid: (max(d.values()), len(d)) for rid, d in _seen.items()}
    if not snap:
        return 0
    db = get_db()
    by_key = {u["uuid"].replace("-", "").lower()[:16]: u["uuid"] for u in db.execute("SELECT uuid FROM feeders").fetchall()}
    n = 0
    for rid, (last, count) in snap.items():
        uuid = by_key.get(rid)
        if not uuid:
            continue
        ts = datetime.fromtimestamp(last, timezone.utc)
        db.execute("INSERT INTO feeder_uat (feeder_uuid, first_seen, last_seen, aircraft_hour) VALUES (%s,%s,%s,%s) ON CONFLICT (feeder_uuid) DO UPDATE SET "
                   "last_seen = GREATEST(feeder_uat.last_seen, EXCLUDED.last_seen), aircraft_hour = EXCLUDED.aircraft_hour", (uuid, ts, ts, count))
        n += 1
    db.commit()
    return n


def _rows():
    return cached(("feeder_uat",), lambda: {r["feeder_uuid"]: r for r in get_db().execute("SELECT feeder_uuid, first_seen, last_seen, aircraft_hour FROM feeder_uat").fetchall()}, 15)


def _window():
    return timedelta(minutes=max(1, cfg_int("uat_recent_minutes")))


def receiving_uuids():
    """Stations that delivered UAT within the window set in Settings > Feeders (public flag, no numbers)."""
    if cfg("uat_enabled") != "1":
        return set()
    cut = datetime.now(timezone.utc) - _window()
    return {u for u, r in _rows().items() if r["last_seen"] >= cut}


def _ago(then):
    s = int((datetime.now(timezone.utc) - then).total_seconds())
    if s < 90:
        return "a minute ago"
    if s < 5400:
        return f"{round(s / 60)} minutes ago"
    if s < 172800:
        return f"{round(s / 3600)} hours ago"
    return f"{round(s / 86400)} days ago"


def station_state(uuid):
    """{'state': 'receiving' | 'earlier' | 'none', 'text', 'last_seen', 'aircraft'} for one station, or None when the UAT pages are switched off."""
    if cfg("uat_enabled") != "1":
        return None
    r = _rows().get(uuid)
    if not r:
        return {"state": "none", "last_seen": None, "aircraft": 0,
                "text": "No UAT has come through this station yet. That is normal without a 978 MHz radio, and also when no UAT aircraft are in range."}
    if r["last_seen"] >= datetime.now(timezone.utc) - _window():
        return {"state": "receiving", "last_seen": r["last_seen"], "aircraft": r["aircraft_hour"],
                "text": f"UAT came through this station {_ago(r['last_seen'])}: {r['aircraft_hour']} aircraft in the last hour."}
    return {"state": "earlier", "last_seen": r["last_seen"], "aircraft": 0,
            "text": f"UAT last came through this station {_ago(r['last_seen'])}. If the 978 MHz radio is running, it may simply have no UAT aircraft in range right now."}


def network_count():
    return len(receiving_uuids())


def start(app):
    def loop():
        time.sleep(20)
        while True:
            try:
                with app.app_context():
                    flush(time.time())
            except Exception:
                pass
            time.sleep(30)
    threading.Thread(target=loop, name="uatfeed", daemon=True).start()
