"""A community's own place on the broker, against the real broker on the test box (sh tests/deploy_mesh.sh): its logins can write under <root>/<community>/ and nowhere else, ordinary logins cannot write
there, an uplink login is sent nothing and a downlink login is sent only what is on its community's topic, a login can change kind, a node heard on the community's topic counts as the community's, and the
community page makes the logins and tells a gateway how to connect."""
import base64, json, os, re, struct, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
ADMPW = PW
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18482"
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=120).stdout
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def mpsql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"{DK} exec mesh-db psql -U mesh -d mesh -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    requests.get(P + "/health", timeout=8)
except Exception as e:
    print("SKIP communitybroker_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)
def varint(v):
    v &= (1 << 64) - 1
    out = b""
    while True:
        b = v & 0x7F; v >>= 7
        out += bytes([b | (0x80 if v else 0)])
        if not v:
            return out
def tag(num, wt): return varint((num << 3) | wt)
def vi(num, v): return tag(num, 0) + varint(v)
def ld(num, b): return tag(num, 2) + varint(len(b)) + (b if isinstance(b, bytes) else b.encode())
def fx(num, v): return tag(num, 5) + struct.pack("<I", v & 0xFFFFFFFF)
def sfx(num, v): return tag(num, 5) + struct.pack("<i", v)
def map_report(name, lat, lon):
    return ld(1, name) + ld(2, name[:4]) + vi(3, 2) + vi(4, 9) + ld(5, "2.5.9") + vi(6, 1) + vi(7, 0) + vi(8, 1) + sfx(9, int(lat * 1e7)) + sfx(10, int(lon * 1e7)) + vi(11, 100) + vi(12, 16) + vi(13, 3) + vi(14, 1)
def env(sender, name):
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, vi(1, 73) + ld(2, map_report(name, 34.9, -92.1))) + fx(6, 12345)
    return ld(1, pk) + ld(2, "LongFast") + ld(3, "!feedbeef")
def pub(user, pw, topic, blob):
    out = ssh(f"echo {base64.b64encode(blob).decode()} | base64 -d > /tmp/cb_pkt.bin; {DK} cp /tmp/cb_pkt.bin mesh-mqtt:/tmp/cb_pkt.bin; {DK} exec mesh-mqtt mosquitto_pub -h localhost -u {user} -P {pw} -t {topic} -f /tmp/cb_pkt.bin; echo rc=$?")
    return out.strip().splitlines()[-1] if out.strip() else "rc=?"
def wait_for(path, tries=10):
    for _ in range(tries):
        time.sleep(1)
        r = requests.get(P + path)
        if r.status_code == 200:
            return r.json()
    return None
def users(**kw):
    return requests.post(P + "/credentials/sync", json={"users": [dict(username=k, password=v[0], community=v[1], down=v[2]) for k, v in kw.items()], "prune": False}, timeout=30).json()

