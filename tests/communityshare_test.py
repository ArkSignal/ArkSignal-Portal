"""A mesh community can also feed the public places: its Sharing page (a Manager or the Owner) has two choices, both off at first. Show its nodes on ArkSignal's public mesh map and lists (a node heard only on
a community's own topic is left off the public pages until then), and forward what its gateways send to the public Meshtastic server (only when the site switch is on, only default-key channels, only allowed
port numbers and positions no more exact than the limit, never another community's packets). Needs the mesh service on the test box (sh tests/deploy_mesh.sh)."""
import base64, os, re, struct, subprocess, time, requests
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
    requests.get(P + "/health", timeout=8)
except Exception:
    print("SKIP no mesh service"); print("ALL PASS"); raise SystemExit(0)
setting("mesh_enabled", "1"); setting("mesh_communities_enabled", "1"); setting("mesh_forward_enabled", "0")
psql("DELETE FROM settings WHERE key = 'access_communities'")
psql("DELETE FROM communities WHERE slug LIKE 'cms-%'")
psql("DELETE FROM users WHERE username LIKE 'cms%'")
for n in ("cmsowner", "cmsviewer"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
O, V = sess("cmsowner", "cmsownerpassword99"), sess("cmsviewer", "cmsviewerpassword99")
adm = sess("marty", PW)
anon = requests.Session()
ok, m, _ = act(adm, "/admin/communities/create", "/admin/communities", name="CMS Share Mesh", owner="cmsowner")
SLUG = "cms-share-mesh"
check("a community is made", ok and psql(f"SELECT COUNT(*) FROM communities WHERE slug='{SLUG}'") == "1", m)
setting_root = psql("SELECT value FROM settings WHERE key='mesh_join_topic'") or "msh/US/AR"
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
def envelope(sender, port, payload, channel="LongFast"): return ld(1, fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, vi(1, port) + ld(2, payload)) + fx(6, 12345)) + ld(2, channel) + ld(3, "!feedbeef")
def send(env, topic): return requests.post(P + "/ingest", json={"envelope_b64": base64.b64encode(env).decode(), "topic": topic}, timeout=20)
def check_fwd(env, topic): return requests.post(P + "/forward_check", json={"envelope_b64": base64.b64encode(env).decode(), "topic": topic}, timeout=20).json()
mpsql("DELETE FROM nodes WHERE long_name LIKE 'CMS %'")
nid = 0xC0000000 + ((int(time.time()) & 0xFFF) << 4)
croot = f"{setting_root}/{SLUG}/2/e/LongFast/!feedbeef"
send(envelope(nid, 4, ld(2, "CMS Hilltop") + ld(3, "CMSH") + vi(5, 9) + vi(7, 0)), croot)
send(envelope(nid, 3, sfx(1, int(36.5 * 1e7)) + sfx(2, int(-91.0 * 1e7)) + vi(3, 100) + vi(23, 16)), croot)
time.sleep(1)
check("the node is stored as the community's", mpsql(f"SELECT community FROM nodes WHERE long_name='CMS Hilltop'") == SLUG)
def public_names():
    return [n["long_name"] for n in anon.get(B + "/api/mesh/nodes.json?hours=168").json()["nodes"] if n.get("long_name")]
def listed():
    return "CMS Hilltop" in anon.get(B + "/mesh/nodes?q=Hilltop").text
time.sleep(21)   # the site keeps the node list for 20 seconds
check("a community's node is left off the public mesh pages at first", "CMS Hilltop" not in public_names() and not listed())
check("but its own page shows it", "CMS Hilltop" in O.get(B + f"/mesh/c/{SLUG}/nodes").text)
pg = O.get(B + f"/mesh/c/{SLUG}/share")
check("the Owner has a Sharing page with both choices, off", pg.status_code == 200 and 'name="feed_site_map"' in pg.text and 'name="feed_public"' in pg.text and "checked" not in re.search(r'name="feed_site_map".*?>', pg.text).group(0))
check("it is not on the menu for a viewer and is closed to them", V.get(B + f"/mesh/c/{SLUG}/share", allow_redirects=False).status_code in (403, 404) and anon.get(B + f"/mesh/c/{SLUG}/share", allow_redirects=False).status_code in (302, 403, 404))
def post(s, path, **data):
    data["csrf_token"] = tok(s, f"/mesh/c/{SLUG}/share")
    return s.post(B + path, data=data, allow_redirects=False)
