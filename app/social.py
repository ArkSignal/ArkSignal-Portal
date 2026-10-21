"""What chat, messages and friends lists share: names, who is a friend, blocking, the contact rule, who is online and the unread counts.

A member chooses who may contact them (My Account > Friends): everyone, friends only (a stranger can only send a friend request) or nobody (no new chats or messages, not even friend requests; people who are already
friends can still write). A member can block another: the blocked member cannot start a chat, send a friend request or reach their inbox, and is not told. Moderators (the Moderate Chat and Messages permission) are
exempt from the contact rule so that they can always reach a member about a report."""
import re
from datetime import datetime, timedelta, timezone

from flask_login import current_user

from .db import get_db
from .settings import cfg, cfg_int

POLICIES = (("anyone", "Everyone"), ("friends", "Friends only"), ("nobody", "Nobody new"))


def name_of(row):
    """What a person is called: their display name, or their user name."""
    return row["display_name"] or row["username"]


def is_mod():
    return current_user.is_authenticated and current_user.has_permission("moderate_chat")


def muted(user_id, scope):
    return get_db().execute("SELECT reason, until FROM chat_mutes WHERE user_id=%s AND scope=%s AND (until IS NULL OR until > now())", (user_id, scope)).fetchone()


def pair(a, b):
    return (a, b) if a < b else (b, a)


def is_friend(a, b):
    x, y = pair(a, b)
    return get_db().execute("SELECT 1 FROM friendships WHERE user_a=%s AND user_b=%s AND status='accepted'", (x, y)).fetchone() is not None


def friend_ids(user_id):
    rows = get_db().execute("SELECT CASE WHEN user_a=%s THEN user_b ELSE user_a END AS f FROM friendships WHERE status='accepted' AND (user_a=%s OR user_b=%s)", (user_id, user_id, user_id)).fetchall()
    return {r["f"] for r in rows}


def blocked(by_id, who_id):
    """True when `by_id` has blocked `who_id`."""
    return get_db().execute("SELECT 1 FROM pm_blocks WHERE blocker_id=%s AND blocked_id=%s", (by_id, who_id)).fetchone() is not None


def either_blocked(a, b):
    return blocked(a, b) or blocked(b, a)


def find_user(text):
    """The member a user name, call sign or whole email address names, or None; disabled accounts are never found. A call sign that more than one member has finds nobody (use the user name). Members who switched off
    being found by email or by call sign are not found that way (moderators still find them)."""
    text = (text or "").strip().lstrip("@")
    if not text:
        return None
    d = get_db()
    cols = "id, username, display_name, contact_policy, show_online, disabled"
    u = d.execute(f"SELECT {cols} FROM users WHERE lower(username)=lower(%s)", (text,)).fetchone()
    if u is None and "@" in text and cfg("people_search_email") == "1":
        u = d.execute(f"SELECT {cols} FROM users WHERE lower(email)=lower(%s) AND (find_by_email OR %s)", (text, is_mod())).fetchone()
    if u is None and cfg("people_search_callsign") == "1" and CALL_RE.match(text.upper()):
        rows = d.execute(f"SELECT {cols} FROM users WHERE upper(callsign)=upper(%s) AND NOT disabled AND (find_by_callsign OR %s) LIMIT 2", (text, is_mod())).fetchall()
        u = rows[0] if len(rows) == 1 else None
    return None if u is None or u["disabled"] else u


CALL_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z](-[0-9]{1,2})?$")   # a ham call sign, optionally with an SSID


_search_hits = {}   # member id -> (minute, count): a small per-worker limit on people searches


