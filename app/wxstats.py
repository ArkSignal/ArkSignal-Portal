"""Weather statistics over every source: a snapshot of every weather station that reports (members' stations, APRS stations, airport weather) is kept every few minutes in `wx_obs`, with a directory of the
stations (`wx_station_dir`: name, position, county, city and ZIP code worked out once). The Statistics page and `/api/weather/stats.json` read them with filters: which sources, which counties, cities, ZIP codes or
stations, and which period; each reading is kept in the stored imperial units. Kept for Settings > Weather Stations > Keep Weather Statistics (days)."""
import threading
import time
from datetime import datetime, timedelta, timezone

import psycopg

from .db import get_db
from .settings import cfg, cfg_int

METRICS = {"temp_f": "Temperature", "dew_f": "Dew Point", "hum": "Humidity", "press_in": "Pressure", "wind_mph": "Wind", "gust_mph": "Gust", "rain_day_in": "Rain Today", "uv": "UV Index", "cloud_pct": "Cloud Cover"}
PERIODS = {"6h": (6, "6 hours"), "24h": (24, "24 hours"), "3d": (72, "3 days"), "7d": (168, "7 days"), "30d": (720, "30 days")}
SOURCES = ("members", "aprs", "airport")


# ------------------------------------------------------------------ keeping the readings

def _nearest(lat, lon, kind, km):
    d = 1.0 if km >= 60 else 0.5
    return get_db().execute("SELECT name, state FROM places WHERE kind=%s AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s ORDER BY (lat-%s)^2 + ((lon-%s)*0.83)^2 LIMIT 1",
                            (kind, lat - d, lat + d, lon - d, lon + d, lat, lon)).fetchone()


def _directory_row(p):
    from . import counties
    c = counties.locate(p["lat"], p["lon"])
    city = _nearest(p["lat"], p["lon"], "place", 40)
    zc = _nearest(p["lat"], p["lon"], "zip", 20)
    from . import states
    return {"county": (c["base"] + " County" if c and not c["base"].endswith(("County", "Parish")) else (c["base"] if c else "")), "state": (c["state"] if c else states.state_of(p["lat"], p["lon"]).replace("-", "")),
            "city": city["name"] if city else "", "zip": zc["name"] if zc else ""}


