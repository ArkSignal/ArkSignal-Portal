"""Show on Map from an item's page shows only that item: the links carry `only`, each map understands it (balloon flight, launch site, receiving station; aircraft; APRS station; county), a chip clears it,
and one flight loads whatever its age (the live windows stop at 48 hours)."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()

sm = requests.get(B + "/sondes/map").text
check("the Balloon Map understands only=<flight>, only=site:<id> and only=station:<call>, with a chip to clear it", all(x in sm for x in ("function onlyKind()", 'state.only !== "site:" + st.id', 'state.only !== "station:" + st.id', '["only", "Only: "')))
check("the Aircraft Map hides the other aircraft for ?only=1 until it is unticked", "var forceOnly" in requests.get(B + "/live").text and "forceOnly = false" in requests.get(B + "/live").text)
check("the APRS Map shows one station for ?call=X&only=1", "var onlyCall" in requests.get(B + "/aprs/map").text)
check("the County Map shows one county for ?county=X&only=1", "var onlyCounty" in requests.get(B + "/counties/").text)

# one flight loads whatever its age
psql("DELETE FROM sondes WHERE serial = 'ZZOLD-1'")
psql("INSERT INTO sondes (serial, kind, first_seen, last_seen, last_lat, last_lon, status) VALUES ('ZZOLD-1', 'amateur', now() - interval '30 days', now() - interval '30 days', 35.0, -92.0, 'landed')")
try:
    live = requests.get(B + "/api/sondes/map.json", params={"hours": 48, "static": 0}).json()["flights"]
    one = requests.get(B + "/api/sondes/map.json", params={"serial": "ZZOLD-1", "static": 0}).json()["flights"]
    check("the 48 hour window does not hold a 30 day old flight, but asking for that one flight does", not any(f["id"] == "ZZOLD-1" for f in live) and [f["id"] for f in one] == ["ZZOLD-1"], ([f["id"] for f in one]))
    check("asking for one flight never brings the others", len(one) == 1)
    check("an unknown serial gives nothing", requests.get(B + "/api/sondes/map.json", params={"serial": "NOSUCH-9", "static": 0}).json()["flights"] == [])
    pg = requests.get(B + "/sondes/ZZOLD-1")
    check("the flight page's Show on Balloon Map link shows only that flight", pg.status_code == 200 and "/sondes/map?only=ZZOLD-1" in pg.text, pg.status_code)
finally:
    psql("DELETE FROM sondes WHERE serial = 'ZZOLD-1'")

sites = requests.get(B + "/api/sondes/map.json", params={"static": 1}).json().get("sites", [])
for st in sites:   # (each web worker keeps its own list of sites for a while: use one this worker knows)
    pg = requests.get(B + "/sondes/sites/" + st["id"])
    if pg.status_code == 200:
        check("a launch site page links to the map with only that site", "only=site:" + st["id"] in pg.text)
        break
ap = requests.get(B + "/airports/KLIT")
check("an airport page's Show on Map shows only that airport", ap.status_code != 200 or "only=airports:KLIT" in ap.text, ap.status_code)
hs = requests.get(B + "/api/hospitals.json?south=34.6&west=-92.5&north=34.9&east=-92.0").json().get("hospitals", [])
if hs:
    hp = requests.get(B + "/hospitals/" + hs[0]["id"])
    check("a hospital page's Show on Map shows only that hospital", "only=hospitals:" in hp.text, hp.status_code)
lv = requests.get(B + "/live").text
check("the Aircraft Map understands only=airports|hospitals|weather:<id> (no aircraft, no other places)", all(x in lv for x in ("var onlyPlace", "if (onlyPlace) return false;", "onlyPlace.layer === \"airports\"", "onlyPlace.layer === \"hospitals\"", "wxs.set(\"q\", onlyPlace.id, true)")))
print("ALL PASS" if ok_all else "SOME FAILED")
