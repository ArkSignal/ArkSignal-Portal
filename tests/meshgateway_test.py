"""Which gateways and servers are working (request 218): a login's connection state and packet counts come from the broker's own log and the packets; the Test My Connection data; the Public Meshtastic
Server and Mesh Gateways admin pages; how a node was last heard (by itself over MQTT or by a gateway, and through which broker); and the private data on a node's page for the people who may see it.
Needs the mesh service and both portals on the test box (sh tests/deploy_test.sh, sh tests/deploy_mesh.sh)."""
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
def mpsql(sql): return ssh(f"{DK} exec mesh-db psql -U mesh -d mesh -tA -c \"{sql}\"").strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    requests.get(P + "/health", timeout=8)
except Exception as e:
    print("SKIP meshgateway_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)

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
def envelope(sender, port, payload, gateway, channel="LongFast"):
    return ld(1, fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, vi(1, port) + ld(2, payload) + vi(9, 1)) + fx(6, 4242)) + ld(2, channel) + ld(3, gateway)
def ni(sid, long): return ld(1, sid) + ld(2, long) + ld(3, long[-4:]) + vi(5, 9) + vi(7, 0)
def pos(lat, lon): return sfx(1, int(lat * 1e7)) + sfx(2, int(lon * 1e7)) + vi(3, 90) + vi(23, 13)
def regtok(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)

