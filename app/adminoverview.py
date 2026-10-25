"""Admin > Overview: what an administrator wants to know on arrival. What needs attention first (with a link to fix each), then the network at a glance, people,
the health of the data we keep, the system, and the latest activity. Everything is read from what the rest of the site already keeps; nothing here is a second source of truth."""
import os
import time
from datetime import datetime, timezone

from .db import get_db
from .settings import cfg, get_setting


def _n(sql, args=()):
    try:
        return get_db().execute(sql, args).fetchone()["n"]
    except Exception:
        get_db().rollback()
        return 0


def _ago(iso):
    from .status import _age, _ago as ago
    return ago(_age(iso)) if iso else "never"


def build(user):
    from . import hospitals as hp, interesting, status
    from .update_check import read_state
    from . import build_info
    st = status.run(True, sources=False)
    checks = {c["key"]: c for c in st["checks"]}
    attention = []

    def flag(level, text, link, label="Open"):
        attention.append({"level": level, "text": text, "link": link, "label": label})

    for c in st["checks"]:
        if c["group"] == "services" and c["state"] in ("down", "warn"):
            flag(c["state"], f"{c['name']}: {c['summary'] or c['detail']}", "/admin/status", "Status")
    src = status.sources_summary()
    if src and (src["down"] or src["warn"]):
        flag("warn", f"{src['down'] + src['warn']} outside data source{'s' if src['down'] + src['warn'] != 1 else ''} not answering properly.", "/admin/status", "Status")
    upd = read_state()
    if upd.get("update_available"):
        flag("info", f"A new portal release is available ({upd.get('latest_stable_version')}, running {upd.get('current_version')}).", "/admin/updates", "Updates")
    if len(os.environ.get("SECRET_KEY", "")) < 32:
        flag("warn", "The SECRET_KEY in the server's .env is shorter than 32 characters. It protects every sealed secret and every sign-in; use a long random one (for example the output of openssl rand -hex 32; changing it signs everyone out and makes the sealed secrets unreadable, so plan it).", "/admin/updates", "How")
    if cfg("backup_encrypt") != "1":
        flag("warn", "Backups are not encrypted (Settings > Backups > Encrypt Backups). A backup holds every table, including password hashes; turn encryption on.", "/admin/backups", "Backups")
    if not os.environ.get("SERVICE_TOKEN"):
        flag("info", "The stack's services (mesh, ACARS, APRS, predictions, Matrix helper) answer any request on their internal network. Set SERVICE_TOKEN in the server's .env (openssl rand -hex 32) and restart the stack to make them require it.", "/admin/updates", "How")
    if os.environ.get("ADMIN_PASSWORD"):
        flag("warn", "The first administrator's password is still in the server's .env file; remove that line.", "/admin/updates", "How")
    chk = hp.check_counts()
    issues = hp.issue_counts()
    if chk["review"]:
        flag("info", f"{chk['review']} hospital positions need a look (the finders disagreed or only one knew the address).", "/admin/hospitals?check=review", "Review")
    if issues["any"]:
        flag("info", f"{issues['any']} hospitals have missing or doubtful information.", "/admin/hospitals?issue=any", "Review")
    locked = _n("SELECT COUNT(*) AS n FROM users WHERE locked_until > now()")
    if locked:
        flag("warn", f"{locked} account{'s are' if locked != 1 else ' is'} locked after failed sign-ins.", "/admin/users", "Users")
    fails = _n("SELECT COUNT(*) AS n FROM login_events WHERE NOT success AND ts > now() - interval '24 hours'")
    if fails >= 10:
        flag("warn", f"{fails} failed sign-ins in the last 24 hours.", "/admin/signins", "Sign-Ins")
    orphans = _n("SELECT COUNT(*) AS n FROM feeders WHERE owner_id IS NULL AND NOT banned AND online")
    if orphans:
        flag("info", f"{orphans} station{'s are' if orphans != 1 else ' is'} online with no owner yet.", "/admin/feeders", "Feeders")

    from . import services
    hub = checks.get("hub", {})
    fed = checks.get("feeders", {})
    tiles = [
        ("Aircraft now", _tile_value(hub.get("summary", ""), "Tracking "), "/live", hub.get("state")),
        ("Stations online", fed.get("summary", "").split(" stations")[0].replace(" of ", " / ") or "-", "/admin/feeders", fed.get("state")),
        ("Flights recorded", f"{_n('SELECT COUNT(*) AS n FROM flight_log'):,}", "/history/", checks.get("history", {}).get("state")),
        ("Accounts", f"{_n('SELECT COUNT(*) AS n FROM users'):,}", "/admin/users", None),
        ("New accounts (7 days)", str(_n("SELECT COUNT(*) AS n FROM users WHERE created_at > now() - interval '7 days'")), "/admin/users", None),
        ("People signed in (24 h)", str(_n("SELECT COUNT(DISTINCT username) AS n FROM login_events WHERE success AND ts > now() - interval '24 hours'")), "/admin/signins", None),
    ]
    data = [
        ("Hospitals", f"{_n('SELECT COUNT(*) AS n FROM hospitals'):,}", f"{issues['any']} flagged, {chk['review']} to review, {chk['agreed']} confirmed", "/admin/hospitals", get_setting("hospitals_last_import")),
        ("Airports", f"{_n('SELECT COUNT(*) AS n FROM airports'):,}", "", "/admin/airports", get_setting("airports_last_import")),
        ("Places", f"{_n('SELECT COUNT(*) AS n FROM places'):,}" if _has_table("places") else "-", "", "/admin/places", get_setting("places_last_import")),
        ("Air ambulances", f"{interesting_counts('hems_aircraft'):,}", "", "/admin/air-ambulance", get_setting("hems_last_import")),
        ("Interesting aircraft", f"{interesting.counts()['total']:,}", f"{interesting.counts()['yours']} added or imported by you", "/admin/interesting", get_setting("interesting_last_import")),
    ]
    data += services.collect("admin_data")   # every other service names what it keeps (app/services.py)
    data = [{"name": n, "count": c, "note": note, "link": link, "when": _ago(w) if w else "never"} for n, c, note, link, w in data]
    system = []
    for key in ["site"] + [x["status_key"] for x in services.enabled() if x.get("status_key")] + ["photon", "planefence", "stats", "prometheus", "stack", "backup", "disk", "updates", "dataupdates"]:   # each service names its own Service Status check
        if key in checks and checks[key]["state"] != "off":
            c = checks[key]
            system.append({"name": c["name"], "state": c["state"], "word": c["word"], "text": c["summary"] or c["detail"]})
    version = (build_info.read() or {}).get("version") or "unknown"
    activity = get_db().execute("SELECT ts, actor_name, action, target FROM audit_log ORDER BY ts DESC LIMIT 12").fetchall()
    # what each service adds: its numbers (tiles) and what needs attention (app/services.py), each placed after the aircraft numbers
    tiles[4:4] = [(a, b, c, (checks.get(d) or {}).get("state") if d else None) for a, b, c, d in services.collect("admin_tiles")]
    for level, text, link, label in services.collect("admin_attention"):
        flag(level, text, link, label)
    from .adminnav import can_open   # each person sees only what they may open (a weather administrator is not shown the hospital list)
    attention = [a for a in attention if can_open(user, a["link"])]
    tiles = [t for t in tiles if can_open(user, t[2])]
    data = [x for x in data if can_open(user, x["link"])]
    if not user.has_permission("manage_settings"):
        system = []
    if not user.has_permission("view_history"):
        activity = []
    return {"overall": st["overall"], "message": st["message"], "attention": attention, "tiles": tiles, "data": data, "system": system, "version": version,
            "activity": activity, "show_activity": user.has_permission("view_history"), "show_system": user.has_permission("manage_settings"), "accounts": _n("SELECT COUNT(*) AS n FROM users"), "roles": _n("SELECT COUNT(*) AS n FROM roles")}


def _tile_value(summary, prefix):
    s = summary[len(prefix):] if summary.startswith(prefix) else ""
    return s.split(" ")[0] if s else "-"


def _has_table(name):
    return _n("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s", (name,)) > 0


def interesting_counts(table):
    return _n(f"SELECT COUNT(*) AS n FROM {table} WHERE NOT excluded")
