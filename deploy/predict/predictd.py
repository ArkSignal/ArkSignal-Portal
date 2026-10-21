"""arksignal-predict: the prediction and archive service (its own container, its own database).

What it does
- Imports SondeHub's open data archive (AWS Open Data bucket `sondehub-history`, CC BY-SA 2.0) as one small summary per flight: where it launched, burst, and landed.
  Source: `launchsites/<site>/<yyyy>/<mm>/<dd>/<serial>.json` (three frames per flight, from 2022) and, if switched on, the worldwide `date/<yyyy>/<mm>/<dd>/` files
  (from 2018) filtered to our area. The import is throttled, resumable (progress is kept per site and month) and goes from the newest month back as far as it can.
- Predicts where the balloons of FUTURE launches will land: for each launch site it works out the usual launch times, then the expected landing area from past flights of
  the same site, season and launch hour (drift by month and time of day, burst height and flight time by season), blended with the most recent flights (winds persist
  for a day or two). No weather forecast data is used. Every prediction is stored and scored against the real flight later, so the error history is public.
- Serves one flight's full track on demand from the archive (`serial/<serial>.json.gz`), one request per call.

Everything is configured through the database `config` table (the portal sets it through POST /config) and environment defaults; nothing is hard-coded to one site.
The portal reaches this service at an address set in its Settings and keeps working, with a note, when the service is away. Terms: DATA_SOURCES_TERMS.md.
"""
import gzip
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import forecastmethods as fm
import gfsdata
from psycopg.rows import dict_row

DB_URL = os.environ.get("DATABASE_URL", "postgresql://predict:predict@predict-db:5432/predict")
ARCHIVE = os.environ.get("ARCHIVE_URL", "https://sondehub-history.s3.amazonaws.com").rstrip("/")
PORT = int(os.environ.get("PORT", "8470"))
UA = os.environ.get("USER_AGENT", "ArkSignal-predict (https://arksignal.net)")
VERSION = open("/BUILD_INFO").read().split("version:")[1].split()[0] if os.path.exists("/BUILD_INFO") and "version:" in open("/BUILD_INFO").read() else "dev"

DEFAULTS = {
    "sites": "72340,72248,72235,72440,72357,72249,72327,72230,72240,74560,72456",   # Little Rock, Shreveport, Jackson, Springfield MO, Norman, Fort Worth, Nashville, Birmingham, Lake Charles, Lincoln IL, Topeka
    "since": "2022-01-01",
    "rate_per_second": "4",
    "date_mode": "0",                 # 1 = also scan the worldwide date/ files from 2018 for flights in our boxes (many requests)
    "boxes": "30.0,-100.5,39.5,-84.5",   # south,west,north,east of the area the date/ scan keeps
    "horizon_hours": "72",
    "lookback_days": "1825",
    "season_days": "45",
    "hour_window": "2",
    "min_samples": "8",
    "persist_hours": "48",
    "landed_below_m": "4000",
    "auto_import": "1",              # the import runs by itself once a day
    "methods": "statistical,wind",   # the forecast methods worked out and kept for every coming launch (statistical, wind, tawhiri)
    "default_method": "statistical", # the one used when a request does not name a method
    "wind_url": "https://api.open-meteo.com/v1/gfs",   # forecast winds by height (GFS through Open-Meteo)
    "wind_cache_hours": "3",         # a wind column is kept this long (about a quarter degree, one hour) so the same one is never asked for twice
    "descent_ms": "6.0",             # fall speed of a sonde parachute near the ground
    "tawhiri_url": "",               # a Tawhiri predictor this site may use; empty = that method is not offered
    "wind_horizon_hours": "36",      # wind and Tawhiri forecasts are only worked out for launches this close (winds are not worth asking for further ahead)
    "wind_refresh_hours": "6",       # and kept this long before being worked out again (the weather models themselves update every six hours)
    "wind_max_per_day": "1500",      # most wind requests this service makes in a day (Open-Meteo's free allowance is far above this)
    "wind_source": "auto",           # auto = our own GFS data while it is fresh, else Open-Meteo; gfs = only ours; openmeteo = only Open-Meteo
    "gfs_enabled": "1",              # read NOAA's GFS model ourselves (winds by height and the surface forecast)
    "gfs_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl",   # NOAA NOMADS GRIB filter: cuts the model files to a box and to the fields asked for
    "gfs_box": "27.0,-100.0,43.0,-84.0",   # south,west,north,east of the area kept (Arkansas and a wide margin)
    "gfs_hours": "72",               # forecast hours kept, every three hours
    "gfs_levels": "1000,975,950,925,900,850,800,750,700,650,600,550,500,450,400,350,300,250,200,150,100,70,50,30,20,10",   # pressure levels (hPa) of the winds
    "gfs_refresh_minutes": "60",     # how often a new cycle is looked for (a cycle comes every six hours)
    "gfs_keep_cycles": "2",
    "gfs_max_age_hours": "18",       # data older than this is not used for winds (the source is broken or away)
    "gfs_pause_seconds": "1.0",      # between two requests to NOAA
    "gfs_timeout_seconds": "60",
    "gfs_user_agent": "ArkSignal-predict (https://arksignal.net; reads NOAA GFS)",
}

_cfg_cache = {}
_cfg_lock = threading.Lock()
IMPORT = {"running": False, "stop": False, "current": "", "requests": 0, "flights": 0, "error": "", "started": None, "finished": None, "months_done": 0, "months_total": 0}


def db():
    return psycopg.connect(DB_URL, row_factory=dict_row, autocommit=True)


