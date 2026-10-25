"""Friends lists: a member sends a friend request, the other accepts or declines, and friends can see who of them is online and start a chat or a message in one click.

My Account > Friends has the friends with a green dot for those online, the requests waiting for an answer and the ones sent, a box to find a member and send a request, the blocked members, and two choices: who may
contact me (everyone, friends only, nobody new) and whether friends can see that I am online. Blocking a member ends the friendship, stops their requests, chats and messages, and they are not told. If two members
send each other a request, they become friends at once. Limits (Settings > Friends): how many friends a member can have and how many requests a day. Every request, answer, removal and block is in the audit log."""
from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int
from . import social
from .social import name_of

bp = Blueprint("friends", __name__)


def _on():
    if cfg("friends_enabled") != "1":
        abort(404)


def _row(a, b):
    x, y = social.pair(a, b)
    return get_db().execute("SELECT * FROM friendships WHERE user_a=%s AND user_b=%s", (x, y)).fetchone()


def _user(name):
    u = social.find_user(name)
    if u is None:
        return None
    return u


def _count_friends(uid):
    return len(social.friend_ids(uid))


def _resp(ok, message, status=200):
    return jsonify({"ok": ok, "message": message}), (status if ok else (status if status != 200 else 400))


@bp.route("/my/friends")
@login_required
def page():
    _on()
    d = get_db()
    me = current_user.id
    social.touch(me)
    rows = d.execute("SELECT f.status, f.requested_by, f.created_at, u.id, u.username, u.display_name, u.show_online FROM friendships f JOIN users u ON u.id = CASE WHEN f.user_a=%s THEN f.user_b ELSE f.user_a END "
                     "WHERE (f.user_a=%s OR f.user_b=%s) AND NOT u.disabled ORDER BY lower(coalesce(nullif(u.display_name, ''), u.username))", (me, me, me)).fetchall()
    online = social.online_ids({r["id"] for r in rows})
    friends = [dict(r, online=r["id"] in online) for r in rows if r["status"] == "accepted"]
    friends.sort(key=lambda r: (not r["online"],))
    incoming = [r for r in rows if r["status"] == "pending" and r["requested_by"] != me]
    sent = [r for r in rows if r["status"] == "pending" and r["requested_by"] == me]
    blocks = d.execute("SELECT u.username, u.display_name FROM pm_blocks b JOIN users u ON u.id = b.blocked_id WHERE b.blocker_id=%s ORDER BY lower(u.username)", (me,)).fetchall()
    mine = d.execute("SELECT contact_policy, show_online, find_by_email, find_by_callsign, push_friends FROM users WHERE id=%s", (me,)).fetchone()
    return render_template("friends.html", friends=friends, incoming=incoming, sent=sent, blocks=blocks, policy=mine["contact_policy"], show_online=mine["show_online"], find_by_email=mine["find_by_email"], find_by_callsign=mine["find_by_callsign"], policies=social.POLICIES,
                           fmax=cfg_int("friends_max"), chat_on=cfg("chat_enabled") == "1" or cfg("matrix_enabled") == "1", mail_on=cfg("pm_enabled") == "1" and cfg("matrix_enabled") != "1", active="friends", name_of=name_of)


@bp.route("/my/community")
@login_required
def community_settings():
    """My Account > Community Settings: who can contact you, being found, blocked members, and how chat, messages and friends work (the things themselves are in the Community section)."""
    from . import matrix
    if cfg("chat_enabled") != "1" and cfg("pm_enabled") != "1" and cfg("friends_enabled") != "1" and not matrix.replaces_builtin():
        abort(404)
    d = get_db()
    me = current_user.id
    mx = {"element_url": cfg("matrix_element_url").strip(), "server": cfg("matrix_server_name").strip(), "push_on": cfg("push_enabled") == "1", "calls": cfg("matrix_calls") == "1"} if matrix.replaces_builtin() else None
    blocks = d.execute("SELECT u.username, u.display_name FROM pm_blocks b JOIN users u ON u.id = b.blocked_id WHERE b.blocker_id=%s ORDER BY lower(u.username)", (me,)).fetchall()
    mine = d.execute("SELECT contact_policy, show_online, find_by_email, find_by_callsign, push_friends FROM users WHERE id=%s", (me,)).fetchone()
    return render_template("my_community.html", push_friends=mine["push_friends"], blocks=blocks, policy=mine["contact_policy"], show_online=mine["show_online"], find_by_email=mine["find_by_email"], find_by_callsign=mine["find_by_callsign"], policies=social.POLICIES,
                           chat_on=cfg("chat_enabled") == "1", mail_on=cfg("pm_enabled") == "1", friends_on=cfg("friends_enabled") == "1", keep_days=cfg_int("chat_keep_days"),
                           mute=social.muted(me, "chat"), pm_mute=social.muted(me, "pm"), name_of=name_of, mx=mx, active="community")


