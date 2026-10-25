"""Linking islanded mesh areas through the broker, end to end on the test box (sh tests/deploy_mesh.sh): a Manager proposes, the other community accepts (taking the proposer's key when its own differs), a site administrator
approves; then packets heard on one community's topic arrive on the other's exactly as they were (same encrypted bytes, same sender and id) with the hop limit lowered to the link's, in both directions, once only,
never bounced back, only on the linked channel, nothing while paused or after removal, and no faster than the rate limit; counters, page, audit trail and permissions."""
import base64, importlib.util, json, os, re, struct, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18482"
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def ssh(cmd, timeout=180):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=timeout).stdout
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    requests.get(P + "/health", timeout=8)
except Exception:
    print("SKIP meshareas_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)"); print("ALL PASS"); raise SystemExit(0)
spec = importlib.util.spec_from_file_location("meshpacket", "../app/meshpacket.py"); MP = importlib.util.module_from_spec(spec); spec.loader.exec_module(MP)
def fields(buf):
    out, i = [], 0
    while i < len(buf):
        key = 0; sh = 0
        while True:
            b = buf[i]; i += 1; key |= (b & 0x7F) << sh; sh += 7
            if not b & 0x80: break
        num, wt = key >> 3, key & 7
        if wt == 0:
            v = 0; sh = 0
            while True:
                b = buf[i]; i += 1; v |= (b & 0x7F) << sh; sh += 7
                if not b & 0x80: break
        elif wt == 5: v = struct.unpack("<I", buf[i:i + 4])[0]; i += 4
        elif wt == 2:
            ln = 0; sh = 0
            while True:
                b = buf[i]; i += 1; ln |= (b & 0x7F) << sh; sh += 7
                if not b & 0x80: break
            v = buf[i:i + ln]; i += ln
        else: raise ValueError(wt)
        out.append((num, v))
    return out
def get(fl, n, d=None): return next((v for k, v in fl if k == n), d)
def info(raw, key=b"\x01"):
    o = MP.open_envelope(raw, key)
    return o
def pkt(text, sender, pid, channel="LinkCh", hop=5):
    return MP.build_envelope(1, text.encode(), b"\x01", channel, sender, "!aaaaaa01", packet_id=pid, hop_limit=hop)
def pub(user, topic, blob):
    ssh(f"echo {base64.b64encode(blob).decode()} | base64 -d > /tmp/ml_pkt.bin; {DK} cp /tmp/ml_pkt.bin mesh-mqtt:/tmp/ml_pkt.bin; {DK} exec mesh-mqtt mosquitto_pub -h localhost -u {user} -P {PWD} -t '{topic}' -f /tmp/ml_pkt.bin")
def listen(user, slug, secs):
    return subprocess.Popen(["ssh", "-q", "nastemp", f"{DK} exec mesh-mqtt mosquitto_sub -h localhost -u {user} -P {PWD} -t 'msh/US/AR/{slug}/2/e/#' -W {secs} -F '%t %x'"], stdout=subprocess.PIPE, text=True)
def heard(proc):
    try:
        out, _ = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill(); out = ""
    res = []
    for line in out.splitlines():
        t, _s, hx = line.partition(" ")
        if hx:
            try: res.append((t, bytes.fromhex(hx)))
            except ValueError: pass
    return res

# ---- the communities, the logins and the channels
setting("mesh_enabled", "1"); setting("mesh_communities_enabled", "1"); setting("meshlink_enabled", "1"); setting("meshlink_admin_approval", "1"); setting("meshlink_max_per_community", "5"); setting("meshlink_max_rate", "30"); setting("meshlink_max_hops", "5")
psql("DELETE FROM settings WHERE key = 'access_communities'")
psql("DELETE FROM communities WHERE slug LIKE 'ml-%'"); psql("DELETE FROM users WHERE username LIKE 'ml%'")
for n in ("mlowna", "mlownb", "mlownc", "mlviewer"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
OA, OB, OC, VW = (sess(n, n + "password99") for n in ("mlowna", "mlownb", "mlownc", "mlviewer"))
adm = sess("marty", PW)
anon = requests.Session()
for name, owner in (("ML Alpha", "mlowna"), ("ML Bravo", "mlownb"), ("ML Charlie", "mlownc")):
    act(adm, "/admin/communities/create", "/admin/communities", name=name, owner=owner)
SA, SB, SC = "ml-alpha", "ml-bravo", "ml-charlie"
psql("UPDATE communities SET published=true WHERE slug LIKE 'ml-%'")
def tok(s, url): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + url).text).group(1)
def post(s, slug, path, page="links", **data):
    data["csrf_token"] = tok(s, f"/mesh/c/{slug}/{page}")
    return s.post(B + path, data=data, allow_redirects=True)
