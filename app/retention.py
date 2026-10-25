"""Admin > Data Retention: one place that shows every store that grows (size, rows, oldest, and what is done with it as it ages) and thins old detail on a schedule.

What each store does is decided by the setting next to it (the Settings link on each row). The weather readings are thinned here: after Thin Readings Older Than (Days) every station keeps one reading per
Thin To (Seconds) and the rest go (the readings kept are real ones, never averages, and the statistics the site shows are the same either way). Radiosonde positions and feeder statistics have their own
thinning (their sections of Settings); the rest are plain "delete after N days". A run goes in small batches that do not lock the tables, has a dry run that only counts, a log, and an audit entry; it runs
by itself once a night (Run Hour, UTC) when anything is set, and by hand with Run Now. Chat pictures and files (Matrix media) are removed after Keep Chat Files For (Days): the message stays, the file
does not. Defaults keep today's behavior until an administrator chooses."""
import threading
import time
from datetime import datetime, timezone

import psycopg
from flask import Blueprint, render_template, request
from psycopg.rows import dict_row

from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int, get_setting, set_setting

bp = Blueprint("retention", __name__)

LOCK_ID = 727031

# label, table, time column, what happens (text built from settings), the Settings section that holds the choices
STORES = [
    ("Weather Readings", "wx_obs", "ts", lambda: _thin_text("wx_thin_days", "wx_thin_seconds") + _del_text("wx_stats_keep_days"), "Weather Stations"),
    ("Radiosonde Positions", "sonde_points", "ts", lambda: _thin_text("sonde_full_days", "sonde_thin_seconds", own=True) + _del_text("sonde_keep_days"), "Radiosondes"),
    ("Feeder Statistics (Detail)", "feeder_samples", "ts", lambda: f"rolled up to one row per hour after {cfg_int('sample_retention_days')} days (the hourly rows stay), then the detail goes", "History"),
    ("Flight Log", "flight_log", "last_seen", lambda: _del_text("history_retention_days"), "History"),
    ("Aircraft Log", "aircraft_log", "last_seen", lambda: _del_text("history_retention_days"), "History"),
    ("Mesh Alert Channel Log", "mesh_chan_log", "ts", lambda: _del_text("meshalert_log_days"), "Mesh Nodes"),
    ("Aircraft Alert Matches", "alert_events", "ts", lambda: _del_text("history_retention_days"), "History"),
    ("Audit Log", "audit_log", "ts", lambda: _del_text("history_retention_days"), "History"),
    ("Sign-In History", "login_events", "ts", lambda: _del_text("history_retention_days"), "History"),
    ("Outside Request Counts", "api_usage_hourly", "hour", lambda: _del_text("usage_keep_days"), "Service Status"),
]
OWN_DB = [
    ("Mesh Packets and Nodes", "mesh_keep_days", "Mesh Nodes"),
    ("ACARS Messages", "acars_keep_days", "ACARS"),
    ("APRS Packets", "aprs_keep_days", "APRS"),
]


def _del_text(key):
    n = cfg_int(key)
    return f"deleted after {n} days" if n > 0 else "never deleted"


def _thin_text(days_key, secs_key, own=False):
    n, s = cfg_int(days_key), cfg_int(secs_key)
    if n <= 0:
        return "not thinned, "
    return f"after {n} days one reading every {_every(s)}, then "


def _every(s):
    return f"{s // 3600} hour{'s' if s // 3600 != 1 else ''}" if s % 3600 == 0 and s >= 3600 else (f"{s // 60} minute{'s' if s // 60 != 1 else ''}" if s % 60 == 0 and s >= 60 else f"{s} seconds")


def _wx_thin_sql(count):
    """Readings older than the thin age that are not the first of their station and time slot."""
    inner = ("SELECT id, row_number() OVER (PARTITION BY src, sid, floor(extract(epoch FROM ts) / %(step)s) ORDER BY ts) AS rn FROM wx_obs WHERE ts < now() - make_interval(days => %(days)s)")
    if count:
        return f"SELECT COUNT(*) AS n FROM ({inner}) x WHERE rn > 1"
    return f"DELETE FROM wx_obs WHERE id IN (SELECT id FROM ({inner}) x WHERE rn > 1 LIMIT %(batch)s)"


