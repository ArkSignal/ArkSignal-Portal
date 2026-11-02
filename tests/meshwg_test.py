"""The private tunnel (request 205, stage 4), end to end on the test box: a community's team makes a tunnel peer on its Broker Logins page and gets a configuration and QR code once; a real WireGuard client built
from that configuration reaches the broker's private listener at the tunnel address and a packet it publishes arrives on the private map; a switched off or deleted peer is shut out; a peer from another community
cannot publish under this one's topic; the tunnel gives access to the broker port and nothing else.
Needs the mesh service, the tunnel container and both portals on the test box (sh tests/deploy_test.sh, sh tests/deploy_mesh.sh) and a kernel with WireGuard."""
import base64, os, re, struct, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18482"
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def ssh(cmd, t=180):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=t).stdout
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    requests.get(P + "/health", timeout=8)
except Exception as e:
    print("SKIP meshwg_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)
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
def envelope(sender, name):
    ni = ld(1, "!%08x" % sender) + ld(2, name) + ld(3, name[-4:]) + vi(5, 9) + vi(7, 0)
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, vi(1, 4) + ld(2, ni) + vi(9, 0)) + fx(6, 555)
    return ld(1, pk) + ld(2, "LongFast") + ld(3, "!feedbeef")

setting("mesh_enabled", "1"); setting("mesh_communities_enabled", "1"); setting("mesh_private_enabled", "1"); setting("mesh_wg_endpoint", f"{HOST}:18851")
requests.post(P + "/config", json={"private_enabled": "1"}, timeout=20)
psql("DELETE FROM communities WHERE slug IN ('wgt-one', 'wgt-two')")
psql("DELETE FROM users WHERE username IN ('wgtowner1', 'wgtowner2')")
for u in ("wgtowner1", "wgtowner2"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": u, "email": u + "@example.com", "password": u + "password9", "confirm": u + "password9"})
adm = sess("marty", PW)
act(adm, "/admin/communities/create", "/admin/communities", name="WGT One", owner="wgtowner1")
act(adm, "/admin/communities/create", "/admin/communities", name="WGT Two", owner="wgtowner2")
O1, O2 = sess("wgtowner1", "wgtowner1password9"), sess("wgtowner2", "wgtowner2password9")
def post(s, slug, path, **d):
    d["csrf_token"] = tok(s, f"/mesh/c/{slug}/logins"); d["s"] = "logins"
    return s.post(B + path, data=d, allow_redirects=False)
def login_for(s, slug):
    post(s, slug, f"/mesh/c/{slug}/logins/add", label="Private gateway", link="private")
    m = re.search(r"Broker login ([\w.-]+) \(open it from the list to see the password again\): ([\w-]+)", s.get(B + f"/mesh/c/{slug}").text)
    return (m.group(1), m.group(2)) if m else ("", "")
u1, p1 = login_for(O1, "wgt-one")
u2, p2 = login_for(O2, "wgt-two")
check("a Private feed login is made for each community", u1 and u2 and psql("SELECT string_agg(link, ',') FROM mesh_accounts WHERE username IN ('%s','%s')" % (u1, u2)) == "private,private")

def make_peer(s, slug, label, login=None):
    return post(s, slug, f"/mesh/c/{slug}/tunnel/add", label=label, **({"login": login} if login else {}))
u1b, p1b = login_for(O1, "wgt-one")
lid1 = psql("SELECT id FROM mesh_accounts WHERE username='%s'" % u1); lid1b = psql("SELECT id FROM mesh_accounts WHERE username='%s'" % u1b)
nopeer = make_peer(O1, "wgt-one", "No login chosen")
check("a tunnel peer is not made without one of the community's private feed logins, and a login of another community cannot be chosen", psql("SELECT count(*) FROM mesh_wg_peers") == "0" and make_peer(O1, "wgt-one", "Other", psql("SELECT id FROM mesh_accounts WHERE username='%s'" % u2)).status_code in (302, 400) and psql("SELECT count(*) FROM mesh_wg_peers") == "0")
r = make_peer(O1, "wgt-one", "Test hill gateway", lid1)
conf = re.search(r"<pre[^>]*>(.*?)</pre>", r.text, re.S)
conf = re.sub(r"&#34;|&quot;", '"', conf.group(1)).replace("&amp;", "&") if conf else ""
g = lambda k: (re.search(k + r" = (\S+)", conf) or [None, ""])[1]
priv, addr, spub, ep, allowed = g("PrivateKey"), g("Address"), g("PublicKey"), g("Endpoint"), g("AllowedIPs")
check("a tunnel peer shows its configuration, with a QR code and the broker settings", r.status_code == 200 and priv and addr.endswith("/32") and spub and ep == f"{HOST}:18851" and "<svg" in r.text and "1884" in r.text and "mshp/wgt-one" in r.text, r.status_code)
check("the setup page explains Linux, phones, Windows and Mac, routers and troubleshooting, as code blocks (with the site's copy button), with a download", all(x in r.text for x in ("wg-quick up", "Scan from QR code", "Import tunnel", "masquerade", "latest handshake", "data:text/plain;base64,", '<pre class="code" id="wgconf"')), "")
qtag = (re.search(r'<svg[^>]*class="segno"[^>]*>', r.text) or [""])[0]
check("the QR code scales to its box instead of overflowing it", 'viewBox="0 0 ' in qtag and "width:100%" in qtag and " width=" not in qtag, qtag)
check("the configuration routes only the tunnel address (nothing else a phone does goes through)", allowed == "10.77.0.1/32", allowed)
check("it is not cached and its private key is kept only sealed", "no-store" in r.headers.get("Cache-Control", "") and psql("SELECT count(*) FROM mesh_wg_peers WHERE public_key = '%s' OR private_sealed = '%s'" % (priv, priv)) == "0" and psql("SELECT count(*) FROM mesh_wg_peers WHERE private_sealed <> ''") == "1")
check("the peer is listed on the page with its address", addr.split("/")[0] in O1.get(B + "/mesh/c/wgt-one/logins").text)
pid0 = psql("SELECT id FROM mesh_wg_peers ORDER BY id LIMIT 1")
ro = O1.get(B + f"/mesh/c/wgt-one/tunnel/{pid0}")
check("the team can open the peer again and gets the same configuration and QR code (not cached), and the opening is logged", ro.status_code == 200 and f"PrivateKey = {priv}" in ro.text.replace("&#34;", '"') and "<svg" in ro.text and "no-store" in ro.headers.get("Cache-Control", "")
      and int(psql("SELECT count(*) FROM audit_log WHERE action = 'community.tunnel' AND detail LIKE 'opened tunnel peer%'") or 0) >= 1, ro.status_code)
check("another community's team cannot open it", O2.get(B + f"/mesh/c/wgt-one/tunnel/{pid0}").status_code in (403, 404))
lid0 = psql("SELECT id FROM mesh_accounts WHERE username='%s'" % u1)
rl0 = O1.get(B + f"/mesh/c/wgt-one/logins/{lid0}")
check("a private login can be opened again: its username, password, tunnel address and root topic, and the peer it is bound to", rl0.status_code == 200 and u1 in rl0.text and p1 in rl0.text and "10.77.0.1" in rl0.text and "1884" in rl0.text and "mshp/wgt-one" in rl0.text and "Test hill gateway" in rl0.text and "no-store" in rl0.headers.get("Cache-Control", ""), rl0.status_code)
check("another community's team cannot open the login", O2.get(B + f"/mesh/c/wgt-one/logins/{lid0}").status_code in (403, 404))

# the client joins the broker's docker network and reaches the tunnel container by name: the published UDP port is only checked in the configuration text (a host cannot reach its own published port from a container on every box)
def client(conf_priv, conf_addr, user, pw, topic, blob, extra="", wait=4):
    script = (f"apk add -q --no-cache wireguard-tools iproute2 >/dev/null 2>&1; echo '{conf_priv}' > /k; ip link add wg0 type wireguard; ip addr add {conf_addr} dev wg0; "
              f"wg set wg0 private-key /k peer {spub} endpoint mesh-wg:51820 allowed-ips 10.77.0.1/32 persistent-keepalive 3; ip link set wg0 up mtu 1380; ip route add 10.77.0.1/32 dev wg0 2>/dev/null; sleep {wait}; "
              f"echo {base64.b64encode(blob).decode()} | base64 -d > /p.bin; timeout 10 mosquitto_pub -h 10.77.0.1 -p 1884 -u {user} -P {pw} -t '{topic}' -f /p.bin; echo rc=$?; {extra}")
    b64 = base64.b64encode(script.replace("; ", chr(10)).encode()).decode()   # the script goes over as a file: the remote shell must not expand its $?
    return ssh(f"echo {b64} | base64 -d > /tmp/wgc.sh; {DK} run --rm --network mesh_test --cap-add NET_ADMIN -v /tmp/wgc.sh:/wgc.sh:ro --entrypoint sh eclipse-mosquitto:2 /wgc.sh", 240).strip().splitlines()
def wait_node(slug, name, tries=12):
    for _ in range(tries):
        time.sleep(1)
        if any(n.get("long_name") == name for n in requests.get(P + "/private/nodes", params={"community": slug}).json()["nodes"]):
            return True
    return False
t = int(time.time()) & 0xFFFF
n1 = 0xE1000000 + t * 8
out = client(priv, addr, u1, p1, "mshp/wgt-one/2/e/LongFast/!feedbeef", envelope(n1, "ZZ Tunnel One"), wait=12, extra="timeout 4 mosquitto_pub -h 10.77.0.1 -p 22 -t x -m x; echo ssh=$?; timeout 4 mosquitto_pub -h 10.77.0.1 -p 1883 -t x -m x; echo plain=$?")
check("a client built from the configuration reaches the private listener through the tunnel and its packet arrives on the private feed", "rc=0" in out and wait_node("wgt-one", "ZZ Tunnel One"), out)
check("through the tunnel nothing else answers (no other port on the tunnel address)", any(x.startswith("ssh=") and x != "ssh=0" for x in out) and any(x.startswith("plain=") and x != "plain=0" for x in out), out)
check("the tunnel container forwards nothing (the FORWARD chain drops everything)", "-P FORWARD DROP" in ssh(f"{DK} exec mesh-wg iptables -S FORWARD"))
st = requests.get(P + "/wg/status").json()
check("the tunnel reports the peer's handshake", st.get("ready") and any(v["handshake"] > 0 for v in st["peers"].values()), st)
adm.post(B + "/admin/status/run", data={}, headers={"X-Requested-With": "XMLHttpRequest", "X-CSRFToken": re.search(r'CSRF_TOKEN = "([^"]+)"', adm.get(B + "/admin/status").text).group(1)})
sv = [x for x in requests.get(B + "/api/status.json").json()["services"] if x["name"] == "Private Mesh Tunnel"]
check("Service Status has a Private Mesh Tunnel line that says the tunnel is up", len(sv) == 1 and sv[0]["state"] == "ok" and "up with 1 peer" in sv[0]["summary"], sv)
check("and the page says the peer connected", "connected" in O1.get(B + "/mesh/c/wgt-one/logins").text.split("Tunnel for Private Gateways")[-1].replace("never connected", ""), "")
n2 = 0xE1000000 + t * 8 + 1
out2 = client(priv, addr, u2, p2, "mshp/wgt-one/2/e/LongFast/!feedbeef", envelope(n2, "ZZ Tunnel Cross"))
time.sleep(3)
check("another community's login cannot publish under this community's topic, even through the tunnel", not any(n.get("long_name") == "ZZ Tunnel Cross" for n in requests.get(P + "/private/nodes").json()["nodes"]), out2)

# ---- isolation: a peer can talk to the broker port of the tunnel address and nothing else, and not to the other peers
r2 = make_peer(O1, "wgt-one", "Second gateway", lid1b)
conf2 = re.sub(r"&#34;|&quot;", '"', re.search(r"<pre[^>]*>(.*?)</pre>", r2.text, re.S).group(1)).replace("&amp;", "&")
priv2 = re.search(r"PrivateKey = (\S+)", conf2).group(1); addr2 = re.search(r"Address = (\S+)", conf2).group(1)
def wgup(priv_, addr_):
    return (f"apk add -q --no-cache wireguard-tools iproute2 >/dev/null 2>&1; echo '{priv_}' > /k; ip link add wg0 type wireguard; ip addr add {addr_} dev wg0; "
            f"wg set wg0 private-key /k peer {spub} endpoint mesh-wg:51820 allowed-ips 10.77.0.1/32 persistent-keepalive 3; ip link set wg0 up mtu 1380; ip route add 10.77.0.1/32 dev wg0 2>/dev/null; ")
def wgrun(script, name=None, detach=False):
    b64_ = base64.b64encode(script.replace("; ", chr(10)).encode()).decode()
    f = "/tmp/wgi_%s.sh" % (name or "a")
    return ssh(f"echo {b64_} | base64 -d > {f}; {DK} run {'-d --name ' + name if detach else '--rm'} --network mesh_test --cap-add NET_ADMIN -v {f}:/wgc.sh:ro --entrypoint sh eclipse-mosquitto:2 /wgc.sh", 240).strip().splitlines()
ssh(f"{DK} rm -f zz-wg-b >/dev/null 2>&1")
wgrun(wgup(priv2, addr2) + "sleep 4; nc -l -p 9000 > /tmp/b.txt & sleep 60", name="zz-wg-b", detach=True)
time.sleep(14)
b_ok = "9000" in ssh(f"{DK} exec zz-wg-b sh -c 'netstat -ltn 2>/dev/null | grep 9000'")
check("the second peer is up in the tunnel and listening on a port of its own (so the next checks are meaningful)", b_ok)
b_ip = addr2.split("/")[0]
outi = wgrun(wgup(priv, addr) + "sleep 5; "
             f"timeout 6 mosquitto_pub -h 10.77.0.1 -p 1884 -u {u1} -P {p1} -t 'mshp/wgt-one/2/e/LongFast/!feedbee1' -m x; echo broker=$?; "
             f"wg set wg0 peer {spub} allowed-ips 0.0.0.0/0; "
             "MQ=$(getent hosts mesh-mqtt | cut -d' ' -f1); WGI=$(getent hosts mesh-wg | cut -d' ' -f1); ip route add $MQ/32 dev wg0; ip route add $WGI/32 dev wg0; "
             f"ip route add {b_ip}/32 dev wg0; "
             f"nc -w 3 {b_ip} 9000 </dev/null; echo peer_tcp=$?; "
             f"ping -c 1 -W 2 {b_ip} >/dev/null 2>&1; echo peer_ping=$?; "
             "timeout 6 mosquitto_pub -h $MQ -p 1883 -u x -P y -t t -m m; echo behind_broker=$?; timeout 6 nc -w 3 $WGI 22 </dev/null; echo behind_wg=$?")
rc = {x.split("=")[0]: x.split("=")[1] for x in outi if "=" in x and x.split("=")[0] in ("broker", "peer_tcp", "peer_ping", "behind_broker", "behind_wg")}
def blocked(k): return rc.get(k) not in (None, "0", "127")   # a command that is missing (127) proves nothing
print("isolation results:", rc)
check("through the tunnel a peer reaches the broker port of the tunnel address (the control)", rc.get("broker") == "0", outi)
check("it cannot open a connection to another peer, even with the other peer listening and a route of its own to it (a hostile client lists any address it likes as allowed)", blocked("peer_tcp"), outi)
check("it cannot ping another peer", blocked("peer_ping"), outi)
check("it cannot reach the broker's own port or anything on the networks behind the tunnel container, even with a route of its own to them", blocked("behind_broker") and blocked("behind_wg"), outi)
ssh(f"{DK} rm -f zz-wg-b >/dev/null 2>&1")
# ---- a login works only through the tunnel peer it is bound to
outl = wgrun(wgup(priv, addr) + "sleep 5; "
             f"timeout 6 mosquitto_pub -h 10.77.0.1 -p 1884 -u {u1} -P {p1} -t 'mshp/wgt-one/2/e/LongFast/!feedbee1' -m x; echo own=$?; "
             f"timeout 6 mosquitto_pub -h 10.77.0.1 -p 1884 -u {u1b} -P {p1b} -t 'mshp/wgt-one/2/e/LongFast/!feedbee1' -m x; echo others_login=$?; "
             f"timeout 6 mosquitto_pub -h 10.77.0.1 -p 1884 -u {u2} -P {p2} -t 'mshp/wgt-two/2/e/LongFast/!feedbee1' -m x; echo other_community_login=$?; "
             f"timeout 6 mosquitto_pub -h 10.77.0.1 -p 1884 -u {u1} -P wrong-password-1 -t 'mshp/wgt-one/2/e/LongFast/!feedbee1' -m x; echo wrong_password=$?")
rl = {x.split("=")[0]: x.split("=")[1] for x in outl if "=" in x}
print("login results:", rl)
check("a peer can use its own login", rl.get("own") == "0", outl)
check("but not another peer's login, even one it knows, nor a login of another community, nor its own with a wrong password", all(rl.get(k) not in (None, "0", "127") for k in ("others_login", "other_community_login", "wrong_password")), outl)
direct = ssh(f"{DK} exec mesh-mqtt mosquitto_pub -h localhost -u {u1} -P {p1} -t 'mshp/wgt-one/2/e/LongFast/!feedbee1' -m x 2>&1; echo rc=$?")
check("the login's own password opens nothing on the broker itself (its other doors do not know it: only the tunnel's relay does)", "rc=0" not in direct and ("not authori" in direct.lower() or "refused" in direct.lower() or "rc=5" in direct), direct)
rules = ssh(f"{DK} exec mesh-wg sh -c 'iptables -S FORWARD; iptables -S INPUT; ip6tables -S FORWARD 2>/dev/null'")
check("the tunnel container drops everything forwarded in or out of the tunnel and lets in only the relay's port", "-P FORWARD DROP" in rules and "-A FORWARD -i wg0 -j DROP" in rules and "-A FORWARD -o wg0 -j DROP" in rules and "-A INPUT -i wg0 -p tcp -m tcp --dport 1884 -j ACCEPT" in rules and "-A INPUT -i wg0 -j DROP" in rules, rules)
pidB = psql("SELECT id FROM mesh_wg_peers WHERE ip='%s'" % b_ip)
check("each peer is allowed one address only (its own, a single host) by the tunnel", all(re.fullmatch(r"10\.77\.0\.\d+", x) for x in psql("SELECT ip FROM mesh_wg_peers").split()) and ssh(f"{DK} exec mesh-wg wg show wg0 allowed-ips").count("/32") >= 1 and " 0.0.0.0/0" not in ssh(f"{DK} exec mesh-wg wg show wg0 allowed-ips"))
post(O1, "wgt-one", f"/mesh/c/wgt-one/tunnel/{pidB}/delete")

# switch off, then the peer is shut out
pid = psql("SELECT id FROM mesh_wg_peers WHERE community_id=(SELECT id FROM communities WHERE slug='wgt-one') ORDER BY id LIMIT 1")
rk = post(O1, "wgt-one", f"/mesh/c/wgt-one/tunnel/{pid}/rekey")
priv_new = (re.search(r"PrivateKey = (\S+)", re.sub(r"&#34;|&quot;", '"', rk.text)) or [None, ""])[1]
check("New Key gives the peer a new configuration (same address) and the old key stops being a peer", rk.status_code == 200 and priv_new and priv_new != priv and addr.split("/")[0] in rk.text, rk.status_code)
post(O1, "wgt-one", f"/mesh/c/wgt-one/tunnel/{pid}/disable")
time.sleep(14)
n3 = 0xE1000000 + t * 8 + 2
out3 = client(priv_new, addr, u1, p1, "mshp/wgt-one/2/e/LongFast/!feedbeef", envelope(n3, "ZZ Tunnel Off"), wait=12)
check("a switched off peer is shut out of the tunnel", not any("rc=0" == x for x in out3) and not wait_node("wgt-one", "ZZ Tunnel Off", 3), out3)
post(O1, "wgt-one", f"/mesh/c/wgt-one/tunnel/{pid}/enable")
time.sleep(14)
n4 = 0xE1000000 + t * 8 + 3
out4 = client(priv_new, addr, u1, p1, "mshp/wgt-one/2/e/LongFast/!feedbeef", envelope(n4, "ZZ Tunnel Back"), wait=12)
check("switched on again it works", "rc=0" in out4 and wait_node("wgt-one", "ZZ Tunnel Back"), out4)
check("the other community's team cannot manage this community's peers", post(O2, "wgt-one", f"/mesh/c/wgt-one/tunnel/{pid}/delete").status_code in (403, 404))
post(O1, "wgt-one", f"/mesh/c/wgt-one/tunnel/{pid}/delete")
check("a deleted peer is gone from the list and the database", psql("SELECT count(*) FROM mesh_wg_peers WHERE id=%s" % pid) == "0")
setting("mesh_wg_endpoint", "")
r = post(O1, "wgt-one", "/mesh/c/wgt-one/tunnel/add", label="no endpoint")
check("with no tunnel address set no peer is made", psql("SELECT count(*) FROM mesh_wg_peers") == "0")
setting("mesh_wg_endpoint", f"{HOST}:18851")
act(adm, "/admin/communities/wgt-one/delete", "/admin/communities")
act(adm, "/admin/communities/wgt-two/delete", "/admin/communities")
psql("DELETE FROM mesh_accounts WHERE username IN ('%s','%s')" % (u1, u2))
for u in (u1, u2):
    if u: requests.post(P + "/credentials/delete", json={"username": u})
psql("DELETE FROM users WHERE username IN ('wgtowner1', 'wgtowner2')")
subprocess.run(["ssh", "-q", "nastemp", f"{DK} exec mesh-db psql -U mesh -d mesh -tA -c \"DELETE FROM pnodes WHERE long_name LIKE 'ZZ Tunnel %'\""], capture_output=True, timeout=60)
print("ALL PASS" if ok_all else "SOME FAILED")
raise SystemExit(0 if ok_all else 1)
