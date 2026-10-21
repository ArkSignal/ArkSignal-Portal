"""Admin > Data Updates: the schedule of every outside data set, with Run Now."""
from flask import Blueprint, current_app, render_template, request
from zoneinfo import ZoneInfo

from . import dataupdates as du
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .settings import cfg, set_setting

bp = Blueprint("dataupdates", __name__, url_prefix="/admin/data-updates")


def _ctx():
    tz = ZoneInfo(cfg("timezone"))
    running = du.running_jobs()
    jobs = []
    for j in du.JOBS:
        st = du.state(j.key, tz)
        b = du.blocking_job(j.key)
        st["blocked_by"] = (b.label if b.key != j.key else "") if b else ""   # a related source is running: this one waits
        jobs.append(st)
    return {"busy": ", ".join(x.label for x in running), "jobs": jobs, "time": cfg("airports_update_time"), "trauma_url": cfg("trauma_source_url"), "tzname": cfg("timezone"), "active": "dataupdates"}


def _respond(ok, msg):
    return action_response(ok, msg, redirect_endpoint="dataupdates.page", fragment_template="partials/_data_updates_list.html", fragment_context=_ctx())


@bp.route("/")
@permission_required("manage_map_data")
def page():
    return render_template("admin_data_updates.html", **_ctx())


@bp.route("/<key>/schedule", methods=["POST"])
@permission_required("manage_map_data")
def schedule(key):
    if key not in du.BY_KEY:
        return _respond(False, "Unknown data source.")
    raw = request.form.get("days", "").strip()
    if not raw.isdigit() or not (0 <= int(raw) <= 365):
        return _respond(False, "Enter a whole number of days from 0 (off) to 365.")
    old = du.days_of(key)
    set_setting(f"{key}_update_days", raw)
    audit("dataupdate.schedule", target=key, detail=f"{old} -> {raw} days")
    return _respond(True, f"{du.BY_KEY[key].label}: " + ("automatic updates are off." if raw == "0" else f"updates every {raw} day{'' if raw == '1' else 's'}."))


@bp.route("/time", methods=["POST"])
@permission_required("manage_map_data")
def set_time():
    import re
    t = request.form.get("time", "").strip()
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", t):
        return _respond(False, "Enter the time as HH:MM, for example 03:30.")
    set_setting("airports_update_time", t)
    audit("dataupdate.time", detail=t)
    return _respond(True, f"Automatic updates start at {t} ({cfg('timezone')}).")


@bp.route("/trauma-url", methods=["POST"])
@permission_required("manage_map_data")
def trauma_url():
    from urllib.parse import urlparse
    u = request.form.get("url", "").strip()
    if u and not (u.startswith("https://") and urlparse(u).netloc and " " not in u and len(u) <= 500):
        return _respond(False, "The address must be a full https:// address of a CSV file.")
    set_setting("trauma_source_url", u)
    audit("dataupdate.trauma_url", detail="set" if u else "cleared")
    return _respond(True, "Source address saved." if u else "Source address cleared.")


@bp.route("/<key>/run", methods=["POST"])
@permission_required("manage_map_data")
def run(key):
    if key not in du.BY_KEY:
        return _respond(False, "Unknown data source.")
    busy = du.blocking_job(key)
    if busy is not None:
        return _respond(False, "It is already running." if busy.key == key else f"{busy.label} is running now and is linked to {du.BY_KEY[key].label} (one uses the other's data), so this one waits for it to finish.")
    audit("dataupdate.run_now", target=key)
    du.run_in_background(current_app._get_current_object(), key)
    return _respond(True, f"{du.BY_KEY[key].label} started. It runs in the background; this page shows the result when it finishes.")
