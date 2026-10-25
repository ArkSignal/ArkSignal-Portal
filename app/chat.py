"""Chat: live conversations between two or more signed-in members, and the moderators' page for chat and messages together.

A chat is a conversation between exactly two members (a direct chat) or a small named group. The page shows the member's conversations on the left and the open one on the right (one at a time on a phone). New
messages appear without reloading: the open page asks the site for anything new every few seconds (`static/chatlive.js`, the interval is a setting), and the same script shows who is typing, who has seen a message,
who is online, and an unread count in the menu. Messages are plain text, never HTML. Only the people in a conversation can read it; moderators (the Moderate Chat and Messages permission) never can, and see only a
message somebody reported. Who may start a chat with a member follows that member's contact rule and their block list (`app/social.py`). Rules an administrator sets in Settings > Chat: length, messages a minute,
words that are refused, how old an account must be to write, group size, how many new chats a day, how long messages are kept. Moderators delete a reported message, mute a member and read reports; the structure of
every chat (who started one, who was added or removed, who left) and every moderation action is in the audit log, never the words that were said.

The first version of this feature was a set of public rooms and two-person private threads. Those tables (`chat_rooms`, `chat_messages`, `pm_threads`, `pm_messages`) are kept as they were; the first start of
this version turns every old private thread into a direct chat (`migrate_v2`) and the public rooms are no longer shown."""
import re

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from .action_response import action_response
from .admin import permission_required
from . import vault
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int
from . import social
from .social import is_mod, muted, name_of  # noqa: F401  (messages.py and the tests use these from here)

bp = Blueprint("chat", __name__)


def _on():
    from . import matrix
    if cfg("chat_enabled") != "1" or matrix.replaces_builtin():   # Matrix replaces the built-in chat
        abort(404)


# ------------------------------------------------------------------ the move from rooms and two-person threads

_state = {"purged": 0.0}


def migrate_v2(d):
    """Once: turn each old private thread into a direct chat (keeping its messages, who has read them and the old 'not taking new conversations' choice). Safe to call on every request."""
    if d.execute("SELECT 1 FROM settings WHERE key='chat_v2_migrated'").fetchone():
        return
    for t in d.execute("SELECT * FROM pm_threads ORDER BY id").fetchall():
        key = f"{t['user_a']}:{t['user_b']}"
        if d.execute("SELECT 1 FROM convs WHERE pair_key=%s", (key,)).fetchone():
            continue
        msgs = d.execute("SELECT * FROM pm_messages WHERE thread_id=%s AND delivered AND NOT hidden_a AND NOT hidden_b ORDER BY id", (t["id"],)).fetchall()
        if not msgs:
            continue
        cid = d.execute("INSERT INTO convs (kind, created_by, created_at, last_at, pair_key) VALUES ('direct',%s,%s,%s,%s) RETURNING id", (t["user_a"], t["created_at"], t["last_at"], key)).fetchone()["id"]
        newids = []
        for m in msgs:
            nid = d.execute("INSERT INTO conv_messages (conv_id, user_id, body, created_at) VALUES (%s,%s,%s,%s) RETURNING id", (cid, m["sender_id"], vault.seal(m["body"]), m["created_at"])).fetchone()["id"]
            newids.append((nid, m))
        for uid in (t["user_a"], t["user_b"]):
            last = 0
            for nid, m in newids:
                if m["sender_id"] == uid or m["read_at"] is not None:
                    last = nid
                else:
                    break
            if last == 0 and all(m["sender_id"] != uid and m["read_at"] is None for _, m in newids):
                last = 0
            d.execute("INSERT INTO conv_members (conv_id, user_id, last_read_id) VALUES (%s,%s,%s)", (cid, uid, last))
    d.execute("UPDATE users SET contact_policy='friends' WHERE NOT pm_open AND contact_policy='anyone'")
    d.execute("INSERT INTO settings (key, value) VALUES ('chat_v2_migrated', '1') ON CONFLICT DO NOTHING")
    d.commit()


