"""Radio bridges: a chat linked to an APRS call sign or a mesh node. Out: the member who made the link has what they write sent as an APRS message from their own call sign (a fake APRS-IS server in the portal's
container plays the network); others' messages are never sent; ASCII, length and rate limits hold. In: APRS messages to the member's call sign from the linked call sign are added to the chat (repeats dropped,
others ignored). Mesh: opt-in, a queue and two token-guarded addresses for the gateway. Opt-in, consent, settings, audit and Feature Access are checked. Needs the test box."""
import json, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
SSH = lambda cmd: subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True)
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def psql(sql):
    return SSH(f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def unset(*ks):
    for k in ks:
        psql(f"DELETE FROM settings WHERE key = '{k}'")
def inbox(local, name):
    subprocess.run(["scp", "-q", local, f"nastemp:adsb-portal/{name}"], check=True, capture_output=True)
    SSH(f"{DK} cp ~/adsb-portal/{name} adsb-portal:/tmp/{name}")
KEYS = ("bridge_aprs_enabled", "bridge_mesh_enabled", "bridge_aprs_server", "bridge_aprs_rate_per_hour", "bridge_links_per_member", "bridge_mesh_token", "bridge_aprs_max_text", "access_bridges")

FAKE = r'''
import socket, threading
LOG = "/tmp/bridge.log"
def handle(c):
    f = c.makefile("rwb", buffering=0)
    f.write(b"# fake aprs server\r\n")
    login = f.readline().decode().strip()
    call = login.split()[1] if login.startswith("user ") else "?"
    f.write(("# logresp %s %s, server FAKE\r\n" % (call, "unverified" if call.startswith("K0BAD") else "verified")).encode())
    line = f.readline().decode().strip()
    open(LOG, "a").write(login + "\n" + line + "\n")
    c.close()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 14591)); s.listen(5)
while True:
    c, _ = s.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
'''
FEED = r'''
import socket, threading
LINES = [b"W5REM-9>APRS,TCPIP*::KD5TST   :hello there{12\r\n", b"W5REM-9>APRS,TCPIP*::KD5TST   :hello there{12\r\n", b"W5OTH>APRS,TCPIP*::KD5TST   :from a stranger{1\r\n",
         b"W5REM-9>APRS,TCPIP*::K1OTHER  :not for me{2\r\n", b"W5REM-9>APRS,TCPIP*::KD5TST   :ack12\r\n", b"W5REM-9>APRS,TCPIP*::KD5TST   :second one{13\r\n"]
def handle(c):
    f = c.makefile("rwb", buffering=0)
    f.write(b"# fake aprs feed\r\n")
    login = f.readline().decode().strip()
    open("/tmp/bridge_feed.log", "a").write(login + "\n")
    for l in LINES:
        f.write(l)
    c.close()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 14592)); s.listen(5)
while True:
    c, _ = s.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
'''
UNIT = r'''
import os, json
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from app import create_app
app = create_app()
from app import bridge
out = {}
with app.app_context():
    out["calls"] = bridge.listen_calls()
out["added"] = bridge.listen_once(app, "127.0.0.1", 14592, 4)
print(json.dumps(out))
'''
for n, c in (("fakebridge.py", FAKE), ("fakefeed.py", FEED), ("bridge_unit.py", UNIT)):
    open(n, "w").write(c); inbox(n, n)
SSH(f"{DK} exec adsb-portal pkill -f fakebridge.py; {DK} exec adsb-portal pkill -f fakefeed.py; {DK} exec adsb-portal rm -f /tmp/bridge.log /tmp/bridge_feed.log")
SSH(f"{DK} exec -d adsb-portal python /tmp/fakebridge.py"); SSH(f"{DK} exec -d adsb-portal python /tmp/fakefeed.py")
time.sleep(2)
def log(): return SSH(f"{DK} exec adsb-portal sh -c 'cat /tmp/bridge.log 2>/dev/null'").stdout

unset(*KEYS)
def wipe():
    for t in ("bridge_log", "mesh_outbox", "bridge_links", "conv_messages", "conv_members", "convs", "friendships", "pm_blocks"):
        psql(f"DELETE FROM {t}")
    psql("UPDATE users SET bridge_aprs_ok = FALSE, bridge_mesh_ok = FALSE, contact_policy = 'anyone', ham_status = 'unverified', ham_call = '', ham_name = ''")
wipe()
psql("UPDATE users SET callsign = 'KD5TST', ham_status = 'verified', ham_call = 'KD5TST', ham_name = COALESCE(display_name, '') WHERE username = 'plain1'")
psql("UPDATE users SET callsign = NULL WHERE username = 'claimer1'")
adm = sess("marty", PW); me = sess("plain1", "plainpassword1"); other = sess("claimer1", "claimpassword1"); anon = requests.Session()
def csrf(s):
    return re.search(r'data-csrf="([^"]+)"', s.get(B + "/chat/").text).group(1)
def post(s, path, **data):
    return s.post(B + path, data=dict(data, csrf_token=csrf(s)), headers={"X-Requested-With": "XMLHttpRequest"})

check("bridges are off by default: the page does not exist and the menu does not list it", me.get(B + "/my/bridges").status_code == 404 and 'href="/my/bridges"' not in me.get(B + "/my/").text)
check("the settings are in Admin > Settings", all(x in adm.get(B + "/admin/settings").text for x in ("APRS Bridge for Chats", "Mesh Bridge for Chats", "APRS Bridge: Messages per Hour per Member", "Mesh Bridge: Gateway Token", "Bridges: Links per Member", "Who Can Use Radio Bridges")))
setting("bridge_aprs_enabled", "1"); setting("bridge_mesh_enabled", "1"); setting("bridge_aprs_server", "127.0.0.1:14591")
pg = me.get(B + "/my/bridges").text
check("with the bridges on, a member sees the page with the rules, the licensing words and no way to link yet", all(x in pg for x in ("APRS Messaging", "Meshtastic Mesh", "amateur radio license", "plain ASCII text", "never encrypted", "My Links (0")) and 'href="/my/bridges"' in me.get(B + "/my/").text)
r = post(me, "/chat/new", to="claimer1").json(); cid = r["url"].split("/")[-1]
check("linking without opting in is refused", "Switch the bridge on" in post(me, "/my/bridges/add", kind="aprs", remote="W5REM-9", conv_id=cid).json()["message"])
check("a member without a call sign cannot opt in to APRS", "call sign on your Profile" in post(other, "/my/bridges/optin", kind="aprs", on="1", ack="1").json()["message"])
check("opting in needs the acknowledgement", "Tick the box" in post(me, "/my/bridges/optin", kind="aprs", on="1").json()["message"])
check("with the acknowledgement the member opts in", post(me, "/my/bridges/optin", kind="aprs", on="1", ack="1").json()["ok"] and psql("select bridge_aprs_ok from users where username = 'plain1'") == "t")
check("bad call signs, your own call sign and a chat that is not yours are refused plainly", "looks like W5ABC" in post(me, "/my/bridges/add", kind="aprs", remote="hello there", conv_id=cid).json()["message"]
      and "your own call sign" in post(me, "/my/bridges/add", kind="aprs", remote="KD5TST-7", conv_id=cid).json()["message"] and "Choose one of your chats" in post(me, "/my/bridges/add", kind="aprs", remote="W5REM-9", conv_id="99999").json()["message"])
check("a link is made", post(me, "/my/bridges/add", kind="aprs", remote="w5rem-9", conv_id=cid).json()["ok"] and psql("select remote from bridge_links") == "W5REM-9")
check("the same link twice is refused", "already linked" in post(me, "/my/bridges/add", kind="aprs", remote="W5REM-9", conv_id=cid).json()["message"])
check("the chat page lists the radio link", "Radio:</b> send what you write" in me.get(B + f"/chat/{cid}").text and "W5REM-9" in me.get(B + f"/chat/{cid}").text)

# out
r = post(me, f"/chat/{cid}/send", body="Hello from the net").json()
time.sleep(3)
lg = log()
check("what the link's maker writes is sent as an APRS message from their own call sign to the remote call sign, padded to nine characters", r["ok"] and r["note"] == "" and "user KD5TST pass" in lg and "KD5TST>APARKS,TCPIP*::W5REM-9  :Hello from the net{" in lg, lg)
check("the send is in the bridge log and the audit log, without the words", psql("select count(*) from bridge_log where direction = 'out' and ok") == "1" and "bridge.send" in adm.get(B + "/admin/audit").text and "Hello from the net" not in adm.get(B + "/admin/audit").text)
before = log().count("KD5TST>APARKS")
post(other, f"/chat/{cid}/send", body="I am not a licensed operator")
time.sleep(2)
check("what another member writes in the chat is never sent", log().count("KD5TST>APARKS") == before)
r = post(me, f"/chat/{cid}/send", body="café \U0001F600").json()
check("text that is not plain ASCII is kept in the chat but not sent, and the sender is told", r["ok"] and "plain ASCII" in r["note"] and sum(1 for m in me.get(B + f"/chat/{cid}/poll").json()["messages"] if m["body"].startswith("caf")) == 1)
r = post(me, f"/chat/{cid}/send", body="x" * 80).json()
check("a message over the length limit is kept in the chat but not sent, and the sender is told", "67 characters" in r["note"])
setting("bridge_aprs_rate_per_hour", "1")
r = post(me, f"/chat/{cid}/send", body="one too many").json()
check("the hourly limit stops more", "limit of 1 messages an hour" in r["note"])
unset("bridge_aprs_rate_per_hour")

# in
SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/bridge_unit.py")  # first call primes the app
o = SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/bridge_unit.py").stdout.strip().splitlines()
res = json.loads(o[-1]) if o else {}
check("the listener asks for messages to the linked member's call sign only", res.get("calls") == ["KD5TST"] and "filter g/KD5TST" in SSH(f"{DK} exec adsb-portal sh -c 'cat /tmp/bridge_feed.log'").stdout, res)
p = me.get(B + f"/chat/{cid}/poll").json()
incoming = [m for m in p["messages"] if m.get("via")]
check("messages from the linked call sign appear in the chat under its name, marked via APRS, never as the member's own (a repeat, a stranger, someone else's message and an ack are dropped)",
      sorted(m["body"] for m in incoming) == ["hello there", "second one"] and all(m["name"] == "W5REM-9" and m["via"] == "APRS" and not m["mine"] for m in incoming), p)
check("the other member sees them too, and may report them", [m["name"] for m in other.get(B + f"/chat/{cid}/poll").json()["messages"] if m.get("via")] == ["W5REM-9", "W5REM-9"])
SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/bridge_unit.py")
check("hearing the same messages again adds nothing", len([m for m in me.get(B + f"/chat/{cid}/poll").json()["messages"] if m.get("via")]) == 2 and psql("select count(*) from bridge_log where direction = 'in'") == "2")
check("reception is in the audit log", "bridge.receive" in adm.get(B + "/admin/audit").text)

# links: switch off, limits
lid = psql("select id from bridge_links")
check("a link can be switched off and nothing is sent", post(me, f"/my/bridges/{lid}/toggle").json()["ok"] and (post(me, f"/chat/{cid}/send", body="quiet"), time.sleep(2), log().count("Hello") == 1 and "quiet" not in log())[2])
post(me, f"/my/bridges/{lid}/toggle")
check("only the owner can switch or remove a link", post(other, f"/my/bridges/{lid}/toggle").status_code == 404 and post(other, f"/my/bridges/{lid}/delete").status_code == 404)
setting("bridge_links_per_member", "1")
check("the links-per-member limit is enforced", "1 links at most" in post(me, "/my/bridges/add", kind="aprs", remote="W5NEW", conv_id=cid).json()["message"])
unset("bridge_links_per_member")

# mesh
check("the mesh bridge needs its own opt-in", "Switch the bridge on" in post(me, "/my/bridges/add", kind="mesh", remote="!a1b2c3d4", conv_id=cid).json()["message"])
post(me, "/my/bridges/optin", kind="mesh", on="1")
check("a bad node id is refused and a good one is linked in lower case", "eight hexadecimal" in post(me, "/my/bridges/add", kind="mesh", remote="nonsense", conv_id=cid).json()["message"] and post(me, "/my/bridges/add", kind="mesh", remote="!A1B2C3D4", conv_id=cid).json()["ok"] and psql("select remote from bridge_links where kind = 'mesh'") == "!a1b2c3d4")
post(me, f"/chat/{cid}/send", body="hello mesh")
check("a message to a mesh link is queued for the gateway", psql("select count(*) from mesh_outbox where body = 'hello mesh' and node_id = '!a1b2c3d4'") == "1")
check("the gateway addresses refuse everything while no token is set, and a wrong token", anon.get(B + "/bridge/mesh/outbox").status_code == 401 and anon.get(B + "/bridge/mesh/outbox", headers={"X-Bridge-Token": "x"}).status_code == 401)
setting("bridge_mesh_token", "test-token-123")
H = {"X-Bridge-Token": "test-token-123"}
ob = anon.get(B + "/bridge/mesh/outbox", headers=H)
check("with the token the gateway reads the queue", ob.status_code == 200 and ob.json()["messages"][0]["text"] == "hello mesh" and ob.json()["messages"][0]["node"] == "!a1b2c3d4" and anon.get(B + "/bridge/mesh/outbox", headers={"X-Bridge-Token": "wrong"}).status_code == 401)
oid = ob.json()["messages"][0]["id"]
check("it marks a message done and it leaves the queue", anon.post(B + f"/bridge/mesh/outbox/{oid}/done", headers=H).status_code == 200 and anon.get(B + "/bridge/mesh/outbox", headers=H).json()["messages"] == [])
r = anon.post(B + "/bridge/mesh/inbound", headers=H, json={"node": "!A1B2C3D4", "text": "hi from the mesh", "id": "p1"})
check("the gateway hands over text from the node and it appears in the chat under the node, via mesh", r.json()["delivered"] == 1 and [m for m in me.get(B + f"/chat/{cid}/poll").json()["messages"] if m.get("via") == "mesh"][0]["name"] == "!a1b2c3d4")
check("a repeat, an unlinked node and a bad node id deliver nothing", anon.post(B + "/bridge/mesh/inbound", headers=H, json={"node": "!a1b2c3d4", "text": "hi from the mesh", "id": "p1"}).json()["delivered"] == 0
      and anon.post(B + "/bridge/mesh/inbound", headers=H, json={"node": "!ffffffff", "text": "x", "id": "p2"}).json()["delivered"] == 0 and anon.post(B + "/bridge/mesh/inbound", headers=H, json={"node": "zz", "text": "x"}).status_code == 400)

# opting out, audit, access
post(me, "/my/bridges/optin", kind="aprs", on="0")
check("opting out switches the member's links off", psql("select enabled from bridge_links where kind = 'aprs'") == "f" and psql("select bridge_aprs_ok from users where username = 'plain1'") == "f")
au = adm.get(B + "/admin/audit").text
check("opt-ins, links, switches and sends are in the audit log", all(x in au for x in ("bridge.optin", "bridge.link", "bridge.toggle", "bridge.send", "bridge.receive")))
check("the admin overview shows the bridge counts", "Radio bridge links on" in adm.get(B + "/admin/").text and "bridge_links" in open("../app/restorepart.py", encoding="utf-8").read())
setting("access_bridges", "roles")
check("with Feature Access limited to roles a member without the permission sees none of it", me.get(B + "/my/bridges").status_code == 404)
unset("access_bridges")
setting("bridge_aprs_enabled", "0"); setting("bridge_mesh_enabled", "0")
check("switched off again, the page and the gateway addresses are gone", me.get(B + "/my/bridges").status_code == 404 and anon.get(B + "/bridge/mesh/outbox", headers=H).status_code == 401)
SSH(f"{DK} exec adsb-portal pkill -f fakebridge.py; {DK} exec adsb-portal pkill -f fakefeed.py")
wipe()
psql("UPDATE users SET callsign = NULL WHERE username = 'plain1'")
unset(*KEYS)
print("ALL PASS" if ok_all else "SOME FAILED")
