"""The site's own private feed, the administrators' forwarding choices and site-role access (request 216):
- the Site Private Feed is a hidden community the site owns: administrators open it, nobody else, and it is never public;
- a site role the administrators give access to a community (hidden or not) lets its people in at the chosen level, and taking it away shuts them out again;
- the administrators choose what a community's private feed may pass on to the public map: nothing, or only chosen channels, message types and nodes.
Needs the mesh service and both portals on the test box (sh tests/deploy_test.sh, sh tests/deploy_mesh.sh)."""
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
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    requests.get(P + "/health", timeout=8)
except Exception as e:
    print("SKIP sitefeed_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)
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
def pos(lat, lon): return sfx(1, int(lat * 1e7)) + sfx(2, int(lon * 1e7)) + vi(3, 90)
def envelope(sender, port, payload, gateway, channel="LongFast", ok_mqtt=1):
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, vi(1, port) + ld(2, payload) + vi(9, ok_mqtt)) + fx(6, 777)
    return ld(1, pk) + ld(2, channel) + ld(3, gateway)
def ingest(com, env, gw, channel="LongFast"):
    return requests.post(P + "/ingest_private", json={"topic": f"mshp/{com}/2/e/{channel}/{gw}", "envelope_b64": base64.b64encode(env).decode()}, timeout=20).json()["result"]
def ni(sid, long): return ld(1, sid) + ld(2, long) + ld(3, long[-4:]) + vi(5, 9) + vi(7, 0)
def regtok(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)

