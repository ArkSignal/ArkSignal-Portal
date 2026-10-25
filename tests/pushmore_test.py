"""Notifications for the rest: mesh node alerts by phone or computer (a script inside the portal container with the sender replaced by a recorder: the choice, the combining, one notice per person, a member
with no device marked failed) and friend requests (a request and an acceptance reach the member's device, decrypted with the independent http_ece package; a member can turn them off; the site setting turns them
off for everyone). Needs `pip install http-ece` and the stand-in push service of webpush_test."""
import base64, json, os, re, subprocess, sys, time, requests
os.environ["TEST_HOST"] = "test-box.invalid"
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mxon
psql = mxon.psql
PORT = 18995
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker {cmd}"], capture_output=True, text=True).stdout
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
try:
    import http_ece
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
except ImportError:
    print("SKIP pushmore_test: pip install http-ece first"); raise SystemExit(0)

subprocess.run(["scp", "-q", "fake_push.py", "nastemp:adsb-portal/tests/fake_push.py"], check=True)
subprocess.run(["scp", "-q", "meshpush_unit.py", "nastemp:adsb-portal/meshpush_unit.py"], check=True)
ssh("cp ~/adsb-portal/tests/fake_push.py adsb-portal:/tmp/fake_push.py")
ssh("cp ~/adsb-portal/meshpush_unit.py adsb-portal:/tmp/meshpush_unit.py")

# ---- mesh node alerts
o = ssh("exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/meshpush_unit.py").strip().splitlines()
try:
    res = json.loads(o[-1])
except Exception:
    res = {}
    print("unit output:", o[-5:])
check("mesh: a pass makes the alerts and each waits to be notified", res.get("made") == 3 and res.get("pending") is True, res)
for k, label in (("waits_for_combine", "nothing is sent before the combine wait"), ("one_combined_notice", "one combined notice per person, high urgency, opening My Mesh Nodes"),
                 ("states", "sent for the member with a device, failed for the member with none"), ("not_sent_twice", "a sent alert is not sent again"), ("choice_off_means_none", "with the choice off nothing is queued")):
    check("mesh: " + label, res.get(k) is True, res.get(k))
me = sess("plain1", "plainpassword1")
psql("INSERT INTO settings (key, value) VALUES ('mesh_enabled','1') ON CONFLICT (key) DO UPDATE SET value='1'")
page = me.get(B + "/my/mesh").text
check("mesh: the Node Alerts choices have the phone box", 'name="by_push"' in page)
t = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
me.post(B + "/my/mesh/alerts/save", data={"csrf_token": t, "quiet": "1", "by_push": "1"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("mesh: the choice is saved", psql("select by_push from mesh_alert_prefs mp join users u on u.id = mp.user_id where u.username = 'plain1'") == "t")
psql("DELETE FROM mesh_alert_prefs")

# ---- friend requests
setting("push_enabled", "1")
setting("push_endpoint_hosts", "fcm.googleapis.com,127.0.0.1")
setting("friends_push", "1")
setting("friends_enabled", "1")
ssh(f"exec adsb-portal python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{PORT}/quit',timeout=3)\"")
ssh("exec adsb-portal rm -f /tmp/push.log")
ssh(f"exec -d adsb-portal python /tmp/fake_push.py {PORT}")
time.sleep(2)
def pushlog():
    out = ssh("exec adsb-portal sh -c 'cat /tmp/push.log 2>/dev/null'")
    return [json.loads(l) for l in out.splitlines() if l.strip().startswith("{")]
def decrypt(entry, priv, auth):
    return json.loads(http_ece.decrypt(base64.b64decode(entry["body"]), private_key=priv, auth_secret=auth, version="aes128gcm"))
psql("DELETE FROM users WHERE username LIKE 'pushfr%'")
S, D = {}, {}
for n in ("pushfra", "pushfrb", "pushfrc"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
    S[n] = sess(n, n + "password99")
def api(s, path, body=None, form=None):
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/community").text)
    h = {"X-CSRFToken": t.group(1) if t else "", "X-Requested-With": "XMLHttpRequest"}
    return s.post(B + path, json=body, data=form, headers=h) if body is not None else s.post(B + path, data=dict(form or {}, csrf_token=t.group(1) if t else ""), headers=h)
for n, ep in (("pushfra", "/push/a"), ("pushfrb", "/push/b")):
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    D[n] = (priv, auth)
    r = api(S[n], "/my/push/subscribe", {"subscription": {"endpoint": f"http://127.0.0.1:{PORT}{ep}", "keys": {"p256dh": b64u(pub), "auth": b64u(auth)}}, "device": "Test"})
    check(f"friends: {n} turns notifications on", r.status_code == 200, r.text)
r = api(S["pushfrb"], "/my/friends/request", form={"to": "pushfra"})
time.sleep(1)
log = [e for e in pushlog() if e["path"] == "/push/a"]
check("friends: a request is sent", r.status_code == 200 and r.json().get("ok"), r.text)
msg = decrypt(log[0], *D["pushfra"]) if log else {}
check("friends: the member's device is told who asked", msg.get("title") == "Friend request" and "pushfrb" in msg.get("body", "") and msg.get("url") == "/my/friends", (msg, len(log)))
r = api(S["pushfra"], "/my/friends/pushfrb/accept", form={})
time.sleep(1)
log = [e for e in pushlog() if e["path"] == "/push/b"]
msg = decrypt(log[0], *D["pushfrb"]) if log else {}
check("friends: the one who asked is told it was accepted", r.status_code == 200 and msg.get("title") == "Friend request accepted" and "pushfra" in msg.get("body", ""), (r.text, msg))
r = api(S["pushfra"], "/my/notifications/save", form={})
check("friends: a member can turn the notices off (the switch left empty on the Notifications page)", r.status_code == 200 and psql("SELECT push_friends FROM users WHERE username='pushfra'") == "f", r.text)
before = len(pushlog())
api(S["pushfrc"], "/my/friends/request", form={"to": "pushfra"})
time.sleep(1)
check("friends: with it off nothing is sent", len(pushlog()) == before)
api(S["pushfra"], "/my/notifications/save", form={"kind_friends": "1"})
setting("friends_push", "0")
api(S["pushfra"], "/my/friends/pushfrc/decline", form={})
api(S["pushfrc"], "/my/friends/request", form={"to": "pushfra"})
time.sleep(1)
check("friends: the site setting turns them off for everyone", len(pushlog()) == before)
setting("friends_push", "1")
adm = sess("marty", PW)
check("friends: the setting is on the Settings page", "friends_push" in adm.get(B + "/admin/settings").text)
check("friends: the switch is on the Notifications page", 'name="kind_friends"' in S["pushfrb"].get(B + "/my/notifications").text)
psql("DELETE FROM users WHERE username LIKE 'pushfr%'")
ssh(f"exec adsb-portal python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{PORT}/quit',timeout=3)\"")
print("ALL PASS" if ok_all else "SOME FAILED")
