"""Web Push: the site's push identity and service worker, a device subscribing (and being refused when its notification service is not on the list), a test notification that arrives encrypted and decrypts with an
independent implementation (the http_ece package: `pip install http-ece` on the PC), the site's VAPID signature, and the whole chain with the real Matrix server: a message in Element becomes a notification at the
device's push service, an unreachable device is dropped, and turning notifications off removes the Matrix pusher. Needs `sh tests/deploy_matrix.sh` for the chain (the first part runs without it)."""
import base64, html, importlib, json, os, re, subprocess, sys, time, requests
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
PORT = 18995
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker {cmd}"], capture_output=True, text=True).stdout
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
def b64ud(t):
    return base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))
try:
    import http_ece
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
except ImportError:
    print("SKIP webpush_test: pip install http-ece first"); raise SystemExit(0)

# the stand-in push service, inside the portal container
subprocess.run(["scp", "-q", "fake_push.py", "nastemp:adsb-portal/tests/fake_push.py"], check=True)
ssh(f"exec adsb-portal python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{PORT}/quit',timeout=3)\"")
ssh("cp ~/adsb-portal/tests/fake_push.py adsb-portal:/tmp/fake_push.py")
ssh("exec adsb-portal rm -f /tmp/push.log")
ssh(f"exec -d adsb-portal python /tmp/fake_push.py {PORT}")
time.sleep(2)
def pushlog():
    out = ssh("exec adsb-portal sh -c 'cat /tmp/push.log 2>/dev/null'")
    return [json.loads(l) for l in out.splitlines() if l.strip().startswith("{")]
def newdevice():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return priv, b64u(pub), b64u(auth), auth
def decrypt(entry, priv, auth):
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    raw = priv.private_numbers().private_value.to_bytes(32, "big")
    return json.loads(http_ece.decrypt(base64.b64decode(entry["body"]), private_key=priv, auth_secret=auth, version="aes128gcm"))

psql("DELETE FROM settings WHERE key IN ('push_enabled','push_endpoint_hosts','matrix_enabled')")
anon = requests.Session()
sw = anon.get(B + "/sw.js")
check("the service worker is served as JavaScript for the whole site and is never cached", sw.status_code == 200 and "javascript" in sw.headers["Content-Type"] and sw.headers.get("Service-Worker-Allowed") == "/" and "no-cache" in sw.headers.get("Cache-Control", "") and 'addEventListener("push"' in sw.text)
check("it caches nothing", "caches." not in sw.text and "cache.put" not in sw.text.lower())
k = anon.get(B + "/push/key").json()["key"]
check("the site's push key is published (an uncompressed P-256 point) and stays the same", len(b64ud(k)) == 65 and b64ud(k)[0] == 4 and anon.get(B + "/push/key").json()["key"] == k)
check("the private part is sealed in the database", psql("SELECT value FROM settings WHERE key='webpush_vapid_private'").startswith("gAAAA"))
setting("push_enabled", "0")
check("switched off: no key and no subscriptions", anon.get(B + "/push/key").status_code == 404)
setting("push_enabled", "1")
setting("push_endpoint_hosts", "fcm.googleapis.com,127.0.0.1")
psql("DELETE FROM users WHERE username IN ('mxa','mxb')")
for n in ("mxa", "mxb"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
SA, SB = sess("mxa", "mxapassword99"), sess("mxb", "mxbpassword99")
def api(s, path, body=None, method="POST"):
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/community").text)
    h = {"X-CSRFToken": t.group(1) if t else ""}
    return s.request(method, B + path, json=body, headers=h)
def sub_body(priv_pub, auth_b64, endpoint):
    return {"subscription": {"endpoint": endpoint, "keys": {"p256dh": priv_pub, "auth": auth_b64}}, "device": "Test Browser"}
EP = f"http://127.0.0.1:{PORT}/push/a"
priv, pub, auth64, auth = newdevice()
r = api(SA, "/my/push/subscribe", sub_body(pub, auth64, "https://evil.invalid/push/x"))
check("a device whose notification service is not on the list is refused", r.status_code == 400 and "not one the site is set up to use" in r.text, r.text)
r = api(SA, "/my/push/subscribe", sub_body(b64u(b"short"), auth64, EP))
check("an incomplete subscription is refused", r.status_code == 400, r.text)
r = api(SA, "/my/push/subscribe", sub_body(pub, auth64, EP))
check("a device on the list can turn notifications on", r.status_code == 200 and r.json()["ok"], r.text)
row = psql("SELECT p256dh_sealed || '|' || auth_sealed || '|' || pushkey_hash FROM push_subscriptions s JOIN users u ON u.id = s.user_id WHERE u.username = 'mxa'")
check("the device's keys are sealed and the pusher key is only a hash", row.count("enc1:") == 2 and pub not in row and auth64 not in row and re.search(r"\|[0-9a-f]{64}$", row), row)
st = api(SA, "/my/push/state", method="GET").json()
check("the member can see their devices", len(st["devices"]) == 1 and st["devices"][0]["device"] == "Test Browser")
r = api(SA, "/my/push/test")
time.sleep(1)
log = pushlog()
check("a test notification reaches the device's push service", r.json()["ok"] and len(log) == 1 and log[0]["path"] == "/push/a", (r.text, log))
msg = decrypt(log[0], priv, auth) if log else {}
check("it decrypts with an independent implementation and says what the site meant", msg.get("body") == "Notifications work on this device." and msg.get("url") == "/my/community", msg)
h = log[0]["headers"] if log else {}
check("it is sent as aes128gcm with a time to live", h.get("content-encoding") == "aes128gcm" and h.get("ttl", "").isdigit(), h)
m = re.match(r"vapid t=([^,]+), k=(.+)$", h.get("authorization", ""))
ok_sig = False
if m:
    head, body_, sig = m.group(1).split(".")
    pubkey = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64ud(m.group(2)))
    try:
        pubkey.verify(encode_dss_signature(int.from_bytes(b64ud(sig)[:32], "big"), int.from_bytes(b64ud(sig)[32:], "big")), (head + "." + body_).encode(), ec.ECDSA(hashes.SHA256())); ok_sig = True
    except Exception:
        pass
    claims = json.loads(b64ud(body_))