@bp.route("/my/friends/online.json")
@login_required
def online_json():
    _on()
    social.touch(current_user.id)
    ids = social.friend_ids(current_user.id)
    names = {r["id"]: r["username"] for r in get_db().execute("SELECT id, username FROM users WHERE id = ANY(%s)", (list(ids),)).fetchall()} if ids else {}
    return jsonify({"online": sorted(names[i] for i in social.online_ids(ids) if i in names)})


@bp.route("/my/friends/request", methods=["POST"])
@login_required
def request_friend():
    _on()
    d = get_db()
    me = current_user.id
    u = _user(request.form.get("to"))
    if u is None:
        return _resp(False, "No member matches that. Type a user name, a call sign or a whole email address.")
    ok, why = social.may_contact(u, "friend")
    if not ok:
        return _resp(False, why)
    row = _row(me, u["id"])
    if row is not None and row["status"] == "accepted":
        return _resp(False, f"{name_of(u)} is already your friend.")
    if row is not None and row["requested_by"] == me:
        return _resp(False, "You already sent that request.")
    if _count_friends(me) >= cfg_int("friends_max"):
        return _resp(False, f"You can have {cfg_int('friends_max')} friends at most.")
    a, b = social.pair(me, u["id"])
    if row is not None:   # they had asked me first: that is a yes
        d.execute("UPDATE friendships SET status='accepted', responded_at=now() WHERE user_a=%s AND user_b=%s", (a, b))
        d.commit()
        nudge_matrix()
        audit("friend.accept", target=u["username"], detail="both asked")
        return _resp(True, f"You and {name_of(u)} are now friends.")
    if d.execute("SELECT COUNT(*) AS n FROM friendships WHERE requested_by=%s AND created_at > now() - interval '1 day'", (me,)).fetchone()["n"] >= cfg_int("friends_requests_per_day"):
        return _resp(False, "You have sent a lot of friend requests today. Try again tomorrow.")
    d.execute("INSERT INTO friendships (user_a, user_b, status, requested_by) VALUES (%s,%s,'pending',%s)", (a, b, me))
    d.commit()
    audit("friend.request", target=u["username"])
    _tell(u["id"], "Friend request", f"{_me()} would like to be your friend.", "/my/friends", "friend-request")
    return _resp(True, f"A friend request is sent to {name_of(u)}.")


def _tell(user_id, title, body, url, tag):
    """A notification to a member's phones and computers (if the site allows it and the member did not turn friend notices off)."""
    if cfg("friends_push") != "1":
        return
    from . import pushkinds
    row = get_db().execute("SELECT disabled FROM users WHERE id=%s", (user_id,)).fetchone()
    if row and not row["disabled"]:
        pushkinds.notify(user_id, "friends", title, body, url, tag)


def _pending_from(name):
    u = _user(name)
    if u is None:
        abort(404)
    row = _row(current_user.id, u["id"])
    if row is None:
        abort(404)
    return u, row


@bp.route("/my/friends/<username>/accept", methods=["POST"])
@login_required
def accept(username):
    _on()
    u, row = _pending_from(username)
    if row["status"] != "pending" or row["requested_by"] == current_user.id:
        abort(404)
    if _count_friends(current_user.id) >= cfg_int("friends_max"):
        return _resp(False, f"You can have {cfg_int('friends_max')} friends at most.")
    d = get_db()
    d.execute("UPDATE friendships SET status='accepted', responded_at=now() WHERE user_a=%s AND user_b=%s", (row["user_a"], row["user_b"]))
    d.commit()
    nudge_matrix()
    audit("friend.accept", target=u["username"])
    _tell(u["id"], "Friend request accepted", f"{_me()} accepted your friend request.", "/my/friends", "friend-accept")
    return _resp(True, f"You and {name_of(u)} are now friends.")


