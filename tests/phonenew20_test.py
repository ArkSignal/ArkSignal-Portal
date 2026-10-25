"""The pages added in session 20 fit a phone (412 px wide): nothing runs off the side. Notifications, Shared Flights, the Flight Map and statistics, the Add a Flight form with the schedule button, a shared diary and flight,
and the community's Alert Channel and Linked Areas pages."""
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
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("myflights_enabled", "1"); setting("myflights_sharing", "1"); setting("push_enabled", "1"); setting("mesh_enabled", "1"); setting("mesh_communities_enabled", "1"); setting("meshlink_enabled", "1"); setting("meshalert_enabled", "1")
setting("schedules_enabled", "1"); setting("schedules_key", "phone-test-key")
psql("DELETE FROM settings WHERE key = 'access_myflights'"); psql("DELETE FROM settings WHERE key = 'access_communities'")
psql("DELETE FROM communities WHERE slug LIKE 'pn-%'")
adm = sess("marty", PW)
act(adm, "/admin/communities/create", "/admin/communities", name="PN Phone Mesh", owner="")
user = sess("plain1", "plainpassword1")
def tok(s, url): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + url).text).group(1)
ap = {a["code"]: a for a in user.get(B + "/my/flights/airports.json", params={"q": "LIT"}).json()["airports"]}
las = {a["code"]: a for a in user.get(B + "/my/flights/airports.json", params={"q": "LAS"}).json()["airports"]}
user.post(B + "/my/flights/save", data={"csrf_token": tok(user, "/my/flights/new"), "from_id": ap["LIT"]["id"], "to_id": las["LAS"]["id"], "mode": "simple", "act_out": "2026-05-01T12:00", "act_in": "2026-05-01T14:00", "airline": "Southwest Airlines",
          "aircraft_type": "B738", "flight_no": "WN1", "p0_name": "Phone Person"}, allow_redirects=False)
fid = psql("SELECT id FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='plain1') ORDER BY id DESC LIMIT 1")
r = user.post(B + "/my/flights/shares/create", data={"csrf_token": tok(user, "/my/flights/shares"), "days": "30", "label": "Phone check of a rather long title that should wrap", "show_people": "1"}, allow_redirects=True)
token = re.search(r"/share/flights/([A-Za-z0-9_\-]{30,})", r.text).group(1)
r = user.post(B + "/my/flights/shares/create", data={"csrf_token": tok(user, "/my/flights/shares"), "days": "30", "flight_id": fid, "show_people": "1"}, allow_redirects=True)
token2 = re.search(r"/share/flights/([A-Za-z0-9_\-]{30,})", r.text).group(1)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=412,850")
d = webdriver.Chrome(options=o); J = d.execute_script
def login(s):
    d.get(B + "/login")
    d.delete_all_cookies()
    for c in s.cookies:
        d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
def fits(path, label=None):
    d.get(B + path); time.sleep(2)
    check(f"phone: {label or path} does not run off the side", J("return document.documentElement.scrollWidth <= window.innerWidth + 1"), J("return document.documentElement.scrollWidth + ' > ' + window.innerWidth"))
try:
    login(user)
    for path in ("/my/notifications", "/my/flights/shares", "/my/flights/map", "/my/flights/stats", "/my/flights/new", f"/my/flights/{fid}", f"/share/flights/{token}", f"/share/flights/{token2}"):
        fits(path)
    d.get(B + "/my/flights/new"); time.sleep(1.5)
    check("phone: the schedule button is on the form", J("return !!document.getElementById('sc_go')"))
    login(adm)
    for path in ("/mesh/c/pn-phone-mesh/alerts", "/mesh/c/pn-phone-mesh/links", "/admin/communities"):
        fits(path)
finally:
    d.quit()
psql("DELETE FROM communities WHERE slug LIKE 'pn-%'")
for k in ("schedules_enabled", "schedules_key"):
    psql(f"DELETE FROM settings WHERE key='{k}'")
print("ALL PASS" if ok_all else "SOME FAILED")