def collect(app):
    """One snapshot of every station that reports now. Returns how many readings were kept."""
    from . import wxsources
    with app.app_context():
        pts = wxsources.points(SOURCES, "imperial")
        d = get_db()
        have = {(r["src"], r["sid"]) for r in d.execute("SELECT src, sid FROM wx_station_dir").fetchall()}
        n = 0
        for p in pts:
            r = p.get("r") or {}
            if not any(r.get(k) is not None for k in METRICS):
                continue
            if p.get("age") is not None and p["age"] > cfg_int("wx_stale_minutes") * 60:
                continue   # a station that has not reported lately has nothing new to keep
            if (p["src"], p["id"]) not in have:
                dr = _directory_row(p)
                d.execute("INSERT INTO wx_station_dir (src, sid, name, lat, lon, state, county, city, zip) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (src, sid) DO NOTHING",
                          (p["src"], p["id"], p["name"][:80], p["lat"], p["lon"], dr["state"], dr["county"], dr["city"], dr["zip"]))
                have.add((p["src"], p["id"]))
            d.execute("INSERT INTO wx_obs (src, sid, temp_f, dew_f, hum, press_in, wind_mph, gust_mph, wind_dir, rain_day_in, uv, cloud_pct) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (p["src"], p["id"], r.get("temp_f"), r.get("dew_f"), r.get("hum"), r.get("press_in"), r.get("wind_mph"), r.get("gust_mph"), r.get("wind_dir"), r.get("rain_day_in"), r.get("uv"), r.get("cloud_pct")))
            n += 1
        d.commit()
        return n


def prune(app):
    with app.app_context():
        days = cfg_int("wx_stats_keep_days")
    if days > 0:
        with psycopg.connect(app.config["DATABASE_URL"]) as conn:
            conn.execute("DELETE FROM wx_obs WHERE ts < now() - make_interval(days => %s)", (days,))
            conn.commit()


def start_collector(app):
    def loop():
        time.sleep(90)
        n = 0
        while True:
            with app.app_context():
                wait = max(2, cfg_int("wx_stats_minutes")) * 60
                on = cfg("wx_stats_enabled") == "1"
            if on:
                try:
                    with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock:
                        if lock.execute("SELECT pg_try_advisory_lock(727011)").fetchone()[0]:   # one web worker keeps the readings
                            try:
                                collect(app)
                                n += 1
                                if n % 24 == 0:
                                    prune(app)
                            finally:
                                lock.execute("SELECT pg_advisory_unlock(727011)")
                except Exception:
                    pass
            time.sleep(wait)
    threading.Thread(target=loop, name="wx-stats", daemon=True).start()


# ------------------------------------------------------------------ reading them

def _like(s):
    return "%" + (s or "").replace("%", "").replace("_", "") + "%"


def selected(args):
    """The directory rows that pass the filters in `args` (sources, counties, cities, zips, stations). Returns (where sql, params)."""
    where, params = [], []
    srcs = [s for s in (args.get("sources") or "members,aprs,airport").split(",") if s in SOURCES] or list(SOURCES)
    where.append("d.src = ANY(%s)"); params.append(srcs)
    area, ap = [], []
    for key, col in (("counties", "d.county"), ("cities", "d.city"), ("zips", "d.zip")):
        vals = [v.strip() for v in (args.get(key) or "").split("|") if v.strip()][:40]
        if vals:
            area.append(f"{col} = ANY(%s)"); ap.append(vals)
    for k in (args.get("near") or "").split("|")[:6]:   # a city or ZIP as a center: "lat,lon,miles"
        try:
            la, lo, mi = (float(x) for x in k.split(","))
        except ValueError:
            continue
        area.append("((d.lat-%s)^2 + ((d.lon-%s)*0.83)^2) <= %s"); ap += [la, lo, (mi / 69.0) ** 2]
    if area:
        where.append("(" + " OR ".join(area) + ")"); params += ap
    picks = [x for x in (args.get("stations") or "").split("|") if ":" in x][:200]
    if picks:
        where.append("(d.src || ':' || d.sid) = ANY(%s)"); params.append(picks)
    return " AND ".join(where), params


def stats(args):
    """{metric, period, selected, series: [{ts, min, avg, max, n}], stations: [{src, sid, name, city, county, zip, latest, min, avg, max, n}]} for the filters."""
    metric = args.get("metric") if args.get("metric") in METRICS else "temp_f"
    hours = PERIODS.get(args.get("period"), PERIODS["24h"])[0]
    w, params = selected(args)
    d = get_db()
    bucket = max(300, int(hours * 3600 / 80))
    series = d.execute(f"SELECT to_timestamp(floor(extract(epoch FROM o.ts) / %s) * %s) AS t, min(o.{metric}) AS lo, avg(o.{metric}) AS av, max(o.{metric}) AS hi, count(DISTINCT o.src || o.sid) AS n "
                       f"FROM wx_obs o JOIN wx_station_dir d ON d.src = o.src AND d.sid = o.sid WHERE o.ts > now() - make_interval(hours => %s) AND o.{metric} IS NOT NULL AND {w} GROUP BY 1 ORDER BY 1",
                       [bucket, bucket, hours] + params).fetchall()
    rows = d.execute(f"SELECT d.src, d.sid, d.name, d.city, d.county, d.zip, d.lat, d.lon, min(o.{metric}) AS lo, avg(o.{metric}) AS av, max(o.{metric}) AS hi, count(o.{metric}) AS n, "
                     f"(array_agg(o.{metric} ORDER BY o.ts DESC) FILTER (WHERE o.{metric} IS NOT NULL))[1] AS latest FROM wx_station_dir d JOIN wx_obs o ON o.src = d.src AND o.sid = d.sid "
                     f"WHERE o.ts > now() - make_interval(hours => %s) AND {w} GROUP BY d.src, d.sid, d.name, d.city, d.county, d.zip, d.lat, d.lon HAVING count(o.{metric}) > 0 ORDER BY d.name LIMIT 500",
                     [hours] + params).fetchall()
    return {"metric": metric, "label": METRICS[metric], "period": args.get("period") if args.get("period") in PERIODS else "24h", "hours": hours, "selected": len(rows),
            "series": [{"ts": r["t"].isoformat(), "min": r["lo"], "avg": r["av"], "max": r["hi"], "n": r["n"]} for r in series],
            "stations": [{"src": r["src"], "sid": r["sid"], "name": r["name"], "city": r["city"], "county": r["county"], "zip": r["zip"], "lat": r["lat"], "lon": r["lon"],
                          "latest": r["latest"], "min": r["lo"], "avg": r["av"], "max": r["hi"], "n": r["n"]} for r in rows]}


def areas():
    """The counties, cities and ZIP codes the kept stations are in, with how many stations each holds (for the pickers)."""
    d = get_db()
    out = {}
    for key, col in (("counties", "county"), ("cities", "city"), ("zips", "zip")):
        out[key] = [{"name": r["v"], "n": r["n"]} for r in d.execute(f"SELECT {col} AS v, count(*) AS n FROM wx_station_dir WHERE {col} <> '' GROUP BY {col} ORDER BY {col}").fetchall()]
    out["sources"] = {r["src"]: r["n"] for r in d.execute("SELECT src, count(*) AS n FROM wx_station_dir GROUP BY src").fetchall()}
    return out


def find_stations(q, limit=15):
    d = get_db()
    return [{"src": r["src"], "sid": r["sid"], "name": r["name"], "city": r["city"], "county": r["county"]} for r in d.execute(
        "SELECT src, sid, name, city, county FROM wx_station_dir WHERE sid ILIKE %s OR name ILIKE %s OR city ILIKE %s OR county ILIKE %s OR zip LIKE %s ORDER BY (upper(sid) = upper(%s)) DESC, name LIMIT %s",
        (_like(q), _like(q), _like(q), _like(q), (q or "").replace("%", "") + "%", q or "", limit)).fetchall()]


def coverage():
    """How much is kept: {readings, stations, first, last}."""
    r = get_db().execute("SELECT count(*) AS n, min(ts) AS a, max(ts) AS b FROM wx_obs").fetchone()
    s = get_db().execute("SELECT count(*) AS n FROM wx_station_dir").fetchone()
    return {"readings": r["n"], "stations": s["n"], "first": r["a"] and r["a"].isoformat(), "last": r["b"] and r["b"].isoformat()}