setting("mesh_enabled", "1"); setting("mesh_communities_enabled", "1"); setting("mesh_private_enabled", "1")
requests.post(P + "/config", json={"private_enabled": "1", "public_rules": "1"}, timeout=20)
psql("DELETE FROM communities WHERE slug IN ('site-private', 'sfv-open')")
psql("DELETE FROM roles WHERE name IN ('SF Watchers', 'SF Others')")
psql("DELETE FROM users WHERE username IN ('sfwatch', 'sfplain')")
for u in ("sfwatch", "sfplain"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": regtok(reg, "/my/register"), "username": u, "email": u + "@example.com", "password": u + "password9", "confirm": u + "password9"})
adm = sess("marty", ADMPW)
WATCH, PLAIN = sess("sfwatch", "sfwatchpassword9"), sess("sfplain", "sfplainpassword9")

# ---- 1. the Site Private Feed
check("the admin page offers to set the site feed up", "Set Up the Site Private Feed" in adm.get(B + "/admin/communities").text)
ok, msg, _h = act(adm, "/admin/communities/site-feed", "/admin/communities")
check("an administrator sets it up", ok, msg)
row = psql("SELECT published, system FROM communities WHERE slug='site-private'")
check("it is a hidden system community", row == "f|t", row)
check("an administrator opens it and its Broker Logins and Private Map", adm.get(B + "/mesh/c/site-private").status_code == 200 and adm.get(B + "/mesh/c/site-private/logins").status_code == 200 and adm.get(B + "/mesh/c/site-private/private-map").status_code == 200)
check("a member without access cannot see it exists", PLAIN.get(B + "/mesh/c/site-private").status_code == 404 and PLAIN.get(B + "/mesh/c/site-private/private-map").status_code in (403, 404))
check("it is not in the public list of communities", "site-private" not in requests.get(B + "/api/mesh/communities.json").text and "site-private" not in PLAIN.get(B + "/mesh/communities").text)
ok, msg, _h = act(adm, "/admin/communities/site-private/publish", "/admin/communities")
check("it cannot be made public or deleted from the admin page", not ok and psql("SELECT published FROM communities WHERE slug='site-private'") == "f" and not act(adm, "/admin/communities/site-private/delete", "/admin/communities")[0] and psql("SELECT count(*) FROM communities WHERE slug='site-private'") == "1")
check("its menu has only the pages that make sense for a site feed", "/mesh/c/site-private/forwarding" in adm.get(B + "/mesh/c/site-private").text and "/mesh/c/site-private/news" not in adm.get(B + "/mesh/c/site-private").text)
t = int(time.time()) & 0xFFF
nsite, gw = 0xD2000000 + t * 8, "!%08x" % (0xD2100000 + t)
ingest("site-private", envelope(nsite, 4, ni("!%08x" % nsite, "ZZ Site Node"), gw, ok_mqtt=0), gw)
ingest("site-private", envelope(nsite, 3, pos(35.3, -92.4), gw, ok_mqtt=0), gw)
allnames = [n["long_name"] for n in adm.get(B + "/api/mesh/private/nodes.json").json()["nodes"]]
check("a node its gateways hear is on the administrators' all-communities private map", "ZZ Site Node" in allnames, allnames[:5])
check("and not on any public page", "ZZ Site Node" not in requests.get(B + "/api/mesh/nodes.json").text)

# ---- 2. site-role access
psql("INSERT INTO roles (name, description) VALUES ('SF Watchers', 'test'), ('SF Others', 'test')")
psql("INSERT INTO user_roles (user_id, role_id) SELECT u.id, r.id FROM users u, roles r WHERE u.username='sfwatch' AND r.name='SF Watchers'")
rid = psql("SELECT id FROM roles WHERE name='SF Watchers'")
orid = psql("SELECT id FROM roles WHERE name='SF Others'")
check("before any grant the role's person cannot open the site feed", WATCH.get(B + "/mesh/c/site-private").status_code == 404)
check("the access page lists the roles that can be given (not the all-powerful ones)", "SF Watchers" in adm.get(B + "/mesh/c/site-private/access").text and "Super Admin" not in adm.get(B + "/mesh/c/site-private/access").text.split("Site role")[-1][:600])
ok, msg, _h = act(adm, "/mesh/c/site-private/access", "/mesh/c/site-private/access", role_id=rid, level="viewer")
check("an administrator gives the role access as a Viewer", ok and psql("SELECT level FROM community_role_access a JOIN communities c ON c.id=a.community_id WHERE c.slug='site-private'") == "viewer", msg)
check("a Viewer through the role opens the hidden feed's page but not its private map", WATCH.get(B + "/mesh/c/site-private").status_code == 200 and WATCH.get(B + "/mesh/c/site-private/private-map").status_code == 403)
check("the hidden feed is in that person's list of communities, and in nobody else's", "site-private" in WATCH.get(B + "/mesh/communities").text and "site-private" not in PLAIN.get(B + "/mesh/communities").text)
act(adm, "/mesh/c/site-private/access", "/mesh/c/site-private/access", role_id=rid, level="operator")
check("as a Node Operator the same person opens the private map and its data", WATCH.get(B + "/mesh/c/site-private/private-map").status_code == 200 and "ZZ Site Node" in WATCH.get(B + "/api/mesh/private/nodes.json?community=site-private").text)
check("but not the administrators' pages or other communities' private data", WATCH.get(B + "/admin/mesh/private-map").status_code == 403 and WATCH.get(B + "/mesh/c/site-private/forwarding").status_code in (403, 404) and WATCH.get(B + "/mesh/c/site-private/access", allow_redirects=False).status_code in (403, 404))
check("a role that was not given access gets nothing", PLAIN.get(B + "/mesh/c/site-private/private-map").status_code in (403, 404))
check("each change is in the audit log", int(psql("SELECT count(*) FROM audit_log WHERE action='community.access'") or 0) >= 2)
ok, msg, _h = act(adm, f"/mesh/c/site-private/access/{rid}/remove", "/mesh/c/site-private/access")
check("taking the access away shuts the person out again", ok and WATCH.get(B + "/mesh/c/site-private").status_code == 404 and WATCH.get(B + "/api/mesh/private/nodes.json?community=site-private").status_code in (403, 404), msg)
check("a person cannot give themselves access (the page and the form are for administrators)", WATCH.post(B + "/mesh/c/site-private/access", data={"csrf_token": regtok(WATCH, "/my"), "role_id": rid, "level": "manager"}).status_code in (403, 404))

# ---- 3. forwarding choices (a community that has a public channel)
ok, msg, _h = act(adm, "/admin/communities/create", "/admin/communities", name="SFV Open", owner="sfplain")
com = "sfv-open"
def packet(port, name, node, ch="LongFast"):
    gw2 = "!%08x" % (0xD2200000 + t)
    return ingest(com, envelope(node, port, ni("!%08x" % node, name), gw2, channel=ch), gw2, ch)
def fwd(**data):
    ok, msg, _h = act(adm, f"/mesh/c/{com}/forwarding", f"/mesh/c/{com}/forwarding", **data)
    time.sleep(2)
    return ok
n1, n2 = 0xD2300000 + t * 8, 0xD2300000 + t * 8 + 1
check("by default (whatever the public rules allow) a node that agreed to MQTT passes on to the public path", packet(4, "ZZ Fwd One", n1) == "public")
check("the page for it is for administrators only", PLAIN.get(B + f"/mesh/c/{com}/forwarding").status_code in (403, 404) and adm.get(B + f"/mesh/c/{com}/forwarding").status_code == 200)
check("Nothing keeps it all private", fwd(mode="none") and packet(4, "ZZ Fwd Two", n2) == "private")
check("choosing channels: another channel stays private", fwd(mode="filter", channels="LongTurbo") and packet(4, "ZZ Fwd Three", n1 + 2) == "private")
check("and the chosen channel goes", fwd(mode="filter", channels="LongFast, LongTurbo") and packet(4, "ZZ Fwd Four", n1 + 3) == "public")
check("choosing message types: a type not chosen stays private, a chosen one goes", fwd(mode="filter", port_3="1") and packet(4, "ZZ Fwd Five", n1 + 4) == "private" and fwd(mode="filter", port_4="1") and packet(4, "ZZ Fwd Six", n1 + 5) == "public")
check("only the nodes allowed go, and a refused node never does", fwd(mode="filter", allow="!%08x" % (n1 + 6)) and packet(4, "ZZ Fwd Seven", n1 + 6) == "public" and packet(4, "ZZ Fwd Eight", n1 + 7) == "private"
      and fwd(mode="filter", deny="!%08x" % (n1 + 8)) and packet(4, "ZZ Fwd Nine", n1 + 8) == "private" and packet(4, "ZZ Fwd Ten", n1 + 9) == "public")
check("a node id that is not one is refused with a message", not act(adm, f"/mesh/c/{com}/forwarding", f"/mesh/c/{com}/forwarding", mode="filter", allow="hello")[0])
check("a node that did not agree to MQTT never goes, whatever is chosen (the public rules still apply)", fwd(mode="filter") and ingest(com, envelope(n1 + 10, 4, ni("!%08x" % (n1 + 10), "ZZ Fwd Eleven"), "!%08x" % (0xD2200000 + t), ok_mqtt=0), "!%08x" % (0xD2200000 + t)) == "private")
fwd(mode="auto")
check("back to the default it goes again, and the changes are in the audit log", packet(4, "ZZ Fwd Twelve", n1 + 11) == "public" and int(psql("SELECT count(*) FROM audit_log WHERE action='community.forwarding'") or 0) >= 6)

# what the Site Private Feed passes on must also show on the public node list and map, so its sharing follows the forwarding choice
act(adm, "/mesh/c/site-private/forwarding", "/mesh/c/site-private/forwarding", mode="none")
off = psql("SELECT feed_site_map FROM communities WHERE slug='site-private'")
act(adm, "/mesh/c/site-private/forwarding", "/mesh/c/site-private/forwarding", mode="auto")
on = psql("SELECT feed_site_map FROM communities WHERE slug='site-private'")
check("the Site Private Feed shows on the public lists unless forwarding is Nothing", (off, on) == ("f", "t"), (off, on))
psql("DELETE FROM communities WHERE slug IN ('sfv-open', 'site-private')")
psql("DELETE FROM roles WHERE name IN ('SF Watchers', 'SF Others')")
psql("DELETE FROM users WHERE username IN ('sfwatch', 'sfplain')")
print("ALL PASS" if ok_all else "SOME FAILED")
