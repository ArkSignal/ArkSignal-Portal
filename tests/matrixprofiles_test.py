"""Every member has a chat account before they open chat, so a search finds them: by user name, and by call sign when they allowed being found by it; a chat with a member who never opened chat works at once; and a member whose account was made ahead of time can still sign in through the site. Against the real Synapse on the test box. Needs `sh tests/deploy_matrix.sh`."""
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

setting("matrix_sync_seconds", "3000")   # the background pass stays out of the way: only the passes this test asks for
psql("DELETE FROM users WHERE username IN ('mpa','mpb','mpc')")
for n in ("mpa", "mpb", "mpc"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
psql("UPDATE users SET display_name='Pat Example', callsign='KG5MPA', find_by_callsign=TRUE WHERE username='mpa'")
psql("UPDATE users SET display_name='Quinn Example', callsign='KG5MPB', find_by_callsign=FALSE WHERE username='mpb'")
psql("UPDATE users SET display_name='Robin Example', callsign='KG5MPC', find_by_callsign=TRUE WHERE username='mpc'")
adm = sess("marty", PW)
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
TM = sso_login(adm)
check("the administrator signs in to chat through the site", bool(TM))
def mx(tk, method, path, body=None):
    return requests.request(method, HS + "/_matrix/client/v3" + path, json=body, headers={"Authorization": "Bearer " + tk}, timeout=30)
def find(term):
    r = mx(TM, "POST", "/user_directory/search", {"search_term": term, "limit": 20})
    return {x["user_id"]: x.get("display_name", "") for x in r.json().get("results", [])} if r.status_code == 200 else {}
ok, msg, _h = act(adm, "/admin/matrix/sync", "/admin/matrix")
check("Sync Now works and counts the accounts it made", ok and "accounts made or renamed" in msg, msg)
time.sleep(2)
mpa, mpb, mpc = "@mpa:" + SV, "@mpb:" + SV, "@mpc:" + SV
check("members who never opened chat already have an account (a search by user name finds them)", mpa in find("mpa") and mpb in find("mpb") and mpc in find("mpc"), (find("mpa"), find("mpb")))
check("a member who allowed being found by call sign carries it after the name, and a search for it finds them", find("KG5MPA").get(mpa) == "Pat Example (KG5MPA)", find("KG5MPA"))
check("a member who did not allow it carries only their name, and the call sign finds nobody", find("Quinn Example").get(mpb) == "Quinn Example" and mpb not in find("KG5MPB"), (find("Quinn"), find("KG5MPB")))
check("a search for the first letters of a call sign works too", mpa in find("KG5MP") and mpc in find("KG5MP") and mpb not in find("KG5MP"), find("KG5MP"))
psql("UPDATE users SET find_by_callsign=FALSE WHERE username='mpa'")
ok, msg, _h = act(adm, "/admin/matrix/sync", "/admin/matrix"); time.sleep(2)
check("switching the setting off takes the call sign off the chat name at the next sync", find("Pat Example").get(mpa) == "Pat Example" and mpa not in find("KG5MPA"), find("Pat"))
psql("UPDATE users SET find_by_callsign=TRUE WHERE username='mpa'")
ok, msg, _h = act(adm, "/admin/matrix/sync", "/admin/matrix"); time.sleep(2)
check("and on again puts it back", find("KG5MPA").get(mpa) == "Pat Example (KG5MPA)")
# a chat with someone whose account was never made (registered after the last sync): the Chat button makes it at once
import secrets
R = secrets.token_hex(3)   # new names every run: the chat server keeps accounts between runs
MPD, MPE = "mpd" + R, "mpe" + R
psql(f"INSERT INTO users (username, email, password_hash, display_name, callsign, find_by_callsign) SELECT '{MPD}', '{MPD}@example.com', password_hash, 'Dana Example', 'KG5MPD', TRUE FROM users WHERE username='mpc'")
check("a member made after the last sync has no chat account yet", mx(TM, "GET", "/profile/" + quote("@" + MPD + ":" + SV)).status_code == 404)
r = adm.get(B + "/chat/?dm=" + MPD)
check("the Chat button makes their account first, so the chat can start", r.status_code == 200 and mx(TM, "GET", "/profile/" + quote("@" + MPD + ":" + SV)).json().get("displayname") == "Dana Example (KG5MPD)", mx(TM, "GET", "/profile/" + quote("@" + MPD + ":" + SV)).text)
# a member whose account was made ahead of time still signs in through the site and lands on the same account
SA = sess("mpa", "mpapassword99")
TA = sso_login(SA)
who = mx(TA, "GET", "/account/whoami").json().get("user_id") if TA else None
check("a member whose account was made ahead of time signs in through the site and is that same account", who == mpa, who)
check("their chat name is still the one the site gave", mx(TA, "GET", "/profile/" + quote(mpa)).json().get("displayname") == "Pat Example (KG5MPA)" if TA else False)
# the switch
setting("matrix_precreate_members", "0")
psql(f"INSERT INTO users (username, email, password_hash, display_name) SELECT '{MPE}', '{MPE}@example.com', password_hash, 'Eli Example' FROM users WHERE username='mpc'")
ok, msg, _h = act(adm, "/admin/matrix/sync", "/admin/matrix"); time.sleep(2)
check("with the setting off no account is made ahead of time", mx(TM, "GET", "/profile/" + quote("@" + MPE + ":" + SV)).status_code == 404)
setting("matrix_precreate_members", "1")
psql("DELETE FROM users WHERE username IN ('mpa','mpb','mpc')")
psql(f"DELETE FROM users WHERE username IN ('{MPD}','{MPE}')")
psql("DELETE FROM settings WHERE key IN ('matrix_precreate_members', 'matrix_sync_seconds')")
print("ALL PASS" if ok_all else "SOME FAILED")
