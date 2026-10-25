"""Radio bridges: a chat conversation can reach, and hear from, an amateur radio operator on APRS and a node on the Meshtastic mesh.

How it works. A member opts in (My Account > Radio Bridges), then links one of their own conversations to a remote end: an APRS call sign (with its SSID, for example W5ABC-9) or a mesh node (!a1b2c3d4).
* Out: what the member who made the link writes in that conversation is sent as an APRS message packet from the member's own call sign (the one on their profile) to the remote call sign, through APRS-IS, logged in
  with the member's own call sign and its public passcode, the way any APRS messaging web client does it. What other people in the conversation write is never forwarded, because only a licensed operator may
  transmit under a call sign. Messages are plain ASCII text of at most 67 characters (the APRS limit); nothing is encrypted, translated or obscured, which amateur rules forbid. For the mesh the text is queued in
  `mesh_outbox` for the mesh gateway to pick up (see below).
* In: a background listener (one web worker at a time, by a database lock) stays connected to APRS-IS with a read-only login and a message filter for the call signs of members who linked a conversation; an APRS
  message addressed to the member's call sign from the linked remote call sign is added to the conversation, shown with the remote call sign and "via APRS". A repeat of the same message number is dropped. Mesh
  text arrives through `POST /bridge/mesh/inbound`, which the mesh gateway calls with the shared token set in Settings.
Safety: everything is off until an administrator switches it on in Settings > Radio Bridges and each member opts in with an acknowledgement; a member needs a valid call sign on their profile for APRS; at most a few
links per member; an hourly limit on messages out; every opt-in, link, switch, removal, message sent and message received is in the audit log (never the words). The mesh gateway side (publishing into the mesh and
reading the nodes' text) is not built yet: the portal side is, and tested, so the gateway only has to call the two addresses below."""
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from . import csrf
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("bridge", __name__)

LOCK_ID = 727022
HAM_RE = re.compile(r"^[A-Z0-9]{1,2}[0-9][A-Z0-9]{1,4}(-([0-9]|1[0-5]))?$")
NODE_RE = re.compile(r"^!?([0-9a-fA-F]{8})$")
KINDS = {"aprs": "APRS (amateur radio messages)", "mesh": "Meshtastic mesh"}
MSG_RE = re.compile(r"^([A-Z0-9]{1,2}[0-9][A-Z0-9]{0,5}(?:-[0-9A-Z]{1,2})?)>[^:]*::([A-Z0-9 -]{9}):(.*)$")
_state = {"listening": 0.0, "last": ""}


def enabled(kind):
    return cfg("bridge_" + kind + "_enabled") == "1"


def _norm_call(text):
    t = re.sub(r"[^A-Za-z0-9-]", "", text or "").upper()
    return t[:-2] if t.endswith("-0") else t


def my_call(user_id):
    r = get_db().execute("SELECT callsign FROM users WHERE id=%s", (user_id,)).fetchone()
    c = _norm_call(r["callsign"] if r else "")
    return c if HAM_RE.match(c) else ""


def opted_in(user_id, kind):
    r = get_db().execute("SELECT bridge_aprs_ok, bridge_mesh_ok FROM users WHERE id=%s", (user_id,)).fetchone()
    return bool(r and r["bridge_" + kind + "_ok"])


def _log(link_id, direction, ok, detail):
    d = get_db()
    d.execute("INSERT INTO bridge_log (link_id, direction, ok, detail) VALUES (%s,%s,%s,%s)", (link_id, direction, ok, detail[:200]))
    d.execute("UPDATE bridge_links SET " + ("last_out" if direction == "out" else "last_in") + "=now(), last_result=%s WHERE id=%s", (("ok" if ok else "failed") + ": " + detail[:120], link_id))
    d.commit()


def passcode(call):
    from .aprspush import passcode as p
    return p(call)


def clean_aprs_text(body):
    """(text, problem): APRS carries plain printable ASCII, at most the setting (67 by the APRS standard); braces, pipes and tildes are not allowed in a message."""
    t = " ".join((body or "").split())
    if not t:
        return None, "Nothing to send."
    if any(ord(c) < 32 or ord(c) > 126 for c in t) or any(c in "{|~" for c in t):
        return None, "APRS carries plain ASCII text only (no emoji, accents, braces, pipes or tildes)."
    if len(t) > cfg_int("bridge_aprs_max_text"):
        return None, f"APRS messages can be {cfg_int('bridge_aprs_max_text')} characters at most."
    return t, None