def purge(d):
    import time
    if time.time() - _state["purged"] < 600:
        return
    _state["purged"] = time.time()
    d.execute("DELETE FROM conv_messages WHERE created_at < now() - make_interval(days => %s)", (cfg_int("chat_keep_days"),))
    if cfg_int("pm_keep_days"):
        d.execute("DELETE FROM mail_messages WHERE COALESCE(sent_at, created_at) < now() - make_interval(days => %s)", (cfg_int("pm_keep_days"),))
        d.execute("DELETE FROM pm_messages WHERE created_at < now() - make_interval(days => %s)", (cfg_int("pm_keep_days"),))
    if cfg_int("mail_trash_days"):
        d.execute("DELETE FROM mail_recipients WHERE folder='trash' AND trashed_at < now() - make_interval(days => %s)", (cfg_int("mail_trash_days"),))
        d.execute("UPDATE mail_messages SET sender_folder='gone' WHERE sender_folder='trash' AND trashed_at < now() - make_interval(days => %s)", (cfg_int("mail_trash_days"),))
    d.execute("DELETE FROM mail_messages m WHERE m.sender_folder = 'gone' AND NOT EXISTS (SELECT 1 FROM mail_recipients r WHERE r.message_id = m.id)")
    d.execute("DELETE FROM convs c WHERE NOT EXISTS (SELECT 1 FROM conv_members m WHERE m.conv_id = c.id)")
    d.execute("DELETE FROM chat_mutes WHERE until IS NOT NULL AND until < now() - interval '1 day'")
    d.execute("DELETE FROM friendships WHERE status='pending' AND created_at < now() - interval '90 days'")
    d.commit()


def counts():
    d = get_db()
    return d.execute("SELECT (SELECT COUNT(*) FROM conv_messages WHERE created_at > now() - interval '24 hours' AND deleted_at IS NULL) AS chat_day, (SELECT COUNT(*) FROM convs) AS convs, "
                     "(SELECT COUNT(*) FROM mail_messages WHERE sent_at > now() - interval '24 hours') AS pm_day, "
                     "(SELECT COUNT(*) FROM friendships WHERE status='accepted') AS friends, "
                     "(SELECT COUNT(*) FROM conv_reports WHERE handled_at IS NULL) + (SELECT COUNT(*) FROM mail_reports WHERE handled_at IS NULL) + (SELECT COUNT(*) FROM pm_reports WHERE handled_at IS NULL) AS reports").fetchone()


# ------------------------------------------------------------------ helpers

def _conv(cid):
    """(conversation row, my membership row) or 404: only a member of a conversation can reach it."""
    d = get_db()
    c = d.execute("SELECT * FROM convs WHERE id=%s", (cid,)).fetchone()
    m = d.execute("SELECT * FROM conv_members WHERE conv_id=%s AND user_id=%s", (cid, current_user.id)).fetchone() if c else None
    if c is None or m is None:
        abort(404)
    return c, m


def _members(cid):
    return get_db().execute("SELECT cm.user_id, cm.last_read_id, cm.typing_at, u.username, u.display_name, u.show_online FROM conv_members cm JOIN users u ON u.id = cm.user_id WHERE cm.conv_id=%s ORDER BY cm.joined_at, u.username", (cid,)).fetchall()


def _title(c, members):
    others = [name_of(m) for m in members if m["user_id"] != current_user.id]
    if c["kind"] == "direct":
        return others[0] if others else "Chat"
    return c["name"] or ", ".join(others[:3]) or "Group chat"


def _list():
    """The signed-in member's conversations, newest activity first, each with its title, unread count, last words and who is online."""
    d = get_db()
    me = current_user.id
    rows = d.execute(
        "SELECT c.id, c.kind, c.name, c.last_at, c.created_by, cm.last_read_id, "
        "(SELECT COUNT(*) FROM conv_messages m WHERE m.conv_id = c.id AND m.id > cm.last_read_id AND m.user_id <> %s AND m.deleted_at IS NULL) AS unread, "
        "(SELECT m.body FROM conv_messages m WHERE m.conv_id = c.id AND m.deleted_at IS NULL ORDER BY m.id DESC LIMIT 1) AS last_body, "
        "(SELECT m.user_id FROM conv_messages m WHERE m.conv_id = c.id AND m.deleted_at IS NULL ORDER BY m.id DESC LIMIT 1) AS last_user "
        "FROM conv_members cm JOIN convs c ON c.id = cm.conv_id WHERE cm.user_id = %s AND NOT cm.hidden ORDER BY c.last_at DESC LIMIT %s", (me, me, max(10, cfg_int("chat_list_limit")))).fetchall()
    ids = [r["id"] for r in rows]
    people = {}
    if ids:
        for m in d.execute("SELECT cm.conv_id, cm.user_id, u.username, u.display_name FROM conv_members cm JOIN users u ON u.id = cm.user_id WHERE cm.conv_id = ANY(%s)", (ids,)).fetchall():
            people.setdefault(m["conv_id"], []).append(m)
    online = social.online_ids({m["user_id"] for ms in people.values() for m in ms})
    out = []
    for r in rows:
        ms = people.get(r["id"], [])
        others = [m for m in ms if m["user_id"] != me]
        title = (name_of(others[0]) if others else "Chat") if r["kind"] == "direct" else (r["name"] or ", ".join(name_of(m) for m in others[:3]) or "Group chat")
        out.append({"id": r["id"], "kind": r["kind"], "title": title, "unread": r["unread"], "last": (vault.unseal(r["last_body"]) or "")[:120], "mine": r["last_user"] == me, "at": r["last_at"].isoformat(),
                    "online": r["kind"] == "direct" and bool(others) and others[0]["user_id"] in online, "people": len(ms)})
    return out


