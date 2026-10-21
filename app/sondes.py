"""Radiosondes and weather balloons: the live feed, the stored flights and tracks, the launch sites and the receiving stations.

Source: SondeHub (https://sondehub.org), telemetry uploaded by community receiver stations, licensed Creative Commons BY-SA 2.0 (see DATA_SOURCES_TERMS.md). SondeHub asks that its
API is not polled regularly and that live data is read from its MQTT-over-websocket feed, so that is what this module does: ONE connection (held by one web worker at a time, a database lock
decides which), filtered to Arkansas and its neighbors (Settings > Radiosondes), written to our own tables in small batches. The start of a flight we joined late is fetched once from the
API, and the launch-site list is refreshed from Data Updates. Everything shown credits SondeHub and names the license.

National Weather Service flights are recognized by their launch site: the first position of a flight is matched to the nearest launch site, and a site whose WMO number fits the pattern
in Settings (72xxx and 74xxx) and is not on the exclude list is an NWS site. Amateur balloons come from the same feed under their payload call sign."""
import json
import math
import queue
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from .cache import cached
from .db import get_db
from .settings import cfg, cfg_float, cfg_int, get_setting, set_setting

LOCK_ID = 727110
TOPICS = ("sondes/#", "amateur/#", "listener/#", "amateur-listener/#", "prediction/#")
STATE = {"msgs": 0, "kept": 0, "since": time.time(), "connected": False, "last": None, "error": ""}   # this process's feed counters (written to settings by the feed's owner)


def enabled():
    return cfg("sonde_enabled") == "1"


# ------------------------------------------------------------------ small helpers

