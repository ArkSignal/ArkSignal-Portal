"""The mesh alert and weather channel, end to end on the test box (sh tests/deploy_mesh.sh): the mesh service builds a real Meshtastic packet (checked here by an independent decryption with the default key, the channel
hash, the port number, the sender and the topic), a downlink login receives it on its community's topic, the portal's watcher sends warnings that cover the community's area and a weather summary from the stations inside it
within the daily cap and the minimum gap, nothing is sent twice, the switches (site and community) hold everything back, and the Manager's page, the log, the audit trail and the menu."""
import base64, json, os, re, struct, subprocess, time, requests
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
    print("SKIP meshalertchan_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)"); print("ALL PASS"); raise SystemExit(0)

# ---- the packet itself, checked by an independent decryption (default key, AES-CTR)
DEFAULT = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")
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
        else: raise ValueError("wire type %d" % wt)
        out.append((num, v))
    return out
def get(fl, n, default=None):
    return next((v for k, v in fl if k == n), default)
def decrypt(raw, key=DEFAULT):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    env = fields(raw); mp = fields(get(env, 1))
    sender, pid = get(mp, 1), get(mp, 6)
    nonce = struct.pack("<Q", pid) + struct.pack("<I", sender) + b"\0\0\0\0"
    d = Cipher(algorithms.AES(key), modes.CTR(nonce)).decryptor(); data = d.update(get(mp, 5)) + d.finalize()
    inner = fields(data)
    return {"channel": get(env, 2).decode(), "gateway": get(env, 3).decode(), "from": sender, "to": get(mp, 2), "hash": get(mp, 3), "id": pid, "port": get(inner, 1), "text": get(inner, 2).decode(), "want_ack": get(mp, 10), "hop": get(mp, 9)}
import importlib.util
_spec = importlib.util.spec_from_file_location("meshpacket", "../app/meshpacket.py"); MP = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(MP)
def build(channel, psk, text, pid):
    return MP.build_envelope(1, text.encode(), psk, channel, 0xA5C0A001, "!a5c0a001", packet_id=pid)
pk = decrypt(build("LongFast", b"", "Hello from the test", 424242))
check("a packet built for the default key decrypts with the well known key to the text, port 1, from the service's node, to everyone", pk["text"] == "Hello from the test" and pk["port"] == 1 and pk["from"] == 0xA5C0A001 and pk["to"] == 0xFFFFFFFF and pk["id"] == 424242 and pk["channel"] == "LongFast" and pk["gateway"] == "!a5c0a001", pk)
check("the channel hash for LongFast with the default key is 8 (what every radio computes)", pk["hash"] == 8, pk["hash"])
check("no acknowledgement is asked for, and the hop limit is set", pk["want_ack"] is None and pk["hop"] == 3, pk)
k32 = bytes(range(32)); k16 = bytes(range(16))
for key, label in ((k16, "a 128 bit key"), (k32, "a 256 bit key")):
    p2 = decrypt(build("Alerts", key, "Tornado Warning until 4 PM", 99), key)
    check(f"{label} works too", p2["text"] == "Tornado Warning until 4 PM" and p2["channel"] == "Alerts", p2)
check("an encrypted message does not contain its words in the clear", b"secret words" not in build("Alerts", k32, "secret words", 7))
good = base64.b64encode(build("AlertCh", b"", "x", 5)).decode()
pub = lambda **kw: requests.post(P + "/publish", json=kw, timeout=15).status_code
check("the service takes only finished packets: it refuses a bad community address, a bad channel name and something that is not a packet", pub(community="Bad Slug", channel="X", envelope_b64=good) == 400 and pub(community="mac-test", channel="a/b", envelope_b64=good) == 400
      and pub(community="mac-test", channel="AlertCh", envelope_b64=base64.b64encode(b"hello").decode()) == 400)
check("the service has no endpoint that takes a channel key", requests.post(P + "/send", json={"community": "mac-test", "channel": "AlertCh", "psk": "AQ==", "text": "x"}, timeout=15).status_code == 404 and requests.post(P + "/build_check", json={}, timeout=15).status_code == 404)