@bp.route("/my/friends/<username>/decline", methods=["POST"])
@login_required
def decline(username):
    _on()
    u, row = _pending_from(username)
    if row["status"] != "pending" or row["requested_by"] == current_user.id:
        abort(404)
    d = get_db()
    d.execute("DELETE FROM friendships WHERE user_a=%s AND user_b=%s", (row["user_a"], row["user_b"]))
    d.commit()
    nudge_matrix()
    audit("friend.decline", target=u["username"])
    return _resp(True, "The request is declined. They are not told.")


@bp.route("/my/friends/<username>/cancel", methods=["POST"])
@login_required
def cancel(username):
    _on()
    u, row = _pending_from(username)
    if row["status"] != "pending" or row["requested_by"] != current_user.id:
        abort(404)
    d = get_db()
    d.execute("DELETE FROM friendships WHERE user_a=%s AND user_b=%s", (row["user_a"], row["user_b"]))
    d.commit()
    nudge_matrix()
    audit("friend.cancel", target=u["username"])
    return _resp(True, "The request is taken back.")


@bp.route("/my/friends/<username>/remove", methods=["POST"])
@login_required
def remove(username):
    _on()
    u, row = _pending_from(username)
    if row["status"] != "accepted":
        abort(404)
    d = get_db()
    d.execute("DELETE FROM friendships WHERE user_a=%s AND user_b=%s", (row["user_a"], row["user_b"]))
    d.commit()
    nudge_matrix()
    audit("friend.remove", target=u["username"])
    return _resp(True, f"{name_of(u)} is no longer your friend. They are not told.")


@bp.route("/my/friends/<username>/block", methods=["POST"])
@login_required
def block(username):
    """Block a member (from anywhere: the friends page, a chat or a message). It ends a friendship and any request, and stops their chats, messages and requests."""
    u = _user(username)
    if u is None or u["id"] == current_user.id:
        abort(404)
    d = get_db()
    d.execute("INSERT INTO pm_blocks (blocker_id, blocked_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (current_user.id, u["id"]))
    a, b = social.pair(current_user.id, u["id"])
    d.execute("DELETE FROM friendships WHERE user_a=%s AND user_b=%s", (a, b))
    d.commit()
    nudge_matrix()
    audit("friend.block", target=u["username"])
    return _resp(True, f"{name_of(u)} is blocked: they cannot chat, message or send you requests, and they are not told.")


@bp.route("/my/friends/<username>/unblock", methods=["POST"])
@login_required
def unblock(username):
    u = _user(username)
    if u is None:
        abort(404)
    d = get_db()
    d.execute("DELETE FROM pm_blocks WHERE blocker_id=%s AND blocked_id=%s", (current_user.id, u["id"]))
    d.commit()
    nudge_matrix()
    audit("friend.unblock", target=u["username"])
    return _resp(True, f"{name_of(u)} is unblocked.")


@bp.route("/people/search.json")
@login_required
def people_search():
    """The list under the search box on the friend, chat and message forms: members by user name, call sign or a whole email address (never any email address in the answer)."""
    if not (cfg("friends_enabled") == "1" or cfg("chat_enabled") == "1" or cfg("pm_enabled") == "1"):
        abort(404)
    found, limited = social.search_people(request.args.get("q", ""))
    resp = jsonify({"members": found, "limited": limited})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/my/friends/prefs", methods=["POST"])
@login_required
def prefs():
    pol = request.form.get("contact_policy", "")
    if pol not in {p for p, _ in social.POLICIES}:
        return _resp(False, "Choose who may contact you.")
    show = request.form.get("show_online") == "1"
    d = get_db()
    fe, fc = request.form.get("find_by_email") == "1", request.form.get("find_by_callsign") == "1"
    d.execute("UPDATE users SET contact_policy=%s, show_online=%s, find_by_email=%s, find_by_callsign=%s WHERE id=%s", (pol, show, fe, fc, current_user.id))
    d.commit()
    audit("friend.prefs", target=current_user.username, detail=f"contact {pol}, online shown {show}, found by email {fe}, by call sign {fc}")
    return _resp(True, "Saved.")


def nudge_matrix():
    """Blocks, friendships and disabled accounts feed the Matrix server (ignore lists, who may start a chat, locked accounts): ask for a sync soon."""
    from .matrix import nudge_if_on
    nudge_if_on()


def _me():
    r = get_db().execute("SELECT username, display_name FROM users WHERE id=%s", (current_user.id,)).fetchone()
    return name_of(r)
