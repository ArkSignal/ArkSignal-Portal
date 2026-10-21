"""Weather from every source in one format, for the Weather Map and the statistics: the members' own stations (app/weather.py), the APRS weather stations (CWOP and amateur stations heard on APRS-IS) and airport weather
(METAR from the Aviation Weather Center). Each station comes out as the same kind of point the members' stations are (readings in the stored imperial units under `r`), tagged `src` = members, aprs or airport.

APRS carries the airports' own observations too (call signs such as KXNA are the airport's METAR relayed through CWOP/MADIS), so the same weather would be drawn twice. An APRS station whose call sign is an airport's
code is an airport station: it is left out of the APRS source while the airport source is on, and any APRS view can hide airport stations with its own filter. Positions of APRS and airport stations are public data."""
import math
import threading
import time
from datetime import datetime, timezone

from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int, map_area

SOURCES = (("members", "Members' stations"), ("aprs", "APRS stations"), ("airport", "Airport weather"))
_codes = {"at": 0.0, "set": set(), "names": {}}
_lock = threading.Lock()


def airport_codes():
    """{airport code: name} for every airport with a four-letter ICAO-style code, kept for ten minutes."""
    with _lock:
        if time.time() - _codes["at"] > 600 or not _codes["set"]:
            names = {}
            for r in get_db().execute("SELECT ident, icao_code, gps_code, name FROM airports WHERE length(ident) = 4 OR icao_code <> '' OR gps_code <> ''").fetchall():
                for code in (r["ident"], r["icao_code"], r["gps_code"]):
                    if code and len(code) == 4:
                        names[code.upper()] = r["name"]
            _codes.update(at=time.time(), set=set(names), names=names)
        return _codes["names"]


def is_airport_call(call):
    """True for an APRS call sign that is an airport's code (KXNA, KLIT-1 ...): the airport's own weather relayed over APRS."""
    base = (call or "").split("-")[0].upper()
    return len(base) == 4 and base in airport_codes()


def _age(ts):
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
        return int((datetime.now(timezone.utc) - t).total_seconds())
    except (ValueError, TypeError):
        return None


def _imperial(temp_c=None, wind_ms=None, gust_ms=None, press_hpa=None, dew_c=None, hum=None):
    r = {}
    if temp_c is not None:
        r["temp_f"] = round(temp_c * 9 / 5 + 32, 1)
    if dew_c is not None:
        r["dew_f"] = round(dew_c * 9 / 5 + 32, 1)
    if hum is not None:
        r["hum"] = round(hum)
    if wind_ms is not None:
        r["wind_mph"] = round(wind_ms * 2.236936, 1)
    if gust_ms is not None:
        r["gust_mph"] = round(gust_ms * 2.236936, 1)
    if press_hpa is not None:
        r["press_in"] = round(press_hpa * 0.02953, 2)
    return r


def _point(units, sid, name, lat, lon, r, wind_dir, src, href, age, owner, last):
    from .weather import compass, show
    stale = cfg_int("wx_stale_minutes") * 60
    t, tu = show("temp_f", r.get("temp_f"), units)
    w, wu = show("wind_mph", r.get("wind_mph"), units)
    if wind_dir is not None:
        r = dict(r, wind_dir=wind_dir)
    return {"id": sid, "name": name, "lat": lat, "lon": lon, "online": age is not None and age <= stale, "approx": False, "radius_m": 0, "href": href,
            "temp": t if r.get("temp_f") is not None else "", "temp_unit": tu, "hum": r.get("hum"), "wind": w if r.get("wind_mph") is not None else "", "wind_unit": wu,
            "wind_dir": compass(wind_dir), "rain": "", "rain_unit": show("rain_day_in", 0, units)[1], "temp_f": r.get("temp_f"), "cond": None, "age": age, "r": r,
            "owner": owner, "kind": src, "src": src, "last": last}


def aprs_points(units="imperial", include_airports=False, bbox=None):
    """The APRS weather stations as map points. Airport stations are left out unless include_airports (the airport source draws them)."""
    from . import aprs as ap
    out = []
    for w in ap.weather():
        if w.get("lat") is None or w.get("lon") is None:
            continue
        if not include_airports and is_airport_call(w["call"]):
            continue
        if bbox and not (bbox[0] <= w["lat"] <= bbox[2] and bbox[1] <= w["lon"] <= bbox[3]):
            continue
        r = _imperial(w.get("temp_c"), w.get("wind_ms"), w.get("gust_ms"), w.get("pressure_hpa"), None, w.get("humidity"))
        if w.get("rain_24h_mm") is not None:
            r["rain_day_in"] = round(w["rain_24h_mm"] * 0.03937, 2)   # APRS reports the last 24 hours, the closest thing to "today"
        out.append(_point(units, w["call"], w["call"], w["lat"], w["lon"], r, w.get("wind_dir"), "aprs", "/aprs/" + w["call"].replace("/", "~"), _age(w.get("ts")), "APRS", w.get("ts")))
    return out


def _dewpoint_hum(t, d):
    if t is None or d is None:
        return None
    a, b = 17.625, 243.04
    return max(0, min(100, 100 * math.exp((a * d) / (b + d)) / math.exp((a * t) / (b + t))))


import threading as _th
_lock_tiles = _th.Lock()
TILE = 4   # degrees: airport weather is asked for in tiles of this size, so a view that moves a little reuses what is kept