base = 0xF0000000 + ((int(time.time()) & 0xFFF) << 4)
PW = "cbTestPassword99"
requests.post(P + "/credentials/set", json={"username": "cb-boot", "password": PW}, timeout=30)   # makes sure the broker has the current roles
r = users(**{"zz-test-pub": ("zzTestPublish99", "", False)})
r = users(**{"cb-up": (PW, "cbk-one", False), "cb-down": (PW, "cbk-one", True), "cb-other": (PW, "cbk-two", False), "cb-member": (PW, "", False)})
check("the service makes a community's logins, a downlink login, another community's login and an ordinary one", r.get("ok"), r)
ROOT = "msh/US/AR"
check("an uplink login writes under its community's own topic and the node arrives, remembered as the community's", pub("cb-up", PW, f"{ROOT}/cbk-one/2/map/", env(base + 1, "CB Via Community")) == "rc=0" and (wait_for(f"/nodes/!{base + 1:08x}") or {}).get("node", {}).get("community") == "cbk-one")
pub("cb-up", PW, f"{ROOT}/2/map/", env(base + 2, "CB Wrong Root"))
check("it cannot write to the ordinary topic", wait_for(f"/nodes/!{base + 2:08x}", 5) is None)
pub("cb-up", PW, f"{ROOT}/cbk-two/2/map/", env(base + 3, "CB Other Community"))
check("or to another community's topic", wait_for(f"/nodes/!{base + 3:08x}", 5) is None)
pub("cb-other", PW, f"{ROOT}/cbk-one/2/map/", env(base + 4, "CB Cross Write"))
check("another community's login cannot write here either", wait_for(f"/nodes/!{base + 4:08x}", 5) is None)
pub("cb-member", PW, f"{ROOT}/cbk-one/2/map/", env(base + 5, "CB Member Spoof"))
check("an ordinary member login cannot write into a community's topic", wait_for(f"/nodes/!{base + 5:08x}", 5) is None)
check("but still writes to the ordinary topic, and what it sends is not marked as any community's", pub("cb-member", PW, f"{ROOT}/2/map/", env(base + 6, "CB Ordinary")) == "rc=0" and (wait_for(f"/nodes/!{base + 6:08x}") or {}).get("node", {}).get("community") == "")
check("the downlink login also writes under its community's topic", pub("cb-down", PW, f"{ROOT}/cbk-one/2/map/", env(base + 7, "CB Down Writes")) == "rc=0" and wait_for(f"/nodes/!{base + 7:08x}") is not None)
# what each may read
A = f"{ROOT}/cbk-one/zzretain"; Bt = f"{ROOT}/cbk-two/zzretain"
ssh(f"{DK} exec mesh-mqtt mosquitto_pub -h localhost -u zz-test-pub -P zzTestPublish99 -r -t {A} -m secret-one")
ssh(f"{DK} exec mesh-mqtt mosquitto_pub -h localhost -u zz-test-pub -P zzTestPublish99 -r -t {Bt} -m secret-two")
def read(user, topic):
    return ssh(f"{DK} exec mesh-mqtt mosquitto_sub -h localhost -u {user} -P {PW} -t {topic} -W 3 -C 1")
check("an uplink login is sent nothing", "secret" not in read("cb-up", A))
check("a downlink login is sent what is on its community's topic", "secret-one" in read("cb-down", A))
check("and nothing from another community's topic", "secret-two" not in read("cb-down", Bt))
check("nor from the ordinary topics (a downlink login reads only its own community)", "secret" not in read("cb-down", f"{ROOT}/zzordinary") )
users(**{"cb-down": (PW, "cbk-one", False)})
check("a login moved from downlink to uplink stops being sent anything", "secret-one" not in read("cb-down", A))
users(**{"cb-down": (PW, "cbk-one", True)})
check("and moved back, reads again", "secret-one" in read("cb-down", A))
ssh(f"{DK} exec mesh-mqtt mosquitto_pub -h localhost -u zz-test-pub -P zzTestPublish99 -r -n -t {A}")
ssh(f"{DK} exec mesh-mqtt mosquitto_pub -h localhost -u zz-test-pub -P zzTestPublish99 -r -n -t {Bt}")
for u in ("cb-up", "cb-down", "cb-other", "cb-member"):
    requests.post(P + "/credentials/delete", json={"username": u})

# ---- through the community page
setting("mesh_enabled", "1"); setting("mesh_communities_enabled", "1")
psql("DELETE FROM communities WHERE slug IN ('cbk-one', 'cbk-two')")
psql("DELETE FROM users WHERE username = 'cbkowner'")
reg = requests.Session()
reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": "cbkowner", "email": "cbkowner@example.com", "password": "cbkownerpassword9", "confirm": "cbkownerpassword9"})
adm = sess("marty", ADMPW)
act(adm, "/admin/communities/create", "/admin/communities", name="CBK One", owner="cbkowner")
O = sess("cbkowner", "cbkownerpassword9")
def post(path, **d):
    d["csrf_token"] = tok(O, "/mesh/c/cbk-one/logins"); d["s"] = "logins"
    return O.post(B + path, data=d, allow_redirects=False)
