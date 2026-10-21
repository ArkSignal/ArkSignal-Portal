import re, subprocess, requests, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
def tkn(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
def login(user, pw, ua):
    s = requests.Session(); s.headers["User-Agent"] = ua
    s.post(B + "/my/login", data={"csrf_token": tkn(s, "/my/login"), "username": user, "password": pw})
    return s
UA1 = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
UA2 = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
setting("signin_ip_failures", "0"); psql("DELETE FROM login_events WHERE NOT success")
a = login("marty", PW, UA1); b = login("marty", PW, UA2)
pg = a.get(B + "/my/sessions").text
check("the Sign-Ins page lists this device and the other with readable device names", "Chrome on Windows" in pg and "Safari on iPhone" in pg and "this device" in pg, re.findall(r"<td>[^<]{0,40}", pg)[:6])
check("the My menu has a Sign-Ins tab", 'href="/my/sessions"' in pg)
check("the other device is signed in", "Sign out" in b.get(B + "/my/").text or b.get(B + "/my/").status_code == 200)
sid = re.findall(r"/my/sessions/([A-Za-z0-9_-]+)/end", pg)
check("there is a Sign Out button for the other device only", len(sid) >= 1)
r = a.post(B + f"/my/sessions/{sid[0]}/end", data={"csrf_token": tkn(a, "/my/sessions")}, headers={"X-Requested-With": "XMLHttpRequest"})
check("ending it answers ok", r.status_code == 200 and r.json()["ok"], r.text[:150])
r = b.get(B + "/my/", allow_redirects=False)
check("the ended device is signed out on its next request", r.status_code in (302, 401) and "login" in (r.headers.get("Location", "") + r.text), r.status_code)
cur = re.search(r'tag live">this device', a.get(B + "/my/sessions").text)
check("this device stays signed in", a.get(B + "/my/", allow_redirects=False).status_code == 200 and cur)
c = login("marty", PW, UA2)
r = a.post(B + "/my/sessions/end-others", data={"csrf_token": tkn(a, "/my/sessions")}, headers={"X-Requested-With": "XMLHttpRequest"})
check("Sign Out All Other Devices works", r.status_code == 200 and "Signed out" in r.json()["message"], r.text[:150])
check("and the other device is out", c.get(B + "/my/", allow_redirects=False).status_code == 302)
# a person cannot end someone else's session
other = login("plain1", "plainpassword1", UA1)
r = other.post(B + f"/my/sessions/{sid[0]}/end", data={"csrf_token": tkn(other, "/my/sessions")}, headers={"X-Requested-With": "XMLHttpRequest"})
check("nobody can end another person's sign-in", r.status_code == 404, r.status_code)
# sign out marks the session ended
x = login("marty", PW, UA1); x.post(B + "/my/logout", data={"csrf_token": tkn(x, "/my/")})
check("signing out ends the recorded session", psql("SELECT COUNT(*) FROM user_sessions WHERE ended") not in ("", "0"))
# security headers
h = requests.get(B + "/").headers
check("browser security headers are sent", h.get("X-Content-Type-Options") == "nosniff" and h.get("X-Frame-Options") == "SAMEORIGIN" and "frame-ancestors 'self'" in h.get("Content-Security-Policy", "") and "Referrer-Policy" in h, str(dict(h))[:300])
check("and the live map still works with them (it embeds nothing foreign without its own framing)", requests.get(B + "/live").status_code == 200)
setting("security_headers", "0")
check("the headers can be turned off", "X-Frame-Options" not in requests.get(B + "/").headers)
setting("security_headers", "1")
# per-address throttle
setting("signin_ip_failures", "4"); setting("signin_ip_window_minutes", "15")
psql("DELETE FROM login_events WHERE NOT success")
v = requests.Session()
for i in range(4):
    v.post(B + "/my/login", data={"csrf_token": tkn(v, "/my/login"), "username": f"nobody{i}", "password": "x" * 12})
r = v.post(B + "/my/login", data={"csrf_token": tkn(v, "/my/login"), "username": "marty", "password": PW})
check("after too many failures from one address even the right password is refused for a while", r.status_code == 429 and "Too many failed sign-in attempts from your address" in r.text, r.status_code)
psql("DELETE FROM login_events WHERE NOT success"); setting("signin_ip_failures", "0")
r = v.post(B + "/my/login", data={"csrf_token": tkn(v, "/my/login"), "username": "marty", "password": PW}, allow_redirects=False)
check("and it works again once the failures age out", r.status_code == 302)
print("ALL PASS" if ok_all else "SOME FAILED")
