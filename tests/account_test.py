import hashlib, re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)
    ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def tok(s, page): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + page).text).group(1)
def flashes(r): return " ".join(re.findall(r'class="flash[^>]*>([^<]*)', r.text))
def register(name, pw):
    s = requests.Session()
    s.post(B + "/my/register", data={"csrf_token": tok(s, "/my/register"), "username": name, "email": f"{name}@example.com", "password": pw, "confirm": pw})
    return s
def login(login_name, pw):
    s = requests.Session()
    r = s.post(B + "/my/login", data={"csrf_token": tok(s, "/my/login"), "username": login_name, "password": pw})
    return s, r
adm = sess("marty", PW)
for n in ("acctuser1", "acctuser2", "acctuser3"):
    psql(f"DELETE FROM users WHERE username='{n}'")
psql("DELETE FROM reset_attempts")

# ---- sign in with username or email
register("acctuser1", "acctpassword1")
s, r = login("acctuser1", "acctpassword1"); check("sign in with the username", "/my/login" not in r.url and "Sign out" in r.text)
s, r = login("AcctUser1@Example.COM", "acctpassword1"); check("sign in with the email address, any capitalization", "Sign out" in r.text, flashes(r))
s, r = login("acctuser1@example.com", "wrong-password"); check("a wrong password still fails", "Incorrect username or password" in flashes(r))
check("the sign-in page says username or email and links Forgot Password and Contact", all(t in requests.get(B + "/my/login").text for t in ("Username or email address", "/my/forgot", "/contact")))

# ---- forgot password
fp = requests.get(B + "/my/forgot").text
check("forgot page offers email and station methods", "Reset by Email" in fp and "Reset With a Station You Own" in fp)
check("email reset says so when email is not set up", "Email is not set up" in fp)
ff = requests.Session()
r = ff.post(B + "/my/forgot/email", data={"csrf_token": tok(ff, "/my/forgot"), "login": "acctuser1"}, allow_redirects=True)
check("email reset without email set up gives a clear message", "Email is not set up" in flashes(r), flashes(r))

uid = psql("SELECT id FROM users WHERE username='acctuser1'")
UUID = "acc70000-1111-2222-3333-444455556666"
psql(f"DELETE FROM feeders WHERE uuid='{UUID}'")
psql(f"INSERT INTO feeders (uuid, station_id, name, owner_id, online) VALUES ('{UUID}', 'ACCT-1', 'Account Test Station', {uid}, FALSE)")
def station_reset(login_name, uuid, new, confirm=None):
    c = requests.Session()
    return c.post(B + "/my/forgot/station", data={"csrf_token": tok(c, "/my/forgot"), "login": login_name, "uuid": uuid, "new": new, "confirm": confirm or new})
check("a wrong station UUID is refused with a generic message", "do not match" in flashes(station_reset("acctuser1", "00000000-0000-0000-0000-000000000000", "newpassword-one")))
check("a mismatched confirmation is refused", "do not match" in flashes(station_reset("acctuser1", UUID, "newpassword-one", "different-pass")))
check("a short password is refused", "at least" in flashes(station_reset("acctuser1", UUID, "short")))
r = station_reset("acctuser1", UUID, "newpassword-one")
check("the right UUID resets the password", "password has been changed" in flashes(r), flashes(r))
s, r = login("acctuser1", "newpassword-one"); check("the new password works", "Sign out" in r.text)
s, r = login("acctuser1", "acctpassword1"); check("the old password no longer works", "Incorrect" in flashes(r))
register("acctuser2", "acctpassword2")
check("someone else's UUID does not work for another account", "do not match" in flashes(station_reset("acctuser2", UUID, "newpassword-two")))
check("resets are audited", "password.reset_station" in adm.get(B + "/admin/audit?action=password.reset_station").text)
for i in range(6):
    r = station_reset("acctuser2", f"00000000-0000-0000-0000-00000000000{i}", "newpassword-two")
check("repeated guessing is throttled", "Too many recovery attempts" in flashes(r), flashes(r))
psql("DELETE FROM reset_attempts")

# ---- email reset links (the token is stored as a hash; make one the way the app does)
TOKEN = "test-token-for-reset-0123456789abcdef"
h = hashlib.sha256(TOKEN.encode()).hexdigest()
psql(f"INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES ({uid}, '{h}', now() + interval '10 minutes')")
c = requests.Session()
check("a valid reset link opens the new-password form", requests.get(B + f"/my/reset/{TOKEN}").status_code == 200)
r = c.post(B + f"/my/reset/{TOKEN}", data={"csrf_token": tok(c, f"/my/reset/{TOKEN}"), "new": "emailreset-pass1", "confirm": "emailreset-pass1"})
check("the link sets the password", "password has been changed" in flashes(r), flashes(r))
check("the link works only once", requests.get(B + f"/my/reset/{TOKEN}").status_code == 400)
OLD = "expired-token-0123456789abcdefghij"
psql(f"INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES ({uid}, '{hashlib.sha256(OLD.encode()).hexdigest()}', now() - interval '1 minute')")
check("an expired link is refused", requests.get(B + f"/my/reset/{OLD}").status_code == 400)
check("a made-up link is refused", requests.get(B + "/my/reset/not-a-real-token").status_code == 400)
s, r = login("acctuser1", "emailreset-pass1"); check("sign in with the password set by email", "Sign out" in r.text)