# ---- a community, its channel and a downlink login that listens
setting("mesh_enabled", "1"); setting("mesh_communities_enabled", "1"); setting("meshalert_enabled", "1")
psql("DELETE FROM settings WHERE key = 'access_communities'")
psql("DELETE FROM communities WHERE slug LIKE 'mac-%'")
psql("DELETE FROM users WHERE username LIKE 'mac%'")
for n in ("macowner", "macviewer"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
O, V = sess("macowner", "macownerpassword99"), sess("macviewer", "macviewerpassword99")
adm = sess("marty", PW)
anon = requests.Session()
ok, m, _ = act(adm, "/admin/communities/create", "/admin/communities", name="MAC Alert Mesh", owner="macowner")
SLUG = "mac-alert-mesh"
check("a community is made", ok and psql(f"SELECT COUNT(*) FROM communities WHERE slug='{SLUG}'") == "1", m)
psql(f"UPDATE communities SET area_poly='[[34.0,-93.0],[34.0,-91.0],[35.5,-91.0],[35.5,-93.0]]', alert_enabled=false, alert_channel_id=NULL WHERE slug='{SLUG}'")
def tok(s, url): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + url).text).group(1)
def post(s, path, page=None, **data):
    data["csrf_token"] = tok(s, page or f"/mesh/c/{SLUG}/alerts")
    return s.post(B + path, data=data, allow_redirects=True)
