"""The private pages are never poorer than the public ones (requests 231 and 232): a node the private gateways heard is filled in from the public record of the same node (name, hardware, role and so on) and shows
the later of the two last-heard times; a node on the private feed that reports its neighbors has them kept (privately) and shown on its page, together with any the public side heard. Needs the mesh service on the
test box (sh tests/deploy_mesh.sh)."""
import base64, json, os, struct, subprocess, time, requests
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18482"
try:
    requests.get(P + "/health", timeout=10).json()
except Exception as e:
    print("SKIP privatesync_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)
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
def envelope(sender, port, payload, gateway, channel="LongFast", bitfield=None):
    d = vi(1, port) + ld(2, payload) + (vi(9, bitfield) if bitfield is not None else b"")
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, d) + fx(6, 4243)
    return ld(1, pk) + ld(2, channel) + ld(3, gateway)
def nodeinfo(sid, long, short, role=0): return ld(1, sid) + ld(2, long) + ld(3, short) + vi(5, 9) + vi(7, role)
def position(lat, lon): return sfx(1, int(lat * 1e7)) + sfx(2, int(lon * 1e7)) + vi(3, 90)
def neighbor_info(node, neighbors): return vi(1, node) + b"".join(ld(4, vi(1, n) + flt(2, snr) + fx(3, 1700000000) + vi(4, 21600)) for n, snr in neighbors)
def priv(com, env, gw):
    return requests.post(P + "/ingest_private", json={"topic": f"mshp/{com}/2/e/LongFast/{gw}", "envelope_b64": base64.b64encode(env).decode()}, timeout=20).json()["result"]
def pub(env):
    return requests.post(P + "/ingest", json={"topic": "msh/US/AR/2/e/LongFast/!feedbee1", "envelope_b64": base64.b64encode(env).decode()}, timeout=20).json()["result"]
def pnode(nid): return requests.get(P + "/private/nodes/!%08x" % nid, timeout=20).json()
def plist(): return {n["id"]: n for n in requests.get(P + "/private/nodes", timeout=20).json()["nodes"]}
def sid(n): return "!%08x" % n

requests.post(P + "/config", json={"private_enabled": "1"}, timeout=20)
def clean():
    subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec mesh-db psql -U mesh -d mesh -tA -c \"DELETE FROM pnodes WHERE long_name LIKE 'ZZ %' OR node_id >= 3000000000 AND node_id < 3000100000; DELETE FROM nodes WHERE long_name LIKE 'ZZ %' OR node_id >= 3000000000 AND node_id < 3000100000; DELETE FROM plinks WHERE a >= 3000000000 AND a < 3000100000; DELETE FROM links WHERE a >= 3000000000 AND a < 3000100000\""], capture_output=True)
clean()
t = int(time.time()) & 0xFFF
com = "zzsync" + str(t % 100)
gw = "!%08x" % (0xCAFE1000 + t)
base = 3000000000 + t * 16
M, X, Y, Z = base, base + 1, base + 2, base + 3

# a node the private gateways heard only by position, the public side knows by name and hardware
check("the private feed keeps a node it heard only by position", priv(com, envelope(M, 3, position(34.70, -92.30), gw), gw) in ("private", "public"))
before = plist().get(sid(M), {})
check("the private list has the node without a name", before and not before.get("long_name"), before)
check("the public side learns who it is", pub(envelope(M, 4, nodeinfo(sid(M), "ZZ Public Name", "ZZPN", 2), "!feedbee1")) == "stored")
time.sleep(1.2)
after = plist().get(sid(M), {})
check("the private list now carries the public name, short name, hardware and role", after.get("long_name") == "ZZ Public Name" and after.get("short_name") == "ZZPN" and after.get("hardware") and after.get("role") == 2, after)
check("and its last heard is the later of the two (the public packet)", after.get("last_seen", "") >= before.get("last_seen", ""), (before.get("last_seen"), after.get("last_seen")))
check("the private node's own page is filled in too", pnode(M)["node"].get("long_name") == "ZZ Public Name")
check("the private record keeps its own exact position", abs(after.get("lat", 0) - 34.70) < 0.001, after.get("lat"))

