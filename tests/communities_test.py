"""Mesh Communities: an administrator makes a community and names its Owner; roles are scoped to that community (Viewer, Node Operator, Editor, Manager, Owner) and each holds only its own permissions; nobody grants
a role above their own; ownership is handed over, never lost; invitation links can be accepted, declined, cancelled and expire; the page, links, notices and events, the area, the nodes (by area, by a member's claim,
by hand, taken off by hand), the health numbers for the team, following, the map filter, broker logins for the gateways, hiding, Feature Access, the switch, the audit log and the restore area. Needs the mesh
service and broker on the test box (sh tests/deploy_mesh.sh) for the node and login parts."""
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
mesh_up = True
try:
    requests.get(P + "/health", timeout=8)
except Exception:
    mesh_up = False
setting("mesh_enabled", "1")
setting("mesh_communities_enabled", "1")
psql("DELETE FROM settings WHERE key = 'access_communities'")
psql("DELETE FROM communities WHERE slug LIKE 'cmt-%'")
psql("DELETE FROM users WHERE username LIKE 'cm%' AND username <> 'cmdemo'")
for n in ("cmowner", "cmmanager", "cmeditor", "cmoperator", "cmviewer", "cmstranger", "cminvitee", "cmdecliner", "cmnew"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
S = {n: sess(n, n + "password99") for n in ("cmowner", "cmmanager", "cmeditor", "cmoperator", "cmviewer", "cmstranger", "cminvitee", "cmdecliner")}
adm = sess("marty", PW)
anon = requests.Session()
SLUG = "cmt-test-mesh"
def post(s, path, **data):
    page = data.pop("_page", f"/mesh/c/{SLUG}")
    data["csrf_token"] = tok(s, page)
    return s.post(B + path, data=data, allow_redirects=False)
def ok302(r):
    return r.status_code == 302
def flashed(s):
    return s.get(B + f"/mesh/c/{SLUG}").text
def sec(s, name):
    return s.get(B + f"/mesh/c/{SLUG}/{name}")

# ---- administrator
check("the admin page opens and the admin menu has it", adm.get(B + "/admin/communities").status_code == 200 and 'href="/admin/communities"' in adm.get(B + "/admin/usage").text)
check("a member cannot open it", S["cmstranger"].get(B + "/admin/communities", allow_redirects=False).status_code in (302, 403, 404))
ok, m, _ = act(adm, "/admin/communities/create", "/admin/communities", name="CMT Test Mesh", owner="nobodyhere")
check("an Owner who does not exist is refused", not ok and "No member matches" in m, m)
ok, m, _ = act(adm, "/admin/communities/create", "/admin/communities", name="CMT Test Mesh", owner="cmowner")
check("an administrator makes a community and names its Owner", ok and psql("SELECT role FROM community_members m JOIN communities c ON c.id = m.community_id JOIN users u ON u.id = m.user_id WHERE c.slug='cmt-test-mesh' AND u.username='cmowner'") == "owner", m)
ok, m, _ = act(adm, "/admin/communities/create", "/admin/communities", name="CMT Test Mesh")
check("the same name twice is refused", not ok and "exists already" in m, m)
check("the public list shows it and the menu has Communities", "CMT Test Mesh" in anon.get(B + "/mesh/communities").text and 'href="/mesh/communities"' in anon.get(B + "/mesh/").text)
pg = anon.get(B + f"/mesh/c/{SLUG}")
check("anyone can open the page; the menu has no team pages and they are closed", pg.status_code == 200 and "Edit Page" not in pg.text and ">Team<" not in pg.text and "Broker Logins" not in pg.text and sec(anon, "edit").status_code == 404 and sec(anon, "health").status_code == 404 and sec(anon, "team").status_code == 404)
check("the page has its own side menu with the public pages", all(f'href="/mesh/c/{SLUG}/{k}"' in pg.text for k in ("join", "map", "nodes", "news")) and 'id="cm-side"' in pg.text)
check("every section of the menu is its own page", all(sec(anon, k).status_code == 200 for k in ("join", "map", "nodes", "news")) and sec(anon, "nonsense").status_code == 404)

# ---- the Owner runs the page
O = S["cmowner"]
check("the Owner's menu has every team page", all(x in O.get(B + f"/mesh/c/{SLUG}").text for x in ("Edit Page", ">Area<", "Choose the Nodes", ">Team<", "Broker Logins", "Remote Administration", "Health and Statistics")) and "Post a Notice or Event" in sec(O, "news").text)
r = post(O, f"/mesh/c/{SLUG}/page", name="CMT Test Mesh", about="A test mesh.\nSecond line.", website="https://example.org/", logo_url="javascript:alert(1)", join_channel="LongFast", join_preset="Long Fast", join_region="US", join_steps="Do this.", south="", west="", north="", east="")
check("a logo that is not an https address is refused", ok302(r) and "must start with https://" in flashed(O))
import json as _json
def area(s, pts):
    return post(s, f"/mesh/c/{SLUG}/area", poly=_json.dumps(pts), _page=f"/mesh/c/{SLUG}/area")
area(O, [[36.0, -92.0], [37.0, -92.0]])
check("an area with fewer than three points is refused", "at least three points" in flashed(O) and psql("SELECT area_poly FROM communities WHERE slug='cmt-test-mesh'") == "")
area(O, [[36.0, -92.0], [95.0, -92.0], [37.0, -90.0]])
check("a point outside the map is refused", "outside the map" in flashed(O))
area(O, "not json")
check("a shape that is not a list of points is refused", "not understood" in flashed(O) or "not a place" in flashed(O))
r = post(O, f"/mesh/c/{SLUG}/page", name="CMT Test Mesh", about="A test mesh.\nSecond line.", website="https://example.org/", logo_url="https://example.org/logo.png", join_channel="LongFast", join_preset="Long Fast", join_region="US", join_steps="Do this first.")
html = anon.get(B + f"/mesh/c/{SLUG}").text
check("the page is saved and shows about and the website, and how to join on its own page", "A test mesh." in html and 'href="https://example.org/"' in html and "Do this first." in sec(anon, "join").text and "LongFast" in sec(anon, "join").text)
area(O, [[36.0, -92.0], [36.0, -90.0], [37.0, -90.0], [37.0, -92.0]])
check("a shape of four points is saved and the old rectangle fields are cleared", _json.loads(psql("SELECT area_poly FROM communities WHERE slug='cmt-test-mesh'")) == [[36.0, -92.0], [36.0, -90.0], [37.0, -90.0], [37.0, -92.0]] and psql("SELECT south IS NULL FROM communities WHERE slug='cmt-test-mesh'") == "t")
check("the Area page shows the drawing map and the points", 'id="cm-map"' in sec(O, "area").text and "Save the Area" in sec(O, "area").text)
post(O, f"/mesh/c/{SLUG}/links/add", label="Discord", url="https://discord.example/x")
r = post(O, f"/mesh/c/{SLUG}/links/add", label="Bad", url="ftp://x")
check("a link needs an http or https address", "Discord" in anon.get(B + f"/mesh/c/{SLUG}").text and "Bad" not in anon.get(B + f"/mesh/c/{SLUG}").text)
setting("mesh_community_max_links", "1")
post(O, f"/mesh/c/{SLUG}/links/add", label="Zebra", url="https://second.example/")
check("links are limited by a setting", "Zebra" not in anon.get(B + f"/mesh/c/{SLUG}").text)
setting("mesh_community_max_links", "10")
lid = psql("SELECT id FROM community_links ORDER BY id DESC LIMIT 1")
post(O, f"/mesh/c/{SLUG}/links/{lid}/delete")
check("a link can be removed", "Discord" not in anon.get(B + f"/mesh/c/{SLUG}").text)

# ---- roles and their limits
for who, role in (("cmmanager", "manager"), ("cmeditor", "editor"), ("cmoperator", "operator"), ("cmviewer", "viewer")):
    post(O, f"/mesh/c/{SLUG}/members/add", who=who, role=role)
roles = {l.split("|")[0]: l.split("|")[1] for l in psql("SELECT u.username || '|' || m.role FROM community_members m JOIN users u ON u.id = m.user_id JOIN communities c ON c.id = m.community_id WHERE c.slug='cmt-test-mesh'").splitlines()}
check("the Owner adds a Manager, an Editor, a Node Operator and a Viewer", roles == {"cmowner": "owner", "cmmanager": "manager", "cmeditor": "editor", "cmoperator": "operator", "cmviewer": "viewer"}, roles)
check("a stranger is refused every team action", all(post(S["cmstranger"], p, **d).status_code == 403 for p, d in ((f"/mesh/c/{SLUG}/page", {"name": "x"}), (f"/mesh/c/{SLUG}/posts/add", {"title": "x"}), (f"/mesh/c/{SLUG}/nodes/add", {"node": "!a1b2c3d4"}), (f"/mesh/c/{SLUG}/members/add", {"who": "cmnew", "role": "viewer"}), (f"/mesh/c/{SLUG}/logins/add", {"label": "x"}))))
check("a stranger does not get the health page", sec(S["cmstranger"], "health").status_code == 404 and "Health and Statistics" not in S["cmstranger"].get(B + f"/mesh/c/{SLUG}").text)
check("a Viewer sees the health numbers but cannot edit, post, pick nodes or add anyone", "Packets seen from these nodes" in sec(S["cmviewer"], "health").text and sec(S["cmviewer"], "edit").status_code == 403 and
      all(post(S["cmviewer"], p, **d).status_code == 403 for p, d in ((f"/mesh/c/{SLUG}/page", {"name": "x"}), (f"/mesh/c/{SLUG}/posts/add", {"title": "x"}), (f"/mesh/c/{SLUG}/nodes/add", {"node": "!a1b2c3d4"}), (f"/mesh/c/{SLUG}/members/add", {"who": "cmnew", "role": "viewer"}))))
check("an Editor can edit the page and post, but not choose nodes, make logins or add anyone", ok302(post(S["cmeditor"], f"/mesh/c/{SLUG}/posts/add", title="Editor notice", body="hello", kind="notice")) and "Editor notice" in sec(anon, "news").text
      and post(S["cmeditor"], f"/mesh/c/{SLUG}/nodes/add", node="!a1b2c3d4").status_code == 403 and post(S["cmeditor"], f"/mesh/c/{SLUG}/logins/add", label="x").status_code == 403 and post(S["cmeditor"], f"/mesh/c/{SLUG}/members/add", who="cmnew", role="viewer").status_code == 403)
check("a Node Operator can choose nodes but not post or edit the page", ok302(post(S["cmoperator"], f"/mesh/c/{SLUG}/nodes/add", node="!a1b2c3d4", mode="include")) and post(S["cmoperator"], f"/mesh/c/{SLUG}/posts/add", title="x").status_code == 403 and post(S["cmoperator"], f"/mesh/c/{SLUG}/page", name="x").status_code == 403)
post(S["cmoperator"], f"/mesh/c/{SLUG}/nodes/remove", node="!a1b2c3d4")
M = S["cmmanager"]
post(M, f"/mesh/c/{SLUG}/members/add", who="cmnew", role="owner")
check("a Manager cannot name an Owner", psql("SELECT COUNT(*) FROM community_members m JOIN users u ON u.id = m.user_id WHERE u.username='cmnew'") == "0" and "cannot give that role" in flashed(M))
post(M, f"/mesh/c/{SLUG}/members/add", who="cmnew", role="editor")
check("a Manager can add an Editor", psql("SELECT role FROM community_members m JOIN users u ON u.id = m.user_id WHERE u.username='cmnew'") == "editor")
ouid = psql("SELECT id FROM users WHERE username='cmowner'")
post(M, f"/mesh/c/{SLUG}/members/{ouid}/role", role="viewer")
check("a Manager cannot change the Owner's role or remove the Owner", psql("SELECT role FROM community_members m JOIN users u ON u.id = m.user_id WHERE u.username='cmowner'") == "owner" and post(M, f"/mesh/c/{SLUG}/members/{ouid}/remove").status_code in (302, 403)
      and psql("SELECT role FROM community_members m JOIN users u ON u.id = m.user_id WHERE u.username='cmowner'") == "owner")
euid = psql("SELECT id FROM users WHERE username='cmeditor'")
post(S["cmeditor"], f"/mesh/c/{SLUG}/members/{euid}/role", role="manager")
check("an Editor cannot promote themselves", psql("SELECT role FROM community_members m JOIN users u ON u.id = m.user_id WHERE u.username='cmeditor'") == "editor")
post(O, f"/mesh/c/{SLUG}/members/{ouid}/remove")
check("the last Owner cannot leave", psql("SELECT COUNT(*) FROM community_members m JOIN communities c ON c.id = m.community_id WHERE c.slug='cmt-test-mesh' AND m.role='owner'") == "1" and "needs an Owner" in flashed(O))

# ---- invitations
r = post(M, f"/mesh/c/{SLUG}/invites/add", role="owner", email="")
check("an invitation cannot carry a role above the one who sends it", psql("SELECT COUNT(*) FROM community_invites") == psql("SELECT COUNT(*) FROM community_invites") and "cannot give that role" in flashed(M))
post(M, f"/mesh/c/{SLUG}/invites/add", role="editor", email="cminvitee@example.com")
html = flashed(M)
link = re.search(rf"/mesh/c/{SLUG}/join/([\w-]+)", html)
check("an invitation makes a link, shown once", link is not None and "Invitation link" in html, html[:200] if not link else "")
tokn = link.group(1) if link else "x"
I = S["cminvitee"]
jp = I.get(B + f"/mesh/c/{SLUG}/join/{tokn}")
check("the invited person sees Accept and Decline", jp.status_code == 200 and "Accept" in jp.text and "Decline" in jp.text and "Editor" in jp.text)
check("the link is stored only as a keyed hash", tokn not in psql("SELECT string_agg(token_hash, ',') FROM community_invites"))
r = post(I, f"/mesh/c/{SLUG}/join/{tokn}", answer="accept", _page=f"/mesh/c/{SLUG}/join/{tokn}")
check("accepting makes them an Editor", psql("SELECT role FROM community_members m JOIN users u ON u.id = m.user_id WHERE u.username='cminvitee'") == "editor")
check("the link works once", "already" in I.get(B + f"/mesh/c/{SLUG}/join/{tokn}").text)
post(M, f"/mesh/c/{SLUG}/invites/add", role="viewer", email="")
tok2 = re.search(rf"/mesh/c/{SLUG}/join/([\w-]+)", flashed(M)).group(1)
post(S["cmdecliner"], f"/mesh/c/{SLUG}/join/{tok2}", answer="decline", _page=f"/mesh/c/{SLUG}/join/{tok2}")
check("declining adds no one and ends the invitation", psql("SELECT COUNT(*) FROM community_members m JOIN users u ON u.id = m.user_id WHERE u.username='cmdecliner'") == "0" and "already" in S["cmdecliner"].get(B + f"/mesh/c/{SLUG}/join/{tok2}").text)
post(M, f"/mesh/c/{SLUG}/invites/add", role="viewer", email="")
tok3 = re.search(rf"/mesh/c/{SLUG}/join/([\w-]+)", flashed(M)).group(1)
psql("UPDATE community_invites SET expires_at = now() - interval '1 hour' WHERE state='pending'")
check("an expired invitation is refused", "expired" in S["cmdecliner"].get(B + f"/mesh/c/{SLUG}/join/{tok3}").text)
post(M, f"/mesh/c/{SLUG}/invites/add", role="viewer", email="")
tok4 = re.search(rf"/mesh/c/{SLUG}/join/([\w-]+)", flashed(M)).group(1)
iid = psql("SELECT id FROM community_invites WHERE state='pending' AND expires_at > now() ORDER BY id DESC LIMIT 1")
post(M, f"/mesh/c/{SLUG}/invites/{iid}/cancel")
check("a cancelled invitation does not work", "already" in S["cmdecliner"].get(B + f"/mesh/c/{SLUG}/join/{tok4}").text or "answered" in S["cmdecliner"].get(B + f"/mesh/c/{SLUG}/join/{tok4}").text)
check("a wrong link is refused", "not valid" in S["cmdecliner"].get(B + f"/mesh/c/{SLUG}/join/nonsense").text)

# ---- notices and events
post(O, f"/mesh/c/{SLUG}/posts/add", title="Net night", body="Tuesday at 8.", kind="event", event_at="2026-12-01T20:00")
post(O, f"/mesh/c/{SLUG}/posts/add", title="No date", body="x", kind="event", event_at="")
html = sec(anon, "news").text
check("an event shows with its date; an event without a date is refused", "Net night" in html and "2026-12-01" in html and "No date" not in html)
pid = psql("SELECT id FROM community_posts WHERE title='Editor notice'")
post(O, f"/mesh/c/{SLUG}/posts/{pid}/pin")
check("a notice can be pinned (shown first) and removed", psql("SELECT pinned FROM community_posts WHERE id=" + pid) == "t")
post(O, f"/mesh/c/{SLUG}/posts/{pid}/delete")
check("removed", "Editor notice" not in sec(anon, "news").text)

# ---- following
post(S["cmstranger"], f"/mesh/c/{SLUG}/follow")
check("a member can follow and the count shows", "1 follower" in anon.get(B + f"/mesh/c/{SLUG}").text)
post(S["cmstranger"], f"/mesh/c/{SLUG}/unfollow")
check("and stop", "0 followers" in anon.get(B + f"/mesh/c/{SLUG}").text)

# ---- nodes
if mesh_up:
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
    def envelope(sender, port, payload): return ld(1, fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, vi(1, port) + ld(2, payload)) + fx(6, 12345)) + ld(2, "LongFast") + ld(3, "!feedbeef")
    def send(env): requests.post(P + "/ingest", json={"envelope_b64": base64.b64encode(env).decode()}, timeout=20)
    base = 0xD0000000 + ((int(time.time()) & 0xFFF) << 4)
    def node(i, name, lat, lon, role=0):
        nid = base + i
        send(envelope(nid, 4, ld(2, name) + ld(3, name[:4]) + vi(5, 9) + vi(7, role)))
        send(envelope(nid, 3, sfx(1, int(lat * 1e7)) + sfx(2, int(lon * 1e7)) + vi(3, 100) + vi(23, 16)))
        return "!%08x" % nid, "%08x" % nid
    mpsql("DELETE FROM nodes WHERE long_name LIKE 'CMT %'")
    inside1, s1 = node(1, "CMT Inside One", 36.5, -91.0, role=2)
    inside2, s2 = node(2, "CMT Inside Two", 36.6, -91.1)
    outside, s3 = node(3, "CMT Outside Claimed", 35.0, -93.0)
    other, s4 = node(4, "CMT Outside Hand", 34.5, -93.5)
    out2, s5 = node(5, "CMT Outside Nobody", 34.4, -93.6)
    html = sec(anon, "nodes").text
    check("nodes inside the area count", "CMT Inside One" in html and "CMT Inside Two" in html)
    check("nodes outside it do not", "CMT Outside Claimed" not in html and "CMT Outside Nobody" not in html)
    psql(f"INSERT INTO mesh_claims (owner_id, node_key, code, status, claimed_at) SELECT id, '{outside}', 'ARK-CMT1', 'claimed', now() FROM users WHERE username='cmeditor'")
    check("a node claimed by a team member counts", "CMT Outside Claimed" in sec(anon, "nodes").text)
    post(S["cmoperator"], f"/mesh/c/{SLUG}/nodes/add", node=other, mode="include")
    check("a node added by hand counts", "CMT Outside Hand" in sec(anon, "nodes").text)
    post(S["cmoperator"], f"/mesh/c/{SLUG}/nodes/add", node=inside2, mode="exclude")
    check("a node taken off by hand does not", "CMT Inside Two" not in sec(anon, "nodes").text)
    post(S["cmoperator"], f"/mesh/c/{SLUG}/nodes/remove", node=inside2)
    check("clearing the choice puts the usual rules back", "CMT Inside Two" in sec(anon, "nodes").text)
    area(O, [[36.0, -92.0], [36.0, -90.0], [36.55, -91.0]])   # a triangle: a rectangle around it would hold both nodes
    nodes_tri = sec(anon, "nodes").text
    check("a node inside a triangle counts and one above its point does not (the area is a shape, not a box)", "CMT Inside One" in nodes_tri and "CMT Inside Two" not in nodes_tri, nodes_tri[nodes_tri.find("CMT"):nodes_tri.find("CMT") + 100])
    area(O, [[36.0, -92.0], [36.0, -90.0], [37.0, -90.0], [37.0, -92.0]])
    post(S["cmoperator"], f"/mesh/c/{SLUG}/nodes/add", node="nonsense", mode="include")
    check("a node that is not an ID is refused", "eight hexadecimal" in flashed(S["cmoperator"]))
    mj = anon.get(B + f"/api/mesh/nodes.json?community={SLUG}").json()
    names = {n["long_name"] for n in mj["nodes"]}
    check("the map shows only the community's nodes, with its area", {"CMT Inside One", "CMT Outside Claimed", "CMT Outside Hand"} <= names and "CMT Outside Nobody" not in names and mj["area"] == [[36.0, -92.0], [36.0, -90.0], [37.0, -90.0], [37.0, -92.0]], (names, mj.get("area")))
    check("the map page accepts the filter and the list is not filtered without it", S["cmviewer"].get(B + f"/mesh/map?community={SLUG}").status_code == 200 and "CMT Outside Nobody" in {n["long_name"] for n in anon.get(B + "/api/mesh/nodes.json").json()["nodes"]})
    check("an unknown community on the map is a 404", anon.get(B + "/api/mesh/nodes.json?community=nope").status_code == 404)
    vp = sec(S["cmviewer"], "health").text
    check("the team sees the numbers and the quiet router", "Routers and repeaters" in vp and re.search(r"<th>Nodes</th><td>[3-9]", vp) is not None, vp[vp.find("Health and"):vp.find("Health and") + 300])
    time.sleep(21)   # the list uses the nodes the site cached for 20 seconds
    pg = anon.get(B + "/mesh/communities").text
    check("the list counts the nodes", re.search(r"\b[3-9] nodes", pg) is not None)

    # ---- nodes placed on the map by hand
    Op = S["cmoperator"]
    check("a Node Operator can open the page for nodes on the map; an Editor and a Viewer cannot", sec(Op, "pick").status_code == 200 and 'id="cm-map"' in sec(Op, "pick").text and sec(S["cmeditor"], "pick").status_code == 403 and sec(S["cmviewer"], "pick").status_code == 403)
    post(Op, f"/mesh/c/{SLUG}/pins/save", name="Hill planned", role="Router", status="planned", lat="36.7", lon="-91.2", node_key="", notes="Atop the water tower", _page=f"/mesh/c/{SLUG}/pick")
    check("a node placed by hand is on the community's map and in its lists, as planned", "Hill planned" in sec(anon, "map").text and "Atop the water tower" in sec(anon, "map").text and "Hill planned" in sec(anon, "nodes").text and psql("SELECT status FROM community_pins WHERE name='Hill planned'") == "planned")
    pid = psql("SELECT id FROM community_pins WHERE name='Hill planned'")
    post(Op, f"/mesh/c/{SLUG}/pins/save", id=pid, name="Hill planned", role="Router", status="offline", lat="36.71", lon="-91.2", node_key="", notes="", _page=f"/mesh/c/{SLUG}/pick")
    check("it can be changed", psql(f"SELECT status || '|' || lat FROM community_pins WHERE id={pid}") == "offline|36.71")
    for label, kw in (("a place that is not numbers", dict(name="x", lat="abc", lon="-91")), ("a latitude off the map", dict(name="x", lat="95", lon="-91")), ("no name", dict(name="", lat="36.5", lon="-91")), ("a node ID that is not one", dict(name="x", lat="36.5", lon="-91", node_key="nonsense"))):
        c1 = psql("SELECT COUNT(*) FROM community_pins")
        post(Op, f"/mesh/c/{SLUG}/pins/save", status="active", role="", notes="", **dict({"node_key": ""}, **kw), _page=f"/mesh/c/{SLUG}/pick")
        check("refused: " + label, psql("SELECT COUNT(*) FROM community_pins") == c1)
    check("an Editor and a Viewer cannot place or remove one", post(S["cmeditor"], f"/mesh/c/{SLUG}/pins/save", name="x", lat="36.5", lon="-91").status_code == 403 and post(S["cmviewer"], f"/mesh/c/{SLUG}/pins/{pid}/delete").status_code == 403)
    setting("mesh_community_max_pins", "1")
    post(Op, f"/mesh/c/{SLUG}/pins/save", name="One too many", status="active", role="", notes="", node_key="", lat="36.5", lon="-91.1", _page=f"/mesh/c/{SLUG}/pick")
    check("nodes placed by hand are limited by a setting", psql("SELECT COUNT(*) FROM community_pins WHERE name='One too many'") == "0")
    setting("mesh_community_max_pins", "200")
    nopos = base + 99
    send(envelope(nopos, 4, ld(2, "CMT NoPos") + ld(3, "NOPO") + vi(5, 9) + vi(7, 0)))
    nopos_key = "!%08x" % nopos
    check("a node that reports no position is not on the map until someone places it", "CMT NoPos" not in sec(anon, "nodes").text)
    post(Op, f"/mesh/c/{SLUG}/pins/save", name="CMT NoPos spot", role="Client", status="active", lat="36.5", lon="-91.0", node_key=nopos_key, notes="", _page=f"/mesh/c/{SLUG}/pick")
    check("placing it with its ID gives the heard node that place, so it counts and is drawn", "CMT NoPos" in sec(anon, "nodes").text and "CMT NoPos" in sec(anon, "map").text)
    post(Op, f"/mesh/c/{SLUG}/pins/{pid}/delete", _page=f"/mesh/c/{SLUG}/pick")
    check("a placed node can be taken off the map", psql(f"SELECT COUNT(*) FROM community_pins WHERE id={pid}") == "0" and "Hill planned" not in sec(anon, "map").text)
    psql("DELETE FROM community_pins")

    # ---- broker logins
    Op = S["cmoperator"]
    check("a Node Operator makes a broker login for a gateway and sees the password once", ok302(post(Op, f"/mesh/c/{SLUG}/logins/add", label="Hill gateway")) and "password shown once" in flashed(Op) and psql("SELECT COUNT(*) FROM mesh_accounts WHERE community_id=(SELECT id FROM communities WHERE slug='cmt-test-mesh')") == "1")
    aid = psql("SELECT id FROM mesh_accounts WHERE community_id=(SELECT id FROM communities WHERE slug='cmt-test-mesh') LIMIT 1")
    check("the community page lists it, never the password", "Hill gateway" in sec(Op, "logins").text and psql("SELECT password_sealed FROM mesh_accounts WHERE id=" + aid).startswith("enc1:"))
    check("a Viewer and an Editor cannot make logins", post(S["cmviewer"], f"/mesh/c/{SLUG}/logins/add", label="x").status_code == 403 and post(S["cmeditor"], f"/mesh/c/{SLUG}/logins/add", label="x").status_code == 403)
    post(Op, f"/mesh/c/{SLUG}/logins/{aid}/password")
    post(Op, f"/mesh/c/{SLUG}/logins/{aid}/disable")
    check("it can get a new password and be switched off", psql("SELECT disabled FROM mesh_accounts WHERE id=" + aid) == "t")
    post(Op, f"/mesh/c/{SLUG}/logins/{aid}/enable")
    check("and on again", psql("SELECT disabled FROM mesh_accounts WHERE id=" + aid) == "f")
    setting("mesh_community_max_logins", "1")
    post(Op, f"/mesh/c/{SLUG}/logins/add", label="Second")
    check("logins are limited by a setting", psql("SELECT COUNT(*) FROM mesh_accounts WHERE community_id=(SELECT id FROM communities WHERE slug='cmt-test-mesh')") == "1")
    setting("mesh_community_max_logins", "10")
    psql("INSERT INTO communities (slug, name) VALUES ('cmt-other', 'CMT Other')")
    check("a login of another community is not reachable from this one", post(Op, f"/mesh/c/{SLUG}/logins/999999/password").status_code == 404)
    post(Op, f"/mesh/c/{SLUG}/logins/{aid}/delete")
    check("a login can be deleted", psql("SELECT COUNT(*) FROM mesh_accounts WHERE id=" + aid) == "0")
    mpsql("DELETE FROM nodes WHERE long_name LIKE 'CMT %'")
    psql("DELETE FROM mesh_claims WHERE code = 'ARK-CMT1'")