def _server():
    host, _sep, port = cfg("bridge_aprs_server").rpartition(":")
    return host or "rotate.aprs2.net", int(port) if port.isdigit() else 14580


def build_packet(call, remote, text, msgno):
    return f"{call}>APARKS,TCPIP*::{remote:<9}:{text}{{{msgno}"


def _send_aprs(app, link_id, call, remote, text, msgno):
    """Log in with the member's own call sign and send one message packet. Runs in a thread; the result goes to the link's log and the audit log."""
    with app.app_context():
        host, port = _server()
        wait = cfg_int("bridge_timeout_seconds")
    code = passcode(call)
    ok, why = False, ""
    try:
        with socket.create_connection((host, port), timeout=wait) as s:
            s.settimeout(wait)
            f = s.makefile("rwb", buffering=0)
            f.readline()
            f.write(f"user {call} pass {code} vers ArkSignal 1.0\r\n".encode())
            reply = f.readline().decode("utf-8", "replace").strip()
            if "unverified" in reply.lower() or "verified" not in reply.lower():
                why = "the server did not accept the call sign"
            else:
                f.write((build_packet(call, remote, text, msgno) + "\r\n").encode())
                time.sleep(0.4)
                ok, why = True, f"sent to {remote} through {host}"
    except (OSError, ValueError) as e:
        why = f"could not reach {host}:{port} ({type(e).__name__})"
    with app.app_context():
        _log(link_id, "out", ok, why)
        audit("bridge.send", target=remote, detail=f"APRS message from {call}: {'sent' if ok else why}", actor_name=call)


def forward(conv_id, sender_id, body):
    """Called after a chat message is stored: send it out through the conversation's links when it was written by the member who made the link. Returns a short note for the sender when something was not
    sent (so the chat can say so), or ''."""
    links = get_db().execute("SELECT * FROM bridge_links WHERE conv_id=%s AND enabled AND created_by=%s", (conv_id, sender_id)).fetchall()
    return _forward(links, sender_id, body)


def forward_room(room_id, sender_id, body):
    """The same for a Matrix room: a text message the member who made the link wrote in the room goes out over the link."""
    links = get_db().execute("SELECT * FROM bridge_links WHERE room_id=%s AND enabled AND created_by=%s", (room_id, sender_id)).fetchall()
    note = _forward(links, sender_id, body)
    if note and links:
        from . import matrix
        matrix.post_to_room(room_id, note)   # the room is told why a message did not go out
    return note


def _forward(links, sender_id, body):
    d = get_db()
    notes = []
    for l in links:
        if not enabled(l["kind"]) or not opted_in(sender_id, l["kind"]):
            continue
        per_hour = cfg_int("bridge_" + l["kind"] + "_rate_per_hour")
        if d.execute("SELECT COUNT(*) AS n FROM bridge_log WHERE link_id IN (SELECT id FROM bridge_links WHERE created_by=%s AND kind=%s) AND direction='out' AND at > now() - interval '1 hour'", (sender_id, l["kind"])).fetchone()["n"] >= per_hour:
            notes.append(f"Not sent to {l['remote']}: the limit of {per_hour} messages an hour is reached.")
            continue
        if l["kind"] == "aprs":
            call = my_call(sender_id)
            if not call:
                notes.append("Not sent to APRS: your profile needs a valid call sign.")
                continue
            from .bridge_verify import verified
            if not verified(sender_id):
                notes.append("Not sent to APRS: your amateur license is not verified yet (see My Account > Radio Bridges).")
                continue
            text, why = clean_aprs_text(body)
            if why:
                notes.append(f"Not sent to {l['remote']}: {why}")
                continue
            msgno = (l["id"] * 7 + int(time.time())) % 100000
            threading.Thread(target=_send_aprs, args=(current_app._get_current_object(), l["id"], call, l["remote"], text, msgno), daemon=True).start()
        else:
            text = " ".join((body or "").split())[:cfg_int("bridge_mesh_max_text")]
            d.execute("INSERT INTO mesh_outbox (link_id, node_id, body) VALUES (%s,%s,%s)", (l["id"], l["remote"], text))
            d.commit()
            _log(l["id"], "out", True, "queued for the mesh gateway")
            audit("bridge.send", target=l["remote"], detail="mesh message queued")
    return " ".join(notes)


