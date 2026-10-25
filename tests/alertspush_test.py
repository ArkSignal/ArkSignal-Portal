"""Aircraft alerts by phone or computer notification: the Also Notify box on an alert, a match becoming one combined encrypted notification at the member's device (decrypted with the independent http_ece
package: `pip install http-ece` on the PC), the per-hour limit holding matches back, a member with no device (the match is kept on the page and marked failed), a device its push service says is gone being
removed, and the limits being settings. Uses the stand-in push service of webpush_test."""
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
    print("SKIP alertspush_test: pip install http-ece first"); raise SystemExit(0)

subprocess.run(["scp", "-q", "fake_push.py", "nastemp:adsb-portal/tests/fake_push.py"], check=True)
subprocess.run(["scp", "-q", "alertspush_tick.py", "nastemp:adsb-portal/alertspush_tick.py"], check=True)
ssh("cp ~/adsb-portal/tests/fake_push.py adsb-portal:/tmp/fake_push.py")
ssh("cp ~/adsb-portal/alertspush_tick.py adsb-portal:/tmp/alertspush_tick.py")
ssh(f"exec adsb-portal python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{PORT}/quit',timeout=3)\"")
ssh("exec adsb-portal rm -f /tmp/push.log")
ssh(f"exec -d adsb-portal python /tmp/fake_push.py {PORT}")
time.sleep(2)
def pushlog():
    out = ssh("exec adsb-portal sh -c 'cat /tmp/push.log 2>/dev/null'")
    return [json.loads(l) for l in out.splitlines() if l.strip().startswith("{")]
def tick(arg=""):
    out = ssh(f"exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/alertspush_tick.py {arg} 2>&1")
    line = [l for l in out.splitlines() if l.startswith("{")]
    return json.loads(line[-1]) if line else {"error": out[-300:]}
def newdevice():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return priv, b64u(pub), b64u(auth), auth
def decrypt(entry, priv, auth):
    return json.loads(http_ece.decrypt(base64.b64decode(entry["body"]), private_key=priv, auth_secret=auth, version="aes128gcm"))

psql("DELETE FROM settings WHERE key IN ('push_enabled','push_endpoint_hosts','matrix_enabled')")
setting("push_enabled", "1")
setting("push_endpoint_hosts", "fcm.googleapis.com,127.0.0.1")
setting("alert_push_combine_minutes", "0")
setting("alert_push_per_hour", "20")
psql("DELETE FROM users WHERE username LIKE 'pushal%'")
S = {}
for n in ("pushala", "pushalb", "pushalc"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
    S[n] = sess(n, n + "password99")
def api(s, path, body):
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/community").text)
    return s.post(B + path, json=body, headers={"X-CSRFToken": t.group(1) if t else ""})
devs = {}
for n, ep in (("pushala", "/push/a"), ("pushalc", "/push/c/gone")):
    priv, pub, a64, auth = newdevice()
    devs[n] = (priv, auth)
    r = api(S[n], "/my/push/subscribe", {"subscription": {"endpoint": f"http://127.0.0.1:{PORT}{ep}", "keys": {"p256dh": pub, "auth": a64}}, "device": "Test"})
    check(f"{n}: a device is subscribed", r.status_code == 200, r.text)

page = S["pushala"].get(B + "/my/alerts").text
check("the alert form has the Also Notify box", "Also notify my phone or computer" in page and 'name="notify_push"' in page)
for n in S:
    ok, m, html = act(S[n], "/my/alerts/save", "/my/alerts", kind="military", value="", name="R-push", area_lat="", area_lon="", area_radius_nm="", alt_min_ft="", alt_max_ft="", cooldown_minutes="30", notify_push="1")
    check(f"{n}: a rule with the box ticked is saved", ok, m)
    if n == "pushala":
        check("it shows a phone tag in the list", '<span class="tag">phone</span>' in html)
ok, m, html = act(S["pushala"], "/my/alerts/save", "/my/alerts", kind="emergency", value="", name="R-quiet", area_lat="", area_lon="", area_radius_nm="", alt_min_ft="", alt_max_ft="", cooldown_minutes="30")
check("a rule without the box is saved", ok, m)

r1 = tick()
check("one pass makes an event for each member's rule", r1.get("events", 0) >= 3 and r1["states"] == ["pending", "pending", "pending"], r1)
r2 = tick("send")
check("the notification pass sends one (the member with no device and the gone device do not count as sent)", r2.get("sent") == 1, r2)
check("event states follow: sent, failed (no device), failed (gone)", r2["states"] == ["sent", "failed", "failed"], r2)
log = pushlog()
paths = sorted(e["path"] for e in log)
check("the push service got one notice for the device and one for the gone device", paths == ["/push/a", "/push/c/gone"], paths)
a = next((e for e in log if e["path"] == "/push/a"), None)
msg = decrypt(a, *devs["pushala"]) if a else {}
check("the notice decrypts and names the alert and the aircraft", msg.get("title") == "Aircraft alert: R-push" and "PUSH1" in msg.get("body", "") and "C17" in msg["body"] and msg.get("url") == "/my/alerts", msg)
check("it is sent urgent", a and a["headers"].get("urgency") == "high", a and a["headers"])
check("the gone device was removed", psql("SELECT COUNT(*) FROM push_subscriptions s JOIN users u ON u.id = s.user_id WHERE u.username='pushalc'") == "0")
check("the notification is counted for the limits", r2["pushes"] == [{"events": 1, "ok": True}, {"events": 1, "ok": False}], r2["pushes"])

setting("alert_push_per_hour", "1")
psql("DELETE FROM alert_state")
r3 = tick()
r4 = tick("send")
check("over the hourly limit the match waits", r3["events"] >= 1 and r4["sent"] == 0 and "pending" in r4["states"], (r3, r4))
setting("alert_push_per_hour", "20")
r5 = tick("send")
check("when the limit allows, the waiting match goes out", r5["sent"] == 1 and len([e for e in pushlog() if e["path"] == "/push/a"]) == 2, r5)
setting("push_enabled", "0")
psql("DELETE FROM alert_state")
tick(); r6 = tick("send")
check("with notifications switched off for the site nothing is sent", r6["sent"] == 0 and len([e for e in pushlog() if e["path"] == "/push/a"]) == 2, r6)
setting("push_enabled", "1")
specs = requests.Session()
adm = sess("marty", PW)
sp = adm.get(B + "/admin/settings").text
check("the limits are settings on the Settings page", all(k in sp for k in ("alert_push_per_hour", "alert_push_per_day", "alert_push_combine_minutes")))
psql("DELETE FROM users WHERE username LIKE 'pushal%'")
ssh(f"exec adsb-portal python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{PORT}/quit',timeout=3)\"")
print("ALL PASS" if ok_all else "SOME FAILED")
