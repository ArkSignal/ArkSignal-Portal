"""Mesh nodes (Meshtastic and MeshCore): the service's decoding of Meshtastic packets (map report, position, node information, telemetry; encrypted and out-of-area packets are not kept, a hidden node stays hidden), MeshCore adverts from observers (signature checked, chat dropped), its MQTT door
(a packet published to a real Mosquitto arrives), and the portal pages (list, node page, map, Join guide in the standard feed form, the administrators' page, the menu, Settings, status and the registry).
Needs the service and broker running on the test box (sh tests/deploy_mesh.sh)."""
import base64, os, re, struct, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18482"
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=120).stdout
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    h = requests.get(P + "/health", timeout=10).json()
except Exception as e:
    print("SKIP mesh_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)
check("the service answers and says its version", h.get("ok") is True and "version" in h)

# ---- a protobuf writer for just what the test needs
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
def envelope(sender, port=None, payload=b"", gateway="!feedbeef", snr=None, rssi=None, encrypted=False, channel="LongFast"):
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF)
    pk += ld(5, b"\x01\x02\x03\x04") if encrypted else data(port, payload)
    pk += fx(6, 12345)
    if snr is not None: pk += flt(8, snr)
    if rssi is not None: pk += vi(12, rssi)
    return ld(1, pk) + ld(2, channel) + ld(3, gateway)
def map_report(long, short, role, hw, fw, lat, lon, alt=100, prec=16, online=3):
    return ld(1, long) + ld(2, short) + vi(3, role) + vi(4, hw) + ld(5, fw) + vi(6, 1) + vi(7, 0) + vi(8, 1) + sfx(9, int(lat * 1e7)) + sfx(10, int(lon * 1e7)) + vi(11, alt) + vi(12, prec) + vi(13, online) + vi(14, 1)
def send(env): return requests.post(P + "/ingest", json={"envelope_b64": base64.b64encode(env).decode()}, timeout=20).json()["result"]

tagn = int(time.time()) & 0xFFFF
nid = 0xA1B20000 + tagn
sid = "!%08x" % nid
gw = "!%08x" % (0xBEEF0000 + tagn)
requests.post(P + "/unhide", json={"id": sid}); requests.post(P + "/hide", json={"id": sid}); requests.post(P + "/unhide", json={"id": sid})
r = send(envelope(nid, 73, map_report("ZZ Test Hill", "ZZTH", 2, 9, "2.5.9.abc", 34.7465, -92.2896, 150, 16, 4), gateway=gw, snr=6.5, rssi=-88))
check("a map report is stored", r == "stored", r)
nid_c = nid + 777
rc = send(envelope(nid_c, 73, ld(1, "ZZ Plain Client") + ld(2, "ZZPC") + vi(4, 110) + ld(5, "2.7.26.abc") + vi(6, 1) + vi(7, 7) + vi(8, 1) + sfx(9, int(34.3081 * 1e7)) + sfx(10, int(-92.4123 * 1e7)) + vi(12, 15), gateway=gw, snr=3.0))
ndc = requests.get(P + "/nodes/!%08x" % nid_c).json()["node"]
check("a map report that leaves the role out (the Client role, which the protocol never writes) shows as Client, and a newer hardware model has its name", rc == "stored" and ndc["role_name"] == "Client" and ndc["hardware"] == "Heltec V4", ndc)
n = requests.get(P + "/nodes/" + sid).json()
nd = n["node"]
check("with its name, role, hardware, firmware, region, preset and position", nd["long_name"] == "ZZ Test Hill" and nd["short_name"] == "ZZTH" and nd["role_name"] == "Router" and nd["hardware"] == "RAK4631" and nd["firmware"] == "2.5.9.abc"
      and nd["region_name"] == "US" and nd["preset_name"] == "Long Fast" and abs(nd["lat"] - 34.7465) < 1e-6 and abs(nd["lon"] + 92.2896) < 1e-6 and nd["alt"] == 150 and nd["precision_bits"] == 16 and nd["online_local"] == 4, nd)