check("the site proves who it is: a signed VAPID token for the push service's address, with the site's own key and contact", m and ok_sig and m.group(2) == k and claims["aud"] == f"http://127.0.0.1:{PORT}" and claims["exp"] > time.time() and claims["sub"].startswith("mailto:"), h.get("authorization"))
gw = requests.post(B + "/_matrix/push/v1/notify", json={"notification": {"type": "m.room.message", "devices": [{"app_id": "net.arksignal.web", "pushkey": "not-a-real-key"}]}})
check("the gateway answers a key it does not know with a rejection (so the Matrix server forgets it)", gw.status_code == 200 and gw.json()["rejected"] == ["not-a-real-key"], gw.text)
check("a notification for another app is ignored", requests.post(B + "/_matrix/push/v1/notify", json={"notification": {"devices": [{"app_id": "someone.else", "pushkey": "x"}]}}).json()["rejected"] == [])

# the whole chain with the real Matrix server
adm = sess("marty", PW)
psql("INSERT INTO settings (key, value) VALUES ('matrix_enabled', '1') ON CONFLICT (key) DO UPDATE SET value = '1'")
if "service not answering" in adm.get(B + "/admin/matrix").text:
    print("SKIP the Matrix part of webpush_test: the Matrix service is not running on the test box (sh tests/deploy_matrix.sh fresh)")
