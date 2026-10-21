"""Off-site backups: a copy of the whole hub's data somewhere other than the hub VM.

Two kinds of copy, each its own encrypted file on its own schedule:
  small  the newest portal database backup (every table: flights, history, stations, alerts, settings...), the hub's .env and compose files (they hold the
         key that decrypts the portal's own backups) and Grafana's data. Daily by default.
  big    the data that is large and slow-changing: Prometheus statistics and the hub's own history (traces, heatmap, replay). Weekly by default.
Everything is set here, in Admin > Backups > Off-Site Copy, and done by the `adsb-backup` service (deploy/backup), which reads this configuration and the
secrets from the portal over the internal network, runs what is due, and reports back. Nothing is edited by hand on the hub.
"""
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, jsonify, request

from . import backup as bk
from . import csrf
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .db import get_db
from .settings import cfg, get_secret, get_setting, set_secret, set_setting

bp = Blueprint("offsite", __name__)

TYPES = {"sftp": "SFTP (any server with SSH)", "s3": "S3-compatible bucket", "smb": "SMB / Windows share (NAS)", "webdav": "WebDAV (Nextcloud and others)"}
CONFIG_KEY = "offsite_config"
COMMAND_KEY = "offsite_command"
TEST_KEY = "offsite_test"
DEFAULTS = {
    "enabled": False, "type": "sftp", "host": "", "port": "", "user": "", "path": "adsb-backups", "endpoint": "", "region": "", "bucket": "", "access_key": "",
    "small": {"time": "03:30", "every_days": 1, "keep": 14},
    "big": {"time": "04:30", "every_days": 7, "keep": 4, "globe": True, "prometheus": True},
}
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.:\-/]{0,250})$")
USER_RE = re.compile(r"^[A-Za-z0-9._@\\-]{1,80}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def load():
    try:
        saved = json.loads(get_setting(CONFIG_KEY) or "{}")
    except ValueError:
        saved = {}
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        elif k in out:
            out[k] = v
    return out


def configured(c=None):
    c = c or load()
    ok = bool(c["host"] or c["endpoint"]) and bool(get_secret("offsite_pass"))
    if c["type"] == "s3":
        ok = ok and bool(c["bucket"] and c["access_key"] and get_secret("offsite_secret"))
    else:
        ok = ok and bool(c["user"]) and bool(get_secret("offsite_secret") or get_secret("offsite_key"))
    return ok


def _zone():
    try:
        return ZoneInfo(cfg("timezone"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _int(v, lo, hi, label):
    try:
        n = int(str(v).strip())
    except ValueError:
        raise ValueError(f"{label} must be a number.")
    if not lo <= n <= hi:
        raise ValueError(f"{label} must be from {lo} to {hi}.")
    return n


def save(form):
    """Validate the Off-Site Copy form. Returns an error message or None. Blank secret fields keep what is saved."""
    c = load()
    try:
        t = form.get("type", "sftp")
        if t not in TYPES:
            raise ValueError("Choose a destination type.")
        c["type"] = t
        c["enabled"] = bool(form.get("enabled"))
        host = form.get("host", "").strip()
        if host and not HOST_RE.match(host):
            raise ValueError("Host must be a host name, address or (for WebDAV) a full web address.")
        c["host"] = host
        port = form.get("port", "").strip()
        if port and not (port.isdigit() and 1 <= int(port) <= 65535):
            raise ValueError("Port must be a number from 1 to 65535.")
        c["port"] = port
        user = form.get("user", "").strip()
        if user and not USER_RE.match(user):
            raise ValueError("User name may only contain letters, digits and . _ @ - \\")
        c["user"] = user
        path = form.get("path", "").strip().strip("/")
        if ".." in path.split("/") or "\\" in path or len(path) > 200 or "\x00" in path:
            raise ValueError("Folder must be a plain path without '..'.")
        c["path"] = path
        c["endpoint"] = form.get("endpoint", "").strip()[:200]
        c["region"] = form.get("region", "").strip()[:60]
        c["bucket"] = form.get("bucket", "").strip()[:100]
        c["access_key"] = form.get("access_key", "").strip()[:200]
        for kind in ("small", "big"):
            tm = form.get(f"{kind}_time", "").strip()
            if not TIME_RE.match(tm):
                raise ValueError(f"{kind.title()} backup time must be HH:MM, for example 03:30.")
            c[kind]["time"] = tm
            c[kind]["every_days"] = _int(form.get(f"{kind}_every_days"), 1, 365, "Days between copies")
            c[kind]["keep"] = _int(form.get(f"{kind}_keep"), 1, 365, "Copies to keep")
        c["big"]["globe"] = bool(form.get("big_globe"))
        c["big"]["prometheus"] = bool(form.get("big_prometheus"))
        if c["enabled"] and not (host or c["endpoint"]):
            raise ValueError("Enter the destination host before switching the copy on.")
    except ValueError as e:
        return str(e)
    set_setting(CONFIG_KEY, json.dumps(c))
    for field, key in (("secret", "offsite_secret"), ("key", "offsite_key"), ("passphrase", "offsite_pass")):
        v = form.get(field, "")
        if v.strip() or (field == "key" and v):
            set_secret(key, v if field == "key" else v.strip())
    if form.get("clear_key"):
        set_secret("offsite_key", "")
    return None


# ---------------------------------------------------------------- what is due

def _local(dt):
    return dt.astimezone(_zone())


def runs(limit=12):
    return get_db().execute("SELECT * FROM offsite_runs ORDER BY started DESC LIMIT %s", (limit,)).fetchall()


def _last_success(kind):
    row = get_db().execute("SELECT max(finished) AS t FROM offsite_runs WHERE kind=%s AND ok", (kind,)).fetchone()
    return row["t"]


def _busy(kind, now):
    d = get_db()
    if d.execute("SELECT 1 FROM offsite_runs WHERE kind=%s AND ok IS NULL AND started > %s", (kind, now - timedelta(hours=8))).fetchone():
        return True
    bad = d.execute("SELECT 1 FROM offsite_runs WHERE kind=%s AND ok = FALSE AND finished > %s", (kind, now - timedelta(minutes=30))).fetchone()
    return bool(bad)


def next_slot(kind, c=None, now=None):
    c = c or load()
    now = now or datetime.now(timezone.utc)
    k = c[kind]
    hh, mm = (int(x) for x in k["time"].split(":"))
    last = _last_success(kind)
    base = _local(last).date() + timedelta(days=int(k["every_days"])) if last else _local(now).date()
    return datetime(base.year, base.month, base.day, hh, mm, tzinfo=_zone())


def due(now=None):
    now = now or datetime.now(timezone.utc)
    c = load()
    if not c["enabled"] or not configured(c):
        return []
    out = []
    for kind in ("small", "big"):
        if kind == "big" and not (c["big"]["globe"] or c["big"]["prometheus"]):
            continue
        if now >= next_slot(kind, c, now).astimezone(timezone.utc) and not _busy(kind, now):
            out.append(kind)
    return out


def command():
    try:
        cmd = json.loads(get_setting(COMMAND_KEY) or "null")
    except ValueError:
        return None
    if not cmd:
        return None
    try:
        if datetime.now(timezone.utc) - datetime.fromisoformat(cmd["at"]) > timedelta(minutes=30):
            return None   # an old request nobody picked up
    except (KeyError, ValueError):
        return None
    return cmd


def context():
    c = load()
    now = datetime.now(timezone.utc)
    return {"offsite": {
        "cfg": c, "types": TYPES, "has_secret": bool(get_secret("offsite_secret")), "has_key": bool(get_secret("offsite_key")), "has_pass": bool(get_secret("offsite_pass")),
        "ready": configured(c), "runs": runs(), "command": command(), "test": _test_result(),
        "next": {k: next_slot(k, c, now) for k in ("small", "big")},
    }}


def _test_result():
    try:
        return json.loads(get_setting(TEST_KEY) or "null")
    except ValueError:
        return None


# ---------------------------------------------------------------- the service's API (internal network only)

def _internal():
    token = os.environ.get("STACK_TOKEN", "")
    got = request.headers.get("Authorization", "")
    # the portal is also reachable through the public proxy, which always adds a forwarded address: internal calls never carry one
    if request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP") or request.headers.get("CF-Connecting-IP"):
        return False
    return bool(token) and hmac.compare_digest(got, "Bearer " + token)


def _guard():
    if not _internal():
        return jsonify({"error": "not found"}), 404
    return None


@bp.route("/internal/backup/config")
@csrf.exempt
def api_config():
    g = _guard()
    if g:
        return g
    c = load()
    return jsonify({"config": c, "ready": configured(c), "due": due(), "command": command(), "now": datetime.now(timezone.utc).isoformat(),
                    "secrets": {"secret": get_secret("offsite_secret"), "key": get_secret("offsite_key"), "passphrase": get_secret("offsite_pass")}})


@bp.route("/internal/backup/start", methods=["POST"])
@csrf.exempt
def api_start():
    g = _guard()
    if g:
        return g
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    if kind not in ("small", "big"):
        return jsonify({"error": "kind"}), 400
    trigger = "manual" if body.get("manual") else "schedule"
    d = get_db()
    db_backup = None
    if kind == "small":   # a fresh snapshot of the portal's database right now, so the copy is as current as the schedule says
        try:
            db_backup, _ = bk.create_backup(current_app.config["DATABASE_URL"], "scheduled")
            bk.apply_retention()
        except Exception as e:
            return jsonify({"error": f"could not create the database backup: {e}"}), 500
    rid = d.execute("INSERT INTO offsite_runs (kind, trigger) VALUES (%s,%s) RETURNING id", (kind, trigger)).fetchone()["id"]
    d.commit()
    cmd = command()
    if cmd and cmd.get("action") == f"run-{kind}":
        set_setting(COMMAND_KEY, "")
    return jsonify({"run_id": rid, "db_backup": db_backup})


@bp.route("/internal/backup/report", methods=["POST"])
@csrf.exempt
def api_report():
    g = _guard()
    if g:
        return g
    b = request.get_json(silent=True) or {}
    d = get_db()
    d.execute("UPDATE offsite_runs SET finished=now(), ok=%s, bytes=%s, detail=%s, remote_name=%s WHERE id=%s",
              (bool(b.get("ok")), int(b.get("bytes") or 0), str(b.get("detail") or "")[:1500], str(b.get("remote_name") or "")[:200], int(b.get("run_id") or 0)))
    d.commit()
    return jsonify({"ok": True})


@bp.route("/internal/backup/test-result", methods=["POST"])
@csrf.exempt
def api_test_result():
    g = _guard()
    if g:
        return g
    b = request.get_json(silent=True) or {}
    set_setting(TEST_KEY, json.dumps({"ok": bool(b.get("ok")), "detail": str(b.get("detail") or "")[:1500], "at": datetime.now(timezone.utc).isoformat()}))
    cmd = command()
    if cmd and cmd.get("action") == "test":
        set_setting(COMMAND_KEY, "")
    return jsonify({"ok": True})


# ---------------------------------------------------------------- administrators

@bp.route("/admin/backups/offsite/save", methods=["POST"])
@permission_required("manage_settings")
def admin_save():
    err = save(request.form)
    if err:
        return action_response(False, err, redirect_endpoint="admin.backups")
    audit("backup.offsite.save", detail=f"{load()['type']} {'on' if load()['enabled'] else 'off'}")
    return action_response(True, "Off-site copy settings saved.", redirect_endpoint="admin.backups")


@bp.route("/admin/backups/offsite/command", methods=["POST"])
@permission_required("manage_settings")
def admin_command():
    action = request.form.get("action", "")
    if action not in ("test", "run-small", "run-big"):
        return action_response(False, "Unknown action.", redirect_endpoint="admin.backups")
    if not configured():
        return action_response(False, "Save the destination, its login and the passphrase first.", redirect_endpoint="admin.backups")
    set_setting(COMMAND_KEY, json.dumps({"action": action, "at": datetime.now(timezone.utc).isoformat()}))
    if action == "test":
        set_setting(TEST_KEY, "")
    audit("backup.offsite." + action)
    return action_response(True, "Asked the backup service to " + {"test": "test the connection", "run-small": "make the small copy", "run-big": "make the big copy"}[action]
                           + ". It picks this up within a minute; reload to see the result.", redirect_endpoint="admin.backups")