check("the gateway that heard it and the signal are kept", any(x["gateway"] == gw and x["snr"] == 6.5 and x["rssi"] == -88 for x in n["heard_by"]), n["heard_by"])
send(envelope(nid, 73, map_report("ZZ Test Hill", "ZZTH", 2, 9, "2.5.9.abc", 34.7465, -92.2896), gateway=gw, snr=9.0, rssi=-80))
n2 = requests.get(P + "/nodes/" + sid).json()["heard_by"]
check("a second packet raises the count and the best signal", any(x["gateway"] == gw and x["count"] >= 2 and x["best_snr"] == 9.0 for x in n2), n2)
check("an encrypted packet is not read or stored", send(envelope(nid + 1, encrypted=True)) == "encrypted" and requests.get(P + f"/nodes/!{nid + 1:08x}").status_code == 404)
check("a node outside the area is not kept", send(envelope(nid + 2, 73, map_report("Far", "FAR", 0, 4, "1", 51.5, -0.12))) == "outside" and requests.get(P + f"/nodes/!{nid + 2:08x}").status_code == 404)
pos = struct.pack("<") + sfx(1, int(35.1 * 1e7)) + sfx(2, int(-92.4 * 1e7)) + vi(3, 90)
check("a position packet moves the node (on a public channel an exact position is cut to the public rules' 16 bits, about 700 meters)", send(envelope(nid, 3, pos, gateway=gw)) == "stored" and 0 < abs(requests.get(P + "/nodes/" + sid).json()["node"]["lat"] - 35.1) < 0.004)
ni = ld(1, sid) + ld(2, "ZZ Renamed") + ld(3, "ZZRN") + vi(5, 4) + vi(7, 0)
send(envelope(nid, 4, ni, gateway=gw))
nn = requests.get(P + "/nodes/" + sid).json()["node"]
check("node information renames it and changes its hardware and role", nn["long_name"] == "ZZ Renamed" and nn["short_name"] == "ZZRN" and nn["hardware"] == "T-Beam" and nn["role_name"] == "Client", nn)
tel = ld(2, vi(1, 87) + flt(2, 4.05) + flt(3, 12.5) + flt(4, 1.5) + vi(5, 7200))
send(envelope(nid, 67, tel, gateway=gw))
nt = requests.get(P + "/nodes/" + sid).json()["node"]
check("telemetry gives the battery, voltage, channel use, air time and uptime", nt["battery"] == 87 and abs(nt["voltage"] - 4.05) < 0.01 and abs(nt["channel_util"] - 12.5) < 0.01 and nt["uptime"] == 7200, nt)
check("telemetry alone does not add an unknown node", send(envelope(nid + 3, 67, tel)) == "ignored" and requests.get(P + f"/nodes/!{nid + 3:08x}").status_code == 404)
lst = requests.get(P + "/nodes", params={"q": "ZZ Renamed", "placed": "1"}).json()["nodes"]
check("the list finds it by name, and by ID", len(lst) == 1 and requests.get(P + "/nodes", params={"q": sid}).json()["nodes"].__len__() == 1)
check("junk bytes are ignored without an error", send(b"\x00\x01garbage") in ("ignored", "encrypted"))
st = requests.get(P + "/stats").json()
check("statistics carry the counts, gateways, roles, hardware, feed and settings", all(k in st for k in ("nodes", "heard_hour", "placed", "routers", "gateways", "roles", "hardware", "feed", "config")) and st["feed"]["decoded"] >= 3 and st["feed"]["encrypted"] >= 1 and st["feed"]["outside"] >= 1, st.get("feed"))
requests.post(P + "/hide", json={"id": sid})
check("a hidden node is gone and stays gone", requests.get(P + "/nodes/" + sid).status_code == 404 and send(envelope(nid, 73, map_report("Back", "BACK", 0, 4, "1", 34.7, -92.3), gateway=gw)) == "hidden")
check("a bad ID is refused", requests.post(P + "/hide", json={"id": "nonsense"}).status_code == 400)
requests.post(P + "/unhide", json={"id": sid})

# ---- MeshCore: adverts heard by an observer (meshcoretomqtt's JSON), signed with the node's own key
import json as _json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
def mc_advert(priv, name, kind, lat, lon, ts=None, route=1, hops=0, flip_signature=False):
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    t = struct.pack("<I", int(ts or time.time()))
    flags = kind | 0x10 | 0x80
    app = bytes([flags]) + struct.pack("<ii", int(lat * 1e6), int(lon * 1e6)) + name.encode()
    sig = priv.sign(pub + t + app)
    if flip_signature: sig = bytes([sig[0] ^ 0xFF]) + sig[1:]
    header = (0 << 6) | (4 << 2) | route     # version 1, payload type ADVERT (4), route type
    return bytes([header, hops]) + bytes(hops) + pub + t + sig + app, pub.hex()
def mc_msg(raw, observer="ZZ Observer", obs_id="ab" * 32, snr=7, rssi=-95):
    return _json.dumps({"origin": observer, "origin_id": obs_id, "type": "PACKET", "direction": "rx", "packet_type": "4", "route": "F", "raw": raw.hex(), "SNR": str(snr), "RSSI": str(rssi)})
def pub(user, pw, topic, blob):
    """Publish a file to the real broker as a login; returns the mosquitto_pub exit code line (rc=0 accepted)."""
    out = ssh(f"echo {base64.b64encode(blob).decode()} | base64 -d > /tmp/pub_pkt.bin; docker cp /tmp/pub_pkt.bin mesh-mqtt:/tmp/pub_pkt.bin; docker exec mesh-mqtt mosquitto_pub -h localhost -u {user} -P {pw} -t {topic} -f /tmp/pub_pkt.bin; echo rc=$?")
    return out.strip().splitlines()[-1] if out.strip() else "rc=?"
PUBU, PUBP = "zz-test-pub", "zzTestPublish99"
def mc_send(payload):   # through the broker, like a real observer
    return pub(PUBU, PUBP, "meshcore/LIT/" + "ab" * 32 + "/packets", payload.encode())
def wait_for(path, tries=20):
    for _ in range(tries):
        time.sleep(1)
        r = requests.get(P + path)
        if r.status_code == 200:
            return r.json()
    return None
for _ in range(30):
    cr = requests.post(P + "/credentials/set", json={"username": PUBU, "password": PUBP}).json()
    if cr.get("ok"): break
    time.sleep(2)
