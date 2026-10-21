"""What the site asks of the outside world and what it keeps: Admin > Usage.

Every web request this program makes goes through urllib's urlopen, so ONE wrapper installed at start-up counts them all by host (requests, errors, bytes read, last time) without touching the
dozens of places that make them. Each worker keeps its counts in memory and adds them to the `api_usage` table once a minute (and when the page is opened), so the numbers are the whole site's.
The separate service containers (APRS, ACARS, mesh, prediction and archive) report their own database sizes and outside requests in their /stats, which this page reads.
Live subscriptions (SondeHub's feed, APRS-IS, MQTT) are connections, not requests: their message counters are shown separately.

Counted: calls made, failures (a refused or timed out call, or an HTTP error), and bytes read from the answer (read() on the response; an answer the code never reads is counted as a call only)."""
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

_orig = urllib.request.urlopen
_lock = threading.Lock()
_buf = {}          # (day, hour, host) -> [calls, errors, bytes, last_epoch]; hour is the UTC hour as "YYYY-MM-DD HH:00+00"
_state = {"dsn": "", "started": False, "installed": False, "app": None}

NAMES = {
    "api.v2.sondehub.org": "SondeHub API",
    "sondehub-history.s3.amazonaws.com": "SondeHub archive (AWS Open Data)",
    "sondehub-history.s3.us-east-1.amazonaws.com": "SondeHub archive (AWS Open Data)",
    "api.weather.gov": "National Weather Service",
    "earthquake.usgs.gov": "USGS Earthquake Catalog",
    "api.water.noaa.gov": "NOAA National Water Prediction Service",
    "aviationweather.gov": "Aviation Weather Center",
    "nominatim.openstreetmap.org": "OpenStreetMap Nominatim",
    "photon.komoot.io": "Photon geocoder",
    "tile.openstreetmap.org": "OpenStreetMap tiles",
    "raw.githubusercontent.com": "GitHub (public content, update check)",
    "api.github.com": "GitHub (update check)",
    "hub.docker.com": "Docker Hub",
    "registry.hub.docker.com": "Docker Hub",
    "www.hearham.com": "Hearham repeaters",
    "hearham.com": "Hearham repeaters",
    "radioid.net": "RadioID",
    "www.radioid.net": "RadioID",
    "ourairports.com": "OurAirports",
    "davidmegginson.github.io": "OurAirports data",
    "registry.opendata.aws": "AWS Open Data",
    "api.open-meteo.com": "Open-Meteo (GFS forecast winds)",
    "mqtt.meshtastic.org": "Meshtastic public server",
}


def _private(host):
    h = (host or "").lower()
    if not h or "." not in h or h.endswith(".local") or h.endswith(".internal") or h == "localhost":
        return True
    p = h.split(".")
    try:
        a, b = int(p[0]), int(p[1])
        return a == 10 or a == 127 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
    except (ValueError, IndexError):
        return False