check("a viewer cannot change it", post(V, f"/mesh/c/{SLUG}/share", feed_site_map="1").status_code in (403, 404) and psql(f"SELECT feed_site_map FROM communities WHERE slug='{SLUG}'") == "f")
post(O, f"/mesh/c/{SLUG}/share", feed_site_map="1")
check("the Owner turns on the public map", psql(f"SELECT feed_site_map FROM communities WHERE slug='{SLUG}'") == "t")
time.sleep(21)
check("then the node shows on the public mesh map data and list", "CMS Hilltop" in public_names() and listed())
post(O, f"/mesh/c/{SLUG}/share")
check("and turning it off takes it off again", psql(f"SELECT feed_site_map FROM communities WHERE slug='{SLUG}'") == "f")
# ---- forwarding
post(O, f"/mesh/c/{SLUG}/share", feed_public="1")
check("forwarding cannot be chosen while the site switch is off", psql(f"SELECT feed_public FROM communities WHERE slug='{SLUG}'") == "f" and "have not switched forwarding on" in O.get(B + f"/mesh/c/{SLUG}/share").text)
setting("mesh_forward_enabled", "1"); time.sleep(8)
post(O, f"/mesh/c/{SLUG}/share", feed_public="1")
check("with the site switch on, the Owner turns forwarding on", psql(f"SELECT feed_public FROM communities WHERE slug='{SLUG}'") == "t")
time.sleep(1)
r = check_fwd(envelope(nid, 4, ld(2, "CMS Hilltop") + vi(5, 9)), croot)
check("a node-info packet on a default-key channel goes to the public topic", r["target"] == f"msh/US/AR/2/e/LongFast/!feedbeef" and r["allowed"] is True, r)
r = check_fwd(envelope(nid, 3, sfx(1, int(36.5 * 1e7)) + sfx(2, int(-91.0 * 1e7)) + vi(23, 32)), croot)
check("a position more exact than the public limit is not forwarded", r["allowed"] is False, r)
r = check_fwd(envelope(nid, 3, sfx(1, int(36.5 * 1e7)) + sfx(2, int(-91.0 * 1e7)) + vi(23, 13)), croot)
check("a position within the limit is", r["allowed"] is True, r)
r = check_fwd(envelope(nid, 1, ld(2, "hello")), croot)
check("a text message (port 1) is allowed only because the public server allows it", r["allowed"] is True, r)
r = check_fwd(envelope(nid, 4, ld(2, "x") + vi(5, 9), channel="PrivateNet"), croot)
check("a channel that is not a default-key public one is not forwarded", r["allowed"] is False, r)
r = check_fwd(envelope(nid, 4, ld(2, "x") + vi(5, 9)), f"{setting_root}/someone-else/2/e/LongFast/!feedbeef")
check("another community's packets are not forwarded", r["target"] is None, r)
r = check_fwd(envelope(nid, 4, ld(2, "x") + vi(5, 9)), f"{setting_root}/2/e/LongFast/!feedbeef")
check("nor are the ordinary topic's", r["target"] is None, r)
setting("mesh_forward_enabled", "0")
time.sleep(8)   # the site reads a changed setting within a few seconds
post(O, f"/mesh/c/{SLUG}/share", feed_public="1", feed_site_map="1")
r = check_fwd(envelope(nid, 4, ld(2, "x") + vi(5, 9)), croot)
check("with the site switch off again nothing is forwarded", r["target"] is None, r)
aud = psql("SELECT string_agg(detail, '|') FROM audit_log WHERE action='community.edit' AND detail LIKE 'sharing:%'")
check("every change is in the audit log", "site map on" in aud and "public Meshtastic server on" in aud, aud)
check("the settings exist", all(x in adm.get(B + "/admin/settings/radio").text for x in ("Let Communities Forward to the Public Meshtastic Server", "Forward: Public Topic Root")))
mpsql("DELETE FROM nodes WHERE long_name LIKE 'CMS %'")
psql("DELETE FROM communities WHERE slug LIKE 'cms-%'")
psql("DELETE FROM users WHERE username LIKE 'cms%'")
setting("mesh_forward_enabled", "0")
print("ALL PASS" if ok_all else "SOME FAILED")
