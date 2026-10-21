"""Amateur radio nets and Winlink gateways: two directories kept by the network's own members.

A *net* is an on-air meeting that repeats on a schedule (a weekly ARES net, a monthly club net, a daily check-in): the page shows when each one next happens, in the viewer's own time zone, and what to tune to. A
*Winlink gateway* is a station that connects radio email to the internet (RMS): its call sign, frequency, mode and place, with the distance from where the visitor is.

Both live in one table (`radio_entries`, told apart by `kind`). Members add and keep their own on My Account > My Nets and Gateways; with Settings > Nets and Gateways > Editors Check New Entries on, a new or changed
entry waits for an editor (the Moderate Repeaters permission) before it shows; administrators and editors manage every entry, can add one for a member, and everything is in the audit log."""
import math
import re
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo, available_timezones

from flask import Blueprint, abort, flash, render_template, request, url_for
from flask_login import current_user, login_required

from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("radiolists", __name__)

HAM_RE = re.compile(r"^[A-Z0-9]{1,2}[0-9][A-Z0-9]{1,4}(-([0-9]|1[0-5]))?$")
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NTH = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "last"}
RECURRENCE = {"weekly": "Every week", "monthly": "Once a month", "daily": "Every day", "irregular": "Irregular (described in the notes)"}
NET_MODES = ["FM voice", "SSB voice", "CW", "DMR", "D-STAR", "Fusion", "P25", "Digital (VARA, Winlink, packet)", "APRS", "Echolink or AllStar", "Other"]
WL_MODES = ["VARA FM", "VARA HF", "ARDOP", "Packet 1200 baud", "Packet 9600 baud", "Pactor", "Telnet (internet)", "Other"]
KINDS = {"net": "Net", "winlink": "Winlink Gateway"}
STATUSES = {"approved": "Showing", "pending": "Waiting for an editor", "hidden": "Hidden"}


# ------------------------------------------------------------------ when a net happens

def matches(e, day):
    rec = e["recurrence"]
    if rec == "daily":
        return True
    if rec not in ("weekly", "monthly") or e["weekday"] is None or day.weekday() != e["weekday"]:
        return False
    if rec == "weekly":
        return True
    nth = e["nth"] or 1
    if nth == 5:
        return (day + timedelta(days=7)).month != day.month
    return (day.day - 1) // 7 + 1 == nth


def _tz(e):
    try:
        return ZoneInfo(e["tz"] or cfg("timezone"))
    except Exception:
        return ZoneInfo("UTC")


def next_runs(e, now, n=3, horizon=70):
    """The next `n` times (UTC, aware) a net happens after `now`; [] for an irregular one or one with no time."""
    if e["recurrence"] not in ("daily", "weekly", "monthly") or not re.match(r"^\d\d:\d\d$", e["at_time"] or ""):
        return []
    tz = _tz(e)
    local = now.astimezone(tz)
    hh, mm = int(e["at_time"][:2]), int(e["at_time"][3:])
    out = []
    for i in range(horizon):
        day = local.date() + timedelta(days=i)
        if matches(e, day):
            dt = datetime.combine(day, dtime(hh, mm), tzinfo=tz)
            if dt >= local:
                out.append(dt.astimezone(timezone.utc))
                if len(out) >= n:
                    break
    return out


def when_text(e):
    """'Every Tuesday at 7:30 PM Central', 'The 2nd Thursday of every month at 8:00 PM Central', 'Every day at ...'."""
    rec = e["recurrence"]
    if rec == "irregular" or not re.match(r"^\d\d:\d\d$", e["at_time"] or ""):
        return "Irregular: see the notes" if rec == "irregular" else "Time not given"
    hh, mm = int(e["at_time"][:2]), int(e["at_time"][3:])
    ref = datetime.now(_tz(e)).replace(hour=hh, minute=mm)
    clock = f"{(hh % 12) or 12}:{mm:02d} {'AM' if hh < 12 else 'PM'}"
    zone = ref.tzname() or ""
    wd = WEEKDAYS[e["weekday"]] if e["weekday"] is not None and 0 <= e["weekday"] <= 6 else ""
    if rec == "daily":
        return f"Every day at {clock} {zone}".strip()
    if rec == "monthly":
        return f"The {NTH.get(e['nth'] or 1, '1st')} {wd} of every month at {clock} {zone}".replace("The last", "The last").strip()
    return f"Every {wd} at {clock} {zone}".strip()


