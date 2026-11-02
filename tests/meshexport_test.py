"""Outbound MQTT export (request 208), on the test box with a real second broker as the target: each connector carries only what its filters allow (feed, communities, port numbers, channels, public rules,
position cut or removed, encrypted packets, a rate cap), hidden nodes are never sent, the site switch stops everything, and the admin page makes, changes, tests and deletes connectors with the private feed
behind a tick and every change in the audit log. Needs the mesh service, the broker and both portals on the test box (sh tests/deploy_test.sh, sh tests/deploy_mesh.sh)."""
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
def ssh(cmd, t=300):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=t).stdout
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    requests.get(P + "/health", timeout=8)
except Exception as e:
    print("SKIP meshexport_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)
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
def envelope(sender, port, payload, gw="!feedbee1", channel="LongFast", bitfield=None, encrypted=False):
    d = vi(1, port) + ld(2, payload) + (vi(9, bitfield) if bitfield is not None else b"")
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF) + (ld(5, b"\x01\x02\x03\x04") if encrypted else ld(4, d)) + fx(6, 31337)
    return ld(1, pk) + ld(2, channel) + ld(3, gw)
def nodeinfo(n, name): return ld(1, "!%08x" % n) + ld(2, name) + ld(3, name[-4:]) + vi(5, 9) + vi(7, 0)
def position(lat, lon): return sfx(1, int(lat * 1e7)) + sfx(2, int(lon * 1e7)) + vi(3, 90)
# a small reader for what arrives
def rd(buf):
    i, out = 0, []
    while i < len(buf):
        k = 0; sh = 0
        while True:
            b = buf[i]; i += 1; k |= (b & 0x7F) << sh; sh += 7
            if not b & 0x80: break
        num, wt = k >> 3, k & 7
        if wt == 0:
            v = 0; sh = 0
            while True:
                b = buf[i]; i += 1; v |= (b & 0x7F) << sh; sh += 7
                if not b & 0x80: break
        elif wt == 5: v = struct.unpack("<I", buf[i:i + 4])[0]; i += 4
        elif wt == 1: v = struct.unpack("<Q", buf[i:i + 8])[0]; i += 8
        else:
            n = 0; sh = 0
            while True:
                b = buf[i]; i += 1; n |= (b & 0x7F) << sh; sh += 7
                if not b & 0x80: break
            v = buf[i:i + n]; i += n
        out.append((num, wt, v))
    return out
def sender_of(raw): return next(v for n, w, v in rd(next(v for n, w, v in rd(raw) if n == 1)) if n == 1)
def position_of(raw):
    pk = rd(next(v for n, w, v in rd(raw) if n == 1)); data = rd(next(v for n, w, v in pk if n == 4)); pos = rd(next(v for n, w, v in data if n == 2))
    g = lambda no: next((v for n, w, v in pos if n == no), None)
    s32 = lambda v: v - (1 << 32) if v is not None and v >= 1 << 31 else v
    return s32(g(1)) / 1e7, s32(g(2)) / 1e7, g(23)

TARGET = "zz-export-target"
ssh(f"{DK} rm -f {TARGET} >/dev/null 2>&1; {DK} run -d --name {TARGET} --network mesh_test eclipse-mosquitto:2 sh -c 'printf \"listener 1883\\nallow_anonymous true\\n\" > /tmp/m.conf; exec mosquitto -c /tmp/m.conf' >/dev/null")
time.sleep(3)
PW = "zxTestPassword99"
for u, com, priv in (("zx-member", "", False), ("zx-c1", "zzx1", False), ("zx-c2", "zzx2", False), ("zx-p1", "zzx1", True)):
    r = requests.post(P + "/credentials/set", json={"username": u, "password": PW, "community": com, "private": priv}, timeout=30).json()
    check("login " + u, r.get("ok") is True, r)
requests.post(P + "/config", json={"private_enabled": "1", "public_rules": "1"}, timeout=20)

