"""MLAT server status for the portal.

The MLAT server (the `adsb-mlat` container, see deploy/mlat) writes clients.json into its work folder every 15 seconds; the portal mounts that
folder read-only. That file also holds each station's exact coordinates and source address, so this module copies out only the health numbers
and never returns coordinates, addresses or the raw document. Stations are matched to portal feeders by the UUID their mlat-client sends,
or failing that by a user name equal to the MLAT User Name saved on the station, or finally to its Station ID.
"""
import json
import os
import time

from .db import get_db

_cache = {"t": 0.0, "rows": None}


def _path():
    return os.path.join(os.environ.get("MLAT_WORK_DIR", "/mlat"), "clients.json")


def read_clients(max_age=10):
    """Connected MLAT clients as a list of health rows, or None when the server's folder is not available. Cached briefly."""
    now = time.time()
    if _cache["rows"] is not None and now - _cache["t"] < max_age:
        return _cache["rows"]
    try:
        p = _path()
        age = now - os.path.getmtime(p)
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        _cache.update(t=now, rows=None)
        return None
    rows = []
    for user, c in doc.items():
        rows.append({"user": str(user), "uuid": c.get("uuid") or "", "message_rate": c.get("message_rate", 0),
                     "peer_count": c.get("peer_count", 0), "outlier_percent": c.get("outlier_percent", 0.0),
                     "bad_sync_timeout": c.get("bad_sync_timeout", 0), "stale": age > 120})
    _cache.update(t=now, rows=rows)
    return rows


def status_by_feeder(max_age=10):
    """({feeder uuid: health row}, [unmatched rows]) for every connected MLAT client; (None, []) when the server's folder is unavailable."""
    rows = read_clients(max_age)
    if rows is None:
        return None, []
    feeders = get_db().execute("SELECT uuid, lower(station_id) AS sid, lower(mlat_user) AS mu FROM feeders").fetchall()
    by_uuid = {f["uuid"].lower(): f["uuid"] for f in feeders}
    by_mu = {f["mu"]: f["uuid"] for f in feeders if f["mu"]}
    by_sid = {f["sid"]: f["uuid"] for f in feeders if f["sid"]}
    matched, unmatched = {}, []
    for r in rows:
        key = by_uuid.get(r["uuid"].lower()) or by_mu.get(r["user"].lower()) or by_sid.get(r["user"].lower())
        if key:
            matched[key] = r
        else:
            unmatched.append(r)
    return matched, unmatched


def describe(r):
    """(state, sentence) for a health row: state is 'working', 'syncing' or 'problem'. None row -> ('off', ...)."""
    if r is None:
        return "off", "Not connected to MLAT."
    if r["stale"]:
        return "problem", "The MLAT server has stopped reporting, so this status is out of date."
    if r["bad_sync_timeout"]:
        return "problem", ("Connected, but the server has paused this station's MLAT because its clock sync is bad. "
                           "Check that the station's latitude, longitude and altitude are exact and that the receiver is not overloaded.")
    if r["peer_count"] == 0:
        return "syncing", ("Connected, waiting for other stations. MLAT needs three or more stations that hear the same aircraft, "
                           "so it starts once neighbours are connected.")
    return "working", f"Working: synchronised with {r['peer_count']} other station{'s' if r['peer_count'] != 1 else ''}, {r['message_rate']} messages/s sent."


_ac_cache = {"t": 0.0, "doc": None}


def aircraft_counts(hexid, max_age=10):
    """What the MLAT server says about one aircraft: how many stations hear it, and how many MLAT positions it has produced. Counts only."""
    now = time.time()
    if _ac_cache["doc"] is None or now - _ac_cache["t"] > max_age:
        try:
            with open(os.path.join(os.environ.get("MLAT_WORK_DIR", "/mlat"), "aircraft.json"), encoding="utf-8") as f:
                _ac_cache.update(t=now, doc=json.load(f))
        except (OSError, ValueError):
            _ac_cache.update(t=now, doc={})
    a = (_ac_cache["doc"] or {}).get(hexid.upper())
    if not a:
        return None
    return {"stations_hearing": a.get("tracking", 0), "mlat_positions": a.get("mlat_result_count", 0), "allowed": bool(a.get("allow_mlat", 1)),
            "seconds_ago": a.get("elapsed_seen")}


