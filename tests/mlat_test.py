import json, re, subprocess, requests, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout.strip()
def dk(args): return ssh(f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker {args}")
def psql(sql): return dk(f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"")

adm = sess("marty", PW)
UU = "test0000-0000-4000-8000-00000000ml01"
psql(f"DELETE FROM feeders WHERE uuid='{UU}'")
psql(f"INSERT INTO feeders (uuid, station_id, name, owner_id) VALUES ('{UU}', 'TMLAT1', 'MLAT Test Station', (SELECT id FROM users WHERE username='marty'))")
psql("INSERT INTO settings (key, value) VALUES ('mlat_enabled','1') ON CONFLICT (key) DO UPDATE SET value='1'")
psql("INSERT INTO settings (key, value) VALUES ('public_cache_seconds','0') ON CONFLICT (key) DO UPDATE SET value='0'")
dk("exec -u root adsb-portal rm -f /mlat/clients.json")
time.sleep(11)  # the portal caches the file briefly
html = adm.get(B + "/admin/mlat").text
check("without the server's folder the page explains it is not available", "status folder is not available" in html)

doc = {"mlatone": {"user": "mlatone", "uid": 0, "uuid": UU, "coords": "34.812345,-92.398765", "lat": 34.812345, "lon": -92.398765, "alt": 100.0,
                   "privacy": False, "connection": "x", "source_ip": "203.0.113.77", "source_port": "5555", "message_rate": 321, "peer_count": 4,
                   "outlier_percent": 1.5, "bad_sync_timeout": 0},
       "stranger": {"user": "stranger", "uuid": "nobody", "lat": 35.5, "lon": -91.5, "source_ip": "198.51.100.9", "message_rate": 5,
                    "peer_count": 0, "outlier_percent": 0.0, "bad_sync_timeout": 45}}
open("clients_fixture.json", "w").write(json.dumps(doc))
subprocess.run(["scp", "-q", "clients_fixture.json", "nastemp:adsb-portal/clients_fixture.json"], check=True, capture_output=True)
dk("cp ~/adsb-portal/clients_fixture.json adsb-portal:/mlat/clients.json")
time.sleep(11)
html = adm.get(B + "/admin/mlat").text
check("the page lists both connected stations", "mlatone" in html and "stranger" in html)
check("the matched station links to its feeder page", f"/admin/feeders/{UU}" in html and "MLAT Test Station" in html)
check("the unmatched one is marked as not matched", "Not matched to a feeder" in html)
check("rates, peers and sync problems show", ">321<" in html and ">4<" in html and "Timed out 45 s" in html)
check("exact coordinates and addresses never appear", not re.search(r"34\.81|92\.39|203\.0\.113|198\.51\.100|35\.5|91\.5", html))
fp = adm.get(B + f"/admin/feeders/{UU}").text
check("the feeder page has an MLAT row", "4 peers" in fp and "321 msgs/s" in fp)
check("coordinates are not on the feeder page either", "203.0.113" not in fp and "34.812345" not in fp)
open("aircraft_fixture.json", "w").write(json.dumps({"ABCDEF": {"icao": "ABCDEF", "elapsed_seen": 2.0, "allow_mlat": 1, "tracking": 5, "mlat_result_count": 77, "adsb_seen": 0}}))
subprocess.run(["scp", "-q", "aircraft_fixture.json", "nastemp:adsb-portal/aircraft_fixture.json"], check=True, capture_output=True)
dk("cp ~/adsb-portal/aircraft_fixture.json adsb-portal:/mlat/aircraft.json"); time.sleep(11)
ac = requests.get(B + "/api/mlat/aircraft/abcdef.json").json()
check("the aircraft endpoint gives counts only", ac == {"stations_hearing": 5, "mlat_positions": 77, "allowed": True, "seconds_ago": 2.0}, ac)
check("an unknown aircraft gives an empty answer", requests.get(B + "/api/mlat/aircraft/123456.json").json() == {})
check("a malformed hex is refused", requests.get(B + "/api/mlat/aircraft/zzzzzz.json").status_code == 400)
live = requests.get(B + "/live").text
check("the live map card has the How it is tracked block", 'id="c-how"' in live and "MLAT (multilateration)" in live)
mine = adm.get(B + f"/my/feeders/{UU}").text
check("the owner's station page says MLAT is working with the numbers", "sec-mlat" in mine and "Working: synchronised with 4 other stations" in mine and ">321<" in mine)
check("the owner's page has no coordinates or addresses", "203.0.113" not in mine)
myip = re.search(r"Your public IP address is <span class=\"mono\">([^<]+)</span>", requests.get(B + "/feed/check").text).group(1)
psql(f"UPDATE feeders SET last_ip='{myip}', last_seen=now() WHERE uuid='{UU}'")
fc = requests.get(B + "/feed/?check=1").text
check("Am I Feeding shows MLAT working for a claimed station (its UUID stays hidden)", "TMLAT1" in fc and "working" in fc and UU not in fc and "Working: synchronised with 4 other stations" in fc, re.findall(r"MLAT:.{0,160}", fc, re.S)[:1])
pub = requests.get(B + "/feeders/").text
check("the public station list flags the working MLAT station", re.search(r"TMLAT1.*?MLAT</span>", pub, re.S) is not None)
pp = requests.get(B + "/feeders/TMLAT1").text
check("the public station page shows MLAT working and nothing more", ">MLAT<" in pp and "321" not in pp.split("MLAT")[1][:200])
api = requests.get(B + "/api/feeders.json").json()
check("the public API carries an mlat flag", any(f["station_id"] == "TMLAT1" and f["mlat"] is True for f in api["feeders"]))
check("the admin feeder list has an MLAT column", ">working<" in adm.get(B + "/admin/feeders").text)
# a station that is connected but alone, and one in trouble
doc["mlatone"]["peer_count"] = 0
open("clients_fixture.json", "w").write(json.dumps(doc))
subprocess.run(["scp", "-q", "clients_fixture.json", "nastemp:adsb-portal/clients_fixture.json"], check=True, capture_output=True)
dk("cp ~/adsb-portal/clients_fixture.json adsb-portal:/mlat/clients.json"); time.sleep(11)
mine = adm.get(B + f"/my/feeders/{UU}").text
check("a station with no peers is told what MLAT needs", "waiting for other stations" in mine and "three or more stations" in mine)
check("and is not flagged working publicly", not re.search(r"TMLAT1.*?MLAT</span>", requests.get(B + "/feeders/").text, re.S))
doc["mlatone"]["bad_sync_timeout"] = 30; doc["mlatone"]["peer_count"] = 3
open("clients_fixture.json", "w").write(json.dumps(doc))
subprocess.run(["scp", "-q", "clients_fixture.json", "nastemp:adsb-portal/clients_fixture.json"], check=True, capture_output=True)
dk("cp ~/adsb-portal/clients_fixture.json adsb-portal:/mlat/clients.json"); time.sleep(11)
check("a station in sync trouble is told to check its coordinates", "clock sync is bad" in adm.get(B + f"/my/feeders/{UU}").text)
dk("exec -u root adsb-portal rm -f /mlat/clients.json"); time.sleep(11)
check("with the file gone the owner sees the server is not reporting", "not reporting" in adm.get(B + f"/my/feeders/{UU}").text)
psql("INSERT INTO settings (key, value) VALUES ('mlat_enabled','0') ON CONFLICT (key) DO UPDATE SET value='0'")
check("with MLAT switched off the owner page has no MLAT section", "sec-mlat" not in adm.get(B + f"/my/feeders/{UU}").text)
# the network page and its data
psql("INSERT INTO settings (key, value) VALUES ('mlat_enabled','1') ON CONFLICT (key) DO UPDATE SET value='1'")
U2 = "test0000-0000-4000-8000-00000000ml02"
psql(f"DELETE FROM feeders WHERE uuid='{U2}'")
psql(f"INSERT INTO feeders (uuid, station_id, name, owner_id, lat, lon, privacy) VALUES ('{U2}', 'TMLAT2', 'Second Test', (SELECT id FROM users WHERE username='marty'), 35.26, -92.93, 'general')")
psql(f"UPDATE feeders SET lat=34.812345, lon=-92.398765, privacy='general' WHERE uuid='{UU}'")
doc["mlatone"].update(peer_count=1, bad_sync_timeout=0)
doc["mlattwo"] = {"user": "mlattwo", "uuid": U2, "lat": 35.2612, "lon": -92.9345, "source_ip": "203.0.113.88", "message_rate": 50, "peer_count": 1, "outlier_percent": 0.5, "bad_sync_timeout": 0}
sync = {"mlatone": {"peers": {"mlattwo": [40, 0.8, 5, 0, 0, 3, 5, 9]}, "bad_syncs": 0, "lat": 34.81, "lon": -92.39},
        "mlattwo": {"peers": {"mlatone": [40, 0.8, 5, 0, 0, 3, 5, 9], "stranger": [10, 3.0, 5, 0, 0, 3, 5, 9]}, "bad_syncs": 0, "lat": 35.26, "lon": -92.93}}
for name, obj, dest in (("clients_fixture.json", doc, "clients.json"), ("sync_fixture.json", sync, "sync.json")):
    open(name, "w").write(json.dumps(obj))
    subprocess.run(["scp", "-q", name, f"nastemp:adsb-portal/{name}"], check=True, capture_output=True)
    dk(f"cp ~/adsb-portal/{name} adsb-portal:/mlat/{dest}")
time.sleep(11)
net = requests.get(B + "/api/mlat/network.json").json()
st = {s["id"]: s for s in net["stations"]}
check("the network data lists both named stations with their peers", set(st) == {"TMLAT1", "TMLAT2"} and st["TMLAT1"]["peer_count"] == 1 and st["TMLAT1"]["peers"][0]["id"] == "TMLAT2", net)
check("peer quality and distance are worked out", st["TMLAT1"]["peers"][0]["quality"] == "good" and 60 < st["TMLAT1"]["closest_km"] < 90, st["TMLAT1"])
check("a peer that is not a named station is left out", len(st["TMLAT2"]["peers"]) == 1)
raw = requests.get(B + "/api/mlat/network.json").text
check("only privacy-applied places are published, never exact ones, addresses or MLAT user names", st["TMLAT1"]["lat"] == 34.8 and "34.812345" not in raw and "203.0.113" not in raw and "mlatone" not in raw, raw[:300])
pg = requests.get(B + "/mlat/").text
check("the public MLAT network page renders with the stations", "MLAT <span>Network</span>" in pg and "TMLAT1" in pg and "mlatmap" in pg)
check("the Feeders page links to it", "/mlat/" in requests.get(B + "/feeders/").text)
psql(f"DELETE FROM feeders WHERE uuid='{U2}'")
# matching by saved MLAT user name, with the Station ID as the fallback
U3 = "test0000-0000-4000-8000-00000000ml03"
psql(f"DELETE FROM feeders WHERE uuid='{U3}'")
psql(f"INSERT INTO feeders (uuid, station_id, name, owner_id) VALUES ('{U3}', 'TMLAT3', 'Old Name Station', (SELECT id FROM users WHERE username='marty'))")
def put(users):
    open("clients_fixture.json", "w").write(json.dumps({u: {"user": u, "uuid": "unrelated-" + u, "message_rate": 9, "peer_count": 2, "outlier_percent": 0.0, "bad_sync_timeout": 0} for u in users}))
    subprocess.run(["scp", "-q", "clients_fixture.json", "nastemp:adsb-portal/clients_fixture.json"], check=True, capture_output=True)
    dk("cp ~/adsb-portal/clients_fixture.json adsb-portal:/mlat/clients.json"); time.sleep(11)
put(["TMLAT3"])
check("with no MLAT User Name saved, a client named like the Station ID is matched", f"/admin/feeders/{U3}" in adm.get(B + "/admin/mlat").text)
put(["MyOldName"])
check("an unrelated name is not matched", f"/admin/feeders/{U3}" not in adm.get(B + "/admin/mlat").text)
ok, msg, _ = act(adm, f"/my/feeders/{U3}/save", f"/my/feeders/{U3}", privacy="general", mlat_user="bad name!")
check("an invalid MLAT User Name is refused with a reason", not ok and "MLAT User Name" in msg, msg)
ok, msg, _ = act(adm, f"/my/feeders/{U3}/save", f"/my/feeders/{U3}", privacy="general", mlat_user="MyOldName")
check("a valid MLAT User Name saves", ok, msg)
time.sleep(11)
check("then the existing MLAT_USER is matched to the station", f"/admin/feeders/{U3}" in adm.get(B + "/admin/mlat").text)
check("the station page shows the field", 'name="mlat_user"' in adm.get(B + f"/my/feeders/{U3}").text and 'value="MyOldName"' in adm.get(B + f"/my/feeders/{U3}").text)
psql(f"DELETE FROM feeders WHERE uuid='{U3}'")
anon = requests.get(B + "/admin/mlat", allow_redirects=False)
check("the page is not public", anon.status_code in (302, 401, 403))
psql(f"DELETE FROM feeders WHERE uuid='{UU}'")
dk("exec -u root adsb-portal rm -f /mlat/clients.json")
import os; [os.path.exists(f) and os.remove(f) for f in ("clients_fixture.json", "aircraft_fixture.json", "sync_fixture.json")]
print("ALL PASS" if ok_all else "SOME FAILED")
