"""Weather stations: members' own stations (and, later, public ones) as a capability of the one station record.

A weather station either belongs to a feeder (it then shares the feeder's station ID, location and privacy) or stands alone (weather only; it has its
own ID, location and privacy). Readings arrive three ways, all from the owner's own device or account:
  * push: the station (an Ecowitt gateway "custom server", or anything speaking the Weather Underground upload format) posts to a private address;
  * relay: Home Assistant or any script posts a JSON object to a private address (the way for Tempest and other cloud-only hardware);
  * pull: the portal reads the owner's own Ecowitt.net account with the owner's keys (and, switched off by default, a Tempest account).
Everything is stored in imperial units as the pushes send it and converted for display. Raw readings are kept a few days, then rolled up to hourly rows.
Terms of every source are in DATA_SOURCES_TERMS.md ("Weather stations"); nothing here pulls data that belongs to somebody else."""
import hashlib
import json
import math
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import vault
from .db import get_db
from .settings import cfg, cfg_int

LOCK_ID = 727020

# the fields every reading is reduced to: key -> (label, sanity range)
FIELDS = {
    "temp_f": ("Temperature", (-90, 150)), "dew_f": ("Dew Point", (-100, 120)), "hum": ("Humidity", (0, 100)), "press_in": ("Pressure", (20, 32.5)),
    "wind_mph": ("Wind", (0, 250)), "gust_mph": ("Gust", (0, 300)), "wind_dir": ("Wind Direction", (0, 360)), "rain_rate_in": ("Rain Rate", (0, 40)),
    "rain_day_in": ("Rain Today", (0, 100)), "rain_week_in": ("Rain This Week", (0, 300)), "rain_month_in": ("Rain This Month", (0, 600)),
    "rain_year_in": ("Rain This Year", (0, 2000)), "solar": ("Solar Radiation", (0, 1800)), "uv": ("UV Index", (0, 20)),
    "lightning_n": ("Lightning Strikes Today", (0, 100000)), "lightning_mi": ("Lightning Distance", (0, 250)),
}
KINDS = {
    "ecowitt_push": "Ecowitt gateway or console (custom server)",
    "wu_push": "Weather Underground upload format (Ambient, Davis, many others)",
    "relay": "Home Assistant, a Tempest bridge or a script (JSON)",
    "ecowitt_cloud": "Ecowitt.net account (the owner's own keys)",
    "tempest_cloud": "Tempest account (the owner's own token)",
}
RESERVED_IDS = ("MAP", "STATISTICS", "STATS", "STATS-AREAS", "STATS-STATIONS")   # /weather/map is the full weather map
PUSH_KINDS = ("ecowitt_push", "wu_push", "relay")
CLOUD_KINDS = ("ecowitt_cloud", "tempest_cloud")
from .privacy import PRIVACY   # the one list of location choices: Exact, Near, Close, General, Wide, Hidden

SELECT = ("SELECT w.*, COALESCE(f.station_id, w.station_id) AS sid, COALESCE(f.lat, w.lat) AS plat, COALESCE(f.lon, w.lon) AS plon, "
          "COALESCE(f.privacy, w.privacy, 'general') AS pprivacy, f.uuid AS fuuid, f.banned AS fbanned, f.name AS fname, "
          "u.username AS owner_name, u.display_name AS owner_display, u.callsign AS owner_callsign, COALESCE((SELECT p.enabled FROM wx_push p WHERE p.station_pk = w.id), FALSE) AS aprs_on "
          "FROM wx_stations w LEFT JOIN feeders f ON f.uuid = w.feeder_uuid LEFT JOIN users u ON u.id = w.owner_id ")
PUBLIC_WHERE = "w.public AND NOT w.disabled AND COALESCE(f.station_id, w.station_id) IS NOT NULL AND COALESCE(f.banned, FALSE) = FALSE"


def enabled():
    return cfg("wx_enabled") == "1"


# ------------------------------------------------------------------ units

def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _unit(u):
    return re.sub(r"[\s°º]", "", str(u or "")).lower().replace("℉", "f").replace("℃", "c").replace("²", "2").replace("/hr", "/h").replace("per", "/")


def to_f(v, u="f"):
    v, u = _num(v), _unit(u)
    if v is None:
        return None
    if u in ("k", "kelvin"):
        v, u = v - 273.15, "c"
    return v * 9 / 5 + 32 if u in ("c", "degc", "celsius") else v


def to_mph(v, u="mph"):
    v, u = _num(v), _unit(u)
    if v is None:
        return None
    k = {"km/h": 0.621371, "kph": 0.621371, "kmh": 0.621371, "m/s": 2.236936, "ms": 2.236936, "kn": 1.150779, "kt": 1.150779, "knots": 1.150779, "knot": 1.150779, "kts": 1.150779, "ft/s": 0.681818, "fps": 0.681818}
    return v * k.get(u, 1.0)


def to_inhg(v, u="inhg"):
    v, u = _num(v), _unit(u)
    if v is None:
        return None
    k = {"hpa": 0.0295300, "mb": 0.0295300, "mbar": 0.0295300, "kpa": 0.2953, "mmhg": 0.0393701, "pa": 0.000295300}
    return v * k.get(u, 1.0)


def to_in(v, u="in"):
    v, u = _num(v), _unit(u)
    if v is None:
        return None
    u = u.replace("/h", "")
    return v / 25.4 if u in ("mm", "millimeters", "millimetres") else v


def to_mi(v, u="mi"):
    v, u = _num(v), _unit(u)
    if v is None:
        return None
    return v * 0.621371 if u in ("km", "kilometers", "kilometres") else (v / 1609.344 if u in ("m", "meters") else (v * 0.000189394 if u == "ft" else v))