def deliver(link, source, text, remote_id):
    """Put a message that came in from the remote end into the conversation, written under the link maker's account but shown with the remote name. Returns True when added, False for a repeat."""
    d = get_db()
    text = " ".join((text or "").split())[:cfg_int("chat_max_length")]
    if not text:
        return False
    rid = (remote_id or "")[:60] or secrets.token_hex(6)
    if link["room_id"]:   # a Matrix room: the bridge bot says it, once per remote message number
        if d.execute("INSERT INTO bridge_seen (link_id, remote_id) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING 1", (link["id"], source + ":" + rid)).fetchone() is None:
            d.commit()
            return False
        d.execute("DELETE FROM bridge_seen WHERE at < now() - interval '2 days'")
        d.commit()
        from . import matrix
        ok = matrix.post_to_room(link["room_id"], f"{link['kind'].upper()} {source}: {text}")
        _log(link["id"], "in", ok, f"message from {source}" if ok else "the Matrix server did not take the message")
        audit("bridge.receive", target=source, detail=f"{link['kind']} message into room {link['room_name'] or link['room_id']}", actor_name="bridge")
        return ok
    if d.execute("SELECT 1 FROM conv_messages WHERE conv_id=%s AND remote_id=%s", (link["conv_id"], link["kind"] + ":" + source + ":" + rid)).fetchone():
        return False
    mid = d.execute("INSERT INTO conv_messages (conv_id, user_id, body, remote_from, remote_id) VALUES (%s,%s,%s,%s,%s) RETURNING id", (link["conv_id"], link["created_by"], __import__("app.vault", fromlist=["seal"]).seal(text), source, link["kind"] + ":" + source + ":" + rid)).fetchone()["id"]
    d.execute("UPDATE convs SET last_at=now() WHERE id=%s", (link["conv_id"],))
    d.execute("UPDATE conv_members SET hidden=FALSE WHERE conv_id=%s", (link["conv_id"],))
    d.commit()
    _log(link["id"], "in", True, f"message from {source}")
    audit("bridge.receive", target=source, detail=f"{link['kind']} message into chat #{link['conv_id']}", actor_name="bridge")
    return True


# ------------------------------------------------------------------ the APRS listener

def listen_calls():
    """The call signs (members' own) that have an enabled APRS link, for the message filter."""
    from .bridge_verify import verified
    rows = get_db().execute("SELECT DISTINCT u.id, u.callsign FROM bridge_links l JOIN users u ON u.id = l.created_by WHERE l.kind='aprs' AND l.enabled AND u.bridge_aprs_ok AND u.callsign IS NOT NULL").fetchall()
    return sorted({c for c in (_norm_call(r["callsign"]) for r in rows if verified(r["id"])) if HAM_RE.match(c)})


def handle_line(line):
    """One line from APRS-IS: when it is a message from a linked remote call sign to a member's call sign, deliver it. Returns True when something was added."""
    m = MSG_RE.match(line.strip())
    if not m:
        return False
    src, to, text = _norm_call(m.group(1)), _norm_call(m.group(2)), m.group(3)
    mid = ""
    mm = re.search(r"\{([A-Za-z0-9]{1,5})$", text)
    if mm:
        mid, text = mm.group(1), text[:mm.start()]
    if re.match(r"^(ack|rej)[A-Za-z0-9]{1,5}$", text):
        return False
    from .bridge_verify import verified
    rows = get_db().execute("SELECT l.*, u.callsign FROM bridge_links l JOIN users u ON u.id = l.created_by WHERE l.kind='aprs' AND l.enabled AND u.bridge_aprs_ok").fetchall()
    for l in rows:
        if _norm_call(l["remote"]) == src and _norm_call(l["callsign"]) == to and verified(l["created_by"]):
            return deliver(l, src, text, mid or secrets.token_hex(4))
    return False


