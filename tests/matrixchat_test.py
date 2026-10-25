"""The Chat page while Matrix is on: Element Web framed inside the site (the site's menu around it), the site's own sign-in carries into it with no second login, its configuration comes from the site (our server only,
the site's colors, registration and sharing off), the server is found from the site's .well-known files, and the old built-in chat and messages addresses show Element too. Needs `sh tests/deploy_matrix.sh` (Synapse and Element on the
test box) and is run with the portal reached at the test box's own address (TEST_HOST=test-box.invalid), because the browser keeps sign-in cookies out of frames that are not on the same site."""
import importlib, json, os, re, sys, time, requests
os.environ["TEST_HOST"] = "test-box.invalid"   # the frame and the site must share a host for the browser to send the sign-in cookie
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
psql("DELETE FROM settings WHERE key = 'matrix_enabled'")
anon = requests.Session()
check("switched off: no well-known files and no Element configuration", all(anon.get(B + p).status_code == 404 for p in ("/.well-known/matrix/client", "/.well-known/matrix/server", "/matrix/element-config.json")))
adm = sess("marty", PW)
psql("INSERT INTO settings (key, value) VALUES ('matrix_enabled', '1') ON CONFLICT (key) DO UPDATE SET value = '1'")
if "service not answering" in adm.get(B + "/admin/matrix").text:
    print("SKIP matrixchat_test: the Matrix service is not running on the test box (sh tests/deploy_matrix.sh fresh)")
    psql("DELETE FROM settings WHERE key = 'matrix_enabled'")
    raise SystemExit(0)
importlib.reload(mxon)   # sets the test addresses and turns Matrix on
adm = sess("marty", PW)
ok, msg, _h = act(adm, "/admin/matrix/apply", "/admin/matrix")
check("Apply sends the test addresses to the service", ok, msg)
time.sleep(5)
act(adm, "/admin/matrix/sync", "/admin/matrix")   # accounts locked by an earlier run or suite are unlocked before anyone signs in
for _ in range(40):
    try:
        if requests.get(HS + "/health", timeout=3).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(3)
adm = sess("marty", PW)
wk = anon.get(B + "/.well-known/matrix/client")
check("the client well-known names the Matrix server, readable from any page", wk.status_code == 200 and wk.json()["m.homeserver"]["base_url"] == HS and wk.headers.get("Access-Control-Allow-Origin") == "*", wk.text)
ws = anon.get(B + "/.well-known/matrix/server").json()
check("the server well-known gives the host and a port", re.match(r"^[^:]+:\d+$", ws["m.server"]) is not None, ws)
cfg = anon.get(B + "/matrix/element-config.json").json()
check("Element's configuration: our server only, no custom servers, no guests, immediate sign-in through the site", cfg["default_server_config"]["m.homeserver"]["server_name"] == "matrix-test.local" and cfg["disable_custom_urls"] and cfg["disable_guests"] and cfg["sso_redirect_options"]["immediate"], cfg)
sd = cfg["setting_defaults"]
check("registration, password reset, deactivation and the integrations are switched off, and the site's own theme is the default", sd["UIFeature.registration"] is False and sd["UIFeature.passwordReset"] is False and sd["UIFeature.deactivate"] is False and cfg["integrations_ui_url"] == "" and cfg["default_theme"] == "custom-ArkSignal" and sd["custom_themes"][0]["colors"]["accent-color"] == "#3ee6a8")
check("the configuration holds nothing secret", not re.search(r"secret|token|password", json.dumps(cfg).replace("passwordReset", ""), re.I))
r = requests.get(EL + "/", timeout=10)
check("Element can be framed only by the site's own pages", r.status_code == 200 and "frame-ancestors 'self'" in r.headers.get("Content-Security-Policy", "") and "http://test-box.invalid:18430" in r.headers["Content-Security-Policy"], r.headers)
served = None
for _ in range(15):   # the element container refreshes its copy every 20 seconds on the test box
    served = requests.get(EL + "/config.json", timeout=10).json()
    if served.get("default_theme") == "custom-ArkSignal":
        break
    time.sleep(3)