def init_db():
    for attempt in range(60):
        try:
            with db() as c:
                c.execute("""CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
                c.execute("""CREATE TABLE IF NOT EXISTS flights (
                    serial TEXT PRIMARY KEY, site TEXT NOT NULL DEFAULT '', type TEXT NOT NULL DEFAULT '', subtype TEXT NOT NULL DEFAULT '', manufacturer TEXT NOT NULL DEFAULT '',
                    launch_ts TIMESTAMPTZ, launch_lat DOUBLE PRECISION, launch_lon DOUBLE PRECISION, launch_alt DOUBLE PRECISION,
                    burst_ts TIMESTAMPTZ, burst_lat DOUBLE PRECISION, burst_lon DOUBLE PRECISION, burst_alt DOUBLE PRECISION,
                    land_ts TIMESTAMPTZ, land_lat DOUBLE PRECISION, land_lon DOUBLE PRECISION, land_alt DOUBLE PRECISION,
                    lost BOOLEAN NOT NULL DEFAULT FALSE, source TEXT NOT NULL DEFAULT 'archive', imported TIMESTAMPTZ NOT NULL DEFAULT now())""")
                c.execute("CREATE INDEX IF NOT EXISTS flights_site_launch ON flights (site, launch_ts DESC)")
                c.execute("CREATE TABLE IF NOT EXISTS tracks (serial TEXT PRIMARY KEY, pts TEXT NOT NULL, fetched TIMESTAMPTZ NOT NULL DEFAULT now())")
                c.execute("""CREATE TABLE IF NOT EXISTS import_months (site TEXT NOT NULL, ym TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', keys INT NOT NULL DEFAULT 0,
                    done INT NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', updated TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (site, ym))""")
                c.execute("""CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY, site TEXT NOT NULL, launch_ts TIMESTAMPTZ NOT NULL, made TIMESTAMPTZ NOT NULL DEFAULT now(), lat DOUBLE PRECISION, lon DOUBLE PRECISION,
                    r50_km DOUBLE PRECISION, r90_km DOUBLE PRECISION, burst_alt DOUBLE PRECISION, flight_min DOUBLE PRECISION, samples INT, recent INT,
                    actual_serial TEXT, actual_lat DOUBLE PRECISION, actual_lon DOUBLE PRECISION, err_km DOUBLE PRECISION, scored TIMESTAMPTZ,
                    UNIQUE (site, launch_ts))""")
                c.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS data TEXT")
                c.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS method TEXT NOT NULL DEFAULT 'statistical'")
                c.execute("ALTER TABLE predictions DROP CONSTRAINT IF EXISTS predictions_site_launch_ts_key")
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS predictions_site_launch_method ON predictions (site, launch_ts, method)")
                gfsdata.init(c)
            return
        except Exception as e:
            print("waiting for the database:", str(e)[:100], flush=True)
            time.sleep(2)
    raise SystemExit("the database never answered")


def cfg(key):
    with _cfg_lock:
        if key in _cfg_cache and time.time() - _cfg_cache[key][1] < 20:
            return _cfg_cache[key][0]
    try:
        with db() as c:
            r = c.execute("SELECT value FROM config WHERE key=%s", (key,)).fetchone()
        v = r["value"] if r else os.environ.get("PREDICT_" + key.upper(), DEFAULTS[key])
    except Exception:
        v = DEFAULTS[key]
    with _cfg_lock:
        _cfg_cache[key] = (v, time.time())
    return v


def set_cfg(key, value):
    if key not in DEFAULTS:
        raise ValueError("unknown setting " + key)
    with db() as c:
        c.execute("INSERT INTO config (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, str(value)))
    with _cfg_lock:
        _cfg_cache.pop(key, None)


def fnum(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def km_between(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1.0, a)))


def offset_km(la0, lo0, la, lo):
    """(east, north) kilometers from the first point to the second."""
    return (lo - lo0) * math.cos(math.radians(la0)) * 111.195, (la - la0) * 111.195


def move_km(la0, lo0, east, north):
    return la0 + north / 111.195, lo0 + east / (111.195 * math.cos(math.radians(la0)))


# ------------------------------------------------------------------ archive access

OUT_DAYS = {}   # host -> day -> [requests, bytes] since this service started (the AWS archive, the wind forecasts, a Tawhiri server)


def note_out(host, nbytes):
    d = OUT_DAYS.setdefault(host, {}).setdefault(time.strftime("%Y-%m-%d", time.gmtime()), [0, 0])
    d[0] += 1
    d[1] += nbytes


def http_json(url, params=None, timeout=30):
    """A JSON answer from a forecast source; counted. Raises on failure."""
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(full, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(20_000_000)
    note_out(urllib.parse.urlsplit(url).hostname or "", len(body))
    return json.loads(body.decode("utf-8"))


def http_get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read(200_000_000)
    IMPORT["requests"] += 1
    IMPORT["bytes"] = IMPORT.get("bytes", 0) + len(body)
    note_out(urllib.parse.urlsplit(url).hostname or "", len(body))
    return body if binary else body.decode("utf-8")


def list_keys(prefix):
    """Every key under a prefix (S3 anonymous listing with a prefix is allowed; the bare bucket listing is not)."""
    keys, token = [], None
    while True:
        q = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            q["continuation-token"] = token
        root = ET.fromstring(http_get(ARCHIVE + "/?" + urllib.parse.urlencode(q)))
        ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
        keys += [k.text for k in root.iter(ns + "Key") if k.text]
        trunc = root.find(ns + "IsTruncated")
        nt = root.find(ns + "NextContinuationToken")
        if trunc is not None and trunc.text == "true" and nt is not None and nt.text:
            token = nt.text
            time.sleep(0.25)
        else:
            return keys


def summarize(frames, site=""):
    """One flight's summary (launch, burst, landing) from the archive's frames (the launchsites files hold the first, highest and last frame; a full file holds them all)."""
    pts = []
    for f in frames:
        if not isinstance(f, dict):
            continue
        t, la, lo, al = parse_ts(f.get("datetime")), fnum(f.get("lat")), fnum(f.get("lon")), fnum(f.get("alt"))
        if t is None or la is None or lo is None or not (-90 <= la <= 90 and -180 <= lo <= 180):
            continue
        pts.append((t, la, lo, al, f))
    if not pts:
        return None
    pts.sort(key=lambda p: p[0])
    first, last = pts[0], pts[-1]
    top = max((p for p in pts if p[3] is not None), key=lambda p: p[3], default=None)
    meta = first[4]
    low = float(cfg("landed_below_m"))
    s = {"serial": str(meta.get("serial") or ""), "site": str(meta.get("launch_site") or site or ""), "type": str(meta.get("type") or "")[:40], "subtype": str(meta.get("subtype") or "")[:40],
         "manufacturer": str(meta.get("manufacturer") or "")[:40]}
    if first[3] is not None and first[3] < 3500:   # the first frame is at the launch site (otherwise the flight was joined late)
        s.update(launch_ts=first[0], launch_lat=first[1], launch_lon=first[2], launch_alt=first[3])
    if top:
        s.update(burst_ts=top[0], burst_lat=top[1], burst_lon=top[2], burst_alt=top[3])
    if last[3] is not None and last[3] < low:
        s.update(land_ts=last[0], land_lat=last[1], land_lon=last[2], land_alt=last[3], lost=False)
    else:
        s.update(land_ts=None, lost=True)
    return s


COLS = ("serial", "site", "type", "subtype", "manufacturer", "launch_ts", "launch_lat", "launch_lon", "launch_alt", "burst_ts", "burst_lat", "burst_lon", "burst_alt",
        "land_ts", "land_lat", "land_lon", "land_alt", "lost", "source")


TEXT_COLS = ("site", "type", "subtype", "manufacturer")


def store_flight(c, s, source="archive"):
    """Insert or refresh one flight; a value the new summary lacks never erases one already known."""
    s = dict(s)
    s["source"] = source
    vals = [s.get(k) for k in COLS]
    for k in TEXT_COLS:
        vals[COLS.index(k)] = str(s.get(k) or "")
    vals[COLS.index("lost")] = bool(s.get("lost"))
    sets = [f"{k}=COALESCE(NULLIF(EXCLUDED.{k}, ''), flights.{k})" if k in TEXT_COLS else f"{k}=COALESCE(EXCLUDED.{k}, flights.{k})" for k in COLS if k not in ("serial", "lost", "source")]
    c.execute(f"INSERT INTO flights ({','.join(COLS)}) VALUES ({','.join(['%s'] * len(COLS))}) ON CONFLICT (serial) DO UPDATE SET " + ",".join(sets) + ", lost=EXCLUDED.lost, source=EXCLUDED.source", vals)


def in_boxes(lat, lon):
    try:
        s, w, n, e = [float(x) for x in cfg("boxes").split(",")]
    except ValueError:
        return False
    return s <= lat <= n and w <= lon <= e


# ------------------------------------------------------------------ the import

def month_list(since):
    d0 = datetime.strptime(since, "%Y-%m-%d").date().replace(day=1)
    today = datetime.now(timezone.utc).date().replace(day=1)
    out, d = [], today
    while d >= d0:   # newest first: recent flights matter most for the predictions
        out.append(d.strftime("%Y-%m"))
        d = (d.replace(day=1) - timedelta(days=1)).replace(day=1)
    return out


def throttle():
    time.sleep(1.0 / max(0.2, fnum(cfg("rate_per_second")) or 4))


def import_month(site, ym):
    """List a site's month and fetch each flight's summary that is not stored yet. Returns the flights added."""
    y, m = ym.split("-")
    keys = [k for k in list_keys(f"launchsites/{site}/{y}/{m}/") if k.endswith(".json")]
    with db() as c:
        have = {r["serial"] for r in c.execute("SELECT serial FROM flights WHERE site=%s", (site,)).fetchall()}
        c.execute("INSERT INTO import_months (site, ym, status, keys, done) VALUES (%s,%s,'running',%s,0) ON CONFLICT (site, ym) DO UPDATE SET status='running', keys=EXCLUDED.keys, done=0, error='', updated=now()", (site, ym, len(keys)))
    added = done = 0
    for k in keys:
        if IMPORT["stop"]:
            raise InterruptedError()
        serial = k.rsplit("/", 1)[-1][:-5]
        done += 1
        if serial in have:
            continue
        try:
            s = summarize(json.loads(http_get(ARCHIVE + "/" + urllib.parse.quote(k))), site)
        except (urllib.error.URLError, ValueError) as e:
            IMPORT["error"] = f"{k}: {str(e)[:80]}"
            continue
        finally:
            throttle()
        if s and s["serial"]:
            with db() as c:
                store_flight(c, s)
            added += 1
            IMPORT["flights"] += 1
    with db() as c:
        c.execute("UPDATE import_months SET status='done', done=%s, updated=now() WHERE site=%s AND ym=%s", (done, site, ym))
    return added


def import_dates(since):
    """The worldwide date/ scan: keeps only flights whose first frame is inside the boxes. Slow (one request per flight in the world); off unless date_mode is 1."""
    d = datetime.strptime(since, "%Y-%m-%d").date()
    day = datetime.now(timezone.utc).date()
    while day >= d and not IMPORT["stop"]:
        ymd = day.strftime("%Y-%m-%d")
        with db() as c:
            r = c.execute("SELECT status FROM import_months WHERE site='date' AND ym=%s", (ymd,)).fetchone()
        if not r or (r["status"] != "done"):
            IMPORT["current"] = "world day " + ymd
            try:
                keys = [k for k in list_keys("date/" + day.strftime("%Y/%m/%d/")) if k.endswith(".json")]
            except urllib.error.URLError:
                keys = []
            for k in keys:
                if IMPORT["stop"]:
                    return
                serial = k.rsplit("/", 1)[-1][:-5]
                with db() as c:
                    if c.execute("SELECT 1 FROM flights WHERE serial=%s", (serial,)).fetchone():
                        continue
                try:
                    s = summarize(json.loads(http_get(ARCHIVE + "/" + urllib.parse.quote(k))))
                except (urllib.error.URLError, ValueError):
                    s = None
                throttle()
                if s and s["serial"] and s.get("launch_lat") is not None and in_boxes(s["launch_lat"], s["launch_lon"]):
                    with db() as c:
                        store_flight(c, s, "archive-date")
                    IMPORT["flights"] += 1
            with db() as c:
                c.execute("INSERT INTO import_months (site, ym, status, keys, done) VALUES ('date',%s,'done',%s,%s) ON CONFLICT (site, ym) DO UPDATE SET status='done', keys=EXCLUDED.keys, done=EXCLUDED.done, updated=now()", (ymd, len(keys), len(keys)))
        day -= timedelta(days=1)


def import_worker():
    IMPORT.update(running=True, stop=False, error="", started=datetime.now(timezone.utc).isoformat(), finished=None, requests=0, flights=0)
    try:
        sites = [s.strip() for s in cfg("sites").split(",") if s.strip()]
        months = month_list(cfg("since"))
        IMPORT["months_total"] = len(sites) * len(months)
        IMPORT["months_done"] = 0
        this_month = datetime.now(timezone.utc).strftime("%Y-%m")
        for ym in months:
            for site in sites:
                if IMPORT["stop"]:
                    raise InterruptedError()
                with db() as c:
                    r = c.execute("SELECT status, updated FROM import_months WHERE site=%s AND ym=%s", (site, ym)).fetchone()
                fresh = r and r["status"] == "done" and (ym != this_month or datetime.now(timezone.utc) - r["updated"] < timedelta(hours=6))
                if not fresh:
                    IMPORT["current"] = f"{site} {ym}"
                    try:
                        import_month(site, ym)
                    except urllib.error.URLError as e:
                        IMPORT["error"] = f"{site} {ym}: {str(e)[:80]}"
                        with db() as c:
                            c.execute("INSERT INTO import_months (site, ym, status, error) VALUES (%s,%s,'error',%s) ON CONFLICT (site, ym) DO UPDATE SET status='error', error=EXCLUDED.error, updated=now()", (site, ym, str(e)[:200]))
                IMPORT["months_done"] += 1
        if cfg("date_mode") == "1":
            import_dates(cfg("since") if cfg("since") > "2018-01-01" else "2018-01-01")
    except InterruptedError:
        IMPORT["error"] = IMPORT["error"] or "stopped"
    except Exception as e:   # the loop is never allowed to die silently
        IMPORT["error"] = "failed: " + str(e)[:150]
    finally:
        IMPORT.update(running=False, current="", finished=datetime.now(timezone.utc).isoformat())


def start_import():
    if IMPORT["running"]:
        return False
    threading.Thread(target=import_worker, daemon=True).start()
    return True


# ------------------------------------------------------------------ predictions

def wquantile(vals, weights, q):
    pairs = sorted(zip(vals, weights))
    tot = sum(w for _v, w in pairs)
    if not pairs or tot <= 0:
        return None
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * tot:
            return v
    return pairs[-1][0]


def circ_days(a, b):
    d = abs(a - b) % 365.25
    return min(d, 365.25 - d)


def launch_schedule(c, site, now, horizon_h):
    """The usual launch times of a site: the hours of the day it launches in (from the last 180 days), the median minute of each, and the times of the next launches inside the horizon."""
    rows = c.execute("SELECT launch_ts FROM flights WHERE site=%s AND launch_ts > now() - interval '180 days' AND launch_ts IS NOT NULL", (site,)).fetchall()
    by = {}
    for r in rows:
        t = r["launch_ts"].astimezone(timezone.utc)
        hr = (t + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)   # a launch at 23:15 belongs to the 23 hour, one at 22:55 to the 23 hour too
        by.setdefault(hr.hour, []).append((t - hr).total_seconds() / 60.0)
    if not by:
        return []
    top = max(len(v) for v in by.values())
    hours = {h: sorted(v)[len(v) // 2] for h, v in by.items() if len(v) >= max(3, 0.5 * top)}
    out = []
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    for d in range(0, int(horizon_h / 24) + 3):
        for h, off in hours.items():
            t = day0 + timedelta(days=d, hours=h, minutes=off)
            if now - timedelta(hours=1) <= t <= now + timedelta(hours=horizon_h):
                out.append(t)
    return sorted(set(out))


TRACK_POINTS = 70   # points kept of a past flight used as the shape of a forecast path


def cached_track(c, serial, fetch):
    """A past flight's track as [[seconds from launch, lat, lon, alt], ...]: from the cache, or (when `fetch`) from the archive once and kept. None when it is not available."""
    r = c.execute("SELECT pts FROM tracks WHERE serial=%s", (serial,)).fetchone()
    if r:
        return json.loads(r["pts"]) or None
    if not fetch:
        return None
    t, _why = fetch_track(serial, TRACK_POINTS)
    pts = []
    if t and len(t["t"]) >= 10:
        t0 = t["t"][0]
        pts = [[round(a - t0, 1), b, d, e] for a, b, d, e in zip(t["t"], t["lat"], t["lon"], t["alt"]) if e is not None]
    c.execute("INSERT INTO tracks (serial, pts) VALUES (%s,%s) ON CONFLICT (serial) DO UPDATE SET pts=EXCLUDED.pts, fetched=now()", (serial, json.dumps(pts)))
    time.sleep(0.25)
    return pts or None


def build_path(c, analog, la0, lo0, ce, cn, smp, fetch):
    """The drawn path of a forecast flight: where it climbs, bursts and comes down. Shaped like the track of the most similar past flight (the one whose landing is nearest the forecast landing),
    moved to the launch site and bent a little along the way so it ends exactly on the forecast landing. When that track is not at hand, a simple climb and fall through the usual burst point.
    Returns (path [[seconds, lat, lon, alt_m]], burst [lat, lon, alt_m], source text)."""
    row = analog[3]
    trk = cached_track(c, row["serial"], fetch)
    if trk:
        e0, n0 = offset_km(trk[0][1], trk[0][2], trk[-1][1], trk[-1][2])   # where the analog flight ended, relative to its own start
        T = max(1.0, trk[-1][0])
        pts = []
        for tt, la, lo, al in trk:
            e, n = offset_km(trk[0][1], trk[0][2], la, lo)
            f = tt / T
            e += (ce - e0) * f
            n += (cn - n0) * f   # the bend that lands the path on the forecast landing
            lat, lon = move_km(la0, lo0, e, n)
            pts.append([round(tt), round(lat, 4), round(lon, 4), round(al)])
        top = max(pts, key=lambda p: p[3])
        return pts, [top[1], top[2], top[3]], "past flight " + row["serial"]
    # without a track: climb in a smooth curve to the usual burst point, fall to the forecast landing
    bw = [(offset_km(la0, lo0, r["burst_lat"], r["burst_lon"]), w) for _e, _n, w, r in smp if r["burst_lat"] is not None and r["burst_alt"]]
    if bw:
        W = sum(w for _o, w in bw)
        be, bn = sum(o[0] * w for o, w in bw) / W, sum(o[1] * w for o, w in bw) / W
    else:
        be, bn = ce * 0.6, cn * 0.6
    balt = wquantile([r["burst_alt"] for _e, _n, w, r in smp if r["burst_alt"]], [w for _e, _n, w, r in smp if r["burst_alt"]], 0.5) or 30000.0
    lalt = row["launch_alt"] or 170.0
    land_alt = row["land_alt"] or 250.0
    mins = [(r["land_ts"] - r["launch_ts"]).total_seconds() for _e, _n, w, r in smp if r["land_ts"] and r["launch_ts"]]
    total = (sorted(mins)[len(mins) // 2] if mins else 9000.0)
    tb = total * 0.65   # the climb takes about two thirds of the flight
    pts = []
    for k in range(0, 21):
        f = k / 20.0
        lat, lon = move_km(la0, lo0, be * f ** 1.2, bn * f ** 1.2)
        pts.append([round(tb * f), round(lat, 4), round(lon, 4), round(lalt + (balt - lalt) * f)])
    for k in range(1, 21):
        f = k / 20.0
        lat, lon = move_km(la0, lo0, be + (ce - be) * f, bn + (cn - bn) * f)
        pts.append([round(tb + (total - tb) * f), round(lat, 4), round(lon, 4), round(land_alt + (balt - land_alt) * (1 - f) ** 2.2)])
    top = max(pts, key=lambda p: p[3])
    return pts, [top[1], top[2], top[3]], "usual climb and fall (no past track yet)"


def predict_one(c, site, when, now, fetch=False):
    """Expected landing area of a balloon launched at `site` at time `when`: a center, the radii holding half and 90 percent of the past flights, burst height and flight time."""
    base = c.execute("SELECT launch_lat, launch_lon FROM flights WHERE site=%s AND launch_lat IS NOT NULL ORDER BY launch_ts DESC LIMIT 1", (site,)).fetchone()
    if not base:
        return None
    la0, lo0 = base["launch_lat"], base["launch_lon"]
    look = int(cfg("lookback_days"))
    rows = c.execute("SELECT serial, launch_ts, land_ts, land_lat, land_lon, land_alt, launch_alt, burst_lat, burst_lon, burst_alt FROM flights WHERE site=%s AND launch_ts > now() - make_interval(days => %s) AND land_lat IS NOT NULL AND NOT lost AND launch_ts IS NOT NULL", (site, look)).fetchall()
    season, hwin, need = float(cfg("season_days")), float(cfg("hour_window")), int(cfg("min_samples"))
    doy = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60.0

    def pick(season_d, hour_w):
        s = []
        for r in rows:
            t = r["launch_ts"].astimezone(timezone.utc)
            dd = circ_days(t.timetuple().tm_yday, doy)
            dh = abs(((t.hour + t.minute / 60.0) - hour + 12) % 24 - 12)
            if dd <= season_d and dh <= hour_w:
                w = math.exp(-0.5 * (dd / (season_d / 2.0 + 1e-9)) ** 2) * (1.0 + 0.15 * min(5, max(0, (t.year - (now.year - 4)))))   # nearer in season counts more, recent years a little more
                e, n = offset_km(la0, lo0, r["land_lat"], r["land_lon"])
                s.append((e, n, w, r))
        return s
    smp = pick(season, hwin)
    if len(smp) < need:
        smp = pick(season * 2, 24)   # not enough at that hour and season: any hour, a wider season
    if len(smp) < max(3, need // 2):
        return None
    W = sum(w for _e, _n, w, _r in smp)
    me, mn = sum(e * w for e, _n, w, _r in smp) / W, sum(n * w for _e, n, w, _r in smp) / W
    ce, cn, used_recent = me, mn, 0
    # winds persist for a day or two: pull the center toward the offsets of the latest flights from this site
    recent = c.execute("SELECT land_lat, land_lon, launch_ts FROM flights WHERE site=%s AND land_lat IS NOT NULL AND NOT lost AND launch_ts > now() - make_interval(hours => %s) AND launch_ts < %s ORDER BY launch_ts DESC LIMIT 6",
                       (site, int(cfg("persist_hours")), when)).fetchall()
    if recent:
        rw = [0.5 ** (max(0.0, (when - r["launch_ts"]).total_seconds() / 3600.0) / 24.0) for r in recent]
        re_ = sum(offset_km(la0, lo0, r["land_lat"], r["land_lon"])[0] * w for r, w in zip(recent, rw)) / sum(rw)
        rn = sum(offset_km(la0, lo0, r["land_lat"], r["land_lon"])[1] * w for r, w in zip(recent, rw)) / sum(rw)
        mix = min(0.6, 0.2 * sum(rw))
        ce, cn, used_recent = me * (1 - mix) + re_ * mix, mn * (1 - mix) + rn * mix, len(recent)
    dist = [math.hypot(e - ce, n - cn) for e, n, _w, _r in smp]
    ws = [w for _e, _n, w, _r in smp]
    r50, r90 = wquantile(dist, ws, 0.5), wquantile(dist, ws, 0.9)
    bursts = [(r["burst_alt"], w) for _e, _n, w, r in smp if r["burst_alt"]]
    durs = [((r["land_ts"] - r["launch_ts"]).total_seconds() / 60.0, w) for _e, _n, w, r in smp if r["land_ts"] and r["launch_ts"]]
    lat, lon = move_km(la0, lo0, ce, cn)
    wmed = sorted(w for _e, _n, w, _r in smp)[len(smp) // 2]
    analog = min([x for x in smp if x[2] >= wmed] or smp, key=lambda x: math.hypot(x[0] - ce, x[1] - cn))
    path, burst, psrc = build_path(c, analog, la0, lo0, ce, cn, smp, fetch)
    return {"site": site, "path": path, "burst": burst, "path_source": psrc, "launch_alt": round(analog[3]["launch_alt"] or 170.0), "launch": when.isoformat(), "lat": round(lat, 4), "lon": round(lon, 4), "launch_lat": la0, "launch_lon": lo0, "r50_km": round(max(r50 or 0, 2.0), 1), "r90_km": round(max(r90 or 0, 4.0), 1),
            "burst_alt": round(wquantile([b for b, _w in bursts], [w for _b, w in bursts], 0.5)) if bursts else None,
            "flight_min": round(wquantile([d for d, _w in durs], [w for _d, w in durs], 0.5)) if durs else None, "samples": len(smp), "recent": used_recent,
            "drift_km": round(math.hypot(ce, cn), 1), "bearing": round((math.degrees(math.atan2(ce, cn)) + 360) % 360)}


WIND = {}   # (lat, lon, hour) -> (made, column)
WIND_DAY = {}      # day -> requests made today
WIND_BLOCK = [0.0]  # no wind requests before this time (after a "too many requests" answer)


def available_methods():
    out = ["statistical", "wind"]
    if cfg("tawhiri_url").strip():
        out.append("tawhiri")
    return out


def enabled_methods():
    want = [m.strip() for m in cfg("methods").split(",") if m.strip()]
    return [m for m in available_methods() if m in want] or ["statistical"]


WIND_SRC = {"last": ""}


def wind_col(lat, lon, t):
    """The forecast winds by height near a place at a time, from the cache or one request."""
    src = cfg("wind_source")
    if src in ("auto", "gfs") and cfg("gfs_enabled") == "1":
        try:
            if gfsdata.fresh(db, cfg):
                col = gfsdata.column(db, lat, lon, t)
                if col:
                    WIND_SRC["last"] = "NOAA GFS (read by this site)"
                    return col
        except Exception as e:
            print("gfs wind:", str(e)[:100], flush=True)
        if src == "gfs":
            raise RuntimeError("no fresh GFS data for that place and time")
    WIND_SRC["last"] = "GFS through Open-Meteo"
    hour = (t + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
    la, lo = round(lat * 4) / 4.0, round(lon * 4) / 4.0
    key = (la, lo, hour.isoformat())
    hit = WIND.get(key)
    if hit and time.time() - hit[0] < float(cfg("wind_cache_hours")) * 3600:
        return hit[1]
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if time.time() < WIND_BLOCK[0]:
        raise RuntimeError("the wind source asked us to slow down; trying again later")
    if WIND_DAY.get(day, 0) >= int(float(cfg("wind_max_per_day"))):
        raise RuntimeError("the daily limit of wind requests is used up")
    WIND_DAY.clear() if day not in WIND_DAY else None
    WIND_DAY[day] = WIND_DAY.get(day, 0) + 1
    time.sleep(0.5)   # never a burst of requests
    try:
        col = fm.parse_column(http_json(cfg("wind_url"), fm.wind_query(la, lo, hour)), hour)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            WIND_BLOCK[0] = time.time() + 3600   # told to slow down: stop asking for an hour
        raise
    if len(WIND) > 400:
        WIND.clear()
    WIND[key] = (time.time(), col)
    return col


def typical(c, site, when, now):
    """The launch place and the usual numbers of a site: from the statistical forecast when it has enough flights, else general figures."""
    base = c.execute("SELECT launch_lat, launch_lon, launch_alt FROM flights WHERE site=%s AND launch_lat IS NOT NULL ORDER BY launch_ts DESC LIMIT 1", (site,)).fetchone()
    if not base:
        return None
    p = predict_one(c, site, when, now, False)
    burst = (p and p["burst_alt"]) or 30000.0
    mins = (p and p["flight_min"]) or 110.0
    lalt = (p and p["launch_alt"]) or base["launch_alt"] or 170.0
    asc = max(3.0, min(8.0, (burst - lalt) / (mins * 60.0 * 0.65)))
    return {"lat": base["launch_lat"], "lon": base["launch_lon"], "alt": float(lalt), "burst": float(burst), "mins": float(mins), "asc": asc, "stat": p}


def finish(c, method, site, when, t, pts, burst, note, stat):
    """The forecast dictionary every method returns (the same fields as the statistical one)."""
    lat0, lon0 = pts[0][1], pts[0][2]
    lat, lon = pts[-1][1], pts[-1][2]
    e, n = offset_km(lat0, lon0, lat, lon)
    sc = scores(site, method)
    r50 = sc["median_km"] if sc["count"] >= 5 and sc["median_km"] else 20.0
    r90 = sc["p90_km"] if sc["count"] >= 5 and sc["p90_km"] else 50.0
    return {"site": site, "method": method, "path": fm.thin(pts, 70), "burst": burst, "path_source": note, "launch_alt": round(t["alt"]), "launch": when.isoformat(), "lat": round(lat, 4), "lon": round(lon, 4),
            "launch_lat": lat0, "launch_lon": lon0, "r50_km": round(r50, 1), "r90_km": round(r90, 1), "burst_alt": round(t["burst"]), "flight_min": round(pts[-1][0] / 60.0),
            "samples": sc["count"], "recent": 0, "drift_km": round(math.hypot(e, n), 1), "bearing": round((math.degrees(math.atan2(e, n)) + 360) % 360)}


def predict_wind(c, site, when, now, fetch=False):
    """Our own flight model through forecast winds. None when the winds for that time cannot be had (more than about two weeks ahead, or the wind service is away)."""
    t = typical(c, site, when, now)
    if not t:
        return None
    if when > now + timedelta(days=15) or when < now - timedelta(hours=6):
        return None
    try:
        up = wind_col(t["lat"], t["lon"], when + timedelta(minutes=40))
        if not up:
            return None
        desc = float(cfg("descent_ms"))
        pts, burst = fm.fly(t["lat"], t["lon"], t["alt"], t["burst"], t["asc"], desc, 250.0, up, up)
        down = wind_col(burst[0], burst[1], when + timedelta(seconds=[q for q in pts if q[3] >= burst[2] - 1][0][0]))   # the winds where and when it comes down
        if down:
            pts, burst = fm.fly(t["lat"], t["lon"], t["alt"], t["burst"], t["asc"], desc, 250.0, up, down)
    except Exception as e:
        print("wind forecast:", str(e)[:100], flush=True)
        return None
    return finish(c, "wind", site, when, t, pts, burst, "forecast winds (" + (WIND_SRC["last"] or "GFS") + ")", t["stat"])


def predict_tawhiri(c, site, when, now, fetch=False):
    url = cfg("tawhiri_url").strip()
    if not url:
        return None
    t = typical(c, site, when, now)
    if not t:
        return None
    try:
        doc = http_json(url, fm.tawhiri_query(t["lat"], t["lon"], when, t["alt"], t["asc"], t["burst"], float(cfg("descent_ms"))), timeout=60)
        got = fm.parse_tawhiri(doc, when)
    except Exception as e:
        print("tawhiri:", str(e)[:100], flush=True)
        return None
    if not got:
        return None
    return finish(c, "tawhiri", site, when, t, got[0], got[1], "Tawhiri balloon predictor", t["stat"])


def predict_method(c, method, site, when, now, fetch=False):
    if method == "wind":
        return predict_wind(c, site, when, now, fetch)
    if method == "tawhiri":
        return predict_tawhiri(c, site, when, now, fetch)
    p = predict_one(c, site, when, now, fetch)
    if p:
        p["method"] = "statistical"
    return p


def predictions(site_list=None, fetch=False, method=None):
    now = datetime.now(timezone.utc)
    horizon = int(cfg("horizon_hours"))
    out = []
    with db() as c:
        sites = site_list or [s.strip() for s in cfg("sites").split(",") if s.strip()]
        for site in sites:
            for when in launch_schedule(c, site, now, horizon):
              for meth in ([method] if method else enabled_methods()):
                p, reused = None, False
                if meth in ("wind", "tawhiri"):
                    if when - now > timedelta(hours=float(cfg("wind_horizon_hours"))):
                        continue
                    row = c.execute("SELECT data FROM predictions WHERE site=%s AND launch_ts=%s AND method=%s AND data IS NOT NULL AND made > now() - (%s * interval '1 hour')",
                                    (site, when, meth, float(cfg("wind_refresh_hours")))).fetchone()
                    if row:
                        try:
                            p, reused = json.loads(row["data"]), True
                        except ValueError:
                            p = None
                if p is None:
                    p = predict_method(c, meth, site, when, now, fetch)
                if not p:
                    continue
                p["hours_to_launch"] = round((when - now).total_seconds() / 3600.0, 1)
                out.append(p)
                if when - now > timedelta(hours=1) and not reused:   # the last forecast before the launch is kept and frozen an hour before, then scored against the real flight
                    c.execute("INSERT INTO predictions (site, launch_ts, method, made, lat, lon, r50_km, r90_km, burst_alt, flight_min, samples, recent, data) VALUES (%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                              "ON CONFLICT (site, launch_ts, method) DO UPDATE SET made=now(), lat=EXCLUDED.lat, lon=EXCLUDED.lon, r50_km=EXCLUDED.r50_km, r90_km=EXCLUDED.r90_km, burst_alt=EXCLUDED.burst_alt, "
                              "flight_min=EXCLUDED.flight_min, samples=EXCLUDED.samples, recent=EXCLUDED.recent, data=EXCLUDED.data WHERE predictions.actual_serial IS NULL",
                              (site, when, meth, p["lat"], p["lon"], p["r50_km"], p["r90_km"], p["burst_alt"], p["flight_min"], p["samples"], p["recent"], json.dumps(p) if meth in ("wind", "tawhiri") else None))
    out.sort(key=lambda p: p["launch"])
    return out


def score_predictions():
    """Compare each forecast whose launch has passed with the real flight that launched from that site within 90 minutes of the forecast time."""
    n = 0
    with db() as c:
        for p in c.execute("SELECT * FROM predictions WHERE actual_serial IS NULL AND launch_ts < now() - interval '8 hours' AND launch_ts > now() - interval '30 days'").fetchall():
            f = c.execute("SELECT serial, land_lat, land_lon FROM flights WHERE site=%s AND land_lat IS NOT NULL AND launch_ts BETWEEN %s AND %s ORDER BY abs(extract(epoch FROM launch_ts - %s)) LIMIT 1",
                          (p["site"], p["launch_ts"] - timedelta(minutes=90), p["launch_ts"] + timedelta(minutes=90), p["launch_ts"])).fetchone()
            if f:
                c.execute("UPDATE predictions SET actual_serial=%s, actual_lat=%s, actual_lon=%s, err_km=%s, scored=now() WHERE id=%s", (f["serial"], f["land_lat"], f["land_lon"], round(km_between(p["lat"], p["lon"], f["land_lat"], f["land_lon"]), 1), p["id"]))
                n += 1
    return n


def scores(site=None, method="statistical"):
    with db() as c:
        q = "SELECT * FROM predictions WHERE err_km IS NOT NULL AND method=%s" + (" AND site=%s" if site else "") + " ORDER BY launch_ts DESC LIMIT 400"
        rows = c.execute(q, (method,) + ((site,) if site else ())).fetchall()
    errs = sorted(r["err_km"] for r in rows)
    def q_(f):
        return errs[min(len(errs) - 1, int(f * len(errs)))] if errs else None
    inside50 = sum(1 for r in rows if r["err_km"] <= r["r50_km"])
    inside90 = sum(1 for r in rows if r["err_km"] <= r["r90_km"])
    return {"method": method, "count": len(rows), "median_km": q_(0.5), "p90_km": q_(0.9), "inside_r50": round(inside50 / len(rows), 2) if rows else None, "inside_r90": round(inside90 / len(rows), 2) if rows else None,
            "recent": [{"site": r["site"], "launch": r["launch_ts"].isoformat(), "err_km": r["err_km"], "serial": r["actual_serial"], "r50_km": r["r50_km"], "r90_km": r["r90_km"]} for r in rows[:25]]}


def loop():
    """Every half hour: refresh and store the forecasts, and score the old ones. Once a day (and at start) the import runs by itself when it is switched on."""
    last_import = 0
    last_gfs = 0
    while True:
        try:
            if cfg("gfs_enabled") == "1" and time.time() - last_gfs > float(cfg("gfs_refresh_minutes")) * 60:
                last_gfs = time.time()
                threading.Thread(target=lambda: print("gfs:", gfsdata.refresh(db, cfg, note_out), flush=True), daemon=True).start()   # in its own thread: reading a cycle takes minutes
            predictions(fetch=True)   # the background pass fetches the tracks the forecast paths are shaped from; requests only use what is cached
            score_predictions()
            if cfg("auto_import") == "1" and time.time() - last_import > 86400 and not IMPORT["running"]:
                last_import = time.time()
                start_import()
        except Exception as e:
            print("loop:", e, flush=True)
        time.sleep(1800)


# ------------------------------------------------------------------ the track on demand

def fetch_track(serial, max_points=600):
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,39}$", serial):
        return None, "bad serial"
    try:
        body = gzip.decompress(http_get(ARCHIVE + "/serial/" + urllib.parse.quote(serial) + ".json.gz", binary=True))
    except urllib.error.HTTPError as e:
        return None, "not in the archive" if e.code in (403, 404) else f"archive error {e.code}"
    except Exception as e:
        return None, "archive unreachable: " + str(e)[:60]
    seen, pts = set(), []
    for f in json.loads(body):
        t, la, lo = parse_ts(f.get("datetime")), fnum(f.get("lat")), fnum(f.get("lon"))
        if t is None or la is None or lo is None or t in seen:
            continue
        seen.add(t)
        pts.append((t.timestamp(), round(la, 5), round(lo, 5), fnum(f.get("alt"))))
    pts.sort()
    if len(pts) > max_points:
        step = (len(pts) - 1) / (max_points - 1)
        pts = [pts[round(i * step)] for i in range(max_points)]
    return {"serial": serial, "t": [p[0] for p in pts], "lat": [p[1] for p in pts], "lon": [p[2] for p in pts], "alt": [p[3] for p in pts]}, ""


# ------------------------------------------------------------------ HTTP

def flight_dict(r):
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items() if k != "imported"}


def dbsize():
    """The size of this service's whole database in bytes (shown on the portal's Admin > Usage)."""
    try:
        with db() as c:
            return int(c.execute("SELECT pg_database_size(current_database()) AS s").fetchone()["s"])
    except Exception:
        return None


class H(BaseHTTPRequestHandler):
    server_version = "arksignal-predict"

    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        p = u.path.rstrip("/")
        try:
            if p in ("", "/health"):
                with db() as c:
                    n = c.execute("SELECT COUNT(*) AS n FROM flights").fetchone()["n"]
                return self.send({"ok": True, "version": VERSION, "flights": n})
            if p == "/stats":
                with db() as c:
                    n = c.execute("SELECT COUNT(*) AS n, MIN(launch_ts) AS a, MAX(launch_ts) AS b, COUNT(DISTINCT site) AS sites FROM flights").fetchone()
                    bysite = c.execute("SELECT site, COUNT(*) AS n, MIN(launch_ts) AS a, MAX(launch_ts) AS b FROM flights GROUP BY site ORDER BY n DESC").fetchall()
                    months = c.execute("SELECT status, COUNT(*) AS n FROM import_months WHERE site<>'date' GROUP BY status").fetchall()
                    pr = c.execute("SELECT COUNT(*) AS n, COUNT(err_km) AS scored FROM predictions").fetchone()
                return self.send({"db_bytes": dbsize(), "version": VERSION, "flights": n["n"], "first": n["a"] and n["a"].isoformat(), "last": n["b"] and n["b"].isoformat(), "sites": n["sites"],
                                  "by_site": [{"site": r["site"], "flights": r["n"], "first": r["a"] and r["a"].isoformat(), "last": r["b"] and r["b"].isoformat()} for r in bysite],
                                  "months": {r["status"]: r["n"] for r in months}, "predictions": pr["n"], "scored": pr["scored"], "import": dict(IMPORT), "gfs": gfsdata.status(db, cfg), "outside": [{"host": h, "days": {k: v for k, v in sorted(d.items())[-30:]}} for h, d in OUT_DAYS.items()], "methods": available_methods(), "config": {k: cfg(k) for k in DEFAULTS}})
            if p == "/predict/next":
                sites = [s for s in (q.get("site") or "").split(",") if s] or None
                meth = (q.get("method") or "").strip()
                if meth and meth not in available_methods():
                    return self.send({"error": "unknown method " + meth, "methods": available_methods()}, 400)
                return self.send({"predictions": predictions(sites, method=meth or None), "methods": available_methods(), "scores": scores(sites[0] if sites and len(sites) == 1 else None, meth or cfg("default_method")),
                                  "scores_by_method": {m: scores(sites[0] if sites and len(sites) == 1 else None, m) for m in enabled_methods()}})
            if p == "/predict/at":   # a forecast for a launch from one site at a chosen time (nothing is kept): the same method as the next launches, for any date
                when, site = parse_ts(q.get("when")), (q.get("site") or "").strip()
                if not site or not when:
                    return self.send({"error": "site and when (a date and time) are needed"}, 400)
                with db() as c:
                    meth = (q.get("method") or cfg("default_method")).strip()
                    if meth not in available_methods():
                        meth = "statistical"
                    r = predict_method(c, meth, site, when, datetime.now(timezone.utc), False)
                return self.send({"prediction": r, "method": meth, "methods": available_methods(), "note": "" if r else ("Not enough past flights from this site in that season to forecast." if meth == "statistical" else "That method has no forecast for this launch (the winds for that time are not available).")})
            if p == "/gfs/status":
                return self.send(gfsdata.status(db, cfg))
            if p == "/gfs/forecast":   # the surface forecast of a place, every three hours, from our own GFS data
                try:
                    la, lo = float(q.get("lat", "")), float(q.get("lon", ""))
                except ValueError:
                    return self.send({"error": "lat and lon are needed"}, 400)
                got = gfsdata.point_forecast(db, la, lo) if cfg("gfs_enabled") == "1" else None
                if not got or not got["rows"]:
                    return self.send({"ok": False, "note": "No GFS data for that place yet (it is outside the area kept, or the first cycle is still being read)."})
                return self.send(dict(got, ok=True, credit="NOAA GFS model data (public domain), read and interpolated by this site"))
            if p == "/scores":
                out = scores(q.get("site"), q.get("method") or cfg("default_method"))
                out["by_method"] = {m: scores(q.get("site"), m) for m in available_methods()}
                return self.send(out)
            if p == "/flights":
                with db() as c:
                    rows = c.execute("SELECT * FROM flights WHERE (%s = '' OR site=%s) AND (%s = '' OR launch_ts >= %s::timestamptz) ORDER BY launch_ts DESC NULLS LAST LIMIT %s",
                                     (q.get("site", ""), q.get("site", ""), q.get("since", ""), q.get("since") or "1970-01-01", min(int(q.get("limit", 200)), 2000))).fetchall()
                return self.send({"flights": [flight_dict(r) for r in rows]})
            m = re.match(r"^/flights/([^/]+)/track$", p)
            if m:
                t, why = fetch_track(urllib.parse.unquote(m.group(1)), min(int(q.get("max", 600)), 3000))
                return self.send(t or {"error": why}, 200 if t else 404)
            return self.send({"error": "not found"}, 404)
        except Exception as e:
            return self.send({"error": str(e)[:200]}, 500)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path.rstrip("/")
        b = self.body_json()
        try:
            if p == "/import/start":
                return self.send({"started": start_import(), "import": dict(IMPORT)})
            if p == "/import/stop":
                IMPORT["stop"] = True
                return self.send({"stopping": IMPORT["running"]})
            if p == "/gfs/refresh":   # read the newest cycle now (in the background; /gfs/status says how it goes)
                if gfsdata.STATE["running"]:
                    return self.send({"started": False, "note": "already reading"})
                threading.Thread(target=lambda: print("gfs:", gfsdata.refresh(db, cfg, note_out, force=bool(b.get("force"))), flush=True), daemon=True).start()
                return self.send({"started": True})
            if p == "/config":
                for k, v in (b or {}).items():
                    set_cfg(k, v)
                return self.send({"ok": True})
            if p == "/ingest":   # the portal sends flights it recorded itself (the archive trails real time by days)
                n = 0
                with db() as c:
                    for f in b.get("flights", [])[:500]:
                        s = {k: f.get(k) for k in COLS if k in f}
                        for k in ("launch_ts", "burst_ts", "land_ts"):
                            s[k] = parse_ts(s.get(k))
                        if s.get("serial") and s.get("site"):
                            store_flight(c, s, "portal")
                            n += 1
                return self.send({"stored": n})
            return self.send({"error": "not found"}, 404)
        except Exception as e:
            return self.send({"error": str(e)[:200]}, 500)


def main():
    init_db()
    threading.Thread(target=loop, daemon=True).start()
    print(f"arksignal-predict {VERSION} listening on {PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