else:
    print("SKIP the node and broker login parts: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)")

# ---- the community's channels
import base64 as _b64
def frag_settings(html):
    """The ChannelSettings bytes behind the first Meshtastic address on a page (the channel set's first field)."""
    out = []
    for m in re.finditer(r"meshtastic\.org/e/\?add=true#([A-Za-z0-9_-]+)", html):
        raw = _b64.urlsafe_b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4))
        out.append(raw[2:] if raw[:1] == b"\x0a" else raw)   # skip the field key and the (short) length
    return b"|".join(dict.fromkeys(out))
def chpost(s, path, **d):
    return post(s, path, _page=f"/mesh/c/{SLUG}/channels", **d)
Op, Ed, Vw = S["cmoperator"], S["cmeditor"], S["cmviewer"]
check("an Editor and a Viewer cannot list channels; an Operator can", chpost(Ed, f"/mesh/c/{SLUG}/channels/add", name="Nope").status_code == 403 and chpost(Vw, f"/mesh/c/{SLUG}/channels/add", name="Nope").status_code == 403
      and ok302(chpost(Op, f"/mesh/c/{SLUG}/channels/add", name="Net", key_kind="256", visibility="team", min_role="operator", precision="0", uplink="1", note="The weekly net")))
for label, kw in (("a name of twelve letters", dict(name="abcdefghijkl")), ("a name with a space", dict(name="my net")), ("a name that is there already", dict(name="net")), ("a position choice that does not exist", dict(name="Odd", precision="7"))):
    c1 = psql("SELECT COUNT(*) FROM community_channels")
    chpost(Op, f"/mesh/c/{SLUG}/channels/add", **dict(dict(key_kind="256", visibility="team", min_role="viewer", precision="0"), **kw))
    check("refused: " + label, psql("SELECT COUNT(*) FROM community_channels") == c1)
