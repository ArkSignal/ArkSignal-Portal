"""Messages: email-style private mail between members.

A member writes a message with one or more recipients (members on this site, by user name), a subject and a body, and sends it or keeps it as a draft. Every member has an Inbox, Sent, Drafts, Archive and Trash;
a message can be read or unread, archived, deleted to the Trash (and from there for good), searched, and answered with Reply or Reply to All, which keeps the replies together as a thread. This is the quiet,
take-your-time way to write to someone; Chat (`app/chat.py`) is the live one. Rules: who may write to a member follows that member's contact rule and block list (`app/social.py`: a blocked sender looks sent and
is quietly not delivered); a message reaches only the people it was sent to; administrators cannot read messages, and moderators see only one that was reported, with who wrote it and who reported it. Limits are
Settings > Private Messages: body and subject length, recipients, messages an hour, how old an account must be to send, days kept, days in the Trash. A member a moderator has muted for private messages cannot send.
Sending, deleting for good and reporting are in the audit log (who and to whom, never the words)."""
from flask import Blueprint, abort, jsonify, redirect, render_template, request
from flask_login import current_user, login_required

from . import vault
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int
from . import social
from .social import muted, name_of

bp = Blueprint("messages", __name__)

FOLDERS = (("inbox", "Inbox"), ("sent", "Sent"), ("drafts", "Drafts"), ("archive", "Archive"), ("trash", "Trash"))
ACTIONS = {"archive", "inbox", "trash", "restore", "unread", "read", "delete"}


def _on():
    from . import matrix
    if cfg("pm_enabled") != "1" or matrix.replaces_builtin():   # Matrix replaces the built-in messages
        abort(404)


def unread_count(user_id):
    return social.mail_unread(user_id)


def _split(text):
    import re
    seen, out = set(), []
    for part in re.split(r"[,;\s]+", text or ""):
        p = part.strip().lstrip("@")
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


# ------------------------------------------------------------------ the folders

def _counts():
    d = get_db()
    me = current_user.id
    r = d.execute("SELECT (SELECT COUNT(*) FROM mail_recipients WHERE user_id=%s AND folder='inbox' AND delivered AND read_at IS NULL) AS unread, "
                  "(SELECT COUNT(*) FROM mail_messages WHERE sender_id=%s AND sender_folder='drafts') AS drafts, "
                  "(SELECT COUNT(*) FROM mail_recipients WHERE user_id=%s AND folder='trash') + (SELECT COUNT(*) FROM mail_messages WHERE sender_id=%s AND sender_folder='trash') AS trash", (me, me, me, me)).fetchone()
    return r


def _list(folder, q, limit, offset):
    """(rows, total) for a folder. Each row: id, subject, body, who (to whom or from whom), at, unread, mine. The text is sealed in the database: a search opens this member's own messages of the folder and looks in those."""
    d = get_db()
    me = current_user.id
    parts, args = [], []

    def recv(f):
        parts.append("SELECT m.id, m.subject, m.body, COALESCE(NULLIF(s.display_name, ''), s.username) AS who, s.username AS who_user, m.sent_at AS at, (r.read_at IS NULL) AS unread, FALSE AS mine "
                     "FROM mail_recipients r JOIN mail_messages m ON m.id = r.message_id JOIN users s ON s.id = m.sender_id WHERE r.user_id=%s AND r.folder=%s AND r.delivered")
        args.extend([me, f])

    def own(f):
        parts.append("SELECT m.id, m.subject, m.body, CASE WHEN m.draft_to = '' THEN '(no recipient yet)' ELSE m.draft_to END AS who, '' AS who_user, COALESCE(m.sent_at, m.created_at) AS at, FALSE AS unread, TRUE AS mine "
                     "FROM mail_messages m WHERE m.sender_id=%s AND m.sender_folder=%s")
        args.extend([me, f])
    if folder in ("inbox", "archive"):
        recv(folder)
    elif folder in ("sent", "drafts"):
        own(folder)
    else:
        recv("trash")
        own("trash")
    sql = " UNION ALL ".join(parts)

    def opened(rs):
        return [dict(r, subject=vault.unseal(r["subject"]), body=vault.unseal(r["body"])) for r in rs]
    if q:
        ql = q.lower()
        rows = [r for r in opened(d.execute(f"SELECT * FROM ({sql}) t ORDER BY at DESC, id DESC", args).fetchall())
                if ql in (r["subject"] or "").lower() or ql in (r["body"] or "").lower() or ql in (r["who"] or "").lower() or ql in (r["who_user"] or "").lower()]
        return rows[offset:offset + limit], len(rows)
    total = d.execute(f"SELECT COUNT(*) AS n FROM ({sql}) t", args).fetchone()["n"]
    rows = d.execute(f"SELECT * FROM ({sql}) t ORDER BY at DESC, id DESC LIMIT %s OFFSET %s", args + [limit, offset]).fetchall()
    return opened(rows), total


