"""Sign in with Google: nothing shows until it is configured; a member links an account, signs in with it, unlinks; every way a token can be wrong is refused; accounts are never linked by email alone; disabled
and two-factor accounts keep their rules; new accounts from Google only when allowed. A stand-in for Google's servers (tests/fake_oidc.py) runs inside the portal container."""
import base64, json, re, subprocess, time, requests
from hashlib import sha256
from urllib.parse import parse_qs, urlparse
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def unset(*keys):
    for k in keys:
        psql(f"DELETE FROM settings WHERE key = '{k}'")
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker {cmd}"], capture_output=True, text=True).stdout
def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
AJAX = {"X-Requested-With": "XMLHttpRequest"}
CID = "test-client.apps.googleusercontent.com"
PORT = 18990
K = ("google_link_by_email", "google_enabled", "google_client_id", "google_allow_signup", "google_auth_url", "google_token_url", "google_jwks_url", "google_client_secret", "registration_open")

# the stand-in for Google, inside the portal container
subprocess.run(["scp", "-q", "fake_oidc.py", "nastemp:adsb-portal/tests/fake_oidc.py"], check=True)
ssh("exec adsb-portal python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:%d/quit',timeout=3)\"" % PORT)
ssh("cp ~/adsb-portal/tests/fake_oidc.py adsb-portal:/tmp/fake_oidc.py")
ssh("exec -d adsb-portal python /tmp/fake_oidc.py %d" % PORT)
for _ in range(20):
    if '"keys"' in ssh("exec adsb-portal python -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:%d/certs',timeout=3).read().decode())\"" % PORT):
        break
    time.sleep(1)
else:
    raise SystemExit("the stand-in for Google did not start")

unset(*K)
psql("DELETE FROM login_events WHERE NOT success")
psql("DELETE FROM google_links")
psql("DELETE FROM users WHERE username LIKE 'gnew%' OR email LIKE '%@gtest.example'")
adm = sess("marty", PW)
me = sess("plain1", "plainpassword1")
other = sess("claimer1", "claimpassword1")
anon = requests.Session()

# nothing shows until it is configured
check("not configured: the sign-in page has no Google button and the address is a 404", "Sign In with Google" not in anon.get(B + "/my/login").text and anon.get(B + "/login/google").status_code == 404 and me.get(B + "/my/google").status_code == 404)
setting("google_enabled", "1"); setting("google_client_id", CID)
check("switched on with an ID but no secret: still nothing shows", "Sign In with Google" not in anon.get(B + "/my/login").text and anon.get(B + "/login/google").status_code == 404)
ta = re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + "/admin/google").text).group(1)
r = adm.post(B + "/admin/google/secret", data={"csrf_token": ta, "secret": "x"}, headers=AJAX)
check("a too-short secret is refused", not r.json()["ok"])
r = adm.post(B + "/admin/google/secret", data={"csrf_token": ta, "secret": "test-secret-123"}, headers=AJAX)
check("an administrator saves the secret", r.json()["ok"] and "test-secret-123" not in adm.get(B + "/admin/google").text)
check("the secret is stored sealed, not as text", "test-secret-123" not in psql("select value from settings where key = 'google_client_secret'") and psql("select count(*) from settings where key = 'google_client_secret' and value <> ''") == "1")
setting("google_auth_url", "https://accounts.example.test/o/oauth2/v2/auth")
setting("google_token_url", "http://127.0.0.1:%d/token" % PORT)
setting("google_jwks_url", "http://127.0.0.1:%d/certs" % PORT)
lp = anon.get(B + "/my/login").text
check("configured: the sign-in page has the Sign In with Google button", "Sign In with Google" in lp and 'href="/login/google"' in lp)
check("My Account has the Google Sign-In tab", 'href="/my/google"' in me.get(B + "/my/").text)
check("the admin menu lists Google Sign-In and the page shows the redirect address to give Google", 'href="/admin/google"' in adm.get(B + "/admin/").text and "/login/google/callback" in adm.get(B + "/admin/google").text)
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("Sign In with Google", "Google Client ID", "New Accounts from Google", "Google Sign-In Address", "Google Token Address", "Google Signing Keys Address", "Google Issuers Accepted")))