def host_of(target):
    url = target.full_url if hasattr(target, "full_url") else str(target)
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _add(host, calls=0, errors=0, nbytes=0):
    if not host:
        return
    key = (date.today().isoformat(), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:00+00"), host)
    with _lock:
        row = _buf.setdefault(key, [0, 0, 0, 0.0])
        row[0] += calls
        row[1] += errors
        row[2] += nbytes
        row[3] = time.time()


def _counted(host):
    def wrap(resp):
        try:
            orig = resp.read

            def read(*a, **k):
                b = orig(*a, **k)
                _add(host, nbytes=len(b) if b else 0)
                return b
            resp.read = read
        except (AttributeError, TypeError):
            pass
        return resp
    return wrap


def urlopen(target, *a, **k):
    host = host_of(target)
    try:
        resp = _orig(target, *a, **k)
    except Exception:
        _add(host, calls=1, errors=1)
        raise
    _add(host, calls=1)
    return _counted(host)(resp)


def install(dsn, app=None):
    """Count every urllib request of this process and start adding the counts to the database. Safe to call twice."""
    _state["dsn"] = dsn
    _state["app"] = app or _state["app"]
    if not _state["installed"]:
        urllib.request.urlopen = urlopen
        _state["installed"] = True
    if not _state["started"]:
        _state["started"] = True
        threading.Thread(target=_loop, daemon=True, name="usage-flush").start()


def _loop():
    while True:
        time.sleep(60)
        flush()
        sample()


def flush():
    """Add this process's counts to the table. Counts that could not be written stay for the next try."""
    with _lock:
        take, _buf_items = dict(_buf), None
        _buf.clear()
    if not take or not _state["dsn"]:
        return 0
    try:
        import psycopg
        with psycopg.connect(_state["dsn"]) as c:
            c.execute("SELECT pg_advisory_xact_lock_shared(727013)")   # a restore takes this exclusively, so the two never wait on each other's tables (see backup.pause_background)
            for (day, hour, host), (calls, errors, nbytes, last) in take.items():
                c.execute("INSERT INTO api_usage (day, host, calls, errors, bytes, last_call) VALUES (%s,%s,%s,%s,%s,to_timestamp(%s)) ON CONFLICT (day, host) DO UPDATE SET "
                          "calls = api_usage.calls + EXCLUDED.calls, errors = api_usage.errors + EXCLUDED.errors, bytes = api_usage.bytes + EXCLUDED.bytes, "
                          "last_call = GREATEST(api_usage.last_call, EXCLUDED.last_call)", (day, host, calls, errors, nbytes, last))
                c.execute("INSERT INTO api_usage_hourly (hour, host, calls, errors, bytes, last_call) VALUES (%s,%s,%s,%s,%s,to_timestamp(%s)) ON CONFLICT (hour, host) DO UPDATE SET "
                          "calls = api_usage_hourly.calls + EXCLUDED.calls, errors = api_usage_hourly.errors + EXCLUDED.errors, bytes = api_usage_hourly.bytes + EXCLUDED.bytes, "
                          "last_call = GREATEST(api_usage_hourly.last_call, EXCLUDED.last_call)", (hour, host, calls, errors, nbytes, last))
    except Exception:
        with _lock:
            for k, v in take.items():
                row = _buf.setdefault(k, [0, 0, 0, 0.0])
                row[0] += v[0]; row[1] += v[1]; row[2] += v[2]; row[3] = max(row[3], v[3])
        return 0
    return len(take)


# ------------------------------------------------------------------ the live feeds and the service containers
# Their counters live in the services' memory and start again at zero whenever a service restarts, so once a minute the portal reads them, works out what is new since the last reading
# (a counter that went down means a restart: everything it shows is new) and adds that to tables of its own. The totals therefore cover all time, not just the time since a start.

def _new_since(c, key, v1, v2=0):
    """What a cumulative counter gained since the last reading of `key` (remembered in usage_state)."""
    row = c.execute("SELECT v1, v2 FROM usage_state WHERE k = %s", (key,)).fetchone()
    c.execute("INSERT INTO usage_state (k, v1, v2) VALUES (%s,%s,%s) ON CONFLICT (k) DO UPDATE SET v1 = EXCLUDED.v1, v2 = EXCLUDED.v2", (key, v1, v2))
    if row is None or v1 < row[0] or v2 < row[1]:
        return v1, v2
    return v1 - row[0], v2 - row[1]


def sample():
    """Read every live feed's and service's counters once and add what is new to the all-time tables. One worker at a time (an advisory lock); never raises."""
    app = _state.get("app")
    if not app or not _state["dsn"]:
        return 0
    try:
        import psycopg
        from . import usage_views
        with app.app_context():
            feeds, outs = usage_views.counters()
        hour = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:00+00")
        day = date.today().isoformat()
        with psycopg.connect(_state["dsn"]) as c:
            if not c.execute("SELECT pg_try_advisory_xact_lock(727014)").fetchone()[0]:
                return 0
            for name, total in feeds.items():
                add, _x = _new_since(c, "feed|" + name, int(total or 0))
                if add:
                    c.execute("INSERT INTO feed_usage_hourly (hour, feed, msgs) VALUES (%s,%s,%s) ON CONFLICT (hour, feed) DO UPDATE SET msgs = feed_usage_hourly.msgs + EXCLUDED.msgs", (hour, name, add))
            for (via, host, d), (calls, nbytes) in outs.items():
                a, b = _new_since(c, f"svc|{via}|{host}|{d}", int(calls or 0), int(nbytes or 0))
                if a or b:
                    c.execute("INSERT INTO svc_usage (day, via, host, calls, bytes) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (day, via, host) DO UPDATE SET calls = svc_usage.calls + EXCLUDED.calls, "
                              "bytes = svc_usage.bytes + EXCLUDED.bytes", (d, via, host, a, b))
            c.execute("DELETE FROM usage_state WHERE k LIKE 'svc|%%' AND k NOT LIKE %s", ("%|" + day,))   # only today's per-day counters are still growing
        return len(feeds) + len(outs)
    except Exception:
        return 0


# ------------------------------------------------------------------ reading it back

def parse_when(text, default):
    """An ISO date or date and time (the page sends UTC) as an aware datetime; `default` when it is blank or not understood."""
    try:
        t = datetime.fromisoformat((text or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return default
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def outbound(db, start, end):
    """One row per host: calls today and in 7 days, and for the chosen range (start up to end) calls, failures and bytes, plus the last call of all time."""
    rows = db.execute(
        "SELECT host, SUM(calls) FILTER (WHERE day = current_date) AS today, SUM(calls) FILTER (WHERE day > current_date - 7) AS d7, MAX(last_call) AS last FROM api_usage GROUP BY host").fetchall()
    rng = {r["host"]: r for r in db.execute("SELECT host, SUM(calls) AS calls, SUM(errors) AS errors, SUM(bytes) AS bytes FROM api_usage_hourly WHERE hour >= %s AND hour < %s GROUP BY host", (start, end)).fetchall()}
    out = []
    for r in rows:
        g = rng.get(r["host"])
        out.append({"host": r["host"], "name": NAMES.get(r["host"], ""), "internal": _private(r["host"]), "today": int(r["today"] or 0), "d7": int(r["d7"] or 0),
                    "calls": int(g["calls"] or 0) if g else 0, "errors": int(g["errors"] or 0) if g else 0, "bytes": int(g["bytes"] or 0) if g else 0, "last": r["last"]})
    return sorted(out, key=lambda x: -x["calls"])


def feed_totals(db, start, end):
    """{feed: messages in the range} and {feed: messages of all time}."""
    a = {r["feed"]: int(r["msgs"] or 0) for r in db.execute("SELECT feed, SUM(msgs) AS msgs FROM feed_usage_hourly WHERE hour >= %s AND hour < %s GROUP BY feed", (start, end)).fetchall()}
    b = {r["feed"]: int(r["msgs"] or 0) for r in db.execute("SELECT feed, SUM(msgs) AS msgs FROM feed_usage_hourly GROUP BY feed").fetchall()}
    return a, b


def service_requests(db, start, end):
    """Outside requests made by the service containers, by host: the days from the start's date to the end's date."""
    return db.execute("SELECT via, host, SUM(calls) AS calls, SUM(bytes) AS bytes FROM svc_usage WHERE day >= %s AND day <= %s GROUP BY via, host ORDER BY SUM(calls) DESC",
                      (start.date(), end.date())).fetchall()


def daily(db, host, days=30):
    return db.execute("SELECT day, calls, errors, bytes FROM api_usage WHERE host=%s AND day > current_date - %s ORDER BY day", (host, days)).fetchall()


def prune(db, keep_days):
    """Remove counts older than keep_days; 0 keeps everything."""
    if int(keep_days or 0) <= 0:
        return
    db.execute("DELETE FROM api_usage WHERE day < current_date - %s", (int(keep_days),))
    db.execute("DELETE FROM api_usage_hourly WHERE hour < now() - make_interval(days => %s)", (int(keep_days),))
    db.execute("DELETE FROM feed_usage_hourly WHERE hour < now() - make_interval(days => %s)", (int(keep_days),))
    db.execute("DELETE FROM svc_usage WHERE day < current_date - %s", (int(keep_days),))
    db.commit()


# which service a table of the portal's own database belongs to (first match wins; the rest is "Site")
GROUPS = [
    ("Radiosondes", ("sonde",)), ("Weather stations", ("weather", "wx_")), ("Aircraft history", ("flight", "aircraft", "sighting", "hist")), ("Feeders and MLAT", ("feeder", "mlat", "station")),
    ("Repeaters", ("repeater", "dmr")), ("ACARS", ("acars",)), ("Mesh", ("mesh",)), ("APRS", ("aprs",)), ("Places and maps", ("airport", "hospital", "place", "count", "road", "layer", "geocode", "interesting", "hems")),
    ("Accounts and logs", ("user", "role", "session", "audit", "signin", "login", "alert", "webhook", "setting")),
]


def portal_storage(db):
    """The portal database's tables, grouped by what they belong to: [(group, bytes, rows)] largest first, and the whole database's size."""
    rows = db.execute("SELECT c.relname AS name, pg_total_relation_size(c.oid) AS size, c.reltuples::bigint AS n FROM pg_class c JOIN pg_namespace ns ON ns.oid = c.relnamespace "
                      "WHERE c.relkind = 'r' AND ns.nspname = 'public'").fetchall()
    groups = {}
    for r in rows:
        name = r["name"].lower()
        g = next((label for label, pre in GROUPS if any(name.startswith(p) or p in name for p in pre)), "Other")
        s = groups.setdefault(g, [0, 0])
        s[0] += int(r["size"] or 0)
        s[1] += max(0, int(r["n"] or 0))
    total = db.execute("SELECT pg_database_size(current_database()) AS s").fetchone()["s"]
    return sorted(([g, v[0], v[1]] for g, v in groups.items()), key=lambda x: -x[1]), int(total)


def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return (f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}")
        n /= 1024