check("Element serves the configuration the site made", served["default_server_config"]["m.homeserver"]["server_name"] == "matrix-test.local" and served.get("default_theme") == "custom-ArkSignal", served)
for path, want in (("/chat/", EL), ("/my/messages", EL), ("/chat/?dm=Plain1", EL + "/#/user/@plain1:matrix-test.local"), ("/my/messages?dm=plain1", EL + "/#/user/@plain1:matrix-test.local")):
    page = adm.get(B + path).text
    src = re.search(r'<iframe id="mx-frame"[^>]*src="([^"]+)"', page)
    check(f"{path} shows Element inside the site's page ({want.replace(EL, '')})", src is not None and src.group(1) == want and "Community" in page, src.group(1) if src else page[:200])
check("the frame is allowed the camera, the microphone and notifications", all(x in adm.get(B + "/chat/").text for x in ("camera", "microphone", "notifications")))
acct = adm.get(B + "/my/account").text
menu = adm.get(B + "/my/friends").text
check("the menus offer Chat and no separate Messages while Matrix is on, and the account overview says chats are encrypted", 'href="/chat/"' in menu and 'href="/my/messages"' not in menu and "encrypted, in Element" in acct, [x for x in re.findall(r'href="(/[^"]*)"', menu) if "chat" in x or "messages" in x])
psql("INSERT INTO friendships (user_a, user_b, status, requested_by) SELECT LEAST(a.id, b.id), GREATEST(a.id, b.id), 'accepted', a.id FROM users a, users b WHERE a.username='marty' AND b.username='plain1' ON CONFLICT DO NOTHING")
check("the friends list and a member's page start a chat with a link into Element", 'href="/chat/?dm=plain1"' in adm.get(B + "/my/friends").text and 'href="/chat/?dm=plain1"' in adm.get(B + "/members/plain1").text)
psql("DELETE FROM friendships WHERE user_a IN (SELECT id FROM users WHERE username IN ('marty','plain1')) AND user_b IN (SELECT id FROM users WHERE username IN ('marty','plain1'))")
check("the settings page for community explains chat on Matrix and offers notifications", all(x in adm.get(B + "/my/community").text for x in ("Chat and Notifications", "Turn On Notifications", "Element app")))
check("the old built-in chat's send addresses are closed while Matrix is on", adm.post(B + "/chat/new", data={"csrf_token": tok(adm, "/chat/")}, allow_redirects=False).status_code in (403, 404, 409))
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,1000")
    o.add_argument("--unsafely-treat-insecure-origin-as-secure=http://test-box.invalid:18009,http://test-box.invalid:18430,http://test-box.invalid:18008")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + "/about")
        for c in adm.cookies:
            d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
        d.get(B + "/chat/")
        d.switch_to.frame(d.find_element(By.ID, "mx-frame"))
        body = ""
        for _ in range(30):   # Element loads, signs in through the site and starts up
            time.sleep(2)
            try:
                body = d.find_element(By.TAG_NAME, "body").text
            except Exception:
                body = ""
            if "Welcome marty" in body or "No chats yet" in body or "Back up your chats" in body or "Confirm your digital identity" in body:
                break
        check("Element opens inside the page already signed in as the member, with no second login", "Welcome marty" in body or "No chats yet" in body or "Back up your chats" in body or "Confirm your digital identity" in body, body[:200])
        theme = d.execute_script("var e = document.querySelector('.mx_MatrixChat_wrapper') || document.body; return getComputedStyle(e).backgroundColor + '|' + getComputedStyle(document.body).backgroundColor")
        check("it wears the site's dark colors, not Element's own", "rgb(7, 13, 23)" in theme or "rgb(5, 10, 18)" in theme or "rgb(11, 20, 36)" in theme, theme)
        d.switch_to.default_content()
        d.set_window_size(412, 900); d.get(B + "/chat/"); time.sleep(10)
        w = d.execute_script("var f = document.getElementById('mx-frame'); var r = f.getBoundingClientRect(); return {w: Math.round(r.width), h: Math.round(r.height), overflow: document.documentElement.scrollWidth <= window.innerWidth}")
        check("on a phone the frame fills the width and the page does not scroll sideways", w["w"] >= 360 and w["h"] >= 420 and w["overflow"], w)
    finally:
        d.quit()
except ImportError:
    print("SKIP the browser checks: selenium not installed")
psql("DELETE FROM settings WHERE key = 'matrix_enabled'")
print("ALL PASS" if ok_all else "SOME FAILED")