def listen_once(app, host, port, seconds):
    """Connect read-only, ask for messages to the linked members' call signs, handle what arrives for up to `seconds`. Returns the number added."""
    with app.app_context():
        calls = listen_calls()
    if not calls:
        return 0
    added, end = 0, time.time() + seconds
    try:
        with socket.create_connection((host, port), timeout=15) as s:
            f = s.makefile("rwb", buffering=0)
            f.readline()
            with app.app_context():
                login = cfg("bridge_aprs_listen_login") or "N0CALL"
            f.write(f"user {login} pass -1 vers ArkSignal 1.0 filter g/{'/'.join(calls)}\r\n".encode())
            while time.time() < end:
                s.settimeout(max(1.0, end - time.time()))
                try:
                    line = f.readline().decode("utf-8", "replace")
                except (socket.timeout, TimeoutError):
                    break
                if not line:
                    break
                if line.startswith("#"):
                    continue
                with app.app_context():
                    if handle_line(line):
                        added += 1
    except OSError:
        pass
    return added


def start_listener(app):
    import psycopg

    def loop():
        time.sleep(90)
        while True:
            try:
                with app.app_context():
                    on = enabled("aprs")
                    host, port = _server()
                if on:
                    with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock_conn:
                        if lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
                            try:
                                listen_once(app, host, port, 240)
                            finally:
                                lock_conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
                    with app.app_context():
                        d = get_db()
                        d.execute("DELETE FROM bridge_log WHERE at < now() - make_interval(days => %s)", (cfg_int("bridge_log_keep_days"),))
                        d.execute("DELETE FROM mesh_outbox WHERE created_at < now() - interval '2 days'")
                        d.commit()
            except Exception:
                pass
            time.sleep(20)
    threading.Thread(target=loop, name="aprs-bridge", daemon=True).start()


# ------------------------------------------------------------------ the member's page

def _on():
    from . import matrix
    if not (cfg("chat_enabled") == "1" or matrix.replaces_builtin()) or not (enabled("aprs") or enabled("mesh")):
        abort(404)


def _json(ok, message, status=None):
    return jsonify({"ok": ok, "message": message}), (200 if ok else (status or 400))


@bp.route("/my/bridges")
@login_required
def page():
    _on()
    d = get_db()
    me = current_user.id
    from . import matrix
    in_matrix = matrix.replaces_builtin()
    rooms, rooms_note = [], ""
    if in_matrix:
        links = d.execute("SELECT l.*, COALESCE(NULLIF(l.room_name, ''), l.room_id) AS title FROM bridge_links l WHERE l.created_by=%s AND l.room_id IS NOT NULL ORDER BY l.id", (me,)).fetchall()
        ok, got = matrix.rooms_of(current_user.username)
        rooms, rooms_note = (got, "") if ok else ([], got)
        convs = []
    else:
        links = d.execute("SELECT l.*, COALESCE(NULLIF(c.name, ''), 'Chat #' || c.id) AS title FROM bridge_links l JOIN convs c ON c.id = l.conv_id WHERE l.created_by=%s ORDER BY l.id", (me,)).fetchall()
    convs = [] if in_matrix else d.execute("SELECT c.id, c.kind, c.name, (SELECT string_agg(COALESCE(NULLIF(u.display_name, ''), u.username), ', ') FROM conv_members m2 JOIN users u ON u.id = m2.user_id WHERE m2.conv_id = c.id AND m2.user_id <> %s) AS others "
                      "FROM conv_members m JOIN convs c ON c.id = m.conv_id WHERE m.user_id=%s ORDER BY c.last_at DESC LIMIT 100", (me, me)).fetchall()
    u = d.execute("SELECT bridge_aprs_ok, bridge_mesh_ok, callsign FROM users WHERE id=%s", (me,)).fetchone()
    from .bridge_verify import member_context, required as verify_required
    return render_template("bridges.html", ham=member_context(me), verify_required=verify_required(), links=links, convs=convs, aprs_on=enabled("aprs"), mesh_on=enabled("mesh"), aprs_ok=u["bridge_aprs_ok"], mesh_ok=u["bridge_mesh_ok"], call=my_call(me), profile_call=u["callsign"] or "",
                           max_links=cfg_int("bridge_links_per_member"), per_hour=cfg_int("bridge_aprs_rate_per_hour"), max_text=cfg_int("bridge_aprs_max_text"), kinds=KINDS, in_matrix=in_matrix, rooms=rooms, rooms_note=rooms_note, active="bridges")


