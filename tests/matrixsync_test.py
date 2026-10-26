"""Matrix kept in step with the site, against the real Synapse on the test box: the standing rooms (public, unencrypted, moderators from the site), who may start rooms and chats (a Synapse module reading the
site's policy), blocks becoming ignore lists, disabled and locked accounts signed out and refused, and reports from Element handled on Admin > Matrix Chat. Needs `sh tests/deploy_matrix.sh`."""
import html, importlib, json, os, re, sys, time, requests
from urllib.parse import parse_qs, quote, urlparse
os.environ["TEST_HOST"] = "test-box.invalid"
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mxon
psql = mxon.psql
HS = "http://test-box.invalid:18008"
SV = "matrix-test.local"
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
adm = sess("marty", PW)
psql("INSERT INTO settings (key, value) VALUES ('matrix_enabled', '1') ON CONFLICT (key) DO UPDATE SET value = '1'")
if "service not answering" in adm.get(B + "/admin/matrix").text:
    print("SKIP matrixsync_test: the Matrix service is not running on the test box (sh tests/deploy_matrix.sh fresh)")
    psql("DELETE FROM settings WHERE key = 'matrix_enabled'")
    raise SystemExit(0)
importlib.reload(mxon)
for k in ("matrix_create_rooms", "matrix_invite"):
    setting(k, "everyone")
adm = sess("marty", PW)
ok, msg, _h = act(adm, "/admin/matrix/apply", "/admin/matrix")
for _ in range(60):
    try:
        if requests.get(HS + "/health", timeout=3).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(3)
