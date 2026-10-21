"""The Mesh overview and Neighbor Info. The overview is a real overview (live numbers from the mesh service, folding sections, a map), the node list is /mesh/nodes and the old addresses lead there. Neighbor Info
packets (portnum 71) are decoded only when they arrive in the clear, only from a node we already have, never for a hidden node, kept as direct links, shown as a Direct Links layer on the Mesh Map where both
ends have a position, listed on each node's page, counted on the overview, and pruned by age. Needs the service and broker on the test box (sh tests/deploy_mesh.sh)."""
import base64, json, os, re, struct, subprocess, time, requests
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
    h = requests.get(P + "/health", timeout=10).json()
except Exception as e:
    print("SKIP meshlinks_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)

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
def envelope(sender, port=None, payload=b"", gateway="!feedbeef", snr=None, encrypted=False):
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF)
    pk += ld(5, b"\x01\x02\x03\x04") if encrypted else data(port, payload)
    pk += fx(6, 12345)
    if snr is not None: pk += flt(8, snr)
    return ld(1, pk) + ld(2, "LongFast") + ld(3, gateway)
def map_report(long, short, role, hw, fw, lat, lon):
    return ld(1, long) + ld(2, short) + vi(3, role) + vi(4, hw) + ld(5, fw) + vi(6, 1) + vi(7, 0) + vi(8, 1) + sfx(9, int(lat * 1e7)) + sfx(10, int(lon * 1e7)) + vi(11, 100) + vi(12, 16) + vi(13, 2) + vi(14, 1)
def neighbor_info(node, neighbors, interval=21600):
    nb = b"".join(ld(4, vi(1, n) + flt(2, snr) + fx(3, 1700000000) + vi(4, 21600)) for n, snr in neighbors)
    return vi(1, node) + vi(2, node) + vi(3, interval) + nb
def send(env): return requests.post(P + "/ingest", json={"envelope_b64": base64.b64encode(env).decode()}, timeout=20).json()["result"]

tagn = int(time.time()) & 0xFFFF
A, Bn, C, D, OUT = (0xC1D20000 + tagn + i * 17 for i in range(5))
sid = lambda n: "!%08x" % n
gw = "!%08x" % (0xBEEF0000 + tagn)
for n in (A, Bn, C, D, OUT):
    requests.post(P + "/unhide", json={"id": sid(n)})
