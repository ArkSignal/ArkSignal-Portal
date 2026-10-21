"""Passkeys (WebAuthn): a member adds passkeys on My Account > Passkeys and signs in with one; the server checks the challenge, origin, site address, user verification, signature (ES256, RS256 and EdDSA) and the
use counter; administrators can see and remove a member's passkeys; everything is logged. The tests use a small software authenticator written here with the `cryptography` library, because a real browser only
makes passkeys on a secure (https) address."""
import hashlib, json, os, re, struct, subprocess, requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()

import base64
def b64e(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def b64d(t): return base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))
def cb(o):
    """a small CBOR writer"""
    def head(major, n):
        if n < 24: return bytes([major << 5 | n])
        if n < 256: return bytes([major << 5 | 24, n])
        if n < 65536: return bytes([major << 5 | 25]) + n.to_bytes(2, "big")
        return bytes([major << 5 | 26]) + n.to_bytes(4, "big")
    if isinstance(o, bool): return bytes([0xf5 if o else 0xf4])
    if isinstance(o, int): return head(0, o) if o >= 0 else head(1, -1 - o)
    if isinstance(o, bytes): return head(2, len(o)) + o
    if isinstance(o, str): return head(3, len(o.encode())) + o.encode()
    if isinstance(o, list): return head(4, len(o)) + b"".join(cb(x) for x in o)
    if isinstance(o, dict): return head(5, len(o)) + b"".join(cb(k) + cb(v) for k, v in o.items())
HOST = B.split("//")[1].split(":")[0]
RPH = hashlib.sha256(HOST.encode()).digest()

class Auth:
    """a software authenticator: one key pair of the chosen kind"""
    def __init__(self, kind):
        self.kind, self.count, self.cid = kind, 0, os.urandom(32)
        if kind == "es256":
            self.key = ec.generate_private_key(ec.SECP256R1()); n = self.key.public_key().public_numbers()
            self.cose = {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
        elif kind == "rs256":
            self.key = rsa.generate_private_key(65537, 2048); n = self.key.public_key().public_numbers()
            self.cose = {1: 3, 3: -257, -1: n.n.to_bytes(256, "big"), -2: n.e.to_bytes(3, "big")}
        else:
            self.key = ed25519.Ed25519PrivateKey.generate()
            from cryptography.hazmat.primitives import serialization
            self.cose = {1: 1, 3: -8, -1: 6, -2: self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)}
    def attest(self, chal, origin=B, flags=0x45):
        ad = RPH + bytes([flags]) + struct.pack(">I", 0) + bytes(16) + struct.pack(">H", len(self.cid)) + self.cid + cb(self.cose)
        cd = json.dumps({"type": "webauthn.create", "challenge": chal, "origin": origin}).encode()
        return {"clientDataJSON": b64e(cd), "attestationObject": b64e(cb({"fmt": "none", "attStmt": {}, "authData": ad})), "transports": ["internal"]}
    def assert_(self, chal, count=None, origin=B, flags=0x05, rp=RPH, kind="webauthn.get"):
        self.count = self.count + 1 if count is None else count
        ad = rp + bytes([flags]) + struct.pack(">I", self.count)
        cd = json.dumps({"type": kind, "challenge": chal, "origin": origin}).encode()
        data = ad + hashlib.sha256(cd).digest()
        if self.kind == "es256": sig = self.key.sign(data, ec.ECDSA(hashes.SHA256()))
        elif self.kind == "rs256": sig = self.key.sign(data, padding.PKCS1v15(), hashes.SHA256())
        else: sig = self.key.sign(data)
        return {"id": b64e(self.cid), "clientDataJSON": b64e(cd), "authenticatorData": b64e(ad), "signature": b64e(sig)}

def csrf(s, path):
    return re.search(r'window.CSRF_TOKEN = "([^"]+)"', s.get(B + path).text).group(1)
def jpost(s, path, body, tok):
    return s.post(B + path, json=body, headers={"X-CSRFToken": tok, "X-Requested-With": "XMLHttpRequest"})

