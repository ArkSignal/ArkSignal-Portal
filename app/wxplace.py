"""Weather for any place: a ZIP code, a city, an airport or an address becomes a weather page made from the readings this site already holds (members' stations, APRS stations and airport reports
near it), with the National Weather Service's forecast for the spot beside it. Nothing here is stored: it is worked out from the latest readings, the kept history (wx_obs, see wxstats.py) and,
when Settings > Weather Stations allows, one request to the NWS forecast service per place (kept for a while)."""
import json
import math
import re
import urllib.parse
import urllib.request

from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int

KEYS = ("temp_f", "dew_f", "feels_f", "hum", "press_in", "wind_mph", "gust_mph", "rain_day_in", "rain_rate_in", "uv", "solar", "cloud_pct", "vis_mi")


def km(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(x)))


def resolve(q):
    """What a visitor typed -> [{label, lat, lon, kind}], best first: an airport code, then places (cities, ZIP areas) and, failing those, addresses."""
    q = " ".join((q or "").split())[:120]
    if len(q) < 2:
        return []
    d, out = get_db(), []
    code = re.sub(r"[^A-Za-z0-9]", "", q).upper()
    if re.fullmatch(r"[A-Z0-9]{3,4}", code) and not q.isdigit():
        for r in d.execute("SELECT ident, name, lat, lon, municipality, region FROM airports WHERE upper(ident)=%s OR upper(icao_code)=%s OR upper(iata_code)=%s OR upper(local_code)=%s ORDER BY scheduled_service DESC LIMIT 3",
                           (code, code, code, code)).fetchall():
            out.append({"label": f"{r['name']} ({r['ident']})", "lat": r["lat"], "lon": r["lon"], "kind": "airport", "ident": r["ident"]})
    from . import places
    m = re.match(r"^(.*\S)\s+([A-Za-z]{2})$", q)
    pq = (m.group(1) + ", " + m.group(2)) if m and "," not in q and not q.isdigit() else q   # "Conway AR" is "Conway, AR"
    for r in places.search(pq, limit=6):
        if r["kind"] != "county":
            out.append({"label": r["label"], "lat": r["lat"], "lon": r["lon"], "kind": r["kind"]})
    if len(out) < 2 and (any(c.isdigit() for c in q) or "," in q):
        try:
            from . import addrlookup
            cands, _e = addrlookup.lookup(q, (float(cfg("map_lat")), float(cfg("map_lon"))), ["census", "photon"])
            for c in cands[:4]:
                out.append({"label": c["label"], "lat": c["lat"], "lon": c["lon"], "kind": "address"})
        except Exception:
            pass
    return out


def nearby(lat, lon, radius_mi):
    """Every reporting station of every source within the radius, nearest first: [{dist_mi, ...the map point}]."""
    from . import wxsources
    pts = cached(("wxplace_pts",), lambda: wxsources.points(("members", "aprs", "airport"), "imperial"), 30)
    out = []
    for p in pts:
        if p.get("lat") is None or p.get("lon") is None:
            continue
        mi = km(lat, lon, p["lat"], p["lon"]) / 1.609344
        if mi <= radius_mi:
            out.append(dict(p, dist_mi=mi))
    return sorted(out, key=lambda x: x["dist_mi"])


def combine(stations):
    """The place's conditions from the reporting stations near it: each reading is the average of the stations that carry it, the nearer counting more; wind is averaged as a vector. {readings, n, cond}."""
    live = [s for s in stations if s.get("online") and s.get("r")]
    r = {}
    for k in KEYS:
        num = den = 0.0
        for s in live:
            v = s["r"].get(k)
            if v is None:
                continue
            w = 1 / (s["dist_mi"] ** 2 + 4)
            num += w * v; den += w
        if den:
            r[k] = num / den
    sx = sy = sw = 0.0
    for s in live:
        v, dr = s["r"].get("wind_mph"), s["r"].get("wind_dir")
        if v is None or dr is None:
            continue
        w = 1 / (s["dist_mi"] ** 2 + 4)
        sx += w * v * math.sin(math.radians(dr)); sy += w * v * math.cos(math.radians(dr)); sw += w
    if sw:
        r["wind_dir"] = (math.degrees(math.atan2(sx, sy)) + 360) % 360
    cond = next((s["cond"] for s in live if s.get("cond")), None)
    return {"readings": r, "n": len(live), "cond": cond}