else:
    importlib.reload(mxon)
    adm = sess("marty", PW)
    act(adm, "/admin/matrix/apply", "/admin/matrix")
    for _ in range(60):
        try:
            if requests.get(HS + "/health", timeout=3).status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(3)
    time.sleep(5)
    act(adm, "/admin/matrix/sync", "/admin/matrix")   # accounts locked by an earlier run or suite are unlocked before anyone signs in
    def sso_login(s):
        r = s.get(HS + "/_matrix/client/v3/login/sso/redirect/oidc-arksignal", params={"redirectUrl": "http://localhost:9/done"}, allow_redirects=False)
        for c in r.headers.get("Set-Cookie", "").split(", "):
            if "=" in c.split(";")[0] and "oidc" in c.split("=")[0]:
                s.cookies.set(c.split("=")[0].strip(), c.split(";")[0].split("=", 1)[1], domain=urlparse(HS).hostname, path="/")
        r = s.get(r.headers["Location"], allow_redirects=False)
        r = s.get(r.headers["Location"], allow_redirects=False)
        loc = r.headers.get("Location", "")
        if not loc.startswith("http://localhost:9/done"):
            mm = re.search(r'(http://localhost:9/done[^"\' <]*loginToken=[^"\' <]+)', html.unescape(r.text))
            loc = mm.group(1) if mm else ""
        tokn = parse_qs(urlparse(loc).query).get("loginToken", [""])[0]
        return requests.post(HS + "/_matrix/client/v3/login", json={"type": "m.login.token", "token": tokn}).json().get("access_token")
    TA, TB = sso_login(SA), sso_login(SB)
    def mx(tk, method, path, body=None):
        return requests.request(method, HS + "/_matrix/client/v3" + path, json=body, headers={"Authorization": "Bearer " + tk}, timeout=30)
    ssh("exec adsb-portal rm -f /tmp/push.log")
    # mxa's device subscribes again now that mxa has a chat account, so the pusher is made on the Matrix server
    priv, pub, auth64, auth = newdevice()
    r = api(SA, "/my/push/subscribe", sub_body(pub, auth64, f"http://127.0.0.1:{PORT}/push/a"))
    pushers = mx(TA, "GET", "/pushers").json().get("pushers", [])
    check("turning notifications on makes a pusher for the member on the Matrix server, aimed at the site", r.json()["ok"] and len(pushers) == 1 and pushers[0]["app_id"] == "net.arksignal.web" and pushers[0]["data"]["url"].endswith("/_matrix/push/v1/notify"), (r.text, pushers))
    room = mx(TB, "POST", "/createRoom", {"name": "Push test", "preset": "public_chat", "visibility": "private", "invite": [f"@mxa:{SV}"]}).json()["room_id"]
    time.sleep(6)
    log = pushlog()
    inv = [decrypt(e, priv, auth) for e in log if e["path"] == "/push/a"]
    check("an invitation to a chat becomes a notification", any("invited you" in m.get("body", "") for m in inv), inv)
    mx(TA, "POST", f"/join/{quote(room)}")
    ssh("exec adsb-portal rm -f /tmp/push.log")
    mx(TB, "PUT", f"/rooms/{quote(room)}/send/m.room.message/n{time.time_ns()}", {"msgtype": "m.text", "body": "hi mxa, are you there?"})
    got = []
    for _ in range(15):
        time.sleep(2)
        got = [decrypt(e, priv, auth) for e in pushlog() if e["path"] == "/push/a"]
        if got:
            break
    check("a message to the member becomes a notification: who it is from, the room and the words (the room is not encrypted)", got and "mxb" in got[0]["title"] and "Push test" in got[0]["title"] and got[0]["body"] == "hi mxa, are you there?" and quote(room, safe="") in got[0]["url"], got)
    # a device that has gone away is dropped
    r = api(SB, "/my/push/subscribe", sub_body(*newdevice()[1:3], f"http://127.0.0.1:{PORT}/push/gone"))
    check("a second member turns notifications on with a device that will be gone", r.json()["ok"], r.text)
    mx(TA, "PUT", f"/rooms/{quote(room)}/send/m.room.message/n{time.time_ns()}", {"msgtype": "m.text", "body": "hello mxb"})
    gone = False
    for _ in range(15):
        time.sleep(2)
        if psql("SELECT count(*) FROM push_subscriptions s JOIN users u ON u.id = s.user_id WHERE u.username = 'mxb'") == "0":
            gone = True
            break
    check("a push service that says the device is gone has it removed here", gone)
    # turning notifications off
    r = api(SA, "/my/push/unsubscribe", {"endpoint": ""})
    time.sleep(2)
    pushers = mx(TA, "GET", "/pushers").json().get("pushers", [])
    check("turning notifications off removes the member's pusher on the Matrix server", r.json()["ok"] and not [p for p in pushers if p["app_id"] == "net.arksignal.web"], (r.text, pushers))
    ssh("exec adsb-portal rm -f /tmp/push.log")
    mx(TB, "PUT", f"/rooms/{quote(room)}/send/m.room.message/n{time.time_ns()}", {"msgtype": "m.text", "body": "after off"})
    time.sleep(8)
    check("and nothing more is sent to the device", not pushlog())
check("subscribing and unsubscribing are in the audit log", all(a in psql("SELECT string_agg(DISTINCT action, ',') FROM audit_log WHERE action LIKE 'push.%'") for a in ("push.subscribe", "push.unsubscribe")))
ssh(f"exec adsb-portal python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{PORT}/quit',timeout=3)\"")
psql("DELETE FROM users WHERE username IN ('mxa','mxb')")
psql("DELETE FROM settings WHERE key IN ('push_endpoint_hosts','matrix_enabled')")
print("ALL PASS" if ok_all else "SOME FAILED")
