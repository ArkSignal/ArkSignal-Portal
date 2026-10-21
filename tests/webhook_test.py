import json, re, subprocess, threading, time, requests
from http.server import BaseHTTPRequestHandler, HTTPServer
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
HOST, PORT = "test-box.invalid", 2531
got = []
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); got.append((self.path, dict(self.headers), self.rfile.read(n).decode("utf-8", "replace")))
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
srv = HTTPServer(("0.0.0.0", PORT), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
adm = sess("marty", PW)
def tk(path): return re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + path).text).group(1)
def save(url, kind="json"): return act(adm, "/my/alerts/webhook", "/my/alerts", webhook_url=url, webhook_kind=kind)
def test(): return act(adm, "/my/alerts/webhook/test", "/my/alerts")
try:
    setting("webhook_allow_private", "0"); setting("webhook_combine_minutes", "0")
    ok, m, _ = save(f"http://{HOST}:{PORT}/h"); check("plain http is refused", not ok and "https" in m, m)
    ok, m, _ = save("https://127.0.0.1/x"); check("an address on a private network is refused", not ok and "private" in m, m)
    ok, m, _ = save("https://localhost/x"); check("localhost is refused", not ok and "private" in m, m)
    ok, m, _ = save("https://user:pw@example.com/x"); check("a user name and password in the address is refused", not ok, m)
    ok, m, _ = save("not a url"); check("garbage is refused", not ok, m)
    ok, m, _ = save("https://example.com/x", "carrier-pigeon"); check("an unknown service kind is refused", not ok, m)
    ok, m, _ = test(); check("the test button asks for an address first when none is saved", not ok and "Save a webhook address" in m, m)
    ok, m, _ = save(""); check("an empty address turns it off", ok and "off" in m, m)
    # now allow this network (the test receiver is a private address) and deliver for real
    setting("webhook_allow_private", "1")
    for kind in ("json", "discord", "slack", "ntfy"):
        got.clear()
        ok, m, _ = save(f"http://{HOST}:{PORT}/{kind}", kind); check(f"{kind}: the address is saved", ok, m)
        ok, m, _ = test(); time.sleep(0.5)
        check(f"{kind}: the test message arrives in that service's format", ok and len(got) == 1, f"{m} {got}")
        if got:
            path, hdr, body = got[0]
            if kind == "json": check("json: carries site, count, matches and text", {"site", "count", "matches", "text"} <= set(json.loads(body)), body[:150])
            if kind == "discord": check("discord: has a content field", "content" in json.loads(body) and "working" in json.loads(body)["content"], body[:150])
            if kind == "slack": check("slack: has a text field", "text" in json.loads(body), body[:150])
            if kind == "ntfy": check("ntfy: plain text with a Title header", "working" in body and hdr.get("Title"), str(hdr)[:200])
    # an alert rule that sends to the webhook, end to end through the background job
    air = requests.get(B + "/api/aircraft.json").json()["aircraft"]
    if air:
        got.clear()
        save(f"http://{HOST}:{PORT}/live", "json")
        hx = air[0]["hex"]
        adm.post(B + "/my/alerts/save", data={"csrf_token": tk("/my/alerts"), "kind": "icao", "value": hx.upper(), "name": "Webhook e2e rule", "cooldown_minutes": "30", "notify_webhook": "1"}, headers={"X-Requested-With": "XMLHttpRequest"})
        for _ in range(40):
            if any("Webhook e2e rule" in g[2] for g in got): break
            time.sleep(3)
        check("a matching aircraft sends the alert to the webhook by itself", any("Webhook e2e rule" in g[2] for g in got), str([g[2][:120] for g in got]))
        check("the match is marked as sent", psql("SELECT webhook_state FROM alert_events e JOIN alert_rules r ON r.id=e.rule_id WHERE r.name='Webhook e2e rule' LIMIT 1") == "sent")
        for rid in set(re.findall(r'id="rule-(\d+)"', adm.get(B + "/my/alerts").text)):
            adm.post(B + f"/my/alerts/{rid}/delete", data={"csrf_token": tk("/my/alerts")}, headers={"X-Requested-With": "XMLHttpRequest"})
    else:
        print("note: no aircraft right now; the end-to-end part was skipped")
    page = adm.get(B + "/my/alerts").text
    check("the alerts page has the webhook section and the per-alert checkbox", "Alert Webhook" in page and "Also send to my webhook" in page)
    save("")
finally:
    setting("webhook_allow_private", "0"); setting("webhook_combine_minutes", "1"); srv.shutdown()
st = adm.get(B + "/admin/settings").text
check("webhook settings are on the Settings page", all(x in st for x in ("Alert Webhook Messages per User per Hour", "Allow Webhooks to Private Addresses", "Webhook Timeout")))
print("ALL PASS" if ok_all else "SOME FAILED")
