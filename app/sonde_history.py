"""Historical balloon flights: every flight SondeHub's archive holds for Arkansas and the launch sites around it, brought in as full flights (track, burst, landing, who heard it) so they look
and behave like the flights recorded live. A slow, resumable background job: it lists the archive month by month (newest first, one request per site and month), then fetches each flight that is
not here yet (one request per flight, a pause between flights), thins it to the interval set in Settings and keeps it for good (source 'archive'). Finished past months are never listed again.
Data: SondeHub Radiosonde Telemetry, CC BY-SA 2.0 (DATA_SOURCES_TERMS.md); sites, dates, pause and thinning are in Settings > Radiosondes."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from . import sonde_archive as sa
from .settings import cfg, cfg_float, cfg_int, get_setting, set_setting

STATE = {"running": False, "site": "", "ym": "", "serial": "", "imported": 0, "skipped": 0, "errors": 0, "error": "", "started": None, "last": None}
_lock = threading.Lock()


def sites():
    return [s.strip() for s in cfg("sonde_history_sites").split(",") if s.strip()]


def months(since):
    """Year-months from the current one back to `since`, newest first."""
    try:
        d0 = datetime.strptime(since, "%Y-%m-%d").date().replace(day=1)
    except ValueError:
        d0 = datetime(2022, 1, 1).date()
    d = datetime.now(timezone.utc).date().replace(day=1)
    out = []
    while d >= d0:
        out.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    return out


def list_serials(site, ym):
    """The serials the archive holds for a launch site in a month (an anonymous listing with a prefix; the bare bucket cannot be listed)."""
    y, m = ym.split("-")
    base = cfg("sonde_archive_url").rstrip("/")
    keys, token = [], None
    while True:
        q = {"list-type": "2", "prefix": f"launchsites/{site}/{y}/{m}/", "max-keys": "1000"}
        if token:
            q["continuation-token"] = token
        req = urllib.request.Request(base + "/?" + urllib.parse.urlencode(q), headers={"User-Agent": cfg("lookup_user_agent")})
        with urllib.request.urlopen(req, timeout=60) as r:
            root = ET.fromstring(r.read(20_000_000))
        ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
        keys += [k.text for k in root.iter(ns + "Key") if k.text and k.text.endswith(".json")]
        trunc, nt = root.find(ns + "IsTruncated"), root.find(ns + "NextContinuationToken")
        if trunc is not None and trunc.text == "true" and nt is not None and nt.text:
            token = nt.text
            time.sleep(0.3)
        else:
            return [k.rsplit("/", 1)[-1][:-5] for k in keys]


def import_flight(conn, serial, fetch=None):
    """Bring one flight in, thinned to the history interval. Returns 'imported', 'have' or a reason it was skipped."""
    if conn.execute("SELECT 1 FROM sondes WHERE lower(serial)=lower(%s)", (serial,)).fetchone():
        return "have"
    if conn.execute("SELECT 1 FROM sonde_history_skipped WHERE serial=%s AND ts > now() - interval '30 days'", (serial,)).fetchone():
        return "skipped"
    packets, why = (fetch or sa.fetch_serial)(serial)
    if packets is None:
        conn.execute("INSERT INTO sonde_history_skipped (serial, reason, ts) VALUES (%s,%s,now()) ON CONFLICT (serial) DO UPDATE SET reason=EXCLUDED.reason, ts=now()", (serial, why[:200]))
        conn.commit()
        return "skipped"
    n = sa.import_packets(conn, serial, packets, keep_every=max(1, cfg_int("sonde_history_thin_seconds")))
    if not n:
        conn.execute("INSERT INTO sonde_history_skipped (serial, reason, ts) VALUES (%s,'no usable positions',now()) ON CONFLICT (serial) DO UPDATE SET reason=EXCLUDED.reason, ts=now()", (serial,))
        conn.commit()
        return "skipped"
    return "imported"


def step_month(conn, site, ym, lister=None, fetch=None, pause=None, should_stop=lambda: False):
    """One launch site and month: list, then import what is missing. Marks the month done when it is a past month (the current month is looked at again each day)."""
    serials = (lister or list_serials)(site, ym)
    STATE.update(site=site, ym=ym)
    n_new = 0
    for sr in serials:
        if should_stop():
            return n_new, False
        STATE["serial"] = sr
        try:
            res = import_flight(conn, sr, fetch)
        except Exception as e:   # one bad flight never stops the job
            conn.rollback()
            STATE["errors"] += 1
            STATE["error"] = f"{sr}: {type(e).__name__}: {str(e)[:100]}"
            res = "error"
        if res == "imported":
            STATE["imported"] += 1
            n_new += 1
            time.sleep(cfg_float("sonde_history_pause_seconds") if pause is None else pause)
        elif res in ("skipped", "error"):
            STATE["skipped"] += 1
    conn.execute("INSERT INTO sonde_history_months (site, ym, done, flights) VALUES (%s,%s,now(),%s) ON CONFLICT (site, ym) DO UPDATE SET done=now(), flights=sonde_history_months.flights + EXCLUDED.flights", (site, ym, n_new))
    conn.commit()
    return n_new, True


def pending(conn):
    """The (site, month) pairs still to look at, newest first: past months are done once; the current month again after a day."""
    done = {(r["site"], r["ym"]): r["done"] for r in conn.execute("SELECT site, ym, done FROM sonde_history_months").fetchall()}
    this = datetime.now(timezone.utc).strftime("%Y-%m")
    out = []
    for ym in months(cfg("sonde_history_since")):
        for site in sites():
            d = done.get((site, ym))
            if d is None or (ym == this and datetime.now(timezone.utc) - d > timedelta(hours=24)):
                out.append((site, ym))
    return out


def progress():
    """What the job last did (written by whichever worker runs it), for the admin page."""
    try:
        return json.loads(get_setting("sonde_history_progress") or "{}")
    except ValueError:
        return {}


def counts(conn):
    r = conn.execute("SELECT COUNT(*) FILTER (WHERE source='archive') AS n, MIN(first_seen) FILTER (WHERE source='archive') AS first FROM sondes").fetchone()
    m = conn.execute("SELECT COUNT(*) AS done FROM sonde_history_months").fetchone()["done"]
    left = len(pending(conn))
    return {"flights": r["n"], "oldest": r["first"], "months_done": m, "months_left": left, "skipped": conn.execute("SELECT COUNT(*) AS n FROM sonde_history_skipped").fetchone()["n"]}


def worker(app):
    """The background thread: while the job is switched on (Settings), work through the pending months one flight at a time."""
    import psycopg
    from psycopg.rows import dict_row
    conn = None
    while True:
        try:
            with app.app_context():
                on = cfg("sonde_history_enabled") == "1" and cfg("sonde_enabled") == "1"
                if not on:
                    STATE["running"] = False
                    time.sleep(20)
                    continue
                if conn is None or conn.closed:
                    conn = psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row)
                    conn.autocommit = False
                if not conn.execute("SELECT pg_try_advisory_lock(7351001) AS got").fetchone()["got"]:   # one web worker at a time runs the job
                    conn.rollback()
                    time.sleep(60)
                    continue
                todo = pending(conn)
                if not todo:
                    STATE["running"] = False
                    time.sleep(600)
                    continue
                STATE.update(running=True, started=STATE["started"] or datetime.now(timezone.utc).isoformat())
                site, ym = todo[0]
                try:
                    step_month(conn, site, ym, should_stop=lambda: cfg("sonde_history_enabled") != "1")
                except (urllib.error.URLError, OSError, ET.ParseError) as e:
                    conn.rollback()
                    STATE["error"] = f"{site} {ym}: {type(e).__name__}"
                    time.sleep(60)
                STATE["last"] = datetime.now(timezone.utc).isoformat()
                set_setting("sonde_history_progress", json.dumps({"site": site, "ym": ym, "at": STATE["last"], "imported": STATE["imported"], "skipped": STATE["skipped"], "errors": STATE["errors"], "error": STATE["error"]}))   # for the admin page (any web worker can read it)
        except Exception as e:
            STATE["error"] = f"{type(e).__name__}: {str(e)[:100]}"
            try:
                conn and conn.rollback()
            except Exception:
                pass
            time.sleep(30)


def start(app):
    with _lock:
        if STATE.get("thread"):
            return
        STATE["thread"] = True
    threading.Thread(target=worker, args=(app,), daemon=True, name="sonde-history").start()