# ---- alert notice
psql(f"INSERT INTO alert_rules (user_id, name, kind, value) VALUES ({uid}, 'Notice rule', 'icao', 'ffffff')")
rid = psql(f"SELECT id FROM alert_rules WHERE user_id={uid} AND name='Notice rule'")
for i in range(3):
    psql(f"INSERT INTO alert_events (rule_id, user_id, hex, callsign) VALUES ({rid}, {uid}, 'abc00{i}', 'NTC{i}')")
check("signed-in pages show a banner and menu badge for unseen alerts", 'id="alert-banner-n">3<' in s.get(B + "/feeders/").text and 'id="nav-alerts-n">3<' in s.get(B + "/feeders/").text)
check("the count is available for the page to refresh", s.get(B + "/my/alerts/count.json").json() == {"unseen": 3})
al = s.get(B + "/my/alerts").text
check("the alerts page marks the new matches", al.count('>New<') == 3, al.count('>New<'))
check("viewing the alerts clears the notice", s.get(B + "/my/alerts/count.json").json() == {"unseen": 0} and 'id="alert-banner" class="panel" style="display:none' in s.get(B + "/feeders/").text)
check("visitors see no alert notice", 'id="alert-banner"' not in requests.get(B + "/feeders/").text)

# ---- delete account
def delete(sess_, pw, text="DELETE"):
    return sess_.post(B + "/my/profile/delete", data={"csrf_token": tok(sess_, "/my/profile"), "current_password": pw, "confirm_text": text})
check("profile offers Delete My Account", "Delete My Account" in s.get(B + "/my/profile").text)
check("deletion needs the right password", "password is not correct" in flashes(delete(s, "wrong-password")))
check("deletion needs the word DELETE", "Type DELETE" in flashes(delete(s, "emailreset-pass1", "delete")))
last = delete(adm, PW)
check("the last administrator cannot delete themselves", "last administrator" in flashes(last), flashes(last))
check("the administrator account still exists", psql("SELECT COUNT(*) FROM users WHERE username='marty'") == "1")
r = delete(s, "emailreset-pass1")
check("a correct request deletes the account and signs out", "has been deleted" in flashes(r), flashes(r))
check("the account is gone", psql("SELECT COUNT(*) FROM users WHERE username='acctuser1'") == "0")
check("its alert rules and history are gone", psql(f"SELECT COUNT(*) FROM alert_rules WHERE user_id={uid}") == "0")
check("its station was released and cleared", psql(f"SELECT coalesce(owner_id::text,'none')||'|'||coalesce(station_id,'none')||'|'||coalesce(name,'none') FROM feeders WHERE uuid='{UUID}'") == "none|none|none")
s2, r = login("acctuser1", "emailreset-pass1"); check("sign in fails afterwards", "Incorrect" in flashes(r))
check("deletion is audited", "user.delete" in adm.get(B + "/admin/audit?action=user.delete").text)
psql(f"DELETE FROM feeders WHERE uuid='{UUID}'")

# ---- contacts
psql("DELETE FROM site_contacts")
check("the contact page says when nothing is listed", "No contact details have been added yet" in requests.get(B + "/contact").text)
def csave(**kw):
    d = dict(id="", name="Marty", role="Site administrator", email="admin@example.org", url="", notes="", sort_order="10", shown="1"); d.update(kw)
    for k in [k for k, v in d.items() if v is None]: d.pop(k)
    return act(adm, "/admin/contacts/save", "/admin/contacts", **d)
ok, m, html = csave(); check("admin adds a contact", ok and "Marty" in html, m)
ct = requests.get(B + "/contact").text
check("the public page lists it with a mail link", "Site administrator" in ct and 'href="mailto:admin@example.org"' in ct)
for name, kw in [("no name", dict(name="")), ("bad email", dict(email="nope")), ("bad web address", dict(url="javascript:alert(1)")), ("nothing to reach them by", dict(email="", url="", notes="")), ("bad order", dict(sort_order="x"))]:
    ok, m, _ = csave(**kw); check("reject contact: " + name, not ok, m)
ok, m, _ = csave(name="Hidden Person", shown=None, sort_order="20"); check("add a hidden contact", ok, m)
check("hidden contacts are not public", "Hidden Person" not in requests.get(B + "/contact").text)
check("footer and sign-in link to Contact", "/contact" in requests.get(B + "/feeders/").text.split("</footer>")[0])
ids = re.findall(r'id="contact-(\d+)"', adm.get(B + "/admin/contacts").text)
for i in ids: act(adm, f"/admin/contacts/{i}/delete", "/admin/contacts")
check("contacts can be deleted", psql("SELECT COUNT(*) FROM site_contacts") == "0")
for n in ("acctuser2", "acctuser3"): psql(f"DELETE FROM users WHERE username='{n}'")
print("ALL PASS" if ok_all else "SOME FAILED")