@bp.route("/my/bridges/optin", methods=["POST"])
@login_required
def optin():
    _on()
    kind = request.form.get("kind", "")
    if kind not in KINDS or not enabled(kind):
        return _json(False, "That bridge is not switched on.")
    on = request.form.get("on") == "1"
    if on and kind == "aprs":
        if not my_call(current_user.id):
            return _json(False, "Put your amateur radio call sign on your Profile first.")
        from .bridge_verify import verified
        if not verified(current_user.id):
            return _json(False, "Check your amateur license first (Check My License): APRS can only be used by a verified, licensed operator.")
        if request.form.get("ack") != "1":
            return _json(False, "Tick the box to confirm the rules for APRS.")
    d = get_db()
    d.execute(f"UPDATE users SET bridge_{kind}_ok=%s WHERE id=%s", (on, current_user.id))
    if not on:
        d.execute("UPDATE bridge_links SET enabled=FALSE WHERE created_by=%s AND kind=%s", (current_user.id, kind))
    d.commit()
    audit("bridge.optin", target=current_user.username, detail=f"{kind} {'on' if on else 'off'}")
    return _json(True, "Saved.")


@bp.route("/my/bridges/add", methods=["POST"])
@login_required
def add():
    _on()
    d = get_db()
    me = current_user.id
    kind = request.form.get("kind", "")
    if kind not in KINDS or not enabled(kind):
        return _json(False, "That bridge is not switched on.")
    if not opted_in(me, kind):
        return _json(False, "Switch the bridge on for your account first.")
    from . import matrix
    room_id, room_name = "", ""
    if matrix.replaces_builtin():
        room_id = request.form.get("room_id", "")
        ok, got = matrix.rooms_of(current_user.username)
        mine = {r["room_id"]: r for r in got} if ok else {}
        if room_id not in mine:
            return _json(False, "Choose one of your rooms." if ok else got)
        if mine[room_id]["encrypted"]:
            return _json(False, "That room is end-to-end encrypted, so the bridge could not read it. Use a room that is not encrypted (a room made as public, or with encryption left off).")
        room_name = mine[room_id]["name"][:120]
        cid = ""
    else:
        cid = request.form.get("conv_id", "")
        if not cid.isdigit() or not d.execute("SELECT 1 FROM conv_members WHERE conv_id=%s AND user_id=%s", (int(cid), me)).fetchone():
            return _json(False, "Choose one of your chats.")
    remote = (request.form.get("remote") or "").strip()
    if kind == "aprs":
        remote = _norm_call(remote)
        if not HAM_RE.match(remote):
            return _json(False, "An APRS call sign looks like W5ABC or W5ABC-9.")
        if remote.split("-")[0] == my_call(me).split("-")[0]:
            return _json(False, "That is your own call sign.")
    else:
        m = NODE_RE.match(remote)
        if not m:
            return _json(False, "A mesh node ID is eight hexadecimal characters, for example !a1b2c3d4.")
        remote = "!" + m.group(1).lower()
    if d.execute("SELECT COUNT(*) AS n FROM bridge_links WHERE created_by=%s", (me,)).fetchone()["n"] >= cfg_int("bridge_links_per_member"):
        return _json(False, f"You can have {cfg_int('bridge_links_per_member')} links at most.")
    if room_id:
        if d.execute("SELECT 1 FROM bridge_links WHERE room_id=%s AND kind=%s AND remote=%s", (room_id, kind, remote)).fetchone():
            return _json(False, "That room is already linked to it.")
        joined, why = matrix.bridge_join(room_id, current_user.username)
        if not joined:
            return _json(False, "The bridge could not join the room: " + why)
        d.execute("INSERT INTO bridge_links (conv_id, room_id, room_name, kind, remote, created_by) VALUES (NULL,%s,%s,%s,%s,%s)", (room_id, room_name, kind, remote, me))
        d.commit()
        audit("bridge.link", target=remote, detail=f"{kind} link to room {room_name}")
        return _json(True, "Linked. The bridge joined the room; it says nothing there until the radio side answers.")
    if d.execute("SELECT 1 FROM bridge_links WHERE conv_id=%s AND kind=%s AND remote=%s", (int(cid), kind, remote)).fetchone():
        return _json(False, "That chat is already linked to it.")
    d.execute("INSERT INTO bridge_links (conv_id, kind, remote, created_by) VALUES (%s,%s,%s,%s)", (int(cid), kind, remote, me))
    d.commit()
    audit("bridge.link", target=remote, detail=f"{kind} link to chat #{cid}")
    return _json(True, "Linked.")


def _own(lid):
    l = get_db().execute("SELECT * FROM bridge_links WHERE id=%s AND created_by=%s", (lid, current_user.id)).fetchone()
    if l is None:
        abort(404)
    return l