def dew_point_f(t_f, hum):
    """Magnus formula, from temperature (F) and relative humidity (%)."""
    if t_f is None or not hum or hum <= 0:
        return None
    c = (t_f - 32) * 5 / 9
    g = math.log(hum / 100) + 17.62 * c / (243.12 + c)
    return round(243.12 * g / (17.62 - g) * 9 / 5 + 32, 1)


def feels_like_f(t, hum, wind):
    """Heat index (NWS Rothfusz) from 80 F up, wind chill (NWS) at or below 50 F with wind; otherwise the air temperature."""
    if t is None:
        return None
    if t >= 80 and hum is not None and hum >= 40:
        hi = (-42.379 + 2.04901523 * t + 10.14333127 * hum - 0.22475541 * t * hum - 0.00683783 * t * t - 0.05481717 * hum * hum
              + 0.00122874 * t * t * hum + 0.00085282 * t * hum * hum - 0.00000199 * t * t * hum * hum)
        return round(hi, 1)
    if t <= 50 and wind is not None and wind >= 3:
        return round(35.74 + 0.6215 * t - 35.75 * wind ** 0.16 + 0.4275 * t * wind ** 0.16, 1)
    return round(t, 1)


def clean(raw):
    """Keep only the fields that are present and plausible (a broken sensor must not draw a 900 degree day); fill in dew point and feels-like when absent.
    Returns the reading, or None when nothing usable arrived."""
    r = {}
    for k, v in raw.items():
        if k in FIELDS and v is not None:
            lo, hi = FIELDS[k][1]
            if lo <= v <= hi:
                r[k] = round(v, 3)
    if not any(k in r for k in ("temp_f", "hum", "press_in", "wind_mph", "rain_day_in", "solar", "uv", "gust_mph")):
        return None
    if "dew_f" not in r and r.get("temp_f") is not None and r.get("hum"):
        d = dew_point_f(r["temp_f"], r["hum"])
        if d is not None and FIELDS["dew_f"][1][0] <= d <= FIELDS["dew_f"][1][1]:
            r["dew_f"] = d
    fl = feels_like_f(r.get("temp_f"), r.get("hum"), r.get("wind_mph"))
    if fl is not None:
        r["feels_f"] = fl
    return r


# ------------------------------------------------------------------ parsers (one per way a reading arrives)

def parse_pws(d):
    """The form an Ecowitt gateway posts and the query a Weather Underground style station sends: both name things alike (tempf, humidity, baromrelin or baromin...)."""
    q = {str(k).lower(): v for k, v in d.items()}
    g = lambda *names: next((q[n] for n in names if q.get(n) not in (None, "")), None)
    r = {
        "temp_f": _num(g("tempf")), "hum": _num(g("humidity")), "dew_f": _num(g("dewptf")), "press_in": _num(g("baromrelin", "baromin", "baromabsin")),
        "wind_mph": _num(g("windspeedmph")), "gust_mph": _num(g("windgustmph")), "wind_dir": _num(g("winddir")),
        "rain_rate_in": _num(g("rainratein", "rainin")), "rain_day_in": _num(g("dailyrainin")), "rain_week_in": _num(g("weeklyrainin")),
        "rain_month_in": _num(g("monthlyrainin")), "rain_year_in": _num(g("yearlyrainin")), "solar": _num(g("solarradiation")), "uv": _num(g("uv")),
        "lightning_n": _num(g("lightning_num")),
    }
    km = _num(g("lightning"))
    r["lightning_mi"] = None if km is None else km * 0.621371
    out = clean(r)
    if out is not None:
        extra = {k: str(q[k])[:40] for k in ("model", "stationtype", "softwaretype", "freq") if q.get(k)}
        if extra:
            out["_extra"] = extra
    return out


# friendly names a relay may use: key -> (field, converter, default unit)
_RELAY = [("temp_f", "temp_f", to_f, "f"), ("temp_c", "temp_f", to_f, "c"), ("dew_f", "dew_f", to_f, "f"), ("dew_c", "dew_f", to_f, "c"),
          ("humidity", "hum", _num, ""), ("pressure_inhg", "press_in", to_inhg, "inhg"), ("pressure_hpa", "press_in", to_inhg, "hpa"), ("pressure_mb", "press_in", to_inhg, "hpa"),
          ("wind_mph", "wind_mph", to_mph, "mph"), ("wind_kph", "wind_mph", to_mph, "kph"), ("wind_ms", "wind_mph", to_mph, "m/s"),
          ("gust_mph", "gust_mph", to_mph, "mph"), ("gust_kph", "gust_mph", to_mph, "kph"), ("gust_ms", "gust_mph", to_mph, "m/s"),
          ("wind_dir", "wind_dir", _num, ""), ("rain_rate_in", "rain_rate_in", to_in, "in"), ("rain_rate_mm", "rain_rate_in", to_in, "mm"),
          ("rain_day_in", "rain_day_in", to_in, "in"), ("rain_day_mm", "rain_day_in", to_in, "mm"), ("rain_week_in", "rain_week_in", to_in, "in"), ("rain_week_mm", "rain_week_in", to_in, "mm"),
          ("rain_month_in", "rain_month_in", to_in, "in"), ("rain_month_mm", "rain_month_in", to_in, "mm"), ("rain_year_in", "rain_year_in", to_in, "in"), ("rain_year_mm", "rain_year_in", to_in, "mm"),
          ("solar", "solar", _num, ""), ("uv", "uv", _num, ""), ("lightning_count", "lightning_n", _num, ""),
          ("lightning_mi", "lightning_mi", to_mi, "mi"), ("lightning_km", "lightning_mi", to_mi, "km")]