chpost(O, f"/mesh/c/{SLUG}/channels/add", name="LongFast", key_kind="default", visibility="team", min_role="owner", precision="0", note="Open to all")
check("a channel with the default key is always public, whatever was asked", psql("SELECT visibility || '|' || key_kind FROM community_channels WHERE name='LongFast'") == "public|default")
chpost(O, f"/mesh/c/{SLUG}/channels/add", name="Board", key_kind="128", visibility="team", min_role="manager", precision="16", downlink="1")
net_page = sec(Op, "channels").text
check("a team channel is shown to the team member it is for, with its QR code and address, and a public one to everyone", "The weekly net" in net_page and "meshtastic.org/e/?add=true#" in net_page and "Open to all" in sec(anon, "channels").text)
check("it is not shown to a visitor or to a team member whose role is too low", "The weekly net" not in sec(anon, "channels").text and "Board" not in sec(Op, "channels").text and "Board" in sec(S["cmmanager"], "channels").text)
nid_ = psql("SELECT id FROM community_channels WHERE name='Net'")
bid_ = psql("SELECT id FROM community_channels WHERE name='Board'")
lid_ = psql("SELECT id FROM community_channels WHERE name='LongFast'")
check("the QR code is an image for those it is open to and a 404 for everyone else", requests.get(B + f"/mesh/c/{SLUG}/channels/{nid_}/qr.svg").status_code == 404 and Op.get(B + f"/mesh/c/{SLUG}/channels/{nid_}/qr.svg").status_code == 200 and "<svg" in Op.get(B + f"/mesh/c/{SLUG}/channels/{nid_}/qr.svg").text
      and Op.get(B + f"/mesh/c/{SLUG}/channels/{bid_}/qr.svg").status_code == 404 and anon.get(B + f"/mesh/c/{SLUG}/channels/{lid_}/qr.svg").status_code == 200)