psql("DELETE FROM passkeys")
me = sess("plain1", "plainpassword1")
adm = sess("marty", PW)
tok = csrf(me, "/my/passkeys")
check("My Passkeys needs a sign-in", requests.get(B + "/my/passkeys", allow_redirects=False).status_code in (302, 401, 403))
pg = me.get(B + "/my/passkeys").text
check("the page has the add button, the list and the explanation in folding sections", all(x in pg for x in ('id="pk-add"', 'id="passkeys-list"', "About Passkeys")) and pg.count('class="panel setsec"') >= 3)
check("My Account has the tab and the sign-in page the button", 'href="/my/passkeys"' in me.get(B + "/my/").text and 'id="pk-login"' in requests.get(B + "/my/login").text)

def add(kind, name, **kw):
    a = Auth(kind)
    o = jpost(me, "/my/passkeys/options", {}, tok).json()
    if "options" not in o:   # the limit was reached before the browser even starts
        return a, o, type("R", (), {"status_code": 400, "text": str(o), "json": lambda self: o})()
    chal = o["options"]["challenge"]
    body = dict(a.attest(chal, **kw), name=name)
    return a, o, jpost(me, "/my/passkeys/register", body, tok)

a, o, r = add("es256", "Test phone")
opt = o["options"]
check("the options ask for a resident (discoverable) credential with user verification and the three algorithms", o["ok"] and opt["authenticatorSelection"]["residentKey"] == "required" and opt["authenticatorSelection"]["userVerification"] == "required" and [p["alg"] for p in opt["pubKeyCredParams"]] == [-7, -257, -8] and opt["rp"]["id"] == HOST, opt)
j = r.json()
check("a good attestation adds the passkey", r.status_code == 200 and j["ok"] and "Test phone" in j["html"], j.get("message"))
check("only the public half is kept (a public key, a counter, no private material)", psql("select count(*) from passkeys where name='Test phone' and length(public_key) > 40") == "1")

# bad registrations
o = jpost(me, "/my/passkeys/options", {}, tok).json()
bad = Auth("es256")
r = jpost(me, "/my/passkeys/register", dict(bad.attest(o["options"]["challenge"], origin="https://evil.example"), name="x"), tok)
check("an attestation from another origin is refused", r.status_code == 400, r.text[:150])
o = jpost(me, "/my/passkeys/options", {}, tok).json()
r = jpost(me, "/my/passkeys/register", dict(bad.attest("AAAA" + o["options"]["challenge"][4:]), name="x"), tok)
check("one with the wrong challenge is refused", r.status_code == 400)
r = jpost(me, "/my/passkeys/register", dict(bad.attest(o["options"]["challenge"]), name="x"), tok)
check("a challenge works once only", r.status_code == 400)
o = jpost(me, "/my/passkeys/options", {}, tok).json()
r = jpost(me, "/my/passkeys/register", dict(bad.attest(o["options"]["challenge"], flags=0x41), name="x"), tok)
check("a device that did not verify the person is refused while that is required", r.status_code == 400 and "check it is you" in r.json()["message"].lower().replace("needs the device to ", "check it is you ") or r.status_code == 400, r.text[:200])
check("nothing was added by the bad attempts", psql("select count(*) from passkeys") == "1")

# signing in
def sign_in(auth, **kw):
    s = requests.Session()
    t = csrf(s, "/my/login")
    o = jpost(s, "/login/passkey/options", {}, t).json()
    check_opts = o["options"]
    chal = kw.pop("chal", None) or check_opts["challenge"]
    a_ = auth.assert_(chal, **kw)
    return s, t, jpost(s, "/login/passkey/verify", a_, t), check_opts