check("the service understands port 71: a node we know reports its neighbors", True)
send(envelope(A, 73, map_report("ZZ Link Alpha", "ZZLA", 2, 9, "2.7.9.abc", 34.80, -92.30), gateway=gw))
send(envelope(Bn, 73, map_report("ZZ Link Bravo", "ZZLB", 0, 9, "2.7.9.abc", 34.90, -92.10), gateway=gw))
send(envelope(C, 73, map_report("ZZ Link Charlie", "ZZLC", 0, 9, "2.7.9.abc", 35.20, -92.00), gateway=gw))
requests.post(P + "/hide", json={"id": sid(D), "reason": "test"})
r = send(envelope(A, 71, neighbor_info(A, [(Bn, 8.5), (C, -7.25), (D, 2.0), (A, 5.0), (0xFFFFFFFF, 1.0), (0x0000FEED, -3.0)]), gateway=gw, snr=4.0))
check("a neighbor report from a known node is stored", r == "stored", r)
L = {(x["a"], x["b"]): x for x in requests.get(P + "/links").json()["links"]}
check("the direct links are kept with the signal, the node that reported and the gateway that passed it on", L[(sid(A), sid(Bn))]["snr"] == 8.5 and L[(sid(A), sid(C))]["snr"] == -7.25 and L[(sid(A), sid(Bn))]["reported_by"] == gw, str(L)[:300])
check("a hidden neighbor, the node itself and the broadcast address are not kept, an unknown node is (its position may come later)", (sid(A), sid(D)) not in L and (sid(A), sid(A)) not in L and not any(k[1] == "!ffffffff" for k in L) and (sid(A), "!0000feed") in L)
check("an encrypted packet is never read", send(envelope(A, encrypted=True, gateway=gw)) == "encrypted" and send(envelope(A, 71, neighbor_info(A, [(Bn, 9.9)]), gateway=gw, encrypted=True)) == "encrypted" and requests.get(P + "/links").json()["links"] is not None)
check("a report from a node we do not have is ignored (so nothing outside the area is kept)", send(envelope(OUT, 71, neighbor_info(OUT, [(A, 3.0)]), gateway=gw)) == "ignored" and not any(x["a"] == sid(OUT) for x in requests.get(P + "/links").json()["links"]))
requests.post(P + "/hide", json={"id": sid(Bn)}); requests.post(P + "/unhide", json={"id": sid(Bn)})
check("hiding a node removes the links it is on", not any(sid(Bn) in (x["a"], x["b"]) for x in requests.get(P + "/links").json()["links"]))
send(envelope(Bn, 73, map_report("ZZ Link Bravo", "ZZLB", 0, 9, "2.7.9.abc", 34.90, -92.10), gateway=gw))
send(envelope(A, 71, neighbor_info(A, [(Bn, 9.0), (C, -7.0)]), gateway=gw))
send(envelope(Bn, 71, neighbor_info(Bn, [(A, 6.0)]), gateway=gw))
r = requests.post(P + "/ingest_json", json={"topic": "msh/US/AR/2/json/LongFast/" + gw, "payload": json.dumps({"from": C, "type": "neighborinfo", "sender": gw, "payload": {"node_id": C, "neighbors": [{"node_id": A, "snr": -6.0}]}})}).json()["result"]
check("a gateway's decoded JSON neighbor report is read the same way", r == "stored" and any(x["a"] == sid(C) and x["b"] == sid(A) for x in requests.get(P + "/links").json()["links"]), r)
nd = requests.get(P + "/nodes/" + sid(A)).json()
check("the service's node answer lists the neighbors, both directions, with names", {(x["id"], x["direction"]) for x in nd["neighbors"]} >= {(sid(Bn), "hears"), (sid(Bn), "heard_by"), (sid(C), "heard_by")} and any(x["name"] == "ZZ Link Bravo" for x in nd["neighbors"]), nd["neighbors"])
check("the statistics count the links and the nodes that reported", requests.get(P + "/stats").json()["links"] >= 4 and requests.get(P + "/stats").json()["link_reporters"] >= 2)
old = mpsql(f"UPDATE links SET last_seen = now() - interval '40 days' WHERE a = {C} RETURNING a")
requests.post(P + "/prune")
check("a link not reported again within the keep days is deleted, the others stay", not any(x["a"] == sid(C) for x in requests.get(P + "/links?hours=100000").json()["links"]) and any(x["a"] == sid(A) for x in requests.get(P + "/links").json()["links"]))

