"""Feeder achievements: badges worked out from the real numbers on the Feeders page and each feeder's page, an Achievements section that says what each takes and who holds it, and the limits in Admin > Settings.
Needs the test box's feeders (the seeded test stations)."""
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    import subprocess
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
U1, U2 = "aaaaaaaa-0000-4000-8000-0000000000a1", "aaaaaaaa-0000-4000-8000-0000000000a2"
psql(f"DELETE FROM feeders WHERE uuid IN ('{U1}','{U2}')")
psql(f"INSERT INTO feeders (uuid, station_id, name, owner_id, online, first_seen) VALUES ('{U1}', 'ACHV-1', 'Achievement Veteran', (SELECT id FROM users WHERE username='marty'), TRUE, now() - interval '800 days'), "
     f"('{U2}', 'ACHV-2', 'Achievement Newcomer', (SELECT id FROM users WHERE username='marty'), TRUE, now() - interval '5 days')")
psql(f"INSERT INTO feeder_range (feeder_uuid, day, sector, max_nm) VALUES ('{U1}', current_date, 3, 420)")
try:
    r = requests.get(B + "/feeders/", timeout=60)
    t = r.text
    check("the Feeders page has an Achievements section listing every badge and its holders", r.status_code == 200 and 'id="sec-achievements"' in t and all(x in t for x in ("Long Reach", "Furthest Reach", "Always On", "Busy Antenna", "Veteran", "Established", "New Arrival", "Network Pillar")), r.status_code)
    check("the limits in the descriptions come from the settings", "150 nautical miles" in t and "99%" in t, "")
    row = lambda sid: re.search(r'href="/feeders/' + sid + r'"><b class="mono">.*?</tr>', t, re.S)
    a, b = row("ACHV-1"), row("ACHV-2")
    check("a long-range, long-standing feeder earns Long Reach, Furthest Reach and Veteran", a and all(x in a.group(0) for x in ("Long Reach", "Furthest Reach", "Veteran")), a.group(0)[:300] if a else None)
    check("a feeder that joined days ago earns New Arrival and not Veteran", b and "New Arrival" in b.group(0) and "Veteran" not in b.group(0), b.group(0)[:300] if b else None)
    check("the section names the holders", re.search(r'Long Reach</span></td><td>[^<]*</td><td>.*?ACHV-1', t, re.S) is not None, "")
    p = requests.get(B + "/feeders/ACHV-1", timeout=60).text
    check("a feeder's own page shows its achievements", 'id="achv-mine"' in p and "Veteran" in p and "Long Reach" in p, "")
    check("a hover explains a badge", 'title="Heard an aircraft 150 nautical miles' in p, "")
finally:
    psql(f"DELETE FROM feeders WHERE uuid IN ('{U1}','{U2}')")
adm = sess("marty", PW)
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("Feeder Achievements", "Long Reach: Nautical Miles", "Always On: Uptime Percent", "Busy Antenna: How Many Feeders", "Veteran: Days in the Network", "Established: Days in the Network", "New Arrival: Days in the Network")))
print("ALL PASS" if ok_all else "SOME FAILED")