def begin(s, path="/login/google", **q):
    r = s.get(B + path, params=q, allow_redirects=False)
    loc = urlparse(r.headers.get("Location", ""))
    return r, {k: v[0] for k, v in parse_qs(loc.query).items()}, loc
def code_for(p, claims, **extra):
    c = {"claims": dict({"sub": "g-111", "email": "plain1@gtest.example", "email_verified": True, "name": "Plain One", "nonce": p["nonce"]}, **claims), "challenge": p["code_challenge"]}
    c.update(extra)
    return b64u(json.dumps(c).encode())
def finish(s, p, code, state=None):
    return s.get(B + "/login/google/callback", params={"code": code, "state": state or p["state"]}, allow_redirects=False)
def msg(s, r):
    return s.get(B + r.headers["Location"]).text if r.headers.get("Location", "").startswith("/") else ""
def who(s):
    t = s.get(B + "/my/").text
    return re.search(r'class="(?:acct|me)[^"]*"[^>]*>([^<]+)<', t) and True

# the start of a sign-in
s0 = requests.Session()
r, p, loc = begin(s0, next="/my/")
check("Sign In with Google sends the visitor to Google with the client ID, the redirect address, a state, a nonce and a PKCE challenge", r.status_code == 302 and loc.netloc == "accounts.example.test" and p["client_id"] == CID and p["redirect_uri"].endswith("/login/google/callback") and p["response_type"] == "code"
      and "openid" in p["scope"] and p["state"] and p["nonce"] and p["code_challenge_method"] == "S256" and p["code_challenge"], (r.status_code, loc, p))

# linking: a member links, another cannot take the same Google account
tl = me
r, p, _ = begin(me, "/my/google/link")
r = finish(me, p, code_for(p, {"sub": "g-111", "email": "plain1@gtest.example"}))
check("a member links a Google account", r.status_code == 302 and r.headers["Location"].endswith("/my/google") and psql("select email from google_links where sub = 'g-111'") == "plain1@gtest.example")
check("the My Google Sign-In page shows it linked, with a way to unlink", "plain1@gtest.example" in me.get(B + "/my/google").text and "Unlink Google" in me.get(B + "/my/google").text)
r, p, _ = begin(other, "/my/google/link")
r = finish(other, p, code_for(p, {"sub": "g-111", "email": "plain1@gtest.example"}))
check("another member cannot link the same Google account", "already linked to another member" in msg(other, r) and psql("select count(*) from google_links") == "1")
r, p, _ = begin(me, "/my/google/link")
r = finish(me, p, code_for(p, {"sub": "g-222", "email": "second@gtest.example"}))
check("one member cannot link two Google accounts", "already have a Google account linked" in msg(me, r) and psql("select count(*) from google_links") == "1")

# signing in
s1 = requests.Session()
r, p, _ = begin(s1, next="/my/")
r = finish(s1, p, code_for(p, {"sub": "g-111"}))
check("a linked Google account signs the member in and goes on to the page asked for", r.status_code == 302 and r.headers["Location"] == "/my/" and "Sign Out" in s1.get(B + "/my/").text or "plain1" in s1.get(B + "/my/").text, r.headers.get("Location"))
check("it is in the sign-in history as a Google sign-in", psql("select count(*) from login_events where username = 'plain1' and success and detail = 'google'") != "0")
r = finish(s1, p, code_for(p, {"sub": "g-111"}))
check("the same state cannot be used twice", r.status_code == 302 and r.headers["Location"].endswith("/my/login"))

def refused(claims=None, why="", **extra):
    psql("DELETE FROM login_events WHERE NOT success")   # many refusals from one address would trip the sign-in guard, which is not what is tested here
    s = requests.Session()
    r, p, _ = begin(s)
    r2 = finish(s, p, code_for(p, claims or {}, **extra))
    page = msg(s, r2)
    return r2.status_code == 302 and r2.headers["Location"].endswith("/my/login") and "Sign Out" not in s.get(B + "/my/").text and (why in page), page[:0]