setting("mesh_enabled", "1"); setting("mesh_private_enabled", "1"); setting("mesh_communities_enabled", "1"); setting("mesh_gateway_silent_minutes", "180")
psql("DELETE FROM users WHERE username IN ('gwmember', 'gwteam', 'gwplain')")
psql("DELETE FROM mesh_accounts WHERE label LIKE 'ZZ GW%'")
mpsql("DELETE FROM nodes WHERE long_name LIKE 'GW %'"); mpsql("DELETE FROM pnodes WHERE long_name LIKE 'GW %'"); mpsql("DELETE FROM gw_status WHERE username LIKE 'gwmember-%'")
for n in ("gwmember", "gwteam", "gwplain"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": regtok(reg, "/my/register"), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
adm, MEM, TEAM, PLAIN = sess("marty", PW), sess("gwmember", "gwmemberpassword99"), sess("gwteam", "gwteampassword99"), sess("gwplain", "gwplainpassword99")

# ---- the two admin pages and the service's status doors
check("the Public Meshtastic Server page is for administrators", adm.get(B + "/admin/mesh/public-server").status_code == 200 and PLAIN.get(B + "/admin/mesh/public-server").status_code in (302, 403, 404))
check("and so is Mesh Gateways", adm.get(B + "/admin/mesh/gateways").status_code == 200 and PLAIN.get(B + "/admin/mesh/gateways").status_code in (302, 403, 404))
menu = adm.get(B + "/admin/mesh/gateways").text
check("both are on the admin menu", "/admin/mesh/public-server" in menu and "/admin/mesh/gateways" in menu)
f, u = requests.get(P + "/forward/status", timeout=10).json(), requests.get(P + "/upstream/status", timeout=10).json()
check("the service reports forwarding: the switch, the connection, counts, reasons and the last packets", all(k in f for k in ("enabled", "connected", "sent", "dropped", "error", "log")) and all(k in f["log"] for k in ("reasons", "chan", "port", "recent", "src")), list(f))
check("and reading: connection, messages, regions, results, the area and the node counts", all(k in u for k in ("enabled", "topics", "area", "nodes", "up_", "own")) and "connected" in u["up_"] and "regions" in u["own"], list(u))
psql("DELETE FROM settings WHERE key IN ('mesh_up_topic', 'mesh_south', 'mesh_west', 'mesh_north', 'mesh_east')")
radio = adm.get(B + "/admin/settings/mesh").text
check("the default topics read Arkansas and the states around it", all(f"msh/US/{s}/#" in radio for s in ("AR", "MO", "TN", "MS", "LA", "TX", "OK")))
check("the admin pages show them", "Sending to the Public Server" in adm.get(B + "/admin/mesh/public-server").text and "Reading the Public Server" in adm.get(B + "/admin/mesh/public-server").text)
check("the settings have the forward switch and the silent time", all(x in adm.get(B + "/admin/settings/mesh").text for x in ("Forward to the Public Meshtastic Server", "Gateway Gone Silent After")))
adm.post(B + "/admin/status/run", data={}, headers={"X-Requested-With": "XMLHttpRequest", "X-CSRFToken": re.search(r'CSRF_TOKEN = "([^"]+)"', adm.get(B + "/admin/status").text).group(1)})
check("Service Status has a line for it", "Public Meshtastic Server" in " | ".join(x["name"] for x in requests.get(B + "/api/status.json").json()["services"]))

# ---- a member's login, then a gateway that uses it
tm = regtok(MEM, "/my/mesh")
MEM.post(B + "/my/mesh/add", data={"csrf_token": tm, "label": "ZZ GW Hill", "nodes": ""}, allow_redirects=True)
aid = psql("SELECT id FROM mesh_accounts WHERE label='ZZ GW Hill'")
user = psql(f"SELECT username FROM mesh_accounts WHERE id={aid}") if aid else ""
page = MEM.get(B + f"/my/mesh/{aid}").text if aid else ""
pw = (re.search(r"Password</th><td class=\"mono copyable\">([^<]+)<", page) or [0, ""])[1]
check("a member makes a login", bool(aid) and user.startswith("gwmember-") and len(pw) >= 18, (aid, user))
time.sleep(4)   # the broker takes the new login
check("its page has Connection Status and the Test button", "Connection Status" in page and "Test My Connection" in page and f"/my/mesh/{aid}/status.json" in page)
st0 = MEM.get(B + f"/my/mesh/{aid}/status.json").json()
check("before any gateway uses it: never connected, in words", st0["log_feed"] and "Never connected" in st0["summary"]["text"], st0)
check("the status data is the owner's only", PLAIN.get(B + f"/my/mesh/{aid}/status.json").status_code == 404 and adm.get(B + f"/my/mesh/{aid}/status.json").status_code == 200)

gwid = 0xAB12C000 + (int(time.time()) & 0xFFF)
gw = "!%08x" % gwid
def mq_publish(user_, pw_, cid, topic, env, extra=""):
    """In the broker's container: one MQTT client with a chosen client id, as a gateway does."""
    b64 = base64.b64encode(env).decode()
    return ssh(f"{DK} exec mesh-mqtt sh -c \"echo {b64} | base64 -d > /tmp/gw.bin; mosquitto_pub -h localhost -p 1883 -u {user_} -P {pw_} -i '{cid}' -t '{topic}' -f /tmp/gw.bin {extra} 2>&1\"", 60)
root = psql("SELECT value FROM settings WHERE key='mesh_join_topic'") or "msh/US/AR"
mq_publish(user, pw, gw, f"{root}/2/e/LongFast/{gw}", envelope(gwid, 4, ni(gw, "GW Itself"), gw))
mq_publish(user, pw, gw, f"{root}/2/e/LongFast/{gw}", envelope(gwid, 3, pos(35.2, -92.5), gw))
time.sleep(4)
st = MEM.get(B + f"/my/mesh/{aid}/status.json").json()
L = st["login"]
check("after a gateway connects and sends: packets counted, last packet just now, the client id (the node id) kept", L.get("packets", 0) >= 2 and L.get("last_packet_ago") is not None and L["last_packet_ago"] < 60 and L.get("clientid") == gw.lstrip("!"), L)
check("what became of them is counted (kept)", (L.get("outcomes") or {}).get("stored", 0) >= 1, L.get("outcomes"))
check("the connection log was read: it connected and left again", L.get("last_disconnect_ago") is not None and L.get("since_ago") is not None, L)
check("the words say so", "Not connected, left" in st["summary"]["text"] or "Connected" in st["summary"]["text"], st["summary"])
check("My Mesh Nodes shows the state on the login's card", "Not connected, left" in MEM.get(B + "/my/mesh").text or "Connected" in MEM.get(B + "/my/mesh").text)
check("Mesh Gateways lists the login with its packets", user in adm.get(B + "/admin/mesh/gateways").text)

# ---- how it was last heard
row = mpsql(f"SELECT last_gateway || '|' || last_source || '|' || public_direct FROM nodes WHERE node_id={gwid}")
check("the node's last heard is kept: the gateway, the broker, and that it was public-side", row == f"{gw}|own|true", row)
nodepage = requests.get(B + f"/mesh/{gwid:08x}").text
check("its page says it was heard directly, by the node itself over MQTT, through this site's broker", "Directly from the node: it sends to MQTT itself" in nodepage and "this site&#39;s broker" in nodepage.replace("'", "&#39;") or "this site's broker" in nodepage, nodepage[nodepage.find("Last heard"):][:300])
other = gwid + 1
mq_publish(user, pw, gw, f"{root}/2/e/LongFast/{gw}", envelope(other, 4, ni("!%08x" % other, "GW Relayed"), gw))
mq_publish(user, pw, gw, f"{root}/2/e/LongFast/{gw}", envelope(other, 3, pos(35.3, -92.6), gw))
time.sleep(4)
check("a node heard through another gateway says so, with the gateway's name", "Relayed: heard over the air by the gateway" in requests.get(B + f"/mesh/{other:08x}").text and "GW Itself" in requests.get(B + f"/mesh/{other:08x}").text)
time.sleep(21)
lst = requests.get(B + "/mesh/nodes?q=GW+").text
check("the public node list has a Heard Via column: itself, and the gateway", "Heard Via" in lst and ">Itself<" in lst, lst[lst.find("Heard Via"):][:200])

# ---- a refused login is counted
bad = mq_publish(user, "wrongpassword", "!deadbeef", f"{root}/2/e/LongFast/!deadbeef", envelope(1, 4, ni("!00000001", "x"), "!deadbeef"))
time.sleep(3)
allst = requests.get(P + "/gateways", timeout=10).json()["logins"]
check("a connection refused for a wrong password is counted by the broker's log", any(v.get("refused", 0) >= 1 for v in allst.values()), (bad[:100], {k: v.get("refused") for k, v in allst.items()}))

# ---- the words for each state, and the silent logic (the portal's own code)
script = r'''
from app import mesh as ms, mesh_alerts as ma
ok = lambda **k: dict({"connected": False, "since_ago": None, "last_disconnect_ago": None, "last_packet_ago": None, "packets_hour": 0, "packets": 0, "outcomes": {}, "refused_ago": None, "refused": 0}, **k)
def s(L, feed=True, down=False): return ms.gateway_summary({"log_feed": feed, "down": down, "logins": {"u": L} if L is not None else {}}, "u")
print("1", s(None)["text"])
print("2", s(ok(connected=True, since_ago=30))["text"])
print("3", s(ok(connected=True, since_ago=300, last_packet_ago=20, packets=5, packets_hour=5))["text"], s(ok(connected=True, last_packet_ago=20))["level"])
print("4", s(ok(connected=True, last_packet_ago=4000))["text"], s(ok(connected=True, last_packet_ago=4000))["level"])
print("5", s(ok(refused=3, refused_ago=60))["text"], s(ok(refused=3, refused_ago=60))["level"])
print("6", s(ok(last_disconnect_ago=7200, last_packet_ago=7300))["text"])
print("7", s(None, down=True)["text"])
print("8", s(None, feed=False)["text"])
st = {"log_feed": True, "logins": {"a": ok(last_packet_ago=3 * 3600 + 5, packets=9), "b": ok(last_packet_ago=60, packets=9), "c": ok(last_packet_ago=None, packets=0), "d": ok(last_packet_ago=99999, packets=0)}}
print("9", sorted(ma.silent_logins(st, 180)))
print("10", sorted(ma.silent_logins(st, 0)), ma.silent_logins({"log_feed": False, "logins": st["logins"]}, 180), ma.silent_logins({"down": True}, 180))
'''
subprocess.run(["ssh", "-q", "nastemp", "cat > /tmp/gw_unit.py"], input=script, text=True, timeout=60)
out = ssh(f"{DK} cp /tmp/gw_unit.py adsb-portal:/tmp/gw_unit.py; {DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/gw_unit.py 2>&1", 120)
lines = {l.split(" ", 1)[0]: l.split(" ", 1)[1] if " " in l else "" for l in out.splitlines() if l[:2].strip().isdigit()}
check("never connected is said plainly", lines.get("1", "").startswith("Never connected"), out[-400:])
check("connected with no packets yet", lines.get("2", "").startswith("Connected, no packets yet"), lines.get("2"))
check("connected and sending is ok", lines.get("3", "").startswith("Connected, last packet just now") and lines.get("3", "").endswith("ok"), lines.get("3"))
check("connected but quiet is a warning", "Connected but quiet" in lines.get("4", "") and lines.get("4", "").endswith("warn"), lines.get("4"))
check("refused is bad, with the count", lines.get("5", "").startswith("Refused 3 times") and lines.get("5", "").endswith("bad"), lines.get("5"))
check("left and quiet says when", lines.get("6", "").startswith("Not connected, left 2 hours ago"), lines.get("6"))
check("the service not answering and no connection log are said, not guessed", "did not answer" in lines.get("7", "") and "No connection data yet" in lines.get("8", ""), (lines.get("7"), lines.get("8")))
check("only a login that sent before and has been quiet longer than the setting is silent", lines.get("9", "") == "['a']", lines.get("9"))
check("zero minutes, no connection log, or no answer means nothing is flagged", lines.get("10", "") == "[] {} {}", lines.get("10"))

# ---- the private data on a node's page, for the people who may see it
com = "gw-team"
psql("DELETE FROM communities WHERE slug = 'gw-team'")
ok1, m1, _h = act(adm, "/admin/communities/create", "/admin/communities", name="GW Team", owner="gwteam")
pn = 0xAB13C000 + (int(time.time()) & 0xFFF)
pgw = "!%08x" % (pn + 5)
def pingest(env, g, channel="LongFast"): return requests.post(P + "/ingest_private", json={"topic": f"mshp/{com}/2/e/{channel}/{g}", "envelope_b64": base64.b64encode(env).decode()}, timeout=20).json()["result"]
pingest(envelope(pn, 4, ni("!%08x" % pn, "GW Secret"), pgw, channel="PrivateNet"), pgw, "PrivateNet")
pingest(envelope(pn, 3, pos(35.4, -92.7), pgw, channel="PrivateNet"), pgw, "PrivateNet")
time.sleep(2)
check("a node only the private feed heard is stored privately", mpsql(f"SELECT community FROM pnodes WHERE node_id={pn}") == com and mpsql(f"SELECT COUNT(*) FROM nodes WHERE node_id={pn}") == "0")
pub = requests.get(B + f"/mesh/{pn:08x}")
check("the public cannot open it", pub.status_code == 404)
check("a signed-in person with no access cannot either", PLAIN.get(B + f"/mesh/{pn:08x}").status_code == 404)
adm_page = adm.get(B + f"/mesh/{pn:08x}")
check("an administrator opens it and sees the private data, marked as not on the public pages", adm_page.status_code == 200 and "Private Data" in adm_page.text and "not on the public pages" in adm_page.text and "GW Secret" in adm_page.text and "Exact position" in adm_page.text, adm_page.status_code)
check("the page links to the private map and list", f"/mesh/c/{com}/private-map" in adm_page.text and f"/mesh/c/{com}/private-nodes" in adm_page.text)
check("the private gateway that heard it is listed", pgw in adm_page.text or pgw.lstrip("!") in adm_page.text)
check("opening it is logged", int(psql("SELECT count(*) FROM audit_log WHERE action='community.privatenode'") or 0) >= 1)
tp = TEAM.get(B + f"/mesh/{pn:08x}")
check("the community's Owner opens it too", tp.status_code == 200 and "Private Data" in tp.text and "GW Secret" in tp.text, tp.status_code)
psql(f"INSERT INTO community_members (community_id, user_id, role, created_by) SELECT c.id, u.id, 'viewer', u.id FROM communities c, users u WHERE c.slug='{com}' AND u.username='gwplain' ON CONFLICT DO NOTHING")
check("a plain Viewer of the community does not see the private data (it needs the Private Map permission)", PLAIN.get(B + f"/mesh/{pn:08x}").status_code == 404)
# a node that is on the public pages too shows the private panel only to those who may
pingest(envelope(gwid, 4, ni(gw, "GW Itself"), "!aaaaaaaa"), "!aaaaaaaa")
time.sleep(1)
psql("DELETE FROM community_members WHERE user_id IN (SELECT id FROM users WHERE username='gwplain')")

# tidy up
psql("DELETE FROM communities WHERE slug = 'gw-team'")
psql("DELETE FROM users WHERE username IN ('gwmember', 'gwteam', 'gwplain')")
psql("DELETE FROM mesh_accounts WHERE label LIKE 'ZZ GW%'")
mpsql("DELETE FROM nodes WHERE long_name LIKE 'GW %'"); mpsql("DELETE FROM pnodes WHERE long_name LIKE 'GW %'"); mpsql("DELETE FROM gw_status WHERE username LIKE 'gwmember-%'")
print("ALL PASS" if ok_all else "SOME FAILED")