# the "value with its own unit" form the Home Assistant integration and the Tempest bridge send: {"temperature": {"value": 21, "unit": "°C"}}
_GENERIC = {"temperature": ("temp_f", to_f), "dew_point": ("dew_f", to_f), "humidity": ("hum", lambda v, u: _num(v)), "pressure": ("press_in", to_inhg),
            "wind_speed": ("wind_mph", to_mph), "wind_gust": ("gust_mph", to_mph), "wind_direction": ("wind_dir", lambda v, u: _num(v)), "rain_rate": ("rain_rate_in", to_in),
            "rain_today": ("rain_day_in", to_in), "rain_week": ("rain_week_in", to_in), "rain_month": ("rain_month_in", to_in), "rain_year": ("rain_year_in", to_in),
            "solar_radiation": ("solar", lambda v, u: _num(v)), "uv_index": ("uv", lambda v, u: _num(v)), "lightning_count": ("lightning_n", lambda v, u: _num(v)),
            "lightning_distance": ("lightning_mi", to_mi)}


def parse_relay(d):
    """A JSON object from Home Assistant, the Tempest bridge or a script: values that carry their own unit ({"temperature": {"value": 21, "unit": "°C"}}), friendly names with
    the unit in the name (temp_c, wind_kph, pressure_hpa, rain_day_mm...), or the Ecowitt names."""
    if not isinstance(d, dict):
        return None
    r = {}
    for key, (field, conv) in _GENERIC.items():
        node = d.get(key)
        if isinstance(node, dict) and "value" in node:
            val = conv(node.get("value"), node.get("unit") or "")
            if val is not None:
                r[field] = val
    for key, field, conv, unit in _RELAY:
        if d.get(key) is not None and field not in r:
            r[field] = conv(d[key], unit) if unit else conv(d[key])
    out = dict(parse_pws({k: v for k, v in d.items() if not isinstance(v, (dict, list))}) or {})   # Ecowitt-style names work too; the friendly names win
    out.update(clean(r) or {})
    return out or None


def _cloud_value(node, conv):
    """One value of an Ecowitt.net answer: {"time": ..., "unit": "℉", "value": "68.5"}; the unit text says what it is, so the portal never depends on unit numbers."""
    if not isinstance(node, dict):
        return None
    return conv(node.get("value"), node.get("unit") or "")


def parse_ecowitt_cloud(js):
    """api.ecowitt.net/api/v3/device/real_time (call_back=all) -> reading."""
    if not isinstance(js, dict) or js.get("code") not in (0, "0") or not isinstance(js.get("data"), dict):
        return None
    data = js["data"]
    out, wind, rain, pr = data.get("outdoor") or {}, data.get("wind") or {}, data.get("rainfall") or {}, data.get("pressure") or {}
    sol, lt = data.get("solar_and_uvi") or {}, data.get("lightning") or {}
    r = {
        "temp_f": _cloud_value(out.get("temperature"), to_f), "dew_f": _cloud_value(out.get("dew_point"), to_f), "hum": _cloud_value(out.get("humidity"), lambda v, u: _num(v)),
        "press_in": _cloud_value(pr.get("relative"), to_inhg), "wind_mph": _cloud_value(wind.get("wind_speed"), to_mph), "gust_mph": _cloud_value(wind.get("wind_gust"), to_mph),
        "wind_dir": _cloud_value(wind.get("wind_direction"), lambda v, u: _num(v)),
        "rain_rate_in": _cloud_value(rain.get("rain_rate"), to_in), "rain_day_in": _cloud_value(rain.get("daily"), to_in), "rain_week_in": _cloud_value(rain.get("weekly"), to_in),
        "rain_month_in": _cloud_value(rain.get("monthly"), to_in), "rain_year_in": _cloud_value(rain.get("yearly"), to_in),
        "solar": _cloud_value(sol.get("solar"), lambda v, u: _num(v)), "uv": _cloud_value(sol.get("uvi"), lambda v, u: _num(v)),
        "lightning_n": _cloud_value(lt.get("count"), lambda v, u: _num(v)), "lightning_mi": _cloud_value(lt.get("distance"), to_mi),
    }
    return clean(r)


def parse_tempest(js):
    """swd.weatherflow.com .../observations/station/<id>: the newest observation. Its values are SI (C, m/s, mb, mm, km), as the API documents."""
    if not isinstance(js, dict) or not js.get("obs"):
        return None
    o = js["obs"][-1]
    if not isinstance(o, dict):
        return None
    r = {
        "temp_f": to_f(o.get("air_temperature"), "c"), "dew_f": to_f(o.get("dew_point"), "c"), "hum": _num(o.get("relative_humidity")),
        "press_in": to_inhg(o.get("sea_level_pressure", o.get("barometric_pressure")), "mb"), "wind_mph": to_mph(o.get("wind_avg"), "m/s"), "gust_mph": to_mph(o.get("wind_gust"), "m/s"),
        "wind_dir": _num(o.get("wind_direction")), "rain_rate_in": None if o.get("precip") is None else to_in(_num(o["precip"]) * 60, "mm"),
        "rain_day_in": to_in(o.get("precip_accum_local_day"), "mm"), "solar": _num(o.get("solar_radiation")), "uv": _num(o.get("uv")),
        "lightning_n": _num(o.get("lightning_strike_count_last_3hr")), "lightning_mi": to_mi(o.get("lightning_strike_last_distance"), "km"),
    }
    return clean(r)


