"""The forecast methods of the prediction service besides the statistical one (which lives in predictd.py):

  wind      our own flight model: the balloon climbs at the site's usual rate to its usual burst height and falls under its parachute, carried by forecast winds at each height
            (GFS pressure level winds from Open-Meteo; nothing here fetches by itself, predictd.py passes the fetched answers in so this file stays testable without a network).
  tawhiri   the open-source balloon predictor (CUSF Tawhiri); only the request and the reading of its answer are here. The address is a setting, empty until the site has one it may use
            (SondeHub asks to be contacted before its hosted predictor is used by another application; a copy run by the site itself works the same).

Heights are meters above sea level, speeds meters per second, positions degrees."""
import math
from datetime import datetime, timedelta, timezone

LEVELS = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300, 250, 200, 150, 100, 70, 50, 30, 20, 10]   # hPa, from near the ground to about 31 km
M_PER_DEG = 111195.0


def wind_query(lat, lon, hour):
    """The Open-Meteo request (as a dict of query values) for the winds of one forecast hour at one place."""
    names = ",".join(f"{v}_{lv}hPa" for lv in LEVELS for v in ("wind_speed", "wind_direction", "geopotential_height"))
    day = hour.strftime("%Y-%m-%d")
    return {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": names, "wind_speed_unit": "ms", "timezone": "UTC", "start_date": day, "end_date": day}


def parse_column(doc, hour):
    """[(height, u, v)] from low to high for the hour asked, or [] when the answer has no usable levels. u is the eastward and v the northward wind."""
    try:
        h = doc["hourly"]
        idx = h["time"].index(hour.strftime("%Y-%m-%dT%H:00"))
    except (KeyError, ValueError, TypeError, AttributeError):
        return []
    col = []
    for lv in LEVELS:
        try:
            s, d, z = h[f"wind_speed_{lv}hPa"][idx], h[f"wind_direction_{lv}hPa"][idx], h[f"geopotential_height_{lv}hPa"][idx]
        except (KeyError, IndexError, TypeError):
            continue
        if s is None or d is None or z is None:
            continue
        r = math.radians(d)   # the direction the wind comes FROM
        col.append((float(z), -s * math.sin(r), -s * math.cos(r)))
    col.sort()
    return col


def wind_at(col, alt):
    """(u, v) at a height: linear between the levels, the lowest or highest level beyond them."""
    if not col:
        return 0.0, 0.0
    if alt <= col[0][0]:
        return col[0][1], col[0][2]
    for (z0, u0, v0), (z1, u1, v1) in zip(col, col[1:]):
        if alt <= z1:
            f = (alt - z0) / (z1 - z0) if z1 > z0 else 0.0
            return u0 + (u1 - u0) * f, v0 + (v1 - v0) * f
    return col[-1][1], col[-1][2]


def descent_speed(alt, sea_ms):
    """Fall speed under a parachute: it grows as the air thins (density falls by about e every 7.5 km)."""
    return sea_ms * math.exp(max(0.0, alt) / 15000.0)


def fly(lat0, lon0, alt0, burst_alt, ascent_ms, descent_sea_ms, land_alt, col_up, col_down, step_up=60.0, step_down=30.0):
    """Step the balloon up through `col_up` and down through `col_down`. Returns (points, burst) where points are [seconds, lat, lon, alt] and burst is [lat, lon, alt]."""
    lat, lon, alt, t = lat0, lon0, alt0, 0.0
    pts = [[0, round(lat, 4), round(lon, 4), round(alt)]]
    ascent_ms = max(1.0, ascent_ms)

    def move(u, v, dt):
        nonlocal lat, lon
        lat += v * dt / M_PER_DEG
        lon += u * dt / (M_PER_DEG * max(0.05, math.cos(math.radians(lat))))
    while alt < burst_alt and t < 20000:
        dt = min(step_up, (burst_alt - alt) / ascent_ms)
        u, v = wind_at(col_up, alt + ascent_ms * dt / 2)
        move(u, v, dt)
        alt += ascent_ms * dt
        t += dt
        pts.append([round(t), round(lat, 4), round(lon, 4), round(alt)])
    burst = [round(lat, 4), round(lon, 4), round(alt)]
    while alt > land_alt and t < 40000:
        vd = descent_speed(alt, descent_sea_ms)
        dt = min(step_down, (alt - land_alt) / vd)
        u, v = wind_at(col_down, alt - vd * dt / 2)
        move(u, v, dt)
        alt -= vd * dt
        t += dt
        pts.append([round(t), round(lat, 4), round(lon, 4), round(max(alt, land_alt))])
    return pts, burst


def thin(pts, n=80):
    if len(pts) <= n:
        return pts
    step = (len(pts) - 1) / float(n - 1)
    return [pts[round(i * step)] for i in range(n)]


def tawhiri_query(lat, lon, when, alt, ascent_ms, burst_alt, descent_ms):
    """Tawhiri's standard profile request. It wants longitude 0 to 360 and a UTC launch time in RFC 3339."""
    return {"profile": "standard_profile", "launch_latitude": f"{lat:.4f}", "launch_longitude": f"{lon % 360:.4f}", "launch_datetime": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "launch_altitude": f"{alt:.0f}", "ascent_rate": f"{ascent_ms:.2f}", "burst_altitude": f"{burst_alt:.0f}", "descent_rate": f"{descent_ms:.2f}"}


def parse_tawhiri(doc, launch):
    """(points, burst) from Tawhiri's answer (a list of stages, each with a trajectory of latitude, longitude, altitude and datetime), or None."""
    try:
        stages = doc["prediction"]
        pts, burst = [], None
        for st in stages:
            for p in st["trajectory"]:
                lon = float(p["longitude"])
                lon = lon - 360 if lon > 180 else lon
                t = datetime.fromisoformat(str(p["datetime"]).replace("Z", "+00:00"))
                pts.append([round((t - launch).total_seconds()), round(float(p["latitude"]), 4), round(lon, 4), round(float(p["altitude"]))])
            if st.get("stage") == "ascent" and pts:
                burst = [pts[-1][1], pts[-1][2], pts[-1][3]]
        if len(pts) < 3:
            return None
        return pts, burst or max(pts, key=lambda q: q[3])[1:]
    except (KeyError, TypeError, ValueError):
        return None