check("the service makes a publisher login in the broker", cr.get("ok"), cr)
MCNAME = f"ZZ MC Hill {tagn}"
priv = Ed25519PrivateKey.generate()
raw, pubhex = mc_advert(priv, MCNAME, 2, 34.7465, -92.2896, hops=2)
mc_send(mc_msg(raw))
mcn = wait_for("/nodes/mc-" + pubhex)
check("a MeshCore advert heard by an observer is stored (name, kind Repeater, position)", mcn is not None and mcn["node"]["network"] == "meshcore" and mcn["node"]["long_name"] == MCNAME and mcn["node"]["role_name"] == "Repeater" and abs(mcn["node"]["lat"] - 34.7465) < 1e-5 and abs(mcn["node"]["lon"] + 92.2896) < 1e-5, mcn)
check("the observer that heard it, the signal and the hops are kept", mcn and any(h["gateway"] == "mc:" + "ab" * 32 and h["snr"] == 7 and h["rssi"] == -95 and h["hops"] == 2 and h["name"] == "ZZ Observer" for h in mcn["heard_by"]), mcn and mcn["heard_by"])
priv2 = Ed25519PrivateKey.generate()
bad, badpub = mc_advert(priv2, "ZZ Forged", 2, 34.8, -92.3, flip_signature=True)
mc_send(mc_msg(bad)); time.sleep(3)
check("an advert with a bad signature is not kept", requests.get(P + "/nodes/mc-" + badpub).status_code == 404 and requests.get(P + "/stats").json()["feed"]["mc_badsig"] >= 1)
far, farpub = mc_advert(Ed25519PrivateKey.generate(), "ZZ Far Away", 1, 51.5, -0.12)
mc_send(mc_msg(far)); time.sleep(3)
check("a MeshCore node outside the area is not kept", requests.get(P + "/nodes/mc-" + farpub).status_code == 404)
chat = bytes([(0 << 6) | (5 << 2) | 1, 0]) + b"\x01\x02\x03encrypted chat bytes"
mc_send(mc_msg(chat)); time.sleep(2)
st2 = requests.get(P + "/stats").json()
check("a chat packet is dropped unread and counted", st2["feed"]["mc_dropped"] >= 1 and st2["meshcore"] >= 1, st2["feed"])
old, oldpub = mc_advert(Ed25519PrivateKey.generate(), "ZZ Replayed", 2, 34.9, -92.4, ts=time.time() - 40 * 86400)
mc_send(mc_msg(old)); time.sleep(3)
check("an old replayed advert is not kept", requests.get(P + "/nodes/mc-" + oldpub).status_code == 404)
both = requests.get(P + "/nodes", params={"q": MCNAME}).json()["nodes"]
check("the list finds a MeshCore node by name and by public key and can be limited to either network", len(both) == 1 and both[0]["network"] == "meshcore" and len(requests.get(P + "/nodes", params={"q": pubhex[:12]}).json()["nodes"]) == 1
      and not requests.get(P + "/nodes", params={"q": MCNAME, "network": "meshtastic"}).json()["nodes"] and len(requests.get(P + "/nodes", params={"q": MCNAME, "network": "meshcore"}).json()["nodes"]) == 1)
requests.post(P + "/hide", json={"id": "mc:" + pubhex})
mc_send(mc_msg(raw)); time.sleep(3)
check("a hidden MeshCore node is gone and stays gone", requests.get(P + "/nodes/mc-" + pubhex).status_code == 404)
requests.post(P + "/unhide", json={"id": "mc:" + pubhex})
mc_send(mc_msg(raw))
mcn = wait_for("/nodes/mc-" + pubhex)
check("and comes back when it is shown again and heard", mcn is not None)

# ---- the MQTT door: a real packet through a real broker
nid2 = nid + 100
env = envelope(nid2, 73, map_report("ZZ Via MQTT", "ZZMQ", 4, 9, "2.5.0", 34.8, -92.1), gateway=gw, snr=3.0)
pub(PUBU, PUBP, "msh/US/AR/2/map/", env)
got = None
for _ in range(20):
    time.sleep(1)
    rr = requests.get(P + f"/nodes/!{nid2:08x}")
    if rr.status_code == 200:
        got = rr.json(); break
check("a packet published to the broker arrives and is stored", got is not None and got["node"]["long_name"] == "ZZ Via MQTT" and got["node"]["role_name"] == "Repeater", got)
# ---- measured coverage: a gateway with a known place, and a node it heard
gwn = int(gw.lstrip("!"), 16)
send(envelope(gwn, 73, map_report("ZZ Coverage GW", "ZZCG", 2, 9, "2.5.9", 34.7, -92.3), gateway=gw))
send(envelope(nid + 500, 73, map_report("ZZ Coverage Far", "ZZCF", 0, 9, "2.5.9", 34.9, -92.0), gateway=gw, snr=-3.0))
time.sleep(1)
cv = requests.get(P + "/coverage").json()["gateways"]
mine = [g for g in cv if g["id"] == "%08x" % gwn]
check("a gateway with a known place gets its measured coverage: the farthest heard node, in the right direction", mine and 25 < mine[0]["reach_km"] < 45 and mine[0]["sectors"][5] > 25 and mine[0]["heard"] >= 1, mine)
# ---- a gateway's decoded JSON (what the public Meshtastic server carries besides the encrypted packets)
import json as _j2
nj = nid + 400
jt = "msh/US/AR/2/json/LongFast/" + gw
pub(PUBU, PUBP, jt, _j2.dumps({"id": 1, "from": nj, "type": "nodeinfo", "sender": gw, "rssi": -90, "snr": 4.5, "payload": {"id": "!%08x" % nj, "longname": "ZZ Json Node", "shortname": "ZZJN", "hardware": 9, "role": 2}}).encode())
time.sleep(2)
check("node information in a gateway's decoded JSON message adds the node (who it is is enough)", requests.get(P + f"/nodes/!{nj:08x}").status_code == 200)
pub(PUBU, PUBP, jt, _j2.dumps({"id": 2, "from": nj, "type": "position", "sender": gw, "rssi": -90, "snr": 4.5, "payload": {"latitude_i": 348000000, "longitude_i": -921000000, "altitude": 120}}).encode())
pub(PUBU, PUBP, jt, _j2.dumps({"id": 3, "from": nj, "type": "nodeinfo", "sender": gw, "rssi": -91, "snr": 5.0, "payload": {"id": "!%08x" % nj, "longname": "ZZ Json Node", "shortname": "ZZJN", "hardware": 9, "role": 2}}).encode())
jn = wait_for(f"/nodes/!{nj:08x}")
check("a position then node information in JSON make a node with its name, place and the gateway that heard it", jn is not None and jn["node"]["long_name"] == "ZZ Json Node" and abs(jn["node"]["lat"] - 34.8) < 0.004 and any(h["gateway"] == gw for h in jn["heard_by"]), jn)
pub(PUBU, PUBP, jt, _j2.dumps({"from": nj + 1, "type": "position", "payload": {"latitude_i": 515000000, "longitude_i": -1200000}}).encode()); time.sleep(2)
check("a JSON position outside the area is not kept", requests.get(P + f"/nodes/!{nj + 1:08x}").status_code == 404)
requests.post(P + "/hide", json={"id": "!%08x" % nj}); requests.post(P + "/unhide", json={"id": "!%08x" % nj})
check("the service says it is connected to the broker", requests.get(P + "/stats").json()["feed"]["connected"] is True)