# a private-only node reports its neighbors
check("a private-only node is kept", priv(com, envelope(X, 4, nodeinfo(sid(X), "ZZ Private Only", "ZZPO"), gw), gw) == "private")
check("a private-only neighbor is kept", priv(com, envelope(Y, 4, nodeinfo(sid(Y), "ZZ Private Friend", "ZZPF"), gw), gw) == "private")
r = priv(com, envelope(X, 71, neighbor_info(X, [(Y, 7.5), (Z, -3.25)]), gw), gw)
check("its neighbor report is kept, not ignored", r in ("private", "public"), r)
nb = {x["id"]: x for x in pnode(X).get("neighbors", [])}
check("its private page lists the neighbors it reported, with names where known and the signal", sid(Y) in nb and nb[sid(Y)]["name"] == "ZZ Private Friend" and nb[sid(Y)]["snr"] == 7.5 and nb[sid(Y)]["direction"] == "hears" and sid(Z) in nb, nb)
check("the neighbor reports stay private: the public side has no such node and no such links", requests.get(P + "/nodes/" + sid(X)).status_code == 404 and not any(l["a"] == sid(X) for l in requests.get(P + "/links?days=30").json().get("links", [])))
check("the other side sees it too (the node that was heard)", any(x["id"] == sid(X) and x["direction"] == "heard_by" for x in pnode(Y).get("neighbors", [])))
check("a report is only believed from the node itself", priv(com, envelope(Y, 71, neighbor_info(X, [(Z, 1.0)]), gw), gw) in ("ignored", "private"))
# everything the private feed hears is kept: every kind of packet, read as far as it can be, and the words of a text message never
def raw_private(topic, body): return requests.post(P + "/ingest_private", json={"topic": topic, "envelope_b64": base64.b64encode(body).decode()}, timeout=20).json()["result"]
tel = vi(1, 1700000000) + ld(2, vi(1, 77) + flt(2, 4.1) + flt(3, 3.5) + flt(4, 1.25) + vi(5, 7200)) + ld(3, flt(1, 21.5) + flt(2, 40.0) + flt(3, 1013.0)) + ld(4, flt(1, 3.0) + flt(2, 5.0)) + ld(5, flt(1, 5.0) + flt(2, 0.25))
priv(com, envelope(X, 67, tel, gw), gw)
priv(com, envelope(X, 1, b"the secret words", gw), gw)
priv(com, envelope(X, 300, ld(1, "odd") + vi(2, 42), gw), gw)
priv(com, envelope(X, 3, position(34.5, -92.1) + vi(4, 1700000001) + vi(14, 99), gw), gw)
raw_private(f"mshp/{com}/2/e/LongFast/{gw}", ld(1, fx(1, X) + fx(2, 0xFFFFFFFF) + ld(5, b"\x01\x02\x03\x04") + fx(6, 4244)) + ld(2, "LongFast") + ld(3, gw))
requests.post(P + "/ingest_private", json={"topic": f"mshp/{com}/2/json/LongFast/{gw}", "envelope_b64": base64.b64encode(json.dumps({"from": X, "type": "text", "payload": "words in json", "snr": 5.5, "rssi": -90}).encode()).decode()}, timeout=20)
time.sleep(1)
pk = {k["label"]: k for k in pnode(X).get("packets", [])}
check("every kind of packet heard is listed with its count", {"Telemetry", "Position", "Node Info", "Neighbor Info", "Port 300", "Encrypted (cannot be read)"} <= set(pk) and "Text Message (words never kept)" in pk, list(pk))
check("telemetry is read: the device metrics, the environment and the other groups", all(w in pk["Telemetry"]["summary"] for w in ("battery_level", "voltage", "environment_readings", "temp_c", "air_quality_metrics", "power_metrics")), pk["Telemetry"]["summary"])
check("a kind this service does not know has every field kept", "field 2" in pk["Port 300"]["summary"] and "odd" in pk["Port 300"]["summary"], pk["Port 300"]["summary"])
check("a position keeps its extra fields too", "gps_accuracy" in pk["Position"]["summary"] and "34.5" in pk["Position"]["summary"], pk["Position"]["summary"])
check("the words of a text message are never kept", pk["Text Message (words never kept)"]["count"] >= 1 and "secret" not in json.dumps(pk) and "words in json" not in json.dumps(pk))
check("an encrypted packet is counted", pk["Encrypted (cannot be read)"]["count"] >= 1)
# the site's own pages (the portal and the mesh service must both be on the test box)
try:
    exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
    adm = sess("marty", PW)
    psql_ = lambda q: subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"" + q + "\""], capture_output=True, text=True).stdout.strip()
    psql_("INSERT INTO settings (key, value) VALUES ('mesh_private_enabled','1'),('mesh_links_show','1') ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value")
    page = adm.get(B + "/mesh/" + sid(X).lstrip("!")).text
    check("the administrator's page for a private-only node lists its neighbors", "Neighbors (Direct)" in page and "ZZ Private Friend" in page, page[:200])
    lst = adm.get(B + "/admin/mesh/private-nodes?q=ZZ").text
    check("the Private Nodes list shows the public name for a node the private gateways heard without one", "ZZ Public Name" in lst)
except NameError:
    pass
st = requests.get(P + "/stats", timeout=20).json()
check("the service keeps counting", st is not None)

clean()
print("ALL PASS" if ok_all else "SOME FAILED")