def trend(lat, lon, radius_mi):
    """rising, falling or steady: the pressure near the place now against about three hours ago, from the kept history; '' when there is too little of it."""
    from . import wxstats
    s = wxstats.stats({"near": f"{lat},{lon},{radius_mi}", "metric": "press_in", "period": "6h"})["series"]
    if len(s) < 4:
        return ""
    now = s[-1]["avg"]
    # the bucket closest to three hours earlier
    from datetime import datetime, timedelta
    t_now = datetime.fromisoformat(s[-1]["ts"])
    then = min(s, key=lambda x: abs((datetime.fromisoformat(x["ts"]) - (t_now - timedelta(hours=3))).total_seconds()))
    if abs((datetime.fromisoformat(then["ts"]) - (t_now - timedelta(hours=3))).total_seconds()) > 5400:
        return ""
    diff = now - then["avg"]
    return "rising" if diff > 0.03 else "falling" if diff < -0.03 else "steady"


def icon_from_text(text, daytime=True):
    """(icon name, words) for the words of a forecast period, so a place with no sky reading still shows a picture; None when nothing fits."""
    t = (text or "").lower()
    if "thunder" in t:
        return ("storm", text)
    if any(w in t for w in ("rain", "shower", "drizzle", "snow", "sleet")):
        return ("rain", text)
    if "fog" in t or "mist" in t or "haze" in t:
        return ("fog", text)
    if "partly" in t or "mostly sunny" in t or "mostly clear" in t:
        return ("partly" if daytime else "moon", text)
    if "sunny" in t or "clear" in t:
        return ("sunny" if daytime else "moon", text)
    if "cloud" in t or "overcast" in t:
        return ("cloud", text)
    if "wind" in t:
        return ("windy", text)
    return None


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/geo+json, application/json"})
    with urllib.request.urlopen(req, timeout=cfg_int("lookup_timeout_seconds") + 4) as r:
        return json.loads(r.read(2_000_000).decode("utf-8", "replace"))


def forecast(lat, lon):
    """The National Weather Service forecast for the spot: {office, periods: [{name, temp, unit, wind, short, detail, icon, daytime}]} or None (a place outside the NWS area, or the service did not answer)."""
    if cfg("place_forecast_enabled") != "1":
        return None
    la, lo = round(lat, 3), round(lon, 3)

    def build():
        base = cfg("nws_api_url").rstrip("/")
        try:
            pt = _get_json(f"{base}/points/{la},{lo}")["properties"]
            fc = _get_json(pt["forecast"])["properties"]
            return {"office": pt.get("cwa", ""), "place": ((pt.get("relativeLocation") or {}).get("properties") or {}).get("city", ""),
                    "updated": fc.get("updateTime", ""), "periods": [{"name": p["name"], "temp": p["temperature"], "unit": p["temperatureUnit"], "wind": f"{p.get('windSpeed', '')} {p.get('windDirection', '')}".strip(),
                                                                       "short": p["shortForecast"], "detail": p["detailedForecast"], "daytime": p["isDaytime"], "icon": (icon_from_text(p["shortForecast"], p["isDaytime"]) or ("", ""))[0], "rain": ((p.get("probabilityOfPrecipitation") or {}).get("value"))} for p in fc.get("periods", [])[:14]]}
        except Exception:
            return None
    return cached(("nws_fc", la, lo), build, 1800)