# ------------------------------------------------------------------ stations

def new_token():
    return secrets.token_urlsafe(24)


def token_hash(token):
    return hashlib.sha256(("wx\0" + token).encode()).hexdigest()


def by_token(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token or ""):
        return None
    return get_db().execute(SELECT + "WHERE w.token_hash=%s", (token_hash(token),)).fetchone()


def get(sid):
    return get_db().execute(SELECT + "WHERE lower(COALESCE(f.station_id, w.station_id)) = lower(%s)", (sid,)).fetchone()


def get_public(sid):
    return get_db().execute(SELECT + f"WHERE {PUBLIC_WHERE} AND lower(COALESCE(f.station_id, w.station_id)) = lower(%s)", (sid,)).fetchone()


def get_owned(wid, user):
    r = get_db().execute(SELECT + "WHERE w.id=%s", (wid,)).fetchone()
    return r if r and r["owner_id"] == user.id else None


def mine(user_id):
    return get_db().execute(SELECT + "WHERE w.owner_id=%s ORDER BY w.created", (user_id,)).fetchall()


def all_stations():
    return get_db().execute(SELECT + "ORDER BY w.created").fetchall()


def station_taken(sid, exclude_id=None):
    from . import stations as st
    d = get_db()
    if d.execute("SELECT 1 FROM wx_stations WHERE lower(station_id)=lower(%s) AND id <> COALESCE(%s, 0)", (sid, exclude_id)).fetchone():
        return True
    return st._taken(sid)


def is_online(row, now=None):
    if not row["last_report"]:
        return False
    return (now or datetime.now(timezone.utc)) - row["last_report"] < timedelta(minutes=max(1, cfg_int("wx_stale_minutes")))


def public_loc(row):
    """The only place a weather station's coordinates are prepared for public use: the same rule as feeders (hidden gives nothing, general rounds)."""
    from . import feeders as fd
    return fd.public_location({"lat": row["plat"], "lon": row["plon"], "privacy": row["pprivacy"]})


def create(user, kind, name, feeder_uuid=None, sid=None, lat=None, lon=None, privacy="general", hardware="", description="", public=False, secrets_json=None, by_admin=False):
    """Add a weather station. Returns (id, error). Validation of the form lives in the view; this checks the rules that protect other people's data."""
    from . import stations as st
    d = get_db()
    if kind not in KINDS:
        return None, "Choose how the station sends its readings."
    if kind in CLOUD_KINDS and cfg(f"wx_{kind.split('_')[0]}_pull_enabled") != "1":
        return None, "Reading that kind of account is switched off on this site."
    if not by_admin and d.execute("SELECT COUNT(*) AS n FROM wx_stations WHERE owner_id=%s", (user.id,)).fetchone()["n"] >= cfg_int("wx_max_per_user"):
        return None, f"You can have {cfg_int('wx_max_per_user')} weather stations."
    if feeder_uuid:
        f = st.get_owned(feeder_uuid, user)
        if f is None:
            return None, "Choose one of your own feeders."
        if d.execute("SELECT 1 FROM wx_stations WHERE feeder_uuid=%s", (feeder_uuid,)).fetchone():
            return None, "That feeder already has a weather station."
        sid = None
    else:
        sid = (sid or "").strip().upper()
        if not st.STATION_ID_RE.match(sid):
            return None, "A station ID is 3 to 16 characters: letters, numbers and dashes."
        if sid in RESERVED_IDS:
            return None, f"{sid} is used by the site itself. Choose another station ID."
        if station_taken(sid):
            return None, f"{sid} is already in use."
    token = new_token()
    row = d.execute(
        "INSERT INTO wx_stations (station_id, owner_id, feeder_uuid, name, lat, lon, privacy, kind, token_hash, token_sealed, secrets, public, hardware, description) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (sid, user.id, feeder_uuid, (name or "").strip()[:60], lat, lon, privacy if privacy in PRIVACY else "general", kind, token_hash(token), vault.seal(token),
         vault.seal(json.dumps(secrets_json)) if secrets_json else "", bool(public), (hardware or "").strip()[:200], (description or "").strip()[:500])).fetchone()
    d.commit()
    return row["id"], None


def push_token(row):
    return vault.unseal(row["token_sealed"])


def cloud_secrets(row):
    try:
        return json.loads(vault.unseal(row["secrets"]) or "{}")
    except ValueError:
        return {}


# ------------------------------------------------------------------ storing readings

_COLS = ["temp_f", "dew_f", "hum", "press_in", "wind_mph", "gust_mph", "wind_dir", "rain_rate_in", "rain_day_in", "solar", "uv", "lightning_n", "lightning_mi"]


def store(conn, wid, reading, now=None):
    """Keep a reading: the station's latest values always, a raw row at most once per `wx_store_seconds`. Returns True when a raw row was written."""
    now = now or datetime.now(timezone.utc)
    reading = dict(reading)
    extra = reading.pop("_extra", None)
    latest = dict(reading, ts=now.isoformat())
    row = conn.execute("SELECT last_stored, latest FROM wx_stations WHERE id=%s", (wid,)).fetchone()
    if row is None:
        return False
    try:   # fields a device sent earlier but not this time (some send rain only now and then) keep their last value
        old = json.loads(row["latest"]) if row["latest"] else {}
    except ValueError:
        old = {}
    if now - (row["last_stored"] or datetime(1970, 1, 1, tzinfo=timezone.utc)) >= timedelta(seconds=max(1, cfg_int("wx_store_seconds"))):
        conn.execute(f"INSERT INTO wx_readings (station_pk, ts, {', '.join(_COLS)}) VALUES (%s, %s, {', '.join(['%s'] * len(_COLS))}) ON CONFLICT DO NOTHING",
                     [wid, now] + [reading.get(c) for c in _COLS])
        stored = True
    else:
        stored = False
    for k, v in old.items():
        if k not in latest and k != "ts" and k in FIELDS and old.get("ts") and now - datetime.fromisoformat(old["ts"]) < timedelta(hours=6):
            latest[k] = v
    if extra:
        latest["extra"] = extra
    elif old.get("extra"):
        latest["extra"] = old["extra"]
    conn.execute("UPDATE wx_stations SET latest=%s, last_report=%s, last_error='', fail_count=0" + (", last_stored=%s" if stored else "") + " WHERE id=%s",
                 [json.dumps(latest), now] + ([now] if stored else []) + [wid])
    conn.commit()
    return stored


