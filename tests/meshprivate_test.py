"""The private mesh feed (request 205): a packet on the private root is kept in the private tables for its community (exact position, every port, no area limit) and goes on to the public path only when the
firmware would have uplinked it (decoded, OK to MQTT bit set or sent by the gateway itself, on a public channel). Needs the mesh service on the test box (sh tests/deploy_mesh.sh)."""
import base64, os, struct, time, requests
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18482"
try:
    requests.get(P + "/health", timeout=10).json()
except Exception as e:
    print("SKIP meshprivate_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)
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
def envelope(sender, port, payload, gateway, channel="LongFast", bitfield=None, encrypted=False):
    d = vi(1, port) + ld(2, payload) + (vi(9, bitfield) if bitfield is not None else b"")
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF) + (ld(5, b"\x01\x02\x03\x04") if encrypted else ld(4, d)) + fx(6, 4242)
    return ld(1, pk) + ld(2, channel) + ld(3, gateway)
def nodeinfo(sid, long, short): return ld(1, sid) + ld(2, long) + ld(3, short) + vi(5, 9) + vi(7, 0)
def position(lat, lon): return sfx(1, int(lat * 1e7)) + sfx(2, int(lon * 1e7)) + vi(3, 90)
def priv(com, env, channel="LongFast", gw="!feedbeef"):
    return requests.post(P + "/ingest_private", json={"topic": f"mshp/{com}/2/e/{channel}/{gw}", "envelope_b64": base64.b64encode(env).decode()}, timeout=20).json()["result"]
def pnodes(com=""):
    return {n["id"] if "id" in n else n.get("node_id"): n for n in requests.get(P + "/private/nodes", params={"community": com} if com else {}).json()["nodes"]}
def pub_status(nid): return requests.get(P + "/nodes/!%08x" % nid).status_code

requests.post(P + "/config", json={"private_enabled": "1"}, timeout=20)
import subprocess as _sp
def _clean(): _sp.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec mesh-db psql -U mesh -d mesh -tA -c \"DELETE FROM pnodes WHERE long_name LIKE 'ZZ %'; DELETE FROM nodes WHERE long_name LIKE 'ZZ %'\""], capture_output=True, timeout=60)
_clean()
t = int(time.time()) & 0xFFFF
com, other = "zzpriv" + str(t % 100), "zzother" + str(t % 100)
gwid = 0xCAFE0000 + t
gw = "!%08x" % gwid
base = 0xB1000000 + t * 16
A, B, C, D, E, F = (base + i for i in range(6))
def sid(n): return "!%08x" % n

# 1. OK to MQTT set: private and public
r = priv(com, envelope(A, 4, nodeinfo(sid(A), "ZZ Consent", "ZZCN"), gw, bitfield=1), gw=gw)
check("a decoded packet with the OK to MQTT bit set is kept privately and goes on to the public path", r == "public", r)
check("it is on the public list too", pub_status(A) == 200)
# 2. bit clear: private only
r = priv(com, envelope(B, 4, nodeinfo(sid(B), "ZZ NoConsent", "ZZNC"), gw, bitfield=0), gw=gw)
check("a packet with the bit clear stays private", r == "private", r)
check("it is not on the public list", pub_status(B) == 404)
r = priv(com, envelope(C, 4, nodeinfo(sid(C), "ZZ NoBits", "ZZNB"), gw), gw=gw)
check("a packet with no bit field at all stays private", r == "private" and pub_status(C) == 404, r)
# 3. the gateway's own packet
r = priv(com, envelope(gwid, 4, nodeinfo(gw, "ZZ Gateway", "ZZGW"), gw), gw=gw)
check("a packet the gateway sent itself goes on, like the firmware", r == "public" and pub_status(gwid) == 200, r)
# 4. a channel that is not public
r = priv(com, envelope(D, 4, nodeinfo(sid(D), "ZZ Secret", "ZZSC"), gw, channel="ZZSecret", bitfield=1), channel="ZZSecret", gw=gw)
check("a packet on a channel the public server does not list stays private even with the bit set", r == "private" and pub_status(D) == 404, r)
# 5. exact position stays private, the public copy is cut
r = priv(com, envelope(A, 3, position(35.123456, -92.654321), gw, bitfield=1), gw=gw)
pn = pnodes(com)
a = pn.get(A) or pn.get(sid(A)) or next((n for n in pn.values() if "ZZ Consent" == n.get("long_name")), None)
check("the private copy keeps the exact position", a is not None and abs(a["lat"] - 35.123456) < 1e-6 and abs(a["lon"] + 92.654321) < 1e-6, a)
pubn = requests.get(P + "/nodes/" + sid(A)).json()["node"]
check("the public copy is cut to the public rules", pubn["lat"] is not None and 0 < abs(pubn["lat"] - 35.123456) < 0.05, pubn.get("lat"))
# 6. outside the area is kept privately and not publicly
r = priv(com, envelope(E, 3, position(51.5, -0.12), gw, bitfield=1), gw=gw)
r2 = priv(com, envelope(E, 4, nodeinfo(sid(E), "ZZ Far", "ZZFR"), gw, bitfield=1), gw=gw)
r3 = priv(com, envelope(E, 3, position(51.5, -0.12), gw, bitfield=1), gw=gw)
fe = [n for n in pnodes(com).values() if n.get("long_name") == "ZZ Far"]
check("a node outside the public area is kept privately", len(fe) == 1 and abs(fe[0]["lat"] - 51.5) < 1e-6, fe)
check("and its position never reaches the public list", pub_status(E) == 404 or requests.get(P + "/nodes/" + sid(E)).json()["node"]["lat"] is None)
# 7. encrypted, junk, wrong topic
check("an encrypted packet is not read or kept", priv(com, envelope(F, 4, b"", gw, encrypted=True), gw=gw) == "encrypted")
check("junk is ignored", priv(com, b"\x00\x01garbage") in ("ignored", "encrypted"))
rr = requests.post(P + "/ingest_private", json={"topic": "msh/US/AR/" + com + "/2/e/LongFast/" + gw, "envelope_b64": base64.b64encode(envelope(F, 4, nodeinfo(sid(F), "ZZ Wrong", "ZZWR"), gw, bitfield=1)).decode()}).json()["result"]
check("a packet that is not on the private root is not taken by the private door", rr == "ignored", rr)
# 8. communities apart
check("another community sees none of it", not any(n.get("long_name", "").startswith("ZZ ") for n in pnodes(other).values()))
check("the community sees its nodes", sum(1 for n in pnodes(com).values() if n.get("long_name", "").startswith("ZZ ")) >= 4)
allp = pnodes()
check("the all-communities list has them too", sum(1 for n in allp.values() if n.get("long_name", "").startswith("ZZ ") and n.get("community") == com) >= 4)
# 9. hidden stays hidden
requests.post(P + "/hide", json={"id": sid(B)})
check("a node hidden by ID is not taken on the private feed either", priv(com, envelope(B, 4, nodeinfo(sid(B), "ZZ NoConsent", "ZZNC"), gw, bitfield=1), gw=gw) == "hidden")
requests.post(P + "/unhide", json={"id": sid(B)})
st = requests.get(P + "/stats").json()
check("the private counts are in the statistics", isinstance(st.get("feed", {}), dict))