ok, _x = refused({}, "signature is wrong", bad_sig=True)
check("a token signed with the wrong key is refused", ok)
ok, _x = refused({"aud": "someone-else.apps.googleusercontent.com"}, "different application")
check("a token made for another application is refused", ok)
ok, _x = refused({"iss": "https://evil.example"}, "issuer this site does not accept")
check("a token from the wrong issuer is refused", ok)
ok, _x = refused({"exp": int(time.time()) - 3600}, "has expired")
check("an expired token is refused", ok)
ok, _x = refused({"nonce": "not-the-one"}, "did not start here")
check("a token whose nonce is not the one this sign-in started with is refused", ok)
s2 = requests.Session()
r, p, _ = begin(s2)
r = s2.get(B + "/login/google/callback", params={"code": code_for(p, {}), "state": "wrong-state"}, allow_redirects=False)
check("a wrong state is refused", r.status_code == 302 and "took too long or did not start here" in msg(s2, r))
s2 = requests.Session()
r, p, _ = begin(s2)
bad = b64u(json.dumps({"claims": {"sub": "g-111", "nonce": p["nonce"]}, "challenge": "not-the-challenge"}).encode())
r = finish(s2, p, bad)
check("a code that does not match the PKCE challenge is refused by Google and the site says it could not finish", r.status_code == 302 and "Could not reach Google" in msg(s2, r))
s2 = requests.Session()
r, p, _ = begin(s2)
r = s2.get(B + "/login/google/callback", params={"error": "access_denied", "state": p["state"]}, allow_redirects=False)
check("choosing not to allow it at Google comes back with a plain message", "chose not to allow it" in msg(s2, r))
s2 = requests.Session()
r = s2.get(B + "/login/google/callback", params={"code": "x", "state": "y"}, allow_redirects=False)
check("a callback with no sign-in started is refused", r.status_code == 302 and r.headers["Location"].endswith("/my/login"))
check("every refusal is in the audit log", "google.refused" in adm.get(B + "/admin/audit").text)

# an unlinked Google account, even with a matching email, is not signed in
ok, _x = refused({"sub": "g-777", "email": "plain1@gtest.example"}, "No account is linked")
check("an unlinked Google account is not signed in, even when its email matches an account", ok)
# disabled and locked accounts
psql("UPDATE users SET disabled = TRUE WHERE username = 'plain1'")
ok, _x = refused({"sub": "g-111"}, "disabled")
check("a disabled account cannot sign in with Google either", ok)
psql("UPDATE users SET disabled = FALSE WHERE username = 'plain1'")
# two-factor
psql("UPDATE users SET totp_enabled = TRUE WHERE username = 'plain1'")
s3 = requests.Session()
r, p, _ = begin(s3)
r = finish(s3, p, code_for(p, {"sub": "g-111"}))
check("a member with two-factor sign-in still gets the code step", r.status_code == 302 and r.headers["Location"].endswith("/login/2fa") and "Sign Out" not in s3.get(B + "/my/").text)
psql("UPDATE users SET totp_enabled = FALSE WHERE username = 'plain1'")

# an existing account is not linked by email alone unless that setting is on
psql("DELETE FROM google_links WHERE user_id = (select id from users where username = 'plain1')")
uemail = psql("select email from users where username = 'plain1'")
ok, _x = refused({"sub": "g-880", "email": uemail}, "No account is linked")
check("by default a Google account whose verified email matches an existing account is NOT linked or signed in, and no second account is made", ok and psql("select count(*) from users where lower(email) = lower('" + uemail + "')") == "1" and psql("select count(*) from google_links where sub = 'g-880'") == "0")
setting("google_link_by_email", "1")
ok, _x = refused({"sub": "g-881", "email": uemail, "email_verified": False}, "No account is linked")
check("with the setting on, an email Google has not verified still links nothing", ok and psql("select count(*) from google_links where sub = 'g-881'") == "0")
s5 = requests.Session()
r, p, _ = begin(s5, next="/my/")
r = finish(s5, p, code_for(p, {"sub": "g-882", "email": uemail.upper()}))
check("with the setting on, a verified email that matches an existing account links it and signs in, without a second account", r.status_code == 302 and r.headers["Location"] == "/my/" and psql("select count(*) from google_links where sub = 'g-882' and user_id = (select id from users where username = 'plain1')") == "1" and psql("select count(*) from users where lower(email) = lower('" + uemail + "')") == "1")
check("it is in the audit log", "google.autolink" in adm.get(B + "/admin/audit").text)
psql("DELETE FROM google_links WHERE sub = 'g-882'")
psql("DELETE FROM settings WHERE key = 'google_link_by_email'")
psql("INSERT INTO google_links (sub, user_id, email) SELECT 'g-111', id, 'plain1@gtest.example' FROM users WHERE username = 'plain1' ON CONFLICT DO NOTHING")   # as it was before this block

