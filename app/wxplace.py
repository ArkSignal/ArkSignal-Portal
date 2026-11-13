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


def _grid_series(series):
    """{UTC hour 'YYYY-MM-DDTHH': millimeters in that hour} from an NWS gridpoint series: each block (validTime 'start/PT6H') is shared out evenly over its hours."""
    import re
    from datetime import datetime, timedelta
    out = {}
    for v in (series or {}).get("values", []):
        try:
            start, dur = v["validTime"].split("/")
            m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?)?", dur)
            n = int(m.group(1) or 0) * 24 + int(m.group(2) or 0)
            if n <= 0 or v["value"] is None:
                continue
            t0 = datetime.fromisoformat(start)
            for i in range(n):
                out[(t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H")] = float(v["value"]) / n
        except Exception:
            continue
    return out


def sun_times(lat, lon, day, tz):
    """(sunrise, sunset) of a date at a spot as aware datetimes in `tz` (the NOAA solar equations, good to about a minute); None for a side that does not happen (polar day or night)."""
    import math
    from datetime import datetime, timedelta, timezone
    jd = day.toordinal() + 1721424.5 + 0.5   # noon of the date, as the NOAA equations count days
    js = round(jd - 2451545.0) + 0.0008 - lon / 360.0
    m = math.radians((357.5291 + 0.98560028 * js) % 360)
    lam = math.radians((math.degrees(m) + 1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m) + 180 + 102.9372) % 360)
    transit = 2451545.0 + js + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    sd = math.sin(lam) * math.sin(math.radians(23.4397))
    cd = math.cos(math.asin(sd))
    c = (math.sin(math.radians(-0.833)) - math.sin(math.radians(lat)) * sd) / (math.cos(math.radians(lat)) * cd)
    if abs(c) > 1:
        return None, None
    w = math.degrees(math.acos(c)) / 360.0
    def at(j):
        return (datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=j - 2451545.0)).astimezone(tz)
    return at(transit - w), at(transit + w)


def _with_sun(hours, stamps, lat, lon):
    """The hourly list with a sunrise or sunset marker where each falls between two hours (like Weather Underground's strip)."""
    from datetime import timedelta
    if not hours:
        return hours
    tz, first, last = stamps[0].tzinfo, stamps[0], stamps[-1] + timedelta(hours=1)
    events = []
    d = first.date() - timedelta(days=1)
    while d <= last.date():
        for kind, t in zip(("Sunrise", "Sunset"), sun_times(lat, lon, d, tz)):
            if t is not None and first <= t < last:
                events.append((t, kind))
        d += timedelta(days=1)
    out, ev = [], sorted(events)
    for h, s in zip(hours, stamps):
        while ev and ev[0][0] < s + timedelta(hours=0):
            t, kind = ev.pop(0)
            out.append({"sun": kind, "date": t.strftime("%Y-%m-%d"), "label": t.strftime("%I:%M %p").lstrip("0")})
        out.append(h)
    for t, kind in ev:
        out.append({"sun": kind, "date": t.strftime("%Y-%m-%d"), "label": t.strftime("%I:%M %p").lstrip("0")})
    return out


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
            hours, totals = [], {}
            try:   # the hour-by-hour forecast; the daily one is still shown if this does not answer
                from datetime import datetime, timezone
                stamps = []
                try:   # how much is forecast to fall: the NWS gridpoint data (4 to 6 hour blocks, shared out over their hours)
                    grid = _get_json(pt["forecastGridData"])["properties"]
                    amounts = {k: _grid_series(grid.get(k)) for k in ("quantitativePrecipitation", "snowfallAmount", "iceAccumulation")}
                except Exception:
                    amounts = {}
                for p in _get_json(pt["forecastHourly"])["properties"].get("periods", [])[:156]:
                    t = datetime.fromisoformat(p["startTime"])   # the place's own clock, as the NWS writes it
                    snow = any(w in p["shortForecast"].lower() for w in ("snow", "flurr", "sleet", "wintry", "ice"))
                    stamps.append(t)
                    hk = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
                    amt = {a: round(amounts.get(k, {}).get(hk, 0.0), 3) for a, k in (("liq", "quantitativePrecipitation"), ("snowmm", "snowfallAmount"), ("icemm", "iceAccumulation"))} if amounts else {}
                    hours.append(dict(amt, **{"date": t.strftime("%Y-%m-%d"), "label": t.strftime("%I %p").lstrip("0"), "temp": p["temperature"], "unit": p["temperatureUnit"],
                                  "wind": f"{p.get('windSpeed', '')} {p.get('windDirection', '')}".strip(), "short": p["shortForecast"], "daytime": p["isDaytime"],
                                  "icon": (icon_from_text(p["shortForecast"], p["isDaytime"]) or ("", ""))[0], "rain": ((p.get("probabilityOfPrecipitation") or {}).get("value")), "snow": snow}))
                totals = {}
                for h in hours:
                    if "liq" in h:
                        d = totals.setdefault(h["date"], {"liq": 0.0, "snowmm": 0.0, "icemm": 0.0})
                        for k in d:
                            d[k] += h[k]
                hours = _with_sun(hours, stamps, la, lo)
            except Exception:
                hours, totals = [], {}
            return {"office": pt.get("cwa", ""), "place": ((pt.get("relativeLocation") or {}).get("properties") or {}).get("city", ""), "hours": hours, "totals": totals,
                    "updated": fc.get("updateTime", ""), "periods": [{"date": str(p.get("startTime", ""))[:10], "name": p["name"], "temp": p["temperature"], "unit": p["temperatureUnit"], "wind": f"{p.get('windSpeed', '')} {p.get('windDirection', '')}".strip(),
                                                                       "short": p["shortForecast"], "detail": p["detailedForecast"], "daytime": p["isDaytime"], "icon": (icon_from_text(p["shortForecast"], p["isDaytime"]) or ("", ""))[0], "rain": ((p.get("probabilityOfPrecipitation") or {}).get("value"))} for p in fc.get("periods", [])[:14]]}
        except Exception:
            return None
    return cached(("nws_fc", la, lo), build, 1800)
