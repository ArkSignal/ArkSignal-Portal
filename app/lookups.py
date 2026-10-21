"""Aircraft photo, aircraft make/model/owner and flight route lookups for the live map's details card.

The portal asks the outside services (addresses are settings) on the visitor's behalf and caches the answers, so
a popular aircraft costs one request per cache period and the visitor's browser never contacts those services about
which aircraft they looked at (the photo itself is the one exception: the browser loads it from the photo host).
"""
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, jsonify, make_response, request

from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("lookups", __name__)

_lock = threading.Lock()
_cache = {}  # (kind, key) -> (expires, value)
MAX_CACHE = 5000  # entries per worker; bounds memory, not a user-facing tunable


def _cached(kind, key, loader):
    now = time.time()
    with _lock:
        hit = _cache.get((kind, key))
        if hit and hit[0] > now:
            return hit[1]
    value = loader()
    ttl = cfg_int("lookup_cache_hours") * 3600 if value else cfg_int("lookup_miss_minutes") * 60
    with _lock:
        if len(_cache) >= MAX_CACHE:
            for k in [k for k, v in _cache.items() if v[0] <= now] or list(_cache)[: MAX_CACHE // 10]:
                _cache.pop(k, None)
        _cache[(kind, key)] = (now + ttl, value)
    return value


def _http(url, body=None):
    headers = {"User-Agent": cfg("lookup_user_agent"), "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=cfg_int("lookup_timeout_seconds")) as r:
            return json.loads(r.read(2_000_000))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _photo(hex_, reg="", make=""):
    """A photo of this very aircraft. The service can hold an old photo against a re-used address, so when the registration is
    known the photo page must be for that registration (its address says so); otherwise the registration search is tried."""
    reg = re.sub(r"[^A-Za-z0-9]", "", reg).lower()
    make = re.sub(r"[^a-z]", "", make.lower())[:4]    # the maker's first letters, for example "cess" or "boei"

    def pick(d):
        for p in (d or {}).get("photos") or []:
            link = (p.get("link") or "").lower()
            right_plane = not reg or f"/{reg}-" in link or f"/{reg}?" in link
            if right_plane and make and make not in link.replace("-", ""):
                right_plane = False        # same registration, different aircraft: it has been re-used
            if right_plane:
                return {"src": (p.get("thumbnail_large") or p.get("thumbnail") or {}).get("src"), "link": p.get("link"),
                        "photographer": p.get("photographer")}
        return None

    def load():
        base = cfg("photo_api_url").rstrip("/")
        for path in (["/hex/" + hex_] + (["/reg/" + reg] if reg else [])):
            try:
                found = pick(_http(base + path))
            except Exception:
                continue
            if found:
                return found
        return None
    return _cached("photo", (hex_, reg, make), load)


def _aircraft(hex_):
    def load():
        try:
            d = _http(cfg("aircraft_db_url").rstrip("/") + "/aircraft/" + hex_)
        except Exception:
            return None
        a = ((d or {}).get("response") or {}).get("aircraft")
        if not isinstance(a, dict):
            return None
        keep = ("type", "icao_type", "manufacturer", "registration", "registered_owner", "registered_owner_country_name")
        return {k: a.get(k) for k in keep if a.get(k)}
    return _cached("aircraft", hex_, load)


def _airport(a):
    return {"icao": a.get("icao") or a.get("icao_code"), "iata": a.get("iata") or a.get("iata_code"),
            "name": a.get("name"), "city": a.get("location") or a.get("municipality"),
            "lat": a.get("lat") if a.get("lat") is not None else a.get("latitude"), "lon": a.get("lon") if a.get("lon") is not None else a.get("longitude")}


def _route(callsign, lat, lon):
    """Route from the position-checked route service, falling back to the callsign database."""
    def load():
        try:
            d = _http(cfg("route_api_url"), {"planes": [{"callsign": callsign, "lat": lat or 0, "lng": lon or 0}]})
            r = (d or [None])[0]
            if r and r.get("_airports"):
                return {"airports": [_airport(a) for a in r["_airports"]], "plausible": bool(r.get("plausible", True)), "source": "route"}
        except Exception:
            pass
        try:
            d = _http(cfg("aircraft_db_url").rstrip("/") + "/callsign/" + callsign)
            fr = ((d or {}).get("response") or {}).get("flightroute")
            if fr and fr.get("origin") and fr.get("destination"):
                return {"airports": [_airport(fr["origin"]), _airport(fr["destination"])], "plausible": None, "source": "callsign",
                        "airline": (fr.get("airline") or {}).get("name")}
        except Exception:
            pass
        return None
    return _cached("route", callsign, load)


def _iata_for(icao):
    """Two-letter airline code for an ICAO airline code: the administrator's corrections first, then the airline lookup."""
    for pair in cfg("airline_iata_overrides").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip().upper() == icao and v.strip():
                return v.strip().upper()
    def load():
        try:
            d = _http(cfg("aircraft_db_url").rstrip("/") + "/airline/" + icao)
            rows = (d or {}).get("response")
            if isinstance(rows, list) and rows:
                return (rows[0].get("iata") or "").upper() or None
        except Exception:
            pass
        return None
    return _cached("airline", icao, load)


_inflight = set()   # airline codes whose logo is being fetched right now
_pool = None


def _store_logo(app, code):
    """Fetch one airline's logo and keep it (a miss is kept too, so it is not asked for again for a while). Runs in the background, never inside a page request."""
    try:
        with app.app_context():
            body, ctype = _fetch_logo(code)
            d = get_db()
            d.execute("INSERT INTO airline_logos (code, content, content_type, source, fetched_at) VALUES (%s,%s,%s,%s,now()) "
                      "ON CONFLICT (code) DO UPDATE SET content=EXCLUDED.content, content_type=EXCLUDED.content_type, source=EXCLUDED.source, fetched_at=now()", (code, body, ctype, cfg("airline_logo_url").strip()))
            d.commit()
    except Exception:
        pass
    finally:
        with _lock:
            _inflight.discard(code)


def queue_logo(app, code):
    """Start fetching a logo in the background (two at a time, at most a dozen waiting). False when it is already on its way or the queue is full."""
    global _pool
    with _lock:
        if code in _inflight or len(_inflight) >= 12:
            return False
        _inflight.add(code)
        if _pool is None:
            from concurrent.futures import ThreadPoolExecutor
            _pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="logo")
    _pool.submit(_store_logo, app, code)
    return True


def warm_logos(app, callsigns):
    """Called with the callsigns of the aircraft the hub reports: start fetching the logos of airlines not kept yet, a few at a time, so they are ready when someone opens the map (the map
    then never waits for an airline's logo to be fetched)."""
    if cfg("lookups_enabled") != "1" or not cfg("airline_logo_url").strip():
        return
    codes = sorted({m.group(1) for c in callsigns for m in [re.match(r"^([A-Z]{3})[0-9]", (c or "").strip().upper())] if m})
    if not codes:
        return
    try:
        have = {r["code"] for r in get_db().execute("SELECT code FROM airline_logos WHERE code = ANY(%s) AND fetched_at > now() - make_interval(days => %s) AND source = %s",
                                                    (codes, cfg_int("airline_logo_days"), cfg("airline_logo_url").strip())).fetchall()}
    except Exception:
        get_db().rollback()
        return
    for code in [c for c in codes if c not in have][:6]:
        queue_logo(app, code)


def _fetch_logo(icao):
    url = cfg("airline_logo_url").strip()
    if not url:
        return None, None
    iata = _iata_for(icao) if "{iata}" in url else ""
    if "{iata}" in url and not iata:
        return None, None
    req = urllib.request.Request(url.replace("{iata}", urllib.parse.quote(iata)).replace("{icao}", icao),
                                 headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "image/*"})
    try:
        with urllib.request.urlopen(req, timeout=cfg_int("lookup_timeout_seconds")) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            body = r.read(300_000)
        if ctype in ("image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml") and body:
            return body, ctype
    except Exception:
        pass
    return None, None


