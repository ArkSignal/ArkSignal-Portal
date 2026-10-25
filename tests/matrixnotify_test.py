"""Notifications while the Chat page is open: Element inside the site's frame shows a browser notification for a message that arrives in a chat the member is not looking at (once the browser has been allowed to show
notifications for the chat address, which a browser asks for from a page that is not inside another page), and sets itself to show them (the site's configuration turns them on). The browser is Chrome driven with the permission
granted and the notification call recorded. Needs `sh tests/deploy_matrix.sh` and a Chrome with selenium."""
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
EL = "http://test-box.invalid:18009"
HS = "http://test-box.invalid:18008"
SV = "matrix-test.local"
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP matrixnotify_test: selenium is not installed"); raise SystemExit(0)
adm = sess("marty", PW)
psql("INSERT INTO settings (key, value) VALUES ('matrix_enabled', '1') ON CONFLICT (key) DO UPDATE SET value = '1'")
if "service not answering" in adm.get(B + "/admin/matrix").text:
    print("SKIP matrixnotify_test: the Matrix service is not running on the test box (sh tests/deploy_matrix.sh fresh)")
    psql("DELETE FROM settings WHERE key = 'matrix_enabled'")
    raise SystemExit(0)
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
cfg = requests.get(B + "/matrix/element-config.json").json()["setting_defaults"]
check("the site's configuration turns Element's desktop notifications, their text and sound on", cfg["notificationsEnabled"] is True and cfg["notificationBodyEnabled"] is True and cfg["audioNotificationsEnabled"] is True, cfg)
psql("DELETE FROM users WHERE username = 'mxb'")
reg = requests.Session()
reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": "mxb", "email": "mxb@example.com", "password": "mxbpassword99", "confirm": "mxbpassword99"})
SB = sess("mxb", "mxbpassword99")
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
TM, TB = sso_login(sess("marty", PW)), sso_login(SB)
def mx(tk, method, path, body=None):
    return requests.request(method, HS + "/_matrix/client/v3" + path, json=body, headers={"Authorization": "Bearer " + tk}, timeout=30)

o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,1000")
o.add_argument("--unsafely-treat-insecure-origin-as-secure=http://test-box.invalid:18009,http://test-box.invalid:18430,http://test-box.invalid:18008")
d = webdriver.Chrome(options=o)
try:
    d.execute_cdp_cmd("Browser.grantPermissions", {"permissions": ["notifications"], "origin": EL})
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
      (function () { var Orig = window.Notification;
        function N(title, opts) { try { var a = JSON.parse(localStorage.getItem('__notes') || '[]'); a.push({title: title, body: (opts || {}).body || ''}); localStorage.setItem('__notes', JSON.stringify(a)); } catch (e) {} return new Orig(title, opts); }
        N.prototype = Orig.prototype; N.requestPermission = function () { return Promise.resolve('granted'); }; Object.defineProperty(N, 'permission', { get: function () { return 'granted'; } }); window.Notification = N; })();"""})
    d.get(B + "/about")
    for c in adm.cookies:
        d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
    d.get(B + "/chat/")
    d.switch_to.frame(d.find_element(By.ID, "mx-frame"))
    body = ""
    for _ in range(30):
        time.sleep(2)
        try:
            body = d.find_element(By.TAG_NAME, "body").text
        except Exception:
            body = ""
        if "Welcome marty" in body or "No chats yet" in body or "Back up your chats" in body or "Confirm your digital identity" in body:
            break
    check("Element opens inside the page signed in as the member", bool(body), body[:100])
    d.execute_script("localStorage.removeItem('__notes')")
    room = mx(TB, "POST", "/createRoom", {"is_direct": True, "preset": "trusted_private_chat", "invite": [f"@marty:{SV}"], "initial_state": []}).json()["room_id"]
    time.sleep(2)
    mx(TM, "POST", f"/join/{quote(room)}")
    time.sleep(3)
    mx(TB, "PUT", f"/rooms/{quote(room)}/send/m.room.message/n{time.time_ns()}", {"msgtype": "m.text", "body": "ping for the notification test"})
    notes = []
    for _ in range(15):
        time.sleep(2)
        notes = json.loads(d.execute_script("return localStorage.getItem('__notes') || '[]'"))
        if any("ping" in (n.get("body") or "") or "mxb" in (n.get("title") or "") for n in notes):
            break
    check("a message in a chat the member is not looking at makes Element show a notification from inside the frame, with who wrote it", any("mxb" in (n.get("title") or "") or "ping for the notification test" in (n.get("body") or "") for n in notes), notes)
finally:
    d.quit()
psql("DELETE FROM users WHERE username = 'mxb'")
psql("DELETE FROM settings WHERE key = 'matrix_enabled'")
print("ALL PASS" if ok_all else "SOME FAILED")