def distance_mi(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


# ------------------------------------------------------------------ checking what a person typed

def clean(kind, f):
    """(values, error) for a form of the given kind."""
    name = " ".join((f.get("name") or "").split())
    v = {"kind": kind, "name": name[:80], "area": " ".join((f.get("area") or "").split())[:120], "contact": " ".join((f.get("contact") or "").split())[:120],
         "notes": (f.get("notes") or "").strip()[:1000], "freq_note": " ".join((f.get("freq_note") or "").split())[:160]}
    mode = (f.get("mode") or "").strip()
    v["mode"] = mode
    if mode not in (NET_MODES if kind == "net" else WL_MODES):
        return None, "Choose a mode."
    url = (f.get("url") or "").strip()
    if url and not re.match(r"^https?://[^\s]+$", url):
        return None, "A website address starts with http:// or https://."
    v["url"] = url[:300]
    ft = (f.get("freq_mhz") or "").strip()
    v["freq_mhz"] = None
    if ft:
        try:
            v["freq_mhz"] = float(ft)
        except ValueError:
            return None, "The frequency is a number in MHz, such as 146.76."
        if not 0.1 <= v["freq_mhz"] <= 6000:
            return None, "The frequency is between 0.1 and 6000 MHz."
    for key, lo, hi, label in (("lat", -90, 90, "Latitude"), ("lon", -180, 180, "Longitude")):
        t = (f.get(key) or "").strip()
        v[key] = None
        if t:
            try:
                v[key] = float(t)
            except ValueError:
                return None, f"{label} is a number."
            if not lo <= v[key] <= hi:
                return None, f"{label} is between {lo} and {hi}."
    if (v["lat"] is None) != (v["lon"] is None):
        return None, "Give both the latitude and the longitude, or neither."
    if kind == "winlink":
        v["name"] = re.sub(r"[^A-Za-z0-9-]", "", name).upper()[:12]
        if not HAM_RE.match(v["name"]):
            return None, "A gateway's name is its call sign, such as KD5VCF or KD5VCF-10."
        if v["freq_mhz"] is None and v["mode"] != "Telnet (internet)":
            return None, "Give the frequency the gateway listens on."
        v.update(recurrence="irregular", weekday=None, nth=None, at_time="", tz="")
        return v, None
    if len(name) < 3:
        return None, "Give the net a name of at least three letters."
    rec = f.get("recurrence", "weekly")
    if rec not in RECURRENCE:
        return None, "Choose how often it meets."
    v["recurrence"] = rec
    v["weekday"], v["nth"], v["at_time"], v["tz"] = None, None, "", ""
    if rec != "irregular":
        t = (f.get("at_time") or "").strip()
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", t):
            return None, "Give the time as hours and minutes on a 24-hour clock, such as 19:30."
        v["at_time"] = t
        tz = (f.get("tz") or cfg("timezone")).strip()
        if tz not in available_timezones():
            return None, "That time zone is not known."
        v["tz"] = tz
    if rec in ("weekly", "monthly"):
        try:
            v["weekday"] = int(f.get("weekday", ""))
        except ValueError:
            return None, "Choose the day of the week."
        if not 0 <= v["weekday"] <= 6:
            return None, "Choose the day of the week."
    if rec == "monthly":
        try:
            v["nth"] = int(f.get("nth", ""))
        except ValueError:
            return None, "Choose which one of the month."
        if v["nth"] not in NTH:
            return None, "Choose which one of the month."
    if rec == "irregular" and not v["notes"]:
        return None, "Say in the notes when it meets."
    return v, None


COLS = ("kind", "name", "freq_mhz", "freq_note", "mode", "area", "lat", "lon", "recurrence", "weekday", "nth", "at_time", "tz", "contact", "url", "notes")


def _insert(v, owner_id, status):
    d = get_db()
    rid = d.execute("INSERT INTO radio_entries (" + ", ".join(COLS) + ", owner_id, status) VALUES (" + ", ".join(["%s"] * len(COLS)) + ", %s, %s) RETURNING id",
                    [v[c] for c in COLS] + [owner_id, status]).fetchone()["id"]
    d.commit()
    return rid


def _update(rid, v, status=None):
    d = get_db()
    sets = ", ".join(f"{c}=%s" for c in COLS if c != "kind") + ", updated_at=now()" + (", status=%s" if status else "")
    d.execute(f"UPDATE radio_entries SET {sets} WHERE id=%s", [v[c] for c in COLS if c != "kind"] + ([status] if status else []) + [rid])
    d.commit()


def new_status(editor=False):
    return "approved" if editor or cfg("radiolist_approval") != "1" else "pending"


def is_editor():
    return current_user.is_authenticated and current_user.has_permission("moderate_repeaters")


def _enabled():
    if cfg("radiolists_enabled") != "1":
        abort(404)


# ------------------------------------------------------------------ the public pages

def _rows(kind):
    return get_db().execute("SELECT e.*, u.username AS owner_name, u.callsign AS owner_call FROM radio_entries e LEFT JOIN users u ON u.id = e.owner_id WHERE e.kind=%s AND e.status='approved' ORDER BY lower(e.name)", (kind,)).fetchall()


@bp.route("/radio/nets")
def nets():
    _enabled()
    now = datetime.now(timezone.utc)
    q = " ".join(request.args.get("q", "").split())[:60].lower()
    day = request.args.get("day", "")
    mode = request.args.get("mode", "")
    rows = []
    for e in _rows("net"):
        if q and q not in (e["name"] + " " + e["area"] + " " + e["contact"] + " " + e["freq_note"] + " " + e["notes"]).lower():
            continue
        if mode and e["mode"] != mode:
            continue
        if day.isdigit() and e["recurrence"] != "daily" and e["weekday"] != int(day):
            continue
        rows.append(dict(e, when=when_text(e), nxt=next_runs(e, now, 3)))
    soon = sorted((r for r in rows if r["nxt"] and r["nxt"][0] < now + timedelta(days=7)), key=lambda r: r["nxt"][0])
    return render_template("radio_nets.html", rows=rows, soon=soon, q=q, day=day, mode=mode, modes=NET_MODES, weekdays=WEEKDAYS, now=now, can_add=cfg("radiolists_enabled") == "1")


@bp.route("/radio/winlink")
def winlink():
    _enabled()
    from . import wxplace
    q = " ".join(request.args.get("q", "").split())[:120]
    mode = request.args.get("mode", "")
    here, label = None, ""
    try:
        la, lo = float(request.args.get("lat", "")), float(request.args.get("lon", ""))
        if -90 <= la <= 90 and -180 <= lo <= 180:
            here, label = (la, lo), request.args.get("label", "") or f"{la:.3f}, {lo:.3f}"
    except ValueError:
        pass
    if here is None and q:
        c = wxplace.resolve(q)
        if c:
            here, label = (c[0]["lat"], c[0]["lon"]), c[0]["label"]
    rows = []
    for e in _rows("winlink"):
        if mode and e["mode"] != mode:
            continue
        if q and here is None and q.lower() not in (e["name"] + " " + e["area"] + " " + e["notes"]).lower():
            continue
        dist = distance_mi(here[0], here[1], e["lat"], e["lon"]) if here and e["lat"] is not None else None
        rows.append(dict(e, dist=dist))
    rows.sort(key=lambda r: (r["dist"] is None, r["dist"] if r["dist"] is not None else 0, r["name"]))
    return render_template("radio_winlink.html", rows=rows, q=q, mode=mode, modes=WL_MODES, here=here, label=label)


# ------------------------------------------------------------------ My Account

def _mine(kind=None):
    return get_db().execute("SELECT * FROM radio_entries WHERE owner_id=%s" + (" AND kind=%s" if kind else "") + " ORDER BY kind, lower(name)", ([current_user.id] + ([kind] if kind else []))).fetchall()


def _my_ctx():
    rows = _mine()
    return {"nets": [r for r in rows if r["kind"] == "net"], "gateways": [r for r in rows if r["kind"] == "winlink"], "limit": cfg_int("radiolist_per_user"), "approval": cfg("radiolist_approval") == "1", **form_ctx()}


def form_ctx():
    return {"net_modes": NET_MODES, "wl_modes": WL_MODES, "weekdays": WEEKDAYS, "nth": NTH, "recurrence": RECURRENCE, "statuses": STATUSES, "site_tz": cfg("timezone"),
            "tzs": ["America/Chicago", "America/New_York", "America/Denver", "America/Los_Angeles", "America/Phoenix", "America/Anchorage", "Pacific/Honolulu", "UTC"]}


@bp.route("/my/nets")
@login_required
def my_page():
    _enabled()
    return render_template("my_listings.html", **_my_ctx())


def _resp(ok, message, template="partials/_my_listings.html"):
    return action_response(ok, message, redirect_endpoint="radiolists.my_page", fragment_template=template, fragment_context=_my_ctx())


@bp.route("/my/nets/add", methods=["POST"])
@login_required
def my_add():
    _enabled()
    kind = request.form.get("kind", "net")
    if kind not in KINDS:
        abort(400)
    if len(_mine()) >= cfg_int("radiolist_per_user"):
        return _resp(False, f"You can keep {cfg_int('radiolist_per_user')} entries. Remove one to add another.")
    v, err = clean(kind, request.form)
    if err:
        return _resp(False, err)
    status = new_status(is_editor())
    rid = _insert(v, current_user.id, status)
    audit("radiolist.add", target=f"{kind} {v['name']}", detail=f"#{rid}, {status}")
    return _resp(True, f"{v['name']} is added." + (" An editor checks it before it shows." if status == "pending" else " It shows now."))


@bp.route("/my/nets/<int:rid>/save", methods=["POST"])
@login_required
def my_save(rid):
    _enabled()
    d = get_db()
    e = d.execute("SELECT * FROM radio_entries WHERE id=%s AND owner_id=%s", (rid, current_user.id)).fetchone()
    if e is None:
        abort(404)
    v, err = clean(e["kind"], request.form)
    if err:
        return _resp(False, err)
    status = new_status(is_editor()) if e["status"] != "hidden" else None
    _update(rid, v, status)
    audit("radiolist.update", target=f"{e['kind']} {v['name']}", detail=f"#{rid}")
    return _resp(True, "Saved." + (" An editor checks the change before it shows." if status == "pending" else ""))


@bp.route("/my/nets/<int:rid>/delete", methods=["POST"])
@login_required
def my_delete(rid):
    _enabled()
    d = get_db()
    e = d.execute("SELECT kind, name FROM radio_entries WHERE id=%s AND owner_id=%s", (rid, current_user.id)).fetchone()
    if e is None:
        abort(404)
    d.execute("DELETE FROM radio_entries WHERE id=%s", (rid,))
    d.commit()
    audit("radiolist.delete", target=f"{e['kind']} {e['name']}", detail=f"#{rid}")
    return _resp(True, f"{e['name']} is removed.")


# ------------------------------------------------------------------ Admin and editors

def _admin_ctx(kind):
    status = request.args.get("status", "")
    rows = get_db().execute("SELECT e.*, u.username AS owner_name, u.display_name AS owner_display FROM radio_entries e LEFT JOIN users u ON u.id = e.owner_id WHERE e.kind=%s"
                            + (" AND e.status=%s" if status in STATUSES else "") + " ORDER BY (e.status='pending') DESC, lower(e.name)", [kind] + ([status] if status in STATUSES else [])).fetchall()
    pending = get_db().execute("SELECT COUNT(*) AS n FROM radio_entries WHERE kind=%s AND status='pending'", (kind,)).fetchone()["n"]
    return {"kind": kind, "label": KINDS[kind], "rows": rows, "status": status, "pending": pending, "active": "nets" if kind == "net" else "winlink", **form_ctx()}


def _admin_resp(kind, ok, message):
    return action_response(ok, message, redirect_endpoint="radiolists.admin_nets" if kind == "net" else "radiolists.admin_winlink", fragment_template="partials/_admin_listings.html", fragment_context=_admin_ctx(kind))


@bp.route("/admin/nets")
@permission_required("moderate_repeaters")
def admin_nets():
    return render_template("admin_listings.html", **_admin_ctx("net"))


@bp.route("/admin/winlink")
@permission_required("moderate_repeaters")
def admin_winlink():
    return render_template("admin_listings.html", **_admin_ctx("winlink"))


@bp.route("/admin/listings/add", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_add():
    """Add one for a member (or for the network itself when no member is named): showing at once."""
    kind = request.form.get("kind", "net")
    if kind not in KINDS:
        abort(400)
    d = get_db()
    who = (request.form.get("username") or "").strip()
    owner = None
    if who:
        u = d.execute("SELECT id FROM users WHERE lower(username)=lower(%s) OR lower(email)=lower(%s)", (who, who)).fetchone()
        if u is None:
            return _admin_resp(kind, False, "No member has that user name or email address.")
        owner = u["id"]
    v, err = clean(kind, request.form)
    if err:
        return _admin_resp(kind, False, err)
    rid = _insert(v, owner, "approved")
    audit("radiolist.admin_add", target=f"{kind} {v['name']}", detail=f"#{rid}" + (f" for {who}" if who else ""))
    return _admin_resp(kind, True, f"{v['name']} is added and showing.")


@bp.route("/admin/listings/<int:rid>/save", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_save(rid):
    d = get_db()
    e = d.execute("SELECT * FROM radio_entries WHERE id=%s", (rid,)).fetchone()
    if e is None:
        abort(404)
    v, err = clean(e["kind"], request.form)
    if err:
        return _admin_resp(e["kind"], False, err)
    _update(rid, v)
    audit("radiolist.admin_update", target=f"{e['kind']} {v['name']}", detail=f"#{rid}")
    return _admin_resp(e["kind"], True, "Saved.")


@bp.route("/admin/listings/<int:rid>/status", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_status(rid):
    d = get_db()
    e = d.execute("SELECT kind, name, status FROM radio_entries WHERE id=%s", (rid,)).fetchone()
    if e is None:
        abort(404)
    to = request.form.get("to", "")
    if to not in STATUSES:
        abort(400)
    d.execute("UPDATE radio_entries SET status=%s, reviewed_by=%s, updated_at=now() WHERE id=%s", (to, current_user.username, rid))
    d.commit()
    audit("radiolist.status", target=f"{e['kind']} {e['name']}", detail=f"#{rid}: {e['status']} to {to}")
    return _admin_resp(e["kind"], True, f"{e['name']}: {STATUSES[to].lower()}.")


@bp.route("/admin/listings/<int:rid>/delete", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_delete(rid):
    d = get_db()
    e = d.execute("SELECT kind, name FROM radio_entries WHERE id=%s", (rid,)).fetchone()
    if e is None:
        abort(404)
    d.execute("DELETE FROM radio_entries WHERE id=%s", (rid,))
    d.commit()
    audit("radiolist.admin_delete", target=f"{e['kind']} {e['name']}", detail=f"#{rid}")
    return _admin_resp(e["kind"], True, f"{e['name']} is deleted.")
