"""Small in-memory answer cache for read-mostly public data (rankings, totals, airport lists).

Each web worker keeps its own copy, so an answer can be at most `public_cache_seconds` old (Admin > Settings). Nothing an
administrator edits and then expects to see at once should go through here.
"""
import threading
import time

from .settings import cfg_int

_lock = threading.Lock()
_store = {}
MAX_ENTRIES = 2000  # bounds memory; not a user-facing tunable


def peek(key):
    """What is kept under a key right now, or None: never loads anything."""
    with _lock:
        hit = _store.get(key)
        return hit[1] if hit and hit[0] > time.time() else None


def cached(key, loader, seconds=None):
    ttl = cfg_int("public_cache_seconds") if seconds is None else seconds
    now = time.time()
    if ttl > 0:
        with _lock:
            hit = _store.get(key)
            if hit and hit[0] > now:
                return hit[1]
    value = loader()
    if ttl > 0:
        with _lock:
            if len(_store) >= MAX_ENTRIES:
                for k in [k for k, v in _store.items() if v[0] <= now] or list(_store)[: MAX_ENTRIES // 10]:
                    _store.pop(k, None)
            _store[key] = (now + ttl, value)
    return value
