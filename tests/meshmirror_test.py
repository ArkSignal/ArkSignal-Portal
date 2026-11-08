"""Mirror logins (request 217): an administrator makes a read-only login on this site's broker for another ArkSignal site, which reads this site's public mesh feed live.
The login may read the public mesh topics and nothing else (never the private feed), cannot publish, can be shown again, given a new password, switched off and deleted; and "Read Another Site" points this
site's second subscription at another broker (with TLS as an option) and the service really connects with the login.
Needs the mesh service and both portals on the test box (sh tests/deploy_test.sh, sh tests/deploy_mesh.sh)."""
import os, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
ADMPW = PW
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18482"
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def ssh(cmd, t=120): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=t).stdout
def psql(sql): return ssh(f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    requests.get(P + "/health", timeout=8)
except Exception as e:
    print("SKIP meshmirror_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)

setting("mesh_enabled", "1")
psql("DELETE FROM mesh_mirror_logins")
psql("DELETE FROM users WHERE username = 'mmplain'")
reg = requests.Session()
reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": "mmplain", "email": "mmplain@example.com", "password": "mmplainpassword9", "confirm": "mmplainpassword9"})
adm, PLAIN = sess("marty", ADMPW), sess("mmplain", "mmplainpassword9")
ADM = ssh(f"{DK} exec mesh-mqtt cat /mosquitto/config/admin.pass").strip()
def ctrl(*args): return ssh(f"{DK} exec mesh-mqtt mosquitto_ctrl -h localhost -p 1883 -u mesh-admin -P {ADM} dynsec " + " ".join(args))
PUB = ("mmpub", "mmpubpassword1")   # a temporary login that may publish anywhere (the broker's admin login only manages the broker)
ctrl("deleteClient mmpub"); ctrl("deleteRole mmpubrole")
ctrl("createRole mmpubrole"); ctrl("addRoleACL mmpubrole publishClientSend '#' allow"); ctrl("addRoleACL mmpubrole subscribePattern '#' allow"); ctrl("addRoleACL mmpubrole publishClientReceive '#' allow"); ctrl(f"createClient mmpub -p {PUB[1]}"); ctrl("addClientRole mmpub mmpubrole")

def mq(user, pw, subs, pubs, wait=7):
    """In the broker's container: subscribe as (user, pw) to the topics, publish (user, pw, topic, message) from others, and return what the subscriber got."""
    s = " ".join(f"-t '{t}'" for t in subs)
    p = "; ".join(f"mosquitto_pub -h localhost -p 1883 -u {u} -P {w} -t '{t}' -m '{m}'" for u, w, t, m in pubs)
    return ssh(f"{DK} exec mesh-mqtt sh -c \"mosquitto_sub -h localhost -p 1883 -u {user} -P {pw} {s} -v -W {wait} > /tmp/mm.out 2>&1 & sleep 2; {p}; sleep {wait - 1}; cat /tmp/mm.out\"", 60)

check("the page is for administrators only", PLAIN.get(B + "/admin/mesh/mirror").status_code in (302, 401, 403, 404) and adm.get(B + "/admin/mesh/mirror").status_code == 200)
check("the admin menu has it", "/admin/mesh/mirror" in adm.get(B + "/admin/mesh/mirror").text)
ok, msg, _h = act(adm, "/admin/mesh/mirror/make", "/admin/mesh/mirror", label="Test site")
row = psql("SELECT id, username FROM mesh_mirror_logins WHERE label='Test site'")
mid, user = (row.split("|") + ["", ""])[:2]
check("an administrator makes a mirror login, named for the site that will use it", ok and re.fullmatch(r"mirror-\d+", user or ""), (msg, row))
sh = adm.get(B + f"/admin/mesh/mirror/{mid}")
pw = (re.search(r'Password</th><td class="mono copyable">([^<]+)<', sh.text) or [None, ""])[1]
check("it can be opened again: server, port, username, password and the topics to read, never cached, and the opening is logged", sh.status_code == 200 and user in sh.text and len(pw) >= 18 and "meshcore/#" in sh.text and "/#" in sh.text and "no-store" in sh.headers.get("Cache-Control", "")
      and int(psql("SELECT count(*) FROM audit_log WHERE action='mesh.mirror' AND detail LIKE 'opened%'") or 0) >= 1, sh.status_code)
check("the password is kept sealed", psql(f"SELECT count(*) FROM mesh_mirror_logins WHERE password_sealed = '{pw}'") == "0" and pw != "")
check("a member cannot open it", PLAIN.get(B + f"/admin/mesh/mirror/{mid}").status_code in (302, 401, 403, 404))

out = mq(user, pw, ["msh/#", "mshp/#", "meshcore/#"], [("mmpub", PUB[1], "msh/US/AR/2/e/LongFast/!mirrortest", "publicpacket"), ("mmpub", PUB[1], "mshp/zz/2/e/LongFast/!mirrortest", "privatepacket"), ("mmpub", PUB[1], "meshcore/x/y", "corepacket")])
check("the mirror login reads the public mesh topics (Meshtastic and MeshCore)", "publicpacket" in out and "corepacket" in out, out[-300:])
check("and never the private feed", "privatepacket" not in out, out[-300:])
adm_out = ssh(f"{DK} exec mesh-mqtt sh -c \"mosquitto_sub -h localhost -p 1883 -u mmpub -P {PUB[1]} -t 'msh/#' -v -W 6 > /tmp/ma.out 2>&1 & sleep 2; mosquitto_pub -h localhost -p 1883 -u {user} -P {pw} -t 'msh/US/AR/2/e/LongFast/!mirrortest' -m forgedbymirror; sleep 5; cat /tmp/ma.out\"", 60)
check("it cannot publish", "forgedbymirror" not in adm_out, adm_out[-200:])

ok, msg, _h = act(adm, f"/admin/mesh/mirror/{mid}/password", "/admin/mesh/mirror")
pw2 = (re.search(r'Password</th><td class="mono copyable">([^<]+)<', adm.get(B + f"/admin/mesh/mirror/{mid}").text) or [None, ""])[1]
check("a new password replaces the old one on the broker", ok and pw2 and pw2 != pw and "publicpacket" in mq(user, pw2, ["msh/#"], [("mmpub", PUB[1], "msh/US/AR/2/e/LongFast/!mirrortest", "publicpacket")]) and "publicpacket" not in mq(user, pw, ["msh/#"], [("mmpub", PUB[1], "msh/US/AR/2/e/LongFast/!mirrortest", "publicpacket")]), msg)
pw = pw2
ok, msg, _h = act(adm, f"/admin/mesh/mirror/{mid}/off", "/admin/mesh/mirror")
check("switching it off refuses the other site at once", ok and "publicpacket" not in mq(user, pw, ["msh/#"], [("mmpub", PUB[1], "msh/US/AR/2/e/LongFast/!mirrortest", "publicpacket")]), msg)
ok, msg, _h = act(adm, f"/admin/mesh/mirror/{mid}/on", "/admin/mesh/mirror")
check("and switching it on lets it read again with the same password", ok and "publicpacket" in mq(user, pw, ["msh/#"], [("mmpub", PUB[1], "msh/US/AR/2/e/LongFast/!mirrortest", "publicpacket")]), msg)
check("the other member logins' automatic sync keeps it (it is not pruned)", ok and requests.post(P + "/credentials/sync", json={"users": [], "prune": False}, timeout=30).status_code == 200 and "publicpacket" in mq(user, pw, ["msh/#"], [("mmpub", PUB[1], "msh/US/AR/2/e/LongFast/!mirrortest", "publicpacket")]))

# ---- the reading side
check("a wrong entry is refused with a message", not act(adm, "/admin/mesh/mirror/read", "/admin/mesh/mirror", host="x", port="8883", tls="1", user=user, password=pw, topics="msh/#")[0] and not act(adm, "/admin/mesh/mirror/read", "/admin/mesh/mirror", host="mesh-mqtt", port="abc", user=user, password=pw, topics="msh/#")[0]
      and not act(adm, "/admin/mesh/mirror/read", "/admin/mesh/mirror", host="mesh-mqtt", port="1883", user="", password="", topics="msh/#")[0])
ok, msg, _h = act(adm, "/admin/mesh/mirror/read", "/admin/mesh/mirror", host="mesh-mqtt", port="1883", user=user, password=pw, topics="msh/#,meshcore/#")
sv = {k: psql(f"SELECT value FROM settings WHERE key='{k}'") for k in ("mesh_up_host", "mesh_up_port", "mesh_up_tls", "mesh_up_user", "mesh_up_topic", "mesh_up_enabled")}
check("Read Another Site fills in the second subscription (TLS off here)", ok and sv == {"mesh_up_host": "mesh-mqtt", "mesh_up_port": "1883", "mesh_up_tls": "0", "mesh_up_user": user, "mesh_up_topic": "msh/#,meshcore/#", "mesh_up_enabled": "1"}, (msg, sv))
conn = False
for _ in range(30):
    time.sleep(2)
    try:
        conn = bool(requests.get(P + "/stats", timeout=8).json().get("feed", {}).get("up_connected"))
    except Exception:
        conn = False
    if conn:
        break
check("the service reads the other broker with the mirror login (it connects)", conn)
ok, msg, _h = act(adm, "/admin/mesh/mirror/read", "/admin/mesh/mirror", host="mesh-mqtt", port="8883", tls="1", user=user, password=pw, topics="msh/#")
check("with TLS ticked the setting follows and is shown on the page", ok and psql("SELECT value FROM settings WHERE key='mesh_up_tls'") == "1" and "checked" in re.search(r'name="tls"[^>]*>', adm.get(B + "/admin/mesh/mirror").text).group(0), msg)
check("the audit log has it", int(psql("SELECT count(*) FROM audit_log WHERE action='mesh.mirror' AND detail LIKE 'this site now reads%'") or 0) >= 2)
check("the Settings page has the TLS choice", "mesh_up_tls" in adm.get(B + "/admin/settings/mesh").text)

# put the second subscription back to the public Meshtastic server and tidy up
psql("DELETE FROM settings WHERE key IN ('mesh_up_host', 'mesh_up_port', 'mesh_up_tls', 'mesh_up_user', 'mesh_up_password', 'mesh_up_topic')")
act(adm, "/admin/mesh/apply", "/admin/mesh")
ok, msg, _h = act(adm, f"/admin/mesh/mirror/{mid}/delete", "/admin/mesh/mirror")
check("deleting it refuses the other site at once and removes the row", ok and psql("SELECT count(*) FROM mesh_mirror_logins") == "0" and "publicpacket" not in mq(user, pw, ["msh/#"], [("mmpub", PUB[1], "msh/US/AR/2/e/LongFast/!mirrortest", "publicpacket")]), msg)
psql("DELETE FROM users WHERE username = 'mmplain'")
ctrl("deleteClient mmpub"); ctrl("deleteRole mmpubrole")
print("ALL PASS" if ok_all else "SOME FAILED")
