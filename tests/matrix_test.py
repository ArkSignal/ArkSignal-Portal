"""The Matrix service: the admin page, Apply (settings and the sign-in client go to the service, Synapse starts), and a real sign-in end to end: Synapse sends the browser to the site, the site signs the member in and
hands back a code, Synapse swaps it at the site's token address and makes the Matrix user. Needs `sh tests/deploy_matrix.sh fresh` first (a throwaway Synapse and database on the test box, port 18008)."""
import html, json, re, subprocess, time, requests
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
HS = "http://test-box.invalid:18008"
SERVER = "matrix-test.local"
KEYS = ("matrix_enabled", "matrix_server_name", "matrix_public_url", "matrix_issuer", "matrix_oidc_internal", "matrix_redirect_uris", "matrix_url_previews", "oidc_signing_key", "oidc_client_secret")
psql("DELETE FROM settings WHERE key IN (" + ",".join(f"'{k}'" for k in KEYS) + ")")
adm = sess("marty", PW)
page = adm.get(B + "/admin/matrix")
check("the admin page needs no setup to open and explains the state", page.status_code == 200 and "Matrix Chat" in page.text and "Setting It Up" in page.text)
setting("matrix_enabled", "1")
page = adm.get(B + "/admin/matrix").text
if "service not answering" in page:
    print("SKIP matrix_test: the Matrix service is not running on the test box (sh tests/deploy_matrix.sh fresh)"); psql("DELETE FROM settings WHERE key = 'matrix_enabled'"); raise SystemExit(0)
check("the service is up and waiting for its settings (or already given them by an earlier run)", "waiting for settings" in page or "running" in page or "starting or stopped" in page, re.sub(r"\s+", " ", page)[:300])
ok, msg, _h = act(adm, "/admin/matrix/apply", "/admin/matrix")
check("Apply with the settings not filled in says what is missing and sends nothing", not ok and "Not ready" in msg and "Main Address" in msg, msg)
setting("matrix_server_name", SERVER)
setting("matrix_public_url", HS)
setting("matrix_issuer", B)
setting("matrix_oidc_internal", "http://adsb-portal:8430")
setting("matrix_redirect_uris", HS + "/_synapse/client/oidc/callback")
setting("matrix_url_previews", "0")
ok, msg, _h = act(adm, "/admin/matrix/apply", "/admin/matrix")
check("Apply sends the settings and starts Synapse", ok, msg)
up = False
for _ in range(60):
    try:
        if requests.get(HS + "/health", timeout=3).status_code == 200:
            up = True; break
    except requests.RequestException:
        pass
    time.sleep(3)
check("Synapse answers its health check", up)
act(adm, "/admin/matrix/sync", "/admin/matrix")   # accounts locked by an earlier run or suite are unlocked before anyone signs in
page = adm.get(B + "/admin/matrix").text
check("the admin page shows it running with the server name", 'class="tag live">running' in page and SERVER in page)
fl = requests.get(HS + "/_matrix/client/v3/login").json()["flows"]
types = [f["type"] for f in fl]
check("Matrix offers sign-in through the site only: no password login", "m.login.sso" in types and "m.login.password" not in types, fl)
idp = [i["id"] for f in fl if f["type"] == "m.login.sso" for i in f.get("identity_providers", [])]
check("the one provider is the site", idp == ["oidc-arksignal"], idp)
reg = requests.post(HS + "/_matrix/client/v3/register", json={"username": "intruder", "password": "x" * 12, "auth": {"type": "m.login.dummy"}})
check("nobody can register on Matrix itself", reg.status_code in (400, 401, 403) and "access_token" not in reg.text, reg.text[:200])