time.sleep(5)
act(adm, "/admin/matrix/sync", "/admin/matrix")   # accounts locked by an earlier run or suite are unlocked before anyone signs in
# two members of our own, so nothing here touches anyone else's chat
psql("DELETE FROM matrix_report_state")   # the test server was wiped, so its report numbers start again
psql("DELETE FROM users WHERE username IN ('mxa','mxb')")
for n in ("mxa", "mxb"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
SA, SB = sess("mxa", "mxapassword99"), sess("mxb", "mxbpassword99")
def sso_login(s):
    r = s.get(HS + "/_matrix/client/v3/login/sso/redirect/oidc-arksignal", params={"redirectUrl": "http://localhost:9/done"}, allow_redirects=False)
    for c in r.headers.get("Set-Cookie", "").split(", "):
        if "=" in c.split(";")[0] and "oidc" in c.split("=")[0]:
            s.cookies.set(c.split("=")[0].strip(), c.split(";")[0].split("=", 1)[1], domain=urlparse(HS).hostname, path="/")
    r = s.get(r.headers["Location"], allow_redirects=False)
    if r.status_code != 302 or not r.headers["Location"].startswith(HS):
        return None
    r = s.get(r.headers["Location"], allow_redirects=False)
    loc = r.headers.get("Location", "")
    if not loc.startswith("http://localhost:9/done"):
        m = re.search(r'(http://localhost:9/done[^"\' <]*loginToken=[^"\' <]+)', html.unescape(r.text))
        loc = m.group(1) if m else ""
    tokn = parse_qs(urlparse(loc).query).get("loginToken", [""])[0]
    if not tokn:
        return None
    j = requests.post(HS + "/_matrix/client/v3/login", json={"type": "m.login.token", "token": tokn}).json()
    return j.get("access_token")
TM, TA, TB = sso_login(adm), sso_login(SA), sso_login(SB)
check("three members can sign in to chat through the site", all([TM, TA, TB]))
def mx(tk, method, path, body=None):
    return requests.request(method, HS + "/_matrix/client/v3" + path, json=body, headers={"Authorization": "Bearer " + tk}, timeout=30)
def sync():
    ok, msg, _h = act(adm, "/admin/matrix/sync", "/admin/matrix")
    return ok, msg
ok, msg = sync()
check("Sync Now works and says what it did", ok and "Synced" in msg, msg)

# standing rooms
alias = quote("#general:" + SV)
r = requests.get(HS + "/_matrix/client/v3/directory/room/" + alias)
rid = r.json().get("room_id", "")
check("the standing rooms exist with the names in Settings", r.status_code == 200 and rid.startswith("!"), r.text)
mx(TM, "POST", f"/join/{alias}")
pl = mx(TM, "GET", f"/rooms/{quote(rid)}/state/m.room.power_levels").json() if rid else {}
check("a person holding Moderate Chat is a moderator of the standing room", pl.get("users", {}).get(f"@marty:{SV}") == 50, pl.get("users"))
check("the standing room is public and not encrypted (so it can be moderated and bridged)", mx(TM, "GET", f"/rooms/{quote(rid)}/state/m.room.encryption").status_code == 404 and mx(TM, "GET", f"/rooms/{quote(rid)}/state/m.room.join_rules").json().get("join_rule") == "public")
check("a member can join it", mx(TA, "POST", f"/join/{alias}").status_code == 200)

# who may start a room
setting("matrix_create_rooms", "moderators"); sync()
time.sleep(1)
check("moderators only: an ordinary member cannot start a room", mx(TA, "POST", "/createRoom", {"name": "x"}).status_code == 403)
check("moderators only: a moderator can", mx(TM, "POST", "/createRoom", {"name": "mod room"}).status_code == 200)
setting("matrix_create_rooms", "everyone"); sync(); time.sleep(1)
check("everyone: an ordinary member can again", mx(TA, "POST", "/createRoom", {"name": "a room"}).status_code == 200)

# who may start a chat with whom
setting("matrix_invite", "friends"); sync(); time.sleep(1)
room = mx(TA, "POST", "/createRoom", {"name": "chat"}).json()["room_id"]
check("friends only: inviting someone who is not a friend is refused", mx(TA, "POST", f"/rooms/{quote(room)}/invite", {"user_id": f"@mxb:{SV}"}).status_code == 403)
psql("INSERT INTO friendships (user_a, user_b, status, requested_by) SELECT a.id, b.id, 'accepted', a.id FROM users a, users b WHERE a.username='mxa' AND b.username='mxb' ON CONFLICT DO NOTHING")
sync(); time.sleep(1)
_inv = mx(TA, "POST", f"/rooms/{quote(room)}/invite", {"user_id": f"@mxb:{SV}"})
check("friends only: a friend can be invited", _inv.status_code == 200, (_inv.status_code, _inv.text[:200], mx(TA, "GET", "/account/whoami").text))
setting("matrix_invite", "moderators"); sync(); time.sleep(1)
room2 = mx(TA, "POST", "/createRoom", {"name": "chat2"}).json()["room_id"]
check("moderators only: even a friend cannot be invited by an ordinary member", mx(TA, "POST", f"/rooms/{quote(room2)}/invite", {"user_id": f"@mxb:{SV}"}).status_code == 403)
check("moderators can always invite", mx(TM, "POST", f"/rooms/{quote(mx(TM, 'POST', '/createRoom', {'name': 'm'}).json()['room_id'])}/invite", {"user_id": f"@mxb:{SV}"}).status_code == 200)
setting("matrix_invite", "everyone"); sync()

# blocks become ignore lists
psql("DELETE FROM friendships WHERE user_a IN (SELECT id FROM users WHERE username IN ('mxa','mxb'))")
psql("INSERT INTO pm_blocks (blocker_id, blocked_id) SELECT a.id, b.id FROM users a, users b WHERE a.username='mxa' AND b.username='mxb' ON CONFLICT DO NOTHING")
ok, msg = sync()
ign = mx(TA, "GET", f"/user/@mxa:{SV}/account_data/m.ignored_user_list").json()
check("a member's block is their Matrix ignore list", f"@mxb:{SV}" in ign.get("ignored_users", {}), (ign, msg))
psql("DELETE FROM pm_blocks WHERE blocker_id IN (SELECT id FROM users WHERE username='mxa')")
sync()
ign = mx(TA, "GET", f"/user/@mxa:{SV}/account_data/m.ignored_user_list").json()
check("and lifting the block lifts it", not ign.get("ignored_users"), ign)

# a block made on the site reaches Matrix by itself (no Sync Now)
r = SA.post(B + "/my/friends/mxb/block", data={"csrf_token": tok(SA, "/my/friends")}, headers={"X-Requested-With": "XMLHttpRequest"})
seen = False
for _ in range(20):
    time.sleep(3)
    ign = mx(TA, "GET", f"/user/@mxa:{SV}/account_data/m.ignored_user_list").json()
    if f"@mxb:{SV}" in ign.get("ignored_users", {}):
        seen = True
        break
check("blocking someone on the site reaches their Matrix ignore list within a minute, with no one pressing Sync Now", r.status_code == 200 and seen, (r.status_code, ign))
SA.post(B + "/my/friends/mxb/unblock", data={"csrf_token": tok(SA, "/my/friends")}, headers={"X-Requested-With": "XMLHttpRequest"})
sync()

# locked and disabled accounts
ok, msg, _h = act(adm, "/admin/matrix/lock", "/admin/matrix", username="mxb", hours="1", reason="test")
check("a moderator can lock a chat account", ok, msg)
time.sleep(1)
w = mx(TB, "GET", "/account/whoami")
check("a locked account is signed out of chat at once", w.status_code in (401, 403) and "LOCKED" in w.text.upper(), w.text)
check("and cannot sign in to chat again (the site refuses)", sso_login(SB) is None)
check("their site account still works", requests.get(B + "/my/account", cookies=SB.cookies, allow_redirects=False).status_code == 200)
lid = psql("SELECT id FROM matrix_locks WHERE NOT lifted ORDER BY id DESC LIMIT 1")
ok, msg, _h = act(adm, f"/admin/matrix/unlock/{lid}", "/admin/matrix")
time.sleep(1)
check("lifting the lock gives chat back", ok and mx(TB, "GET", "/account/whoami").status_code == 200 and sso_login(SB), msg)
psql("UPDATE users SET disabled = true WHERE username = 'mxb'")
sync(); time.sleep(1)
check("a disabled site account is locked out of chat by the sync", mx(TB, "GET", "/account/whoami").status_code in (401, 403))
psql("UPDATE users SET disabled = false WHERE username = 'mxb'")
sync(); time.sleep(1)
check("and enabling it again gives chat back", mx(TB, "GET", "/account/whoami").status_code == 200)

# reports from Element
sent = mx(TA, "PUT", f"/rooms/{quote(rid)}/send/m.room.message/t{int(time.time())}", {"msgtype": "m.text", "body": "something rude"}).json()
ev = sent.get("event_id", "")
mx(TB, "POST", f"/join/{alias}")
rep = mx(TB, "POST", f"/rooms/{quote(rid)}/report/{quote(ev)}", {"reason": "rude", "score": -100})
check("a member can report a message in Element", rep.status_code == 200, rep.text)
page = adm.get(B + "/admin/matrix").text
check("the report is on Admin > Matrix Chat with who wrote it and the reason", "rude" in page and "mxa" in page and "Remove the Message" in page)
rid_rep = re.search(r"/admin/matrix/reports/(\d+)/delete", page)
ok, msg, _h = act(adm, f"/admin/matrix/reports/{rid_rep.group(1)}/delete", "/admin/matrix") if rid_rep else (False, "no report", "")
check("a moderator can remove the reported message and close the report", ok, msg)
got = mx(TB, "GET", f"/rooms/{quote(rid)}/event/{quote(ev)}").json()
check("the message is gone for everyone", not got.get("content", {}).get("body"), got)
check("and the report no longer waits", "/admin/matrix/reports/" + (rid_rep.group(1) if rid_rep else "0") + "/delete" not in adm.get(B + "/admin/matrix").text)
check("the lock, the removal and the sync are in the audit log", all(a in psql("SELECT string_agg(DISTINCT action, ',') FROM audit_log WHERE action LIKE 'matrix.%'") for a in ("matrix.lock", "matrix.unlock", "matrix.report_delete", "matrix.sync")))
psql("DELETE FROM matrix_report_state")   # the test server was wiped, so its report numbers start again
psql("DELETE FROM users WHERE username IN ('mxa','mxb')")
psql("DELETE FROM settings WHERE key IN ('matrix_enabled','matrix_create_rooms','matrix_invite')")
print("ALL PASS" if ok_all else "SOME FAILED")