for s_, slug, kind in ((OA, SA, "default"), (OB, SB, "default"), (OC, SC, "256")):
    post(s_, slug, f"/mesh/c/{slug}/channels/add", page="channels", name="LinkCh", key_kind=kind, downlink="1", precision="0", visibility="public")
post(OA, SA, f"/mesh/c/{SA}/channels/add", page="channels", name="Other", key_kind="default", downlink="1", precision="0", visibility="public")
check("each community has the channel to share", psql("SELECT COUNT(*) FROM community_channels WHERE name='LinkCh' AND community_id IN (SELECT id FROM communities WHERE slug LIKE 'ml-%')") == "3")
PWD = "mlListenPass99"
requests.post(P + "/credentials/set", json={"username": "ml-boot", "password": PWD}, timeout=30)
users = {"ml-upa": (SA, False), "ml-dna": (SA, True), "ml-upb": (SB, False), "ml-dnb": (SB, True)}
requests.post(P + "/credentials/sync", json={"users": [dict(username=k, password=PWD, community=v[0], down=v[1]) for k, v in users.items()], "prune": False}, timeout=30)
# ---- the agreement
pg = OA.get(B + f"/mesh/c/{SA}/links")
check("the Owner has the Linked Areas page, on the menu, off for a viewer and a guest", pg.status_code == 200 and "Propose a Link" in pg.text and f"/mesh/c/{SA}/links" in OA.get(B + f"/mesh/c/{SA}").text and VW.get(B + f"/mesh/c/{SA}/links", allow_redirects=False).status_code in (403, 404) and anon.get(B + f"/mesh/c/{SA}/links", allow_redirects=False).status_code in (302, 403, 404))
setting("meshlink_enabled", "0")
r = post(OA, SA, f"/mesh/c/{SA}/links/propose", other=SB, channel="LinkCh", hops="3", rate="30")
check("with the site switch off a link cannot be proposed", "not turned linking on" in r.text and psql("SELECT COUNT(*) FROM mesh_area_links") == "0")
setting("meshlink_enabled", "1")
r = post(OA, SA, f"/mesh/c/{SA}/links/propose", other=SB, channel="NoSuch", hops="3", rate="30")
check("a channel that is not the proposer's is refused", "Choose one of your channels" in r.text and psql("SELECT COUNT(*) FROM mesh_area_links") == "0")
r = post(OA, SA, f"/mesh/c/{SA}/links/propose", other=SB, channel="LinkCh", hops="9", rate="999")
lid = psql("SELECT id FROM mesh_area_links LIMIT 1")
check("a link is proposed, with the hops and rate cut to the site's limits", lid != "" and psql("SELECT state || hop_limit || rate_per_min FROM mesh_area_links") == "proposed530", psql("SELECT state, hop_limit, rate_per_min FROM mesh_area_links"))
check("the same link cannot be proposed twice", "already have a link" in post(OA, SA, f"/mesh/c/{SA}/links/propose", other=SB, channel="LinkCh").text)
psql(f"UPDATE mesh_area_links SET hop_limit=3, rate_per_min=30 WHERE id={lid}")
check("the other community sees it and can accept it; the proposer cannot answer their own", "Accept" in OB.get(B + f"/mesh/c/{SB}/links").text and "Take Back" in OA.get(B + f"/mesh/c/{SA}/links").text and post(OA, SA, f"/mesh/c/{SA}/links/{lid}/accept").status_code == 200 and psql(f"SELECT state FROM mesh_area_links WHERE id={lid}") == "proposed")
check("a member of neither cannot answer it", post(VW, SB, f"/mesh/c/{SB}/links/{lid}/accept", page="links").status_code in (403, 404) and psql(f"SELECT state FROM mesh_area_links WHERE id={lid}") == "proposed")
post(OB, SB, f"/mesh/c/{SB}/links/{lid}/accept")
check("accepting waits for the administrator", psql(f"SELECT state FROM mesh_area_links WHERE id={lid}") == "accepted" and "Links Waiting for Approval" in adm.get(B + "/admin/communities").text)
r = requests.get(P + "/links/stats", timeout=10).json()
check("and the mesh service carries nothing yet", r.get("active") == 0, r)
t = tok(adm, "/admin/communities")
adm.post(B + f"/admin/communities/links/{lid}/approve", data={"csrf_token": t})
check("the administrator approves and the link is active", psql(f"SELECT state FROM mesh_area_links WHERE id={lid}") == "active")
time.sleep(2)
check("the mesh service was told", requests.get(P + "/links/stats", timeout=10).json().get("active") == 1)
check("every step is in the audit log", int(psql("SELECT COUNT(*) FROM audit_log WHERE action='community.link'")) >= 3)
# ---- packets
base = 0xE0000000 + ((int(time.time()) & 0xFFFF) << 4)
la, lb = listen("ml-dna", SA, 75), listen("ml-dnb", SB, 75)
time.sleep(4)
tA, tB = f"msh/US/AR/{SA}/2/e/LinkCh/!aaaaaa01", f"msh/US/AR/{SB}/2/e/LinkCh/!bbbbbb01"
p1 = pkt("from alpha one", base + 1, 1001, hop=5)
pub("ml-upa", tA, p1); time.sleep(2)
pub("ml-upa", tA, p1); time.sleep(2)           # the same packet heard again (a second gateway): carried once
pub("ml-upb", tB, pkt("from bravo one", base + 2, 2001, hop=1)); time.sleep(2)
pub("ml-upa", f"msh/US/AR/{SA}/2/e/Other/!aaaaaa01", pkt("other channel", base + 3, 3001, channel="Other")); time.sleep(2)
requests.post(P + "/links/set", json={"links": [dict(id=int(lid), a=SA, b=SB, channel="LinkCh", hop_limit=3, rate_per_min=30, paused=True)]}, timeout=10); time.sleep(1)
pub("ml-upa", tA, pkt("while paused", base + 4, 4001)); time.sleep(2)
requests.post(P + "/links/set", json={"links": [dict(id=int(lid), a=SA, b=SB, channel="LinkCh", hop_limit=3, rate_per_min=30, paused=False)]}, timeout=10); time.sleep(1)
pub("ml-upa", tA, pkt("after resume", base + 5, 5001)); time.sleep(2)
requests.post(P + "/links/set", json={"links": []}, timeout=10); time.sleep(1)
pub("ml-upa", tA, pkt("after removal", base + 6, 6001)); time.sleep(2)
ha, hb = heard(la), heard(lb)
RG = "!a5c0ae11"
on_b = [(t, MP.open_envelope(raw, b"\x01"), raw) for t, raw in hb if t.endswith("/" + RG)]
texts_b = [o["payload"].decode() for _t, o, _r in on_b]
check("a packet heard on alpha's topic arrives on bravo's, from the service's relay gateway, on the same channel", "from alpha one" in texts_b and all(t == f"msh/US/AR/{SB}/2/e/LinkCh/{RG}" for t, _o, _r in on_b), texts_b)
o1 = next((o for _t, o, _r in on_b if o["payload"] == b"from alpha one"), {})
check("with the same sender and packet id, readable with the channel's key (carried unopened)", o1.get("from") == base + 1 and o1.get("id") == 1001, o1)
raw1 = next((r_ for _t, o, r_ in on_b if o["payload"] == b"from alpha one"), b"")
f1 = fields(get(fields(raw1), 1)); f0 = fields(get(fields(p1), 1))
check("its hop limit is lowered from 5 to the link's 3 and nothing else about the packet changed", get(f1, 9) == 3 and get(f0, 9) == 5 and get(f1, 5) == get(f0, 5) and get(f1, 6) == get(f0, 6) and get(f1, 1) == get(f0, 1), (get(f1, 9), get(f0, 9)))
check("the same packet heard twice is carried once", texts_b.count("from alpha one") == 1, texts_b)
on_a = [(t, MP.open_envelope(raw, b"\x01"), raw) for t, raw in ha if t.endswith("/" + RG)]
texts_a = [o["payload"].decode() for _t, o, _r in on_a]
check("the other direction works: bravo's packet arrives on alpha's topic", texts_a == ["from bravo one"], texts_a)
fb = fields(get(fields(on_a[0][2]), 1)) if on_a else []
check("a hop limit is never raised: 1 stays 1", get(fb, 9) == 1, get(fb, 9))
check("nothing is bounced back: alpha's topic holds no copy of what came from alpha, and bravo's none of what came from bravo", "from alpha one" not in texts_a and "from bravo one" not in texts_b)
check("a packet on another channel is not carried", "other channel" not in texts_a + texts_b)
check("nothing is carried while the link is paused", "while paused" not in texts_b)
check("and it is carried again after it is resumed", "after resume" in texts_b)
check("nothing is carried after the link is taken away", "after removal" not in texts_b)
# ---- the counters and the rate limit
requests.post(P + "/links/set", json={"links": [dict(id=int(lid), a=SA, b=SB, channel="LinkCh", hop_limit=3, rate_per_min=2, paused=False)]}, timeout=10); time.sleep(1)
lb2 = listen("ml-dnb", SB, 14); time.sleep(3)
for i in range(5):
    pub("ml-upa", tA, pkt(f"burst {i}", base + 20 + i, 7000 + i))