# ------------------------------------------------------------------ the member's pages

@bp.route("/chat/")
@bp.route("/chat/<int:cid>")
@login_required
def page(cid=None):
    from . import matrix
    if matrix.replaces_builtin():   # Matrix replaces the built-in chat: the same address shows Element
        return matrix.framed_page(dm=request.args.get("dm"), room=request.args.get("room"))
    _on()
    d = get_db()
    migrate_v2(d)
    purge(d)
    social.touch(current_user.id)
    conv = None
    if cid is not None:
        c, m = _conv(cid)
        d.execute("UPDATE conv_members SET hidden=FALSE WHERE conv_id=%s AND user_id=%s", (cid, current_user.id))
        d.commit()
        ms = _members(cid)
        conv = {"id": c["id"], "kind": c["kind"], "title": _title(c, ms), "mine": c["created_by"] == current_user.id, "people": [{"uid": x["user_id"], "name": name_of(x), "username": x["username"]} for x in ms]}
    links = d.execute("SELECT id, kind, remote, enabled, created_by FROM bridge_links WHERE conv_id=%s ORDER BY id", (cid,)).fetchall() if cid is not None else []
    from .bridge import enabled as bridge_on, my_call, opted_in
    from .bridge_verify import verified_call
    me = current_user.id
    radio = {"aprs": bridge_on("aprs"), "mesh": bridge_on("mesh"), "call": my_call(me), "ham": verified_call(me), "aprs_ok": bridge_on("aprs") and opted_in(me, "aprs"), "mesh_ok": bridge_on("mesh") and opted_in(me, "mesh")}
    friends = []
    ids = social.friend_ids(current_user.id)
    if ids:
        friends = d.execute("SELECT username, display_name FROM users WHERE id = ANY(%s) AND NOT disabled ORDER BY lower(coalesce(nullif(display_name, ''), username))", (list(ids),)).fetchall()
    return render_template("chat.html", conv=conv, links=links, radio=radio, friends=friends, mute=muted(current_user.id, "chat"), is_mod=is_mod(), max_len=cfg_int("chat_max_length"), seconds=max(2, cfg_int("chat_poll_seconds")),
                           group_max=cfg_int("chat_group_max"), keep_days=cfg_int("chat_keep_days"), friends_on=cfg("friends_enabled") == "1", active="chat")


@bp.route("/chat/list.json")
@login_required
def list_json():
    _on()
    d = get_db()
    migrate_v2(d)
    social.touch(current_user.id)
    return jsonify({"chats": _list()})


@bp.route("/chat/unread.json")
@login_required
def unread_json():
    """The numbers beside Chat, Messages and Friends in the menu; asking also tells the site this member is online."""
    social.touch(current_user.id)
    me = current_user.id
    from . import matrix
    builtin = not matrix.replaces_builtin()
    return jsonify({"chat": social.chat_unread(me) if cfg("chat_enabled") == "1" and builtin else 0, "mail": social.mail_unread(me) if cfg("pm_enabled") == "1" and builtin else 0,
                    "friends": social.friend_requests(me) if cfg("friends_enabled") == "1" else 0, "seconds": max(10, cfg_int("chat_badge_seconds"))})


def _usernames(text):
    seen, out = set(), []
    for part in re.split(r"[,;\s]+", text or ""):
        p = part.strip().lstrip("@")
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