def _sync_doc():
    try:
        with open(os.path.join(os.environ.get("MLAT_WORK_DIR", "/mlat"), "sync.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _quality(st):
    """Sync quality of one peer pairing, from the server's own thresholds: [syncs, error us, drift, bad syncs, jumped, outlier %, age s, peer's peers]."""
    n, err, _drift, bad, jumped, outl, age = st[:7]
    if bad > 0 or age > 20 or jumped or err > 2.4 or outl > 35:
        return "poor"
    if err > 1.2 or outl > 20:
        return "fair"
    return "good"


def _km(a, b):
    import math
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def network():
    """The public MLAT picture: named stations with their approximate (privacy-applied) places, MLAT state and synchronised peers.
    Returns None when the MLAT server is not reporting. Never includes the server's own coordinates, addresses or user names."""
    from . import feeders as fd
    from .settings import cfg
    if cfg("mlat_enabled") != "1":
        return None
    matched, _ = status_by_feeder()
    sync = _sync_doc()
    if matched is None or sync is None:
        return None
    rows = get_db().execute("SELECT uuid, station_id, name, lat, lon, privacy FROM feeders WHERE owner_id IS NOT NULL AND station_id IS NOT NULL AND NOT banned").fetchall()
    byuuid = {r["uuid"]: r for r in rows}
    # MLAT user name -> feeder uuid (the same matching as everywhere)
    user_to_uuid = {r["user"]: u for u, r in matched.items()}
    out = {}
    for user, uuid in user_to_uuid.items():
        f = byuuid.get(uuid)
        if f is None:
            continue
        state, _text = describe(matched[uuid])
        loc = fd.public_location(f)
        out[user] = {"id": f["station_id"], "name": f["name"] or "", "state": state, "lat": loc[0] if loc else None, "lon": loc[1] if loc else None,
                     "approximate": (f["privacy"] or "general") != "exact", "radius_m": __import__("app.privacy", fromlist=["x"]).radius_m(f["privacy"] or "general"), "peers": []}
    for user, s in sync.items():
        me = out.get(user)
        if not me:
            continue
        for peer, st in (s.get("peers") or {}).items():
            other = out.get(peer)
            if not other or len(st) < 7:
                continue
            d = _km((me["lat"], me["lon"]), (other["lat"], other["lon"])) if me["lat"] is not None and other["lat"] is not None else None
            me["peers"].append({"id": other["id"], "quality": _quality(st), "km": round(d, 1) if d is not None else None})
    stations = []
    for me in out.values():
        km = [p["km"] for p in me["peers"] if p["km"] is not None]
        me["peer_count"] = len(me["peers"])
        me["closest_km"] = min(km) if km else None
        me["farthest_km"] = max(km) if km else None
        stations.append(me)
    stations.sort(key=lambda s: s["id"].lower())
    return {"stations": stations, "working": sum(1 for s in stations if s["state"] == "working")}


def station_state(uuid):
    """{'state', 'text', 'row'} for one feeder when MLAT is switched on in Settings, else None."""
    from .settings import cfg
    if cfg("mlat_enabled") != "1":
        return None
    matched, _ = status_by_feeder()
    if matched is None:
        return {"state": "unavailable", "text": "The MLAT server is not reporting yet.", "row": None}
    row = matched.get(uuid)
    state, text = describe(row)
    return {"state": state, "text": text, "row": row}


def working_uuids():
    """Set of feeder uuids whose MLAT is currently working (public flag; no numbers)."""
    from .settings import cfg
    if cfg("mlat_enabled") != "1":
        return set()
    matched, _ = status_by_feeder()
    return {u for u, r in (matched or {}).items() if describe(r)[0] == "working"}


def feeder_status(uuid):
    """Health row for one feeder, or None (server unavailable or station not connected to MLAT)."""
    matched, _ = status_by_feeder()
    return (matched or {}).get(uuid)