@bp.route("/my/messages")
@login_required
def inbox():
    from . import matrix
    if matrix.replaces_builtin():   # Matrix replaces the built-in messages: direct messages are in Element
        return matrix.framed_page(dm=request.args.get("dm"))
    _on()
    from . import chat
    d = get_db()
    chat.migrate_v2(d)
    folder = request.args.get("folder", "inbox")
    if folder not in dict(FOLDERS):
        folder = "inbox"
    q = " ".join(request.args.get("q", "").split())[:80]
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per = max(5, cfg_int("mail_page"))
    rows, total = _list(folder, q, per, (page - 1) * per)
    return render_template("my_messages.html", rows=rows, total=total, page=page, per=per, folder=folder, folders=FOLDERS, q=q, counts=_counts(), mute=muted(current_user.id, "pm"), active="messages",
                           trash_days=cfg_int("mail_trash_days"))


# ------------------------------------------------------------------ writing

def _refusal(body, subject):
    if muted(current_user.id, "pm"):
        return "You are muted from private messages for now."
    if not body.strip():
        return "Write something first."
    if len(body) > cfg_int("pm_max_length"):
        return f"A message can be {cfg_int('pm_max_length')} characters at most."
    if len(subject) > cfg_int("mail_max_subject"):
        return f"A subject can be {cfg_int('mail_max_subject')} characters at most."
    if cfg_int("pm_min_days") and social.account_age_days() < cfg_int("pm_min_days"):
        return f"New members can send private messages after {cfg_int('pm_min_days')} days."
    if get_db().execute("SELECT COUNT(*) AS n FROM mail_messages WHERE sender_id=%s AND sent_at > now() - interval '1 hour'", (current_user.id,)).fetchone()["n"] >= cfg_int("pm_rate_per_hour"):
        return "You have sent a lot of messages this hour. Wait a while."
    return None


def _form():
    body = (request.form.get("body") or "").replace("\r\n", "\n").strip()
    subject = " ".join((request.form.get("subject") or "").split())
    return request.form.get("to") or "", subject, body


def _own_draft(did):
    if not str(did or "").isdigit():
        return None
    r = get_db().execute("SELECT * FROM mail_messages WHERE id=%s AND sender_id=%s AND sender_folder='drafts'", (int(did), current_user.id)).fetchone()
    return dict(r, subject=vault.unseal(r["subject"]), body=vault.unseal(r["body"])) if r else None


@bp.route("/my/messages/compose")
@login_required
def compose():
    _on()
    d = get_db()
    to, subject, body, parent, draft = request.args.get("to", ""), "", "", None, None
    did = request.args.get("draft")
    if _own_draft(did):
        dr = _own_draft(did)
        to, subject, body, parent, draft = dr["draft_to"], dr["subject"], dr["body"], dr["parent_id"], dr["id"]
    elif request.args.get("reply", "").isdigit():
        m = _visible(int(request.args["reply"]))
        parent = m["id"]
        subject = m["subject"] if m["subject"].lower().startswith("re:") else "Re: " + m["subject"]
        names = [m["sender_name"]] if m["sender_id"] != current_user.id else _split(m["draft_to"])
        if request.args.get("all") == "1":
            names += [n for n in _split(m["draft_to"]) if n.lower() != current_user.username.lower()]
        to = ", ".join(dict.fromkeys(n for n in names if n and n.lower() != current_user.username.lower()))
    return render_template("mail_compose.html", to=to, subject=subject, body=body, parent=parent, draft=draft, max_len=cfg_int("pm_max_length"), max_subject=cfg_int("mail_max_subject"), mute=muted(current_user.id, "pm"),
                           max_to=cfg_int("mail_max_recipients"), active="messages", counts=_counts(), folders=FOLDERS, folder="")


