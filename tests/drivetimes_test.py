"""Drive times and the County Map rings through the web: the data files (a real routing answer when the routing server is reachable through the test box's tunnel address, otherwise those parts skip),
the panel, the Drive Times section and the ring controls on the County Map, the search offers, the settings, and the in-container unit checks (drivetimes_unit.py)."""
import math, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout

out = ssh("docker cp ~/adsb-portal/tests/drivetimes_unit.py adsb-portal:/tmp/drivetimes_unit.py; docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/drivetimes_unit.py")
check("drivetimes_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-800:])

adm = sess("marty", PW)
cm = requests.get(B + "/counties/").text
check("the County Map has the Drive Times section and panel", 'id="dr-on"' in cm and 'id="lv-drive"' in cm and "/static/drivetimes.js" in cm)
check("the County Map can show counties shaded, outlined or both", 'id="cm-look"' in cm and "Shaded and outlined" in cm)
check("the County Map has the hospital rings choice with trauma centers", 'id="cm-rings"' in cm and "Level I and II trauma centers" in cm)
for path in ("/sondes/map", "/weather/map", "/aprs/map", "/repeaters/map"):
    check(f"{path} has the Drive Times section too (one shared map shell)", 'id="dr-on"' in requests.get(B + path).text, path)
js = requests.get(B + "/static/drivetimes.js").text
check("the script offers ArkDrive.at, search and ringLayer and says it is an estimate", all(x in js for x in ("ArkDrive", "ringLayer", "search: search", "Our own estimate")))
check("the search box offers Drive times from what was typed, and from a picked place", "Drive times from" in requests.get(B + "/static/mapsearch.js").text)
check("a bad position is a 400", requests.get(B + "/api/drivetimes.json?lat=x&lon=1").status_code == 400)
check("an empty geocode query gives nothing", requests.get(B + "/api/geocode.json?q=a").json()["results"] == [])
g = requests.get(B + "/api/geocode.json?q=Conway").json()["results"]
check("a city is found from the gazetteer", any(x["label"].startswith("Conway, AR") for x in g), g[:2])
sp = adm.get(B + "/admin/settings").text
check("the drive time settings are in Admin > Settings", all(f'name="{k}"' in sp for k in ("drivetimes_hospitals", "drivetimes_tie_minutes", "drivetimes_per_minute")))
check("the radiosonde station setting offers the twelve-step list (without Exact)", 'name="sonde_station_privacy"' in sp and "Block (rounded to about" in sp)

ssh("docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('osrm_url', 'http://osrm-tunnel.example.net:5000') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value\"")
try:
    reach = subprocess.run(["ssh", "-q", "nastemp", "curl -s -m 6 -o /dev/null -w %{http_code} http://osrm-tunnel.example.net:5000/"], capture_output=True, text=True, timeout=30).stdout.strip() in ("200", "400", "404")
except Exception:
    reach = False
if reach:
    j = requests.get(B + "/api/drivetimes.json?lat=34.75&lon=-92.29", timeout=120).json()
    hs = j.get("hospitals", [])
    check("Little Rock: hospitals timed by road, a Level I among the first", len(hs) >= 5 and hs[0]["minutes"] is not None and any(h["trauma"] == "I" for h in hs[:3]), hs[:2])
    j2 = requests.get(B + "/api/drivetimes.json?lat=34.75&lon=-92.29&rings=1", timeout=120).json()
    check("the rings come with it: 15, 30, 60 and 90 minutes", sorted(map(int, j2["rings"])) == [15, 30, 60, 90] and all(len(p) == 48 for p in j2["rings"].values()), j2.get("note"))
    def far(p):
        return max(math.hypot((a - 34.75) * 111, (b + 92.29) * 91) for a, b in p)
    check("each ring reaches farther than the one before", far(j2["rings"]["15"]) < far(j2["rings"]["30"]) < far(j2["rings"]["60"]) < far(j2["rings"]["90"]))
    a = requests.get(B + "/api/geocode.json?q=" + requests.utils.quote("1500 W Markham St, Little Rock AR"), timeout=60).json()["results"]
    check("an address is found (Census locator or our Photon)", any(x["kind"] == "address" for x in a), a[:1])
else:
    print("note: the routing server is not reachable from the test box, real routing checks skipped")
ssh("docker exec adsb-portal-db psql -U portal -d portal -c \"DELETE FROM settings WHERE key = 'osrm_url'\"")
print("ALL PASS" if ok_all else "SOME FAILED")
