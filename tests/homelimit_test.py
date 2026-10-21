import json, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout
def psql(sql): return ssh(D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
# the unit checks of the state outline run inside the portal container
subprocess.run(["scp", "-q", "arkansas_unit.py", "nastemp:adsb-portal/arkansas_unit.py"], check=True, capture_output=True)
ssh(D + "cp ~/adsb-portal/arkansas_unit.py adsb-portal:/tmp/arkansas_unit.py")
out = ssh(D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/arkansas_unit.py")
check("the state outline checks pass inside the portal", "ALL PASS" in out and "FAIL" not in out, out[-400:])
anon = requests.Session()
setting("public_cache_seconds", "0"); setting("home_limit_ar", "1"); setting("home_limit_buffer_miles", "15"); setting("wx_enabled", "1")
psql("DELETE FROM wx_stations WHERE station_id LIKE 'LIM%'"); psql("DELETE FROM feeders WHERE uuid LIKE 'limit0%'")
def feeder(u, sid, lat, lon):
    psql(f"INSERT INTO feeders (uuid, station_id, name, owner_id, lat, lon, privacy, online, last_ip, last_seen) VALUES ('{u}', '{sid}', '{sid}', (SELECT id FROM users WHERE username='marty'), {lat}, {lon}, 'exact', TRUE, '203.0.113.9', now())")
base_ar = anon.get(B + "/api/aircraft.json").json()["totals"].get("stations_online_ar", 0)
feeder("limit000-0000-4000-8000-000000000001", "LIM-AR", 34.75, -92.3)
feeder("limit000-0000-4000-8000-000000000002", "LIM-TX", 32.78, -96.8)
t = anon.get(B + "/api/aircraft.json").json()["totals"]
check("the stations-online count for the home page leaves out a station in Dallas but keeps the one in Little Rock", t["stations_online_ar"] == base_ar + 1 and t["stations_online"] >= t["stations_online_ar"] + 1, t)
home = anon.get(B + "/").text
layers = json.loads(re.search(r"var LAYERS = (\[.*?\]), M = ", home, re.S).group(1))
ids = {p["id"] for ly in layers for p in ly["points"]}
check("the home map shows the Arkansas station and not the Dallas one", "LIM-AR" in ids and "LIM-TX" not in ids, ids)
check("the page hands its script the state outline", '"on": true' in home.replace(": ", ": ") or '"on":true' in home, home[home.find("var AR"):home.find("var AR") + 120])
# weather stations: one in Little Rock, one in Dallas
for sid, lat, lon in (("LIM-WX-AR", 34.75, -92.3), ("LIM-WX-TX", 32.78, -96.8)):
    temp = 70.0 if "AR" in sid else 99.0
    psql(f"INSERT INTO wx_stations (station_id, owner_id, name, kind, token_hash, public, lat, lon, privacy, latest, last_report) VALUES ('{sid}', (SELECT id FROM users WHERE username='marty'), '{sid}', 'relay', 'limhash{sid}', TRUE, {lat}, {lon}, 'exact', '{{' || chr(34) || 'temp_f' || chr(34) || ': {temp}, ' || chr(34) || 'hum' || chr(34) || ': 50}}', now())")
layers = json.loads(re.search(r"var LAYERS = (\[.*?\]), M = ", anon.get(B + "/").text, re.S).group(1))
wx = [p["id"] for ly in layers if ly["key"] == "wx" for p in ly["points"]]
check("the home map's weather layer keeps the Arkansas station and drops the Dallas one", "LIM-WX-AR" in wx and "LIM-WX-TX" not in wx, wx)
page = anon.get(B + "/").text
check("the weather card averages only Arkansas stations (70, not 85) and counts only them", "average 70" in re.sub(r"\s+", " ", page) and "of 1 station" in re.sub(r"\s+", " ", page), re.search(r"Weather</h3>.*?</a>", page, re.S).group(0)[:400] if "Weather</h3>" in page else "no card")
setting("home_limit_ar", "0")
t = anon.get(B + "/api/aircraft.json").json()["totals"]
home = anon.get(B + "/").text
layers = json.loads(re.search(r"var LAYERS = (\[.*?\]), M = ", home, re.S).group(1))
ids = {p["id"] for ly in layers for p in ly["points"]}
check("with the limit switched off everything shows again", "LIM-TX" in ids and "LIM-WX-TX" in ids and "in Arkansas" not in home, ids)
setting("home_limit_ar", "1")
psql("DELETE FROM wx_stations WHERE station_id LIKE 'LIM%'"); psql("DELETE FROM feeders WHERE uuid LIKE 'limit0%'")
print("ALL PASS" if ok_all else "SOME FAILED")