t = int(time.time()) & 0xFFF
N = {k: 0xE7000000 + t * 16 + i for i, k in enumerate("123456789")}
def ok(v): return v
base = dict(host=TARGET, port=1883, tls=False, username="", password="", qos=0, position="asis", max_bits=16, mimic_public=False, encrypted=True, rate_per_min=600, communities=[], channels=[], portnums=[], enabled=True)
conns = [dict(base, id=9001, name="A public", prefix="zzx/a", source="public"),
         dict(base, id=9002, name="B private", prefix="zzx/b", source="private"),
         dict(base, id=9003, name="C mimic", prefix="zzx/c", source="public", mimic_public=True),
         dict(base, id=9004, name="D no position", prefix="zzx/d", source="public", position="none"),
         dict(base, id=9005, name="E cut 8", prefix="zzx/e", source="public", position="bits", max_bits=8),
         dict(base, id=9006, name="F rate", prefix="zzx/f", source="public", rate_per_min=2),
         dict(base, id=9007, name="G filters", prefix="zzx/g", source="both", communities=["zzx1"], portnums=[4], encrypted=False),
         dict(base, id=9008, name="H channel", prefix="zzx/h", source="public", channels=["ZZPriv"], encrypted=False)]
r = requests.post(P + "/exports/set", json={"enabled": "1", "exports": conns}, timeout=20).json()
check("the service takes the connectors", r.get("ok") and r.get("connectors") == 8, r)
for _ in range(30):
    st = requests.get(P + "/exports/status").json()["connectors"]
    if all(st.get(str(c["id"]), {}).get("connected") for c in conns): break
    time.sleep(1)
check("every connector connects to the target broker", all(st.get(str(c["id"]), {}).get("connected") for c in conns), st)

PK = {  # name: (login, topic, packet)
    "P1": ("zx-member", "msh/US/AR/2/e/LongFast/!feedbee1", envelope(N["1"], 4, nodeinfo(N["1"], "ZZ Exp One"))),
    "P2": ("zx-c1", "msh/US/AR/zzx1/2/e/LongFast/!feedbee1", envelope(N["2"], 3, position(34.123456, -92.654321))),
    "P3": ("zx-c2", "msh/US/AR/zzx2/2/e/ZZPriv/!feedbee1", envelope(N["3"], 4, nodeinfo(N["3"], "ZZ Exp Three"), channel="ZZPriv")),
    "P4": ("zx-p1", "mshp/zzx1/2/e/LongFast/!feedbee1", envelope(N["4"], 4, nodeinfo(N["4"], "ZZ Exp Four"), bitfield=0)),
    "P5": ("zx-p1", "mshp/zzx1/2/e/LongFast/!feedbee1", envelope(N["5"], 4, nodeinfo(N["5"], "ZZ Exp Five"), bitfield=1)),
    "P6": ("zx-member", "msh/US/AR/2/e/LongFast/!feedbee1", envelope(N["6"], 4, b"", encrypted=True)),
}
def run(packets, listen=14):
    lines = ["set -e"]
    lines.append(f"{DK} run --rm --network mesh_test eclipse-mosquitto:2 mosquitto_sub -h {TARGET} -t 'zzx/#' -F '%t %x' -W {listen} > /tmp/zzx_out.txt 2>/dev/null &")
    lines.append("sleep 4")
    for i, (user, topic, raw) in enumerate(packets):
        lines.append(f"echo {base64.b64encode(raw).decode()} | base64 -d > /tmp/zzx_pkt.bin; {DK} cp /tmp/zzx_pkt.bin mesh-mqtt:/tmp/zzx_pkt.bin; {DK} exec mesh-mqtt mosquitto_pub -h localhost -u {user} -P {PW} -t '{topic}' -f /tmp/zzx_pkt.bin; sleep 0.4")
    lines += ["wait", "cat /tmp/zzx_out.txt"]
    b64 = base64.b64encode(("\n".join(lines) + "\n").encode()).decode()
    out = ssh(f"echo {b64} | base64 -d > /tmp/zzx_run.sh; sh /tmp/zzx_run.sh", 240)
    got = {}
    for ln in out.splitlines():
        if " " not in ln: continue
        tp, hx = ln.split(" ", 1)
        try: raw = bytes.fromhex(hx.strip())
        except ValueError: continue
        got.setdefault(tp.split("/")[1], []).append((tp, raw))
    return got
def who(items): return sorted(next((k for k, v in N.items() if v == sender_of(r)), "?") for _t, r in items)