@bp.route("/chat/new", methods=["POST"])
@login_required
def new():
    """Start a direct chat (one other member) or a group (two or more), optionally with a first message."""
    _on()
    d = get_db()
    me = current_user.id
    if muted(me, "chat"):
        return jsonify({"ok": False, "message": "You are muted in chat for now."}), 400
    names = _usernames(request.form.get("to"))
    if not names:
        return jsonify({"ok": False, "message": "Choose who to chat with."}), 400
    targets = []
    for n in names:
        u = social.find_user(n)
        if u is None:
            return jsonify({"ok": False, "message": f"No member has the user name {n}."}), 400
        ok, why = social.may_contact(u, "chat")
        if not ok:
            return jsonify({"ok": False, "message": why if len(names) == 1 else f"{n}: {why}"}), 400
        targets.append(u)
    if len(targets) + 1 > cfg_int("chat_group_max"):
        return jsonify({"ok": False, "message": f"A group chat can have {cfg_int('chat_group_max')} people at most."}), 400
    body = " ".join((request.form.get("body") or "").split())
    if len(targets) == 1:
        a, b = social.pair(me, targets[0]["id"])
        key = f"{a}:{b}"
        c = d.execute("SELECT * FROM convs WHERE pair_key=%s", (key,)).fetchone()
        if c is not None:
            d.execute("INSERT INTO conv_members (conv_id, user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (c["id"], me))
            d.execute("INSERT INTO conv_members (conv_id, user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (c["id"], targets[0]["id"]))
            d.execute("UPDATE conv_members SET hidden=FALSE WHERE conv_id=%s AND user_id=%s", (c["id"], me))
            d.commit()
            cid = c["id"]
        else:
            if d.execute("SELECT COUNT(*) AS n FROM convs WHERE created_by=%s AND created_at > now() - interval '1 day'", (me,)).fetchone()["n"] >= cfg_int("chat_new_per_day"):
                return jsonify({"ok": False, "message": "You have started a lot of chats today. Try again tomorrow."}), 400
            cid = d.execute("INSERT INTO convs (kind, created_by, pair_key) VALUES ('direct',%s,%s) RETURNING id", (me, key)).fetchone()["id"]
            d.execute("INSERT INTO conv_members (conv_id, user_id) VALUES (%s,%s), (%s,%s)", (cid, me, cid, targets[0]["id"]))
            d.commit()
            audit("chat.start", target=targets[0]["username"], detail="direct chat")
    else:
        if d.execute("SELECT COUNT(*) AS n FROM convs WHERE created_by=%s AND created_at > now() - interval '1 day'", (me,)).fetchone()["n"] >= cfg_int("chat_new_per_day"):
            return jsonify({"ok": False, "message": "You have started a lot of chats today. Try again tomorrow."}), 400
        name = " ".join((request.form.get("name") or "").split())[:60]
        cid = d.execute("INSERT INTO convs (kind, name, created_by) VALUES ('group',%s,%s) RETURNING id", (name, me)).fetchone()["id"]
        d.execute("INSERT INTO conv_members (conv_id, user_id) VALUES (%s,%s)", (cid, me))
        for u in targets:
            d.execute("INSERT INTO conv_members (conv_id, user_id) VALUES (%s,%s)", (cid, u["id"]))
        d.commit()
        audit("chat.start", target=", ".join(u["username"] for u in targets), detail=f"group chat {name or '(no name)'}")
    if body:
        err = _send_refusal(body)
        if err:
            return jsonify({"ok": True, "url": f"/chat/{cid}", "message": err})
        _store(cid, body)
        from .bridge import forward
        forward(cid, me, body)
    return jsonify({"ok": True, "url": f"/chat/{cid}"})


def _msg(row, mods_can_delete=False):
    me = current_user.id
    out = {"id": row["id"], "name": name_of(row), "uid": row["user_id"], "at": row["created_at"].isoformat(), "body": vault.unseal(row["body"]), "mine": row["user_id"] == me,
            "del_url": f"/chat/message/{row['id']}/delete" if row["user_id"] == me else "", "report_url": f"/chat/message/{row['id']}/report" if row["user_id"] != me else ""}
    from .bridge_verify import verified_call
    out["ham"] = verified_call(row["user_id"])
    return _remote(row, out)


def _remote(row, out):
    """A message that came in from APRS or the mesh is shown under the remote name, with where it came from, and is never 'mine'."""
    if row["remote_from"]:
        out["name"] = row["remote_from"]
        out["via"] = "APRS" if row["remote_id"].startswith("aprs:") else "mesh"
        out["mine"] = False
        out["ham"] = ""
        out["report_url"] = f"/chat/message/{row['id']}/report" if row["user_id"] != current_user.id else ""
    return out


@bp.route("/chat/<int:cid>/poll")
@login_required
def poll(cid):
    _on()
    c, mine = _conv(cid)
    try:
        after = int(request.args.get("after", "0"))
    except ValueError:
        after = 0
    d = get_db()
    social.touch(current_user.id)
    q = "SELECT m.id, m.user_id, m.body, m.created_at, m.remote_from, m.remote_id, u.username, u.display_name FROM conv_messages m JOIN users u ON u.id = m.user_id WHERE m.conv_id=%s AND m.deleted_at IS NULL"
    if after:
        rows = d.execute(q + " AND m.id > %s ORDER BY m.id LIMIT 200", (cid, after)).fetchall()
    else:
        rows = list(reversed(d.execute(q + " ORDER BY m.id DESC LIMIT %s", (cid, max(10, cfg_int("chat_history")))).fetchall()))
    gone = [x["id"] for x in d.execute("SELECT id FROM conv_messages WHERE conv_id=%s AND deleted_at IS NOT NULL AND id > %s ORDER BY id DESC LIMIT 100", (cid, max(0, after - 200))).fetchall()] if after else []
    newest = d.execute("SELECT COALESCE(MAX(id), 0) AS n FROM conv_messages WHERE conv_id=%s AND deleted_at IS NULL", (cid,)).fetchone()["n"]
    if newest > mine["last_read_id"]:
        d.execute("UPDATE conv_members SET last_read_id=%s WHERE conv_id=%s AND user_id=%s", (newest, cid, current_user.id))
    d.commit()
    ms = _members(cid)
    online = social.online_ids({m["user_id"] for m in ms})
    window = max(2, cfg_int("chat_typing_seconds"))
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    typing = [name_of(m) for m in ms if m["user_id"] != current_user.id and m["typing_at"] and now - m["typing_at"] < timedelta(seconds=window)]
    others = [{"uid": m["user_id"], "name": name_of(m), "read": m["last_read_id"], "online": m["user_id"] in online} for m in ms if m["user_id"] != current_user.id]
    return jsonify({"messages": [_msg(x) for x in rows], "gone": gone, "last": rows[-1]["id"] if rows else after, "typing": typing, "others": others, "people": len(ms)})


def _send_refusal(body):
    if muted(current_user.id, "chat"):
        return "You are muted in chat for now."
    if not body:
        return "Write something first."
    if len(body) > cfg_int("chat_max_length"):
        return f"A message can be {cfg_int('chat_max_length')} characters at most."
    if cfg_int("chat_new_member_days") and social.account_age_days() < cfg_int("chat_new_member_days") and not is_mod():
        return f"New members can write in chat after {cfg_int('chat_new_member_days')} days."
    words = [w.strip().lower() for w in cfg("chat_blocked_words").split(",") if w.strip()]
    low = body.lower()
    if next((w for w in words if re.search(r"\b" + re.escape(w) + r"\b", low)), None):
        return "That message has a word this chat does not allow."
    if get_db().execute("SELECT COUNT(*) AS n FROM conv_messages WHERE user_id=%s AND created_at > now() - interval '1 minute'", (current_user.id,)).fetchone()["n"] >= cfg_int("chat_rate_per_minute"):
        return "You are sending messages too fast. Wait a moment."
    return None


def _store(cid, body):
    d = get_db()
    mid = d.execute("INSERT INTO conv_messages (conv_id, user_id, body) VALUES (%s,%s,%s) RETURNING id", (cid, current_user.id, vault.seal(body))).fetchone()["id"]
    d.execute("UPDATE convs SET last_at=now() WHERE id=%s", (cid,))
    d.execute("UPDATE conv_members SET hidden=FALSE WHERE conv_id=%s", (cid,))
    d.execute("UPDATE conv_members SET last_read_id=%s, typing_at=NULL WHERE conv_id=%s AND user_id=%s", (mid, cid, current_user.id))
    d.commit()
    return mid


@bp.route("/chat/<int:cid>/send", methods=["POST"])
@login_required
def send(cid):
    _on()
    c, m = _conv(cid)
    body = " ".join((request.form.get("body") or (request.get_json(silent=True) or {}).get("body") or "").split())
    err = _send_refusal(body)
    if not err and c["kind"] == "direct":
        other = next((x for x in _members(cid) if x["user_id"] != current_user.id), None)
        if other and social.either_blocked(current_user.id, other["user_id"]):
            err = "You cannot write to that member."
    if err:
        return jsonify({"ok": False, "message": err}), 400
    mid = _store(cid, body)
    from .bridge import forward
    return jsonify({"ok": True, "id": mid, "note": forward(cid, current_user.id, body)})


@bp.route("/chat/<int:cid>/typing", methods=["POST"])
@login_required
def typing(cid):
    _on()
    _conv(cid)
    d = get_db()
    d.execute("UPDATE conv_members SET typing_at=now() WHERE conv_id=%s AND user_id=%s", (cid, current_user.id))
    d.commit()
    return jsonify({"ok": True})


@bp.route("/chat/<int:cid>/close", methods=["POST"])
@login_required
def close(cid):
    """Take a conversation out of the list (it comes back when somebody writes in it)."""
    _on()
    _conv(cid)
    d = get_db()
    d.execute("UPDATE conv_members SET hidden=TRUE WHERE conv_id=%s AND user_id=%s", (cid, current_user.id))
    d.commit()
    return jsonify({"ok": True, "url": "/chat/"})


def _group_creator(cid):
    c, m = _conv(cid)
    if c["kind"] != "group":
        abort(404)
    return c


@bp.route("/chat/<int:cid>/rename", methods=["POST"])
@login_required
def rename(cid):
    _on()
    c = _group_creator(cid)
    if c["created_by"] != current_user.id:
        return jsonify({"ok": False, "message": "Only the member who made the group can rename it."}), 403
    name = " ".join((request.form.get("name") or "").split())[:60]
    d = get_db()
    d.execute("UPDATE convs SET name=%s WHERE id=%s", (name, cid))
    d.commit()
    audit("chat.group_rename", target=name or "(no name)", detail=f"group chat #{cid}")
    return jsonify({"ok": True, "message": "Saved."})


@bp.route("/chat/<int:cid>/add", methods=["POST"])
@login_required
def add(cid):
    _on()
    c = _group_creator(cid)
    if c["created_by"] != current_user.id:
        return jsonify({"ok": False, "message": "Only the member who made the group can add people."}), 403
    d = get_db()
    u = social.find_user(request.form.get("to"))
    if u is None:
        return jsonify({"ok": False, "message": "No member has that user name."}), 400
    ok, why = social.may_contact(u, "chat")
    if not ok:
        return jsonify({"ok": False, "message": why}), 400
    if d.execute("SELECT 1 FROM conv_members WHERE conv_id=%s AND user_id=%s", (cid, u["id"])).fetchone():
        return jsonify({"ok": False, "message": "They are already in this group."}), 400
    if d.execute("SELECT COUNT(*) AS n FROM conv_members WHERE conv_id=%s", (cid,)).fetchone()["n"] >= cfg_int("chat_group_max"):
        return jsonify({"ok": False, "message": f"A group chat can have {cfg_int('chat_group_max')} people at most."}), 400
    d.execute("INSERT INTO conv_members (conv_id, user_id, last_read_id) VALUES (%s,%s,(SELECT COALESCE(MAX(id), 0) FROM conv_messages WHERE conv_id=%s))", (cid, u["id"], cid))
    d.commit()
    audit("chat.group_add", target=u["username"], detail=f"group chat #{cid}")
    return jsonify({"ok": True, "message": f"{name_of(u)} is added."})


@bp.route("/chat/<int:cid>/remove", methods=["POST"])
@login_required
def remove(cid):
    _on()
    c = _group_creator(cid)
    if c["created_by"] != current_user.id:
        return jsonify({"ok": False, "message": "Only the member who made the group can remove people."}), 403
    d = get_db()
    u = social.find_user(request.form.get("to"))
    if u is None or u["id"] == current_user.id or not d.execute("SELECT 1 FROM conv_members WHERE conv_id=%s AND user_id=%s", (cid, u["id"])).fetchone():
        return jsonify({"ok": False, "message": "That member is not in this group."}), 400
    d.execute("DELETE FROM conv_members WHERE conv_id=%s AND user_id=%s", (cid, u["id"]))
    d.commit()
    audit("chat.group_remove", target=u["username"], detail=f"group chat #{cid}")
    return jsonify({"ok": True, "message": f"{name_of(u)} is removed."})


@bp.route("/chat/<int:cid>/leave", methods=["POST"])
@login_required
def leave(cid):
    """Leave a group chat (a direct chat is only closed, with Close). The next oldest member takes over as its maker; the last one out deletes it."""
    _on()
    c = _group_creator(cid)
    d = get_db()
    d.execute("DELETE FROM conv_members WHERE conv_id=%s AND user_id=%s", (cid, current_user.id))
    rest = d.execute("SELECT user_id FROM conv_members WHERE conv_id=%s ORDER BY joined_at LIMIT 1", (cid,)).fetchone()
    if rest is None:
        d.execute("DELETE FROM convs WHERE id=%s", (cid,))
    elif c["created_by"] == current_user.id:
        d.execute("UPDATE convs SET created_by=%s WHERE id=%s", (rest["user_id"], cid))
    d.commit()
    audit("chat.group_leave", target=f"group chat #{cid}", detail=c["name"])
    return jsonify({"ok": True, "url": "/chat/"})


@bp.route("/chat/message/<int:mid>/delete", methods=["POST"])
@login_required
def delete(mid):
    _on()
    d = get_db()
    m = d.execute("SELECT m.id, m.user_id FROM conv_messages m JOIN conv_members cm ON cm.conv_id = m.conv_id AND cm.user_id = %s WHERE m.id=%s AND m.deleted_at IS NULL", (current_user.id, mid)).fetchone()
    if m is None:
        abort(404)
    if m["user_id"] != current_user.id:
        abort(403)
    d.execute("UPDATE conv_messages SET deleted_at=now(), deleted_by=%s WHERE id=%s", (current_user.username, mid))
    d.commit()
    return jsonify({"ok": True})


@bp.route("/chat/message/<int:mid>/report", methods=["POST"])
@login_required
def report(mid):
    _on()
    d = get_db()
    m = d.execute("SELECT m.id, m.user_id FROM conv_messages m JOIN conv_members cm ON cm.conv_id = m.conv_id AND cm.user_id = %s WHERE m.id=%s AND m.deleted_at IS NULL", (current_user.id, mid)).fetchone()
    if m is None or m["user_id"] == current_user.id:
        abort(404)
    note = " ".join((request.form.get("note") or "").split())[:300]
    if d.execute("SELECT 1 FROM conv_reports WHERE message_id=%s AND reporter_id=%s", (mid, current_user.id)).fetchone():
        return jsonify({"ok": True, "message": "You already reported that message."})
    d.execute("INSERT INTO conv_reports (message_id, reporter_id, note) VALUES (%s,%s,%s)", (mid, current_user.id, note))
    d.commit()
    return jsonify({"ok": True, "message": "Thank you: a moderator will look at that message (and only that message)."})


# ------------------------------------------------------------------ the moderators' page

def _admin_ctx():
    d = get_db()
    migrate_v2(d)
    ctx = {"mutes": d.execute("SELECT c.*, u.username FROM chat_mutes c JOIN users u ON u.id = c.user_id ORDER BY c.created_at DESC").fetchall(),
            "reports": {
                "chat": d.execute("SELECT r.id, r.note, r.created_at, m.body, u.username AS author, rp.username AS reporter FROM conv_reports r JOIN conv_messages m ON m.id = r.message_id "
                                  "JOIN users u ON u.id = m.user_id JOIN users rp ON rp.id = r.reporter_id WHERE r.handled_at IS NULL ORDER BY r.created_at").fetchall(),
                "mail": d.execute("SELECT r.id, r.note, r.created_at, m.subject, m.body, u.username AS author, rp.username AS reporter FROM mail_reports r JOIN mail_messages m ON m.id = r.message_id "
                                  "JOIN users u ON u.id = m.sender_id JOIN users rp ON rp.id = r.reporter_id WHERE r.handled_at IS NULL ORDER BY r.created_at").fetchall(),
                "pm": d.execute("SELECT r.id, r.note, r.created_at, m.body, u.username AS author, rp.username AS reporter FROM pm_reports r JOIN pm_messages m ON m.id = r.message_id "
                                "JOIN users u ON u.id = m.sender_id JOIN users rp ON rp.id = r.reporter_id WHERE r.handled_at IS NULL ORDER BY r.created_at").fetchall()},
            "c": counts(), "active": "chat"}
    for kind, rows in ctx["reports"].items():   # the reported text is sealed in the database: a moderator sees it only here, for the report
        ctx["reports"][kind] = [dict(r, **{c: vault.unseal(r[c]) for c in ("subject", "body") if c in r}) for r in rows]
    return ctx


def _admin_resp(ok, message):
    return action_response(ok, message, redirect_endpoint="chat.admin_page", fragment_template="partials/_admin_chat.html", fragment_context=_admin_ctx())


@bp.route("/admin/chat")
@permission_required("moderate_chat")
def admin_page():
    return render_template("admin_chat.html", **_admin_ctx())


@bp.route("/admin/chat/mute", methods=["POST"])
@permission_required("moderate_chat")
def admin_mute():
    who = (request.form.get("username") or "").strip()
    scope = request.form.get("scope", "both")
    hours = request.form.get("hours", "")
    d = get_db()
    u = d.execute("SELECT id, username FROM users WHERE lower(username)=lower(%s) OR lower(email)=lower(%s)", (who, who)).fetchone()
    if u is None:
        return _admin_resp(False, "No member has that user name or email address.")
    if scope not in ("chat", "pm", "both"):
        return _admin_resp(False, "Choose what to mute.")
    until = None
    if hours.isdigit() and 0 < int(hours) <= 24 * 365:
        until = f"{int(hours)} hours"
    reason = " ".join((request.form.get("reason") or "").split())[:200]
    for sc in (("chat", "pm") if scope == "both" else (scope,)):
        d.execute("INSERT INTO chat_mutes (user_id, scope, until, reason, by_name) VALUES (%s,%s," + ("now() + %s::interval" if until else "NULL") + ",%s,%s) "
                  "ON CONFLICT (user_id, scope) DO UPDATE SET until=EXCLUDED.until, reason=EXCLUDED.reason, by_name=EXCLUDED.by_name, created_at=now()", [u["id"], sc] + ([until] if until else []) + [reason, current_user.username])
    d.commit()
    audit("chat.mute", target=u["username"], detail=f"{scope}, {until or 'until lifted'}: {reason}")
    return _admin_resp(True, f"{u['username']} is muted ({scope}{', for ' + until if until else ''}).")


@bp.route("/admin/chat/unmute", methods=["POST"])
@permission_required("moderate_chat")
def admin_unmute():
    d = get_db()
    uid, scope = request.form.get("user_id", ""), request.form.get("scope", "")
    if not uid.isdigit() or scope not in ("chat", "pm"):
        abort(400)
    u = d.execute("SELECT username FROM users WHERE id=%s", (int(uid),)).fetchone()
    d.execute("DELETE FROM chat_mutes WHERE user_id=%s AND scope=%s", (int(uid), scope))
    d.commit()
    audit("chat.unmute", target=u["username"] if u else uid, detail=scope)
    return _admin_resp(True, "Unmuted.")


REPORT_TABLES = {"chat": "conv_reports", "mail": "mail_reports", "pm": "pm_reports"}


@bp.route("/admin/chat/reports/<kind>/<int:rid>/<act>", methods=["POST"])
@permission_required("moderate_chat")
def admin_report(kind, rid, act):
    """Handle a report: dismiss it, or delete the reported message and close it."""
    if kind not in REPORT_TABLES or act not in ("dismiss", "delete"):
        abort(400)
    d = get_db()
    table = REPORT_TABLES[kind]
    rep = d.execute(f"SELECT message_id FROM {table} WHERE id=%s AND handled_at IS NULL", (rid,)).fetchone()
    if rep is None:
        abort(404)
    if act == "delete":
        if kind == "chat":
            d.execute("UPDATE conv_messages SET deleted_at=now(), deleted_by=%s WHERE id=%s", (current_user.username, rep["message_id"]))
        elif kind == "mail":
            d.execute("UPDATE mail_messages SET subject='[removed by a moderator]', body='[removed by a moderator]' WHERE id=%s", (rep["message_id"],))
        else:
            d.execute("UPDATE pm_messages SET hidden_a=TRUE, hidden_b=TRUE WHERE id=%s", (rep["message_id"],))
    d.execute(f"UPDATE {table} SET handled_by=%s, handled_at=now() WHERE id=%s", (current_user.username, rid))
    d.commit()
    audit("chat.report_" + act, target=f"{kind} report #{rid}", detail=f"message #{rep['message_id']}")
    return _admin_resp(True, "The message is deleted and the report closed." if act == "delete" else "The report is closed.")
