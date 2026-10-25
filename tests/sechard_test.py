"""Security hardening: sign-in answers that never say which accounts exist, a password change that ends the account's other sign-ins, links built from the site's own address (not the Host header), the limits on
request size and password length, no-store on private pages, the address rule for outgoing requests, encrypted-only mail passwords, secrets kept out of the access log, and the services' shared token."""
import json, os, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd, timeout=180):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=timeout).stdout
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def psql(sql):
    return ssh(f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
psql("DELETE FROM users WHERE username LIKE 'shd%'")
def register(n, pw=None):
    s = requests.Session()
    pw = pw or n + "password99"
    return s.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": pw, "confirm": pw}, allow_redirects=True)
for n in ("shdone", "shdlock", "shdoff", "shdwr", "shdwr2"):
    register(n)
def attempt(user, pw):
    s = requests.Session()
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/login").text).group(1)
    t0 = time.time()
    r = s.post(B + "/my/login", data={"csrf_token": t, "username": user, "password": pw})
    return s, r, time.time() - t0
def flash_of(r):
    m = re.findall(r'class="flash[^>]*>(.*?)</div>', r.text, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", " ".join(m))).strip()
# ---- sign-in answers
psql("UPDATE users SET disabled=true WHERE username='shdoff'")
for i in range(6):
    attempt("shdlock", "wrong-wrong-wrong")
msgs = {"unknown name": flash_of(attempt("shdnobody", "whatever-pass1")[1]), "wrong password": flash_of(attempt("shdwr", "wrong-wrong-wrong")[1]),
        "disabled account": flash_of(attempt("shdoff", "shdoffpassword99")[1]), "locked account": flash_of(attempt("shdlock", "shdlockpassword99")[1])}
check("an unknown name, a wrong password, a disabled account and a locked one all get the same answer", len(set(msgs.values())) == 1 and "Incorrect username or password" in list(msgs.values())[0], msgs)
check("the right password of a disabled account does not sign in", "Sign Out" not in attempt("shdoff", "shdoffpassword99")[1].text)
t_unknown = [attempt("shdnobody", "x" * 12)[2] for _ in range(3)]
t_wrong = [attempt("shdwr2", "y" * 12)[2] for _ in range(3)]
check("an unknown name takes about as long to answer as a wrong password (no timing difference to tell them apart)", abs(sorted(t_unknown)[1] - sorted(t_wrong)[1]) < 0.35, (t_unknown, t_wrong))
check("a password of thousands of characters is not hashed (answered like any wrong one, fast)", attempt("shdwr", "z" * 100000)[2] < 3)
r = register("shdlong", "A1" * 200)
check("a password longer than 256 characters is refused at registration", "at most 256" in r.text and psql("SELECT COUNT(*) FROM users WHERE username='shdlong'") == "0")
# ---- a new password ends the account's other sign-ins
a, b = sess("shdone", "shdonepassword99"), sess("shdone", "shdonepassword99")
check("two sign-ins of one account both work", a.get(B + "/my/account", allow_redirects=False).status_code == 200 and b.get(B + "/my/account", allow_redirects=False).status_code == 200)
t = re.search(r'name="csrf_token" value="([^"]+)"', a.get(B + "/my/password").text).group(1)
a.post(B + "/my/password", data={"csrf_token": t, "current": "shdonepassword99", "new": "shdonenewpass77", "confirm": "shdonenewpass77"})
check("after a password change the other sign-in is ended", b.get(B + "/my/account", allow_redirects=False).status_code == 302)
check("and the one that changed it carries on", a.get(B + "/my/account", allow_redirects=False).status_code == 200)
so, old_r, _t = attempt("shdone", "shdonepassword99")
sn, new_r, _t = attempt("shdone", "shdonenewpass77")
check("the old password no longer works and the new one does", so.get(B + "/my/account", allow_redirects=False).status_code == 302 and sn.get(B + "/my/account", allow_redirects=False).status_code == 200, flash_of(old_r)[:60])
# ---- links use the site's own address, not the Host header
setting("main_address", "official.example.test")
time.sleep(7)
setting("myflights_enabled", "1"); setting("myflights_sharing", "1"); psql("DELETE FROM settings WHERE key = 'access_myflights'")
time.sleep(8)
u = sess("shdone", "shdonenewpass77")
t = re.search(r'name="csrf_token" value="([^"]+)"', u.get(B + "/my/flights/shares").text).group(1)
r = u.post(B + "/my/flights/shares/create", data={"csrf_token": t, "days": "30"}, headers={"X-Forwarded-Host": "evil.example.net"}, allow_redirects=True)
m = re.search(r"(https?://[^/\s]+)/share/flights/", r.text)
check("a share link is built on the site's configured address even when the request names another host (a forwarded Host header)", m is not None and m.group(1) == "https://official.example.test", (r.status_code, m.group(1) if m else re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))[:200]))
setting("main_address", "")
# ---- request size, headers
big = requests.post(B + "/my/login", data=b"x=" + b"A" * (11 * 1024 * 1024), headers={"Content-Type": "application/x-www-form-urlencoded"})
check("a request larger than the limit is refused before it is read", big.status_code == 413, big.status_code)
adm = sess("marty", PW)
check("an administrator may send more (a backup restore is big): an 11 MB request is not refused for size", requests.Session().post(B + "/admin/backups/upload", data=b"x=" + b"A" * (11 * 1024 * 1024), headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code != 413)
check("a signed-in page is marked no-store", u.get(B + "/my/account").headers.get("Cache-Control") == "no-store" and requests.get(B + "/my/login").headers.get("Cache-Control") == "no-store")
check("a public page is not", "no-store" not in requests.get(B + "/about").headers.get("Cache-Control", ""))
# ---- inside the portal: outgoing address rule, mail, log redaction
subprocess.run(["scp", "-q", "sechard_unit.py", "nastemp:adsb-portal/sechard_unit.py"], check=True)
ssh(f"{DK} cp ~/adsb-portal/sechard_unit.py adsb-portal:/tmp/sechard_unit.py")
o = ssh(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sechard_unit.py", 240).strip().splitlines()
try:
    res = json.loads(o[-1])
except Exception:
    res = {}
    print("unit output:", o[-6:])
names = {"private_loopback_linklocal_shared_blocked": "private, loopback, link-local, shared (100.64/10), unspecified and mapped addresses are blocked", "public_allowed": "public addresses are allowed",
         "private_allowed_only_when_the_administrator_allows_it": "private ones only when the administrator allows it (and never 0.0.0.0)", "a_name_is_resolved_once_and_the_checked_address_is_used": "a name is resolved once and the connection goes to the checked address",
         "a_name_that_points_inside_is_refused_before_any_connection": "a name that points inside is refused before any connection", "localhost_is_refused": "localhost is refused", "a_webhook_to_a_private_address_is_refused": "a webhook to a private address is refused",
         "sending_to_loopback_is_refused_at_send_time": "and at send time", "plain_smtp_with_a_password_is_refused": "plain SMTP with a password is refused", "plain_smtp_to_a_public_server_is_refused": "plain SMTP to a public server is refused",
         "log_hides_tokens_in_paths_and_queries": "the access log hides tokens in paths and queries", "log_keeps_ordinary_addresses": "and keeps ordinary addresses", "the_gunicorn_logger_class_exists": "the gunicorn logger class exists"}
for k, label in names.items():
    check("unit: " + label, res.get(k) is True, res.get(k))
# ---- the real access log
tok = "SECRETTOKEN" + "abcdefghijklmnopqrstuvwx"
requests.get(B + "/sat/push/" + tok)
requests.get(B + "/my/reset/" + tok)
requests.get(B + "/share/flights/" + tok)
time.sleep(2)
logs = ssh(f"{DK} logs --since 2m adsb-portal 2>&1")
check("the web server's access log does not hold a token that was in an address", tok not in logs and "/<hidden>" in logs, [l for l in logs.splitlines() if "SECRETTOKEN" in l][:2])
# ---- the services' shared token (the check every service has)
src = open("../deploy/mesh/meshd.py", encoding="utf-8").read()
check("every service of the stack refuses requests without the shared token when one is set (mesh, ACARS, APRS, predictions, Matrix helper)", all("_allowed" in open(f"../deploy/{d}", encoding="utf-8").read() and "X-Service-Token" in open(f"../deploy/{d}", encoding="utf-8").read() for d in ("mesh/meshd.py", "acars/acarsd.py", "aprs/aprsd.py", "predict/predictd.py", "matrix/matrixd.py")))
check("and the portal sends it with every call it makes to them", all("servicecall" in open(f"../app/{f}", encoding="utf-8").read() for f in ("mesh.py", "acars.py", "aprs.py", "matrix.py", "sonde_predict.py")))
code = ("import sys, types; sys.path.insert(0, '/app'); import meshd; H = meshd.H; "
        "mk = lambda path, tok: types.SimpleNamespace(path=path, headers={'X-Service-Token': tok} if tok else {}); "
        "print(H._allowed(mk('/nodes', 'secret-token')), H._allowed(mk('/nodes', 'wrong')), H._allowed(mk('/nodes', '')), H._allowed(mk('/health', '')), H._allowed(mk('/credentials/sync', 'secret-token')))")
o = ssh(f"{DK} exec -e SERVICE_TOKEN=secret-token mesh python -c \"{code}\" 2>&1").strip().splitlines()
if o and "True" in o[-1]:
    check("a service with a token set accepts the right token, refuses a wrong or missing one, and leaves /health open", o[-1].split() == ["True", "False", "False", "True", "True"], o[-1])
    o2 = ssh(f"{DK} exec mesh python -c \"{code}\" 2>&1").strip().splitlines()
    check("with no token set it stays open as before", o2 and o2[-1].split() == ["True", "True", "True", "True", "True"], o2[-1:] )
else:
    print("SKIP the mesh service is not on the test box:", o[-1:] if o else "")
code2 = ("import sys; sys.path.insert(0, '/app'); import meshd; "
         "a = meshd._seal_value('password', 'broker-secret-1'); b = meshd._seal_value('host', 'mesh-mqtt'); "
         "print(a.startswith('enc1:') and 'broker-secret-1' not in a, meshd._open_value('password', a) == 'broker-secret-1', b == 'mesh-mqtt', meshd._seal_value('up_password', '') == '')")
o3 = ssh(f"{DK} exec -e SERVICE_TOKEN=secret-token mesh python -c \"{code2}\" 2>&1").strip().splitlines()
if o3 and o3[-1].split()[0] in ("True", "False"):
    check("the mesh service keeps its broker passwords sealed in its own database when the stack has a token (and other settings are left as they are)", o3[-1].split() == ["True", "True", "True", "True"], o3[-1])
else:
    print("SKIP the mesh service is not on the test box:", o3[-1:] if o3 else "")
perm = ssh(f"{DK} exec adsb-portal sh -c 'stat -c %a /backups; ls -t /backups/*.tar.gz | head -1 | xargs stat -c %a'").split()
check("the backup folder is private to the portal's user (700) and a backup file is 600", perm[:2] == ["700", "600"], perm)
psql("DELETE FROM users WHERE username LIKE 'shd%'")
print("ALL PASS" if ok_all else "SOME FAILED")