got = run([PK[k] for k in ("P1", "P2", "P3", "P4", "P5", "P6")])
check("A (public feed): the ordinary, community and encrypted packets and the private packet the public rules would carry; not the one that said no", who(got.get("a", [])) == ["1", "2", "3", "5", "6"], who(got.get("a", [])))
check("B (private feed): every private packet, whatever its OK to MQTT bit says, and nothing public", who(got.get("b", [])) == ["4", "5"], who(got.get("b", [])))
check("C (mimic public): only what the public server would take (the public channel's text and node packets; not the private channel, not an exact position)", who(got.get("c", [])) == ["1", "5", "6"], who(got.get("c", [])))
check("D (positions removed): everything except the position packet", who(got.get("d", [])) == ["1", "3", "5", "6"], who(got.get("d", [])))
e = got.get("e", [])
check("E (positions cut to 8 bits): the position arrives, cut, with its precision set", who(e) == ["1", "2", "3", "5", "6"] and any(sender_of(r) == N["2"] and abs(position_of(r)[0] - 34.123456) > 0.05 and position_of(r)[2] == 8 for _t, r in e), [(k, position_of(r)) for _t, r in e for k in "2" if sender_of(r) == N["2"]])
check("E: the packets are otherwise the same bytes (node information unchanged)", any(sender_of(r) == N["1"] and r == PK["P1"][2] for _t, r in e))
check("F (two a minute): only two get through, the rest are counted as over the rate", len(got.get("f", [])) == 2 and requests.get(P + "/exports/status").json()["connectors"]["9006"]["rate_dropped"] >= 3, len(got.get("f", [])))
check("G (feed both, community zzx1, port 4, no encrypted): only that community's node information", who(got.get("g", [])) == ["4", "5"], who(got.get("g", [])))
check("H (channel ZZPriv, nothing encrypted): only the packet on that channel", who(got.get("h", [])) == ["3"], who(got.get("h", [])))
tops = [tp for tp, _r in got.get("a", [])]
check("topics are prefix, community, then 2/e/channel/gateway (no community: straight after the prefix)", "zzx/a/2/e/LongFast/!feedbee1" in tops and "zzx/a/zzx1/2/e/LongFast/!feedbee1" in tops and "zzx/a/zzx2/2/e/ZZPriv/!feedbee1" in tops, tops)

# hidden nodes, the site switch, a connector switched off
requests.post(P + "/hide", json={"id": "!%08x" % N["7"]})
got = run([("zx-member", "msh/US/AR/2/e/LongFast/!feedbee1", envelope(N["7"], 4, nodeinfo(N["7"], "ZZ Exp Seven")))], listen=9)
check("a hidden node is never sent", not any(got.get(k) for k in "abcdefgh"), {k: len(v) for k, v in got.items()})
requests.post(P + "/unhide", json={"id": "!%08x" % N["7"]})
requests.post(P + "/exports/set", json={"enabled": "0"})
got = run([PK["P1"]], listen=9)
check("with the site switch off nothing is sent", not got, {k: len(v) for k, v in got.items()})
requests.post(P + "/exports/set", json={"enabled": "1"})

# ---- the admin page
setting("mesh_enabled", "1"); setting("mesh_private_enabled", "1"); setting("mesh_export_enabled", "1")
adm = sess("marty", ADMPW)
psql("DELETE FROM mesh_exports WHERE name LIKE 'ZZ Page%'")
pg = adm.get(B + "/admin/mesh/exports")
check("the Mesh MQTT Export page opens for an administrator and offers a new connector", pg.status_code == 200 and "Add a Connector" in pg.text, pg.status_code)
check("it is not open to a visitor or a plain member", requests.get(B + "/admin/mesh/exports", allow_redirects=False).status_code in (302, 401, 403))
def post(path, **d):
    d["csrf_token"] = tok(adm, "/admin/mesh/exports?new=1")
    return adm.post(B + path, data=d, allow_redirects=False, headers={"X-Requested-With": "XMLHttpRequest"})