@bp.route("/my/messages/draft", methods=["POST"])
@login_required
def save_draft():
    _on()
    to, subject, body = _form()
    d = get_db()
    parent = int(request.form["parent"]) if (request.form.get("parent") or "").isdigit() else None
    dr = _own_draft(request.form.get("draft"))
    if dr:
        d.execute("UPDATE mail_messages SET draft_to=%s, subject=%s, body=%s WHERE id=%s", (to[:400], vault.seal(subject[:300]), vault.seal(body[:20000]), dr["id"]))
        did = dr["id"]
    else:
        did = d.execute("INSERT INTO mail_messages (sender_id, subject, body, draft_to, parent_id) VALUES (%s,%s,%s,%s,%s) RETURNING id", (current_user.id, vault.seal(subject[:300]), vault.seal(body[:20000]), to[:400], parent)).fetchone()["id"]
    d.commit()
    return jsonify({"ok": True, "id": did, "message": "Draft saved."})


@bp.route("/my/messages/send", methods=["POST"])
@login_required
def send():
    _on()
    d = get_db()
    me = current_user.id
    to, subject, body = _form()
    names = _split(to)
    if not names:
        return jsonify({"ok": False, "message": "Choose who to send it to."}), 400
    if len(names) > cfg_int("mail_max_recipients"):
        return jsonify({"ok": False, "message": f"A message can go to {cfg_int('mail_max_recipients')} people at most."}), 400
    subject = subject or "(no subject)"
    err = _refusal(body, subject)
    if err:
        return jsonify({"ok": False, "message": err}), 400
    targets = []
    for n in names:
        u = social.find_user(n)
        if u is None:
            return jsonify({"ok": False, "message": f"No member has the user name {n}."}), 400
        if u["id"] == me:
            return jsonify({"ok": False, "message": "You cannot send a message to yourself."}), 400
        if social.blocked(u["id"], me):
            targets.append((u, False))   # looks sent, is not delivered, they are not told
            continue
        ok, why = social.may_contact(u, "mail")
        if not ok:
            return jsonify({"ok": False, "message": why if len(names) == 1 else f"{n}: {why}"}), 400
        targets.append((u, True))
    parent = None
    if (request.form.get("parent") or "").isdigit():
        try:
            parent = _visible(int(request.form["parent"]))
        except Exception:
            parent = None
    shown = ", ".join(u["username"] for u, _ in targets)
    dr = _own_draft(request.form.get("draft"))
    if dr:
        d.execute("UPDATE mail_messages SET subject=%s, body=%s, draft_to=%s, sent_at=now(), sender_folder='sent' WHERE id=%s", (vault.seal(subject), vault.seal(body), shown, dr["id"]))
        mid = dr["id"]
        if dr["parent_id"]:
            parent = d.execute("SELECT id, thread_id FROM mail_messages WHERE id=%s", (dr["parent_id"],)).fetchone()
    else:
        mid = d.execute("INSERT INTO mail_messages (sender_id, subject, body, draft_to, sent_at, sender_folder, parent_id) VALUES (%s,%s,%s,%s,now(),'sent',%s) RETURNING id", (me, vault.seal(subject), vault.seal(body), shown, parent["id"] if parent else None)).fetchone()["id"]
    d.execute("UPDATE mail_messages SET thread_id=%s WHERE id=%s", (parent["thread_id"] or parent["id"] if parent else mid, mid))
    for u, delivered in targets:
        if delivered:
            d.execute("INSERT INTO mail_recipients (message_id, user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (mid, u["id"]))
    d.commit()
    audit("mail.send", target=shown, detail=f"message #{mid}")
    return jsonify({"ok": True, "id": mid, "url": "/my/messages?folder=sent", "message": "Sent."})


# ------------------------------------------------------------------ reading

def _visible(mid):
    """A message the signed-in member wrote (not trashed for good) or received, with the sender's name, or 404."""
    m = get_db().execute("SELECT m.*, s.username AS sender_name, s.display_name AS sender_display, r.folder AS my_folder, r.read_at, r.delivered, r.user_id AS rcpt FROM mail_messages m JOIN users s ON s.id = m.sender_id "
                         "LEFT JOIN mail_recipients r ON r.message_id = m.id AND r.user_id = %s WHERE m.id=%s", (current_user.id, mid)).fetchone()
    if m is None:
        abort(404)
    m = dict(m, subject=vault.unseal(m["subject"]), body=vault.unseal(m["body"]))
    mine = m["sender_id"] == current_user.id and m["sender_folder"] != "gone"
    got = m["rcpt"] is not None and m["delivered"]
    if not (mine or got):
        abort(404)
    return m


@bp.route("/my/messages/<int:mid>")
@login_required
def view(mid):
    _on()
    d = get_db()
    m = _visible(mid)
    if m["sender_id"] == current_user.id and m["sender_folder"] == "drafts":
        return redirect(f"/my/messages/compose?draft={mid}")
    if m["rcpt"] is not None and m["read_at"] is None:
        d.execute("UPDATE mail_recipients SET read_at=now() WHERE message_id=%s AND user_id=%s", (mid, current_user.id))
        d.commit()
    me = current_user.id
    thread = d.execute("SELECT m.id, m.subject, m.body, m.draft_to, m.sent_at, m.sender_id, COALESCE(NULLIF(s.display_name, ''), s.username) AS sender_name, s.username AS sender_user "
                       "FROM mail_messages m JOIN users s ON s.id = m.sender_id LEFT JOIN mail_recipients r ON r.message_id = m.id AND r.user_id = %s "
                       "WHERE m.thread_id=%s AND m.sent_at IS NOT NULL AND ((m.sender_id = %s AND m.sender_folder NOT IN ('gone')) OR (r.user_id IS NOT NULL AND r.delivered)) ORDER BY m.sent_at, m.id", (me, m["thread_id"] or m["id"], me)).fetchall()
    thread = [dict(t, subject=vault.unseal(t["subject"]), body=vault.unseal(t["body"])) for t in thread]
    folder = m["my_folder"] if m["rcpt"] is not None else ("sent" if m["sender_folder"] == "sent" else m["sender_folder"])
    return render_template("mail_view.html", m=m, thread=thread, folder=folder, folders=FOLDERS, counts=_counts(), active="messages", mine=m["sender_id"] == me, sender_blocked=social.blocked(me, m["sender_id"]),
                           names=_split(m["draft_to"]))


@bp.route("/my/messages/bulk", methods=["POST"])
@login_required
def bulk():
    """Move, mark or delete one or more messages (checked in a folder list, or the one being read)."""
    _on()
    act = request.form.get("act", "")
    if act not in ACTIONS:
        return jsonify({"ok": False, "message": "Choose what to do."}), 400
    ids = [int(x) for x in request.form.getlist("ids") if x.isdigit()][:200]
    if not ids:
        return jsonify({"ok": False, "message": "Choose a message first."}), 400
    d = get_db()
    me = current_user.id
    n = 0
    for mid in ids:
        r = d.execute("SELECT folder FROM mail_recipients WHERE message_id=%s AND user_id=%s AND delivered", (mid, me)).fetchone()
        s = d.execute("SELECT sender_folder FROM mail_messages WHERE id=%s AND sender_id=%s AND sender_folder <> 'gone'", (mid, me)).fetchone()
        if r:
            if act in ("archive", "inbox"):
                d.execute("UPDATE mail_recipients SET folder=%s, trashed_at=NULL WHERE message_id=%s AND user_id=%s", ("archive" if act == "archive" else "inbox", mid, me))
            elif act == "trash":
                d.execute("UPDATE mail_recipients SET folder='trash', trashed_at=now() WHERE message_id=%s AND user_id=%s", (mid, me))
            elif act == "restore" and r["folder"] == "trash":
                d.execute("UPDATE mail_recipients SET folder='inbox', trashed_at=NULL WHERE message_id=%s AND user_id=%s", (mid, me))
            elif act == "unread":
                d.execute("UPDATE mail_recipients SET read_at=NULL WHERE message_id=%s AND user_id=%s", (mid, me))
            elif act == "read":
                d.execute("UPDATE mail_recipients SET read_at=COALESCE(read_at, now()) WHERE message_id=%s AND user_id=%s", (mid, me))
            elif act == "delete" and r["folder"] == "trash":
                d.execute("DELETE FROM mail_recipients WHERE message_id=%s AND user_id=%s", (mid, me))
            else:
                continue
            n += 1
        elif s:
            if act == "trash":
                d.execute("UPDATE mail_messages SET sender_folder='trash', trashed_at=now() WHERE id=%s", (mid,))
            elif act == "restore" and s["sender_folder"] == "trash":
                d.execute("UPDATE mail_messages SET sender_folder='sent', trashed_at=NULL WHERE id=%s", (mid,))
            elif act == "delete" and s["sender_folder"] in ("trash", "drafts"):
                d.execute("UPDATE mail_messages SET sender_folder='gone' WHERE id=%s", (mid,))
            else:
                continue
            n += 1
    d.execute("DELETE FROM mail_messages m WHERE m.id = ANY(%s) AND m.sender_folder='gone' AND NOT EXISTS (SELECT 1 FROM mail_recipients r WHERE r.message_id = m.id)", (ids,))
    d.commit()
    if act == "delete" and n:
        audit("mail.delete", target=current_user.username, detail=f"{n} message(s) deleted for good")
    return jsonify({"ok": True, "count": n, "message": "Done."})


@bp.route("/my/messages/empty-trash", methods=["POST"])
@login_required
def empty_trash():
    _on()
    d = get_db()
    me = current_user.id
    ids = [r["message_id"] for r in d.execute("SELECT message_id FROM mail_recipients WHERE user_id=%s AND folder='trash'", (me,)).fetchall()]
    a = d.execute("DELETE FROM mail_recipients WHERE user_id=%s AND folder='trash'", (me,)).rowcount
    b = d.execute("UPDATE mail_messages SET sender_folder='gone' WHERE sender_id=%s AND sender_folder='trash'", (me,)).rowcount
    d.execute("DELETE FROM mail_messages m WHERE (m.sender_id=%s OR m.id = ANY(%s)) AND m.sender_folder='gone' AND NOT EXISTS (SELECT 1 FROM mail_recipients r WHERE r.message_id = m.id)", (me, ids))
    d.commit()
    if a + b:
        audit("mail.delete", target=current_user.username, detail=f"{a + b} message(s) deleted for good (Trash emptied)")
    return jsonify({"ok": True, "message": "The Trash is empty."})


@bp.route("/my/messages/<int:mid>/report", methods=["POST"])
@login_required
def report(mid):
    _on()
    m = _visible(mid)
    if m["sender_id"] == current_user.id or m["rcpt"] is None:
        abort(404)
    d = get_db()
    if d.execute("SELECT 1 FROM mail_reports WHERE message_id=%s AND reporter_id=%s", (mid, current_user.id)).fetchone():
        return jsonify({"ok": True, "message": "You already reported that message."})
    note = " ".join((request.form.get("note") or "").split())[:300]
    d.execute("INSERT INTO mail_reports (message_id, reporter_id, note) VALUES (%s,%s,%s)", (mid, current_user.id, note))
    d.commit()
    audit("mail.report", target=m["sender_name"], detail=f"message #{mid}")
    return jsonify({"ok": True, "message": "Thank you: a moderator will look at that message (and only that message)."})