def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def parse_time(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def pos3(v):
    """(lat, lon, alt) from a position that arrives as a list ([lat, lon, alt]) or as text ("lat,lon,alt"), or None when it is missing, out of range or the empty 0,0 point.
    (The live feed sends both forms: reading the text form as a list gave stations like 3.0, 4.0.)"""
    if isinstance(v, str):
        v = v.replace(";", ",").split(",")
    if not isinstance(v, (list, tuple)) or len(v) < 2:
        return None
    lat, lon = _f(v[0]), _f(v[1])
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or (abs(lat) < 0.001 and abs(lon) < 0.001):
        return None
    return lat, lon, _f(v[2]) if len(v) > 2 else None


def look_angles(slat, slon, salt, lat, lon, alt):
    """Azimuth (degrees), elevation (degrees) and range (km) from a station to a balloon, on a spherical earth with the ordinary refraction ignored."""
    r = 6371.0088
    p1, p2, dl = math.radians(slat), math.radians(lat), math.radians(lon - slon)
    az = (math.degrees(math.atan2(math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) + 360) % 360
    ground = km(slat, slon, lat, lon)
    h1, h2 = (salt or 0) / 1000.0, (alt or 0) / 1000.0
    theta = ground / r
    x = (r + h2) * math.cos(theta) - (r + h1)
    y = (r + h2) * math.sin(theta)
    el = math.degrees(math.atan2(x, y))
    return az, el, math.hypot(x, y)


def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def boxes():
    """[(south, west, north, east)] from Settings > Radiosondes > Area."""
    out = []
    for part in cfg("sonde_regions").replace("\n", ";").split(";"):
        nums = [_f(x) for x in part.split(",")]
        if len(nums) == 4 and None not in nums:
            out.append(tuple(nums))
    return out


def in_region(lat, lon, bx=None):
    if lat is None or lon is None:
        return False
    for s, w, n, e in (bx if bx is not None else boxes()):
        if s <= lat <= n and w <= lon <= e:
            return True
    return False


def dew_point_c(t_c, rh):
    """Dew point from temperature (C) and relative humidity (%), the Magnus formula (good to a fraction of a degree between -45 and 60 C)."""
    if t_c is None or rh is None or rh <= 0:
        return None
    a, b = 17.62, 243.12
    g = math.log(rh / 100.0) + a * t_c / (b + t_c)
    return b * g / (a - g)


PHASES = {"climbing": "Climbing", "falling": "Burst, falling", "landed": "Landed", "lost": "Signal lost"}


def phase(r):
    """Where a flight is in its life: climbing (the balloon is intact), falling (it has burst and the sonde is coming down), landed, or lost (the signal ended in the air).
    Works on a sondes row or on a dict with the same keys."""
    st = r["status"]
    if st == "landed":
        return "landed"
    if st == "lost":
        return "lost"
    vv, alt, top = r["last_vel_v"], r["last_alt"], r["max_alt"]
    if vv is not None and vv < -1.0:
        return "falling"
    if alt is not None and top is not None and top - alt > 300:
        return "falling"
    return "climbing"


def phase_label(r):
    p = phase(r)
    if p == "falling" and r["max_alt"] is not None:
        return f"Burst at {round(r['max_alt'] * 3.28084):,} ft, falling"
    return PHASES[p]


# ------------------------------------------------------------------ launch sites

def is_nws(site_id):
    sid = str(site_id or "")
    if sid in {x.strip() for x in cfg("sonde_nws_exclude").split(",") if x.strip()}:
        return False
    try:
        return bool(re.fullmatch(cfg("sonde_nws_pattern"), sid))
    except re.error:
        return False


def import_sites(raw=None):
    """Replace the launch-site list from SondeHub's /sites (one request, from Data Updates). `raw` ({id: record}) is for tests."""
    if raw is None:
        req = urllib.request.Request(cfg("sonde_api_url").rstrip("/") + "/sites", headers={"User-Agent": cfg("lookup_user_agent"), "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read(20_000_000)
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                body = gzip.decompress(body)
        raw = json.loads(body.decode("utf-8"))
    rows = []
    for sid, v in raw.items():
        pos = v.get("position") or []
        if len(pos) < 2:
            continue
        lon, lat = _f(pos[0]), _f(pos[1])
        if lat is None or lon is None:
            continue
        types = []
        for t in v.get("rs_types") or []:
            types.append(str(t[0] if isinstance(t, list) and t else t))
        times = [str(t) for t in (v.get("times") or []) if isinstance(t, str)]
        rows.append((str(sid), str(v.get("station_name") or sid)[:200], lat, lon, _f(v.get("alt")), ",".join(types), ",".join(times), is_nws(sid)))
    if not rows:
        raise ValueError("SondeHub returned no launch sites.")
    d = get_db()
    d.execute("TRUNCATE sonde_sites")
    with d.cursor() as cur:
        cur.executemany("INSERT INTO sonde_sites (site_id, name, lat, lon, alt, rs_types, times, nws) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    d.commit()
    _sites_cache["rows"] = None
    set_setting("sonde_sites_stamp", datetime.now(timezone.utc).isoformat())
    n_region = sum(1 for r in rows if in_region(r[2], r[3]))
    set_setting("sonde_sites_summary", f"{len(rows)} launch sites, {n_region} in the area, {sum(1 for r in rows if r[7] and in_region(r[2], r[3]))} National Weather Service")
    return {"sites": len(rows), "region": n_region}


_sites_cache = {"rows": None, "at": 0, "stamp": None}


def sites():
    """Every launch site, kept in this process until the list is replaced (every web worker notices the new stamp) or for ten minutes."""
    stamp = get_setting("sonde_sites_stamp") or ""
    if _sites_cache["rows"] is None or _sites_cache["stamp"] != stamp or time.time() - _sites_cache["at"] > 600:
        _sites_cache.update(rows=[dict(r) for r in get_db().execute("SELECT * FROM sonde_sites").fetchall()], at=time.time(), stamp=stamp)
    return _sites_cache["rows"]


def nearest_site(lat, lon, within_km=None, rows=None):
    best, bd = None, (within_km if within_km is not None else cfg_float("sonde_site_match_km"))
    for s in (rows if rows is not None else sites()):
        d = km(lat, lon, s["lat"], s["lon"])
        if d <= bd:
            best, bd = s, d
    return best


def site_items():
    """Launch sites in the area, as the county pages list them."""
    bx = boxes()
    return [{"id": s["site_id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"], "nws": s["nws"], "href": "/sondes/sites/" + s["site_id"]} for s in sites() if in_region(s["lat"], s["lon"], bx)
            and (s["nws"] or cfg("sonde_county_all_sites") == "1")]


def landing_items(days=30):
    rows = get_db().execute("SELECT serial, type, agency, landing_lat, landing_lon, last_seen FROM sondes WHERE status='landed' AND landing_lat IS NOT NULL AND last_seen > now() - make_interval(days => %s)", (days,)).fetchall()
    return [{"id": r["serial"], "type": r["type"], "lat": r["landing_lat"], "lon": r["landing_lon"], "when": r["last_seen"], "href": "/sondes/" + r["serial"]} for r in rows]


# ------------------------------------------------------------------ receiving stations (privacy and claims)

def public_position(s):
    """The only place a receiving station's coordinates are prepared for public use: hidden gives nothing, general rounds like feeders do, exact is the owner's choice."""
    if s["lat"] is None or s["lon"] is None or (s["privacy"] or "general") == "hidden" or s["mobile"] and s["privacy"] != "exact":
        return None
    from . import privacy
    return privacy.position(s["lat"], s["lon"], s["privacy"] or "general")


def base_call(c):
    """The call sign itself, without whatever follows it: KD5VCF-LR, KD5VCF-10, KD5VCF/P, KD5VCF_4 and kd5vcf Home all give KD5VCF."""
    m = re.match(r"[A-Za-z0-9]+", (c or "").strip())
    return m.group(0).upper() if m else ""


def station(callsign):
    return get_db().execute("SELECT s.*, u.username AS owner_name, u.display_name AS owner_display, u.callsign AS owner_callsign FROM sonde_stations s LEFT JOIN users u ON u.id = s.owner_id "
                            "WHERE lower(s.callsign) = lower(%s)", (callsign,)).fetchone()


def my_stations(user_id):
    return get_db().execute("SELECT * FROM sonde_stations WHERE owner_id=%s ORDER BY callsign", (user_id,)).fetchall()


def claim(user, callsign):
    """Ask to own a receiving station. Returns (status, message). The call sign on the user's profile matching the station makes it immediate; otherwise the person proves control by
    putting a short code into the station's antenna description (auto_rx station.cfg) which the feed then sees, or an administrator approves it."""
    import secrets
    callsign = (callsign or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_ .-]{1,31}", callsign):
        return None, "Enter the station's call sign or name exactly as it appears on SondeHub (letters, numbers, dashes)."
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM sonde_stations WHERE owner_id=%s", (user.id,)).fetchone()["n"] >= cfg_int("sonde_max_per_user"):
        return None, f"One account can claim up to {cfg_int('sonde_max_per_user')} stations."
    row = station(callsign)
    if row and row["owner_id"] and row["owner_id"] != user.id:
        return None, "This station already belongs to another member. If it is yours, contact an administrator."
    if row and row["owner_id"] == user.id:
        return row["claim_status"], "You already have this station."
    mine = base_call(getattr(user, "callsign", "") or "") == base_call(callsign) and base_call(callsign) != ""
    code = "ark-" + secrets.token_hex(3)
    if row is None:
        d.execute("INSERT INTO sonde_stations (callsign, source, privacy) VALUES (%s, 'claim', %s)", (callsign, cfg("sonde_station_privacy")))
        row = station(callsign)
    if mine and cfg("sonde_claim_mode") == "match":
        d.execute("UPDATE sonde_stations SET owner_id=%s, claim_status='claimed', claim_code='', claimed_at=now() WHERE id=%s", (user.id, row["id"]))
        d.commit()
        return "claimed", "Claimed: the call sign matches your profile."
    d.execute("UPDATE sonde_stations SET owner_id=%s, claim_status='pending', claim_code=%s, claimed_at=NULL WHERE id=%s", (user.id, code, row["id"]))
    d.commit()
    return "pending", f"Requested. To prove the station is yours, put the code {code} in the station's antenna description in auto_rx (upload_station_antenna) and let it upload once; the claim then completes by itself. An administrator can also approve it."


def assign(callsign, username, mode="assign", details=None):
    """An administrator puts a receiving station with a member. mode "assign": the station is theirs at once. mode "claim": a claim is started for them (the proof code, or an
    administrator's approval, completes it). `details` are the member's own options (display_name, notes, privacy, public; privacy and listing only when given now). The station is created if it has not been heard yet; one that belongs to someone else is taken over. Returns (ok, message, row)."""
    import secrets
    callsign = (callsign or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_ .-]{1,31}", callsign):
        return False, "Enter the station's call sign exactly as it appears on SondeHub.", None
    d = get_db()
    u = d.execute("SELECT id, username FROM users WHERE lower(username) = lower(%s) OR lower(email) = lower(%s)", ((username or "").strip(), (username or "").strip())).fetchone()
    if u is None:
        return False, "No member has that user name or email address.", None
    row = station(callsign)
    if row is None:
        d.execute("INSERT INTO sonde_stations (callsign, source, privacy) VALUES (%s, 'admin', %s)", (callsign, cfg("sonde_station_privacy")))
        row = station(callsign)
    if mode == "claim":
        d.execute("UPDATE sonde_stations SET owner_id=%s, claim_status='pending', claim_code=%s, claimed_at=NULL WHERE id=%s", (u["id"], "ark-" + secrets.token_hex(3), row["id"]))
        msg = f"A claim was started for {u['username']}. They see it under My Radiosonde Stations with the code to put in the station's antenna description, or you can approve it."
    else:
        d.execute("UPDATE sonde_stations SET owner_id=%s, claim_status='claimed', claim_code='', claimed_at=now() WHERE id=%s", (u["id"], row["id"]))
        msg = f"{row['callsign']} now belongs to {u['username']}."
    if details:
        vals = {k: details[k] for k in ("display_name", "notes") if k in details}
        if mode == "assign":
            vals.update({k: details[k] for k in ("privacy", "public") if k in details})
        if vals:
            d.execute("UPDATE sonde_stations SET " + ", ".join(f"{k}=%s" for k in vals) + " WHERE id=%s", list(vals.values()) + [row["id"]])
    d.commit()
    return True, msg, station(callsign)


# ------------------------------------------------------------------ the feed: messages in, batches out

PRED_PATH_MAX = 150   # points kept of a predicted path (SondeHub sends one every ~15 s to the landing)


def thin_path(path):
    """A predicted path as JSON text [[time, lat, lon, alt], ...], thinned evenly to PRED_PATH_MAX points and always ending on the last one."""
    pts = []
    for p in path:
        if not isinstance(p, dict):
            continue
        la, lo = _f(p.get("lat")), _f(p.get("lon"))
        if la is None or lo is None:
            continue
        pts.append([_f(p.get("time")), round(la, 5), round(lo, 5), _f(p.get("alt"))])
    if len(pts) > PRED_PATH_MAX:
        step = (len(pts) - 1) / (PRED_PATH_MAX - 1)
        pts = [pts[round(i * step)] for i in range(PRED_PATH_MAX)]
    return json.dumps(pts, separators=(",", ":"))


class Collector:
    """Turns feed messages into rows. handle() is called for every message (cheap: filters and aggregates in memory); flush() writes a batch."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()
        self.keep_every = 5
        self.last_kept = {}      # serial -> last stored point time (seconds), to keep a flight at one point per interval
        self.known = {}          # serial -> time we last knew it was in our area (so a flight that leaves the box is still followed)
        self.spos = {}           # uploader -> (lat, lon) of its station, to measure its reach
        self.bx = []
        self.sites = []
        self.backfill = queue.Queue()
        self.asked = set()

    def reset(self):
        self.points, self.flights, self.heard, self.stations, self.preds = [], {}, {}, {}, {}

    def configure(self):
        self.keep_every = max(1, cfg_int("sonde_keep_every_seconds"))
        self.bx = boxes()
        self.sites = sites()
        self.amateur = cfg("sonde_amateur_enabled") == "1"

    def handle(self, topic, raw):
        STATE["msgs"] += 1
        try:
            m = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(m, dict):
            return
        head = topic.split("/", 1)[0]
        if head in ("sondes", "amateur"):
            if head == "amateur" and not self.amateur:
                return
            self.telemetry(m, head)
        elif head in ("listener", "amateur-listener"):
            self.listener(m)
        elif head == "prediction":
            self.prediction(m)

    def telemetry(self, m, head):
        serial = str(m.get("serial") or m.get("payload_callsign") or "").strip()
        lat, lon, alt = _f(m.get("lat")), _f(m.get("lon")), _f(m.get("alt"))
        t = parse_time(m.get("datetime"))
        if not serial or lat is None or lon is None or t is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return
        now = time.time()
        if not in_region(lat, lon, self.bx) and serial not in self.known:
            return
        self.known[serial] = now
        STATE["kept"] += 1
        up = str(m.get("uploader_callsign") or "").strip()
        ts = t.timestamp()
        with self.lock:
            fl = self.flights.get(serial)
            if fl is None:
                fl = self.flights[serial] = {"serial": serial, "kind": "amateur" if head == "amateur" else "radiosonde", "type": "", "subtype": "", "manufacturer": "", "frequency": None,
                                              "first": t, "last": t, "last_pt": None, "max_alt": None, "max_ts": None, "max_pos": None, "n": 0, "first_pt": None}
            for k in ("type", "subtype", "manufacturer"):
                if m.get(k):
                    fl[k] = str(m[k])[:40]
            if _f(m.get("frequency")):
                fl["frequency"] = _f(m.get("frequency"))
            fl["n"] += 1
            frame = m.get("frame")
            pt = (serial, t, lat, lon, alt, _f(m.get("vel_v")), _f(m.get("vel_h")), _f(m.get("heading")), _f(m.get("temp")), _f(m.get("humidity")), _f(m.get("pressure")), m.get("sats"),
                  _f(m.get("batt")), _f(m.get("frequency")), _f(m.get("snr")), _f(m.get("rssi")), up[:40], frame if isinstance(frame, int) and 0 <= frame < 2**31 else None)
            if fl["first_pt"] is None or t < fl["first"]:
                fl["first"] = min(fl["first"], t)
                if fl["first_pt"] is None or t <= fl["first_pt"][1]:
                    fl["first_pt"] = pt
            if t >= fl["last"]:
                fl["last"], fl["last_pt"] = t, pt
            if alt is not None and (fl["max_alt"] is None or alt > fl["max_alt"]):
                fl["max_alt"], fl["max_ts"], fl["max_pos"] = alt, t, (lat, lon)
            if up:
                h = self.heard.setdefault((serial, up), [0, t, t, None, None, None])
                h[0] += 1
                snr = _f(m.get("snr"))
                if t >= h[2]:
                    h[4], h[5] = snr, _f(m.get("frequency"))   # the latest signal of this station on this flight
                h[1], h[2] = min(h[1], t), max(h[2], t)
                if snr is not None and (h[3] is None or snr > h[3]):
                    h[3] = snr
                self.note_station(m, up)
            prev = self.last_kept.get(serial, 0)
            if ts - prev >= self.keep_every or ts < prev:
                self.last_kept[serial] = ts
                self.points.append(pt)

    def note_station(self, m, up):
        pos = pos3(m.get("uploader_position")) or pos3(m.get("uploader_position_elk"))
        if pos is None:
            return
        self.spos[up] = (pos[0], pos[1])
        self.stations[up] = {"callsign": up, "lat": pos[0], "lon": pos[1], "alt": pos[2] if pos[2] is not None else _f(m.get("uploader_alt")), "antenna": str(m.get("uploader_antenna") or "")[:200],
                             "radio": str(m.get("uploader_radio") or "")[:200], "software": str(m.get("software_name") or "")[:60], "version": str(m.get("software_version") or "")[:40],
                             "mobile": bool(m.get("mobile"))}

    def listener(self, m):
        up = str(m.get("uploader_callsign") or "").strip()
        pos = pos3(m.get("uploader_position")) or pos3(m.get("uploader_position_elk"))
        if not up or pos is None:
            return
        if in_region(pos[0], pos[1], self.bx):
            with self.lock:
                self.note_station(m, up)

    def prediction(self, m):
        serial = str(m.get("serial") or m.get("vehicle") or "")
        if serial not in self.known:
            return
        path = m.get("data")
        if isinstance(path, str):
            try:
                path = json.loads(path)
            except ValueError:
                path = None
        if not isinstance(path, list) or not path:
            return
        last = path[-1]
        lat, lon, tt = _f(last.get("lat")), _f(last.get("lon")), _f(last.get("time"))
        if lat is None or lon is None:
            return
        with self.lock:
            self.preds[serial] = (lat, lon, datetime.fromtimestamp(tt, timezone.utc) if tt else None, bool(m.get("landed")), _f(m.get("burst_altitude")), thin_path(path))

    # ---- writing
    def prune(self):
        cut = time.time() - 86400
        self.known = {k: v for k, v in self.known.items() if v > cut}
        self.last_kept = {k: v for k, v in self.last_kept.items() if k in self.known}
        if len(self.asked) > 5000:
            self.asked = set(list(self.asked)[-1000:])

    def flush(self, conn):
        self.prune()
        with self.lock:
            points, flights, heard, stations, preds = self.points, self.flights, self.heard, self.stations, self.preds
            self.reset()
        if not (points or flights or heard or stations or preds):
            return 0
        cur = conn.cursor()
        for serial, fl in flights.items():
            lp = fl["last_pt"]
            cur.execute(
                "INSERT INTO sondes (serial, kind, type, subtype, manufacturer, frequency, first_seen, last_seen, last_lat, last_lon, last_alt, last_vel_v, last_temp, max_alt, max_alt_ts, frames, status, max_lat, max_lon) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'aloft',%s,%s) "
                "ON CONFLICT (serial) DO UPDATE SET "
                "kind = EXCLUDED.kind, type = COALESCE(NULLIF(EXCLUDED.type, ''), sondes.type), subtype = COALESCE(NULLIF(EXCLUDED.subtype, ''), sondes.subtype), "
                "manufacturer = COALESCE(NULLIF(EXCLUDED.manufacturer, ''), sondes.manufacturer), frequency = COALESCE(EXCLUDED.frequency, sondes.frequency), "
                "first_seen = LEAST(sondes.first_seen, EXCLUDED.first_seen), "
                "last_lat = CASE WHEN EXCLUDED.last_seen >= sondes.last_seen THEN EXCLUDED.last_lat ELSE sondes.last_lat END, "
                "last_lon = CASE WHEN EXCLUDED.last_seen >= sondes.last_seen THEN EXCLUDED.last_lon ELSE sondes.last_lon END, "
                "last_alt = CASE WHEN EXCLUDED.last_seen >= sondes.last_seen THEN EXCLUDED.last_alt ELSE sondes.last_alt END, "
                "last_vel_v = CASE WHEN EXCLUDED.last_seen >= sondes.last_seen THEN EXCLUDED.last_vel_v ELSE sondes.last_vel_v END, "
                "last_temp = CASE WHEN EXCLUDED.last_seen >= sondes.last_seen THEN EXCLUDED.last_temp ELSE sondes.last_temp END, "
                "max_alt_ts = CASE WHEN EXCLUDED.max_alt > COALESCE(sondes.max_alt, -1e9) THEN EXCLUDED.max_alt_ts ELSE sondes.max_alt_ts END, "
                "max_lat = CASE WHEN EXCLUDED.max_alt > COALESCE(sondes.max_alt, -1e9) THEN EXCLUDED.max_lat ELSE sondes.max_lat END, "
                "max_lon = CASE WHEN EXCLUDED.max_alt > COALESCE(sondes.max_alt, -1e9) THEN EXCLUDED.max_lon ELSE sondes.max_lon END, "
                "max_alt = GREATEST(sondes.max_alt, EXCLUDED.max_alt), frames = sondes.frames + EXCLUDED.frames, "
                "status = CASE WHEN EXCLUDED.last_seen >= sondes.last_seen THEN 'aloft' ELSE sondes.status END, last_seen = GREATEST(sondes.last_seen, EXCLUDED.last_seen)",
                (serial, fl["kind"], fl["type"], fl["subtype"], fl["manufacturer"], fl["frequency"], fl["first"], fl["last"], lp[2], lp[3], lp[4], lp[5], lp[8], fl["max_alt"], fl["max_ts"], fl["n"],
                 fl["max_pos"][0] if fl["max_pos"] else None, fl["max_pos"][1] if fl["max_pos"] else None))
        if points:
            cur.executemany("INSERT INTO sonde_points (serial, ts, lat, lon, alt, vel_v, vel_h, heading, temp, hum, press, sats, batt, freq, snr, rssi, uploader, frame) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", points)
        for (serial, up), h in heard.items():
            cur.execute("INSERT INTO sonde_heard (serial, callsign, packets, first_ts, last_ts, best_snr, last_snr, last_freq) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (serial, callsign) DO UPDATE SET "
                        "packets = sonde_heard.packets + EXCLUDED.packets, first_ts = LEAST(sonde_heard.first_ts, EXCLUDED.first_ts), "
                        "last_snr = CASE WHEN EXCLUDED.last_ts >= sonde_heard.last_ts THEN EXCLUDED.last_snr ELSE sonde_heard.last_snr END, "
                        "last_freq = CASE WHEN EXCLUDED.last_ts >= sonde_heard.last_ts THEN EXCLUDED.last_freq ELSE sonde_heard.last_freq END, "
                        "last_ts = GREATEST(sonde_heard.last_ts, EXCLUDED.last_ts), best_snr = GREATEST(sonde_heard.best_snr, EXCLUDED.best_snr)", (serial, up, h[0], h[1], h[2], h[3], h[4], h[5]))
        for up, s in stations.items():
            mobile_default = "hidden" if s["mobile"] else cfg("sonde_station_privacy")
            cur.execute(
                "INSERT INTO sonde_stations (callsign, lat, lon, alt, antenna, radio, software, version, mobile, privacy, source, last_seen) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'sondehub', now()) "
                "ON CONFLICT (lower(callsign)) DO UPDATE SET lat = EXCLUDED.lat, lon = EXCLUDED.lon, alt = EXCLUDED.alt, antenna = EXCLUDED.antenna, radio = EXCLUDED.radio, "
                "software = EXCLUDED.software, version = EXCLUDED.version, mobile = EXCLUDED.mobile, last_seen = now()",
                (up, s["lat"], s["lon"], s["alt"], s["antenna"], s["radio"], s["software"], s["version"], s["mobile"], mobile_default))
            # a claim waiting for its proof: the code in the antenna text completes it
            cur.execute("UPDATE sonde_stations SET claim_status='claimed', claimed_at=now() WHERE lower(callsign)=lower(%s) AND claim_status='pending' AND claim_code <> '' AND position(lower(claim_code) in lower(%s)) > 0",
                        (up, s["antenna"] + " " + s["radio"]))
            auto_claim_station(cur, up)
        reach, secs = {}, {}
        for pt in points:   # the farthest balloon each station has heard (kilometers; only this number is ever shown, never a direction)
            sp = self.spos.get(pt[16])
            if sp and pt[2] is not None:
                km_ = km(sp[0], sp[1], pt[2], pt[3])
                if km_ > reach.get(pt[16], 0):
                    reach[pt[16]] = km_
                if km_ > 1:
                    brg = (math.degrees(math.atan2(math.sin(math.radians(pt[3] - sp[1])) * math.cos(math.radians(pt[2])), math.cos(math.radians(sp[0])) * math.sin(math.radians(pt[2])) - math.sin(math.radians(sp[0])) * math.cos(math.radians(pt[2])) * math.cos(math.radians(pt[3] - sp[1])))) + 360) % 360
                    sec_ = secs.setdefault(pt[16], [0.0] * 36)
                    i_ = int(brg // 10) % 36
                    if km_ > sec_[i_]:
                        sec_[i_] = km_
        for up, r_ in reach.items():
            cur.execute("UPDATE sonde_stations SET reach_km = GREATEST(COALESCE(reach_km, 0), %s) WHERE lower(callsign) = lower(%s)", (round(r_, 1), up))
        for up, new in secs.items():   # the farthest in each ten degree sector, kept over time
            row = cur.execute("SELECT reach_sectors FROM sonde_stations WHERE lower(callsign) = lower(%s)", (up,)).fetchone()
            try:
                old = json.loads(row["reach_sectors"]) if row and row["reach_sectors"] else [0.0] * 36
            except ValueError:
                old = [0.0] * 36
            merged = [round(max(a, b), 1) for a, b in zip(old + [0.0] * 36, new)]
            cur.execute("UPDATE sonde_stations SET reach_sectors = %s WHERE lower(callsign) = lower(%s)", (json.dumps(merged), up))
        for serial, p in preds.items():
            cur.execute("UPDATE sondes SET pred_lat=%s, pred_lon=%s, pred_time=%s, pred_burst=%s, pred_path=%s, pred_at=now() WHERE serial=%s", (p[0], p[1], p[2], p[4], p[5], serial))
        for serial, fl in flights.items():
            if fl["first_pt"] is not None:
                self.assign_launch(cur, serial, fl)
        conn.commit()
        return len(points)

    def assign_launch(self, cur, serial, fl):
        """Find the launch site for a flight the first time we see its start; one that began before we heard it is filled in later from the API."""
        row = cur.execute("SELECT launch_checked, first_seen FROM sondes WHERE serial=%s", (serial,)).fetchone()
        if row is None or row["launch_checked"]:
            return
        fp = fl["first_pt"]
        if fp[4] is not None and fp[4] < 3500:
            site = None if fl.get("kind") == "amateur" else nearest_site(fp[2], fp[3], rows=self.sites)   # launch sites are the weather services' (and other official) sites: an amateur balloon goes up from anywhere and is never an NWS launch
            cur.execute("UPDATE sondes SET launch_checked=TRUE, launch_lat=%s, launch_lon=%s, launch_alt=%s, launch_time=%s, site_id=%s, site_name=%s, agency=%s WHERE serial=%s",
                        (fp[2], fp[3], fp[4], fp[1], site["site_id"] if site else "", site["name"] if site else "", "NWS" if site and site["nws"] else "", serial))
        elif cfg("sonde_backfill") == "1" and serial not in self.asked:
            self.asked.add(serial)
            self.backfill.put(serial)


# ------------------------------------------------------------------ a station sending what it hears straight to us (auto_rx's payload summary, through the small bridge program)
# No other option of auto_rx can reach a private address, and a station that already uploads to SondeHub needs none of this: the live feed above counts it. This is for stations that want to
# be counted at once, or whose telemetry SondeHub never gets. The frames go through the same Collector as the feed, so a flight is one flight whichever way it arrived.

def push_hash(token):
    import hashlib
    return hashlib.sha256(("sondepush\0" + token).encode()).hexdigest()


def make_push_token(station_id):
    """A new private upload address token for a station; the old one stops working."""
    import secrets
    from . import vault
    tok = secrets.token_urlsafe(24)
    d = get_db()
    d.execute("UPDATE sonde_stations SET push_hash=%s, push_sealed=%s WHERE id=%s", (push_hash(tok), vault.seal(tok), station_id))
    d.commit()
    return tok


def push_token_of(row):
    from . import vault
    return vault.unseal(row["push_sealed"]) if row["push_sealed"] else ""


def station_by_push_token(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token or ""):
        return None
    return get_db().execute("SELECT * FROM sonde_stations WHERE push_hash=%s AND push_hash <> ''", (push_hash(token),)).fetchone()


def summary_to_feed(m, station):
    """One payload summary from auto_rx (callsign, latitude, longitude, altitude, speed km/h, heading, time HH:MM:SS UTC, model, frequency MHz, snr, temp, humidity, pressure, batt, sats, vel_v)
    or a message already in SondeHub's form, as a SondeHub style telemetry message from this station. None when it is not a usable frame."""
    if not isinstance(m, dict):
        return None
    serial = str(m.get("serial") or m.get("callsign") or "").strip()[:40]
    lat, lon = _f(m.get("lat") if m.get("lat") is not None else m.get("latitude")), _f(m.get("lon") if m.get("lon") is not None else m.get("longitude"))
    alt = _f(m.get("alt") if m.get("alt") is not None else m.get("altitude"))
    t = parse_time(m.get("datetime")) or parse_time(m.get("time_received"))
    if t is None and re.fullmatch(r"\d\d:\d\d:\d\d", str(m.get("time") or "")):
        now = datetime.now(timezone.utc)
        h, mi, se = (int(x) for x in m["time"].split(":"))
        t = now.replace(hour=h, minute=mi, second=se, microsecond=0)
        if t - now > timedelta(hours=1):
            t -= timedelta(days=1)
    if not serial or lat is None or lon is None or t is None or alt is None:
        return None
    if t > datetime.now(timezone.utc) + timedelta(minutes=10) or t < datetime.now(timezone.utc) - timedelta(days=2):
        return None
    speed = _f(m.get("vel_h"))
    if speed is None and _f(m.get("speed")) is not None:
        speed = _f(m.get("speed")) / 3.6   # the summary gives km/h, the feed meters per second
    out = {"serial": serial, "lat": lat, "lon": lon, "alt": alt, "datetime": t.isoformat(), "vel_h": speed, "vel_v": _f(m.get("vel_v")), "heading": _f(m.get("heading")),
           "temp": _f(m.get("temp")), "humidity": _f(m.get("humidity")), "pressure": _f(m.get("pressure")), "batt": _f(m.get("batt")), "sats": m.get("sats"),
           "type": str(m.get("model") or (m.get("type") if m.get("type") != "PAYLOAD_SUMMARY" else "") or "")[:40], "frequency": _f(m.get("frequency")), "snr": _f(m.get("snr")), "rssi": _f(m.get("rssi")),
           "uploader_callsign": station["callsign"], "software_name": "radiosonde_auto_rx (direct)"}
    if station["lat"] is not None and station["lon"] is not None:
        out["uploader_position"] = [station["lat"], station["lon"], station["alt"]]
    if station["antenna"]:
        out["uploader_antenna"] = station["antenna"]
    return out


def push_frames(station, items):
    """Store the frames a station sent. Returns (accepted, ignored). Frames outside the area we follow are ignored like the feed's."""
    col = Collector()
    col.configure()
    n = bad = 0
    for m in items[:500]:
        f = summary_to_feed(m, station)
        if f is None:
            bad += 1
            continue
        col.telemetry(f, "amateur" if str(m.get("kind") or "") == "amateur" else "sondes")
        n += 1
    import psycopg
    from flask import current_app
    from psycopg.rows import dict_row
    conn = psycopg.connect(current_app.config["DATABASE_URL"], row_factory=dict_row)
    try:
        col.flush(conn)
    finally:
        conn.close()
    d = get_db()
    d.execute("UPDATE sonde_stations SET push_last=now(), push_frames=push_frames+%s, last_seen=now() WHERE id=%s", (n, station["id"]))
    d.commit()
    return n, bad


def assign_states(conn, limit=500):
    """Give flights and receiving stations that have no state yet the one their position is in (a flight: where it launched, else where it was last heard). Returns how many were done."""
    from . import states
    n = 0
    for table, cols in (("sondes", "COALESCE(launch_lat, last_lat) AS lat, COALESCE(launch_lon, last_lon) AS lon"), ("sonde_stations", "lat, lon")):
        key = "serial" if table == "sondes" else "id"
        rows = conn.execute(f"SELECT {key} AS k, {cols} FROM {table} WHERE state IS NULL LIMIT %s", (limit,)).fetchall()
        for r in rows:
            conn.execute(f"UPDATE {table} SET state=%s WHERE {key}=%s", (states.state_of(r["lat"], r["lon"]) if r["lat"] is not None else "-", r["k"]))
        n += len(rows)
    conn.commit()
    return n


def sweep(conn):
    """Flights that have gone quiet are over: landed if they were low, lost if the signal ended at height. The landing is the last position heard."""
    mins = max(2, cfg_int("sonde_stale_minutes"))
    conn.execute("UPDATE sondes SET status = CASE WHEN COALESCE(last_alt, 0) < %s THEN 'landed' ELSE 'lost' END, "
                 "landing_lat = CASE WHEN COALESCE(last_alt, 0) < %s THEN last_lat END, landing_lon = CASE WHEN COALESCE(last_alt, 0) < %s THEN last_lon END, landing_alt = CASE WHEN COALESCE(last_alt, 0) < %s THEN last_alt END "
                 "WHERE status = 'aloft' AND last_seen < now() - make_interval(mins => %s)", (cfg_float("sonde_landed_below_m"),) * 4 + (mins,))
    conn.commit()
    assign_states(conn)


def thin(conn):
    """Old flights keep fewer points (one every N seconds), the very old are dropped; flights and their summaries are kept for good."""
    full, step, keep = cfg_int("sonde_full_days"), max(1, cfg_int("sonde_thin_seconds")), cfg_int("sonde_keep_days")
    if full > 0:
        conn.execute("DELETE FROM sonde_points p USING (SELECT serial, ts, row_number() OVER (PARTITION BY serial, floor(extract(epoch FROM ts) / %s) ORDER BY ts) AS rn FROM sonde_points "
                     "WHERE ts < now() - make_interval(days => %s) AND serial NOT IN (SELECT serial FROM sondes WHERE source = 'archive')) x WHERE p.serial = x.serial AND p.ts = x.ts AND x.rn > 1", (step, full))
    if keep > 0:
        conn.execute("DELETE FROM sonde_points WHERE ts < now() - make_interval(days => %s) AND serial NOT IN (SELECT serial FROM sondes WHERE source = 'archive')", (keep,))
    conn.commit()


def fetch_flight(serial):
    """One request to SondeHub for the whole flight so far (used once per flight we joined late)."""
    url = cfg("sonde_api_url").rstrip("/") + "/sonde/" + urllib.request.quote(serial)
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent"), "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read(60_000_000)
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            body = gzip.decompress(body)
    return json.loads(body.decode("utf-8"))


def backfill_one(conn, col, serial, packets=None):
    """Fill in the start of a flight from SondeHub's history: points (thinned to our interval), the launch position and its site."""
    data = packets if packets is not None else fetch_flight(serial)
    step = max(1, cfg_int("sonde_keep_every_seconds"))
    last, rows = 0, []
    first = None
    for p in sorted((x for x in data if isinstance(x, dict)), key=lambda x: x.get("datetime") or ""):
        t = parse_time(p.get("datetime"))
        lat, lon = _f(p.get("lat")), _f(p.get("lon"))
        if t is None or lat is None or lon is None:
            continue
        if first is None:
            first = (t, lat, lon, _f(p.get("alt")))
        if t.timestamp() - last >= step:
            last = t.timestamp()
            rows.append((serial, t, lat, lon, _f(p.get("alt")), _f(p.get("vel_v")), _f(p.get("vel_h")), _f(p.get("heading")), _f(p.get("temp")), _f(p.get("humidity")), _f(p.get("pressure")),
                         p.get("sats"), _f(p.get("batt")), _f(p.get("frequency")), _f(p.get("snr")), _f(p.get("rssi")), str(p.get("uploader_callsign") or "")[:40],
                         p.get("frame") if isinstance(p.get("frame"), int) and 0 <= p.get("frame") < 2**31 else None))
    cur = conn.cursor()
    if rows:
        cur.executemany("INSERT INTO sonde_points (serial, ts, lat, lon, alt, vel_v, vel_h, heading, temp, hum, press, sats, batt, freq, snr, rssi, uploader, frame) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", rows)
        mx = max((r for r in rows if r[4] is not None), key=lambda r: r[4], default=None)
        cur.execute("UPDATE sondes SET first_seen = LEAST(first_seen, %s), "
                    "max_lat = CASE WHEN %s > COALESCE(max_alt, -1e9) THEN %s ELSE max_lat END, max_lon = CASE WHEN %s > COALESCE(max_alt, -1e9) THEN %s ELSE max_lon END, "
                    "max_alt_ts = CASE WHEN %s > COALESCE(max_alt, -1e9) THEN %s ELSE max_alt_ts END, max_alt = GREATEST(COALESCE(max_alt, -1e9), %s) WHERE serial=%s",
                    (rows[0][1], mx[4] if mx else None, mx[2] if mx else None, mx[4] if mx else None, mx[3] if mx else None, mx[4] if mx else None, mx[1] if mx else None, mx[4] if mx else None, serial))
    if first and first[3] is not None and first[3] < 3500:
        kind = (conn.execute("SELECT kind FROM sondes WHERE serial=%s", (serial,)).fetchone() or {}).get("kind")
        site = None if kind == "amateur" else nearest_site(first[1], first[2], rows=sites())   # see assign_launch: no launch site, no NWS, for an amateur balloon
        cur.execute("UPDATE sondes SET launch_checked=TRUE, launch_lat=%s, launch_lon=%s, launch_alt=%s, launch_time=%s, site_id=%s, site_name=%s, agency=%s WHERE serial=%s",
                    (first[1], first[2], first[3], first[0], site["site_id"] if site else "", site["name"] if site else "", "NWS" if site and site["nws"] else "", serial))
    else:
        cur.execute("UPDATE sondes SET launch_checked=TRUE WHERE serial=%s", (serial,))
    conn.commit()
    return len(rows)


def base_sql(col):
    """SQL for the part of a call sign before a dash or a slash, in upper case (KD5VCF-LR, KD5VCF/P and kd5vcf all give KD5VCF)."""
    return f"upper(substring(trim({col}) from '^[A-Za-z0-9]+'))"


def auto_claim_station(cur, callsign):
    """A station whose call sign (without a suffix such as -LR, -10 or /P) is the profile call sign of exactly one member belongs to that member, if claiming by matching call sign is on.
    Returns True when it was claimed."""
    if cfg("sonde_claim_mode") != "match" or cfg("sonde_auto_claim") != "1":
        return False
    b = base_call(callsign)
    if not b:
        return False
    owners = cur.execute(f"SELECT id FROM users WHERE NOT disabled AND {base_sql('callsign')} = %s", (b,)).fetchall()
    if len(owners) != 1:
        return False
    uid = owners[0]["id"]
    n = cur.execute("SELECT COUNT(*) AS n FROM sonde_stations WHERE owner_id=%s", (uid,)).fetchone()["n"]
    if n >= cfg_int("sonde_max_per_user"):
        return False
    cur.execute("UPDATE sonde_stations SET owner_id=%s, claim_status='claimed', claim_code='', claimed_at=now() WHERE lower(callsign)=lower(%s) AND owner_id IS NULL", (uid, callsign))
    return cur.rowcount > 0


def auto_claim_for_user(user):
    """Claims every unowned station whose call sign matches the member's profile call sign (their stations, whatever the suffix). Returns the call signs claimed."""
    if cfg("sonde_claim_mode") != "match" or cfg("sonde_auto_claim") != "1":
        return []
    b = base_call(getattr(user, "callsign", "") or "")
    d = get_db()
    if not b or d.execute(f"SELECT COUNT(*) AS n FROM users WHERE NOT disabled AND {base_sql('callsign')} = %s", (b,)).fetchone()["n"] != 1:
        return []
    room = cfg_int("sonde_max_per_user") - d.execute("SELECT COUNT(*) AS n FROM sonde_stations WHERE owner_id=%s", (user.id,)).fetchone()["n"]
    if room <= 0:
        return []
    ids = d.execute(f"SELECT id, callsign FROM sonde_stations WHERE owner_id IS NULL AND {base_sql('callsign')} = %s ORDER BY last_seen DESC NULLS LAST LIMIT %s", (b, room)).fetchall()
    for r in ids:
        d.execute("UPDATE sonde_stations SET owner_id=%s, claim_status='claimed', claim_code='', claimed_at=now() WHERE id=%s", (user.id, r["id"]))
    d.commit()
    return [r["callsign"] for r in ids]


def repair_positions(conn):
    for _ in range(400):   # every flight and station that has no state yet (the first start after the update)
        if not assign_states(conn, 1000):
            break
    """Stations whose position was read wrongly (the text form of a position taken as a list gave whole numbers such as 3.0, 4.0) lose it, and get the right one at their next upload."""
    conn.execute("UPDATE sonde_stations SET lat = NULL, lon = NULL WHERE lat IS NOT NULL AND lat = floor(lat) AND lon = floor(lon) AND abs(lat) < 10 AND abs(lon) < 10 AND alt IS NULL")
    # the reach of stations that have already heard balloons (once)
    if not get_setting("sonde_reach_done"):
        conn.execute("UPDATE sonde_stations s SET reach_km = x.km FROM (SELECT st.id, MAX(2 * 6371.0088 * asin(least(1, sqrt(power(sin(radians(p.lat - st.lat) / 2), 2) + cos(radians(st.lat)) * cos(radians(p.lat)) * power(sin(radians(p.lon - st.lon) / 2), 2))))) AS km "
                     "FROM sonde_stations st JOIN sonde_points p ON lower(p.uploader) = lower(st.callsign) WHERE st.lat IS NOT NULL GROUP BY st.id) x WHERE s.id = x.id")
        conn.commit()
        set_setting("sonde_reach_done", "1")
    # flights recorded before the burst position was kept get it from their stored points
    conn.execute("UPDATE sondes s SET max_lat = p.lat, max_lon = p.lon FROM (SELECT DISTINCT ON (serial) serial, lat, lon FROM sonde_points WHERE alt IS NOT NULL ORDER BY serial, alt DESC) p "
                 "WHERE s.serial = p.serial AND s.max_lat IS NULL")
    conn.commit()


def backfill_station_positions(conn, raw=None):
    """ONCE: stations that lost a wrongly read position (see repair_positions) get the one from SondeHub's listener history (a single request for the last day; the API reference asks for
    history to be read once, never polled), instead of waiting for their next upload. Only stations that have no position are filled. Returns how many."""
    if raw is None:
        url = cfg("sonde_api_url").rstrip("/") + "/listeners/telemetry?duration=1d"
        req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent"), "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=90) as r:
            body = r.read(60_000_000)
            if body[:2] == b"\x1f\x8b":
                import gzip
                body = gzip.decompress(body)
        raw = json.loads(body.decode("utf-8"))
    if not isinstance(raw, dict):
        return 0
    n = 0
    cur = conn.cursor()
    for call, hist in raw.items():
        if not isinstance(hist, dict) or not hist:
            continue
        m = hist[max(hist)]   # the newest entry (the keys are ISO times)
        pos = pos3(m.get("uploader_position")) or pos3(m.get("uploader_position_elk"))
        if pos is None:
            continue
        cur.execute("UPDATE sonde_stations SET lat=%s, lon=%s, alt=COALESCE(%s, alt) WHERE lower(callsign)=lower(%s) AND lat IS NULL", (pos[0], pos[1], pos[2], str(call)))
        n += cur.rowcount
    conn.commit()
    return n


def fetch_recoveries(conn, raw=None):
    """SondeHub's recovery reports (people reporting that a sonde was found, or that they looked and did not find it) for flights near the area: ONE request, from the feed's owner about hourly."""
    if raw is None:
        c = boxes()
        lat = sum((b[0] + b[2]) / 2 for b in c) / len(c) if c else 35.0
        lon = sum((b[1] + b[3]) / 2 for b in c) / len(c) if c else -92.0
        url = (cfg("sonde_api_url").rstrip("/") + f"/recovered?lat={lat:.3f}&lon={lon:.3f}&distance={int(cfg_float('sonde_recovery_radius_km') * 1000)}&last={cfg_int('sonde_recovery_days') * 86400}")
        req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent"), "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read(20_000_000)
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                body = gzip.decompress(body)
        raw = json.loads(body.decode("utf-8"))
    n = 0
    cur = conn.cursor()
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        t = parse_time(x.get("datetime"))
        serial = str(x.get("serial") or "").strip()
        if not serial or t is None:
            continue
        # a report one of our members sent comes back in this list under SondeHub's own time: keep SondeHub's copy, drop ours
        cur.execute("DELETE FROM sonde_recoveries WHERE serial=%s AND source='portal' AND sent='sent' AND recovered_by=%s AND abs(extract(epoch FROM ts - %s)) < 900", (serial, str(x.get("recovered_by") or "")[:120], t))
        cur.execute("INSERT INTO sonde_recoveries (serial, ts, recovered, planned, recovered_by, description, lat, lon, alt, software) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (serial, ts) DO UPDATE SET recovered=EXCLUDED.recovered, planned=EXCLUDED.planned, recovered_by=EXCLUDED.recovered_by, description=EXCLUDED.description",
                    (serial, t, bool(x.get("recovered")), bool(x.get("planned")), str(x.get("recovered_by") or "")[:120], str(x.get("description") or "")[:600], _f(x.get("lat")), _f(x.get("lon")), _f(x.get("alt")),
                     str(x.get("recovery_software") or "")[:60]))
        n += 1
    conn.commit()
    return n


def recoveries_for(serial):
    return get_db().execute("SELECT * FROM sonde_recoveries WHERE serial=%s ORDER BY ts DESC", (serial,)).fetchall()


# ------------------------------------------------------------------ the feed's owner (one web worker at a time)

def _presigned_url():
    req = urllib.request.Request(cfg("sonde_api_url").rstrip("/") + "/sondes/websocket", headers={"User-Agent": cfg("lookup_user_agent")})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(2000).decode().strip()


def _connect(col):
    """A new MQTT-over-websocket client subscribed to the topics; returns it (its network loop runs in its own thread)."""
    from urllib.parse import urlparse
    import paho.mqtt.client as mqtt
    url = _presigned_url()
    u = urlparse(url)
    cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport="websockets")
    cl.ws_set_options(path=u.path + ("?" + u.query if u.query else ""), headers={"Host": u.netloc})
    cl.tls_set()

    def on_connect(c, ud, flags, rc, props=None):
        STATE["connected"] = (getattr(rc, "value", rc) == 0)
        STATE["error"] = "" if STATE["connected"] else f"refused ({rc})"
        for t in TOPICS:
            c.subscribe(t)

    def on_disconnect(c, ud, *a):
        STATE["connected"] = False

    def on_message(c, ud, msg):
        col.handle(msg.topic, msg.payload)
    cl.on_connect, cl.on_disconnect, cl.on_message = on_connect, on_disconnect, on_message
    cl.reconnect_delay_set(min_delay=2, max_delay=30)
    cl.connect(u.netloc, 443, 60)
    cl.loop_start()
    return cl


def _save_stats(app):
    with app.app_context():
        elapsed = max(1.0, time.time() - STATE["since"])
        set_setting("sonde_feed_stats", json.dumps({"msgs": STATE["msgs"], "kept": STATE["kept"], "per_min": round(STATE["msgs"] / elapsed * 60), "connected": STATE["connected"], "error": STATE["error"],
                                                     "at": datetime.now(timezone.utc).isoformat()}))


def run_leader(app, should_stop=lambda: False):
    """Hold the feed until it is switched off, the lock is lost or the connection cannot be kept. Blocks."""
    import psycopg
    from psycopg.rows import dict_row
    col = Collector()
    with app.app_context():
        col.configure()
    cl, last_msg, last_flush, last_sweep, last_stats, last_cfg, last_rec, last_pred = None, time.time(), 0, 0, 0, time.time(), 0, 0
    conn = psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row)
    try:
        repair_positions(conn)
    except Exception:
        conn.rollback()
    try:
        with app.app_context():
            if cfg("sonde_backfill") == "1" and not get_setting("sonde_positions_backfilled"):
                n = backfill_station_positions(conn)
                set_setting("sonde_positions_backfilled", "1")
    except Exception as e:   # tried again at the next start
        conn.rollback()
        STATE["error"] = f"station positions: {type(e).__name__}: {e}"[:200]
    STATE.update(msgs=0, kept=0, since=time.time(), error="")
    try:
        from . import sonde_history
        sonde_history.start(app)   # the historical flights job (switched on in Settings) lives in the process that holds the feed
    except Exception:
        pass
    seen = 0
    try:
        while not should_stop():
            now = time.time()
            with app.app_context():
                if not enabled() or cfg("sonde_live_enabled") != "1":
                    return
                rec_min = cfg_int("sonde_recovery_minutes")
                if now - last_cfg > 120:
                    col.configure(); last_cfg = now
            if now - last_stats >= 30:
                last_stats = now
                _save_stats(app)
            if cl is None:
                try:
                    with app.app_context():
                        cl = _connect(col)
                    last_msg = now
                except Exception as e:
                    STATE["error"] = f"{type(e).__name__}: {e}"[:200]
                    time.sleep(15)
                    continue
            if STATE["msgs"] != seen:
                seen = STATE["msgs"]; last_msg = now
            elif now - last_msg > 180:   # connected but silent for three minutes: start over with a fresh address
                try:
                    cl.loop_stop(); cl.disconnect()
                except Exception:
                    pass
                cl = None
                continue
            if now - last_flush >= 3:
                last_flush = now
                try:
                    with app.app_context():   # the settings (and the launch-site match) need the application
                        col.flush(conn)
                except Exception as e:
                    conn.rollback()
                    STATE["error"] = f"{type(e).__name__}: {e}"[:200]
                while True:
                    try:
                        serial = col.backfill.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        with app.app_context():
                            backfill_one(conn, None, serial)
                    except Exception:
                        conn.rollback()
                    time.sleep(2)   # one request at a time, gently
            if rec_min > 0 and now - last_rec >= max(15, rec_min) * 60:
                last_rec = now
                try:
                    with app.app_context():
                        fetch_recoveries(conn)
                except Exception as e:
                    conn.rollback()
                    STATE["error"] = f"recovery reports: {type(e).__name__}: {e}"[:200]
            if now - last_pred >= 1800:
                last_pred = now
                try:
                    with app.app_context():
                        from . import sonde_predict as sp
                        if sp.enabled():
                            sp.push_config()
                            sp.push_recent(conn)
                except Exception:
                    conn.rollback()
            if now - last_sweep >= 60:
                last_sweep = now
                try:
                    with app.app_context():
                        sweep(conn)
                        if datetime.now(timezone.utc).strftime("%Y-%m-%d") != (get_setting("sonde_last_thin") or ""):
                            thin(conn)
                            set_setting("sonde_last_thin", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
                except Exception:
                    conn.rollback()
            time.sleep(0.5)
    finally:
        try:
            if cl is not None:
                cl.loop_stop(); cl.disconnect()
            with app.app_context():
                col.flush(conn)
        except Exception:
            pass
        conn.close()
        STATE["connected"] = False
        try:
            _save_stats(app)
        except Exception:
            pass


def start_feed(app):
    """A background loop in every web worker; the database lock lets only one of them hold the feed (the others take over within a minute if it stops)."""
    import psycopg

    def loop():
        time.sleep(40)
        while True:
            try:
                with app.app_context():
                    on = enabled() and cfg("sonde_live_enabled") == "1"
                if on:
                    with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock_conn:
                        if lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
                            try:
                                run_leader(app)
                            finally:
                                lock_conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            except Exception as e:
                STATE["error"] = f"{type(e).__name__}: {e}"[:200]
            time.sleep(30)
    threading.Thread(target=loop, name="sondes", daemon=True).start()


# ------------------------------------------------------------------ reading things back

def feed_stats():
    try:
        return json.loads(get_setting("sonde_feed_stats") or "{}")
    except ValueError:
        return {}


def feed_alive(maxage=180):
    st = feed_stats()
    at = parse_time(st.get("at"))
    return bool(st.get("connected")) and at is not None and (datetime.now(timezone.utc) - at).total_seconds() < maxage


def counts(states=None):
    """Flight and receiving-station numbers; with `states` (codes) only the flights launched in those states and the stations in them."""
    fw, sw, args = "", "", []
    if states:
        fw = " WHERE (state = ANY(%s) OR state IS NULL)"; sw = fw; args = [list(states)]
    r = get_db().execute("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE status='aloft') AS aloft, COUNT(*) FILTER (WHERE agency='NWS') AS nws, "
                         "COUNT(*) FILTER (WHERE last_seen > now() - interval '24 hours') AS day, COUNT(*) FILTER (WHERE kind='amateur') AS amateur FROM sondes" + fw, args).fetchone()
    s = get_db().execute("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE owner_id IS NOT NULL AND claim_status='claimed') AS claimed, COUNT(*) FILTER (WHERE claim_status='pending') AS pending, "
                         "COUNT(*) FILTER (WHERE last_seen > now() - interval '24 hours') AS active FROM sonde_stations" + sw, args).fetchone()
    return {"flights": dict(r), "stations": dict(s)}


FLIGHT_SORTS = {"last": "s.last_seen", "first": "s.first_seen", "max_alt": "s.max_alt", "serial": "s.serial", "type": "s.type", "site": "s.site_name"}


FALLING_SQL = "(COALESCE(s.last_vel_v, 0) < -1.0 OR COALESCE(s.max_alt - s.last_alt, 0) > 300)"


def flights(hours=None, agency="", status="", q="", kind="", typ="", site="", limit=50, offset=0, sort="last", direction="desc", station="", recovery="", states=None, since=None, until=None, serial=""):
    where, args = ["TRUE"], []
    if serial:
        where.append("lower(s.serial) = lower(%s)"); args.append(serial)
    if since is not None:   # history: flights that were in the air (or on the ground, still being heard) at any time between the two moments
        where.append("s.last_seen >= %s"); args.append(since)
    if until is not None:
        where.append("s.first_seen < %s"); args.append(until)
    if hours:
        where.append("s.last_seen > now() - make_interval(hours => %s)"); args.append(hours)
    if agency == "NWS":
        where.append("s.agency = 'NWS'")
    elif agency == "other":
        where.append("s.agency = ''")
    if status in ("aloft", "landed", "lost"):
        where.append("s.status = %s"); args.append(status)
    elif status == "climbing":
        where.append("s.status = 'aloft' AND NOT " + FALLING_SQL)
    elif status == "falling":
        where.append("s.status = 'aloft' AND " + FALLING_SQL)
    if recovery == "recovered":
        where.append("EXISTS (SELECT 1 FROM sonde_recoveries x WHERE x.serial = s.serial AND x.recovered)")
    elif recovery in ("notfound", "planned"):   # by the latest report: planned = recovered false and planned true, notfound = recovered false and not planned
        where.append("(SELECT NOT x.recovered AND x.planned = %s FROM sonde_recoveries x WHERE x.serial = s.serial ORDER BY x.ts DESC LIMIT 1)")
        args.append(recovery == "planned")
    elif recovery == "unreported":
        where.append("s.status IN ('landed', 'lost') AND NOT EXISTS (SELECT 1 FROM sonde_recoveries x WHERE x.serial = s.serial)")
    if states:
        where.append("(s.state = ANY(%s) OR s.state IS NULL)"); args.append(list(states))
    if kind in ("radiosonde", "amateur"):
        where.append("s.kind = %s"); args.append(kind)
    if typ:
        where.append("lower(s.type) = lower(%s)"); args.append(typ)
    if site:
        where.append("s.site_id = %s"); args.append(site)
    if q:
        where.append("(s.serial ILIKE %s OR s.site_name ILIKE %s OR s.type ILIKE %s)"); args += [q.replace("%", "") + "%", "%" + q.replace("%", "") + "%", q.replace("%", "") + "%"]
    if station:
        where.append("EXISTS (SELECT 1 FROM sonde_heard h WHERE h.serial = s.serial AND lower(h.callsign) = lower(%s))"); args.append(station)
    order = FLIGHT_SORTS.get(sort, "s.last_seen") + (" ASC" if direction == "asc" else " DESC") + " NULLS LAST, s.serial"
    w = " AND ".join(where)
    total = get_db().execute(f"SELECT COUNT(*) AS n FROM sondes s WHERE {w}", args).fetchone()["n"]
    rows = get_db().execute(f"SELECT s.*, r.recovered AS rec_recovered, r.planned AS rec_planned, r.recovered_by AS rec_by, r.description AS rec_note, r.ts AS rec_ts FROM sondes s "
                            "LEFT JOIN LATERAL (SELECT * FROM sonde_recoveries x WHERE x.serial = s.serial ORDER BY x.ts DESC LIMIT 1) r ON TRUE "
                            f"WHERE {w} ORDER BY {order} LIMIT %s OFFSET %s", args + [limit, offset]).fetchall()
    return total, rows


def get(serial):
    return get_db().execute("SELECT * FROM sondes WHERE lower(serial)=lower(%s)", (serial,)).fetchone()


def track(serial, max_points=1500):
    """The stored points of one flight in time order, thinned evenly to at most max_points for drawing."""
    rows = get_db().execute("SELECT ts, lat, lon, alt, vel_v, vel_h, heading, temp, hum, press, sats, batt, freq, snr, uploader FROM sonde_points WHERE serial=%s ORDER BY ts", (serial,)).fetchall()
    if len(rows) > max_points:
        stride = len(rows) / max_points
        rows = [rows[int(i * stride)] for i in range(max_points)] + [rows[-1]]
    return rows


def heard_by(serial):
    return get_db().execute("SELECT h.*, s.lat, s.lon, s.alt AS st_alt, s.privacy, s.mobile, s.owner_id, s.claim_status FROM sonde_heard h LEFT JOIN sonde_stations s ON lower(s.callsign) = lower(h.callsign) "
                            "WHERE h.serial=%s ORDER BY h.packets DESC", (serial,)).fetchall()


def summary(row, pts=None):
    """Numbers worked out from a flight's stored points: durations and rates."""
    out = {}
    first, last = row["first_seen"], row["last_seen"]
    out["duration_min"] = round((last - first).total_seconds() / 60) if first and last else None
    if row["max_alt"] is not None and row["max_alt_ts"] and row["launch_time"] and row["launch_alt"] is not None and row["max_alt_ts"] > row["launch_time"]:
        out["ascent_ms"] = round((row["max_alt"] - row["launch_alt"]) / (row["max_alt_ts"] - row["launch_time"]).total_seconds(), 1)
        out["to_burst_min"] = round((row["max_alt_ts"] - row["launch_time"]).total_seconds() / 60)
    if row["max_alt_ts"] and last and last > row["max_alt_ts"] and row["landing_alt"] is not None and row["max_alt"] is not None:
        out["descent_ms"] = round((row["max_alt"] - row["landing_alt"]) / max(1, (last - row["max_alt_ts"]).total_seconds()), 1)
    if row["launch_lat"] is not None and row["landing_lat"] is not None:
        out["drift_km"] = round(km(row["launch_lat"], row["launch_lon"], row["landing_lat"], row["landing_lon"]), 1)
    return out


def map_flights(hours=24, agency="", kind="", q="", since=None, until=None, serial="", site=""):
    """What the map draws: flights seen in the last hours, each with its latest position and a short thinned track."""
    total, rows = flights(hours=None if (since is not None or serial) else hours, agency=agency, kind=kind, q=q, limit=400, since=since, until=until, serial=serial, site=site)
    ids = [r["serial"] for r in rows]
    trails = {}
    if ids:
        # the WHOLE flight, thinned evenly to about `sonde_trail_points` points (never just its latest stretch, and not cut at the edge of the time window), at least `sonde_trail_step_seconds` apart
        for t in get_db().execute("SELECT serial, ts, lat, lon, alt FROM (SELECT serial, ts, lat, lon, alt, row_number() OVER (PARTITION BY serial ORDER BY ts) AS rn, count(*) OVER (PARTITION BY serial) AS n FROM "
                                  "(SELECT serial, ts, lat, lon, alt, row_number() OVER (PARTITION BY serial, floor(extract(epoch FROM ts) / %s) ORDER BY ts) AS b FROM sonde_points WHERE serial = ANY(%s)) a WHERE b = 1) x "
                                  "WHERE (rn - 1) %% GREATEST(1, CEIL(n::float / %s)::int) = 0 OR rn = n ORDER BY serial, ts",
                                  (max(1, cfg_int("sonde_trail_step_seconds")), ids, max(20, cfg_int("sonde_trail_points")))).fetchall():
            trails.setdefault(t["serial"], []).append([round(t["lat"], 4), round(t["lon"], 4), round(t["alt"]) if t["alt"] is not None else None])
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        if r["last_lat"] is None:
            continue
        out.append({"id": r["serial"], "kind": r["kind"], "type": r["type"], "agency": r["agency"], "site": r["site_name"], "site_id": r["site_id"], "status": r["status"], "phase": phase(r),
                    "label": phase_label(r), "launch": [r["launch_lat"], r["launch_lon"]] if r["launch_lat"] is not None else None, "burst": [r["max_lat"], r["max_lon"]] if r["max_lat"] is not None and phase(r) in ("falling", "landed", "lost") else None,
                    "recovered": r["rec_recovered"], "rec_state": (None if r["rec_recovered"] is None else "recovered" if r["rec_recovered"] else "planned" if r["rec_planned"] else "notfound"), "rec_by": r["rec_by"], "lat": r["last_lat"], "lon": r["last_lon"],
                    "alt": r["last_alt"], "vz": r["last_vel_v"], "temp": r["last_temp"], "max_alt": r["max_alt"], "age": int((now - r["last_seen"]).total_seconds()), "first": r["first_seen"].isoformat(),
                    "pred": [r["pred_lat"], r["pred_lon"]] if r["pred_lat"] is not None and r["status"] == "aloft" else None, "land": [r["landing_lat"], r["landing_lon"]] if r["landing_lat"] is not None else None,
                    "href": "/sondes/" + r["serial"], "freq": r["frequency"], "trail": trails.get(r["serial"], [])})
    return out


def rebuild_reach_sectors():
    """Works out, from the flights each station really heard, how far it heard a balloon in every ten degree direction: the positions of a heard flight between the station's first and last packet of
    that flight, measured from the station. Merged with what live reception has already kept (the larger wins). Returns the number of stations that now have a coverage outline."""
    d = get_db()
    rows = d.execute("""WITH h AS (SELECT st.id, st.lat AS la, st.lon AS lo, p.lat AS pla, p.lon AS plo FROM sonde_heard sh JOIN sonde_stations st ON lower(st.callsign) = lower(sh.callsign) AND st.lat IS NOT NULL AND st.lon IS NOT NULL
                          JOIN sonde_points p ON p.serial = sh.serial AND p.ts BETWEEN COALESCE(sh.first_ts, '-infinity') AND COALESCE(sh.last_ts, 'infinity') AND p.lat IS NOT NULL),
                       m AS (SELECT id, 2 * 6371.0088 * asin(least(1, sqrt(power(sin(radians(pla - la) / 2), 2) + cos(radians(la)) * cos(radians(pla)) * power(sin(radians(plo - lo) / 2), 2)))) AS km,
                                    (degrees(atan2(sin(radians(plo - lo)) * cos(radians(pla)), cos(radians(la)) * sin(radians(pla)) - sin(radians(la)) * cos(radians(pla)) * cos(radians(plo - lo)))) + 360)::numeric % 360 AS brg FROM h)
                       SELECT id, floor(brg / 10)::int % 36 AS sec, max(km) AS km FROM m WHERE km > 1 GROUP BY 1, 2""").fetchall()
    per = {}
    for r in rows:
        per.setdefault(r["id"], [0.0] * 36)[r["sec"]] = round(float(r["km"]), 1)
    n = 0
    for sid, new in per.items():
        row = d.execute("SELECT reach_sectors FROM sonde_stations WHERE id=%s", (sid,)).fetchone()
        try:
            old = json.loads(row["reach_sectors"]) if row and row["reach_sectors"] else [0.0] * 36
        except ValueError:
            old = [0.0] * 36
        merged = [round(max(a, b), 1) for a, b in zip(old + [0.0] * 36, new)]
        d.execute("UPDATE sonde_stations SET reach_sectors=%s, reach_km=GREATEST(COALESCE(reach_km, 0), %s) WHERE id=%s", (json.dumps(merged), max(merged), sid))
        n += 1
    d.commit()
    return n


def _sectors(raw):
    """The farthest balloon heard in each of 36 ten degree sectors (kilometers) for a station at its exact position; None when nothing is kept yet."""
    try:
        v = json.loads(raw) if raw else None
    except ValueError:
        return None
    return v if isinstance(v, list) and len(v) == 36 and any(v) else None


def map_stations():
    out = []
    for s in get_db().execute("SELECT s.*, u.username AS owner_name FROM sonde_stations s LEFT JOIN users u ON u.id = s.owner_id WHERE s.public AND NOT s.disabled AND s.lat IS NOT NULL ORDER BY s.callsign").fetchall():
        loc = public_position(s)
        if loc:
            from . import privacy as _pv
            out.append({"id": s["callsign"], "claimed": s["claim_status"] == "claimed" and s["owner_id"] is not None, "lat": loc[0], "lon": loc[1], "approx": s["privacy"] != "exact", "radius_m": _pv.radius_m(s["privacy"] or "general"), "reach_km": round(s["reach_km"]) if s["reach_km"] else None, "sectors": _sectors(s["reach_sectors"]),   # drawn around the position shown, like the ADS-B coverage: how far a balloon was heard in each direction, whatever the privacy choice (the station itself is shown as precisely as its owner chose) "software": s["software"], "antenna": s["antenna"], "claimed": s["claim_status"] == "claimed",
                        "last": s["last_seen"].isoformat() if s["last_seen"] else None, "href": "/sondes/stations/" + s["callsign"].replace("/", "~")})
    return out


def map_sites(nws_only=False):
    bx = boxes()
    return [{"id": s["site_id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"], "nws": s["nws"], "types": s["rs_types"], "href": "/sondes/sites/" + s["site_id"]}
            for s in sites() if in_region(s["lat"], s["lon"], bx) and (s["nws"] or not nws_only)]


def search(q, limit=8):
    q = (q or "").strip()[:60]
    if len(q) < 2:
        return []
    like = q.replace("%", "").replace("_", "")
    out = []
    d = get_db()
    for r in d.execute("SELECT serial, type, agency, site_name, last_lat, last_lon, status FROM sondes WHERE serial ILIKE %s ORDER BY last_seen DESC LIMIT %s", (like + "%", limit)).fetchall():
        out.append({"kind": "flight", "label": f"{r['serial']} {r['type']}" + (f" ({r['agency']})" if r["agency"] else "") + (f" from {r['site_name']}" if r["site_name"] else ""), "href": "/sondes/" + r["serial"], "lat": r["last_lat"], "lon": r["last_lon"]})
    for r in d.execute("SELECT callsign, lat, lon, privacy, mobile FROM sonde_stations WHERE callsign ILIKE %s AND public AND NOT disabled ORDER BY last_seen DESC NULLS LAST LIMIT %s", (like + "%", limit)).fetchall():
        loc = public_position(r)
        out.append({"kind": "station", "label": f"Receiving station {r['callsign']}", "href": "/sondes/stations/" + r["callsign"].replace("/", "~"), "lat": loc[0] if loc else None, "lon": loc[1] if loc else None})
    for s in sites():
        if like.lower() in s["name"].lower() or like == s["site_id"]:
            out.append({"kind": "site", "label": f"Launch site {s['name']} ({s['site_id']})", "href": "/sondes/sites/" + s["site_id"], "lat": s["lat"], "lon": s["lon"]})
            if len(out) > limit * 3:
                break
    return out[: limit * 2]


def home_card():
    """Balloons in the air over Arkansas right now (the home page shows only Arkansas): count, highest, and the NWS flights."""
    from . import arkansas
    rows = [r for r in get_db().execute("SELECT serial, type, agency, last_lat, last_lon, last_alt, site_name FROM sondes WHERE status='aloft' AND last_seen > now() - interval '30 minutes'").fetchall()
            if arkansas.inside(r["last_lat"], r["last_lon"])]
    day = get_db().execute("SELECT COUNT(*) AS n FROM sondes WHERE last_seen > now() - interval '24 hours' AND agency='NWS'").fetchone()["n"]
    return {"aloft": len(rows), "highest": max((r["last_alt"] or 0 for r in rows), default=None), "nws": sum(1 for r in rows if r["agency"] == "NWS"), "nws_24h": day, "rows": rows[:5]}
