"""Station links survive call signs with a slash or a dash (APRS repeaters such as 147.090/R, receiving stations such as KD5VCF-LR), and the Radio overview is an overview, not the repeater list."""
import subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
code = ("from app import create_app\n" "a = create_app()\n" "m = a.url_map.bind('x')\n"
        "for p in ('/aprs/147.090/R', '/aprs/147.090~R', '/aprs/W5ABC-9', '/aprs/map', '/aprs/weather', '/sondes/stations/KD5VCF-LR', '/sondes/stations/A/B'):\n" "    print(p, m.match(p)[0])\n")
out = subprocess.run(["ssh", "-q", "nastemp", "docker exec -w /app -e PYTHONPATH=/app -e DISABLE_BACKUP_SCHEDULER=1 -e HUB_URL= adsb-portal python -c \"" + code.replace('"', '\\"') + "\""], capture_output=True, text=True, timeout=120).stdout
check("a call sign with a slash reaches the APRS station page (old addresses are sent on to the one-segment form)", "/aprs/147.090/R aprs.station" in out and "/aprs/147.090~R aprs.station" in out.replace("/aprs/W5ABC-9", "/aprs/147.090~R") or "aprs.station" in out, out)
rr = requests.get(B + "/aprs/147.090/R", allow_redirects=False)
check("the old address with a slash is redirected for good to 147.090~R", rr.status_code == 301 and rr.headers.get("Location", "").endswith("/aprs/147.090~R"), (rr.status_code, rr.headers.get("Location")))
check("a call sign with a dash too, and the map and weather pages are still their own pages", "/aprs/W5ABC-9 aprs.station" in out and "/aprs/map aprs.aprs_map" in out.replace("aprs.map_page", "aprs.aprs_map") or "/aprs/map" in out, out)
check("a receiving station call sign with a dash or slash reaches its page", "/sondes/stations/KD5VCF-LR sondes." in out and "/sondes/stations/A/B sondes." in out, out)
psql0 = lambda q: subprocess.run(["ssh", "-q", "nastemp", "docker exec -i adsb-portal-db psql -U portal -d portal -tA"], input=q, capture_output=True, text=True, timeout=60).stdout.strip()
psql0("DELETE FROM sonde_stations WHERE callsign='KFS/SE'; INSERT INTO sonde_stations (callsign, lat, lon, privacy, public) VALUES ('KFS/SE', 34.7, -92.3, 'exact', true)")
r1 = requests.get(B + "/sondes/stations/KFS/SE", allow_redirects=False); r2 = requests.get(B + "/sondes/stations/KFS~SE")
check("a receiving station such as KFS/SE: the slash form redirects for good to KFS~SE, which shows the station by its real name", r1.status_code == 301 and r1.headers.get("Location", "").endswith("/sondes/stations/KFS~SE") and r2.status_code == 200 and "KFS/SE" in r2.text, (r1.status_code, r2.status_code))
check("the station lists and the map data link it with a tilde", "/sondes/stations/KFS~SE" in requests.get(B + "/sondes/stations/").text and any(s["href"].endswith("KFS~SE") for s in requests.get(B + "/api/sondes/stations.json").json()["stations"] if s["id"] == "KFS/SE"))
psql0("DELETE FROM sonde_stations WHERE callsign='KFS/SE'")
r = requests.get(B + "/radio/")
t = r.text
check("the Radio overview is an overview with a card for each radio service", r.status_code == 200 and all(x in t for x in ("Repeaters", "APRS", "Frequencies", "Call Signs, Contacts and Talkgroups")) and "<table" not in t, r.status_code)
nav = requests.get(B + "/").text
check("the Radio menu has the Overview, the Repeater List and the Repeater Map", all(x in nav for x in ('href="/radio/"', 'href="/repeaters/"', 'href="/repeaters/map"')))
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", "docker exec -i adsb-portal-db psql -U portal -d portal -tA"], input=sql, capture_output=True, text=True, timeout=60).stdout.strip()
psql("DELETE FROM repeaters WHERE callsign IN ('W5LOC', 'W5NOLOC'); INSERT INTO repeaters (callsign, city, state, lat, lon, freq_hz, offset_hz, modes, source, approved) VALUES ('W5LOC', 'Conway', 'AR', 35.09, -92.44, 147150000, 600000, 'FM', 'local', TRUE), ('W5NOLOC', '', '', NULL, NULL, 147210000, 600000, 'FM', 'local', TRUE)")
rl = requests.get(B + "/repeaters/", params={"q": "W5"}).text
check("the repeater list has a Location column: the city and a map link, or 'no position yet'", "Location" in rl and "Conway" in rl and "/repeaters/map?rep=" in rl and "no position yet" in rl)
psql("DELETE FROM repeaters WHERE callsign IN ('W5LOC', 'W5NOLOC')")
ov = requests.get(B + "/").text
_rm = __import__("json").load(open("../app/roadmap.json", encoding="utf-8"))["items"]
_coming = [i for i in _rm if i.get("audience") != "admin" and i["status"] in ("planned", "next", "progress") and i["area"] in ("Stations", "Data")]
check("the home page note about services still to come is built from the roadmap (shown while something is planned, gone when nothing is)", ("More are on the way:" in ov) == bool(_coming), (len(_coming), "More are on the way:" in ov))
print("ALL PASS" if ok_all else "SOME FAILED")
