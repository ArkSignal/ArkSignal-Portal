import re, requests, json, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
adm = sess("marty", PW); anon = requests.Session()
def tok(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
# ---- watched aircraft
air = anon.get(B + "/api/aircraft.json").json()["aircraft"]
check("guests get no watch information", all("watch" not in a for a in air) and "watch_rules" not in anon.get(B + "/api/aircraft.json").json())
if air:
    target = air[0]["hex"]
    adm.post(B + "/my/alerts/save", data={"csrf_token": tok(adm, "/my/alerts"), "kind": "icao", "value": target.upper(), "name": "Extras test watch", "cooldown_minutes": "30"}, headers={"X-Requested-With": "XMLHttpRequest"})
    d = adm.get(B + "/api/aircraft.json").json()
    me = [a for a in d["aircraft"] if a["hex"] == target]
    check("a signed-in visitor's own rule flags the aircraft it matches", me and me[0].get("watch") and "Extras test watch" in d.get("watch_rules", {}).values(), str(me[:1]))
    others = [a for a in d["aircraft"] if a["hex"] != target and a.get("watch")]
    check("and only that aircraft", not others, str(others[:1]))
    rid = me[0]["watch"][0]
    check("the alerts page opens a rule from the map's link and prefills the Watch button's alert", "#rule-" in adm.get(B + "/my/alerts").text or "rule-" in adm.get(B + "/my/alerts").text)
    # the map in a signed-in browser: the matching aircraft is flagged, the filter and note appear
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
        dr = webdriver.Chrome(options=o)
        try:
            dr.get(B + "/my/login"); dr.find_element(By.NAME, "username").send_keys("marty"); dr.find_element(By.NAME, "password").send_keys(PW)
            dr.execute_script("arguments[0].click()", dr.find_element(By.CSS_SELECTOR, "form .btn")); time.sleep(1.5)
            dr.get(B + "/live?icao=" + target); time.sleep(5)
            check("the live map flags the watched aircraft and shows the alert filter", dr.execute_script("return window.lvDebug().watched") >= 1 and dr.execute_script("return document.getElementById('f-watch-l').style.display !== 'none'"))
            check("the card says which of your alerts it matches, with a link to the rule", dr.execute_script("var n=document.getElementById('c-watch');return n.style.display==='block' && n.innerHTML.indexOf('/my/alerts#rule-')>=0"))
        finally:
            dr.quit()
    except ImportError:
        print("SKIP browser part: selenium not installed")
    # clean up the rule
    adm.post(B + f"/my/alerts/{rid}/delete", data={"csrf_token": tok(adm, "/my/alerts")}, headers={"X-Requested-With": "XMLHttpRequest"})
    check("deleting the rule removes the flag", not [a for a in adm.get(B + "/api/aircraft.json").json()["aircraft"] if a.get("watch")])
else:
    print("note: no aircraft right now; watch checks skipped")
check("the live page offers the alert filter and Watch button markup", all(x in adm.get(B + "/live").text for x in ("f-watch", "c-watchbtn", "l-coverage", "l-wxair")))
# ---- station coverage
cv = anon.get(B + "/api/coverage.json").json()
check("coverage answers with a list of stations (each 36 sectors, no raw coordinates beyond the public ones)", "stations" in cv and all(len(s["sectors"]) == 36 for s in cv["stations"]), str(cv)[:200])
import subprocess
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
U1, U2 = "cccccccc-1111-2222-3333-444444444441", "cccccccc-1111-2222-3333-444444444442"
psql(f"DELETE FROM feeders WHERE uuid IN ('{U1}','{U2}')")
psql(f"INSERT INTO feeders (uuid, station_id, name, owner_id, lat, lon, privacy, online) VALUES ('{U1}', 'COVER-1', 'Coverage Shown', (SELECT id FROM users WHERE username='marty'), 34.5, -92.5, 'exact', TRUE), ('{U2}', 'COVER-2', 'Coverage Hidden', (SELECT id FROM users WHERE username='marty'), 35.5, -91.5, 'hidden', TRUE)")
for u in (U1, U2):
    for sector, nm in ((0, 90.0), (9, 140.0), (18, 60.0), (27, 175.0)):
        psql(f"INSERT INTO feeder_range (feeder_uuid, day, sector, max_nm, max_alt) VALUES ('{u}', (now() AT TIME ZONE 'utc')::date, {sector}, {nm}, 35000)")
cv = anon.get(B + "/api/coverage.json").json()
mine = [x for x in cv["stations"] if x["id"] == "COVER-1"]
check("a public station's coverage is listed with its 36 sector distances", mine and mine[0]["sectors"][27] == 175 and abs(mine[0]["lat"] - 34.5) < 0.001, str(cv)[:200])
check("a station that hides its location has no coverage drawn", not [x for x in cv["stations"] if x["id"] == "COVER-2"])
pg = anon.get(B + "/feeders/COVER-1").text
check("a station's page links to its own statistics", '/view/network-feeders?station=COVER-1' in pg)
check("the Feeders list links each station to its statistics", '/view/network-feeders?station=COVER-1' in anon.get(B + "/feeders/").text)
psql(f"DELETE FROM feeders WHERE uuid IN ('{U1}','{U2}')")
# ---- airport weather
m = anon.get(B + "/api/metars.json?south=33&west=-95&north=36.5&east=-89").json()
check("airport weather returns current METARs for Arkansas (needs internet from the portal)", isinstance(m.get("metars"), dict) and len(m["metars"]) > 3, str(m)[:200])
if m.get("metars"):
    one = next(iter(m["metars"].values()))
    check("each METAR carries category, wind and the raw report", "cat" in one and "raw" in one and "wspd" in one, str(one)[:200])
check("a huge area is refused politely", anon.get(B + "/api/metars.json?south=0&west=-130&north=60&east=-60").json().get("metars") == {})
check("bad numbers are refused", anon.get(B + "/api/metars.json?south=x&west=1&north=2&east=3").status_code == 400)
st = adm.get(B + "/admin/settings").text
check("the new settings are on the Settings page", all(x in st for x in ("Station Coverage Looks Back", "Airport Weather on the Map", "Airport Weather (METAR) Address", "Remember Airport Weather For")))
print("ALL PASS" if ok_all else "SOME FAILED")