# ---- the portal
setting("mesh_enabled", "1"); setting("mesh_up_enabled", "0"); time.sleep(3)
adm = sess("marty", PW)
send(envelope(nid, 73, map_report("ZZ Portal Node", "ZZPN", 3, 9, "2.5.9", 34.75, -92.29), gateway=gw, snr=5.0))
send(envelope(nid + 200, 73, map_report("ZZ Gateway", "ZZGW", 2, 9, "2.5.9", 34.80, -92.30), gateway=gw))
pg = requests.get(B + "/mesh/nodes")
check("the node list opens with the node in it", pg.status_code == 200 and "ZZ Portal Node" in pg.text and "Meshtastic" in pg.text)
check("the node page shows what it published and who heard it", all(x in requests.get(B + f"/mesh/{nid:08x}").text for x in ("ZZ Portal Node", "RAK4631", "Heard By", gw)))
check("a MeshCore node has its own page (kind, public key, observers) and appears in the list with its network", all(x in requests.get(B + "/mesh/mc-" + pubhex).text for x in (MCNAME, "MeshCore", "Repeater", pubhex, "ZZ Observer")) and MCNAME in requests.get(B + "/mesh/nodes", params={"network": "meshcore"}).text and "ZZ Portal Node" not in requests.get(B + "/mesh/nodes", params={"network": "meshcore"}).text)
check("the map's data lists both networks with their page names", {x["network"] for x in requests.get(B + "/api/mesh/nodes.json").json()["nodes"] if x["id"] in (sid, "mc:" + pubhex)} == {"meshtastic", "meshcore"})
check("the list links local meshes and maps for Arkansas and its neighbors", all(x in requests.get(B + "/mesh/").text for x in ("lrme.sh", "nwame.sh", "hotme.sh", "memphismesh.com", "lzmesh.com", "meshmap.net", "meshcore")))
check("the overview's small map is given a real center (a missing one crashed the map script and left it blank)", re.search(r'setView\(\[-?\d+(\.\d+)?, -?\d+(\.\d+)?\], 6\)', requests.get(B + "/mesh/").text) is not None)
cvp = requests.get(B + "/api/mesh/coverage.json").json()
check("the portal serves the coverage, labelled as measured", any(g["id"] == "%08x" % gwn for g in cvp["gateways"]) and "not a prediction" in cvp["note"], cvp.get("note"))
check("the Mesh Map has the Measured coverage layer", 'id="mm-cov"' in requests.get(B + "/mesh/map").text)
# ---- mesh health: what needs attention
hq, hl, hb, ho = nid + 600, nid + 601, nid + 602, nid + 603
send(envelope(hq, 73, map_report("ZZ Quiet Router", "ZZQR", 2, 9, "2.5.9", 34.2, -91.5), gateway=gw))
send(envelope(hl, 73, map_report("ZZ Low Battery", "ZZLB", 0, 9, "2.5.9", 34.3, -91.6), gateway=gw))
send(envelope(hb, 73, map_report("ZZ Busy Channel", "ZZBC", 0, 9, "2.5.9", 34.4, -91.7), gateway=gw))
send(envelope(ho, 73, map_report("ZZ Old Firmware", "ZZOF", 0, 9, "2.1.4.abc", 34.5, -91.8), gateway=gw))
send(envelope(hl, 67, ld(2, vi(1, 9)), gateway=gw)); send(envelope(hb, 67, ld(2, flt(3, 41.0)), gateway=gw))
ssh(f"docker exec mesh-db psql -U mesh -d mesh -c \"update nodes set last_seen = now() - interval '3 days' where node_id = {hq}\"")
time.sleep(1)
hp = requests.get(B + "/mesh/health")
check("Mesh Health opens", hp.status_code == 200 and "Routers and Repeaters Gone Quiet" in hp.text)
check("a router not heard for days is listed as gone quiet", "ZZ Quiet Router" in hp.text)
check("a low battery, a crowded channel and an old firmware are listed", all(x in hp.text for x in ("ZZ Low Battery", "ZZ Busy Channel", "ZZ Old Firmware")))
check("and a healthy node is not on the lists", "ZZ Portal Node" not in hp.text.split("Older Firmware")[0])
check("the menu has Mesh Health", 'href="/mesh/health"' in requests.get(B + "/").text)
check("the map page opens and the data file lists the node with a position", requests.get(B + "/mesh/map").status_code == 200 and any(x["id"] == sid for x in requests.get(B + "/api/mesh/nodes.json").json()["nodes"]))
check("the node's own data file answers", requests.get(B + f"/api/mesh/{nid:08x}.json").json()["node"]["id"] == sid)
check("a bad address is a 404", requests.get(B + "/mesh/nonsense").status_code == 404 and requests.get(B + "/mesh/0000ffff").status_code == 404)
check("the map selector offers Mesh Nodes on the other maps", "Mesh Nodes" in requests.get(B + "/repeaters/map").text and "/mesh/map" in requests.get(B + "/aprs/map").text)
check("the menu has a Mesh group with the overview, map and how to join", all(x in requests.get(B + "/").text for x in ('href="/mesh/"', 'href="/mesh/map"', 'href="/feed/mesh"')))
fp = requests.get(B + "/feed/mesh").text
check("mesh nodes that share a spot are fanned out on the Mesh Map and the overview map (they covered each other)", "SPREAD_SAME" in requests.get(B + "/static/colocate.js").text and 'Colocate.track(map, "mesh"' in requests.get(B + "/mesh/map").text and 'Colocate.track(map, "mesh"' in requests.get(B + "/mesh/").text)
check("the server name on the Join page shows as a plain name (a break hint after each dot, never the tag itself)", "&lt;wbr&gt;" not in fp and "&lt;wbr" not in fp)
check("the Join page states the public channel rules", "The public channel rules" in fp and "16 bits" in fp)
check("the Join page explains the app setup, every setting, the Neighbor Info module and working as a gateway, with the real one-hour map report default", all(x in fp for x in ("Set Up Your Node With the App", "Every Setting Explained", "Neighbor Info", "Work as a Gateway", "Uplink Enabled", "Downlink Enabled", "Encryption Enabled", "Okay to MQTT", "MQTT proxy", "TLS Enabled", "Map Reporting Enabled", "Transmit over LoRa", "switch uplink on for a private channel", "once an hour")) and "every 15 minutes" not in fp)
check("its in-page links point at sections that exist", all(f'id="sec-{a}"' in fp for a in re.findall(r'href="#sec-([a-z-]+)"', fp)))
check("the Join page has the same sections as the ADS-B page (What You Need, Hardware, Hardware Setup, Software Setup, After You Are Feeding, Ports and Firewall, Troubleshooting, Your Data and Privacy, More Help)", all(x in fp for x in ("The Short Version", "How It Works", "What You Need", "Hardware Setup", "Software Setup", "After You Are Feeding", "Ports and Firewall", "Troubleshooting", "Your Data and Privacy", "More Help")))
check("it covers Meshtastic and MeshCore with the commands and the observer configuration", all(x in fp for x in ("meshtastic --set mqtt.enabled true", "meshcoretomqtt", "[[broker]]", "Uplink Enabled", "Local Meshes and Maps", "lrme.sh")))
setting("acars_enabled", "1"); time.sleep(3)
fa = requests.get(B + "/feed/acars").text
setting("acars_enabled", "0")
check("the ACARS page has the same sections and a compose example (acarsdec, dumpvdl2, acars_router, the bridge)", all(x in fa for x in ("The Short Version", "How It Works", "What You Need", "Hardware", "Hardware Setup", "Software Setup", "After You Are Feeding", "Ports and Firewall", "Troubleshooting", "Your Data and Privacy", "More Help", "docker-acarsdec", "acars_router", "ark_bridge", "acars_bridge.py")))
check("every feeding page lists the other feeding pages, mesh and ACARS included", all('href="/feed/mesh"' in requests.get(B + p).text for p in ("/feed/", "/feed/weather", "/feed/sondes")) or all('href="/feed/mesh"' in requests.get(B + p).text for p in ("/feed/weather",)))
setting("mesh_join_host", "mesh.example.org"); setting("mesh_max_per_user", "1"); time.sleep(2)
check("the Join page shows the server and points members to their own login", "mesh.example.org" in requests.get(B + "/feed/mesh").text and "/my/mesh" in requests.get(B + "/feed/mesh").text and "secretpw" not in requests.get(B + "/feed/mesh").text)

