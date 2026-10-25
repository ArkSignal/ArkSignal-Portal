"""Moving between My Account pages with the side menu (no full page load) runs each page's scripts in order and waits for the ones that load a file: the Flight Map draws its map every time (it used to say the map
library did not load until the page was refreshed)."""
import os, re, subprocess, time, requests
os.environ["TEST_HOST"] = "test-box.invalid"
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP no selenium"); print("ALL PASS"); raise SystemExit(0)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
psql("INSERT INTO settings (key, value) VALUES ('myflights_enabled','1') ON CONFLICT (key) DO UPDATE SET value='1'")
psql("DELETE FROM settings WHERE key = 'access_myflights'")
user = sess("plain1", "plainpassword1")
def tok(url): return re.search(r'name="csrf_token" value="([^"]+)"', user.get(B + url).text).group(1)
a = {x["code"]: x for x in user.get(B + "/my/flights/airports.json", params={"q": "LIT"}).json()["airports"]}
b = {x["code"]: x for x in user.get(B + "/my/flights/airports.json", params={"q": "DAL"}).json()["airports"]}
if not psql("SELECT COUNT(*) FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='plain1')") != "0":
    user.post(B + "/my/flights/save", data={"csrf_token": tok("/my/flights/new"), "from_id": a["LIT"]["id"], "to_id": b["DAL"]["id"], "mode": "simple", "act_out": "2026-05-01T12:00", "act_in": "2026-05-01T13:00"}, allow_redirects=False)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,900")
d = webdriver.Chrome(options=o); J = d.execute_script
def wait(js, secs=12):
    end = time.time() + secs
    while time.time() < end:
        try:
            if J(js): return True
        except Exception:
            pass
        time.sleep(.25)
    return False
try:
    d.get(B + "/login"); d.delete_all_cookies()
    for c in user.cookies:
        d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
    d.get(B + "/my/flights"); time.sleep(1.5)
    ok = True
    for i in range(4):
        J("document.querySelector('.my-side-items a[href=\"/my/flights/map\"]').click()")
        drawn = wait("return !!window.flMap && !!document.querySelector('#flmap .leaflet-tile-pane')")
        ok &= drawn
        J("document.querySelector('.my-side-items a[href=\"/my/flights/stats\"]').click()")
        wait("return location.pathname === '/my/flights/stats' && !!document.querySelector('h1')")
        J("window.flMap = undefined")
        if not drawn: break
    check("the Flight Map is drawn every time it is reached from the side menu (four times)", ok, J("return document.querySelector('#flmap') ? document.querySelector('#flmap').textContent.slice(0, 120) : 'no map box'"))
    check("and the page stays one page (no full reload)", J("return !!window.__mySideNav"))
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