def sso_login(portal_session):
    """Walk the browser flow: Synapse -> the site (signed in) -> Synapse -> the client with a login token."""
    s = portal_session
    r = s.get(HS + f"/_matrix/client/v3/login/sso/redirect/oidc-arksignal", params={"redirectUrl": "http://localhost:9/done"}, allow_redirects=False)
    if r.status_code != 302:
        return None, "no redirect from Synapse: " + str(r.status_code) + r.text[:200]
    for c in r.headers.get("Set-Cookie", "").split(", "):   # Synapse marks its sign-in cookie Secure; the test box speaks plain http, so keep it by hand
        if "=" in c.split(";")[0] and "oidc" in c.split("=")[0]:
            s.cookies.set(c.split("=")[0].strip(), c.split(";")[0].split("=", 1)[1], domain=urlparse(HS).hostname, path="/")
    r = s.get(r.headers["Location"], allow_redirects=False)           # the site's authorize address
    if r.status_code != 302 or not r.headers["Location"].startswith(HS):
        return None, "the site did not send us back to Matrix: " + r.headers.get("Location", str(r.status_code))
    r = s.get(r.headers["Location"], allow_redirects=False)           # Synapse's callback: it swaps the code at the site
    for _ in range(4):
        if r.status_code in (301, 302, 303) and r.headers.get("Location"):
            loc = r.headers["Location"]
            if loc.startswith("http://localhost:9/done"):
                break
            r = s.get(loc if loc.startswith("http") else HS + loc, allow_redirects=False)
        else:
            break
    else:
        loc = ""
    if r.status_code not in (301, 302, 303):
        m = re.search(r'href="([^"]*loginToken=[^"]+)"', r.text) or re.search(r'(http://localhost:9/done[^"\' <]*loginToken=[^"\' <]+)', r.text)
        if not m:
            return None, "no login token in Synapse's answer: " + re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))[:300]
        loc = html.unescape(m.group(1))
    token = parse_qs(urlparse(loc).query).get("loginToken", [""])[0]
    if not token:
        return None, "no login token in " + loc
    j = requests.post(HS + "/_matrix/client/v3/login", json={"type": "m.login.token", "token": token}).json()
    return j, ""

j, why = sso_login(adm)
check("a member signs in to Matrix through the site and gets a Matrix account named for them", j is not None and j.get("user_id") == f"@marty:{SERVER}" and j.get("access_token"), why or j)
if j and j.get("access_token"):
    me = requests.get(HS + "/_matrix/client/v3/account/whoami", headers={"Authorization": "Bearer " + j["access_token"]}).json()
    check("the access token works", me.get("user_id") == f"@marty:{SERVER}", me)
    prof = requests.get(HS + f"/_matrix/client/v3/profile/@marty:{SERVER}").json()
    check("their display name comes from the site", bool(prof.get("displayname")), prof)
j2, why = sso_login(adm)
check("signing in again gives the same Matrix account", j2 is not None and j2.get("user_id") == f"@marty:{SERVER}", why or j2)
anon_flow = requests.Session()
r = anon_flow.get(HS + "/_matrix/client/v3/login/sso/redirect/oidc-arksignal", params={"redirectUrl": "http://localhost:9/done"}, allow_redirects=False)
r2 = anon_flow.get(r.headers["Location"], allow_redirects=False)
check("someone not signed in to the site is sent to the site's sign-in page first", r2.status_code == 302 and "login" in r2.headers["Location"])
check("the status page lists a Matrix check", "Matrix Chat" in adm.get(B + "/admin/status").text)
check("sign-ins are in the audit log", "oidc.authorize" in psql("SELECT string_agg(DISTINCT action, ',') FROM audit_log WHERE action LIKE 'oidc.%' OR action LIKE 'matrix.%'") and "matrix.apply" in psql("SELECT string_agg(DISTINCT action, ',') FROM audit_log WHERE action LIKE 'matrix.%'"))
ok, msg, _h = act(adm, "/admin/matrix/rotate", "/admin/matrix")
check("a new sign-in secret is made and sent, and sign-in still works after Synapse restarts", ok, msg)
for _ in range(40):
    try:
        if requests.get(HS + "/health", timeout=3).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(3)
j3, why = sso_login(adm)
check("the new secret works", j3 is not None and j3.get("user_id") == f"@marty:{SERVER}", why or j3)
psql("DELETE FROM settings WHERE key IN ('matrix_enabled','matrix_url_previews')")
print("ALL PASS" if ok_all else "SOME FAILED")