_bad_ips = {}


def too_many_bad_tokens(ip, note=False):
    """A small in-memory brake on guessing tokens: so many unknown tokens a minute from one address, then it waits."""
    now = time.time()
    hits = [t for t in _bad_ips.get(ip, []) if now - t < 60]
    if note:
        hits.append(now)
    _bad_ips[ip] = hits
    if len(_bad_ips) > 5000:
        _bad_ips.clear()
    return len(hits) >= max(1, cfg_int("wx_bad_token_per_minute"))


# ------------------------------------------------------------------ pulling from an owner's own account

def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers=dict({"User-Agent": cfg("lookup_user_agent")}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=max(2, cfg_int("wx_pull_timeout_seconds"))) as r:
        return json.loads(r.read(2_000_000))


def pull(row, conn=None):
    """Read one cloud station now. Returns (ok, message); the station's own error text and failure count are kept."""
    conn = conn or get_db()
    sec = cloud_secrets(row)
    try:
        if row["kind"] == "ecowitt_cloud":
            if cfg("wx_ecowitt_pull_enabled") != "1":
                return False, "Reading Ecowitt.net accounts is switched off on this site."
            q = urllib.parse.urlencode({"application_key": sec.get("application_key", ""), "api_key": sec.get("api_key", ""), "mac": sec.get("mac", ""), "call_back": "all"})
            js = _get_json(cfg("wx_ecowitt_api_url") + "?" + q)
            if js.get("code") not in (0, "0"):
                raise ValueError(f"Ecowitt said: {str(js.get('msg') or js.get('code'))[:120]}")
            reading = parse_ecowitt_cloud(js)
        elif row["kind"] == "tempest_cloud":
            if cfg("wx_tempest_pull_enabled") != "1":
                return False, "Reading Tempest accounts is switched off on this site."
            sid = re.sub(r"\D", "", str(sec.get("station_id", "")))
            js = _get_json(cfg("wx_tempest_api_url") + sid, {"Authorization": "Bearer " + sec.get("token", "")})
            reading = parse_tempest(js)
        else:
            return False, "This station sends its own readings."
        if reading is None:
            raise ValueError("The answer held no usable readings.")
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:300]
        conn.rollback()
        conn.execute("UPDATE wx_stations SET last_error=%s, fail_count=fail_count+1, last_poll=now() WHERE id=%s", (msg, row["id"]))
        conn.commit()
        return False, msg
    conn.execute("UPDATE wx_stations SET last_poll=now() WHERE id=%s", (row["id"],))
    store(conn, row["id"], reading)
    return True, "Reading received."


def poll_due(conn):
    """One pass: pull every cloud station whose turn has come (a failing one waits longer each time, up to 12 times the usual wait)."""
    base = max(5, cfg_int("wx_poll_minutes")) * 60
    n = 0
    for row in conn.execute(SELECT + "WHERE w.kind = ANY(%s) AND NOT w.disabled", (list(CLOUD_KINDS),)).fetchall():
        wait = base * min(2 ** (row["fail_count"] or 0), 12)
        if row["last_poll"] and (datetime.now(timezone.utc) - row["last_poll"]).total_seconds() < wait:
            continue
        pull(row, conn)
        n += 1
    return n


def rollup(conn):
    """Raw readings older than `wx_raw_days` become hourly rows (average, low, high) and are deleted; hourly rows older than `wx_hourly_days` go too."""
    raw_days, hourly_days = cfg_int("wx_raw_days"), cfg_int("wx_hourly_days")
    cut = datetime.now(timezone.utc) - timedelta(days=max(1, raw_days))
    cut = cut.replace(minute=0, second=0, microsecond=0)
    conn.execute(
        "INSERT INTO wx_hourly (station_pk, hour, n, temp_avg, temp_min, temp_max, hum_avg, press_avg, wind_avg, gust_max, rain_day_max, solar_max, uv_max, dew_avg) "
        "SELECT station_pk, date_trunc('hour', ts), COUNT(*), AVG(temp_f), MIN(temp_f), MAX(temp_f), AVG(hum), AVG(press_in), AVG(wind_mph), MAX(gust_mph), MAX(rain_day_in), MAX(solar), MAX(uv), AVG(dew_f) "
        "FROM wx_readings WHERE ts < %s GROUP BY station_pk, date_trunc('hour', ts) "
        "ON CONFLICT (station_pk, hour) DO UPDATE SET n=EXCLUDED.n, temp_avg=EXCLUDED.temp_avg, temp_min=EXCLUDED.temp_min, temp_max=EXCLUDED.temp_max, hum_avg=EXCLUDED.hum_avg, "
        "press_avg=EXCLUDED.press_avg, wind_avg=EXCLUDED.wind_avg, gust_max=EXCLUDED.gust_max, rain_day_max=EXCLUDED.rain_day_max, solar_max=EXCLUDED.solar_max, uv_max=EXCLUDED.uv_max, dew_avg=EXCLUDED.dew_avg", (cut,))
    conn.execute("DELETE FROM wx_readings WHERE ts < %s", (cut,))
    if hourly_days > 0:
        conn.execute("DELETE FROM wx_hourly WHERE hour < now() - make_interval(days => %s)", (hourly_days,))
    conn.commit()