@bp.route("/my/bridges/<int:lid>/toggle", methods=["POST"])
@login_required
def toggle(lid):
    _on()
    l = _own(lid)
    on = not l["enabled"]
    if on and not opted_in(current_user.id, l["kind"]):
        return _json(False, "Switch the bridge on for your account first.")
    d = get_db()
    d.execute("UPDATE bridge_links SET enabled=%s WHERE id=%s", (on, lid))
    d.commit()
    audit("bridge.toggle", target=l["remote"], detail=f"{l['kind']} link {'on' if on else 'off'}")
    return _json(True, "On." if on else "Off.")


@bp.route("/my/bridges/<int:lid>/delete", methods=["POST"])
@login_required
def delete(lid):
    _on()
    l = _own(lid)
    d = get_db()
    d.execute("DELETE FROM bridge_links WHERE id=%s", (lid,))
    d.commit()
    if l["room_id"] and not d.execute("SELECT 1 FROM bridge_links WHERE room_id=%s", (l["room_id"],)).fetchone() and not d.execute("SELECT 1 FROM matrix_mirrors WHERE room_id=%s", (l["room_id"],)).fetchone():
        from . import matrix
        matrix.bridge_leave(l["room_id"])   # nothing else needs the bot there
    audit("bridge.unlink", target=l["remote"], detail=f"{l['kind']} link to " + (f"room {l['room_name']}" if l["room_id"] else f"chat #{l['conv_id']}"))
    return _json(True, "Removed.")


# ------------------------------------------------------------------ the mesh gateway's two addresses

def _token_ok():
    want = cfg("bridge_mesh_token")
    got = request.headers.get("X-Bridge-Token", "")
    return bool(want) and enabled("mesh") and secrets.compare_digest(want.encode(), got.encode())


@bp.route("/bridge/mesh/inbound", methods=["POST"])
@csrf.exempt
def mesh_inbound():
    """The mesh gateway hands over a text message heard from a node: {"node": "!a1b2c3d4", "text": "...", "id": "packet id"}."""
    if not _token_ok():
        return jsonify({"error": "unauthorized"}), 401
    j = request.get_json(silent=True) or {}
    m = NODE_RE.match(str(j.get("node", "")))
    if not m:
        return jsonify({"error": "bad_node"}), 400
    node = "!" + m.group(1).lower()
    n = 0
    for l in get_db().execute("SELECT l.* FROM bridge_links l JOIN users u ON u.id = l.created_by WHERE l.kind='mesh' AND l.enabled AND l.remote=%s AND u.bridge_mesh_ok", (node,)).fetchall():
        if deliver(l, node, str(j.get("text", "")), str(j.get("id", ""))):
            n += 1
    return jsonify({"ok": True, "delivered": n})


@bp.route("/bridge/mesh/outbox")
def mesh_outbox():
    """The messages waiting to go out to mesh nodes: [{id, node, text}]; the gateway marks each done afterwards."""
    if not _token_ok():
        return jsonify({"error": "unauthorized"}), 401
    rows = get_db().execute("SELECT id, node_id, body FROM mesh_outbox WHERE sent_at IS NULL ORDER BY id LIMIT 50").fetchall()
    return jsonify({"messages": [{"id": r["id"], "node": r["node_id"], "text": r["body"]} for r in rows]})


@bp.route("/bridge/mesh/outbox/<int:oid>/done", methods=["POST"])
@csrf.exempt
def mesh_done(oid):
    if not _token_ok():
        return jsonify({"error": "unauthorized"}), 401
    d = get_db()
    row = d.execute("UPDATE mesh_outbox SET sent_at=now() WHERE id=%s AND sent_at IS NULL RETURNING link_id, node_id", (oid,)).fetchone()
    d.commit()
    if row is None:
        return jsonify({"error": "not_found"}), 404
    _log(row["link_id"], "out", True, f"the mesh gateway sent it to {row['node_id']}")
    return jsonify({"ok": True})


def counts():
    r = get_db().execute("SELECT (SELECT COUNT(*) FROM bridge_links WHERE enabled) AS links, (SELECT COUNT(*) FROM bridge_log WHERE at > now() - interval '24 hours') AS day, "
                         "(SELECT COUNT(*) FROM mesh_outbox WHERE sent_at IS NULL) AS waiting").fetchone()
    return r
