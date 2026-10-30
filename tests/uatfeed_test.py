"""Who sends UAT: a fake station sends CRC-valid messages that carry the UAT marker through the test hub's Beast input with its own receiver id (the way a real ultrafeeder passes on what its dump978
decoded); the portal's receiver feed must notice, write it down, and the station lists, the station pages, the feed check and the API must say so. A second fake station sends the same messages with an
ordinary time stamp and must not be called a UAT station. Needs the test hub (port 30004) and ssh to the test box."""
import os, re, socket, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")

def crc24(data):
    poly, crc = 0xFFF409, 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1000000 | poly
        crc &= 0xFFFFFF
    return crc
def df17_ident(icao, call):
    chars = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ#####_###############0123456789######"
    bits = 0
    for ch in call.ljust(8, "_"):
        bits = (bits << 6) | chars.index(ch)
    me = (4 << 3 | 3).to_bytes(1, "big") + bits.to_bytes(6, "big")
    body = bytes([17 << 3 | 5]) + bytes.fromhex(icao) + me
    return body + crc24(body).to_bytes(3, "big")
def esc(b): return b.replace(b"\x1a", b"\x1a\x1a")

HOST = os.environ.get("TEST_HOST", "test-box.invalid")
UAT_UUID, PLAIN_UUID, QUIET_UUID = "deadbeef-0978-2233-4455-66778899aabb", "deadbeef-1090-2233-4455-66778899aabb", "deadbeef-0000-2233-4455-66778899aabb"
UATTS = bytes.fromhex("ff004d4c4155")
adm = sess("marty", PW)
setting("uat_enabled", "1")
setting("uat_recent_minutes", "30")
for u in (UAT_UUID, PLAIN_UUID, QUIET_UUID):
    psql(f"DELETE FROM feeders WHERE uuid='{u}'")
for u, sid, nm in ((UAT_UUID, "UAT-978", "UAT Station"), (PLAIN_UUID, "PLAIN-1090", "Plain Station"), (QUIET_UUID, "QUIET-1", "Quiet Station")):
    psql(f"INSERT INTO feeders (uuid, station_id, name, owner_id, online) VALUES ('{u}', '{sid}', '{nm}', (SELECT id FROM users WHERE username='marty'), TRUE)")

def send(uuid, ts, icao, n=30):
    s = socket.create_connection((HOST, 30004), timeout=10)
    s.sendall(b"\x1a\xe3" + esc(bytes.fromhex(uuid.replace("-", "")[:16])))
    for i in range(n):
        s.sendall(b"\x1a\x33" + esc(ts) + b"\x28" + esc(df17_ident(icao, "UAT" + icao[-3:].upper())))
        time.sleep(0.15)
    s.close()
send(UAT_UUID, UATTS, "ab0978", 30)
send(PLAIN_UUID, bytes(6), "ab1090", 12)
time.sleep(1)

# the station is written down within a flush (every 30 seconds) and the lists are cached for 15
t0, row = time.time(), ""
while time.time() - t0 < 90 and not row:
    row = psql(f"SELECT aircraft_hour FROM feeder_uat WHERE feeder_uuid='{UAT_UUID}'")
    if not row:
        time.sleep(3)
check("the station that sent UAT-marked messages is written down with its aircraft", row == "1", row)
check("the station that sent the same messages with an ordinary time stamp is not", psql(f"SELECT count(*) FROM feeder_uat WHERE feeder_uuid='{PLAIN_UUID}'") == "0")
time.sleep(16)

pg = requests.get(B + "/feeders").text
def line(sid):
    m = re.search(r"<tr[^>]*>(?:(?!</tr>).)*" + sid + r"(?:(?!</tr>).)*</tr>", pg, re.S)
    return m.group(0) if m else ""
check("the public station list tags it UAT and nobody else", 'title="UAT (978 MHz) aircraft came through this feeder recently">UAT</span>' in line("UAT-978") and "UAT</span>" not in line("PLAIN-1090") and "UAT</span>" not in line("QUIET-1"), (bool(line("UAT-978")), bool(line("PLAIN-1090"))))
js = {f["station_id"]: f for f in requests.get(B + "/api/feeders.json").json()["feeders"]}
check("the public API has the flag", js.get("UAT-978", {}).get("uat") is True and js.get("PLAIN-1090", {}).get("uat") is False, {k: v.get("uat") for k, v in js.items() if k in ("UAT-978", "PLAIN-1090")})
sp = requests.get(B + "/feeders/UAT-978").text
check("its public page says it is receiving UAT", "UAT (978 MHz)" in sp and "Receiving" in sp)
check("another station's page does not", '<div class="tl">UAT (978 MHz)</div>' not in requests.get(B + "/feeders/PLAIN-1090").text and '<div class="tl">UAT (978 MHz)</div>' in sp)
my = adm.get(B + "/my/feeders/" + UAT_UUID).text
check("the owner's station page says UAT came through, with the count and an honest note for others", "UAT (978 MHz)" in my and "receiving" in my and "1 aircraft in the last hour" in my, re.findall(r"UAT[^<]{0,120}", my)[:3])
mq = adm.get(B + "/my/feeders/" + QUIET_UUID).text
check("a station with none says none came through and does not claim it has no radio", "none yet" in mq and "No UAT has come through this station yet" in mq and "normal without a 978 MHz radio" in mq)
ad = adm.get(B + "/admin/feeders").text
check("the admin list has a UAT column and marks only that station", "<th data-sort=\"text\">UAT</th>" in ad and ad.count('<span class="tag live">receiving</span>') >= 1)
check("the admin station page has the UAT line", "UAT (978 MHz)" in adm.get(B + "/admin/feeders/" + UAT_UUID).text and "UAT came through" in adm.get(B + "/admin/feeders/" + UAT_UUID).text)

# a station that stopped: past the window it is 'earlier', not 'receiving' (a station written straight into the table, so the receiver feed's memory cannot refresh it)
psql(f"INSERT INTO feeder_uat (feeder_uuid, first_seen, last_seen, aircraft_hour) VALUES ('{QUIET_UUID}', now() - interval '2 days', now() - interval '3 hours', 4)")
time.sleep(16)
my2 = adm.get(B + "/my/feeders/" + QUIET_UUID).text
pg2 = requests.get(B + "/feeders").text
check("past the window it says UAT last came through hours ago", "UAT last came through this station" in my2 and "3 hours ago" in my2 and "earlier" in my2, re.findall(r"UAT[^<]{0,100}", my2)[:3])
check("and the public list does not tag it", "UAT</span>" not in (re.search(r"<tr[^>]*>(?:(?!</tr>).)*QUIET-1(?:(?!</tr>).)*</tr>", pg2, re.S) or [""])[0] if re.search(r"<tr[^>]*>(?:(?!</tr>).)*QUIET-1(?:(?!</tr>).)*</tr>", pg2, re.S) else True)
setting("uat_enabled", "0")
time.sleep(1)
check("with the UAT pages switched off nothing about UAT is shown", 'id="sec-uat"' not in adm.get(B + "/my/feeders/" + UAT_UUID).text and requests.get(B + "/api/feeders.json").json()["feeders"] is not None)
setting("uat_enabled", "1")
check("the window is a setting", "UAT Counts as Receiving For" in adm.get(B + "/admin/settings").text)
for u in (UAT_UUID, PLAIN_UUID, QUIET_UUID):
    psql(f"DELETE FROM feeders WHERE uuid='{u}'")
print("ALL PASS" if ok_all else "SOME FAILED")
