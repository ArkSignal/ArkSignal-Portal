"""Aircraft type data (long make and model, ICAO type description such as L2J, wake class) from the hub's own tar1090 database.

tar1090 picks each aircraft's icon from the type description and shows the long name in its details; readsb's aircraft.json
carries neither, so the portal reads the same type table tar1090 loads and adds it to the live aircraft feed.
"""
import gzip
import json
import re
import threading
import time
import urllib.request

_lock = threading.Lock()
_types = {"at": 0.0, "data": {}, "tried": 0.0}
RETRY_SECONDS = 300  # after a failed download, wait this long before trying again


def _get(url, limit=6_000_000):
    with urllib.request.urlopen(url, timeout=10) as r:
        raw = r.read(limit)
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def types(hub, max_age_hours):
    """{type code: [long name, description, wake class]}; {} when the hub's table cannot be read."""
    now = time.time()
    with _lock:
        fresh = _types["data"] and now - _types["at"] < max_age_hours * 3600
        if fresh or now - _types["tried"] < RETRY_SECONDS and not _types["data"]:
            return _types["data"]
        _types["tried"] = now
    try:
        base = hub.rstrip("/") + "/"
        folder = re.search(r'databaseFolder\s*=\s*"([^"]+)"', _get(base, 2_000_000).decode("utf-8", "replace")).group(1)
        data = json.loads(_get(base + folder + "/icao_aircraft_types2.js"))
        with _lock:
            _types.update(at=now, data=data)
    except Exception:
        pass
    return _types["data"]
