"""Mesh router planning: where one more router would help the most poorly served nodes (heard by one gateway or none, or only weakly), built from what was heard. Made-up clusters are sent through the service's own door:
a cluster of poorly served nodes far from any router gets a suggestion that helps all of them, a cluster that already has a router beside it gets none, a node two gateways hear well is not counted, a node two
gateways hear only weakly is, and the portal page, menu and settings show it. Needs the service and broker running on the test box (sh tests/deploy_mesh.sh)."""
import base64, math, os, struct, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18482"
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def mpsql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec mesh-db psql -U mesh -d mesh -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    requests.get(P + "/health", timeout=10).json()
except Exception as e:
    print("SKIP meshplan_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)
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
def flt(num, v): return tag(num, 5) + struct.pack("<f", v)
def data(port, payload): return ld(4, vi(1, port) + ld(2, payload))
def envelope(sender, port, payload, gateway="!feedbeef", snr=None, channel="LongFast"):
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF) + data(port, payload) + fx(6, 12345)
    if snr is not None: pk += flt(8, snr)
    return ld(1, pk) + ld(2, channel) + ld(3, gateway)
def send(env): return requests.post(P + "/ingest", json={"envelope_b64": base64.b64encode(env).decode()}, timeout=20).json()["result"]
base = 0xC0000000 + ((int(time.time()) & 0xFFF) << 4)
ids = []
def node(i, name, lat, lon, role=0, gateways=(("!feedbeef", None),)):
    nid = base + i
    ids.append(nid)
    for gw, snr in gateways:
        send(envelope(nid, 4, ld(2, name) + ld(3, name[:4]) + vi(5, 9) + vi(7, role), gateway=gw, snr=snr))
        send(envelope(nid, 3, sfx(1, int(lat * 1e7)) + sfx(2, int(lon * 1e7)) + vi(3, 100) + vi(23, 16), gateway=gw, snr=snr))
    return "%08x" % nid
mpsql("DELETE FROM nodes WHERE node_id >= 3221225472 AND node_id < 3238002688 AND long_name LIKE 'PlanTest%'")
mpsql("DELETE FROM heard WHERE node_id NOT IN (SELECT node_id FROM nodes)")
CA = [node(1 + i, f"PlanTest A{i}", 32.20 + i * 0.01, -95.30 + i * 0.005) for i in range(4)]       # a cluster nobody serves
CB = [node(10 + i, f"PlanTest B{i}", 36.90 + i * 0.002, -89.00) for i in range(3)]                 # a cluster with a router beside it
node(20, "PlanTest B Router", 36.905, -89.001, role=2)
Cn = node(30, "PlanTest C Good", 35.00, -92.00, gateways=(("!feedbeef", 9.0), ("!feedbee0", 8.0)))   # two gateways, strong
Dn = node(31, "PlanTest D Weak", 33.00, -94.50, gateways=(("!feedbeef", -12.0), ("!feedbee0", -14.0)))   # two gateways, weak
r = requests.get(P + "/plan?radius=15&snr=-5&results=30&sep=3", timeout=60).json()
sug = r["suggestions"]
def near(s, lat, lon, km=10):
    return math.hypot((s["lat"] - lat) * 111.2, (s["lon"] - lon) * 111.2 * math.cos(math.radians(lat))) <= km
a = [s for s in sug if near(s, 32.21, -95.29)]
check("the unserved cluster gets a suggestion that helps all four nodes", a and a[0]["helps"] >= 4 and {n["slug"] for n in a[0]["nodes"]} & set(CA), a)
check("the cluster with a router beside it gets none", not any(near(s, 36.90, -89.00) for s in sug), [s for s in sug if near(s, 36.90, -89.00)])
check("a node two gateways hear strongly is not counted", not any(n["slug"] == Cn for s in sug for n in s["nodes"]))
check("a node two gateways hear only weakly is", any(n["slug"] == Dn for s in sug for n in s["nodes"]), [s["nodes"] for s in sug][:3])
check("the answer says how many nodes it looked at and how many are poorly served", r["active"] >= 9 and r["poor"] >= 5 and r["serving"] >= 1 and r["radius_km"] == 15, r)
small = requests.get(P + "/plan?radius=2&snr=-5&results=30&sep=3", timeout=60).json()["suggestions"]
check("a smaller reach helps fewer nodes per place", [s for s in small if near(s, 32.21, -95.29)] and max(s["helps"] for s in small if near(s, 32.21, -95.29)) < a[0]["helps"])

setting("mesh_enabled", "1")
setting("mesh_plan_results", "30")   # the page shows the best places first: other suites leave poorly served nodes around, so ask for enough to see ours
anon = requests.Session()
pg = anon.get(B + "/mesh/planning")
check("the page lists the suggestion with its place and a map link, and explains that it is a guess", pg.status_code == 200 and "32.2" in pg.text and "PlanTest A" in pg.text and "/mesh/map?lat=" in pg.text and "guess" in pg.text, pg.status_code)
check("the page counts the poorly served nodes", "Poorly served nodes" in pg.text)
check("the menu has Router Planning", 'href="/mesh/planning"' in anon.get(B + "/mesh/").text)
adm = sess("marty", PW)
act(adm, "/admin/mesh/hide", "/admin/mesh", node="!" + CA[0], reason="plan test")
check("a hidden node is left out of the names", "PlanTest A0" not in anon.get(B + "/mesh/planning").text)
act(adm, "/admin/mesh/unhide", "/admin/mesh", node="!" + CA[0])
st = adm.get(B + "/admin/settings/radio").text
check("the numbers are settings", all(x in st for x in ("Router Planning: Reach of a New Router", "Router Planning: Weak Signal Below", "Router Planning: Places to Suggest", "Router Planning: Keep Away From Existing Routers")))
psql("DELETE FROM settings WHERE key = 'mesh_plan_results'")
mpsql("DELETE FROM nodes WHERE long_name LIKE 'PlanTest%'")
mpsql("DELETE FROM heard WHERE node_id NOT IN (SELECT node_id FROM nodes)")
print("ALL PASS" if ok_all else "SOME FAILED")