def search_people(q):
    """Members matching what a member typed, for the list under the search box: by user name or display name (anywhere in it), by call sign, and one member for a whole email address typed exactly.
    Never the member themselves, disabled accounts, anyone blocked either way, or anyone who takes no new contact. No email address is ever returned. Returns (list, refused-for-rate)."""
    import time
    me = current_user.id
    minute = int(time.time() // 60)
    last = _search_hits.get(me)
    n = (last[1] + 1) if last and last[0] == minute else 1
    _search_hits[me] = (minute, n)
    if n > max(10, cfg_int("people_search_per_minute")):
        return [], True
    q = (q or "").strip().lstrip("@")[:80]
    if len(q) < 2:
        return [], False
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    d = get_db()
    cols = "id, username, display_name, callsign, contact_policy, find_by_callsign"
    rows = list(d.execute(f"SELECT {cols}, 'name' AS how FROM users WHERE NOT disabled AND (username ILIKE %s OR display_name ILIKE %s) ORDER BY (lower(username)=lower(%s)) DESC, lower(username) LIMIT 20", (like, like, q)).fetchall())
    if cfg("people_search_callsign") == "1":
        rows += d.execute(f"SELECT {cols}, 'callsign' AS how FROM users WHERE NOT disabled AND (find_by_callsign OR %s) AND callsign ILIKE %s ORDER BY lower(username) LIMIT 20", (is_mod(), like)).fetchall()
    if "@" in q and cfg("people_search_email") == "1":
        rows += d.execute(f"SELECT {cols}, 'email' AS how FROM users WHERE NOT disabled AND (find_by_email OR %s) AND lower(email)=lower(%s)", (is_mod(), q)).fetchall()
    out, seen = [], set()
    for r in rows:
        if r["id"] in seen or r["id"] == me or r["contact_policy"] == "nobody" or either_blocked(me, r["id"]):
            continue
        seen.add(r["id"])
        out.append({"username": r["username"], "display_name": r["display_name"] or "",
                    "callsign": (r["callsign"] or "") if (r["how"] == "callsign" or r["find_by_callsign"]) else "",
                    "how": r["how"], "friend": is_friend(me, r["id"])})
        if len(out) >= max(3, cfg_int("people_search_max")):
            break
    return out, False


def may_contact(target, kind="chat"):
    """(allowed, plain reason) for the signed-in member contacting `target` (a users row from find_user). kind is chat, mail or friend."""
    me = current_user.id
    if target["id"] == me:
        return False, "You cannot do that with yourself."
    if blocked(target["id"], me) or blocked(me, target["id"]):
        return False, "You cannot contact that member."
    if is_mod() or is_friend(me, target["id"]):
        return True, ""
    pol = target["contact_policy"]
    if kind == "friend":
        return (False, "That member is not taking new requests.") if pol == "nobody" else (True, "")
    if pol == "nobody":
        return False, "That member is not taking new chats or messages."
    if pol == "friends":
        return False, "That member only hears from friends. Send a friend request first."
    return True, ""


def account_age_days():
    c = get_db().execute("SELECT created_at FROM users WHERE id=%s", (current_user.id,)).fetchone()
    return (datetime.now(timezone.utc) - c["created_at"]).days if c and c["created_at"] else 9999


# ------------------------------------------------------------------ who is online

def touch(user_id):
    """Note that this member's browser is in touch with the site right now."""
    d = get_db()
    d.execute("INSERT INTO chat_presence (user_id, seen_at) VALUES (%s, now()) ON CONFLICT (user_id) DO UPDATE SET seen_at = now()", (user_id,))
    d.commit()


def online_ids(candidates):
    """Which of these member ids are online and have not hidden it (a member always sees themselves)."""
    ids = list(candidates)
    if not ids:
        return set()
    rows = get_db().execute("SELECT p.user_id FROM chat_presence p JOIN users u ON u.id = p.user_id WHERE p.user_id = ANY(%s) AND u.show_online AND p.seen_at > now() - make_interval(secs => %s)",
                            (ids, max(30, cfg_int("chat_online_seconds")))).fetchall()
    return {r["user_id"] for r in rows}


# ------------------------------------------------------------------ the numbers in the menu

def chat_unread(user_id):
    return get_db().execute("SELECT COUNT(*) AS n FROM conv_messages m JOIN conv_members cm ON cm.conv_id = m.conv_id AND cm.user_id = %s WHERE m.id > cm.last_read_id AND m.user_id <> %s AND m.deleted_at IS NULL",
                            (user_id, user_id)).fetchone()["n"]


def mail_unread(user_id):
    return get_db().execute("SELECT COUNT(*) AS n FROM mail_recipients WHERE user_id=%s AND folder='inbox' AND delivered AND read_at IS NULL", (user_id,)).fetchone()["n"]


def friend_requests(user_id):
    return get_db().execute("SELECT COUNT(*) AS n FROM friendships WHERE status='pending' AND requested_by <> %s AND (user_a=%s OR user_b=%s)", (user_id, user_id, user_id)).fetchone()["n"]