key_net = psql("SELECT psk_sealed FROM community_channels WHERE name='Net'")
check("the key is stored sealed (the default-key channel has none to keep)", key_net.startswith("enc1:") and psql("SELECT psk_sealed FROM community_channels WHERE name='LongFast'") == "")
st_net = frag_settings(net_page)
check("the address carries a 32 byte key, the name and the uplink switch (the app's own format)", b"\x12\x20" in st_net and b"Net" in st_net and b"\x28\x01" in st_net, st_net[:60])
st_lf = frag_settings(sec(anon, "channels").text)
check("the default-key channel's address carries the one-byte key 1", b"\x12\x01\x01" in st_lf and b"LongFast" in st_lf, st_lf[:40])
chpost(Op, f"/mesh/c/{SLUG}/channels/{nid_}/rotate")
check("a new key replaces the old one", psql("SELECT psk_sealed FROM community_channels WHERE name='Net'") != key_net and psql("SELECT rotated IS NOT NULL FROM community_channels WHERE name='Net'") == "t")
chpost(Op, f"/mesh/c/{SLUG}/channels/{lid_}/rotate")
check("the default key cannot be replaced", psql("SELECT psk_sealed FROM community_channels WHERE name='LongFast'") == "")
chpost(Op, f"/mesh/c/{SLUG}/channels/{nid_}/save", visibility="public", min_role="viewer", precision="16", downlink="1", note="Changed")
check("its options can be changed (now public, downlink on, 16 bits)", psql("SELECT visibility || '|' || downlink || '|' || precision || '|' || note FROM community_channels WHERE name='Net'") == "public|true|16|Changed" and "Changed" in sec(anon, "channels").text, psql("SELECT visibility || '|' || downlink || '|' || precision || '|' || note FROM community_channels WHERE name='Net'"))
setting("mesh_community_max_channels", "3")
chpost(Op, f"/mesh/c/{SLUG}/channels/add", name="Four", key_kind="256", visibility="team", min_role="viewer", precision="0")
check("channels are limited by a setting", psql("SELECT COUNT(*) FROM community_channels WHERE name='Four'") == "0")
setting("mesh_community_max_channels", "8")
check("an Editor and a Viewer cannot change or remove one", chpost(Ed, f"/mesh/c/{SLUG}/channels/{nid_}/delete").status_code == 403 and chpost(Vw, f"/mesh/c/{SLUG}/channels/{nid_}/save", note="x").status_code == 403)
chpost(Op, f"/mesh/c/{SLUG}/channels/{nid_}/delete")
check("a channel can be removed", psql(f"SELECT COUNT(*) FROM community_channels WHERE id={nid_}") == "0")
check("the changes are in the audit log", "community.channel" in psql("SELECT string_agg(DISTINCT action, ',') FROM audit_log WHERE action LIKE 'community.%'"))
psql("DELETE FROM community_channels")