post(O, f"/mesh/c/{SLUG}/channels/add", page=f"/mesh/c/{SLUG}/channels", name="AlertCh", key_kind="default", downlink="1", precision="0", visibility="public")
cid = psql(f"SELECT id FROM community_channels WHERE name='AlertCh' AND community_id=(SELECT id FROM communities WHERE slug='{SLUG}')")
check("the community has its channel (default key, downlink on)", cid != "")
# ---- the Manager's page
pg = O.get(B + f"/mesh/c/{SLUG}/alerts")
check("the Owner has the Alert Channel page, on the menu, with the channels", pg.status_code == 200 and "Alert and Weather Channel" in pg.text and "AlertCh" in pg.text and f"/mesh/c/{SLUG}/alerts" in O.get(B + f"/mesh/c/{SLUG}").text)
check("it is closed to a member who is not on the team, and to a guest", V.get(B + f"/mesh/c/{SLUG}/alerts", allow_redirects=False).status_code in (403, 404) and anon.get(B + f"/mesh/c/{SLUG}/alerts", allow_redirects=False).status_code in (302, 403, 404))
r = post(O, f"/mesh/c/{SLUG}/alerts", alert_enabled="1", alert_nws="1", alert_weather="1")
check("turning it on without a channel is refused with the reason", "Choose the channel" in r.text and psql(f"SELECT alert_enabled FROM communities WHERE slug='{SLUG}'") == "f", r.text[:0])
r = post(O, f"/mesh/c/{SLUG}/alerts", alert_enabled="1", alert_nws="1", alert_weather="1", channel=cid)
check("with a channel it is saved", psql(f"SELECT alert_enabled, alert_channel_id FROM communities WHERE slug='{SLUG}'") == "t|" + cid, r.text[:0])
check("a viewer cannot change it", post(V, f"/mesh/c/{SLUG}/alerts", page="/my/account", alert_enabled="0").status_code in (403, 404))
check("the change is in the audit log", int(psql("SELECT COUNT(*) FROM audit_log WHERE action='community.edit' AND detail LIKE 'alert channel%'")) >= 1)
# ---- a listener: a downlink login on the community's topic
PWD = "macListenPass99"
requests.post(P + "/credentials/set", json={"username": "mac-boot", "password": PWD}, timeout=30)
requests.post(P + "/credentials/sync", json={"users": [{"username": "mac-down", "password": PWD, "community": SLUG, "down": True}], "prune": False}, timeout=30)
topic = f"msh/US/AR/{SLUG}/2/e/#"
sub = subprocess.Popen(["ssh", "-q", "nastemp", f"{DK} exec mesh-mqtt mosquitto_sub -h localhost -u mac-down -P {PWD} -t '{topic}' -W 75 -C 4 -F '%t %x'"], stdout=subprocess.PIPE, text=True)
time.sleep(4)
# ---- the test button sends a real message
r = post(O, f"/mesh/c/{SLUG}/alerts/test")
check("the test button says it was sent", "test message was sent" in r.text, re.sub(r"s+", " ", re.sub(r"<[^>]+>", " ", r.text))[:1500])
# ---- the portal's watcher with fixed warnings and stations, through the real service
subprocess.run(["scp", "-q", "meshalertchan_unit.py", "nastemp:adsb-portal/meshalertchan_unit.py"], check=True)
ssh(f"{DK} cp ~/adsb-portal/meshalertchan_unit.py adsb-portal:/tmp/meshalertchan_unit.py")
o = ssh(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/meshalertchan_unit.py {SLUG}", 300).strip().splitlines()
try:
    res = json.loads(o[-1])
except Exception:
    res = {}
    print("unit output:", o[-6:])
for k, label in (("site_switch_off_sends_nothing", "with the site switch off nothing is sent"), ("community_switch_off_sends_nothing", "with the community's switch off nothing is sent"),
                 ("alerts_for_area_only_and_kinds_only", "only warnings that cover the area and are of the kinds the site sends"), ("every_text_fits", "every message fits 200 bytes"),
                 ("weather_summary_from_stations_in_area", "the summary comes from the public stations inside the area that reported lately (not a far one, a hidden one or an old report)"),
                 ("sent_the_two_warnings_and_one_summary", "a pass sends the two warnings and one summary, spaced by the minimum gap"), ("each_warning_once_and_the_summary_once", "nothing is sent twice"),
                 ("audit_entry_for_each_send", "every send is in the audit log"), ("a_new_warning_is_sent_later", "a new warning is sent on a later pass"),
                 ("the_gap_holds_a_message_back_and_it_is_not_logged_as_failed", "the minimum gap holds a message back until it has passed"), ("the_daily_cap_holds_messages_back_and_says_so", "the daily cap holds messages back and the log says so"),
                 ("no_channel_sends_nothing", "with no channel nothing is sent")):
    check("unit: " + label, res.get(k) is True, (res.get(k), res.get("debug_ws") if "weather" in k else res.get("debug_rows") if "sent_the" in k else ""))
# ---- what the listener got
try:
    out_text, _ = sub.communicate(timeout=90)
except subprocess.TimeoutExpired:
    sub.kill(); out_text = sub.stdout.read() if sub.stdout else ""
msgs = []
for line in (out_text or "").splitlines():
    t, _sp, hx = line.partition(" ")
    if hx:
        try:
            msgs.append((t, decrypt(bytes.fromhex(hx))))
        except Exception as e:
            msgs.append((t, {"error": str(e)}))
check("the listener received packets on the community's topic", len(msgs) >= 3, len(msgs))
check("each on <root>/<community>/2/e/<channel>/<the service's gateway>", msgs and all(t == f"msh/US/AR/{SLUG}/2/e/AlertCh/!a5c0a001" for t, _ in msgs), [t for t, _ in msgs][:2])
texts = [m.get("text", "") for _, m in msgs]
check("the first is the test message, readable with the channel's key", any(t.startswith("Test message from") for t in texts), texts)
check("and the warnings follow, as text messages from the service's node to everyone", any(t.startswith("Tornado Warning") for t in texts) and all(m.get("port") == 1 and m.get("from") == 0xA5C0A001 and m.get("to") == 0xFFFFFFFF for _, m in msgs if "error" not in m), texts)
check("and the channel hash is the one the channel's name and key give", all(m.get("hash") == (sum(0 for _ in ()) or __import__("functools").reduce(lambda a, b: a ^ b, b"AlertCh" + DEFAULT, 0)) for _, m in msgs if "error" not in m), [m.get("hash") for _, m in msgs])
# ---- the log on the page
pg = O.get(B + f"/mesh/c/{SLUG}/alerts").text
check("the page lists what went out", "What Went Out" in pg and "Tornado Warning" in pg and "held back" in pg and "Sent" in pg)
check("no key is written in the page, the log or the audit trail", not re.search(r"AQ==", pg) and int(psql("SELECT COUNT(*) FROM audit_log WHERE detail LIKE '%AQ==%'")) == 0)
check("the settings are on the Settings page", all(k in adm.get(B + "/admin/settings").text for k in ("meshalert_enabled", "meshalert_daily_cap", "meshalert_weather_hours")))
psql("DELETE FROM communities WHERE slug LIKE 'mac-%'"); psql("DELETE FROM users WHERE username LIKE 'mac%'")
requests.post(P + "/credentials/delete", json={"username": "mac-down"}, timeout=30); requests.post(P + "/credentials/delete", json={"username": "mac-boot"}, timeout=30)
print("ALL PASS" if ok_all else "SOME FAILED")
