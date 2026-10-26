"""The radio bridge on Matrix rooms, against the real Synapse on the test box: the site is a Matrix application service with one bridge bot; a member links a room that is not encrypted to a mesh node, what they write
there is queued for the mesh gateway (and what someone else writes is not), what the gateway hears from the node is said in the room by the bot, an encrypted room is refused, and removing the link makes the bot leave.
(The APRS side shares the same code as the chat bridge and is covered by bridge_test; the Discord mirror is covered by matrixbridge_unit.) Needs `sh tests/deploy_matrix.sh`."""
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
    print("SKIP matrixbridge_test: the Matrix service is not running on the test box (sh tests/deploy_matrix.sh fresh)")
    psql("DELETE FROM settings WHERE key = 'matrix_enabled'")
    raise SystemExit(0)
importlib.reload(mxon)
setting("bridge_mesh_enabled", "1"); setting("bridge_mesh_token", "bridge-test-token"); setting("bridge_links_per_member", "5")
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
    r = s.get(r.headers["Location"], allow_redirects=False)
    loc = r.headers.get("Location", "")
    if not loc.startswith("http://localhost:9/done"):
        m = re.search(r'(http://localhost:9/done[^"\' <]*loginToken=[^"\' <]+)', html.unescape(r.text))
        loc = m.group(1) if m else ""
    tokn = parse_qs(urlparse(loc).query).get("loginToken", [""])[0]
    return requests.post(HS + "/_matrix/client/v3/login", json={"type": "m.login.token", "token": tokn}).json().get("access_token")
TA, TB = sso_login(SA), sso_login(SB)
check("two members can sign in to chat", bool(TA and TB))
def mx(tk, method, path, body=None):
    return requests.request(method, HS + "/_matrix/client/v3" + path, json=body, headers={"Authorization": "Bearer " + tk}, timeout=30)
H = {"X-Bridge-Token": "bridge-test-token"}
def outbox():
    return requests.get(B + "/bridge/mesh/outbox", headers=H).json()["messages"]
for m in outbox():
    requests.post(B + f"/bridge/mesh/outbox/{m['id']}/done", headers=H)
ajax = {"X-Requested-With": "XMLHttpRequest"}
def post(s, path, **data):
    data["csrf_token"] = tok(s, "/my/bridges")
    r = s.post(B + path, data=data, headers=ajax)
    return r.json()
r = post(SA, "/my/bridges/optin", kind="mesh", on="1")
check("the member switches the mesh bridge on for their account", r.get("ok"), r)

room = mx(TA, "POST", "/createRoom", {"name": "Bridge test", "preset": "public_chat", "visibility": "private"}).json()["room_id"]
enc = mx(TA, "POST", "/createRoom", {"name": "Secret", "preset": "private_chat"}).json()["room_id"]
page = SA.get(B + "/my/bridges").text
check("the Radio page lists only the member's own rooms the bridge can read: the encrypted one is not shown at all", "Bridge test" in page and "Secret" not in page.split('id="br-room"')[1].split("</select>")[0], re.sub(r"\s+", " ", page)[:300])
r = post(SA, "/my/bridges/add", kind="mesh", remote="!a1b2c3d4", room_id=enc)
check("an encrypted room is refused, and the reason is plain", not r.get("ok") and "encrypted" in r.get("message", ""), r)
r = post(SA, "/my/bridges/add", kind="mesh", remote="!a1b2c3d4", room_id="!notmine:" + SV)
check("a room the member is not in is refused", not r.get("ok"), r)
r = post(SA, "/my/bridges/add", kind="mesh", remote="!a1b2c3d4", room_id=room)
check("a room that is not encrypted can be linked to a mesh node", r.get("ok"), r)
members = mx(TA, "GET", f"/rooms/{quote(room)}/joined_members").json().get("joined", {})
check("the bridge bot joined the room", f"@arksignal-bridge:{SV}" in members, list(members))
page = SA.get(B + "/my/bridges").text
check("the link is listed with the room's name", "Bridge test" in page and "!a1b2c3d4" in page)
# only the person who made a room can bridge it: not a standing room, not somebody else's room
gen = requests.get(HS + "/_matrix/client/v3/directory/room/" + quote("#general:" + SV)).json().get("room_id", "")
mx(TA, "POST", f"/join/{quote(gen)}")
theirs = mx(TB, "POST", "/createRoom", {"name": "Not yours", "preset": "public_chat", "visibility": "private"}).json()["room_id"]
mx(TA, "POST", f"/join/{quote(theirs)}")
page = SA.get(B + "/my/bridges").text
sel = page.split('id="br-room"')[1].split("</select>")[0]
check("the Radio page offers only rooms the member made (not General, not somebody else's room)", "Bridge test" in sel and "General" not in sel and "Not yours" not in sel, re.sub(r"\s+", " ", sel)[:300])
r = post(SA, "/my/bridges/add", kind="mesh", remote="!bbbb2222", room_id=gen)
check("a standing room cannot be bridged to a mesh node, and the reason is plain", not r.get("ok") and "made a room" in r.get("message", ""), r)
r = post(SA, "/my/bridges/add", kind="mesh", remote="!cccc3333", room_id=theirs)
check("nor can somebody else's room", not r.get("ok") and "made a room" in r.get("message", ""), r)
psql(f"INSERT INTO bridge_links (conv_id, room_id, room_name, kind, remote, created_by) SELECT NULL, '{gen}', 'General', 'mesh', '!dddd4444', id FROM users WHERE username='mxa'")
ok, msg, _h = act(adm, "/admin/matrix/sync", "/admin/matrix")
check("a link made earlier on a room that is not the member's own is switched off at the next sync and logged", psql(f"SELECT enabled FROM bridge_links WHERE room_id='{gen}'") == "f" and "only the person who made a room" in psql("SELECT string_agg(detail, '|') FROM audit_log WHERE action='bridge.unlink'"), psql(f"SELECT enabled FROM bridge_links WHERE room_id='{gen}'"))
psql(f"DELETE FROM bridge_links WHERE room_id='{gen}'")

