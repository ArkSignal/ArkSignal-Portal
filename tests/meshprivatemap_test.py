"""The private mesh maps (request 205, stage 3): a community's private map shows every node its private gateways hear and only those, for its team and the site's administrators; another community's team, a plain
member and a visitor are turned away; the site's administrators have a map of every community; the data is never cached; each opening is logged; with the switch off the pages do not exist.
Needs the mesh service and both portals on the test box (sh tests/deploy_test.sh, sh tests/deploy_mesh.sh)."""
import base64, os, re, struct, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
ADMPW = PW
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18482"
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    requests.get(P + "/health", timeout=8)
except Exception as e:
    print("SKIP meshprivatemap_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)
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
def envelope(sender, port, payload, gateway):
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, vi(1, port) + ld(2, payload) + vi(9, 0)) + fx(6, 777)
    return ld(1, pk) + ld(2, "LongFast") + ld(3, gateway)
def ingest(com, env, gw):
    return requests.post(P + "/ingest_private", json={"topic": f"mshp/{com}/2/e/LongFast/{gw}", "envelope_b64": base64.b64encode(env).decode()}, timeout=20).json()["result"]

setting("mesh_enabled", "1"); setting("mesh_communities_enabled", "1"); setting("mesh_private_enabled", "1")
requests.post(P + "/config", json={"private_enabled": "1"}, timeout=20)
psql("DELETE FROM communities WHERE slug IN ('pvm-one', 'pvm-two')")
psql("DELETE FROM users WHERE username IN ('pvmowner1', 'pvmowner2', 'pvmplain')")
for u in ("pvmowner1", "pvmowner2", "pvmplain"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": u, "email": u + "@example.com", "password": u + "password9", "confirm": u + "password9"})
adm = sess("marty", ADMPW)
act(adm, "/admin/communities/create", "/admin/communities", name="PVM One", owner="pvmowner1")
act(adm, "/admin/communities/create", "/admin/communities", name="PVM Two", owner="pvmowner2")
O1, O2, PL = sess("pvmowner1", "pvmowner1password9"), sess("pvmowner2", "pvmowner2password9"), sess("pvmplain", "pvmplainpassword9")
t = int(time.time()) & 0xFFF
n1, n2, gw = 0xD1000000 + t * 4, 0xD1000000 + t * 4 + 1, "!%08x" % (0xD1100000 + t)
def ni(sid, long): return ld(1, sid) + ld(2, long) + ld(3, long[-4:]) + vi(5, 9) + vi(7, 0)
def pos(lat, lon): return sfx(1, int(lat * 1e7)) + sfx(2, int(lon * 1e7)) + vi(3, 90)
for n, com, name, lat in ((n1, "pvm-one", "ZZ Map One", 35.2), (n2, "pvm-two", "ZZ Map Two", 35.4)):
    ingest(com, envelope(n, 4, ni("!%08x" % n, name), gw), gw)
    ingest(com, envelope(n, 3, pos(lat, -92.5), gw), gw)

pg = O1.get(B + "/mesh/c/pvm-one/private-map")
check("the community's team opens its private map, marked as private", pg.status_code == 200 and "Private map: PVM One" in pg.text and "not public" in pg.text, pg.status_code)
j = O1.get(B + "/api/mesh/private/nodes.json?community=pvm-one")
names = [n["long_name"] for n in j.json()["nodes"]] if j.status_code == 200 else []
check("it lists the community's private nodes with their exact positions, and no other community's", "ZZ Map One" in names and "ZZ Map Two" not in names, names)
check("and the data is never cached", "no-store" in j.headers.get("Cache-Control", ""))
d = O1.get(B + "/api/mesh/private/!%08x.json?community=pvm-one" % n1)
check("a node's details come with who heard it", d.status_code == 200 and d.json()["node"]["long_name"] == "ZZ Map One" and "heard_by" in d.json(), d.status_code)
check("another community's node is not reachable through this community", O1.get(B + "/api/mesh/private/!%08x.json?community=pvm-one" % n2).status_code == 404)
check("the other community's team is turned away from the first one's map and data", O2.get(B + "/mesh/c/pvm-one/private-map").status_code in (403, 404) and O2.get(B + "/api/mesh/private/nodes.json?community=pvm-one").status_code in (403, 404))
check("a member who is not on the team is turned away", PL.get(B + "/mesh/c/pvm-one/private-map").status_code in (403, 404) and PL.get(B + "/api/mesh/private/nodes.json?community=pvm-one").status_code in (403, 404))
check("a visitor is sent to sign in and gets no data", requests.get(B + "/mesh/c/pvm-one/private-map", allow_redirects=False).status_code in (302, 401, 403) and requests.get(B + "/api/mesh/private/nodes.json?community=pvm-one", allow_redirects=False).status_code in (302, 401, 403, 404))
check("a team member cannot ask for every community at once", O1.get(B + "/api/mesh/private/nodes.json").status_code == 403)
ja = adm.get(B + "/api/mesh/private/nodes.json")
allnames = [n["long_name"] for n in ja.json()["nodes"]] if ja.status_code == 200 else []
check("a site administrator sees every community's private nodes", "ZZ Map One" in allnames and "ZZ Map Two" in allnames, allnames[:5])
check("and has the map of all communities, and may open any one community's", adm.get(B + "/admin/mesh/private-map").status_code == 200 and adm.get(B + "/mesh/c/pvm-two/private-map").status_code == 200)
check("the team is not given the all-communities map", O1.get(B + "/admin/mesh/private-map").status_code == 403)
check("a public page does not carry the private nodes", "ZZ Map One" not in requests.get(B + "/api/mesh/nodes.json").text)
n3 = 0xD1000000 + t * 4 + 2
for port, pl in ((4, ni("!%08x" % n3, "ZZ Public Hill")), (3, pos(35.6, -92.1))):
    requests.post(P + "/ingest", json={"envelope_b64": base64.b64encode(envelope(n3, port, pl, gw)).decode()}, timeout=20)
time.sleep(2)
pubset = lambda: {x["long_name"] for x in requests.get(B + "/api/mesh/nodes.json").json()["nodes"]}
if "ZZ Public Hill" in pubset():
    ra = O1.get(B + "/api/mesh/private/nodes.json?community=pvm-one&public=1"); jn = {x["long_name"]: x for x in ra.json()["nodes"]}
    check("with Include public nodes the private map also has the public map's nodes, marked public, and keeps its own private ones", "ZZ Public Hill" in jn and jn["ZZ Public Hill"].get("public_only") is True and "ZZ Map One" in jn and not jn["ZZ Map One"].get("public_only") and "ZZ Map Two" not in jn, list(jn))
    check("without it, only the private nodes", "ZZ Public Hill" not in O1.get(B + "/api/mesh/private/nodes.json?community=pvm-one").text)
    check("the administrators' all-communities map can include them too", "ZZ Public Hill" in adm.get(B + "/api/mesh/private/nodes.json?public=1").text)
    check("a public page still does not carry the private nodes", "ZZ Map One" not in requests.get(B + "/api/mesh/nodes.json").text)
else:
    check("the public test node reached the public map (the setup for the Include public nodes checks)", False, "ZZ Public Hill not on /api/mesh/nodes.json")
mapp = O1.get(B + "/mesh/c/pvm-one/private-map").text
check("both private maps carry the Include public nodes choice", 'id="mm-pub"' in mapp and 'id="mm-pub"' in adm.get(B + "/admin/mesh/private-map").text and 'id="mm-pub"' not in requests.get(B + "/mesh/map").text)
check("each opening is logged", int(psql("SELECT count(*) FROM audit_log WHERE action='community.privatemap' AND target IN ('pvm-one','pvm-two','all')") or 0) >= 3)
lg = O1.get(B + "/mesh/c/pvm-one/logins").text
check("the Broker Logins page offers the Private feed kind and a link to the map", 'value="private"' in lg and "/mesh/c/pvm-one/private-map" in lg)
check("the community's Manage menu has a Private Map item for its team, and it opens the map", "/mesh/c/pvm-one/privmap" in O1.get(B + "/mesh/c/pvm-one").text and O1.get(B + "/mesh/c/pvm-one/privmap", allow_redirects=False).headers.get("Location", "").endswith("/mesh/c/pvm-one/private-map"))
check("not for a member who is not on the team", "/mesh/c/pvm-one/privmap" not in PL.get(B + "/mesh/c/pvm-one").text and PL.get(B + "/mesh/c/pvm-one/privmap", allow_redirects=False).status_code in (403, 404))
check("the Mesh menu gives the site's administrators a Private Map item, and nobody else", "/admin/mesh/private-map" in adm.get(B + "/mesh/").text and "/admin/mesh/private-map" not in O1.get(B + "/mesh/").text and "/admin/mesh/private-map" not in requests.get(B + "/mesh/").text)
setting("mesh_private_enabled", "0")
check("with the switch off the pages and data do not exist", O1.get(B + "/mesh/c/pvm-one/private-map").status_code == 404 and O1.get(B + "/api/mesh/private/nodes.json?community=pvm-one").status_code == 404 and adm.get(B + "/admin/mesh/private-map").status_code == 404)
setting("mesh_private_enabled", "1")
act(adm, "/admin/communities/pvm-one/delete", "/admin/communities")
act(adm, "/admin/communities/pvm-two/delete", "/admin/communities")
psql("DELETE FROM users WHERE username IN ('pvmowner1', 'pvmowner2', 'pvmplain')")
subprocess.run(["ssh", "-q", "nastemp", f"{DK} exec mesh-db psql -U mesh -d mesh -tA -c \"DELETE FROM pnodes WHERE long_name LIKE 'ZZ Map %'\""], capture_output=True, timeout=60)
print("ALL PASS" if ok_all else "SOME FAILED")
raise SystemExit(0 if ok_all else 1)