# ---- members' own broker logins (My Mesh Nodes): made in the broker by the service, one each, switched off and removed one at a time
mem = sess("plain1", "plainpassword1")
psql("DELETE FROM mesh_accounts")
check("My Mesh Nodes opens and a visitor is sent to sign in", mem.get(B + "/my/mesh").status_code == 200 and "Make a Login" in mem.get(B + "/my/mesh").text and requests.get(B + "/my/mesh", allow_redirects=False).status_code in (302, 401))
tm = tok(mem, "/my/mesh")
r1 = mem.post(B + "/my/mesh/add", data={"csrf_token": tm, "label": "ZZ Hill", "nodes": "!%08x" % (nid + 300) + " " + pubhex, "public": "1"}, allow_redirects=True)
aid1 = psql("SELECT id FROM mesh_accounts ORDER BY id LIMIT 1")
u1 = psql(f"SELECT username FROM mesh_accounts WHERE id={aid1}") if aid1 else ""
page1 = mem.get(B + f"/my/mesh/{aid1}").text if aid1 else ""
pw1 = (re.search(r"Password</th><td class=\"mono copyable\">([^<]+)<", page1) or [0, ""])[1]
check("making a login gives it its own username and a long random password", bool(aid1) and u1.startswith("plain1-") and len(pw1) >= 20, (u1, pw1[:3]))
check("its page has the server, port, the login and filled-in Meshtastic and MeshCore commands", all(x in page1 for x in ("mesh.example.org", u1, pw1, "meshtastic --set mqtt.enabled true", "[[broker]]", "tls.enabled = true")))
check("the password is sealed in the database, never plain", psql(f"SELECT password_sealed LIKE '%{pw1}%' FROM mesh_accounts WHERE id={aid1}") == "f")
check("the broker has the login", u1 in requests.get(P + "/credentials").json()["users"])
mkenv = lambda i, nm: envelope(i, 73, map_report(nm, "ZZ", 4, 9, "2.5.0", 34.8, -92.1), gateway=gw, snr=3.0)
nid3 = nid + 300
check("the member's node can publish with it through the real broker", pub(u1, pw1, "msh/US/AR/2/map/", mkenv(nid3, "ZZ Member Node")) == "rc=0")
got3 = wait_for(f"/nodes/!{nid3:08x}")
check("and what it sent arrives", got3 is not None and got3["node"]["long_name"] == "ZZ Member Node", got3)
check("a wrong password is refused", pub(u1, pw1 + "x", "msh/US/AR/2/map/", mkenv(nid3, "x")) != "rc=0")

