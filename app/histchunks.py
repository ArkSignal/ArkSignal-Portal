"""The hub's heatmap files, the way readsb writes them and tar1090 reads them: the portal's own Replay and Heatmap are built on these.

Where: <HUB_URL>/globe_history/YYYY/MM/DD/heatmap/NN.bin.ttf, NN = 2 * UTC hour + (minute >= 30), a gzip of 16-byte records (little endian int32 x 4):
  slice header   word0 = 0x0e7f7c9d (magic), word1 and word2 = the time (word1 * 4294967.296 + word2 / 1000 seconds, unsigned), word3 low 16 bits = milliseconds to the next slice
  position       word0 = ICAO address (24 bits) | bit 24 = non-ICAO address (shown with a leading ~) | bits 27-31 = how the position was received (SOURCES)
                 word1 = latitude * 1e6, word2 = longitude * 1e6, word3 low 16 bits = barometric altitude / 25 ft (signed; -123 on the ground, -124 unknown), high 16 bits = ground speed * 10 kt (-1 unknown)
  information    word1 >= 0x40000000: the squawk in the low 16 bits (as a decimal-looking number) and 8 bytes of call sign in words 2 and 3 (first byte 0 = none); belongs to the ICAO address in word0
The finished half hours never change, so they are kept (the newest few are asked for again). Nothing is stored on disk.
"""
import gzip
import struct
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from flask import current_app

from .settings import cfg_int

MAGIC = 0x0E7F7C9D
SOURCES = ["adsb_icao", "adsb_icao_nt", "adsr_icao", "tisb_icao", "adsc", "mlat", "other", "mode_s", "adsb_other", "adsr_other", "tisb_trackfile", "tisb_other", "mode_ac"]
_lock = threading.Lock()
_raw = OrderedDict()      # (day, index) -> decompressed bytes
_heat = OrderedDict()     # (day, index, zbucket, amin, amax) -> {(iy, ix): count}
_MISSING = {}             # (day, index) -> time of the last failed try (a missing half hour is not asked for again at once)
RETRY_SECONDS = 120       # protects the hub; not a user-facing setting


def chunk_index(ts):
    return 2 * ts.hour + (1 if ts.minute >= 30 else 0)


def chunk_url(day, index):
    return "/globe_history/%s/heatmap/%02d.bin.ttf" % (day.strftime("%Y/%m/%d"), index)


def _finished(day, index):
    """Whether this half hour is over (so its file is final)."""
    end = datetime(day.year, day.month, day.day, index // 2, 30 * (index % 2), tzinfo=timezone.utc) + timedelta(minutes=30, seconds=30)
    return datetime.now(timezone.utc) > end


def _keep(store, key, value, limit):
    with _lock:
        store[key] = value
        store.move_to_end(key)
        while len(store) > limit:
            store.popitem(last=False)


def fetch(day, index):
    """The decompressed file for one half hour (bytes), or None when the hub has none."""
    key = (day, index)
    with _lock:
        if key in _raw:
            _raw.move_to_end(key)
            return _raw[key]
        if time.time() - _MISSING.get(key, 0) < RETRY_SECONDS:
            return None
    hub = current_app.config.get("HUB_URL", "")
    if not hub:
        return None
    from . import typedb
    try:
        data = typedb._get(hub.rstrip("/") + chunk_url(day, index), 80_000_000)
    except Exception:
        with _lock:
            _MISSING[key] = time.time()
            if len(_MISSING) > 2000:
                _MISSING.clear()
        return None
    if len(data) % 16:
        return None
    if _finished(day, index):
        _keep(_raw, key, data, max(8, cfg_int("history_cache_chunks")))
    return data


def decode(data):
    """[{t: seconds, ival: seconds, pts: [(hex, lat, lon, alt_ft or None or 'ground', gs_kt or None, source)], info: {hex: (callsign, squawk)}}]"""
    n = len(data) // 16
    slices, cur = [], None
    for w0, w1, w2, w3 in struct.iter_unpack("<iiii", data[:n * 16]):
        if w0 == MAGIC:
            cur = {"t": (w2 & 0xFFFFFFFF) / 1000 + (w1 & 0xFFFFFFFF) * 4294967.296, "ival": (w3 & 0xFFFF) / 1000, "pts": [], "info": {}}
            slices.append(cur)
            continue
        if cur is None:
            continue
        hexid = "%06x" % (w0 & 0xFFFFFF)
        if w0 & 0x1000000:
            hexid = "~" + hexid
        if w1 >= 0x40000000:
            raw = struct.pack("<ii", w2, w3)
            call = raw.rstrip(b"\x00 ").decode("ascii", "ignore") if raw[0] else ""
            cur["info"][hexid] = (call, "%04d" % (w1 & 0xFFFF))
            continue
        alt = w3 & 0xFFFF
        if alt & 0x8000:
            alt -= 0x10000
        alt = "ground" if alt == -123 else None if alt == -124 else alt * 25
        gs = w3 >> 16
        gs = None if gs == -1 else gs / 10
        src = (w0 >> 27) & 0x1F
        cur["pts"].append((hexid, w1 / 1e6, w2 / 1e6, alt, gs, SOURCES[src] if src < len(SOURCES) else "unknown"))
    return slices


def cell_degrees(z):
    """The side of a heat map cell in degrees at a (whole) map zoom: about four screen pixels."""
    return 360.0 / (256 * (2 ** z)) * 4


def half_hour_heat(day, index, z, amin, amax):
    """{(row, column): positions} for one half hour on a grid whose cell is cell_degrees(z), counting positions between the altitude limits (feet; None = no limit; ground counts as 0)."""
    key = (day, index, z, amin, amax)
    with _lock:
        if key in _heat:
            _heat.move_to_end(key)
            return _heat[key]
    data = fetch(day, index)
    if data is None:
        return None
    cell, grid = cell_degrees(z), {}
    for sl in decode(data):
        for hexid, lat, lon, alt, gs, src in sl["pts"]:
            a = 0 if alt == "ground" else alt
            if amin is not None and (a is None or a < amin):
                continue
            if amax is not None and (a is None or a > amax):
                continue
            k = (int((lat + 90) // cell), int((lon + 180) // cell))
            grid[k] = grid.get(k, 0) + 1
    if _finished(day, index):
        _keep(_heat, key, grid, max(48, cfg_int("history_cache_chunks") * 2))
    return grid


def day_heat(day, z, amin, amax, since=None, until=None):
    """Cells for the half hours of one UTC day inside [since, until] (datetimes, UTC): ({(row, col): n}, half hours found, half hours asked)."""
    lo = chunk_index(since) if since and since.date() == day else 0
    hi = chunk_index(until) if until and until.date() == day else 47
    total, found = {}, 0
    for idx in range(lo, hi + 1):
        g = half_hour_heat(day, idx, z, amin, amax)
        if g is None:
            continue
        found += 1
        for k, n in g.items():
            total[k] = total.get(k, 0) + n
    return total, found, hi - lo + 1