def _metars(bbox=None, info=None):
    """Airport weather over the area asked for (a view: south, west, north, east, in tiles of whole degrees; at most 24 by 16 degrees) or, with none, over the area the Weather Map opens on."""
    from .live import metars_for
    if not bbox:
        area = map_area("weather_map_area") or [33.0, -94.62, 36.5, -89.64]
        s, w, n, e = (math.floor(area[0]), math.floor(area[1]), math.ceil(area[2]), math.ceil(area[3]))
        return metars_for(s, w, n, e) or {}
    s, w, n, e = bbox
    cy, cx = (s + n) / 2, (w + e) / 2
    s, n = max(s, cy - 8), min(n, cy + 8); w, e = max(w, cx - 12), min(e, cx + 12)   # a view of a whole continent is limited to its middle
    tiles = [(ts, tw) for ts in range(int(math.floor(s / TILE)) * TILE, int(math.ceil(n / TILE)) * TILE, TILE) for tw in range(int(math.floor(w / TILE)) * TILE, int(math.ceil(e / TILE)) * TILE, TILE)]
    from flask import current_app
    from .cache import peek
    from .settings import cfg_int as _ci
    app = current_app._get_current_object()
    out, pending = {}, 0
    for t in tiles:
        got = peek(("metars", t[0], t[1], t[0] + TILE, t[1] + TILE))
        if got is not None:
            out.update(got); continue
        pending += 1
        _load_tile_in_background(app, t)   # a tile nobody has asked for yet is fetched in the background: the map gets what is kept now and asks again
    if info is not None:
        info["pending"] = info.get("pending", 0) + pending
    return out


_inflight_tiles = set()


def _load_tile_in_background(app, t):
    import threading
    key = (t[0], t[1])
    with _lock_tiles:
        if key in _inflight_tiles or len(_inflight_tiles) >= 12:
            return
        _inflight_tiles.add(key)

    def run():
        try:
            from .live import metars_for
            with app.app_context():
                metars_for(t[0], t[1], t[0] + TILE, t[1] + TILE)
        except Exception:
            pass
        finally:
            with _lock_tiles:
                _inflight_tiles.discard(key)
    threading.Thread(target=run, name="metar-tile", daemon=True).start()


def airport_points(units="imperial", bbox=None, info=None):
    """Airport weather (METAR) as map points, one per reporting airport."""
    if cfg("metar_api_url").strip() == "" or cfg("airport_weather_default") not in ("0", "1"):
        return []
    names = airport_codes()
    pos = {r["code"]: (r["lat"], r["lon"]) for r in get_db().execute("SELECT upper(coalesce(nullif(icao_code, ''), ident)) AS code, lat, lon FROM airports WHERE length(coalesce(nullif(icao_code, ''), ident)) = 4").fetchall()}
    out = []
    for code, m in _metars(bbox, info).items():
        if code not in pos:
            continue
        if bbox and not (bbox[0] <= pos[code][0] <= bbox[2] and bbox[1] <= pos[code][1] <= bbox[3]):
            continue   # the tiles kept are bigger than the view
        temp, dewp = m.get("temp"), m.get("dewp")
        alt = m.get("altim")
        press = (alt if alt and alt > 500 else (alt / 0.02953 if alt else None))   # the service gives hectopascals; an altimeter in inches is converted back first
        wspd = m.get("wspd")
        r = _imperial(temp, wspd * 0.514444 if wspd is not None else None, (m.get("wgst") or 0) * 0.514444 if m.get("wgst") else None, press, dewp, _dewpoint_hum(temp, dewp))
        obs = m.get("obs")
        age = int(time.time() - obs) if isinstance(obs, (int, float)) else None
        wd = m.get("wdir") if isinstance(m.get("wdir"), (int, float)) else None
        out.append(_point(units, code, names.get(code, code), pos[code][0], pos[code][1], r, wd, "airport", "/airports/" + code, age, "Airport weather (METAR)",
                          datetime.fromtimestamp(obs, timezone.utc).isoformat() if isinstance(obs, (int, float)) else None))
        out[-1]["cat"] = m.get("cat")
        cover = {"SKC": 0, "CLR": 0, "NSC": 0, "FEW": 18, "SCT": 44, "BKN": 75, "OVC": 100, "OVX": 100}
        sky = [cover.get((c.get("cover") or "").upper()) for c in (m.get("clouds") or [])]
        sky = [x for x in sky if x is not None]
        if sky or m.get("clouds") == []:
            out[-1]["r"]["cloud_pct"] = max(sky) if sky else 0   # the thickest layer reported: few 18, scattered 44, broken 75, overcast 100
    return out


def points(sources, units="imperial", bbox=None, info=None):
    """Points for the chosen sources (a set of members, aprs, airport), over a view (south, west, north, east) or everywhere. APRS never repeats an airport that the airport source already shows."""
    from . import weather as wx
    sources = set(sources) & {k for k, _l in SOURCES}
    out = []
    if "members" in sources:
        for p in wx.map_points(units):
            p["src"] = "members"
            if bbox and not (bbox[0] <= p["lat"] <= bbox[2] and bbox[1] <= p["lon"] <= bbox[3]):
                continue
            out.append(p)
    if "airport" in sources:
        out += airport_points(units, bbox, info)
    if "aprs" in sources:
        out += aprs_points(units, include_airports="airport" not in sources, bbox=bbox)   # with the airport source off, the airports APRS carries are shown as APRS stations rather than lost
    return out
