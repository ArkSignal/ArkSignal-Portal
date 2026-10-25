"""Fill the test box's mesh service with a believable set of Arkansas nodes (routers, clients, trackers, a couple with low batteries) through its /ingest address, so the Mesh pages, map, health page, claims and owner alerts
have something to show. Safe to run again: the nodes are the same each time.  Usage: python tests/seed_mesh_demo.py   (needs the mesh service on the test box, sh tests/deploy_mesh.sh)"""
import base64, os, struct, requests

P = f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18482"
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
def envelope(sender, port, payload, gateway, snr=None, rssi=None):
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, vi(1, port) + ld(2, payload)) + fx(6, 4242)
    if snr is not None: pk += flt(8, snr)
    if rssi is not None: pk += vi(12, rssi)
    return ld(1, pk) + ld(2, "LongFast") + ld(3, gateway)
def map_report(long, short, role, hw, fw, lat, lon, alt=120, prec=16, online=3):
    return ld(1, long) + ld(2, short) + vi(3, role) + vi(4, hw) + ld(5, fw) + vi(6, 1) + vi(7, 0) + vi(8, 1) + sfx(9, int(lat * 1e7)) + sfx(10, int(lon * 1e7)) + vi(11, alt) + vi(12, prec) + vi(13, online) + vi(14, 1)
def send(env): return requests.post(P + "/ingest", json={"envelope_b64": base64.b64encode(env).decode()}, timeout=20).json()["result"]

# (id, long name, short, role (0 client, 2 router, 5 tracker, 4 repeater), hardware, lat, lon, battery or None, channel use)
NODES = [
    (0xA1B20001, "Little Rock Hilltop Router", "LRHT", 2, 9, 34.7465, -92.2896, None, 6.0),
    (0xA1B20002, "Conway Water Tower Router", "CNWT", 2, 9, 35.0887, -92.4421, None, 4.5),
    (0xA1B20003, "Hot Springs Mountain Router", "HSMR", 2, 4, 34.5217, -93.0552, None, 9.0),
    (0xA1B20004, "Fayetteville Ridge Router", "FAYR", 2, 9, 36.0626, -94.1574, None, 12.5),
    (0xA1B20005, "Sheridan Solar Node", "SHSO", 0, 9, 34.3076, -92.4005, 14, 3.0),
    (0xA1B20006, "Pine Bluff Client", "PBLF", 0, 4, 34.2284, -92.0032, 76, 2.0),
    (0xA1B20007, "Bryant Handheld", "BRYH", 0, 110, 34.5959, -92.4890, 55, 1.5),
    (0xA1B20008, "Benton Weather Sensor", "BNWX", 5, 9, 34.5645, -92.5868, 88, 1.0),
    (0xA1B20009, "Jonesboro Router", "JBRO", 2, 9, 35.8423, -90.7043, None, 5.0),
    (0xA1B2000A, "Fort Smith Router", "FSMR", 2, 4, 35.3859, -94.3985, None, 7.5),
]
GATEWAYS = {"!feed0001": 1.0, "!feed0002": -1.0}
out = {}
for i, (nid, long, short, role, hw, lat, lon, bat, cu) in enumerate(NODES):
    gw = "!feed0001" if lon > -93 else "!feed0002"
    r = send(envelope(nid, 73, map_report(long, short, role, hw, "2.5.9.abc", lat, lon), gw, snr=5.0 + i, rssi=-100 + i * 3))
    if bat is not None or cu:
        tel = ld(2, (vi(1, bat) if bat is not None else b"") + flt(3, cu))
        send(envelope(nid, 67, tel, gw))
    out[long] = r
print(out)
print("nodes in the service:", requests.get(P + "/stats").json().get("nodes"))