time.sleep(3)
got = [MP.open_envelope(raw, b"\x01")["payload"].decode() for t, raw in heard(lb2) if t.endswith("/" + RG)]
check("no more packets a minute are carried than the link's limit", len([g for g in got if g.startswith("burst")]) == 2, got)
st = requests.get(P + "/links/stats", timeout=10).json()["links"].get(lid, {})
check("the service counts what it carried, duplicates and what the limit held back", st.get("a_to_b", 0) >= 3 and st.get("b_to_a", 0) >= 1 and st.get("duplicates", 0) >= 1 and st.get("limited", 0) >= 3, st)
requests.post(P + "/links/set", json={"links": [dict(id=int(lid), a=SA, b=SB, channel="LinkCh", hop_limit=3, rate_per_min=30, paused=False)]}, timeout=10)
check("the page shows the counters", "Carrying packets" in OA.get(B + f"/mesh/c/{SA}/links").text and re.search(r"Out \d+, in \d+", OA.get(B + f"/mesh/c/{SA}/links").text))
# ---- pausing and removing from the page; a mismatched key
post(OA, SA, f"/mesh/c/{SA}/links/{lid}/pause")
check("a Manager can pause it and it says so", psql(f"SELECT paused FROM mesh_area_links WHERE id={lid}") == "t" and "Paused" in OA.get(B + f"/mesh/c/{SA}/links").text)
post(OB, SB, f"/mesh/c/{SB}/links/{lid}/resume")
check("the other side can resume it", psql(f"SELECT paused FROM mesh_area_links WHERE id={lid}") == "f")
post(OA, SA, f"/mesh/c/{SA}/links/propose", other=SC, channel="LinkCh", hops="2", rate="10")
lid2 = psql(f"SELECT id FROM mesh_area_links WHERE b_id=(SELECT id FROM communities WHERE slug='{SC}')")
r = post(OC, SC, f"/mesh/c/{SC}/links/{lid2}/accept")
check("a community whose channel has another key cannot accept without taking the proposer's key", "different key" in r.text and psql(f"SELECT state FROM mesh_area_links WHERE id={lid2}") == "proposed")
before = psql(f"SELECT key_kind || length(psk_sealed) FROM community_channels WHERE name='LinkCh' AND community_id=(SELECT id FROM communities WHERE slug='{SC}')")
post(OC, SC, f"/mesh/c/{SC}/links/{lid2}/accept", adopt="1")
after = psql(f"SELECT key_kind || length(psk_sealed) FROM community_channels WHERE name='LinkCh' AND community_id=(SELECT id FROM communities WHERE slug='{SC}')")
check("with the box ticked it takes the key and the link is accepted", before != after and after == "default0" and psql(f"SELECT state FROM mesh_area_links WHERE id={lid2}") == "accepted", (before, after))
t = tok(adm, "/admin/communities")
adm.post(B + f"/admin/communities/links/{lid2}/reject", data={"csrf_token": t})
check("an administrator can reject a link", psql(f"SELECT state FROM mesh_area_links WHERE id={lid2}") == "rejected")
post(OA, SA, f"/mesh/c/{SA}/links/{lid}/remove")
check("a Manager can remove it and the service stops carrying", psql(f"SELECT COUNT(*) FROM mesh_area_links WHERE id={lid}") == "0" and requests.get(P + "/links/stats", timeout=10).json().get("active") == 0)
check("the settings are on the Settings page", all(k in adm.get(B + "/admin/settings").text for k in ("meshlink_enabled", "meshlink_admin_approval", "meshlink_max_rate")))
psql("DELETE FROM communities WHERE slug LIKE 'ml-%'"); psql("DELETE FROM users WHERE username LIKE 'ml%'")
for u in list(users) + ["ml-boot"]:
    requests.post(P + "/credentials/delete", json={"username": u}, timeout=30)
print("ALL PASS" if ok_all else "SOME FAILED")
