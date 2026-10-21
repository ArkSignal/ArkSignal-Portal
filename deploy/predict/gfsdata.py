"""Our own NOAA GFS data: the Global Forecast System's winds by height, and its surface weather, for a box around Arkansas, read ourselves instead of through a middleman.

NOAA's NOMADS server cuts the model files down to a box and to the fields asked for (its "GRIB filter", public domain data, see DATA_SOURCES_TERMS.md), so a cycle is a few dozen small requests. The fields are decoded
with pygrib (the ECMWF eccodes library inside), kept as compressed arrays in this service's own database for the last couple of cycles, and read back by interpolation:
  column(lat, lon, t)       the wind by height over a place at a time (what the balloon forecast flies through), [(height m, u m/s, v m/s)] from low to high
  point_forecast(lat, lon)  the surface forecast every three hours: temperature, humidity, wind, gusts, rain, cloud, pressure
Nothing here talks to anyone unless `refresh` is called; predictd.py calls it on a schedule and falls back to the old wind source when there is no fresh data."""
import array
import math
import os
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone

PRESSURE_LEVELS = [1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100, 70, 50, 30, 20, 10]
CYCLE_DELAY_HOURS = 4.5     # a cycle's first files appear about this long after its nominal time
_lock = threading.Lock()
_cache = {}                 # cycle iso -> {"grid": ..., "fields": {(step, name, level): array('f')}}
STATE = {"running": False, "last": "", "error": ""}


def init(c):
    c.execute("CREATE TABLE IF NOT EXISTS gfs_cycles (cycle TIMESTAMPTZ PRIMARY KEY, status TEXT NOT NULL DEFAULT 'loading', steps INT NOT NULL DEFAULT 0, hours INT NOT NULL DEFAULT 0, "
              "fetched TIMESTAMPTZ NOT NULL DEFAULT now(), nlat INT, nlon INT, lat0 DOUBLE PRECISION, lon0 DOUBLE PRECISION, dlat DOUBLE PRECISION, dlon DOUBLE PRECISION, bytes BIGINT NOT NULL DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS gfs_fields (cycle TIMESTAMPTZ NOT NULL, step INT NOT NULL, name TEXT NOT NULL, level INT NOT NULL, data BYTEA NOT NULL, PRIMARY KEY (cycle, step, name, level))")


def box(cfg):
    """(south, west, north, east) of the area kept."""
    try:
        s, w, n, e = (float(x) for x in cfg("gfs_box").split(","))
        if -90 <= s < n <= 90 and -180 <= w < e <= 180 and (n - s) <= 40 and (e - w) <= 50:
            return s, w, n, e
    except ValueError:
        pass
    return 27.0, -100.0, 43.0, -84.0


def levels(cfg):
    try:
        ls = [int(x) for x in cfg("gfs_levels").split(",") if x.strip()]
        ls = [l for l in ls if l in PRESSURE_LEVELS]
        if len(ls) >= 6:
            return sorted(set(ls), reverse=True)
    except ValueError:
        pass
    return PRESSURE_LEVELS


# ------------------------------------------------------------------ reading the model files

def _url(cfg, cycle, step, kind):
    """The GRIB filter address of one forecast hour of a cycle: kind 'p' (winds and heights on pressure levels) or 's' (the surface fields)."""
    s, w, n, e = box(cfg)
    ymd, hh = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    q = [("dir", f"/gfs.{ymd}/{hh}/atmos"), ("file", f"gfs.t{hh}z.pgrb2.0p25.f{step:03d}"), ("subregion", ""), ("leftlon", f"{w:g}"), ("rightlon", f"{e:g}"), ("toplat", f"{n:g}"), ("bottomlat", f"{s:g}")]
    if kind == "p":
        q += [("var_UGRD", "on"), ("var_VGRD", "on"), ("var_HGT", "on")] + [(f"lev_{lv}_mb", "on") for lv in levels(cfg)]
    else:
        q += [("var_TMP", "on"), ("var_RH", "on"), ("var_UGRD", "on"), ("var_VGRD", "on"), ("var_GUST", "on"), ("var_APCP", "on"), ("var_TCDC", "on"), ("var_PRMSL", "on"),
              ("lev_2_m_above_ground", "on"), ("lev_10_m_above_ground", "on"), ("lev_surface", "on"), ("lev_entire_atmosphere", "on"), ("lev_mean_sea_level", "on")]
    return cfg("gfs_url").rstrip("?") + "?" + urllib.parse.urlencode(q)


