"""Remote administration of mesh nodes through a community's Admin Bridge: a Manager makes a bridge (token shown once, kept only as a hash); a Node Operator queues commands for the community's own nodes; reading
goes straight through and anything that changes a node waits for a Manager or the Owner; strict parameters; the bridge asks with its token, is handed the approved commands once, and reports each result; rate
limit, expiry, cancel, reject, new token, the switch and the audit log; and the bridge program itself (it rebuilds every command line from a whitelist and refuses anything else). Needs the mesh service on the
test box for the community's nodes (sh tests/deploy_mesh.sh)."""
import base64, importlib.util, json, os, re, struct, subprocess, time, requests
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
except Exception as e:
    print("SKIP meshbridge_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)
spec = importlib.util.spec_from_file_location("bridgeprog", "../tools/admin_bridge/arksignal_admin_bridge.py")
prog = importlib.util.module_from_spec(spec); spec.loader.exec_module(prog)

setting("mesh_enabled", "1"); setting("mesh_communities_enabled", "1"); setting("mesh_bridge_enabled", "1"); setting("mesh_bridge_polls_per_minute", "30"); setting("mesh_bridge_cmd_expire_hours", "24")
psql("DELETE FROM communities WHERE slug LIKE 'cmb-%'")
psql("DELETE FROM users WHERE username LIKE 'cmb%'")
names = ("cmbowner", "cmbmanager", "cmboperator", "cmbeditor", "cmbviewer")
for n in names:
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
S = {n: sess(n, n + "password99") for n in names}
adm = sess("marty", PW)
anon = requests.Session()
SLUG = "cmb-bridge-mesh"
def post(s, path, **data):
    data["csrf_token"] = tok(s, f"/mesh/c/{SLUG}")
    return s.post(B + path, data=data, allow_redirects=False)
def page(s, sec=""): return s.get(B + f"/mesh/c/{SLUG}" + ("/" + sec if sec else "")).text

act(adm, "/admin/communities/create", "/admin/communities", name="CMB Bridge Mesh", owner="cmbowner")
O = S["cmbowner"]
post(O, f"/mesh/c/{SLUG}/page", name="CMB Bridge Mesh", about="x", website="", logo_url="", join_steps="")
post(O, f"/mesh/c/{SLUG}/area", poly=json.dumps([[34.0, -93.0], [34.0, -92.8], [34.2, -92.8], [34.2, -93.0]]), _page=f"/mesh/c/{SLUG}/area")
for who, role in (("cmbmanager", "manager"), ("cmboperator", "operator"), ("cmbeditor", "editor"), ("cmbviewer", "viewer")):
    post(O, f"/mesh/c/{SLUG}/members/add", who=who, role=role)

# the community's nodes
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
base = 0xE0000000 + ((int(time.time()) & 0xFFF) << 4)
def node(i, name, lat, lon):
    nid = base + i
    send(envelope(nid, 4, ld(2, name) + ld(3, name[:4]) + vi(5, 9) + vi(7, 0)))
    send(envelope(nid, 3, sfx(1, int(lat * 1e7)) + sfx(2, int(lon * 1e7)) + vi(3, 100) + vi(23, 16)))
    return "!%08x" % nid
mpsql("DELETE FROM nodes WHERE long_name LIKE 'CMB %'")
IN = node(1, "CMB Inside", 34.1, -92.9)
OUT = node(2, "CMB Outside", 36.0, -90.0)

# ---- bridges
check("a Node Operator, an Editor and a Viewer cannot make a bridge", all(post(S[n], f"/mesh/c/{SLUG}/bridges/add", name="x").status_code == 403 for n in ("cmboperator", "cmbeditor", "cmbviewer")))
check("only the team sees Remote Administration (a Viewer and an Editor do not)", "Remote Administration" in page(S["cmbmanager"]) and "Remote Administration" in page(S["cmboperator"]) and "Remote Administration" not in page(S["cmbviewer"]) and "Remote Administration" not in page(S["cmbeditor"]) and "Remote Administration" not in page(anon))
post(S["cmbmanager"], f"/mesh/c/{SLUG}/bridges/add", name="Hill computer")
html = page(S["cmbmanager"])
TOKEN = (re.search(r"Token for the bridge Hill computer \(shown once, copy it now\): ([\w-]+)", html) or [None, ""])[1]
check("a Manager makes a bridge and sees its token once", len(TOKEN) > 30 and "Hill computer" in html and TOKEN not in page(S["cmbmanager"]), html[:100])
check("the token is stored only as a keyed hash", TOKEN not in psql("SELECT string_agg(token_hash, ',') FROM mesh_bridges") and re.search(r"^[0-9a-f]{64}$", psql("SELECT token_hash FROM mesh_bridges LIMIT 1")))
bid = psql("SELECT id FROM mesh_bridges WHERE name='Hill computer'")
setting("mesh_bridge_max", "1")
post(S["cmbmanager"], f"/mesh/c/{SLUG}/bridges/add", name="Second")
check("bridges are limited by a setting", psql("SELECT COUNT(*) FROM mesh_bridges") == "1")
setting("mesh_bridge_max", "3")

# ---- queueing
Op, Mg = S["cmboperator"], S["cmbmanager"]
def queue(s, kind, node=IN, **p):
    post(s, f"/mesh/c/{SLUG}/commands/add", bridge=bid, node=node, kind=kind, **p)
def last_state():
    return psql("SELECT state FROM mesh_commands ORDER BY id DESC LIMIT 1")
n0 = int(psql("SELECT COUNT(*) FROM mesh_commands"))
queue(Op, "info")
check("reading a node's settings goes straight through", last_state() == "approved")
queue(Op, "set_role", role="ROUTER")
check("a command that changes a node waits for approval", last_state() == "pending")
cid = psql("SELECT id FROM mesh_commands ORDER BY id DESC LIMIT 1")
check("a Node Operator cannot approve it", post(Op, f"/mesh/c/{SLUG}/commands/{cid}/approve").status_code == 403 and last_state() == "pending")
check("an Editor and a Viewer cannot queue", post(S["cmbeditor"], f"/mesh/c/{SLUG}/commands/add", bridge=bid, node=IN, kind="info").status_code == 403 and post(S["cmbviewer"], f"/mesh/c/{SLUG}/commands/add", bridge=bid, node=IN, kind="info").status_code == 403)
queue(Op, "info", node=OUT)
check("a node outside the community is refused", int(psql("SELECT COUNT(*) FROM mesh_commands")) == n0 + 2 and "not one of this community" in page(Op, "remote"))
for label, kind, p in (("a role that does not exist", "set_role", {"role": "KING"}), ("a name with a shell character", "set_owner", {"long_name": "x; rm -rf", "short_name": "ab"}), ("a channel number out of range", "set_channel_name", {"index": "9", "name": "abc"}),
                       ("a reboot delay out of range", "reboot", {"seconds": "5000"}), ("an unknown command", "format_disk", {})):
    c1 = int(psql("SELECT COUNT(*) FROM mesh_commands"))
    queue(Op, kind, **p)
    check("refused: " + label, int(psql("SELECT COUNT(*) FROM mesh_commands")) == c1)
queue(Mg, "set_owner", long_name="Hill Router", short_name="HILL")
check("a Manager's own change needs no second person", last_state() == "approved" and psql("SELECT decided_by IS NOT NULL FROM mesh_commands ORDER BY id DESC LIMIT 1") == "t")
post(Mg, f"/mesh/c/{SLUG}/commands/{cid}/reject")
check("a Manager can reject a waiting command", psql(f"SELECT state FROM mesh_commands WHERE id={cid}") == "rejected")
queue(Op, "reboot", seconds="10")
cid2 = psql("SELECT id FROM mesh_commands ORDER BY id DESC LIMIT 1")
post(Mg, f"/mesh/c/{SLUG}/commands/{cid2}/approve")
check("a Manager approves a command another member asked for", psql(f"SELECT state FROM mesh_commands WHERE id={cid2}") == "approved" and psql(f"SELECT u.username FROM mesh_commands m JOIN users u ON u.id = m.decided_by WHERE m.id={cid2}") == "cmbmanager")
queue(Op, "set_channel_name", index="1", name="Hill")
cid3 = psql("SELECT id FROM mesh_commands ORDER BY id DESC LIMIT 1")
post(Op, f"/mesh/c/{SLUG}/commands/{cid3}/cancel")
check("the one who asked can cancel while it waits", psql(f"SELECT state FROM mesh_commands WHERE id={cid3}") == "cancelled")
check("the list shows who, what and the state", all(x in page(Mg, "remote") for x in ("cmboperator", "reboot in 10 seconds", "rename to Hill Router", "rejected", "cancelled")))

# ---- the bridge's side
def api(path, token=TOKEN, body=None):
    h = {"Authorization": "Bearer " + token} if token is not None else {}
    return requests.post(B + path, json=body or {}, headers=h, timeout=20)
check("no token and a wrong token are refused", api("/api/mesh/bridge/poll", token=None).status_code == 401 and api("/api/mesh/bridge/poll", token="nonsense").status_code == 401)
r = api("/api/mesh/bridge/poll")
cmds = r.json().get("commands", [])
check("the bridge is handed the approved commands in order, with strict fields", r.status_code == 200 and [c["kind"] for c in cmds] == ["info", "set_owner", "reboot"] and cmds[1]["params"] == {"long_name": "Hill Router", "short_name": "HILL"} and r.json()["poll_seconds"] == 30, r.text[:300])
check("they are marked sent and not handed out twice", api("/api/mesh/bridge/poll").json()["commands"] == [] and psql(f"SELECT state FROM mesh_commands WHERE id={cmds[0]['id']}") == "sent")
check("the site records when the bridge last asked", psql("SELECT last_seen IS NOT NULL FROM mesh_bridges WHERE id=" + bid) == "t")
check("a result for a command never handed over is refused", api("/api/mesh/bridge/result", body={"id": int(cid), "ok": True, "output": "x"}).status_code == 404)
r = api("/api/mesh/bridge/result", body={"id": cmds[0]["id"], "ok": True, "output": "Owner: Hill Router\nRole: ROUTER"})
check("the bridge reports a result", r.status_code == 200 and psql(f"SELECT state FROM mesh_commands WHERE id={cmds[0]['id']}") == "done" and "Role: ROUTER" in page(Mg, "remote"))
api("/api/mesh/bridge/result", body={"id": cmds[1]["id"], "ok": False, "output": "The node did not answer."})
check("a failure is kept as failed with its text", psql(f"SELECT state FROM mesh_commands WHERE id={cmds[1]['id']}") == "failed" and "did not answer" in page(Mg, "remote"))
check("a result cannot be sent twice", api("/api/mesh/bridge/result", body={"id": cmds[0]["id"], "ok": True, "output": "again"}).status_code == 404)
check("both are in the audit log", psql("SELECT COUNT(*) FROM audit_log WHERE action='bridge.command'") != "0" and psql("SELECT COUNT(*) FROM audit_log WHERE action='bridge.result'") != "0")

# ---- expiry and the rate limit
queue(Op, "info")
psql("UPDATE mesh_commands SET requested_at = now() - interval '2 days' WHERE state='approved'")
check("a command not taken within the time is dropped, not run", api("/api/mesh/bridge/poll").json()["commands"] == [] and psql("SELECT state FROM mesh_commands ORDER BY id DESC LIMIT 1") == "expired")
setting("mesh_bridge_polls_per_minute", "2")
codes = [api("/api/mesh/bridge/poll").status_code for _ in range(5)]
check("a bridge that asks too often is told to slow down", 429 in codes, codes)
setting("mesh_bridge_polls_per_minute", "30")

# ---- the program
class A: server = B; host = "10.0.0.5"; port = None; timeout = 5; dry_run = False
queue(Op, "info")
queue(Mg, "set_role", role="REPEATER")
queue(Mg, "set_channel_name", index="2", name="Valley")
time.sleep(61)   # the per-minute count of the last block is over
ran = []
def fake(argv):
    ran.append(argv); return 0, "ok: " + " ".join(argv[-2:])
n, wait = prog.poll_once(A, TOKEN, runner=fake)
check("the program asks, runs each command through meshtastic with the node as the destination and reports", n == 3 and ran[0] == ["meshtastic", "--host", "10.0.0.5", "--dest", IN, "--info"] and ran[1] == ["meshtastic", "--host", "10.0.0.5", "--dest", IN, "--set", "device.role", "REPEATER"]
      and ran[2] == ["meshtastic", "--host", "10.0.0.5", "--dest", IN, "--ch-index", "2", "--ch-set", "name", "Valley"] and wait == 30, (n, ran))
check("the results arrived", psql("SELECT string_agg(state, ',' ORDER BY id) FROM (SELECT id, state FROM mesh_commands ORDER BY id DESC LIMIT 3) x") == "done,done,done" and "ok: --ch-set name" not in "x")
for label, cmd in (("a node that is not a node ID", {"node": "x; ls", "kind": "info"}), ("an unknown command", {"node": IN, "kind": "shell", "params": {}}), ("a role that is not a role", {"node": IN, "kind": "set_role", "params": {"role": "ROUTER; reboot"}}),
                   ("a name with a quote", {"node": IN, "kind": "set_owner", "params": {"long_name": "a'b", "short_name": "ab"}}), ("a boolean for a number", {"node": IN, "kind": "set_channel_name", "params": {"index": True, "name": "a"}})):
    ok, text = prog.run_command(cmd, A, runner=fake)
    check("the program refuses " + label, not ok and "Refused by the bridge" in text, text)
A.dry_run = True
ok, text = prog.run_command({"node": IN, "kind": "info"}, A)
check("a dry run only shows the command line", ok and text.startswith("Dry run, not run: meshtastic"))
A.dry_run = False
ok, text = prog.run_command({"node": IN, "kind": "info"}, A, runner=lambda a: (1, "boom"))
check("a command line that fails is reported as failed with its output", not ok and text == "boom")

# ---- new token, the switch, settings
post(Mg, f"/mesh/c/{SLUG}/bridges/{bid}/token")
check("a new token makes the old one stop working", api("/api/mesh/bridge/poll").status_code == 401)
NEW = (re.search(r"New token for the bridge Hill computer \(shown once, copy it now\): ([\w-]+)", page(Mg)) or [None, ""])[1]
check("and the new one work", len(NEW) > 30 and api("/api/mesh/bridge/poll", token=NEW).status_code == 200)
setting("mesh_bridge_enabled", "0")
check("with bridges switched off the site refuses the bridge and hides the section", api("/api/mesh/bridge/poll", token=NEW).status_code == 401 and "Remote Administration" not in page(Mg))
setting("mesh_bridge_enabled", "1")
st = adm.get(B + "/admin/settings/radio").text
check("the choices are settings", all(x in st for x in ("Admin Bridges for Remote Administration", "Admin Bridges per Community", "Admin Bridge: A Command Expires After", "Admin Bridge: Commands per Request", "Admin Bridge: Most Requests per Minute")))
rp = open("../app/restorepart.py", encoding="utf-8").read().split('("mesh",')[1].split("\n")[0]
check("the tables are in the mesh restore area", "mesh_bridges" in rp and "mesh_commands" in rp)
post(Mg, f"/mesh/c/{SLUG}/bridges/{bid}/delete")
check("removing a bridge removes its commands", psql("SELECT COUNT(*) FROM mesh_commands WHERE bridge_id=" + bid) == "0")
act(adm, f"/admin/communities/{SLUG}/delete", "/admin/communities")
mpsql("DELETE FROM nodes WHERE long_name LIKE 'CMB %'")
psql("DELETE FROM users WHERE username LIKE 'cmb%'")
print("ALL PASS" if ok_all else "SOME FAILED")
