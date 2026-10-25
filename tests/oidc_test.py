"""The site as a sign-in provider for the Matrix server (app/oidcprovider.py): discovery, signing keys, the authorization code flow with PKCE, tokens and user info, and every way a request can be wrong."""
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
def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
def b64ud(t):
    return base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))
RET = "https://matrix.test.invalid/_synapse/client/oidc/callback"
psql("DELETE FROM settings WHERE key IN ('matrix_enabled','matrix_client_id','matrix_redirect_uris','oidc_code_seconds','oidc_token_seconds')")
anon = requests.Session()
check("switched off: nothing answers", all(anon.get(B + p).status_code == 404 for p in ("/.well-known/openid-configuration", "/oidc/jwks", "/oidc/authorize")))
setting("matrix_enabled", "1"); setting("matrix_redirect_uris", RET)
adm = sess("marty", PW)
j = adm.post(B + "/admin/matrix/secret", data={"csrf_token": tok(adm, "/admin/settings")}, headers={"X-Requested-With": "XMLHttpRequest"}).json()
SECRET = j["secret"]
check("an administrator can make the client secret, shown once", j["ok"] and len(SECRET) > 30 and j["client_id"] == "matrix")
check("an ordinary visitor cannot", requests.post(B + "/admin/matrix/secret", allow_redirects=False).status_code in (302, 400, 401, 403))
d = anon.get(B + "/.well-known/openid-configuration").json()
check("discovery names the issuer and the four endpoints", all(d[k].endswith(p) for k, p in (("authorization_endpoint", "/oidc/authorize"), ("token_endpoint", "/oidc/token"), ("userinfo_endpoint", "/oidc/userinfo"), ("jwks_uri", "/oidc/jwks"))) and "S256" in d["code_challenge_methods_supported"], d)
keys = anon.get(B + "/oidc/jwks").json()["keys"]
check("one RSA signing key is published", len(keys) == 1 and keys[0]["kty"] == "RSA" and keys[0]["alg"] == "RS256" and keys[0]["kid"])

VER = b64u(b"v" * 40)
CH = b64u(sha256(VER.encode()).digest())
def ask(s, **over):
    q = {"client_id": "matrix", "redirect_uri": RET, "response_type": "code", "scope": "openid profile email", "state": "st1", "nonce": "n1", "code_challenge": CH, "code_challenge_method": "S256"}
    q.update(over)
    return s.get(B + "/oidc/authorize", params=q, allow_redirects=False)
def newcode():
    return parse_qs(urlparse(ask(adm).headers["Location"]).query)["code"][0]
r = ask(anon)
check("not signed in: sent to the site's login page and brought back here", r.status_code == 302 and "login" in r.headers["Location"] and "oidc" in r.headers["Location"], r.headers.get("Location"))
check("an unregistered client or return address is refused outright", ask(adm, client_id="evil").status_code == 400 and ask(adm, redirect_uri="https://evil.invalid/cb").status_code == 400)
check("a wrong response type or missing openid scope goes back to the application as an error", "error=unsupported_response_type" in ask(adm, response_type="token").headers["Location"] and "error=invalid_scope" in ask(adm, scope="profile").headers["Location"])
setting("matrix_public_url", "https://matrix.test.invalid"); setting("matrix_redirect_uris", "")
check("with the return address setting empty, the one worked out from the Matrix Address is accepted by itself", ask(adm).status_code == 302 and "code=" in ask(adm).headers["Location"] and ask(adm, redirect_uri="https://evil.invalid/cb").status_code == 400)
setting("matrix_redirect_uris", RET)
check("plain PKCE is refused", "error=invalid_request" in ask(adm, code_challenge_method="plain").headers["Location"])
r = ask(adm)
loc = r.headers["Location"]
q = parse_qs(urlparse(loc).query)
check("a signed-in member is sent back with a code and the same state", r.status_code == 302 and loc.startswith(RET) and q["state"] == ["st1"] and q.get("code"), loc)
code = q["code"][0]
def tokreq(**over):
    f = {"grant_type": "authorization_code", "code": code, "redirect_uri": RET, "client_id": "matrix", "client_secret": SECRET, "code_verifier": VER}
    f.update(over)
    return requests.post(B + "/oidc/token", data=f)
check("a wrong client secret is refused", tokreq(client_secret="nope").status_code == 401)
check("a wrong PKCE verifier is refused (and the code is spent)", tokreq(code_verifier="x" * 43).status_code == 400 and tokreq().status_code == 400)
code = newcode()
check("a wrong return address is refused", tokreq(redirect_uri="https://evil.invalid/cb").status_code == 400)
code = newcode()
t = tokreq()
tj = t.json()
check("the right code gives an access token and an ID token", t.status_code == 200 and tj["token_type"] == "Bearer" and tj["id_token"].count(".") == 2 and tj["access_token"], t.text)
check("the code works only once", tokreq().status_code == 400)
head, body, sig = tj["id_token"].split(".")
claims = json.loads(b64ud(body))
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
pub = rsa.RSAPublicNumbers(int.from_bytes(b64ud(keys[0]["e"]), "big"), int.from_bytes(b64ud(keys[0]["n"]), "big")).public_key()
try:
    pub.verify(b64ud(sig), (head + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256()); sigok = True
except Exception:
    sigok = False
check("the ID token's signature checks against the published key", sigok and json.loads(b64ud(head))["kid"] == keys[0]["kid"])
check("its claims: issuer, audience, nonce, the member's number and user name, nothing private", claims["aud"] == "matrix" and claims["nonce"] == "n1" and claims["sub"].isdigit() and claims["preferred_username"] == "marty" and claims["exp"] > time.time() and "legal" not in body and claims["iss"] == d["issuer"], claims)
ui = requests.get(B + "/oidc/userinfo", headers={"Authorization": "Bearer " + tj["access_token"]})
check("user info answers for the token", ui.status_code == 200 and ui.json()["sub"] == claims["sub"] and ui.json()["preferred_username"] == "marty")
check("and refuses a wrong token", requests.get(B + "/oidc/userinfo", headers={"Authorization": "Bearer nope"}).status_code == 401 and requests.get(B + "/oidc/userinfo").status_code == 401)
basic = base64.b64encode(("matrix:" + SECRET).encode()).decode()
code = newcode()
check("client_secret_basic works too", requests.post(B + "/oidc/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": RET, "code_verifier": VER}, headers={"Authorization": "Basic " + basic}).status_code == 200)
code = newcode()
psql("UPDATE oidc_codes SET expires = now() - interval '1 minute'")
check("an expired code is refused", tokreq().status_code == 400)
code = newcode()
tk = tokreq().json()["access_token"]
psql("UPDATE users SET disabled = true WHERE username = 'marty'")
check("a disabled account's token stops working", requests.get(B + "/oidc/userinfo", headers={"Authorization": "Bearer " + tk}).status_code == 401)
psql("UPDATE users SET disabled = false WHERE username = 'marty'")
check("the sign-ins are in the audit log", "oidc.authorize" in psql("SELECT string_agg(DISTINCT action, ',') FROM audit_log WHERE action LIKE 'oidc.%'"))
check("the key and the secret are sealed in the database", all(psql(f"SELECT value FROM settings WHERE key='{k}'").startswith("gAAAA") for k in ("oidc_signing_key", "oidc_client_secret")))
psql("DELETE FROM settings WHERE key IN ('matrix_enabled','matrix_redirect_uris','matrix_public_url')")
print("ALL PASS" if ok_all else "SOME FAILED")