def _get(url, cfg, note_out=None):
    req = urllib.request.Request(url, headers={"User-Agent": cfg("gfs_user_agent")})
    with urllib.request.urlopen(req, timeout=int(float(cfg("gfs_timeout_seconds")))) as r:
        body = r.read(60_000_000)
    if note_out:
        note_out(urllib.parse.urlsplit(url).hostname or "", len(body))
    return body


def _classify(m):
    """(our field name, level) of a decoded message, or None when we do not keep it."""
    sn, tl, lv = m.shortName, m.typeOfLevel, int(m.level)
    if tl == "isobaricInhPa":
        name = {"u": "u", "v": "v", "gh": "z"}.get(sn)
        return (name, lv) if name else None
    if tl == "heightAboveGround":
        if lv == 2:
            name = {"2t": "t2", "t": "t2", "2r": "rh2", "r": "rh2"}.get(sn)
        elif lv == 10:
            name = {"10u": "u10", "u": "u10", "10v": "v10", "v": "v10"}.get(sn)
        else:
            name = None
        return (name, 0) if name else None
    if tl == "surface":
        if sn == "gust":
            return ("gust", 0)
        if sn in ("tp", "apcp"):
            try:
                return ("apcp", int(m.startStep))   # the rain since the start of its accumulation window (the model restarts the window every six hours); the level slot keeps the window's start hour
            except Exception:
                return None
    if tl in ("atmosphere", "entireAtmosphere") and sn in ("tcc", "tcdc") and str(getattr(m, "stepType", "instant")) == "instant":
        return ("tcc", 0)
    if tl in ("meanSea",) and sn in ("prmsl", "msl", "pres"):
        return ("prmsl", 0)
    return None


def _decode(body, want_grid):
    """[(name, level, array('f'))] and the grid of a filter answer."""
    import pygrib
    out, grid = [], None
    with tempfile.NamedTemporaryFile(suffix=".grb2", delete=False) as f:
        f.write(body)
        path = f.name
    try:
        g = pygrib.open(path)
        for m in g:
            key = _classify(m)
            if key is None:
                continue
            vals = m.values
            if want_grid and grid is None:
                lats, lons = m.latlons()
                lons = ((lons + 180) % 360) - 180
                grid = {"nlat": int(vals.shape[0]), "nlon": int(vals.shape[1]), "lat0": float(lats[0][0]), "lon0": float(lons[0][0]),
                        "dlat": float(lats[1][0] - lats[0][0]), "dlon": float(lons[0][1] - lons[0][0])}
            fill = vals.filled(float("nan")) if hasattr(vals, "filled") else vals
            out.append((key[0], key[1], array.array("f", [float(x) for x in fill.flatten()])))
        g.close()
    finally:
        os.unlink(path)
    return out, grid


