"""Friends lists: requests, accepting, declining, taking back, removing, blocking, the contact rule (everyone, friends only, nobody), online status, limits, audit log and Feature Access. Runs against the test portal."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def unset(*ks):
    for k in ks:
        psql(f"DELETE FROM settings WHERE key = '{k}'")
KEYS = ("friends_enabled", "friends_max", "friends_requests_per_day", "access_friends", "chat_online_seconds")
unset(*KEYS)
def wipe():
    for t in ("friendships", "pm_blocks", "chat_presence"):
        psql(f"DELETE FROM {t}")
    psql("UPDATE users SET contact_policy = 'anyone', show_online = TRUE")
wipe()
adm = sess("marty", PW); me = sess("plain1", "plainpassword1"); other = sess("claimer1", "claimpassword1"); third = sess("claimer2", "claimpassword1"); anon = requests.Session()
def csrf(s):
    return re.search(r'var csrf = "([^"]+)"', s.get(B + "/my/friends").text).group(1)
def post(s, path, **data):
    return s.post(B + path, data=dict(data, csrf_token=csrf(s)))

check("friends need a sign-in", anon.get(B + "/my/friends", allow_redirects=False).status_code in (302, 401, 403))
pg = me.get(B + "/my/friends")
check("a member sees the friends page with its sections", pg.status_code == 200 and all(x in pg.text for x in ("Friends (0", "Find a Member", "Who Can Contact Me", "Blocked Members")))
check("the menus list Friends", 'href="/my/friends"' in me.get(B + "/").text and 'href="/my/friends"' in pg.text)
check("a name that does not exist and yourself are refused plainly", "No member" in post(me, "/my/friends/request", to="nobodyatall").json()["message"] and "yourself" in post(me, "/my/friends/request", to="plain1").json()["message"])
r = post(me, "/my/friends/request", to="claimer1").json()
check("a member sends a friend request", r["ok"] and psql("select status from friendships") == "pending" and psql("select count(*) from friendships") == "1", r)
check("sending the same request again says so", "already sent" in post(me, "/my/friends/request", to="claimer1").json()["message"])
op = other.get(B + "/my/friends").text
check("the other member sees it under requests with Accept and Decline, and a count in the menu", "Requests for You (1)" in op and "Accept" in op and "Decline" in op and 'class="msgbadge"' in op)
check("the menu badge numbers are served", other.get(B + "/chat/unread.json").json()["friends"] == 1 and me.get(B + "/chat/unread.json").json()["friends"] == 0)
check("the sender cannot accept their own request", post(me, "/my/friends/claimer1/accept").status_code == 404)
r = post(other, "/my/friends/plain1/accept").json()
check("the other member accepts and they are friends", r["ok"] and psql("select status from friendships") == "accepted" and "claimer1" in me.get(B + "/my/friends").text and "Friends (1" in me.get(B + "/my/friends").text, r)
me.get(B + "/chat/unread.json")
check("online status: the friend who just visited is online", "plain1" in other.get(B + "/my/friends/online.json").json()["online"])
setting("chat_online_seconds", "30")
psql("UPDATE chat_presence SET seen_at = now() - interval '5 minutes'")
check("a friend not seen for a while is offline", "plain1" not in other.get(B + "/my/friends/online.json").json()["online"])
unset("chat_online_seconds")
r = post(me, "/my/friends/prefs", contact_policy="anyone").json()   # show_online left out = off
me.get(B + "/chat/unread.json")
check("a member who hides their online status is not shown online", r["ok"] and "plain1" not in other.get(B + "/my/friends/online.json").json()["online"])
post(me, "/my/friends/prefs", contact_policy="anyone", show_online="1")
check("and shown again when they allow it", "plain1" in other.get(B + "/my/friends/online.json").json()["online"])
r = post(other, "/my/friends/plain1/remove").json()
check("a friend can be removed (no notice), and then they are not friends", r["ok"] and psql("select count(*) from friendships") == "0")
post(me, "/my/friends/request", to="claimer1")
r = post(other, "/my/friends/plain1/decline").json()
check("a request can be declined and the sender is not told", r["ok"] and psql("select count(*) from friendships") == "0" and "declined" not in me.get(B + "/my/friends").text.lower().replace("they are not told", ""))
post(me, "/my/friends/request", to="claimer1")
check("a sender can take a request back", post(me, "/my/friends/claimer1/cancel").json()["ok"] and psql("select count(*) from friendships") == "0")
post(me, "/my/friends/request", to="claimer1")
r = post(other, "/my/friends/request", to="plain1").json()
check("when each asks the other, they become friends at once", r["ok"] and psql("select status from friendships") == "accepted" and "now friends" in r["message"])
post(me, "/my/friends/claimer1/remove")

# the contact rule
r = post(other, "/my/friends/prefs", contact_policy="friends")
check("a member can choose friends-only contact", r.json()["ok"] and psql("select contact_policy from users where username = 'claimer1'") == "friends")
check("a bad choice is refused", not post(other, "/my/friends/prefs", contact_policy="everybody").json()["ok"])
r = post(me, "/chat/new", to="claimer1").json()
check("a stranger cannot start a chat with a friends-only member and is told to send a request", not r["ok"] and "friend request" in r["message"], r)
check("but a friend request is allowed", post(me, "/my/friends/request", to="claimer1").json()["ok"])
post(other, "/my/friends/plain1/accept")
check("once friends, they can chat", post(me, "/chat/new", to="claimer1").json()["ok"])
post(me, "/my/friends/claimer1/remove")
post(other, "/my/friends/prefs", contact_policy="nobody")
check("with nobody-new, even a friend request is refused", "not taking new requests" in post(me, "/my/friends/request", to="claimer1").json()["message"])
check("a moderator can still reach them", post(adm, "/chat/new", to="claimer1").json()["ok"])
post(other, "/my/friends/prefs", contact_policy="anyone")

# blocking
post(me, "/my/friends/request", to="claimer1"); post(other, "/my/friends/plain1/accept")
r = post(other, "/my/friends/plain1/block").json()
check("blocking ends the friendship and is private", r["ok"] and psql("select count(*) from friendships") == "0" and psql("select count(*) from pm_blocks") == "1")
check("the blocked member cannot send a request or start a chat, with a plain message that does not say they are blocked", "cannot contact" in post(me, "/my/friends/request", to="claimer1").json()["message"] and not post(me, "/chat/new", to="claimer1").json()["ok"])
check("the blocker's page lists the block and can lift it", "claimer1" not in other.get(B + "/my/friends").text.split("Blocked Members")[0] and "plain1" in other.get(B + "/my/friends").text.split("Blocked Members")[1])
post(other, "/my/friends/plain1/unblock")
check("unblocking works", psql("select count(*) from pm_blocks") == "0" and post(me, "/my/friends/request", to="claimer1").json()["ok"])
wipe()

# limits
setting("friends_requests_per_day", "1")
post(me, "/my/friends/request", to="claimer1")
check("the requests-per-day limit stops more", "a lot of friend requests" in post(me, "/my/friends/request", to="claimer2").json()["message"])
unset("friends_requests_per_day"); wipe()
setting("friends_max", "1")
post(me, "/my/friends/request", to="claimer1"); post(other, "/my/friends/plain1/accept")
check("a member with the most friends cannot add more", "1 friends at most" in post(me, "/my/friends/request", to="claimer2").json()["message"])
unset("friends_max"); wipe()

# audit, settings, feature access
au = adm.get(B + "/admin/audit").text
check("every friends action is in the audit log", all(x in au for x in ("friend.request", "friend.accept", "friend.decline", "friend.cancel", "friend.remove", "friend.block", "friend.unblock", "friend.prefs")))
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings, with Feature Access", all(x in st for x in ("Friends Lists", "Friends: Most Friends per Member", "Friends: Requests a Member Can Send per Day", "Who Can See Friends Lists")))
setting("access_friends", "roles")
check("limited to roles, a member without the permission sees none of it", me.get(B + "/my/friends").status_code == 404 and adm.get(B + "/my/friends").status_code == 200)
unset("access_friends")
setting("friends_enabled", "0")
check("switched off, the page and the menu link are gone", me.get(B + "/my/friends").status_code == 404 and 'href="/my/friends"' not in me.get(B + "/").text)
unset(*KEYS)
wipe()
print("ALL PASS" if ok_all else "SOME FAILED")