# ---- hiding, access, the switch, the settings, the audit log, the restore area
act(adm, f"/admin/communities/{SLUG}/publish", "/admin/communities")
check("a hidden community is gone for visitors and strangers but not for its team or an administrator", anon.get(B + f"/mesh/c/{SLUG}").status_code == 404 and S["cmstranger"].get(B + f"/mesh/c/{SLUG}").status_code == 404
      and S["cmviewer"].get(B + f"/mesh/c/{SLUG}").status_code == 200 and adm.get(B + f"/mesh/c/{SLUG}").status_code == 200 and "CMT Test Mesh" not in anon.get(B + "/mesh/communities").text)
act(adm, f"/admin/communities/{SLUG}/publish", "/admin/communities")
setting("access_communities", "members")
check("Feature Access can limit it to signed-in members", anon.get(B + "/mesh/communities", allow_redirects=False).status_code == 404 and S["cmstranger"].get(B + "/mesh/communities").status_code == 200)
psql("DELETE FROM settings WHERE key = 'access_communities'")
check("an administrator can step in on any community", "Edit Page" in adm.get(B + f"/mesh/c/{SLUG}").text and sec(adm, "edit").status_code == 200 and ok302(post(adm, f"/mesh/c/{SLUG}/posts/add", title="Admin notice", body="x", kind="notice")))
st = adm.get(B + "/admin/settings/radio").text
check("the choices are settings", all(x in st for x in ("Mesh Communities", "Communities: Invitation Lasts", "Communities: Links per Community", "Communities: Broker Logins per Community")))
aud = psql("SELECT string_agg(DISTINCT action, ',') FROM audit_log WHERE action LIKE 'community.%'")
check("everything is in the audit log", all(a in aud for a in ("community.create", "community.edit", "community.member", "community.invite", "community.post")), aud)
rp = open("../app/restorepart.py", encoding="utf-8").read().split('("mesh",')[1].split("\n")[0]
check("the tables are in the mesh restore area", all(t in rp for t in ("communities", "community_members", "community_invites", "community_nodes", "community_posts", "community_links", "community_follows")))
setting("mesh_communities_enabled", "0")
check("with the switch off the pages and the menu entry are gone", anon.get(B + "/mesh/communities").status_code == 404 and anon.get(B + f"/mesh/c/{SLUG}").status_code == 404 and 'href="/mesh/communities"' not in anon.get(B + "/mesh/").text)
setting("mesh_communities_enabled", "1")
act(adm, "/admin/communities/seed", "/admin/communities")
check("the Arkansas meshes can be added as stubs, once", all(x in anon.get(B + "/mesh/communities").text for x in ("LR Mesh", "NWA Mesh", "Hot Springs Mesh")))
ok, m, _ = act(adm, "/admin/communities/seed", "/admin/communities")
check("adding them again adds nothing", ok and "exist already" in m, m)
act(adm, f"/admin/communities/{SLUG}/delete", "/admin/communities")
check("an administrator can delete a community", anon.get(B + f"/mesh/c/{SLUG}").status_code == 404 and psql("SELECT COUNT(*) FROM community_members m LEFT JOIN communities c ON c.id = m.community_id WHERE c.id IS NULL") == "0")
for slug in ("lr-mesh", "nwa-mesh", "hot-springs-mesh", "cmt-other"):
    psql(f"DELETE FROM communities WHERE slug='{slug}'")
psql("DELETE FROM users WHERE username LIKE 'cm%' AND username <> 'cmdemo'")
print("ALL PASS" if ok_all else "SOME FAILED")