# ---- the portal
setting("mesh_enabled", "1"); setting("mesh_up_enabled", "0"); setting("mesh_links_show", "1"); setting("mesh_links_show_days", "14"); time.sleep(3)
adm = sess("marty", PW)
lj = requests.get(B + "/api/mesh/links.json").json()
pairs = {tuple(sorted((x["a"], x["b"]))): x for x in lj["links"]}
ab = pairs.get(tuple(sorted((sid(A), sid(Bn)))))
check("the map's link data has each pair once, both ends with a position, the stronger signal and whether both ends reported", ab and ab["both"] is True and ab["snr"] == 9.0 and ab["a_lat"] and ab["b_lon"] and "Neighbor Info" in lj["note"], lj)
check("a link to a node with no position is not drawn", not any(sid(0x0000FEED) in (x["a"], x["b"]) for x in lj["links"]))
mp = requests.get(B + "/mesh/map").text
check("the Mesh Map has the Direct Links layer toggle and its legend", 'id="mm-direct"' in mp and "Strong (SNR above 5 dB)" in mp and "Weak (below -5 dB)" in mp)
npg = requests.get(B + f"/mesh/{A:08x}").text
check("the node page lists the neighbors with the signal and the direction", "Neighbors (Direct)" in npg and "ZZ Link Bravo" in npg and "this node hears it" in npg and "it hears this node" in npg and "9.0" in npg)
ov = requests.get(B + "/mesh/").text
st = requests.get(P + "/stats").json()
check("the overview is a real overview: folding sections, tiles from the service's own numbers, no node table", all(x in ov for x in ('id="sec-glance"', 'id="sec-attention"', 'id="sec-map"', "Mesh at a Glance", "Needs Attention", "Online Now", "Gateways and Observers", "Direct Links", "Newly Seen Nodes", "Busiest Gateways")) and "<th" not in ov.split('id="sec-gateways"')[0])
num = lambda key: int(re.search(r'id="%s"><div class="tl"[^>]*>[^<]*</div><div class="tv">(\d+)' % key, ov).group(1))
check("the numbers on it are the service's: nodes, heard in a day, gateways", num("g-nodes") == st["nodes"] and re.search(r'<div class="tv">%d</div>\s*<div class="tl">In the last 24 hours' % st["heard_day"], ov) and num("g-gateways") == len(st["gateways"]), (num("g-nodes"), st["nodes"]))
check("the link count on the overview matches the service", num("g-links") == st["links"], (num("g-links"), st["links"]))
check("the roles, firmware and hardware breakdowns and the problem counts are there", "Roles and kinds" in ov and "Firmware (Meshtastic)" in ov and "Routers not heard for" in ov and "Nodes at or below" in ov and 'href="/mesh/health"' in ov)
check("the new nodes list names the nodes first heard today", "ZZ Link Alpha" in ov)
check("it links to the map, the join page and the node list", all(x in ov for x in ('href="/mesh/map"', 'href="/feed/mesh"', 'href="/mesh/nodes"')))
nl = requests.get(B + "/mesh/nodes")
check("the node list is on its own page, with the search form posting to it", nl.status_code == 200 and "ZZ Link Alpha" in nl.text and 'action="/mesh/nodes"' in nl.text)
red = requests.get(B + "/mesh/", params={"q": "ZZ Link", "network": "meshtastic"}, allow_redirects=False)
check("the old list addresses (with a search) lead to the new page, keeping the search", red.status_code == 301 and red.headers["Location"].startswith("/mesh/nodes?") and "q=ZZ" in red.headers["Location"], red.headers.get("Location"))
check("the Mesh menu lists Nodes", 'href="/mesh/nodes"' in requests.get(B + "/").text)
fp = requests.get(B + "/feed/mesh").text
check("the join page no longer says ArkSignal drops Neighbor Info and says what is done with it", "does not use Neighbor Info yet" not in fp and "Direct Links layer" in fp and "30 days" in fp)
setting("mesh_links_show", "0")
time.sleep(1)
from_off = requests.get(B + "/api/mesh/links.json").json()
check("with links switched off in Settings the map data is empty, the layer toggle and node lists are gone and the overview says so", from_off["links"] == [] and 'id="mm-direct"' not in requests.get(B + "/mesh/map").text and "Neighbors (Direct)" not in requests.get(B + f"/mesh/{A:08x}").text and "switched off in Settings" in requests.get(B + "/mesh/").text)
setting("mesh_links_show", "1")
sp = adm.get(B + "/admin/settings").text
check("the settings are in Admin > Settings", all(x in sp for x in ("Show Direct Links (Neighbor Info)", "Keep Direct Links For (Days)", "Show Links Reported Within (Days)", "Count a Node as Online if Heard Within")))
check("the terms file covers Neighbor Info", "Neighbor Info" in open("../DATA_SOURCES_TERMS.md", encoding="utf-8").read())
for n in (A, Bn, C, D, OUT):
    requests.post(P + "/hide", json={"id": sid(n)}); requests.post(P + "/unhide", json={"id": sid(n)})
setting("mesh_enabled", "0")
print("ALL PASS" if ok_all else "SOME FAILED")