def start_poller(app):
    """A background loop in every web worker; a database lock means only one does the work at a time."""
    import psycopg
    from psycopg.rows import dict_row

    def loop():
        time.sleep(45)
        n = 0
        while True:
            try:
                with app.app_context():
                    on = enabled()
                if on:
                    with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock_conn:
                        if lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
                            try:
                                with app.app_context():
                                    with psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row) as conn:
                                        poll_due(conn)
                                        if n % 120 == 0:
                                            rollup(conn)
                            finally:
                                lock_conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            except Exception:
                pass
            n += 1
            time.sleep(30)
    threading.Thread(target=loop, name="weather", daemon=True).start()


# ------------------------------------------------------------------ reading things back

def sun_elevation(lat, lon, now=None):
    """Height of the sun above the horizon in degrees (NOAA's simple formulas, good to about a degree): whether it is day at a station and how bright a clear sky would be."""
    now = now or datetime.now(timezone.utc)
    doy = now.timetuple().tm_yday
    g = 2 * math.pi / 365 * (doy - 1 + (now.hour - 12) / 24)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = 0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g)
    minutes = now.hour * 60 + now.minute + now.second / 60
    ha = math.radians((minutes + eqt + 4 * lon) / 4 - 180)
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)))


def condition(la, lat=None, lon=None, now=None):
    """(icon name, words) for what the sky and weather are doing, worked out from the station's own readings: rain and wind from the gauge, fog from humidity against dew point,
    sun or cloud from how much sunlight arrives compared with a clear sky at that hour. None when the station gives too little to say."""
    if not la:
        return None
    rate, gust, wind, hum = la.get("rain_rate_in"), la.get("gust_mph"), la.get("wind_mph"), la.get("hum")
    t, dew, solar = la.get("temp_f"), la.get("dew_f"), la.get("solar")
    if rate is not None and rate > 0.005:
        return ("storm", "Thunderstorm") if (la.get("lightning_n") or 0) > 0 else ("rain", "Rain")
    if hum is not None and hum >= 95 and t is not None and dew is not None and t - dew <= 2.5:
        return ("fog", "Fog or mist")
    if (gust or 0) >= 30 or (wind or 0) >= 20:
        return ("windy", "Windy")
    elev = sun_elevation(lat, lon, now) if lat is not None and lon is not None else None
    if elev is not None and elev < -3:
        return ("moon", "Clear night" if (hum is None or hum < 90) else "Night")
    if solar is not None and elev is not None and elev > 4:
        ratio = solar / max(60.0, 990 * math.sin(math.radians(elev)))
        return ("sunny", "Sunny") if ratio >= 0.7 else (("partly", "Partly cloudy") if ratio >= 0.3 else ("cloud", "Cloudy"))
    if solar is not None and solar > 400:
        return ("sunny", "Sunny")
    return None


COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass(deg):
    return "" if deg is None else COMPASS[int((float(deg) % 360) / 22.5 + 0.5) % 16]


def units_for(arg=None):
    from . import units as _u
    return "metric" if (arg or _u.system()) == "metric" else "imperial"


def show(key, v, units="imperial"):
    """(number text, unit text) for one stored value."""
    if v is None:
        return "-", ""
    imp = units != "metric"
    if key in ("temp_f", "dew_f", "feels_f"):
        return (f"{v:.1f}", "°F") if imp else (f"{(v - 32) * 5 / 9:.1f}", "°C")
    if key == "hum":
        return f"{v:.0f}", "%"
    if key == "press_in":
        return (f"{v:.2f}", "inHg") if imp else (f"{v / 0.0295300:.1f}", "hPa")
    if key in ("wind_mph", "gust_mph"):
        return (f"{v:.0f}", "mph") if imp else (f"{v * 1.609344:.0f}", "km/h")
    if key == "wind_dir":
        return f"{v:.0f}", "°"
    if key in ("rain_rate_in",):
        return (f"{v:.2f}", "in/h") if imp else (f"{v * 25.4:.1f}", "mm/h")
    if key.startswith("rain_"):
        return (f"{v:.2f}", "in") if imp else (f"{v * 25.4:.1f}", "mm")
    if key == "solar":
        return f"{v:.0f}", "W/m²"
    if key == "uv":
        return f"{v:.1f}", ""
    if key == "dist_mi":
        return (f"{v:.1f}", "mi") if imp else (f"{v * 1.609344:.1f}", "km")
    if key == "lightning_mi":
        return (f"{v:.0f}", "mi") if imp else (f"{v * 1.609344:.0f}", "km")
    if key == "lightning_n":
        return f"{v:.0f}", ""
    return f"{v}", ""


class Reading(dict):
    """A station's latest values; a field it never sent reads as None (so a template can say `is not none`)."""
    def __missing__(self, key):
        return None


def latest_of(row):
    try:
        return Reading(json.loads(row["latest"])) if row["latest"] else Reading()
    except ValueError:
        return Reading()