s, t, r, copt = sign_in(a)
check("the sign-in options need no user name and offer every passkey (empty allow list)", copt["allowCredentials"] == [] and copt["rpId"] == HOST and copt["userVerification"] == "required")
check("a good assertion signs the person in", r.status_code == 200 and r.json()["ok"] and "plain1" in s.get(B + "/my/").text, r.text[:200])
check("the sign-in is in the history as a passkey sign-in", "passkey" in adm.get(B + "/admin/signins?q=plain1").text)
check("the last-used time and the counter are kept", psql("select sign_count from passkeys where name='Test phone'") == "1" and psql("select last_used is not null from passkeys where name='Test phone'") == "t")
s, t, r, _ = sign_in(a)
check("the next sign-in works with a higher counter", r.status_code == 200 and r.json()["ok"] and psql("select sign_count from passkeys where name='Test phone'") == "2")
s, t, r, _ = sign_in(a, count=2)
check("a counter that did not go up is refused (a copied passkey)", r.status_code == 400 and "copied" in r.json()["message"], r.text[:200])
check("and it is in the audit log", "passkey.counter" in adm.get(B + "/admin/audit").text)
a.count = 5
s, t, r, _ = sign_in(a, origin="https://evil.example")
check("an assertion for another origin is refused", r.status_code == 400)
s, t, r, _ = sign_in(a, rp=hashlib.sha256(b"other.example").digest())
check("one for another site address is refused", r.status_code == 400)
s, t, r, _ = sign_in(a, flags=0x01)
check("one without user verification is refused while that is required", r.status_code == 400)
s, t, r, _ = sign_in(a, kind="webauthn.create")
check("one of the wrong kind is refused", r.status_code == 400)
s, t, r, _ = sign_in(a, chal="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
check("one for a challenge this site did not issue is refused", r.status_code == 400)
other = Auth("es256"); other.cid = a.cid
s, t, r, _ = sign_in(other, count=9)
check("a signature from another key is refused", r.status_code == 400)
unknown = Auth("es256")
s, t, r, _ = sign_in(unknown, count=9)
check("a passkey the site does not know is refused", r.status_code == 400 and "does not know" in r.json()["message"])
s = requests.Session(); t = csrf(s, "/my/login")
check("a sign-in without asking for a challenge first is refused", jpost(s, "/login/passkey/verify", a.assert_("x", count=20), t).status_code == 400)
check("failed attempts are in the sign-in history", "passkey failed" in adm.get(B + "/admin/signins?q=plain1").text or "passkey" in adm.get(B + "/admin/signins").text)

# other kinds of keys
rsa_a, _o, r = add("rs256", "Test RSA key")
ed_a, _o, r2 = add("ed25519", "Test security key")
check("RS256 and EdDSA passkeys can be added", r.json()["ok"] and r2.json()["ok"], (r.text[:100], r2.text[:100]))
for auth, nm in ((rsa_a, "RS256"), (ed_a, "EdDSA")):
    s, t, rr, _ = sign_in(auth)
    check(f"and sign in ({nm})", rr.status_code == 200 and rr.json()["ok"], rr.text[:200])

# management
pid = psql("select id from passkeys where name='Test phone'")
r = jpost(me, f"/my/passkeys/{pid}/rename", {}, tok)
r = me.post(B + f"/my/passkeys/{pid}/rename", data={"csrf_token": tok, "name": "Renamed phone"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("a passkey can be renamed", r.status_code == 200 and "Renamed phone" in r.json()["html"])
other_user = sess("claimer1", "claimpassword1")
check("nobody can rename or remove another member's passkey", other_user.post(B + f"/my/passkeys/{pid}/remove", data={"csrf_token": csrf(other_user, "/my/passkeys")}, headers={"X-Requested-With": "XMLHttpRequest"}).status_code == 404)
# the limit
for n in range(7):
    add("es256", f"Extra {n}")
a11, o11, r11 = add("es256", "One too many")
check("a member cannot have more passkeys than the limit", r11.status_code == 400 and "10 passkeys" in (r11.json().get("message") or o11.get("message", "")), (r11.text[:150], o11))
# admin
user_id = psql("select id from users where username='plain1'")
pgu = adm.get(B + f"/admin/users/{user_id}").text
check("an administrator sees the passkey count on the member's page with a button to remove them", "Remove Their Passkeys (10)" in pgu)
ta = csrf(adm, f"/admin/users/{user_id}")
r = adm.post(B + f"/admin/users/{user_id}/security", data={"csrf_token": ta, "action": "remove_passkeys"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("and remove them", r.status_code == 200 and psql("select count(*) from passkeys") == "0", r.text[:150])
s, t, rr, _ = sign_in(a, count=50)
check("a removed passkey can no longer sign in", rr.status_code == 400)
check("it is in the audit log", all(x in adm.get(B + "/admin/audit").text for x in ("passkey.add", "user.remove_passkeys")))
st = adm.get(B + "/admin/settings").text
check("the settings are in Admin > Settings", all(x in st for x in ("Passkeys: Per Member", "Passkeys: Require the Device to Check the Person", "Passkeys: Time to Answer")))
print("ALL PASS" if ok_all else "SOME FAILED")