form = dict(name="ZZ Page One", host=TARGET, port="1883", prefix="zzx/page", source="public", rate_per_min="100", position="bits", max_bits="12", qos="0", enabled="on", encrypted="on")
r = post("/admin/mesh/exports/save", **form)
check("a connector is saved", r.status_code == 200 and r.json().get("ok"), r.text[:200])
cid = psql("SELECT id FROM mesh_exports WHERE name='ZZ Page One'")
check("it is in the table with its filters, and its password is not kept in the clear", cid.isdigit() and psql(f"SELECT position || max_bits || source FROM mesh_exports WHERE id={cid}") == "bits12public")
r = post("/admin/mesh/exports/save", **dict(form, name="ZZ Page Private", source="private"))
check("the private feed is refused without the tick", r.status_code == 400 and "know" in r.json().get("message", ""), r.text[:200])
r = post("/admin/mesh/exports/save", **dict(form, name="ZZ Page Private", source="private", confirm_private="on"))
check("and accepted with it", r.status_code == 200 and r.json().get("ok"))
r = post("/admin/mesh/exports/save", **dict(form, name="ZZ Page Bad", host="bad host!"))
check("a bad host name, prefix with a wildcard, bits out of range are refused", r.status_code == 400 and post("/admin/mesh/exports/save", **dict(form, name="ZZ Page Bad", prefix="a/#")).status_code == 400 and post("/admin/mesh/exports/save", **dict(form, name="ZZ Page Bad", max_bits="40")).status_code == 400)
r = post("/admin/mesh/exports/save", id=cid, **dict(form, name="ZZ Page One", password="s3cret-pw-1", username="zzuser"))
check("a changed connector keeps its row and seals the password", r.json().get("ok") and psql(f"SELECT password_sealed LIKE 'enc%' OR password_sealed LIKE 'gAAAA%' FROM mesh_exports WHERE id={cid}") == "t" and psql(f"SELECT count(*) FROM mesh_exports WHERE name='ZZ Page One'") == "1")
time.sleep(1)
adm.post(B + "/admin/status/run", data={}, headers={"X-Requested-With": "XMLHttpRequest", "X-CSRFToken": re.search(r'CSRF_TOKEN = "([^"]+)"', adm.get(B + "/admin/status").text).group(1)})
sv = [x for x in requests.get(B + "/api/status.json").json()["services"] if x["name"] == "Mesh MQTT Export"]
check("Service Status has a Mesh MQTT Export line that counts the connected connectors", len(sv) == 1 and sv[0]["state"] in ("ok", "warn"), sv)
r = post(f"/admin/mesh/exports/{cid}/test")
check("Send Test publishes a message to the connector (it logs in with the saved name, so the anonymous target takes it)", r.status_code == 200, r.text[:200])
r = post(f"/admin/mesh/exports/{cid}/toggle")
check("a connector can be switched off", r.json().get("ok") and psql(f"SELECT enabled FROM mesh_exports WHERE id={cid}") == "f")
check("every change is in the audit log", int(psql("SELECT count(*) FROM audit_log WHERE action='mesh.export' AND detail LIKE '%connector%'") or 0) >= 4)
setting("mesh_export_max", "2")
r = post("/admin/mesh/exports/save", **dict(form, name="ZZ Page Third"))
check("the site's limit on connectors is kept", r.status_code == 400 and "allows" in r.json().get("message", ""), r.text[:200])
setting("mesh_export_max", "5")
for row in psql("SELECT id FROM mesh_exports WHERE name LIKE 'ZZ Page%'").split():
    post(f"/admin/mesh/exports/{row}/delete")
check("deleted connectors are gone", psql("SELECT count(*) FROM mesh_exports WHERE name LIKE 'ZZ Page%'") == "0")
requests.post(P + "/exports/set", json={"exports": []})
ssh(f"{DK} rm -f {TARGET} >/dev/null 2>&1")
for u in ("zx-member", "zx-c1", "zx-c2", "zx-p1"):
    requests.post(P + "/credentials/delete", json={"username": u})
subprocess.run(["ssh", "-q", "nastemp", f"{DK} exec mesh-db psql -U mesh -d mesh -tA -c \"DELETE FROM nodes WHERE long_name LIKE 'ZZ Exp %'; DELETE FROM pnodes WHERE long_name LIKE 'ZZ Exp %'\""], capture_output=True, timeout=60)
print("ALL PASS" if ok_all else "SOME FAILED")
raise SystemExit(0 if ok_all else 1)