def pressure_trend(wid):
    """Rising, falling or steady: pressure now against about three hours ago."""
    d = get_db()
    now = d.execute("SELECT press_in FROM wx_readings WHERE station_pk=%s AND press_in IS NOT NULL ORDER BY ts DESC LIMIT 1", (wid,)).fetchone()
    then = d.execute("SELECT press_in FROM wx_readings WHERE station_pk=%s AND press_in IS NOT NULL AND ts <= now() - interval '3 hours' AND ts > now() - interval '6 hours' ORDER BY ts DESC LIMIT 1", (wid,)).fetchone()
    if not now or not then:
        return ""
    diff = now["press_in"] - then["press_in"]
    return "rising" if diff > 0.03 else ("falling" if diff < -0.03 else "steady")


PERIODS = {"24h": ("24 hours", timedelta(hours=24), 300), "7d": ("7 days", timedelta(days=7), 3600), "30d": ("30 days", timedelta(days=30), 10800), "1y": ("1 year", timedelta(days=365), 86400)}


def series(wid, period="24h"):
    """Bucketed averages for the charts: [{t, temp, dew, hum, press, wind, gust, rain, solar, uv}] oldest first. Recent raw rows and older hourly rows are joined."""
    label, window, bucket = PERIODS.get(period, PERIODS["24h"])
    start = datetime.now(timezone.utc) - window
    rows = get_db().execute(
        "WITH s AS (SELECT ts AS t, temp_f AS temp, dew_f AS dew, hum, press_in AS press, wind_mph AS wind, gust_mph AS gust, rain_day_in AS rain, solar, uv FROM wx_readings WHERE station_pk=%(w)s AND ts >= %(a)s "
        "UNION ALL SELECT hour, temp_avg, dew_avg, hum_avg, press_avg, wind_avg, gust_max, rain_day_max, solar_max, uv_max FROM wx_hourly WHERE station_pk=%(w)s AND hour >= %(a)s) "
        "SELECT to_timestamp(floor(extract(epoch from t)/%(b)s)*%(b)s) AS t, AVG(temp) AS temp, AVG(dew) AS dew, AVG(hum) AS hum, AVG(press) AS press, AVG(wind) AS wind, MAX(gust) AS gust, "
        "MAX(rain) AS rain, MAX(solar) AS solar, MAX(uv) AS uv FROM s GROUP BY 1 ORDER BY 1", {"w": wid, "a": start, "b": bucket}).fetchall()
    return [dict(r) for r in rows]


def daily(wid, days=30):
    """One row per local day: high and low temperature, top gust, rain, average humidity."""
    tz = cfg("timezone")
    return get_db().execute(
        "WITH s AS (SELECT ts AS t, temp_f AS lo, temp_f AS hi, gust_mph AS gust, rain_day_in AS rain, hum FROM wx_readings WHERE station_pk=%(w)s AND ts >= now() - make_interval(days => %(d)s) "
        "UNION ALL SELECT hour, temp_min, temp_max, gust_max, rain_day_max, hum_avg FROM wx_hourly WHERE station_pk=%(w)s AND hour >= now() - make_interval(days => %(d)s)) "
        "SELECT (t AT TIME ZONE %(tz)s)::date AS day, MIN(lo) AS low, MAX(hi) AS high, MAX(gust) AS gust, MAX(rain) AS rain, AVG(hum) AS hum FROM s GROUP BY 1 ORDER BY 1 DESC", {"w": wid, "d": days, "tz": tz}).fetchall()


def records(wid):
    """Highs and lows on record for this station (from the raw rows and the hourly roll-ups)."""
    d = get_db()
    q = ("WITH s AS (SELECT ts AS t, temp_f AS lo, temp_f AS hi, gust_mph AS gust, rain_day_in AS rain FROM wx_readings WHERE station_pk=%(w)s "
         "UNION ALL SELECT hour, temp_min, temp_max, gust_max, rain_day_max FROM wx_hourly WHERE station_pk=%(w)s) ")
    out = {}
    for key, order in (("high", "hi DESC"), ("low", "lo ASC"), ("gust", "gust DESC"), ("rain", "rain DESC")):
        col = order.split()[0]
        r = d.execute(q + f"SELECT t, {col} AS v FROM s WHERE {col} IS NOT NULL ORDER BY {order} LIMIT 1", {"w": wid}).fetchone()
        out[key] = r
    return out


def counts():
    d = get_db()
    r = d.execute("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE public AND NOT disabled) AS public, "
                  "COUNT(*) FILTER (WHERE last_report > now() - make_interval(mins => %s) AND NOT disabled) AS online FROM wx_stations", (max(1, cfg_int("wx_stale_minutes")),)).fetchone()
    return dict(r)


def public_stations():
    """Every public station with its latest reading (cached briefly by the callers that serve the map)."""
    out = []
    now = datetime.now(timezone.utc)
    for r in get_db().execute(SELECT + f"WHERE {PUBLIC_WHERE} ORDER BY COALESCE(f.station_id, w.station_id)").fetchall():
        loc = public_loc(r)
        out.append({"row": r, "sid": r["sid"], "name": r["name"] or r["fname"] or r["sid"], "loc": loc, "approx": r["pprivacy"] != "exact", "radius_m": __import__("app.privacy", fromlist=["x"]).radius_m(r["pprivacy"] or "general"), "online": is_online(r, now), "latest": latest_of(r)})
    return out