_logo_mem = {}   # code -> (expires, source, content, content type, etag): the logos served most are answered from memory, with no database read


def _logo_response(content, ctype, etag):
    if request.headers.get("If-None-Match") == etag:
        resp = make_response("", 304)
    else:
        resp = make_response(content)
        resp.headers["Content-Type"] = ctype
    resp.headers["Cache-Control"] = "public, max-age=604800"
    resp.headers["ETag"] = etag
    return resp


@bp.route("/api/airline-logo/<code>.png")
def airline_logo(code):
    """An airline's logo, fetched once from the configured source and kept in the database (and in memory while it is being asked for); 204 when there is none."""
    code = code.upper()
    if not re.fullmatch(r"[A-Z]{3}", code) or cfg("lookups_enabled") != "1":
        return "", 404
    hit = _logo_mem.get(code)
    if hit and hit[0] > time.time() and hit[1] == cfg("airline_logo_url").strip():
        return _logo_response(hit[2], hit[3], hit[4])
    d = get_db()
    days = cfg_int("airline_logo_days")
    source = cfg("airline_logo_url").strip()
    row = d.execute("SELECT content, content_type, fetched_at > now() - make_interval(days => %s) AS fresh, source FROM airline_logos WHERE code=%s",
                    (days, code)).fetchone()
    if not row or not row["fresh"] or row["source"] != source:   # a changed logo address means every kept logo is stale
        from flask import current_app
        queue_logo(current_app._get_current_object(), code)   # fetched in the background: the request never waits for an outside service
        if not row or not row["content"] or row["source"] != source:
            resp = make_response("", 204)   # not ready yet: the map shows the letter badge and asks again a little later
            resp.headers["Cache-Control"] = "public, max-age=15"
            return resp
        # a logo kept earlier is served while its refresh runs
    if not row["content"]:
        resp = make_response("", 204)   # no logo: not an error, so the browser console stays quiet; the map falls back to the letter badge
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp
    content = bytes(row["content"])
    etag = '"' + hashlib.md5(content, usedforsecurity=False).hexdigest()[:16] + '"'
    if len(_logo_mem) > 600:
        _logo_mem.clear()
    if row["fresh"] and row["source"] == source:   # a stale logo is served from the database each time, so its refresh is asked for
        _logo_mem[code] = (time.time() + 600, source, content, row["content_type"], etag)
    return _logo_response(content, row["content_type"], etag)