def run(app, dry=False, who="schedule"):
    """One retention pass. Returns [(what, rows)] (rows that would go, for a dry run). Never raises."""
    out = []
    started = time.time()
    with app.app_context():
        days, step, batch = cfg_int("wx_thin_days"), max(60, cfg_int("wx_thin_seconds")), max(1000, cfg_int("retention_batch_rows"))
        mdays = cfg_int("matrix_media_keep_days")
        with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock:
            if not lock.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
                return [("Another run is already going", 0)]
            try:
                with psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row) as conn:
                    if days > 0:
                        p = {"step": step, "days": days, "batch": batch}
                        if dry:
                            out.append(("Weather Readings thinned", conn.execute(_wx_thin_sql(True), p).fetchone()["n"]))
                        else:
                            total = 0
                            while True:
                                n = conn.execute(_wx_thin_sql(False), p).rowcount
                                conn.commit()
                                total += n
                                if n < batch:
                                    break
                                time.sleep(0.3)
                            out.append(("Weather Readings thinned", total))
                    if mdays > 0:
                        from . import matrix
                        if matrix.enabled():
                            if dry:
                                out.append(("Chat files that would be removed", None))
                            else:
                                ok, d = matrix.call("/media-prune", "POST", {"days": mdays}, timeout=120)
                                out.append(("Chat files removed", int(d.get("total", 0)) if ok and isinstance(d, dict) else 0 if ok else -1))
                    if not dry:
                        conn.execute("INSERT INTO retention_runs (who, what, rows_removed, seconds) VALUES (%s,%s,%s,%s)",
                                     (who, "; ".join(f"{w}: {n}" for w, n in out) or "nothing is set to thin", sum(max(0, n or 0) for _w, n in out), round(time.time() - started, 1)))
                        conn.execute("DELETE FROM retention_runs WHERE ts < now() - make_interval(days => %s)", (max(1, cfg_int("retention_log_keep_days")),))
                    conn.commit()
            finally:
                lock.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return out


def _stats(db):
    """[(label, rows, bytes, oldest, text, section)] for the stores in the portal's database."""
    rows = []
    for label, table, col, text, section in STORES:
        r = db.execute("SELECT pg_total_relation_size(%s::regclass) AS size, (SELECT reltuples::bigint FROM pg_class WHERE oid = %s::regclass) AS n", (table, table)).fetchone()
        try:
            oldest = db.execute(f"SELECT min({col}) AS t FROM {table}").fetchone()["t"]
        except Exception:
            db.rollback()
            oldest = None
        rows.append({"label": label, "table": table, "size": int(r["size"] or 0), "n": max(0, int(r["n"] or 0)), "oldest": oldest, "text": text(), "section": section})
    return rows


def _slug(name):
    return name.lower().replace(" ", "-")


@bp.route("/admin/retention")
@permission_required("manage_settings")
def page():
    from . import adminservices as svc
    from . import matrix
    from .usage import human
    db = get_db()
    stores = _stats(db)
    for s in stores:
        sv = svc.service_of(s["section"])
        s["href"] = svc.settings_href(sv, _slug(s["section"]))
    own = []
    for label, key, section in OWN_DB:
        sv = svc.service_of(section)
        own.append({"label": label, "text": _del_text(key), "href": svc.settings_href(sv, _slug(section))})
    runs = db.execute("SELECT * FROM retention_runs ORDER BY id DESC LIMIT 30").fetchall()
    media = {"on": matrix.enabled(), "days": cfg_int("matrix_media_keep_days")}
    return render_template("admin_retention.html", active="retention", stores=stores, own=own, runs=runs, human=human, media=media, hour=cfg_int("retention_run_hour"),
                           thin={"days": cfg_int("wx_thin_days"), "href": svc.settings_href("site", "data-retention")}, last=get_setting("retention_last_run"))


@bp.route("/admin/retention/run", methods=["POST"])
@permission_required("manage_settings")
def run_now():
    from flask import current_app
    dry = request.form.get("dry") == "1"
    res = run(current_app._get_current_object(), dry=dry, who="Run Now")
    if not dry:
        audit("retention.run", detail="; ".join(f"{w}: {n}" for w, n in res)[:300] or "nothing set")
    if not res:
        return action_response(True, "Nothing is set to be thinned or removed. Choose how long to keep each kind of data under Settings > Data Retention.", redirect_endpoint="retention.page")
    text = "; ".join(f"{w}: {'?' if n is None else f'{n:,}'}" for w, n in res)
    return action_response(True, ("Dry run, nothing was removed. " if dry else "Done. ") + text + ".", redirect_endpoint="retention.page")


def start(app):
    def loop():
        time.sleep(120)
        while True:
            try:
                with app.app_context():
                    now = datetime.now(timezone.utc)
                    today = now.strftime("%Y-%m-%d")
                    due = get_setting("retention_last_run") != today and now.hour >= cfg_int("retention_run_hour") and (cfg_int("wx_thin_days") > 0 or cfg_int("matrix_media_keep_days") > 0)
                if due:
                    run(app, who="schedule")
                    with app.app_context():
                        set_setting("retention_last_run", today)
            except Exception:
                pass
            time.sleep(1800)
    threading.Thread(target=loop, name="retention", daemon=True).start()