def send(tk, text):
    return mx(tk, "PUT", f"/rooms/{quote(room)}/send/m.room.message/t{time.time_ns()}", {"msgtype": "m.text", "body": text}).json().get("event_id")
def wait_outbox(text, tries=12):
    for _ in range(tries):
        time.sleep(2)
        got = [m for m in outbox() if m["text"] == text]
        if got:
            return got
    return []
send(TA, "hello from matrix")
got = wait_outbox("hello from matrix")
check("what the member who made the link writes in the room is queued for the mesh node", len(got) == 1 and got[0]["node"] == "!a1b2c3d4", outbox())
mx(TB, "POST", f"/join/{quote(room)}")
send(TB, "someone else writes")
time.sleep(8)
check("what somebody else writes there is never sent out", not [m for m in outbox() if m["text"] == "someone else writes"])
d = requests.post(B + "/bridge/mesh/inbound", json={"node": "!a1b2c3d4", "text": "node says hi", "id": "p1"}, headers=H).json()
check("what the mesh gateway hears from the node is accepted for the link", d.get("delivered") == 1, d)
time.sleep(2)
msgs = mx(TA, "GET", f"/rooms/{quote(room)}/messages?dir=b&limit=10").json().get("chunk", [])
said = [m for m in msgs if m.get("type") == "m.room.message" and "node says hi" in m["content"].get("body", "")]
check("and the bot says it in the room, naming the node", said and said[0]["sender"] == f"@arksignal-bridge:{SV}" and "MESH !a1b2c3d4" in said[0]["content"]["body"], [m["content"] for m in msgs[:4]])
d = requests.post(B + "/bridge/mesh/inbound", json={"node": "!a1b2c3d4", "text": "node says hi", "id": "p1"}, headers=H).json()
check("a repeat of the same packet is dropped", d.get("delivered") == 0, d)
lid = psql("SELECT id FROM bridge_links WHERE room_id IS NOT NULL ORDER BY id DESC LIMIT 1")
r = post(SA, f"/my/bridges/{lid}/delete")
time.sleep(2)
members = mx(TA, "GET", f"/rooms/{quote(room)}/joined_members").json().get("joined", {})
check("removing the link makes the bot leave the room", r.get("ok") and f"@arksignal-bridge:{SV}" not in members, (r, list(members)))
# the Discord mirror of a standing room (the sending itself is covered by matrixmirror_test)
act(adm, "/admin/matrix/sync", "/admin/matrix")
ok, msg, _h = act(adm, "/admin/matrix/mirror", "/admin/matrix", alias="general", webhook="https://discord.com/api/webhooks/1/abcdefg")
check("an administrator can set a Discord mirror for a standing room", ok, msg)
gid = requests.get(HS + "/_matrix/client/v3/directory/room/" + quote("#general:" + SV)).json().get("room_id", "")
mx(TA, "POST", f"/join/{quote(gid)}")
check("the bridge joined that room and its address is stored sealed", f"@arksignal-bridge:{SV}" in mx(TA, "GET", f"/rooms/{quote(gid)}/joined_members").json().get("joined", {}) and psql("SELECT webhook FROM matrix_mirrors WHERE alias='general'").startswith("enc1:"))
check("the address is never shown again", "discord.com/api/webhooks" not in adm.get(B + "/admin/matrix").text)
ok, msg, _h = act(adm, "/admin/matrix/mirror/general/delete", "/admin/matrix")
time.sleep(2)
check("removing the mirror makes the bridge leave", ok and f"@arksignal-bridge:{SV}" not in mx(TA, "GET", f"/rooms/{quote(gid)}/joined_members").json().get("joined", {}), msg)
check("the link, the sends and the receipts are in the audit log", all(a in psql("SELECT string_agg(DISTINCT action, ',') FROM audit_log WHERE action LIKE 'bridge.%'") for a in ("bridge.link", "bridge.send", "bridge.receive", "bridge.unlink")))
psql("DELETE FROM users WHERE username IN ('mxa','mxb')")
psql("DELETE FROM settings WHERE key IN ('matrix_enabled','bridge_mesh_enabled','bridge_mesh_token','bridge_links_per_member')")
print("ALL PASS" if ok_all else "SOME FAILED")