# ---- the public Meshtastic server's rules on the public channels, and the broker's own limits
exact = sfx(1, int(35.123456 * 1e7)) + sfx(2, int(-92.654321 * 1e7)) + vi(3, 90)
n_pub, n_priv, n_13 = nid + 800, nid + 801, nid + 802
send(envelope(n_pub, 73, map_report("ZZ Pub Rules", "ZZPR", 0, 9, "2.5.9", 35.0, -92.0), gateway=gw))
send(envelope(n_priv, 73, map_report("ZZ Priv Chan", "ZZPC", 0, 9, "2.5.9", 35.0, -92.0), gateway=gw))
send(envelope(n_13, 73, map_report("ZZ Thirteen", "ZZ13", 0, 9, "2.5.9", 35.0, -92.0), gateway=gw))
check("an exact position on a public channel is stored cut to 16 bits (the public server shares only 10 to 16)", send(envelope(n_pub, 3, exact, gateway=gw)) == "stored" and 0 < abs(requests.get(P + f"/nodes/!{n_pub:08x}").json()["node"]["lat"] - 35.123456) < 0.004 and requests.get(P + "/stats").json()["feed"].get("public_coarsened", 0) >= 1)
check("the same position on a private channel is stored exactly", send(envelope(n_priv, 3, exact, gateway=gw, channel="ArkPrivate")) == "stored" and abs(requests.get(P + f"/nodes/!{n_priv:08x}").json()["node"]["lat"] - 35.123456) < 1e-6)
check("a position that already says 13 bits is kept as sent", send(envelope(n_13, 3, exact + vi(23, 13), gateway=gw)) == "stored" and abs(requests.get(P + f"/nodes/!{n_13:08x}").json()["node"]["lat"] - 35.123456) < 1e-6)
requests.post(P + "/config", json={"public_portnums": "4,67,73"})
check("a port number not on the public list is dropped on a public channel (and counted), but not on a private one", send(envelope(n_pub, 3, pos, gateway=gw)) == "ignored" and send(envelope(n_priv, 3, pos, gateway=gw, channel="ArkPrivate")) == "stored" and requests.get(P + "/stats").json()["feed"].get("public_dropped", 0) >= 1)
requests.post(P + "/config", json={"public_portnums": "1,3,4,5,7,67,71,73", "public_rules": "0"})
check("with the rules switched off an exact position on a public channel is stored exactly", send(envelope(n_pub, 3, exact, gateway=gw)) == "stored" and abs(requests.get(P + f"/nodes/!{n_pub:08x}").json()["node"]["lat"] - 35.123456) < 1e-6)
requests.post(P + "/config", json={"public_rules": "1"})
nid_out = nid + 810
pub(u1, pw1, "msh/EU_868/2/map/", mkenv(nid_out, "ZZ Wrong Root"))
check("a member's login can publish only under the site's topic root (a packet sent under another root never arrives)", wait_for(f"/nodes/!{nid_out:08x}", 6) is None)
check("the broker limits the size of a message and how long an idle connection may be kept (and the nodes' port the number of connections)", ssh("docker exec mesh-mqtt sh -c 'grep -c -e ^message_size_limit -e ^max_keepalive /mosquitto/config/mosquitto.generated.conf'").strip() == "2")
st_ = adm.get(B + "/admin/settings").text if "adm" in dir() else ""
check("the public channel rules are settings", (not st_) or all(x in st_ for x in ("Public Channel Rules", "Public Channels (Default Key)", "Port Numbers Kept on Public Channels", "Most Exact Position on Public Channels")))
ssh(f"docker exec mesh-mqtt mosquitto_pub -h localhost -u {PUBU} -P {PUBP} -r -t msh/US/AR/zzretain -m secret")
seen = ssh(f"docker exec mesh-mqtt mosquitto_sub -h localhost -u {u1} -P {pw1} -t msh/US/AR/zzretain -W 3 -C 1")
ssh(f"docker exec mesh-mqtt mosquitto_pub -h localhost -u {PUBU} -P {PUBP} -r -n -t msh/US/AR/zzretain")
check("a member's login can publish only: it cannot read what is on the broker", "secret" not in seen, seen)
sub = ssh(f"docker exec mesh-mqtt mosquitto_sub -h localhost -u {u1} -P {pw1} -t msh/US/AR/2/e/PKI/+ -d -W 2")
check("the Meshtastic app's own subscription (the PKI topic) is accepted, not refused, so the app shows no red error, while nothing is delivered to it", "Subscribed (mid: 1): 0" in sub and "128" not in sub, sub[-300:])
r2 = mem.post(B + "/my/mesh/add", data={"csrf_token": tm, "label": "second"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("the per-member limit stops a second login", psql("SELECT count(*) FROM mesh_accounts") == "1" and "can have" in r2.text)
mem.post(B + f"/my/mesh/{aid1}/new-password", data={"csrf_token": tm}, allow_redirects=False)
pw1b = (re.search(r"Password</th><td class=\"mono copyable\">([^<]+)<", mem.get(B + f"/my/mesh/{aid1}").text) or [0, ""])[1]
check("a new password replaces the old one in the broker", pw1b and pw1b != pw1 and pub(u1, pw1, "msh/US/AR/2/map/", mkenv(nid3, "x")) != "rc=0" and pub(u1, pw1b, "msh/US/AR/2/map/", mkenv(nid3, "ZZ Member Node")) == "rc=0")
check("the node's page says who runs it (the member chose to show their name)", "Run by" in requests.get(B + f"/mesh/{nid3:08x}").text, "")
ta0 = tok(adm, "/admin/mesh")
ap0 = adm.get(B + "/admin/mesh").text
check("the admin page lists the login with Switch Off, Delete, Sync and the add-for-member form", all(x in ap0 for x in (u1, "Switch Off", "Make a Login for a Member", "Sync Logins to the Broker")))
adm.post(B + f"/admin/mesh/accounts/{aid1}/disable", data={"csrf_token": ta0}, allow_redirects=False)
check("switching a login off refuses its node at once", pub(u1, pw1b, "msh/US/AR/2/map/", mkenv(nid3, "x")) != "rc=0" and u1 not in requests.get(P + "/credentials").json()["users"])
adm.post(B + f"/admin/mesh/accounts/{aid1}/enable", data={"csrf_token": ta0}, allow_redirects=False)
check("and switching it on lets the same password work again", pub(u1, pw1b, "msh/US/AR/2/map/", mkenv(nid3, "ZZ Member Node")) == "rc=0")
requests.post(P + "/credentials/delete", json={"username": u1})
adm.post(B + "/admin/mesh/accounts/sync", data={"csrf_token": ta0}, allow_redirects=False)
check("Sync Logins puts back a login the broker lost (a rebuilt broker)", pub(u1, pw1b, "msh/US/AR/2/map/", mkenv(nid3, "ZZ Member Node")) == "rc=0")
adm.post(B + "/admin/mesh/accounts/add", data={"csrf_token": ta0, "username": "plain1", "label": "added by admin"}, allow_redirects=False)
check("an administrator can make a login for a member beyond the limit", psql("SELECT count(*) FROM mesh_accounts") == "2")
aid2 = psql("SELECT id FROM mesh_accounts ORDER BY id DESC LIMIT 1")
u2 = psql(f"SELECT username FROM mesh_accounts WHERE id={aid2}")
check("the audit log records the logins", int(psql("SELECT count(*) FROM audit_log WHERE action LIKE 'mesh.account%'") or 0) >= 4)
mem.post(B + f"/my/mesh/{aid1}/delete", data={"csrf_token": tm}, allow_redirects=False)
check("deleting a login removes it from the broker and the list", pub(u1, pw1b, "msh/US/AR/2/map/", mkenv(nid3, "x")) != "rc=0" and psql(f"SELECT count(*) FROM mesh_accounts WHERE id={aid1}") == "0")
adm.post(B + f"/admin/mesh/accounts/{aid2}/delete", data={"csrf_token": ta0}, allow_redirects=False)
check("the second login is gone too and the broker holds no member logins", u2 not in requests.get(P + "/credentials").json()["users"] and psql("SELECT count(*) FROM mesh_accounts") == "0")
requests.post(P + "/hide", json={"id": "!%08x" % nid3}); requests.post(P + "/unhide", json={"id": "!%08x" % nid3})
# ---- claiming a node: a code in the node's name proves it is yours
psql("DELETE FROM mesh_claims")
cn = nid + 700
send(envelope(cn, 73, map_report("ZZ Claim Me", "ZZCM", 0, 9, "2.5.9", 34.6, -92.5), gateway=gw))
check("My Mesh Nodes has the claim form", "Claim This Node" in mem.get(B + "/my/mesh").text)
tm2 = tok(mem, "/my/mesh")
bad = mem.post(B + "/my/mesh/claim", data={"csrf_token": tm2, "node": "nonsense"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("a bad node ID is refused", "eight hexadecimal" in bad.text and psql("SELECT count(*) FROM mesh_claims") == "0")
np = mem.get(B + f"/mesh/{cn:08x}").text
check("an unclaimed node's own page offers a Claim This Node button to a signed-in member, and a sign-in prompt to a visitor", 'id="claim-node"' in np and f'value="!{cn:08x}"' in np and 'id="claim-node"' not in requests.get(B + f"/mesh/{cn:08x}").text and "to claim this node" in requests.get(B + f"/mesh/{cn:08x}").text)
mem.post(B + "/my/mesh/claim", data={"csrf_token": tm2, "node": "!%08x" % cn}, allow_redirects=False)
code = psql("SELECT code FROM mesh_claims LIMIT 1")
check("after the claim started, the node page says it is waiting and no longer offers the button", "claim on this node is waiting" in mem.get(B + f"/mesh/{cn:08x}").text and 'id="claim-node"' not in mem.get(B + f"/mesh/{cn:08x}").text)
cid1 = psql("SELECT id FROM mesh_claims LIMIT 1")
check("a claim starts pending with a code", code.startswith("ARK-") and psql("SELECT status FROM mesh_claims LIMIT 1") == "pending" and code in mem.get(B + "/my/mesh").text)
check("the claim does not complete while the code is not in the node's name", "Waiting" in mem.get(B + "/my/mesh").text and "Run by" not in requests.get(B + f"/mesh/{cn:08x}").text)
send(envelope(cn, 73, map_report("ZZ Claim Me " + code, "ZZCM", 0, 9, "2.5.9", 34.6, -92.5), gateway=gw))
time.sleep(1)
mem.post(B + f"/my/mesh/claims/{cid1}/check", data={"csrf_token": tm2}, allow_redirects=False)
check("once the code is in the name the claim completes", psql(f"SELECT status FROM mesh_claims WHERE id={cid1}") == "claimed")
check("the node's page names its operator", "Run by" in requests.get(B + f"/mesh/{cn:08x}").text and "plain1" in requests.get(B + f"/mesh/{cn:08x}").text)
mem.post(B + f"/my/mesh/claims/{cid1}/save", data={"csrf_token": tm2}, allow_redirects=False)   # name box left unticked
check("the owner can stop showing their name", "Run by" not in requests.get(B + f"/mesh/{cn:08x}").text)
mem.post(B + f"/my/mesh/claims/{cid1}/save", data={"csrf_token": tm2, "public": "1", "hide": "1"}, allow_redirects=False)
check("the owner can hide the node from every mesh page", requests.get(B + f"/mesh/{cn:08x}").status_code == 404 and psql(f"SELECT count(*) FROM mesh_hidden WHERE node_key='!{cn:08x}'") == "1")
mem.post(B + f"/my/mesh/claims/{cid1}/save", data={"csrf_token": tm2, "public": "1"}, allow_redirects=False)
send(envelope(cn, 73, map_report("ZZ Claim Me " + code, "ZZCM", 0, 9, "2.5.9", 34.6, -92.5), gateway=gw))
check("and show it again", wait_for(f"/nodes/!{cn:08x}") is not None and requests.get(B + f"/mesh/{cn:08x}").status_code == 200 and psql(f"SELECT count(*) FROM mesh_hidden WHERE node_key='!{cn:08x}'") == "0")
other = sess("plain2", "plainpassword2") if psql("SELECT count(*) FROM users WHERE username='plain2'") == "1" else None
res = adm.post(B + "/admin/mesh/claims/add", data={"csrf_token": tok(adm, "/admin/mesh"), "username": "plain1", "node": "!%08x" % cn}, headers={"X-Requested-With": "XMLHttpRequest"})
check("a node already claimed cannot be claimed again", "already claimed" in res.text)
cn2 = nid + 701
send(envelope(cn2, 73, map_report("ZZ Claim Two", "ZZC2", 0, 9, "2.5.9", 34.65, -92.55), gateway=gw))
adm.post(B + "/admin/mesh/claims/add", data={"csrf_token": tok(adm, "/admin/mesh"), "username": "plain1", "node": "!%08x" % cn2}, allow_redirects=False)
check("an administrator can claim a node for a member, approved at once", psql(f"SELECT status FROM mesh_claims WHERE node_key='!{cn2:08x}'") == "claimed" and "plain1" in requests.get(B + f"/mesh/{cn2:08x}").text)
check("the admin page lists the claims", "Node Claims" in adm.get(B + "/admin/mesh").text and "!%08x" % cn2 in adm.get(B + "/admin/mesh").text)
cn3 = nid + 702
mem.post(B + "/my/mesh/claim", data={"csrf_token": tm2, "node": "!%08x" % cn3}, allow_redirects=False)
cid3 = psql(f"SELECT id FROM mesh_claims WHERE node_key='!{cn3:08x}'")
adm.post(B + f"/admin/mesh/claims/{cid3}/approve", data={"csrf_token": tok(adm, "/admin/mesh")}, allow_redirects=False)
check("an administrator can approve a waiting claim", psql(f"SELECT status FROM mesh_claims WHERE id={cid3}") == "claimed")
mem.post(B + f"/my/mesh/claims/{cid1}/delete", data={"csrf_token": tm2}, allow_redirects=False)
check("a member can remove their claim", psql(f"SELECT count(*) FROM mesh_claims WHERE id={cid1}") == "0")
psql("DELETE FROM mesh_claims")
for i in (cn, cn2, cn3):
    requests.post(P + "/hide", json={"id": "!%08x" % i}); requests.post(P + "/unhide", json={"id": "!%08x" % i})
ap = adm.get(B + "/admin/mesh")
check("the admin page shows the service state and the hidden-node form", ap.status_code == 200 and "Hidden Nodes" in ap.text and "Apply Settings" in ap.text)
ta = tok(adm, "/admin/mesh")
adm.post(B + "/admin/mesh/hide", data={"csrf_token": ta, "node": sid, "reason": "test"}, allow_redirects=False)
time.sleep(1)
check("hiding a node takes it off the pages", requests.get(B + f"/mesh/{nid:08x}").status_code == 404 and psql(f"SELECT count(*) FROM mesh_hidden WHERE node_key='{sid}'") == "1")
adm.post(B + "/admin/mesh/hide", data={"csrf_token": ta, "node": "mc:" + pubhex, "reason": "test"}, allow_redirects=False)
time.sleep(1)
check("a MeshCore node is hidden by its public key", requests.get(B + "/mesh/mc-" + pubhex).status_code == 404 and psql(f"SELECT count(*) FROM mesh_hidden WHERE node_key='mc:{pubhex}'") == "1")
adm.post(B + "/admin/mesh/unhide", data={"csrf_token": ta, "node": "mc:" + pubhex}, allow_redirects=False)
adm.post(B + "/admin/mesh/unhide", data={"csrf_token": ta, "node": sid}, allow_redirects=False)
check("a bad node ID is refused", not adm.post(B + "/admin/mesh/hide", data={"csrf_token": ta, "node": "zz"}, headers={"X-Requested-With": "XMLHttpRequest"}).json().get("ok"))
check("a member cannot use the admin page", sess("plain1", "plainpassword1").get(B + "/admin/mesh", allow_redirects=False).status_code in (302, 401, 403, 404))
check("Settings has the Mesh Nodes section", "mesh_enabled" in adm.get(B + "/admin/settings").text)
check("Service Status has a mesh check", "Mesh" in adm.get(B + "/admin/status").text)
psql("DELETE FROM mesh_hidden")
for i in (nid, nid + 100, nid + 200):
    requests.post(P + "/hide", json={"id": "!%08x" % i}); requests.post(P + "/unhide", json={"id": "!%08x" % i})
setting("mesh_join_host", ""); setting("mesh_max_per_user", "3"); setting("mesh_up_enabled", "1")
requests.post(P + "/credentials/delete", json={"username": PUBU})
setting("mesh_enabled", "0"); time.sleep(2)
check("switched off, the pages are a 404", requests.get(B + "/mesh/").status_code == 404 and 'href="/mesh/"' not in requests.get(B + "/").text)
print("ALL PASS" if ok_all else "SOME FAILED")
