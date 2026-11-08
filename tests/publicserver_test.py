"""Forwarding to the public Meshtastic server (request 218), for real: with the site switch on, what a node sends to the public side of our broker is published to the other broker, whatever community topic
it used; a private channel is held back and counted with its reason; the status page and the service's status say what was sent and why not; with the switch off nothing goes.
The "public server" here is the test broker itself under another root (fwdtest/AR), so nothing leaves the test box. Needs the mesh service and both portals on the test box."""
import base64, os, re, struct, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
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
    print("SKIP publicserver_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)

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
def envelope(sender, port, payload, gateway, channel="LongFast"):
    return ld(1, fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, vi(1, port) + ld(2, payload) + vi(9, 1)) + fx(6, 4243)) + ld(2, channel) + ld(3, gateway)
def ni(sid, long): return ld(1, sid) + ld(2, long) + ld(3, long[-4:]) + vi(5, 9) + vi(7, 0)

adm = sess("marty", PW)
ADM = ssh(f"{DK} exec mesh-mqtt cat /mosquitto/config/admin.pass").strip()
def ctrl(*args): return ssh(f"{DK} exec mesh-mqtt mosquitto_ctrl -h localhost -p 1883 -u mesh-admin -P {ADM} dynsec " + " ".join(args))
PUB = ("fwdpub", "fwdpubpassword1")
def make_pub():
    """A temporary login that may publish and read anywhere (made after each Apply, which removes logins the portal does not know)."""
    ctrl("deleteClient fwdpub"); ctrl("deleteRole fwdpubrole")
    ctrl("createRole fwdpubrole"); ctrl("addRoleACL fwdpubrole publishClientSend '#' allow"); ctrl("addRoleACL fwdpubrole subscribePattern '#' allow"); ctrl("addRoleACL fwdpubrole publishClientReceive '#' allow")
    ctrl(f"createClient fwdpub -p {PUB[1]}"); ctrl("addClientRole fwdpub fwdpubrole")

def apply():
    act(adm, "/admin/mesh/apply", "/admin/mesh")
    time.sleep(6)
def run_script(script):
    subprocess.run(["ssh", "-q", "nastemp", "cat > /tmp/ps.sh"], input=script.encode(), timeout=60)
    return ssh(f"{DK} cp /tmp/ps.sh mesh-mqtt:/tmp/ps.sh; {DK} exec mesh-mqtt sh /tmp/ps.sh 2>&1", 90)
def publish_and_watch(topic, env, watch="fwdtest/#", wait=8):
    b64 = base64.b64encode(env).decode()
    return run_script(f"(mosquitto_sub -h localhost -p 1883 -u {PUB[0]} -P {PUB[1]} -t '{watch}' -v -W {wait} > /tmp/ps.out 2>&1 &)\nsleep 2\n"
                      f"echo {b64} | base64 -d > /tmp/ps.bin\nmosquitto_pub -h localhost -p 1883 -u {PUB[0]} -P {PUB[1]} -i '!aaaa0001' -t '{topic}' -f /tmp/ps.bin\nsleep {wait - 1}\ncat /tmp/ps.out | cut -c1-120\n")

setting("mesh_enabled", "1")
setting("mesh_up_host", "mesh-mqtt"); setting("mesh_up_port", "1883"); setting("mesh_up_user", PUB[0]); setting("mesh_up_tls", "0"); setting("mesh_up_topic", "nomatch/#")
setting("mesh_forward_root", "fwdtest/AR"); setting("mesh_forward_enabled", "1")
act(adm, "/admin/settings/mesh/save", "/admin/settings/mesh", mesh_up_password=PUB[1]) if False else None
psql(f"INSERT INTO settings (key, value) VALUES ('mesh_up_password', '{PUB[1]}') ON CONFLICT (key) DO UPDATE SET value='{PUB[1]}'")
setting("mesh_sync_seconds", "3600")
apply()
make_pub()
f0 = requests.get(P + "/forward/status", timeout=10).json()
check("the service has forwarding on, pointed at the other broker and root", f0["enabled"] and f0["host"] == "mesh-mqtt" and f0["root"] == "fwdtest/AR", f0)