def map_points(units="imperial"):
    """Points for the maps: only stations that may be shown (hidden ones have no public location), with a short summary for the label."""
    pts = []
    for s in public_stations():
        if not s["loc"]:
            continue
        la = s["latest"]
        t, tu = show("temp_f", la.get("temp_f"), units)
        w, wu = show("wind_mph", la.get("wind_mph"), units)
        pts.append({"id": s["sid"], "name": s["name"], "lat": s["loc"][0], "lon": s["loc"][1], "online": s["online"], "approx": s["approx"], "radius_m": s["radius_m"], "href": "/weather/" + s["sid"],
                    "temp": t if la.get("temp_f") is not None else "", "temp_unit": tu, "hum": la.get("hum"), "wind": w if la.get("wind_mph") is not None else "", "wind_unit": wu,
                    "wind_dir": compass(la.get("wind_dir")), "rain": show("rain_day_in", la.get("rain_day_in"), units)[0] if la.get("rain_day_in") is not None else "",
                    "rain_unit": show("rain_day_in", 0, units)[1], "temp_f": la.get("temp_f"), "cond": condition(la, s["row"]["plat"], s["row"]["plon"]),
                    "age": None if not s["row"]["last_report"] else int((datetime.now(timezone.utc) - s["row"]["last_report"]).total_seconds()),
                    "r": {k: la[k] for k in list(FIELDS) + ["feels_f"] if la.get(k) is not None}, "owner": s["row"]["owner_display"] or s["row"]["owner_name"] or "", "kind": s["row"]["kind"],
                    "last": s["row"]["last_report"].isoformat() if s["row"]["last_report"] else None})
    return pts


# ------------------------------------------------------------------ charts (inline SVG, no JavaScript, like the feeder charts)

def _nice(lo, hi, ticks=4):
    if hi - lo < 1e-9:
        hi, lo = hi + 1, lo - 1
    raw = (hi - lo) / ticks
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    a, b = math.floor(lo / step) * step, math.ceil(hi / step) * step
    return a, b, step


def chart_svg(points, lines, period="24h", convert=lambda v: v, unit="", width=640, height=210, zero=False, bars=False, spacing=None):
    """lines: [(key, color, label)]. Gaps in the data break the line. Time labels follow the site time zone."""
    from markupsafe import Markup
    pts = [p for p in points if any(p.get(k) is not None for k, _c, _l in lines)]
    if not pts:
        return Markup('<p class="dim">No readings for this period yet.</p>')
    label, window, bucket = PERIODS.get(period, PERIODS["24h"])
    if spacing:   # data that arrives less often than the period's slot (a mesh node reports every half hour): lines join across that spacing and the hover finds the nearest reading
        bucket = max(bucket, spacing)
    now = datetime.now(timezone.utc)
    t0, t1 = (now - window).timestamp(), now.timestamp()
    vals = [convert(p[k]) for p in pts for k, _c, _l in lines if p.get(k) is not None]
    lo, hi = (0 if zero else min(vals)), max(vals)
    lo, hi, step = _nice(lo, hi)
    pl, pr, pt, pb = 46, 12, 10, 24
    w, h = width - pl - pr, height - pt - pb
    x = lambda t: pl + w * (t.timestamp() - t0) / (t1 - t0)
    y = lambda v: pt + h * (1 - (v - lo) / (hi - lo))
    grid, v = [], lo
    while v <= hi + 1e-9:
        grid.append(f'<line x1="{pl}" x2="{width - pr}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="var(--line)"/><text x="{pl - 6}" y="{y(v) + 4:.1f}" text-anchor="end" fill="var(--dim)" font-size="10">{v:g}</text>')
        v += step
    tz = ZoneInfo(cfg("timezone"))
    fmt = "%-I %p" if period == "24h" else "%b %-d"
    for i in range(0, 5):
        tt = t0 + (t1 - t0) * i / 4
        dt = datetime.fromtimestamp(tt, tz)
        grid.append(f'<text x="{pl + w * i / 4:.1f}" y="{height - 6}" text-anchor="{"start" if i == 0 else "end" if i == 4 else "middle"}" fill="var(--dim)" font-size="10">{dt.strftime(fmt).replace("AM", "am").replace("PM", "pm")}</text>')
    body = []
    gap = bucket * 2.5
    series = []
    for key, color, lab in lines:
        series.append({"label": lab, "color": color, "pts": [[round(p["t"].timestamp()), round(convert(p[key]), 2)] for p in pts if p.get(key) is not None]})
    meta = {"t0": t0, "t1": t1, "pl": pl, "w": w, "pt": pt, "h": h, "width": width, "height": height, "lo": lo, "hi": hi, "unit": unit, "tz": cfg("timezone"), "bars": bars, "bucket": bucket, "series": series}
    for key, color, _l in lines:
        if bars:
            bw = max(2.0, w * bucket / (t1 - t0) * 0.8)
            for p in pts:
                if p.get(key) is not None:
                    yy = y(convert(p[key]))
                    body.append(f'<rect x="{x(p["t"]) - bw / 2:.1f}" y="{yy:.1f}" width="{bw:.1f}" height="{max(0.5, y(lo) - yy):.1f}" fill="{color}" opacity=".75"/>')
            continue
        d, last = [], None
        for p in pts:
            if p.get(key) is None:
                continue
            ts = p["t"].timestamp()
            d.append(("M" if last is None or ts - last > gap else "L") + f"{x(p['t']):.1f},{y(convert(p[key])):.1f}")
            last = ts
        body.append(f'<path d="{" ".join(d)}" fill="none" stroke="{color}" stroke-width="1.6"/>')
    import html as _html
    data = _html.escape(json.dumps(meta, separators=(",", ":")), quote=True)
    return Markup(f'<svg class="chart" data-chart="{data}" viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{" and ".join(l for _k, _c, l in lines)} over {label}">{"".join(grid)}{"".join(body)}</svg>')