def latest_cycle(now=None):
    now = now or datetime.now(timezone.utc)
    t = now - timedelta(hours=CYCLE_DELAY_HOURS)
    return t.replace(hour=(t.hour // 6) * 6, minute=0, second=0, microsecond=0)


def refresh(db, cfg, note_out=None, force=False):
    """Read the newest GFS cycle that is out, for every forecast hour asked, into the database. Returns a short result text; never raises."""
    if STATE["running"]:
        return "already reading"
    STATE["running"], STATE["error"] = True, ""
    try:
        hours, step = int(float(cfg("gfs_hours"))), 3
        cyc = latest_cycle()
        with db() as c:
            have = c.execute("SELECT status, steps FROM gfs_cycles WHERE cycle=%s", (cyc,)).fetchone()
        if have and have["status"] == "ready" and not force:
            STATE["last"] = f"cycle {cyc:%Y-%m-%d %Hz} is already here"
            return STATE["last"]
        for attempt in range(3):   # the newest cycle may not be out yet: the one before it is
            try:
                _get(_url(cfg, cyc, 0, "s"), cfg, note_out)
                break
            except Exception:
                cyc -= timedelta(hours=6)
        else:
            raise RuntimeError("no GFS cycle could be read")
        with db() as c:
            row = c.execute("SELECT status FROM gfs_cycles WHERE cycle=%s", (cyc,)).fetchone()
            if row and row["status"] == "ready" and not force:
                STATE["last"] = f"cycle {cyc:%Y-%m-%d %Hz} is already here"
                return STATE["last"]
            c.execute("DELETE FROM gfs_fields WHERE cycle=%s", (cyc,))
            c.execute("INSERT INTO gfs_cycles (cycle, status, hours) VALUES (%s,'loading',%s) ON CONFLICT (cycle) DO UPDATE SET status='loading', hours=EXCLUDED.hours, fetched=now(), steps=0, bytes=0", (cyc, hours))
        grid, nbytes, steps = None, 0, 0
        for st in range(0, hours + 1, step):
            for kind in ("p", "s"):
                time.sleep(float(cfg("gfs_pause_seconds")))
                body = _get(_url(cfg, cyc, st, kind), cfg, note_out)
                fields, g = _decode(body, grid is None)
                grid = grid or g
                with db() as c:
                    for name, lv, arr in fields:
                        blob = zlib.compress(arr.tobytes(), 3)
                        nbytes += len(blob)
                        c.execute("INSERT INTO gfs_fields (cycle, step, name, level, data) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (cycle, step, name, level) DO UPDATE SET data=EXCLUDED.data", (cyc, st, name, lv, blob))
            steps += 1
        with db() as c:
            c.execute("UPDATE gfs_cycles SET status='ready', steps=%s, fetched=now(), nlat=%s, nlon=%s, lat0=%s, lon0=%s, dlat=%s, dlon=%s, bytes=%s WHERE cycle=%s",
                      (steps, grid["nlat"], grid["nlon"], grid["lat0"], grid["lon0"], grid["dlat"], grid["dlon"], nbytes, cyc))
            keep = int(float(cfg("gfs_keep_cycles")))
            old = [r["cycle"] for r in c.execute("SELECT cycle FROM gfs_cycles ORDER BY cycle DESC OFFSET %s", (keep,)).fetchall()]
            for o in old:
                c.execute("DELETE FROM gfs_fields WHERE cycle=%s", (o,))
                c.execute("DELETE FROM gfs_cycles WHERE cycle=%s", (o,))
        with _lock:
            _cache.clear()
        STATE["last"] = f"cycle {cyc:%Y-%m-%d %Hz}: {steps} forecast hours, {nbytes // 1024} KB"
        return STATE["last"]
    except Exception as e:
        STATE["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        return "failed: " + STATE["error"]
    finally:
        STATE["running"] = False


# ------------------------------------------------------------------ reading the stored fields

def _load(db):
    """The newest ready cycle as {"cycle", "grid", "hours", "fields": {(step, name, level): array('f')}} (kept in memory until the next cycle arrives)."""
    with _lock:
        if _cache:
            return next(iter(_cache.values()))
    with db() as c:
        cy = c.execute("SELECT * FROM gfs_cycles WHERE status='ready' ORDER BY cycle DESC LIMIT 1").fetchone()
        if cy is None:
            return None
        rows = c.execute("SELECT step, name, level, data FROM gfs_fields WHERE cycle=%s", (cy["cycle"],)).fetchall()
    fields = {}
    for r in rows:
        a = array.array("f")
        a.frombytes(zlib.decompress(bytes(r["data"])))
        fields[(r["step"], r["name"], r["level"])] = a
    got = {"cycle": cy["cycle"], "hours": cy["hours"], "steps": sorted({k[0] for k in fields}), "fetched": cy["fetched"],
           "grid": {k: cy[k] for k in ("nlat", "nlon", "lat0", "lon0", "dlat", "dlon")}, "fields": fields}
    with _lock:
        _cache.clear()
        _cache[cy["cycle"].isoformat()] = got
    return got


def _at(d, step, name, level, lat, lon):
    """The bilinear value of a field at a place, or None when the place is outside the box or the field is missing."""
    arr = d["fields"].get((step, name, level))
    g = d["grid"]
    if arr is None:
        return None
    fi = (lat - g["lat0"]) / g["dlat"]
    fj = (lon - g["lon0"]) / g["dlon"]
    if fi < 0 or fj < 0 or fi > g["nlat"] - 1 or fj > g["nlon"] - 1:
        return None
    i0, j0 = int(math.floor(fi)), int(math.floor(fj))
    i1, j1 = min(i0 + 1, g["nlat"] - 1), min(j0 + 1, g["nlon"] - 1)
    a, b = fi - i0, fj - j0
    n = g["nlon"]
    v00, v01, v10, v11 = arr[i0 * n + j0], arr[i0 * n + j1], arr[i1 * n + j0], arr[i1 * n + j1]
    if any(x != x for x in (v00, v01, v10, v11)):   # a missing value
        return None
    return v00 * (1 - a) * (1 - b) + v01 * (1 - a) * b + v10 * a * (1 - b) + v11 * a * b


def _bracket(d, t):
    """(step before, step after, weight of the after) for a time, or None when it is outside the hours kept."""
    h = (t - d["cycle"]).total_seconds() / 3600.0
    steps = d["steps"]
    if not steps or h < steps[0] or h > steps[-1]:
        return None
    for i in range(len(steps) - 1):
        if steps[i] <= h <= steps[i + 1]:
            w = (h - steps[i]) / (steps[i + 1] - steps[i])
            return steps[i], steps[i + 1], w
    return steps[-1], steps[-1], 0.0


def column(db, lat, lon, t):
    """The winds over a place at a time: [(geopotential height m, u m/s, v m/s)] from low to high; [] when there is no data for that place and time."""
    d = _load(db)
    if d is None:
        return []
    br = _bracket(d, t)
    if br is None:
        return []
    s0, s1, w = br
    lv = sorted({k[2] for k in d["fields"] if k[1] == "z" and k[0] == s0})
    out = []
    for L in lv:
        vals = []
        for s in (s0, s1):
            z, u, v = (_at(d, s, n, L, lat, lon) for n in ("z", "u", "v"))
            if None in (z, u, v):
                break
            vals.append((z, u, v))
        else:
            z, u, v = (vals[0][i] * (1 - w) + vals[1][i] * w for i in range(3))
            out.append((z, u, v))
    out.sort()
    return out


def _rain3(d, s, lat, lon):
    """The rain (mm) of the three hours up to step s. The model's rain accumulates over windows that restart every six hours: a window that started three hours before is the amount itself; one that
    started six hours before holds both three-hour halves, so the earlier half (the previous step's value in that same window) is taken off."""
    if s == 0:
        return None
    a = _at(d, s, "apcp", s - 3, lat, lon)
    if a is not None:
        return a
    full = _at(d, s, "apcp", s - 6, lat, lon)
    if full is None and s - 6 < 0:
        full = _at(d, s, "apcp", 0, lat, lon)
        half = _at(d, s - 3, "apcp", 0, lat, lon)
        return None if full is None or half is None else full - half
    half = _at(d, s - 3, "apcp", s - 6, lat, lon)
    return None if full is None or half is None else full - half


def point_forecast(db, lat, lon):
    """The surface forecast of a place every three hours: {time, temp_f, rh, wind_mph, wind_dir, gust_mph, rain_in (the three hours up to that time), cloud, pressure_hpa}."""
    d = _load(db)
    if d is None:
        return None
    rows = []
    for s in d["steps"]:
        t2, rh, u, v, gust, tc, pr = (_at(d, s, n, 0, lat, lon) for n in ("t2", "rh2", "u10", "v10", "gust", "tcc", "prmsl"))
        if t2 is None:
            continue
        ap = _rain3(d, s, lat, lon)
        spd = math.hypot(u, v) if u is not None and v is not None else None
        rows.append({"time": (d["cycle"] + timedelta(hours=s)).isoformat(), "hour": s, "temp_f": round((t2 - 273.15) * 9 / 5 + 32, 1), "rh": None if rh is None else round(min(100.0, max(0.0, rh))),
                     "wind_mph": None if spd is None else round(spd * 2.236936, 1), "wind_dir": None if spd is None else round((math.degrees(math.atan2(-u, -v)) + 360) % 360),
                     "gust_mph": None if gust is None else round(gust * 2.236936, 1), "rain_in": None if ap is None or s == 0 else round(max(0.0, ap) / 25.4, 2),
                     "cloud": None if tc is None else round(min(100.0, max(0.0, tc))), "pressure_hpa": None if pr is None else round(pr / 100.0, 1)})
    return {"cycle": d["cycle"].isoformat(), "fetched": d["fetched"].isoformat(), "rows": rows}


def status(db, cfg):
    with db() as c:
        cy = c.execute("SELECT * FROM gfs_cycles ORDER BY cycle DESC LIMIT 1").fetchone()
    out = {"enabled": cfg("gfs_enabled") == "1", "running": STATE["running"], "last": STATE["last"], "error": STATE["error"], "cycle": None, "steps": 0, "bytes": 0, "age_hours": None, "box": list(box(cfg)), "ready": False}
    if cy:
        out.update(cycle=cy["cycle"].isoformat(), steps=cy["steps"], bytes=cy["bytes"], ready=cy["status"] == "ready", status=cy["status"],
                   age_hours=round((datetime.now(timezone.utc) - cy["cycle"]).total_seconds() / 3600, 1))
    return out


def fresh(db, cfg):
    """True when a cycle is stored that is not older than the longest age winds from it are trusted (a cycle is six hours; a day without a new one means the source is broken)."""
    s = status(db, cfg)
    return bool(s["ready"] and s["age_hours"] is not None and s["age_hours"] <= float(cfg("gfs_max_age_hours")))
