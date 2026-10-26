import base64, hashlib, hmac, re, struct, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def totp(secret, at=None):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8)); c = int((at or time.time()) // 30)
    mac = hmac.new(key, struct.pack(">Q", c), hashlib.sha1).digest(); o = mac[-1] & 15
    return str((struct.unpack(">I", mac[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** 6).zfill(6)
def found_codes(html):
    m = re.search(r'<pre class="code">(.*?)</pre>', html, re.S)
    return re.findall(r"[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}", m.group(1)) if m else []
def tk(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
U, P = "tfuser1", "tfpassword123"
s0 = requests.Session()
if "tfuser1" not in psql("SELECT username FROM users"):
    s0.post(B + "/my/register", data={"csrf_token": tk(s0, "/my/register"), "username": U, "email": "tf1@example.com", "password": P, "confirm": P})
psql(f"UPDATE users SET totp_enabled=FALSE, totp_secret='', totp_last=0, failed_attempts=0, locked_until=NULL WHERE username='{U}'"); psql("DELETE FROM recovery_codes WHERE user_id=(SELECT id FROM users WHERE username='tfuser1')")
def login(user=U, pw=P):
    s = requests.Session(); r = s.post(B + "/my/login", data={"csrf_token": tk(s, "/my/login"), "username": user, "password": pw}); return s, r
s, r = login(); check("without two-factor, the password alone signs in", "Sign out" in r.text)
pg = s.get(B + "/my/two-factor").text
check("the Two-Factor tab offers set-up", "Set Up Two-Factor" in pg and 'href="/my/two-factor"' in pg)
s.post(B + "/my/two-factor", data={"csrf_token": tk(s, "/my/two-factor"), "action": "start"})
pg = s.get(B + "/my/two-factor").text
sec = "".join(re.search(r'letter-spacing:\.06em[^>]*>([^<]+)<', pg).group(1).split())
check("the set-up page shows a setup key and a QR code area", len(sec) == 32 and 'id="qr"' in pg and "<svg" in pg.split('id="qr"')[1][:400] and "cdnjs" not in pg and "qrcode" not in pg, sec)
r = s.post(B + "/my/two-factor", data={"csrf_token": tk(s, "/my/two-factor"), "action": "confirm", "code": "000000"}, allow_redirects=True)
check("a wrong code does not turn it on", "not right" in r.text and psql(f"SELECT totp_enabled FROM users WHERE username='{U}'") == "f")
r = s.post(B + "/my/two-factor", data={"csrf_token": tk(s, "/my/two-factor"), "action": "confirm", "code": totp(sec)})
codes = found_codes(r.text)
check("the right code turns it on and shows ten recovery codes once", psql(f"SELECT totp_enabled FROM users WHERE username='{U}'") == "t" and len(codes) == 10, (len(codes), r.text[:100]))
check("recovery codes are stored as keyed hashes, not as the codes", psql("SELECT COUNT(*) FROM recovery_codes WHERE user_id=(SELECT id FROM users WHERE username='tfuser1') AND code_hash ~ '^[0-9a-f]{64}$'") == "10" and not any(c.replace("-", "") in psql("SELECT string_agg(code_hash, ',') FROM recovery_codes") for c in codes))
check("the authenticator secret is encrypted in the database, never plain text", psql(f"SELECT totp_secret FROM users WHERE username='{U}'").startswith("enc1:") and sec not in psql(f"SELECT totp_secret FROM users WHERE username='{U}'"))
check("the codes are not shown again", not found_codes(s.get(B + "/my/two-factor").text))
# sign in with it (a code works once, and the set-up code is used up: wait for the next 30-second step)
time.sleep(31 - time.time() % 30 + 1)
s2, r = login()
check("now the password leads to a code prompt, not a session", "Two-Factor" in r.text and "Sign out" not in r.text and "/login/2fa" in r.url, r.url)
r = s2.post(B + "/my/login/2fa", data={"csrf_token": tk(s2, "/my/login/2fa"), "code": "123456"})
check("a wrong code is refused", "not right" in r.text and "Sign out" not in r.text)
good = totp(sec)
r = s2.post(B + "/my/login/2fa", data={"csrf_token": tk(s2, "/my/login/2fa"), "code": good})
check("the right code signs in", "Sign out" in r.text, r.url)
s3, r = login(); r = s3.post(B + "/my/login/2fa", data={"csrf_token": tk(s3, "/my/login/2fa"), "code": good})
check("the same code cannot be used twice", "Sign out" not in r.text and "not right" in r.text)
# recovery code
s4, r = login(); r = s4.post(B + "/my/login/2fa", data={"csrf_token": tk(s4, "/my/login/2fa"), "code": codes[0]})
check("a recovery code signs in", "Sign out" in r.text and "recovery code" in r.text)
s5, r = login(); r = s5.post(B + "/my/login/2fa", data={"csrf_token": tk(s5, "/my/login/2fa"), "code": codes[0]})
check("and works only once", "Sign out" not in r.text)
check("the audit log notes recovery-code sign-ins", "login.recovery_code" in sess("marty", PW).get(B + "/admin/audit?q=tfuser1").text)
# can't walk past the second step
s6, r = login()
check("the second step cannot be skipped", s6.get(B + "/my/", allow_redirects=False).status_code == 302)
# new codes, then disable
r = s4.post(B + "/my/two-factor", data={"csrf_token": tk(s4, "/my/two-factor"), "action": "new_codes", "password": "wrong"}, allow_redirects=True)
check("new recovery codes need the password", "password is not right" in r.text)
r = s4.post(B + "/my/two-factor", data={"csrf_token": tk(s4, "/my/two-factor"), "action": "new_codes", "password": P})
check("with the password, ten new codes are made", len(found_codes(r.text)) == 10)
# admin reset
adm = sess("marty", PW)
uid = psql(f"SELECT id FROM users WHERE username='{U}'")
r = adm.post(B + f"/admin/users/{uid}", data={"csrf_token": tk(adm, "/admin/users"), "action": "reset_2fa"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("an administrator can reset someone's two-factor", r.status_code == 200 and psql(f"SELECT totp_enabled FROM users WHERE username='{U}'") == "f", r.text[:150])
s7, r = login(); check("after the reset the password alone works again", "Sign out" in r.text)
# required for administrators
psql("INSERT INTO settings (key, value) VALUES ('require_2fa_admins','1') ON CONFLICT (key) DO UPDATE SET value='1'")
try:
    a2 = sess("marty", PW)
    r = a2.get(B + "/admin/", allow_redirects=False)
    check("when required, an administrator without it is sent to set it up", r.status_code == 302 and "two-factor" in r.headers.get("Location", ""), r.status_code)
    check("they can still reach the set-up page and sign out", a2.get(B + "/my/two-factor").status_code == 200)
    check("ordinary users are not affected", requests.Session().get(B + "/").status_code == 200 and s7.get(B + "/my/", allow_redirects=False).status_code == 200)
finally:
    psql("INSERT INTO settings (key, value) VALUES ('require_2fa_admins','0') ON CONFLICT (key) DO UPDATE SET value='0'")
st = adm.get(B + "/admin/settings").text
check("the requirement is a setting", "Require Two-Factor Sign-In for Administrators" in st)
print("ALL PASS" if ok_all else "SOME FAILED")