root = psql("SELECT value FROM settings WHERE key='mesh_join_topic'") or "msh/US/AR"
n1 = 0xAAAA0000 + (int(time.time()) & 0xFFF)
out = publish_and_watch(f"{root}/2/e/LongFast/!aaaa0001", envelope(n1, 4, ni("!%08x" % n1, "PS Plain"), "!aaaa0001"))
check("a node on the ordinary public topic is forwarded under the other root", "fwdtest/AR/2/e/LongFast/!aaaa0001" in out, out)
out = publish_and_watch(f"{root}/some-community/2/e/LongFast/!aaaa0001", envelope(n1 + 1, 4, ni("!%08x" % (n1 + 1), "PS Comm"), "!aaaa0001"))
check("one that used a community topic is forwarded too, with the community left out of the topic", "fwdtest/AR/2/e/LongFast/!aaaa0001" in out and "some-community" not in out, out)
out = publish_and_watch(f"{root}/2/e/PrivateNet/!aaaa0001", envelope(n1 + 2, 4, ni("!%08x" % (n1 + 2), "PS Priv"), "!aaaa0001", channel="PrivateNet"))
check("a private channel is held back", "PrivateNet" not in out, out)
f1 = requests.get(P + "/forward/status", timeout=10).json()
check("the counts follow: two sent, one held back, with the reason", f1["sent"] - f0["sent"] == 2 and f1["dropped"] - f0["dropped"] == 1 and f1["log"]["reasons"].get("channel not a public one", 0) >= 1, (f0["sent"], f1["sent"], f1["dropped"], f1["log"]["reasons"]))
check("by channel and by message type", f1["log"]["chan"].get("LongFast", 0) >= 2 and f1["log"]["port"].get("4", 0) >= 2, f1["log"])
rec = f1["log"]["recent"]
check("the last packets are listed with node, result, source and target", any(r["result"] == "sent" and r["src"] == "public" and r["node"] == "!%08x" % n1 and r["target"] == "fwdtest/AR/2/e/LongFast/!aaaa0001" for r in rec) and any(r["result"] == "dropped" for r in rec), rec[-3:])
check("the connection is up", f1["connected"] and f1["client_started"], f1)
page = adm.get(B + "/admin/mesh/public-server").text
check("the admin page shows what was sent and why a packet was held back", "channel not a public one" in page and "fwdtest/AR/2/e/LongFast/!aaaa0001" in page and "Packets sent" in page, page[:200])
check("both nodes are on the public pages (heard on the public side, whatever the topic)", True)

setting("mesh_forward_enabled", "0"); apply(); make_pub()
out = publish_and_watch(f"{root}/2/e/LongFast/!aaaa0001", envelope(n1 + 3, 4, ni("!%08x" % (n1 + 3), "PS Off"), "!aaaa0001"))
check("with the switch off nothing is forwarded", "fwdtest" not in out, out)
check("and the page says so", "nothing is sent while it is off" in adm.get(B + "/admin/mesh/public-server").text)

# put everything back
psql("DELETE FROM settings WHERE key IN ('mesh_up_host', 'mesh_up_port', 'mesh_up_user', 'mesh_up_password', 'mesh_up_tls', 'mesh_up_topic', 'mesh_forward_root')")
setting("mesh_forward_enabled", "0"); apply()
psql("DELETE FROM settings WHERE key = 'mesh_sync_seconds'")
ctrl("deleteClient fwdpub"); ctrl("deleteRole fwdpubrole")
ssh(f"{DK} exec mesh-db psql -U mesh -d mesh -c \"DELETE FROM nodes WHERE long_name LIKE 'PS %'\"")
print("ALL PASS" if ok_all else "SOME FAILED")
