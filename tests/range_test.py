import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
anon = requests.Session(); adm = sess("marty", PW)
U = "bbbbbbbb-1111-2222-3333-444444444444"
psql(f"DELETE FROM feeders WHERE uuid='{U}'")
psql(f"INSERT INTO feeders (uuid, station_id, name, owner_id, lat, lon, privacy, online) VALUES ('{U}', 'RANGE-1', 'Range Station', (SELECT id FROM users WHERE username='marty'), 34.5, -92.5, 'hidden', TRUE)")
for sector, nm, alt in ((0, 90.0, 35000), (9, 140.0, 38000), (18, 60.0, 30000), (27, 175.0, 40000)):
    psql(f"INSERT INTO feeder_range (feeder_uuid, day, sector, max_nm, max_alt) VALUES ('{U}', (now() AT TIME ZONE 'utc')::date, {sector}, {nm}, {alt})")
psql(f"INSERT INTO feeder_range (feeder_uuid, day, sector, max_nm, max_alt) VALUES ('{U}', (now() AT TIME ZONE 'utc')::date - 5, 12, 210.0, 41000)")
page = anon.get(B + "/feeders/RANGE-1").text
check("the station page shows maximum range tile and the polar chart", "Maximum range (30 days)" in page and "210 nm" in page and "<polygon" in page and ">N<" in page, re.findall(r"Farthest.{0,60}", page))
check("today's and all-time figures are separate", "Farthest today" in page and "175 nm" in page and "Farthest on record" in page)
check("the page never shows the station's coordinates, even though its location is set to hidden", "34.5" not in page and "-92.5" not in page and "34.50" not in page)
lst = anon.get(B + "/feeders/").text
check("the feeders list has a Max Range column", "Max Range" in lst and "210 nm" in lst)
check("and can be ranked by it", "Maximum Range (30 Days)" in lst)
api = [f for f in anon.get(B + "/api/feeders.json").json()["feeders"] if f["station_id"] == "RANGE-1"][0]
check("the public feed carries the 30-day range", api["max_range_nm_30d"] == 210 and api["lat"] is None, api)
m = anon.get(B + "/metrics").text
check("Prometheus metrics carry range per station", 'adsb_portal_station_max_range_nm_30d{station="RANGE-1"} 210' in m and 'adsb_portal_station_max_range_nm_today{station="RANGE-1"} 175' in m)
mine = adm.get(B + f"/my/feeders/{U}").text
check("the owner sees the range on their station page", "Farthest in 30 days" in mine and "<polygon" in mine)
check("range settings exist", all(t in adm.get(B + "/admin/settings").text for t in ("Range Sampling Interval", "Longest Believable Range", "Keep Range History")))
r = anon.get(B + "/feeders/?sort=range")
check("sorting by range works", r.status_code == 200)
psql(f"DELETE FROM feeders WHERE uuid='{U}'")
check("deleting a station removes its range records", psql(f"SELECT COUNT(*) FROM feeder_range WHERE feeder_uuid='{U}'") == "0")
print("ALL PASS" if ok_all else "SOME FAILED")