# new accounts from Google
ok, _x = refused({"sub": "g-900", "email": "gnewbie@gtest.example"}, "No account is linked")
check("with new accounts off, an unknown Google account gets no account", ok and psql("select count(*) from users where email = 'gnewbie@gtest.example'") == "0")
setting("google_allow_signup", "1"); setting("registration_open", "1")
s4 = requests.Session()
r, p, _ = begin(s4, next="/my/")
r = finish(s4, p, code_for(p, {"sub": "g-900", "email": "gnewbie@gtest.example", "name": "New Person"}))
check("with it on and registration open, a verified Google account gets a new account and is signed in", r.status_code == 302 and r.headers["Location"] == "/my/" and psql("select count(*) from users where email = 'gnewbie@gtest.example'") == "1" and psql("select created_by_signup from google_links where sub = 'g-900'") == "t")
ok, _x = refused({"sub": "g-901", "email": "gnewbie@gtest.example"}, "already exists")
check("an email an account already uses is not given a second account", ok)
ok, _x = refused({"sub": "g-902", "email": "gunver@gtest.example", "email_verified": False}, "No account is linked")
check("a Google account whose email is not verified gets no account", ok and psql("select count(*) from users where email = 'gunver@gtest.example'") == "0")
setting("registration_open", "0")
ok, _x = refused({"sub": "g-903", "email": "gclosed@gtest.example"}, "No account is linked")
check("with registration closed, no account is made even when Google sign-up is on", ok)
setting("registration_open", "1")
check("an account made from Google says so, with the advice to set a password before unlinking", "Forgot Password" in s4.get(B + "/my/google").text)

# unlinking and the administrator
t = re.search(r'name="csrf_token" value="([^"]+)"', me.get(B + "/my/google").text).group(1)
r = me.post(B + "/my/google/unlink", data={"csrf_token": t}, headers=AJAX)
setting("google_allow_signup", "0")   # the made-up email below would otherwise get a new account
unl = (r.status_code, r.text[:150], psql("select count(*) from google_links where sub = 'g-111'"))
nl_ = refused({"sub": "g-111"}, "No account is linked")
check("a member unlinks Google, and then Google no longer signs them in", r.json()["ok"] and unl[2] == "0" and nl_[0], (unl, nl_))
r, p, _ = begin(me, "/my/google/link")
rl_ = finish(me, p, code_for(p, {"sub": "g-111"}))
uid = psql("select id from users where username = 'plain1'")
pg_ = adm.get(B + f"/admin/users/{uid}")
check("an administrator sees the link on the member's page and can remove it", "Remove Their Google Link" in pg_.text, (pg_.status_code, uid, psql("select sub, user_id from google_links"), rl_.headers.get("Location"), msg(me, rl_)[-300:]))
ta = re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + f"/admin/users/{uid}").text).group(1)
r = adm.post(B + f"/admin/users/{uid}/security", data={"csrf_token": ta, "action": "remove_google"}, headers=AJAX)
check("removing it works", r.status_code == 200 and psql("select count(*) from google_links where user_id = " + uid) == "0", r.text[:200])
au = adm.get(B + "/admin/audit").text
check("link, unlink, sign-up, secret and the administrator's action are in the audit log", all(x in au for x in ("google.link", "google.unlink", "google.signup", "google.secret_saved", "user.remove_google")))
r = adm.post(B + "/admin/google/secret", data={"csrf_token": ta, "clear": "1"}, headers=AJAX)
check("removing the secret switches Google sign-in off again", r.json()["ok"] and "Sign In with Google" not in anon.get(B + "/my/login").text)
check("the Google link table is in a restore area", "google_links" in open("../app/restorepart.py", encoding="utf-8").read())

ssh("exec adsb-portal python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:%d/quit',timeout=3)\"" % PORT)
unset(*K)
psql("DELETE FROM google_links")
psql("DELETE FROM users WHERE username LIKE 'gnew%' OR email LIKE '%@gtest.example'")
print("ALL PASS" if ok_all else "SOME FAILED")