# ---- through the real broker (stage 2): the private login, its listener and its limits
import subprocess
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=120).stdout
def pubfile(user, pw, topic, blob, port=1883):
    return ssh(f"echo {base64.b64encode(blob).decode()} | base64 -d > /tmp/pv_pkt.bin; {DK} cp /tmp/pv_pkt.bin mesh-mqtt:/tmp/pv_pkt.bin; {DK} exec mesh-mqtt mosquitto_pub -h localhost -p {port} -u {user} -P {pw} -t '{topic}' -f /tmp/pv_pkt.bin; echo rc=$?").strip()
def wait_node(com_, long, tries=12):
    for _ in range(tries):
        time.sleep(1)
        if any(n.get("long_name") == long for n in pnodes(com_).values()):
            return True
    return False
PW = "pvTestPassword99"
com2 = "zzbrk" + str(t % 100)
other2 = "zzbrk2" + str(t % 100)
r1 = requests.post(P + "/credentials/set", json={"username": "pv-gw", "password": PW, "community": com2, "private": True}, timeout=30).json()
r2 = requests.post(P + "/credentials/set", json={"username": "pv-gw2", "password": PW, "community": other2, "private": True}, timeout=30).json()
check("the service makes a private login for a community", r1.get("ok") is True and r2.get("ok") is True, (r1, r2))
G, H, I = base + 0x100, base + 0x101, base + 0x102
pubfile("pv-gw", PW, f"mshp/{com2}/2/e/LongFast/!feedbeef", envelope(G, 4, nodeinfo(sid(G), "ZZ Broker Private", "ZZBP"), "!feedbeef", bitfield=0))
check("a packet from the private login reaches the private feed through the broker (internal listener)", wait_node(com2, "ZZ Broker Private"))
pubfile("pv-gw", PW, f"mshp/{com2}/2/e/LongFast/!feedbeef", envelope(H, 4, nodeinfo(sid(H), "ZZ Broker Port", "ZZBO"), "!feedbeef", bitfield=0), port=1884)
check("and through the private listener (port 1884)", wait_node(com2, "ZZ Broker Port"))
check("without the OK to MQTT bit it did not reach the public list", pub_status(G) == 404 and pub_status(H) == 404)
pubfile("pv-gw", PW, f"mshp/{com2}/2/e/LongFast/!feedbeef", envelope(I, 4, nodeinfo(sid(I), "ZZ Broker Ok", "ZZBK"), "!feedbeef", bitfield=1))
wait_node(com2, "ZZ Broker Ok")
check("with the bit set it also shows on the public list", pub_status(I) == 200)
J, K = base + 0x103, base + 0x104
pubfile("pv-gw", PW, f"mshp/{other2}/2/e/LongFast/!feedbeef", envelope(J, 4, nodeinfo(sid(J), "ZZ Wrong Community", "ZZWC"), "!feedbeef", bitfield=1))
pubfile("pv-gw", PW, f"msh/US/AR/{com2}/2/e/LongFast/!feedbeef", envelope(K, 4, nodeinfo(sid(K), "ZZ Wrong Root", "ZZWR"), "!feedbeef", bitfield=1))
time.sleep(4)
check("the private login cannot publish under another community's private topic or under the public root", not any(n.get("long_name") in ("ZZ Wrong Community", "ZZ Wrong Root") for n in pnodes().values()) and pub_status(J) == 404 and pub_status(K) == 404)
got = ssh(f"{DK} exec mesh-mqtt sh -c \"(mosquitto_sub -h localhost -p 1884 -u pv-gw -P {PW} -t 'mshp/#' -W 6 &) ; sleep 1; mosquitto_pub -h localhost -u pv-gw2 -P {PW} -t 'mshp/{other2}/2/e/LongFast/!feedbeef' -m hello; sleep 6\"").strip()
check("a private login is sent nothing", got == "", got)
_clean()
print("ALL PASS" if ok_all else "SOME FAILED")
raise SystemExit(0 if ok_all else 1)