def flashed(): return O.get(B + "/mesh/c/cbk-one").text
mpsql("DELETE FROM nodes WHERE long_name LIKE 'CB %'")
check("the Broker Logins page tells a gateway how to connect, with the community's own topic and both kinds", all(x in O.get(B + "/mesh/c/cbk-one/logins").text for x in ("How to Connect a Gateway", "msh/US/AR/cbk-one", "Root topic", "Uplink enabled", "Uplink and downlink")))
post("/mesh/c/cbk-one/logins/add", label="Hill uplink", link="up")
m = re.search(r"Broker login ([\w.-]+) \(password shown once, copy it now\): ([\w-]+)", flashed())
up_user, up_pw = (m.group(1), m.group(2)) if m else ("", "")
post("/mesh/c/cbk-one/logins/add", label="Valley downlink", link="down")
m = re.search(r"Broker login ([\w.-]+) \(password shown once, copy it now\): ([\w-]+)", flashed())
dn_user, dn_pw = (m.group(1), m.group(2)) if m else ("", "")
check("a login of each kind is made and remembered with its kind", up_user and dn_user and psql("SELECT string_agg(link, ',' ORDER BY id) FROM mesh_accounts WHERE community_id=(SELECT id FROM communities WHERE slug='cbk-one')") == "up,down" and "uplink and downlink" in O.get(B + "/mesh/c/cbk-one/logins").text)
check("the uplink login writes to the community's topic through the real broker", pub(up_user, up_pw, f"{ROOT}/cbk-one/2/map/", env(base + 20, "CB Page Login")) == "rc=0" and wait_for(f"/nodes/!{base + 20:08x}") is not None)
pub(up_user, up_pw, f"{ROOT}/2/map/", env(base + 21, "CB Page Wrong"))
check("and not to the ordinary topic", wait_for(f"/nodes/!{base + 21:08x}", 5) is None)
nodes_page = O.get(B + "/mesh/c/cbk-one/nodes").text
check("a node heard on the community's topic is one of the community's nodes (no area, claim or choice needed)", "CB Page Login" in nodes_page, nodes_page[nodes_page.find("CB"):nodes_page.find("CB") + 80])
check("the other node heard on the ordinary topic is not", "CB Ordinary" not in nodes_page)
aid = psql("SELECT id FROM mesh_accounts WHERE label='Valley downlink'")
post(f"/mesh/c/cbk-one/logins/{aid}/disable")
post(f"/mesh/c/cbk-one/logins/{aid}/enable")
check("a login switched off and on again keeps its kind", psql(f"SELECT link FROM mesh_accounts WHERE id={aid}") == "down")
ssh(f"{DK} exec mesh-mqtt mosquitto_pub -h localhost -u zz-test-pub -P zzTestPublish99 -r -t {A} -m secret-one")
def read2(user, pw, topic):
    return ssh(f"{DK} exec mesh-mqtt mosquitto_sub -h localhost -u {user} -P {pw} -t {topic} -W 3 -C 1")
check("and still reads its community's topic after that", "secret-one" in read2(dn_user, dn_pw, A))
check("while the uplink login reads nothing", "secret" not in read2(up_user, up_pw, A))
ssh(f"{DK} exec mesh-mqtt mosquitto_pub -h localhost -u zz-test-pub -P zzTestPublish99 -r -n -t {A}")
rs = requests.post(P + "/credentials/sync", json={"users": [], "prune": False}).json()
act(adm, "/admin/mesh/accounts/sync", "/admin/mesh")
check("the administrators' Sync keeps the logins' kinds (uplink only stays uplink only)", "secret" not in read2(up_user, up_pw, A))
act(adm, "/admin/communities/cbk-one/delete", "/admin/communities")
psql("DELETE FROM mesh_accounts WHERE username IN ('%s','%s')" % (up_user, dn_user))
for u in (up_user, dn_user):
    if u: requests.post(P + "/credentials/delete", json={"username": u})
mpsql("DELETE FROM nodes WHERE long_name LIKE 'CB %'")
psql("DELETE FROM users WHERE username = 'cbkowner'")
print("ALL PASS" if ok_all else "SOME FAILED")
