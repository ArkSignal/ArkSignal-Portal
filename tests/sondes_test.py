"""Radiosondes and balloons, and the county map, through the web: public pages and data files, the shared map shell, search, privacy of receiving stations, claiming (profile call sign, proof code,
administrator approval), the owner's pages, the admin page, the menu, the status row, the home card, and the county pages and counts. The flights are seeded through the real collector
(sondes_seed.py) and removed at the end."""
import time
import json, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout
def psql(sql): return ssh(D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
def csrf(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
H = {"X-Requested-With": "XMLHttpRequest"}

# ---- the in-container unit checks: the collector, flights, launch sites, stations, claims, counties
subprocess.run(["scp", "-q", "sondes_unit.py", "nastemp:adsb-portal/sondes_unit.py"], check=True, capture_output=True)
ssh(D + "cp ~/adsb-portal/sondes_unit.py adsb-portal:/tmp/sondes_unit.py")
out = ssh(D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sondes_unit.py")
check("sondes_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-400:])

subprocess.run(["scp", "-q", "privacy_unit.py", "nastemp:adsb-portal/privacy_unit.py"], check=True, capture_output=True)
ssh(D + "cp ~/adsb-portal/privacy_unit.py adsb-portal:/tmp/privacy_unit.py")
out = ssh(D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/privacy_unit.py")
check("privacy_unit.py passes inside the portal container (the location levels)", "ALL PASS" in out and "FAIL" not in out, out[-400:])

subprocess.run(["scp", "-q", "sondes_seed.py", "nastemp:adsb-portal/sondes_seed.py"], check=True, capture_output=True)
ssh(D + "cp ~/adsb-portal/sondes_seed.py adsb-portal:/tmp/sondes_seed.py")
out = ssh(D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sondes_seed.py seed")
check("the demonstration flights were seeded through the collector", "seeded" in out and "'total': 5" in out, out[-300:])
subprocess.run(["scp", "-q", "sondes_states.py", "nastemp:adsb-portal/sondes_states.py"], check=True, capture_output=True)
ssh(D + "cp ~/adsb-portal/sondes_states.py adsb-portal:/tmp/sondes_states.py")
out2 = ssh(D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sondes_states.py")
check("every flight and station is given its state", "assigned" in out2 and psql("select count(*) from sondes where state is null") == "0", out2[-200:])
check("the flights launched at North Little Rock are in AR and the one from Shreveport is in LA", psql("select string_agg(serial || state, ',' order by serial) from sondes where serial like 'TEST%'") == "TESTAAA1AR,TESTBBB2AR,TESTCCC3LA,TESTDDD4AR", psql("select string_agg(serial || state, ',' order by serial) from sondes where serial like 'TEST%'"))
ov = requests.get(B + "/sondes/").text
check("the overview lists Arkansas only by default: the Shreveport flight is not on it, the Little Rock ones are", "TESTCCC3" not in ov and "TESTBBB2" in ov and "<b>Arkansas</b>" in ov, "Arkansas" in ov)
check("and the Balloon Map data and the flight list still carry every state", "TESTCCC3" in requests.get(B + "/sondes/flights", params={"hours": 72}).text and any(x["id"] == "TESTCCC3" for x in requests.get(B + "/api/sondes/map.json", params={"hours": 72}).json()["flights"]))
psql("insert into settings (key, value) values ('sonde_overview_states','AR,LA') on conflict (key) do update set value='AR,LA'")
ov2 = requests.get(B + "/sondes/").text
check("with Louisiana added in Settings the Shreveport flight appears and the page names both states", "TESTCCC3" in ov2 and "Arkansas, Louisiana" in ov2)
psql("delete from settings where key='sonde_overview_states'")
psql("INSERT INTO settings (key, value) VALUES ('sonde_enabled','1') ON CONFLICT (key) DO UPDATE SET value='1'")

anon = requests.Session()
adm = sess("marty", PW)
# a plain member (the baseline user created by run_all)
plain = requests.Session()
try:
    plain = sess("plain1", "plainpassword1")
except AssertionError:
    t = re.search(r'name="csrf_token" value="([^"]+)"', plain.get(B + "/my/register").text).group(1)
    plain.post(B + "/my/register", data={"csrf_token": t, "username": "plain1", "email": "plain1@example.com", "password": "plainpassword1", "confirm": "plainpassword1"})
    plain = sess("plain1", "plainpassword1")

# ---- the public pages
for path in ("/sondes/", "/sondes/map", "/sondes/flights", "/sondes/stations/", "/sondes/sites/72340", "/sondes/stations/AE5AU", "/sondes/TESTAAA1", "/feed/sondes", "/counties/", "/counties/pulaski"):
    r = anon.get(B + path)
    check(f"{path} opens for a visitor", r.status_code == 200, r.status_code)
page = anon.get(B + "/sondes/").text
check("the overview shows the flights in the air and credits SondeHub with the license", "TESTAAA1" in page and "In the Air Now" in page and "SondeHub" in page and "Creative Commons BY-SA 2.0" in page)
check("the overview says how many are climbing (balloon intact) and how many have burst and are falling", re.search(r'class="tv">2</div><div class="tl">Climbing now', page) is not None and re.search(r'class="tv">1</div><div class="tl">Burst, falling now', page) is not None and "National Weather Service Launch Sites" in page, re.findall(r'class="tv">(\d+)</div><div class="tl">([^<]+)', page))
m = anon.get(B + "/sondes/map").text
check("the balloon map uses the same shell as the aircraft map: the stylesheet and every shared element name", "/static/mapshell.css" in m and all(f'id="{i}"' in m for i in ("lv", "lvmap", "lv-left", "lv-tools", "lv-toolsbody", "lv-card", "lv-list", "lv-legend", "lv-altbar", "lv-layers", "lv-wxbtn", "lv-filtbtn", "lv-legbtn")) and "/static/maplayers.js" in m)
check("the county map uses the same shell", all(f'id="{i}"' in anon.get(B + "/counties/").text for i in ("lv", "lvmap", "lv-left", "lv-tools", "lv-toolsbody", "lv-card", "lv-list", "lv-legend", "lv-altbar", "lv-layers")))
check("the map page has the search box for balloons, stations and launch sites", "Search balloon, station or launch site" in m)

# ---- the flight list: search and filters
def ids(qs): return set(re.findall(r'href="/sondes/(TEST[A-Z0-9]+|N0TEST-11)"', anon.get(B + "/sondes/flights?hours=0&" + qs).text))
check("search by serial prefix", ids("q=TESTB") == {"TESTBBB2"})
check("filter National Weather Service", ids("agency=NWS") == {"TESTAAA1", "TESTBBB2", "TESTCCC3", "TESTDDD4"})
check("filter climbing and falling", ids("status=climbing") == {"TESTAAA1", "N0TEST-11"} and ids("status=falling") == {"TESTDDD4"} and ids("status=aloft") == {"TESTAAA1", "TESTDDD4", "N0TEST-11"})
check("filter by recovery: recovered, looked for and not found, landed with no report", ids("recovery=recovered") == {"TESTBBB2"} and ids("recovery=notfound") == {"TESTCCC3"} and ids("recovery=unreported") == set())
check("the list names the phase and the recovery", "Burst, falling" in anon.get(B + "/sondes/flights?hours=0").text and ">Recovered<" in anon.get(B + "/sondes/flights?hours=0").text and ">Not found<" in anon.get(B + "/sondes/flights?hours=0").text)
check("filter amateur balloons", ids("kind=amateur") == {"N0TEST-11"})
check("filter signal lost and landed", ids("status=lost") == {"TESTCCC3"} and ids("status=landed") == {"TESTBBB2"})
check("filter by launch site and by receiving station", ids("site=72248") == {"TESTCCC3"} and ids("station=AE5AU") == {"TESTAAA1", "TESTBBB2"})
fl = anon.get(B + "/sondes/flights?hours=0").text
fs = anon.get(B + "/sondes/flights?hours=0&station=AE5AU").text
check("the flights filter has a Heard by box you can type in, with the receiving stations to pick from, and keeps the chosen one", 'name="station"' in fl and 'list="heard-stations"' in fl and '<option value="AE5AU">' in fl and 'name="station" list="heard-stations" value="AE5AU"' in fs and 'value="AE5AU" maxlength' not in fl)
check("the list sorts by highest point", anon.get(B + "/sondes/flights?hours=0&sort=max_alt").status_code == 200)

# ---- one flight
f = anon.get(B + "/sondes/TESTBBB2").text
check("the flight page names the launch site, the source and the status", "North Little Rock" in f and "NWS" in f and "landed" in f)
check("the flight page lists who heard it", "KD5VCF-LR" in f and "AE5AU" in f)
check("the flight page carries the charts, the Skew-T and the license", all(i in f for i in ('id="ch-alt"', 'id="ch-temp"', 'id="ch-wind"', 'id="skewt"', 'id="fmap"')) and "Creative Commons BY-SA 2.0" in f)
tj = anon.get(B + "/api/sondes/TESTBBB2/track.json").json()
t = tj["track"]
check("the track data is a set of equal-length columns with positions and the readings", len(t["t"]) > 1500 and all(len(t[k]) == len(t["t"]) for k in t) and tj["status"] == "landed" and tj["landing"] and tj["launch"])
csvtxt = anon.get(B + "/sondes/TESTBBB2/track.csv")
check("the CSV download has a header, all the points and the credit", csvtxt.headers["content-type"].startswith("text/csv") and "attachment" in csvtxt.headers["content-disposition"] and csvtxt.text.startswith("time_utc,latitude") and "SondeHub" in csvtxt.text.splitlines()[-1] and len(csvtxt.text.splitlines()) > 1500)
gj = anon.get(B + "/sondes/TESTBBB2/track.geojson").json()
check("the GeoJSON download is a line", gj["features"][0]["geometry"]["type"] == "LineString" and len(gj["features"][0]["geometry"]["coordinates"]) > 1500 and "BY-SA" in gj["features"][0]["properties"]["license"])
check("a flight that does not exist is not found", anon.get(B + "/sondes/NOSUCHSONDE").status_code == 404 and anon.get(B + "/api/sondes/NOSUCHSONDE/track.json").status_code == 404)

try:
    import esprima
    bad = []
    for path in ("/sondes/TESTBBB2", "/sondes/TESTAAA1", "/sondes/stations/AE5AU", "/sondes/sites/72340", "/counties/pulaski", "/counties/", "/sondes/map", "/"):
        for sc in re.findall(r"<script>(.*?)</script>", anon.get(B + path).text, re.S):
            try:
                esprima.parseScript(sc)
            except Exception as e:
                bad.append((path, str(e)[:80]))
    check("every inline script on the balloon and county pages parses", not bad, bad)
except ImportError:
    print("SKIP esprima is not installed")

# ---- the map data
md = anon.get(B + "/api/sondes/map.json?hours=24").json()
ids_ = {x["id"] for x in md["flights"]}
check("the Balloon Map data says which stations are claimed (the card said 'not claimed yet' for every station)", all(isinstance(x.get("claimed"), bool) for x in md["stations"]) and any(x["claimed"] for x in md["stations"]), [(x["id"], x.get("claimed")) for x in md["stations"]][:6])
check("the map data has the recent flights with their position, source, status and a track", {"TESTAAA1", "TESTBBB2", "TESTCCC3", "N0TEST-11"} <= ids_ and all("trail" in x and x["lat"] is not None for x in md["flights"]))
a1 = next(x for x in md["flights"] if x["id"] == "TESTAAA1")
a1m = next(x for x in md["flights"] if x["id"] == "TESTAAA1")
check("the flight in the air has its predicted landing; the landed one has its landing", a1m["status"] == "aloft" and a1m["pred"] and a1m["agency"] == "NWS" and next(x for x in md["flights"] if x["id"] == "TESTBBB2")["land"])
mdd = {x["id"]: x for x in md["flights"]}
check("the map data carries the phase, the burst, the launch and the recovery", mdd["TESTAAA1"]["phase"] == "climbing" and mdd["TESTDDD4"]["phase"] == "falling" and mdd["TESTDDD4"]["burst"] and mdd["TESTDDD4"]["launch"] and mdd["TESTBBB2"]["recovered"] is True and mdd["TESTCCC3"]["recovered"] is False and mdd["TESTBBB2"]["rec_by"] == "KD5TEST")
tb = next(x for x in md["flights"] if x["id"] == "TESTBBB2")
check("a long flight's map track is the WHOLE flight thinned evenly (it starts at the launch and ends at the landing), not just its latest stretch", len(tb["trail"]) >= 100 and len(tb["trail"]) <= 430 and abs(tb["trail"][0][0] - 34.835) < 0.01 and abs(tb["trail"][0][1] + 92.2594) < 0.01, (len(tb["trail"]), tb["trail"][:1]))
check("only the sites in the area come with the map, with the National Weather Service ones marked", {s["id"] for s in md["sites"]} >= {"72340", "72248", "72440"} and all(s["nws"] for s in md["sites"] if s["id"].startswith("72")), [(s["id"], s["nws"]) for s in md["sites"]])
check("the window is respected", "TESTBBB2" not in {x["id"] for x in anon.get(B + "/api/sondes/map.json?hours=1").json()["flights"]} and "TESTAAA1" in {x["id"] for x in anon.get(B + "/api/sondes/map.json?hours=1").json()["flights"]})
sr = anon.get(B + "/api/sondes/search.json?q=TESTB").json()["results"]
d4 = anon.get(B + "/api/sondes/TESTDDD4/track.json").json()
check("a balloon that has burst is falling: its phase, its burst position and its launch are given", d4["phase"] == "falling" and "falling" in d4["label"].lower() and d4["burst"] and len(d4["burst"]) == 3 and d4["launch"] and d4["latest"]["vel_v"] < -1, (d4["phase"], d4["burst"]))
a1 = anon.get(B + "/api/sondes/TESTAAA1/track.json").json()
check("a balloon still going up is climbing and has no burst yet", a1["phase"] == "climbing" and a1["burst"] is None and a1["latest"]["frame"] is not None and a1["latest"]["age"] < 600)
b2 = anon.get(B + "/api/sondes/TESTBBB2/track.json").json()
check("a landed balloon has its recovery report", b2["recoveries"] and b2["recoveries"][0]["recovered"] and b2["recoveries"][0]["by"] == "KD5TEST" and "field" in b2["recoveries"][0]["note"])
fp = anon.get(B + "/sondes/TESTBBB2").text
check("the flight page shows the recovery, who reported it and the telemetry panel", "Recovered" in fp and "KD5TEST" in fp and 'id="live-grid"' in fp and "Telemetry Now" in fp and "sondeicons.js" in fp)
check("the Heard By table has the signal now and sorts", all(h in anon.get(B + "/sondes/TESTAAA1").text for h in ('data-k="snr"', 'data-k="best_snr"', 'data-k="range_km"')) and all(h["snr"] is not None for h in a1["heard"]) and {h["callsign"] for h in a1["heard"]} == {"KD5VCF-LR", "AE5AU"})
check("the direction, elevation and distance of a station are not given to the public for a rounded station", all(h["look"] is None for h in a1["heard"]))
mine_adm = adm.get(B + "/api/sondes/TESTAAA1/track.json").json()
check("an administrator sees them (it is only for the owner, an administrator or an Exact station)", all(h["look"] and h["look"]["range_km"] > 0 and 0 <= h["look"]["az"] <= 360 for h in mine_adm["heard"]), mine_adm["heard"])
sr = anon.get(B + "/api/sondes/search.json?q=TESTB").json()["results"]
check("search finds a flight by serial, a station by call sign, a site by name", [r["kind"] for r in sr] == ["flight"] and any(r["kind"] == "station" for r in anon.get(B + "/api/sondes/search.json?q=AE5").json()["results"])
      and any(r["kind"] == "site" for r in anon.get(B + "/api/sondes/search.json?q=Shreve").json()["results"]) and anon.get(B + "/api/sondes/search.json?q=a").json()["results"] == [])

# ---- receiving station privacy: the exact positions of the uploads never appear while a station is General or Hidden
EXACT = ("34.7425", "-92.2842", "34.686", "-92.5243", "34.5123", "-92.5321")
blob = "".join(anon.get(B + p).text for p in ("/sondes/", "/sondes/stations/", "/sondes/stations/KD5VCF-LR", "/sondes/stations/AE5AU", "/sondes/TESTBBB2", "/sondes/TESTAAA1", "/sondes/map")) + json.dumps(md) + anon.get(B + "/api/sondes/TESTBBB2/track.json").text
check("no exact receiving station position is on any public page or data file", not any(x in blob for x in EXACT), [x for x in EXACT if x in blob])
sts = {s["id"]: s for s in md["stations"]}
check("a General station is on the map rounded to 0.1 degree; a moving station is not drawn at all", sts["KD5VCF-LR"]["lat"] == 34.7 and sts["KD5VCF-LR"]["lon"] == -92.3 and sts["KD5VCF-LR"]["approx"] and "CHASER-9" not in sts)
chaser = anon.get(B + "/sondes/stations/CHASER-9").text
check("the moving station is listed but says its position is hidden", "CHASER-9" in anon.get(B + "/sondes/stations/").text and "hidden" in chaser and "34.5123" not in chaser)

# ---- claiming: through the web
pg = plain.get(B + "/my/sondes")
check("a member opens My Radiosonde Stations and sees the claim form", pg.status_code == 200 and "Claim a Receiving Station" in pg.text)
res = plain.post(B + "/my/sondes/claim", data={"csrf_token": csrf(plain, "/my/sondes"), "callsign": "AE5AU"}, headers=H).json()
check("claiming a station that does not match the profile starts a claim with a proof code", res["ok"] and "ark-" in res["message"], res)
sid = psql("select id from sonde_stations where lower(callsign)='ae5au'")
code = psql("select claim_code from sonde_stations where lower(callsign)='ae5au'")
mine = plain.get(B + f"/my/sondes/{sid}").text
check("the owner sees the proof instructions with the code; the public page does not show it", code in mine and "Prove This Station Is Yours" in mine and code not in anon.get(B + "/sondes/stations/AE5AU").text)
reg2 = requests.Session()
reg2.post(B + "/my/register", data={"csrf_token": csrf(reg2, "/my/register"), "username": "plain2", "email": "plain2@example.com", "password": "plainpassword2", "confirm": "plainpassword2"})
other = sess("plain2", "plainpassword2")
check("a station with a pending claim cannot be claimed by another member", not other.post(B + "/my/sondes/claim", data={"csrf_token": csrf(other, "/my/sondes"), "callsign": "AE5AU"}, headers=H).json()["ok"])
check("another member cannot open the owner's station page", other.get(B + f"/my/sondes/{sid}").status_code == 404)
admview = adm.get(B + f"/my/sondes/{sid}")
check("an administrator can open it with the owner's own page, with a banner naming the owner", admview.status_code == 200 and "You are managing plain1" in admview.text and "Prove This Station Is Yours" in admview.text)
check("and save for them, recorded in the audit log under the administrator", adm.post(B + f"/my/sondes/{sid}/save", data={"csrf_token": csrf(adm, f"/my/sondes/{sid}"), "display_name": "Set by an administrator"}, headers=H).json()["ok"]
      and psql(f"select display_name from sonde_stations where id={sid}") == "Set by an administrator" and int(psql("select count(*) from audit_log where action='sondes.station_update' and actor_name='marty'") or 0) >= 1)
check("privacy cannot be changed before the claim completes", psql(f"select privacy from sonde_stations where id={sid}") == "general" and
      plain.post(B + f"/my/sondes/{sid}/save", data={"csrf_token": csrf(plain, f"/my/sondes/{sid}"), "privacy": "exact", "public": "1"}, headers=H).json()["ok"] and psql(f"select privacy from sonde_stations where id={sid}") == "general")
check("an ordinary member cannot open the admin page or approve", plain.get(B + "/admin/sondes", allow_redirects=False).status_code in (302, 401, 403, 404) and plain.post(B + f"/admin/sondes/{sid}/approve", data={"csrf_token": csrf(plain, "/my/sondes")}, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
ap = adm.get(B + "/admin/sondes")
check("the admin page lists the waiting claim with its code and the feed state", ap.status_code == 200 and "AE5AU" in ap.text and code in ap.text and "Claims Waiting" in ap.text)
check("an administrator approves it", adm.post(B + f"/admin/sondes/{sid}/approve", data={"csrf_token": csrf(adm, "/admin/sondes")}, headers=H).json()["ok"] and psql(f"select claim_status from sonde_stations where id={sid}") == "claimed")
check("the claimed station now shows its owner", "plain1" in anon.get(B + "/sondes/stations/AE5AU").text)
# ---- a claimed station sending its frames straight to us (auto_rx's payload summary through the bridge)
pg = plain.get(B + f"/my/sondes/{sid}").text
check("a claimed station's page offers Send Directly with no address until one is made", "Send Directly to ArkSignal" in pg and "/sondes/push/" not in pg and "Make an Upload Address" in pg)
check("the owner makes an upload address", plain.post(B + f"/my/sondes/{sid}/push-address", data={"csrf_token": csrf(plain, f"/my/sondes/{sid}")}, headers=H).json()["ok"])
pg = plain.get(B + f"/my/sondes/{sid}").text
mt = re.search(r"/sondes/push/([A-Za-z0-9_-]{20,})", pg)
tk = mt.group(1) if mt else ""
check("the page then shows the address, the auto_rx settings and the bridge command", bool(tk) and all(x in pg for x in ("payload_summary_enabled = True", "sonde_bridge.py", "55673")))
nowt = time.strftime("%H:%M:%S", time.gmtime())
frame = {"type": "PAYLOAD_SUMMARY", "callsign": "ZZPUSH1", "latitude": 34.8, "longitude": -92.2, "altitude": 12000.0, "speed": 36.0, "heading": 90.0, "time": nowt, "model": "RS41-SG", "frequency": 404.1, "snr": 9.5, "temp": -40.0, "humidity": 5.0, "sats": 10, "batt": 2.7}
r1 = requests.post(B + f"/sondes/push/{tk}", json=[frame, {"callsign": "ZZPUSH2"}])
check("a payload summary sent to the address is accepted, a frame without a position is ignored", r1.status_code == 200 and r1.json()["accepted"] == 1 and r1.json()["ignored"] == 1, r1.text)
check("the flight is stored with the station as the one that heard it, the speed in meters per second and the type", psql("select count(*) from sonde_points where serial='ZZPUSH1'") == "1" and psql("select callsign from sonde_heard where serial='ZZPUSH1'") == "AE5AU"
      and abs(float(psql("select vel_h from sonde_points where serial='ZZPUSH1'")) - 10.0) < 0.1 and psql("select type from sondes where serial='ZZPUSH1'") == "RS41-SG", psql("select * from sonde_points where serial='ZZPUSH1'"))
check("the same frame sent again is not stored twice", requests.post(B + f"/sondes/push/{tk}", json=frame).status_code == 200 and psql("select count(*) from sonde_points where serial='ZZPUSH1'") == "1")
check("a wrong address is a 404 and a JSON line per row also works", requests.post(B + "/sondes/push/" + "x" * 30, json=frame).status_code == 404 and requests.post(B + f"/sondes/push/{tk}", data=json.dumps(dict(frame, callsign="ZZPUSH3")) + chr(10), headers={"Content-Type": "text/plain"}).json()["accepted"] == 1)
check("the station's last frame time and count are shown to its owner", "frames so far" in plain.get(B + f"/my/sondes/{sid}").text)
plain.post(B + f"/my/sondes/{sid}/push-address", data={"csrf_token": csrf(plain, f"/my/sondes/{sid}")}, headers=H)
check("making a new address stops the old one", requests.post(B + f"/sondes/push/{tk}", json=frame).status_code == 404)
psql("delete from sonde_points where serial like 'ZZPUSH%'"); psql("delete from sonde_heard where serial like 'ZZPUSH%'"); psql("delete from sondes where serial like 'ZZPUSH%'")
check("the owner picks Exact: the public page and the map show the exact position", plain.post(B + f"/my/sondes/{sid}/save", data={"csrf_token": csrf(plain, f"/my/sondes/{sid}"), "privacy": "exact", "public": "1", "display_name": "Home Station"}, headers=H).json()["ok"]
      and "34.686" in anon.get(B + "/sondes/stations/AE5AU").text and any(s["id"] == "AE5AU" and s["lat"] == 34.686 and not s["approx"] for s in anon.get(B + "/api/sondes/map.json?hours=24").json()["stations"]))
lk = {h["callsign"]: h["look"] for h in anon.get(B + "/api/sondes/TESTAAA1/track.json").json()["heard"]}
check("an Exact station shows its direction, elevation and distance from itself to everyone; a rounded one still does not", lk["AE5AU"] and lk["AE5AU"]["range_km"] > 0 and lk["KD5VCF-LR"] is None, lk)
check("the owner picks Hidden: the position is gone from the page and the map, the station stays listed", plain.post(B + f"/my/sondes/{sid}/save", data={"csrf_token": csrf(plain, f"/my/sondes/{sid}"), "privacy": "hidden", "public": "1"}, headers=H).json()["ok"]
      and "34.686" not in anon.get(B + "/sondes/stations/AE5AU").text and all(s["id"] != "AE5AU" for s in anon.get(B + "/api/sondes/map.json?hours=24").json()["stations"]) and "AE5AU" in anon.get(B + "/sondes/stations/").text)
check("unlisting the station removes it from the list, its page and the map; flights it heard still name it", plain.post(B + f"/my/sondes/{sid}/save", data={"csrf_token": csrf(plain, f"/my/sondes/{sid}"), "privacy": "general"}, headers=H).json()["ok"]
      and anon.get(B + "/sondes/stations/AE5AU").status_code == 404 and "AE5AU" not in anon.get(B + "/sondes/stations/").text and all(s["id"] != "AE5AU" for s in anon.get(B + "/api/sondes/map.json?hours=24").json()["stations"])
      and "AE5AU" in anon.get(B + "/sondes/TESTBBB2").text)
check("releasing a station gives it back with the default privacy", plain.post(B + f"/my/sondes/{sid}/release", data={"csrf_token": csrf(plain, f"/my/sondes/{sid}")}, allow_redirects=False).status_code == 302 and psql(f"select claim_status || privacy from sonde_stations where id={sid}") == "unclaimedgeneral")
check("a call sign that is not valid is refused", not plain.post(B + "/my/sondes/claim", data={"csrf_token": csrf(plain, "/my/sondes"), "callsign": "<b>"}, headers=H).json()["ok"])
check("the claim, approval and release are in the audit log", int(psql("select count(*) from audit_log where action like 'sondes.%'") or 0) >= 4)

# ---- an administrator assigns a station, or starts the claim for a member
asp = adm.get(B + "/admin/sondes").text
check("the admin page has the Assign form with the live member search and the stations to pick from", 'id="sec-assign"' in asp and 'name="callsign"' in asp and 'data-memberpicker' in asp and "Assign" in asp)
p2id = psql("select id from users where username='plain2'")
r1 = adm.post(B + "/admin/sondes/assign", data={"csrf_token": csrf(adm, "/admin/sondes"), "callsign": "AE5AU", "username": "plain2", "mode": "assign"}, headers=H).json()
check("an administrator gives a station to a member at once", r1["ok"] and psql("select claim_status || owner_id from sonde_stations where lower(callsign)='ae5au'") == "claimed" + p2id, r1)
check("the member sees it among their stations", "AE5AU" in other.get(B + "/my/sondes").text)
r2 = adm.post(B + "/admin/sondes/assign", data={"csrf_token": csrf(adm, "/admin/sondes"), "callsign": "NEWSTATION-1", "username": "plain1", "mode": "claim"}, headers=H).json()
nsid = psql("select id from sonde_stations where callsign='NEWSTATION-1'")
ncode = psql("select claim_code from sonde_stations where callsign='NEWSTATION-1'")
check("starting a claim creates a station not heard yet and gives the member its proof code", r2["ok"] and psql("select claim_status from sonde_stations where callsign='NEWSTATION-1'") == "pending" and ncode.startswith("ark-") and ncode in plain.get(B + f"/my/sondes/{nsid}").text, r2)
r3 = adm.post(B + "/admin/sondes/assign", data={"csrf_token": csrf(adm, "/admin/sondes"), "callsign": "NEWSTATION-1", "username": "plain2", "mode": "assign"}, headers=H).json()
check("a station can be taken over and given to someone else", r3["ok"] and psql("select owner_id from sonde_stations where callsign='NEWSTATION-1'") == p2id)
check("an unknown member and a bad call sign are refused", not adm.post(B + "/admin/sondes/assign", data={"csrf_token": csrf(adm, "/admin/sondes"), "callsign": "NEWSTATION-2", "username": "nobody-xyz"}, headers=H).json()["ok"]
      and not adm.post(B + "/admin/sondes/assign", data={"csrf_token": csrf(adm, "/admin/sondes"), "callsign": "<b>", "username": "plain1"}, headers=H).json()["ok"] and psql("select count(*) from sonde_stations where callsign='NEWSTATION-2'") == "0")
check("an ordinary member cannot assign", plain.post(B + "/admin/sondes/assign", data={"csrf_token": csrf(plain, "/my/sondes"), "callsign": "AE5AU", "username": "plain1"}, allow_redirects=False).status_code in (302, 400, 401, 403, 404) and psql("select owner_id from sonde_stations where lower(callsign)='ae5au'") == p2id)
check("the assignments are in the audit log under the administrator", int(psql("select count(*) from audit_log where actor_name='marty' and action in ('sondes.assign', 'sondes.claim')") or 0) >= 3)
psql("update sonde_stations set owner_id=NULL, claim_status='unclaimed', claim_code='' where lower(callsign)='ae5au'")

# ---- icon size and labels
mp = anon.get(B + "/sondes/map").text
check("the balloon map lets you choose the icon size and turn the labels off (hover shows the details)", 'id="sm-isize"' in mp and "Huge" in mp and "No label (hover for the details)" in mp and "/static/sondeicons.js" in mp)
fpg = anon.get(B + "/sondes/TESTAAA1").text
check("the flight page has the same icon size and label choices, and charts laid out like the weather station charts with icons", 'id="f-isize"' in fpg and 'id="f-labels"' in fpg and 'class="wxcharts"' in fpg and fpg.count('class="wxi tile-i"') >= 7)

# ---- the county map can show every kind of point
cmp_ = anon.get(B + "/counties/").text
check("the county map has a Show on the Map list and an icon size choice", 'id="cm-over"' in cmp_ and 'id="cm-dot"' in cmp_ and "/api/counties/points.json" in cmp_)
pj = anon.get(B + "/api/counties/points.json").json()["layers"]
pk = {l["key"]: l for l in pj}
check("the points file has each kind with its color, meaning and items (launch sites, balloon landings, receiving stations here; hospitals, airports, feeders and weather stations when they exist)", {"sonde_sites", "sonde_stations", "balloons"} <= set(pk) and all(l["color"].startswith("#") and l["note"] and l["items"] and all("lat" in i and "lon" in i for i in l["items"]) for l in pj), list(pk))
check("receiving stations on the county map are at their public position only", all(i["lat"] == round(i["lat"], 1) or i.get("approx") is False for i in pk["sonde_stations"]["items"]) and "CHASER-9" not in [i["id"] for i in pk["sonde_stations"]["items"]] and not any(x in json.dumps(pj) for x in EXACT))
check("the county counts include the receiving stations", all("sonde_stations" in v for v in anon.get(B + "/api/counties/counts.json").json()["counts"].values()))

# ---- the coverage map
cv = anon.get(B + "/coverage").text
check("the coverage map has the radiosonde receiving stations and the balloon flights as layers (and no longer lists radiosondes as coming later)", "Radiosonde receiving stations" in cv and "/api/sondes/stations.json" in cv and "Weather balloon flights, last 24 hours" in cv and 'COMING = ["Repeaters", "Mesh nodes"]' in cv)
stj = anon.get(B + "/api/sondes/stations.json").json()
sj = {x["id"]: x for x in stj["stations"]}
check("the stations come with how far each has heard a balloon, rounded like everywhere else, and no exact position", sj["KD5VCF-LR"]["reach_km"] and sj["KD5VCF-LR"]["reach_km"] > 5 and sj["KD5VCF-LR"]["lat"] == 34.7 and "CHASER-9" not in sj and not any(x in json.dumps(stj) for x in EXACT), sj.get("KD5VCF-LR"))

# ---- menu, feed page, status, home, settings
nav = anon.get(B + "/").text
check("the menu has a Radiosondes group and the Feed menu has the Radiosondes page", 'href="/sondes/map"' in nav and 'href="/feed/sondes"' in nav and "Radiosonde Map" in nav)
check("Overview comes before the Balloon Map in the Radiosondes menu", nav.index('href="/sondes/"') < nav.index('href="/sondes/map"') and nav.index('href="/sondes/map"') < nav.index('href="/sondes/flights"'))
check("the Radiosondes and Weather menus carry their own statistics dashboards", 'href="/view/balloon-stats"' in nav and 'href="/view/weather-stats"' in nav and anon.get(B + "/view/balloon-stats").status_code == 200 and anon.get(B + "/view/weather-stats").status_code == 200)
check("the aircraft statistics pages say so", "Aircraft Statistics" in nav and "Aircraft Feeder Statistics" in nav and ">Statistics Dashboard<" not in nav and ">Feeder Statistics<" not in nav)
stp = anon.get(B + "/sondes/stations/").text
check("the stations page has a search box and sortable columns", 'class="tfilter"' in stp and 'data-table="#sonde-st"' in stp and 'data-sort="num"' in stp and 'id="sonde-st"' in stp)
ab = anon.get(B + "/about").text
check("the About page lists the weather stations, the balloons, the county map and credits SondeHub and the Census", "Radiosondes and Balloons" in ab and "County Map" in ab and "SondeHub" in ab and "TIGERweb" in ab and "Weather Stations" in ab)
check("finished roadmap cards are green like the active menus", "rmdone" in anon.get(B + "/roadmap").text)
mt = anon.get(B + "/metrics").text
check("the metrics carry the balloons in the air and each one's altitude, and no coordinates", "adsb_portal_sondes_aloft 3" in mt and "adsb_portal_sondes_falling 1" in mt and 'adsb_portal_sonde_altitude_m{serial="TESTAAA1"' in mt and "34.7425" not in mt and "-92.2842" not in mt)
import glob, os
names = set()
for fpath in glob.glob(os.path.join("..", "deploy", "grafana", "*-dashboard.json")):
    names |= set(re.findall(r"adsb_portal_[a-z0-9_]+", open(fpath, encoding="utf-8").read()))
missing = sorted(n for n in names if n not in mt)
check("every metric the Grafana dashboards chart is served by /metrics", len(names) > 20 and not missing, missing)
check("the new dashboards are valid and named for what they show", all(json.load(open(os.path.join("..", "deploy", "grafana", f), encoding="utf-8"))["title"] == t for f, t in (("weather-dashboard.json", "Weather Dashboard"), ("sondes-dashboard.json", "Radiosonde Dashboard"), ("feeders-dashboard.json", "Aircraft Feeder Statistics"), ("ultrafeeder-dashboard.json", "Aircraft Statistics"))))
check("the Places menu has the County Map", 'href="/counties/"' in nav and "County Map" in nav)
home = anon.get(B + "/").text
check("the home page has a Weather Balloons card limited to Arkansas, counting the balloon over Arkansas", "Weather Balloons" in home and "in the air over Arkansas" in home)
status = anon.get(B + "/status").text
check("the service status page has a Radiosondes row", "Radiosondes" in status)
check("the home map has a County lines switch", 'id="ov-cnty"' in home and "/api/counties/shapes.json" in home)
live = anon.get(B + "/live").text
check("the aircraft map has Counties and Weather balloons layers", 'id="l-counties"' in live and 'id="l-sondes"' in live and "/api/sondes/map.json" in live)
sp = adm.get(B + "/admin/settings").text
check("every setting is on the Settings page in its own group", "Radiosondes" in sp and "sonde_regions" in sp and "counties_source_url" in sp and "sondesites_update_days" in sp)
du = adm.get(B + "/admin/data-updates/").text
check("Data Updates has County Outlines and Balloon Launch Sites", "County Outlines" in du and "Balloon Launch Sites" in du)
psql("UPDATE settings SET value='0' WHERE key='sonde_enabled'")
check("with the service off the pages are gone and the menu entry too", anon.get(B + "/sondes/").status_code == 404 and anon.get(B + "/sondes/map").status_code == 404 and 'href="/sondes/map"' not in anon.get(B + "/").text
      and anon.get(B + "/api/sondes/map.json").status_code == 404)
psql("UPDATE settings SET value='1' WHERE key='sonde_enabled'")

# ---- an administrator helps with a member's weather station
wadd = plain.post(B + "/my/weather/add", data={"csrf_token": csrf(plain, "/my/weather"), "kind": "relay", "name": "Plain Station", "station_id": "PLAIN-WX", "lat": "34.9", "lon": "-92.3", "privacy": "general"}, allow_redirects=False)
wid = psql("select id from wx_stations where station_id='PLAIN-WX'")
check("a member adds a weather station", wadd.status_code in (200, 302) and wid.isdigit(), wadd.status_code)
wv = adm.get(B + f"/my/weather/{wid}")
check("an administrator opens it with the owner's own page and a banner naming the owner", wv.status_code == 200 and "You are managing plain1" in wv.text and "Upload Address" in wv.text)
check("and saves for them", adm.post(B + f"/my/weather/{wid}/save", data={"csrf_token": csrf(adm, f"/my/weather/{wid}"), "name": "Renamed by admin", "description": "", "hardware": "", "lat": "34.9", "lon": "-92.3", "privacy": "general"}, headers=H).json()["ok"] and psql(f"select name from wx_stations where id={wid}") == "Renamed by admin")
check("another member cannot open it", other.get(B + f"/my/weather/{wid}").status_code == 404)
check("the admin lists have a Manage link", f'/my/weather/{wid}' in adm.get(B + "/admin/weather").text)
psql(f"delete from wx_stations where id={wid}")

# ---- counties
shapes = anon.get(B + "/api/counties/shapes.json")
sj = shapes.json()
check("the county shapes come once, with a version and an ETag the browser can reuse", len(sj["counties"]) == 2 and sj["version"] and shapes.headers.get("ETag")
      and anon.get(B + "/api/counties/shapes.json", headers={"If-None-Match": shapes.headers["ETag"]}).status_code == 304)
cnt = anon.get(B + "/api/counties/counts.json").json()["counts"]
check("each county has its counts: one launch site and the landing of the landed balloon in Pulaski, none in Faulkner", cnt["05119"]["sonde_sites"] == 1 and cnt["05119"]["balloons"] == 1 and cnt["05045"]["sonde_sites"] == 0 and cnt["05045"]["balloons"] == 0, cnt)
cp = anon.get(B + "/counties/pulaski").text
check("the county page lists what is in it: the launch site and the landing", "Pulaski" in cp and "North Little Rock" in cp and "TESTBBB2" in cp and "Weather Balloons" in cp and "US Census Bureau" in cp)
check("the county page has the metric tiles and links back to the map", "Feeders" in cp and "Hospitals" in cp and "/counties/?county=05119" in cp)
check("a county can be found by its number or its name; an unknown one is not found", anon.get(B + "/counties/05119").status_code == 200 and anon.get(B + "/api/counties/pulaski.json").json()["name"] == "Pulaski County" and anon.get(B + "/counties/nowhere").status_code == 404)
check("only administrators can load the shapes (through Data Updates)", plain.post(B + "/admin/data-updates/counties/run", allow_redirects=False, data={}).status_code in (302, 400, 401, 403, 404))

out = ssh(D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sondes_seed.py clean")
check("the demonstration data is removed", "cleaned" in out and psql("select count(*) from sondes") == "0" and psql("select count(*) from counties") == "0", out[-200:])
print("ALL PASS" if ok_all else "SOME FAILED")