@bp.route("/api/route/<callsign>.json")
def route_only(callsign):
    """Just the route for a callsign (the live map's label fields use this, so a label does not fetch photos too)."""
    callsign = callsign.upper()
    if cfg("lookups_enabled") != "1" or not re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9]{0,4}", callsign):
        return jsonify({"route": None})
    try:
        lat, lon = float(request.args.get("lat", 0)), float(request.args.get("lon", 0))
    except ValueError:
        lat = lon = 0
    resp = jsonify({"route": _route(callsign, lat, lon)})
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


@bp.route("/api/aircraft-info/<hex_>.json")
def aircraft_info(hex_):
    """Photo, make/model/owner and route for one aircraft; every part is optional."""
    hex_ = hex_.lower()
    if not re.fullmatch(r"[0-9a-f]{6}", hex_):
        return jsonify({"error": "bad address"}), 400
    out = {"photo": None, "aircraft": None, "route": None}
    if cfg("lookups_enabled") == "1":
        callsign = request.args.get("callsign", "").strip().upper()
        out["photo"] = _photo(hex_, request.args.get("reg", ""), request.args.get("make", ""))
        out["aircraft"] = _aircraft(hex_)
        if re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9]{0,4}", callsign):
            try:
                lat, lon = float(request.args.get("lat", 0)), float(request.args.get("lon", 0))
            except ValueError:
                lat = lon = 0
            out["route"] = _route(callsign, lat, lon)
    resp = jsonify(out)
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp
