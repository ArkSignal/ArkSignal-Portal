"""Remote administration of mesh nodes through the site, without the node's admin key ever reaching the site.

A mesh community's Manager makes an Admin Bridge for the community (a name and a token shown once). A small program (tools/admin_bridge/arksignal_admin_bridge.py) runs on the group's own computer next to a
gateway node (Wi-Fi or USB), reaches out to the site over https with that token, takes the commands that are ready for it and returns what happened. The admin key stays on the group's side.
Who may do what is the community's: a Node Operator or above queues a command for one of the community's own nodes; the commands that change something (rename, role, channel name, reboot) wait for a Manager or
the Owner to approve them, and "read the configuration" goes straight through. Only the commands in COMMANDS exist, with strict parameters, so the bridge never runs anything else, and every request, approval,
rejection and result is audit-logged and kept in the community's list (Settings > Mesh Nodes: Communities, how long). A command that waits too long expires. The site also checks the Meshtastic firmware and
protobuf releases once a day (see meshrelease.py) so the administrators hear when the project publishes something new."""
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, flash, jsonify, request
from flask_login import current_user, login_required

from . import csrf, vault
from . import mesh as ms
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("meshbridge", __name__)

ROLE_ENUM = ["CLIENT", "CLIENT_MUTE", "ROUTER", "ROUTER_CLIENT", "REPEATER", "TRACKER", "SENSOR", "TAK", "CLIENT_HIDDEN", "LOST_AND_FOUND", "TAK_TRACKER", "ROUTER_LATE", "CLIENT_BASE"]
# kind: (what it does, needs approval, parameters [(name, label, kind, limit)])
COMMANDS = {
    "info": ("Read the node's configuration", False, []),
    "set_owner": ("Rename the node", True, [("long_name", "Long name", "text", 39), ("short_name", "Short name", "text", 4)]),
    "set_role": ("Set the node's role", True, [("role", "Role", "enum", ROLE_ENUM)]),
    "set_channel_name": ("Rename a channel", True, [("index", "Channel number (0 to 7)", "int", (0, 7)), ("name", "Channel name", "text", 11)]),
    "reboot": ("Reboot the node", True, [("seconds", "Seconds before it restarts", "int", (5, 600))]),
}
NAME_RE = re.compile(r"^[A-Za-z0-9 ._:+()#@/-]{1,40}$")
_polls = {}   # bridge id -> (minute, count): a small per-worker limit


def _tell(c_slug, what):
    audit("bridge.command", target=c_slug, detail=what[:300])


def validate(kind, form):
    """(params, error) for a command asked for in a form."""
    if kind not in COMMANDS:
        return None, "Choose a command."
    params = {}
    for name, label, k, limit in COMMANDS[kind][2]:
        raw = (form.get(name) or "").strip()
        if k == "enum":
            if raw not in limit:
                return None, f"{label} must be one of the listed roles."
            params[name] = raw
        elif k == "int":
            try:
                v = int(raw)
            except ValueError:
                return None, f"{label} must be a whole number."
            if not limit[0] <= v <= limit[1]:
                return None, f"{label} must be between {limit[0]} and {limit[1]}."
            params[name] = v
        else:
            if not raw or len(raw) > limit or not NAME_RE.match(raw):
                return None, f"{label} must be 1 to {limit} characters: letters, numbers, spaces and . _ : + ( ) # @ / -"
            params[name] = raw
    return params, ""


def describe(kind, params):
    if kind == "set_owner":
        return f"rename to {params.get('long_name')} ({params.get('short_name')})"
    if kind == "set_role":
        return f"set the role to {params.get('role')}"
    if kind == "set_channel_name":
        return f"rename channel {params.get('index')} to {params.get('name')}"
    if kind == "reboot":
        return f"reboot in {params.get('seconds')} seconds"
    return COMMANDS.get(kind, ("", 0, 0))[0].lower()


# ------------------------------------------------------------------ the community's side (called from communities.py)

def page_context(c):
    d = get_db()
    d.execute("UPDATE mesh_commands SET state='expired', finished_at=now() WHERE community_id=%s AND state IN ('pending','approved') AND requested_at < now() - make_interval(hours => %s)", (c["id"], cfg_int("mesh_bridge_cmd_expire_hours")))
    d.execute("DELETE FROM mesh_commands WHERE community_id=%s AND requested_at < now() - make_interval(days => %s)", (c["id"], cfg_int("mesh_bridge_cmd_keep_days")))
    d.commit()
    bridges = d.execute("SELECT * FROM mesh_bridges WHERE community_id=%s ORDER BY id", (c["id"],)).fetchall()
    cmds = d.execute("SELECT m.*, b.name AS bridge_name, u.username AS who, a.username AS approver FROM mesh_commands m JOIN mesh_bridges b ON b.id = m.bridge_id LEFT JOIN users u ON u.id = m.requested_by LEFT JOIN users a ON a.id = m.decided_by "
                     "WHERE m.community_id=%s ORDER BY m.id DESC LIMIT 30", (c["id"],)).fetchall()
    out = []
    for m in cmds:
        p = json.loads(m["params"] or "{}")
        out.append(dict(m, text=describe(m["kind"], p), label=COMMANDS.get(m["kind"], ("", 0, 0))[0]))
    return {"bridges": bridges, "cmds": out, "commands": COMMANDS, "role_enum": ROLE_ENUM, "poll_url": request.host_url.rstrip("/") + "/api/mesh/bridge/poll",
            "expire_hours": cfg_int("mesh_bridge_cmd_expire_hours")}


def _ctx(slug, perm):
    from . import communities as cm
    c, rank = cm._ctx(slug, perm)
    return c, rank, cm


@bp.route("/mesh/c/<slug>/bridges/add", methods=["POST"])
@login_required
def bridge_add(slug):
    c, _r, cm = _ctx(slug, "manage_bridges")
    name = cm._clean(request.form.get("name"), 60)
    if not name:
        return cm._back(slug, False, "Give the bridge a name, for example the place it runs.")
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM mesh_bridges WHERE community_id=%s", (c["id"],)).fetchone()["n"] >= cfg_int("mesh_bridge_max"):
        return cm._back(slug, False, f"A community can have {cfg_int('mesh_bridge_max')} bridges. Remove one first.")
    token = secrets.token_urlsafe(32)
    d.execute("INSERT INTO mesh_bridges (community_id, name, token_hash, created_by) VALUES (%s,%s,%s,%s)", (c["id"], name, vault.keyed_hash(token, "mbridge"), current_user.id))
    d.commit()
    _tell(slug, f"bridge made: {name}")
    flash(f"Token for the bridge {name} (shown once, copy it now): {token}", "success")
    return cm._back(slug, True, f"The bridge {name} is made. Give its token to the person who runs it.")


@bp.route("/mesh/c/<slug>/bridges/<int:bid>/<action>", methods=["POST"])
@login_required
def bridge_action(slug, bid, action):
    c, _r, cm = _ctx(slug, "manage_bridges")
    d = get_db()
    row = d.execute("SELECT * FROM mesh_bridges WHERE id=%s AND community_id=%s", (bid, c["id"])).fetchone()
    if row is None:
        abort(404)
    if action == "token":
        token = secrets.token_urlsafe(32)
        d.execute("UPDATE mesh_bridges SET token_hash=%s WHERE id=%s", (vault.keyed_hash(token, "mbridge"), bid))
        flash(f"New token for the bridge {row['name']} (shown once, copy it now): {token}", "success")
        msg = "A new token is made; the old one stops working."
    elif action == "delete":
        d.execute("DELETE FROM mesh_bridges WHERE id=%s", (bid,))
        msg = "The bridge is removed with its commands."
    else:
        abort(404)
    d.commit()
    _tell(slug, f"bridge {action}: {row['name']}")
    return cm._back(slug, True, msg)


@bp.route("/mesh/c/<slug>/commands/add", methods=["POST"])
@login_required
def command_add(slug):
    c, rank, cm = _ctx(slug, "queue_commands")
    d = get_db()
    kind = request.form.get("kind", "")
    params, err = validate(kind, request.form)
    if err:
        return cm._back(slug, False, err)
    bridge = d.execute("SELECT * FROM mesh_bridges WHERE id=%s AND community_id=%s", (request.form.get("bridge", type=int), c["id"])).fetchone()
    if bridge is None:
        return cm._back(slug, False, "Choose one of the community's bridges.")
    key = ms.claim_key(request.form.get("node", ""))
    if key is None or key.startswith("mc:"):
        return cm._back(slug, False, "Choose a Meshtastic node of the community (an ID of eight hexadecimal characters, for example !a1b2c3d4).")
    mine = {cm._node_key(n) for n in cm.community_nodes(c)}
    if key not in mine:
        return cm._back(slug, False, "That node is not one of this community's nodes. Add it under Choose the Nodes first.")
    risky = COMMANDS[kind][1]
    state = "approved" if (not risky or cm.can(rank, "approve_commands")) else "pending"   # a Manager's own request needs no second person
    d.execute("INSERT INTO mesh_commands (community_id, bridge_id, node_key, kind, params, risky, state, requested_by, decided_by, decided_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (c["id"], bridge["id"], key, kind, json.dumps(params), risky, state, current_user.id, current_user.id if risky and state == "approved" else None, datetime.now(timezone.utc) if risky and state == "approved" else None))
    d.commit()
    _tell(slug, f"queued for {key}: {describe(kind, params)} ({state})")
    return cm._back(slug, True, "The command is queued." if state == "approved" else "The command waits for a Manager or the Owner to approve it.")


def _command(slug, cid, perm):
    c, rank, cm = _ctx(slug, perm)
    m = get_db().execute("SELECT * FROM mesh_commands WHERE id=%s AND community_id=%s", (cid, c["id"])).fetchone()
    if m is None:
        abort(404)
    return c, rank, cm, m


@bp.route("/mesh/c/<slug>/commands/<int:cid>/<action>", methods=["POST"])
@login_required
def command_action(slug, cid, action):
    if action in ("approve", "reject"):
        c, rank, cm, m = _command(slug, cid, "approve_commands")
        if m["state"] != "pending":
            return cm._back(slug, False, "That command is not waiting for approval.")
        if m["requested_by"] == current_user.id and not cm.is_site_admin() and rank < 5:
            return cm._back(slug, False, "Someone else has to approve a command you asked for.")
    elif action == "cancel":
        c, rank, cm, m = _command(slug, cid, "queue_commands")
        if not (m["requested_by"] == current_user.id or cm.can(rank, "approve_commands")):
            abort(403)
        if m["state"] not in ("pending", "approved"):
            return cm._back(slug, False, "That command has already gone to the bridge or finished.")
    else:
        abort(404)
    d = get_db()
    new = {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[action]
    d.execute("UPDATE mesh_commands SET state=%s, decided_by=%s, decided_at=now(), finished_at=CASE WHEN %s='approved' THEN NULL ELSE now() END WHERE id=%s AND state IN ('pending','approved')", (new, current_user.id, new, cid))
    d.commit()
    _tell(slug, f"{action} #{cid}: {describe(m['kind'], json.loads(m['params'] or '{}'))} for {m['node_key']}")
    return cm._back(slug, True, {"approve": "Approved: the bridge will take it next time it asks.", "reject": "Rejected.", "cancel": "Cancelled."}[action])


# ------------------------------------------------------------------ the bridge's side (a bearer token, no session)

def _bridge():
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return None
    row = get_db().execute("SELECT b.*, c.slug FROM mesh_bridges b JOIN communities c ON c.id = b.community_id WHERE b.token_hash=%s", (vault.keyed_hash(h[7:].strip(), "mbridge"),)).fetchone()
    if row is None or cfg("mesh_bridge_enabled") != "1" or not ms.enabled():
        return None
    minute = int(time.time() // 60)
    last = _polls.get(row["id"])
    n = (last[1] + 1) if last and last[0] == minute else 1
    _polls[row["id"]] = (minute, n)
    if n > cfg_int("mesh_bridge_polls_per_minute"):
        return "slow"
    d = get_db()
    d.execute("UPDATE mesh_bridges SET last_seen=now(), version=%s WHERE id=%s", ((request.headers.get("X-Bridge-Version") or "")[:30], row["id"]))
    d.commit()
    return row


@bp.route("/api/mesh/bridge/poll", methods=["POST"])
@csrf.exempt
def poll():
    b = _bridge()
    if b is None:
        return jsonify({"error": "not allowed"}), 401
    if b == "slow":
        return jsonify({"error": "too many requests"}), 429
    d = get_db()
    d.execute("UPDATE mesh_commands SET state='expired', finished_at=now() WHERE bridge_id=%s AND state IN ('pending','approved') AND requested_at < now() - make_interval(hours => %s)", (b["id"], cfg_int("mesh_bridge_cmd_expire_hours")))
    rows = d.execute("SELECT id, node_key, kind, params FROM mesh_commands WHERE bridge_id=%s AND state='approved' ORDER BY id LIMIT %s", (b["id"], cfg_int("mesh_bridge_poll_max"))).fetchall()
    if rows:
        d.execute("UPDATE mesh_commands SET state='sent', sent_at=now() WHERE id = ANY(%s)", ([r["id"] for r in rows],))
    d.commit()
    return jsonify({"commands": [{"id": r["id"], "node": r["node_key"], "kind": r["kind"], "params": json.loads(r["params"] or "{}")} for r in rows], "poll_seconds": cfg_int("mesh_bridge_poll_seconds")})


@bp.route("/api/mesh/bridge/result", methods=["POST"])
@csrf.exempt
def result():
    b = _bridge()
    if b is None:
        return jsonify({"error": "not allowed"}), 401
    if b == "slow":
        return jsonify({"error": "too many requests"}), 429
    j = request.get_json(silent=True) or {}
    d = get_db()
    row = d.execute("SELECT * FROM mesh_commands WHERE id=%s AND bridge_id=%s AND state='sent'", (j.get("id"), b["id"])).fetchone() if isinstance(j.get("id"), int) else None
    if row is None:
        return jsonify({"error": "no such command waiting for a result"}), 404
    ok = bool(j.get("ok"))
    out = str(j.get("output") or "")[:4000]
    d.execute("UPDATE mesh_commands SET state=%s, result=%s, finished_at=now() WHERE id=%s", ("done" if ok else "failed", out, row["id"]))
    d.commit()
    audit("bridge.result", target=b["slug"], detail=f"#{row['id']} {row['kind']} for {row['node_key']}: {'done' if ok else 'failed'}", actor_name="bridge " + b["name"])
    return jsonify({"ok": True})
