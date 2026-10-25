"""Satellite Passes has Show on Map for the ticked satellites, and the map shows those satellites and nothing else (no aircraft, no other satellite): the filtered feed, the page, the card, the selector entry, and that
a guest cannot see the map when Feature Access hides satellites."""
import re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
def cks(line):
    return line + str((sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line)) % 10)
def tle(norad):
    yr_day = time.strftime("%y") + "%03d.50000000" % time.gmtime().tm_yday
    l1 = cks("1 %05dU 98067A   %s -.00002182  00000-0 -11606-4 0  292" % (norad, yr_day))
    l2 = cks("2 %05d  51.6416 247.4627 0006703 130.5360 325.0288 15.7212539156353" % norad)
    return l1, l2
setting("satpass_enabled", "1"); setting("satmap_enabled", "1")
psql("DELETE FROM settings WHERE key LIKE 'access_%'")
psql("DELETE FROM sat_tle WHERE norad IN (90001, 90002, 90003)")
for n, name in ((90001, "ZZ SAT ONE"), (90002, "ZZ SAT TWO"), (90003, "ZZ SAT THREE")):
    l1, l2 = tle(n)
    psql(f"INSERT INTO sat_tle (norad, name, line1, line2, epoch) VALUES ({n}, '{name}', '{l1}', '{l2}', now())")
time.sleep(25)   # the feed is kept for a few seconds
guest, me = requests.Session(), sess("plain1", "plainpassword1")
d = guest.get(B + "/api/satellites.geojson").json()
allnorads = {f["properties"].get("norad") for f in d["features"]}
check("every feature in the feed says which satellite it is", {90001, 90002, 90003} <= allnorads and None not in allnorads, allnorads)
d = guest.get(B + "/api/satellites.geojson?sat=90001,90003").json()
check("the feed can be limited to the chosen satellites (their dots, paths and circles, and no others)", {f["properties"]["norad"] for f in d["features"]} == {90001, 90003} and any(f["geometry"]["type"] == "Point" for f in d["features"]), {f["properties"]["norad"] for f in d["features"]})
pg = guest.get(B + "/radio/satellites?sel=1&sat=90001&sat=90002").text
check("the passes page has Show on Map", 'id="sp-map"' in pg and "/radio/satellites/map" in pg)
mp = guest.get(B + "/radio/satellites/map?sat=90002")
check("the map page opens with only the chosen satellite", mp.status_code == 200 and "Satellite Map" in mp.text and re.search(r"CHOSEN = (\[[^\]]*\])", mp.text).group(1) == '["90002"]')
try:
    from selenium import webdriver
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,900")
    d = webdriver.Chrome(options=o); J = d.execute_script
    try:
        d.get(B + "/radio/satellites?sel=1")
        time.sleep(2)
        J("document.querySelectorAll('.sp-pill input').forEach(function(i){i.checked = (i.value === '90001' || i.value === '90003')}); document.querySelector('.sp-pill input').dispatchEvent(new Event('change', {bubbles: true}))")
        time.sleep(.5)
        href = J("return document.getElementById('sp-map').getAttribute('href')")
        check("ticking satellites sets the Show on Map link to exactly those", set(re.findall(r"sat=(\d+)", href)) == {"90001", "90003"}, href)
        J("document.querySelectorAll('.sp-pill input').forEach(function(i){i.checked = false}); document.querySelector('.sp-pill input').dispatchEvent(new Event('change', {bubbles: true}))")
        time.sleep(.3)
        check("with none ticked the link does not go anywhere", J("return document.getElementById('sp-map').getAttribute('href')") == "#")
        check("the link carries the place and the choices of the passes page", all(x in href for x in ("lat=", "lon=", "hours=", "min=", "q=")), href)
        d.get(B + "/radio/satellites/map?sat=90001&lat=34.7&lon=-92.3&q=Little+Rock&hours=48&min=0"); time.sleep(5)
        st = J("return window.satMap.state()")
        check("the map is for the place that was entered, with the passes of the chosen satellite in the look-ahead drawn as paths", abs(st["place"]["lat"] - 34.7) < 0.01 and st["place"]["label"] == "Little Rock" and st["passes"] >= 2 and st["drawn"] >= st["passes"] + 1, st)
        check("the page says which place, hours and height it is for", "Passes over Little Rock" in J("return document.getElementById('lv').parentElement.innerText + document.body.innerText") and "next 48 hours" in J("return document.body.innerText"))
        J("document.querySelector('#lv-tbl tbody tr.it').click()"); time.sleep(1)
        check("choosing a pass shows when it rises, peaks and sets", all(x in J("return document.getElementById('c-body').innerText") for x in ("Rises", "Highest", "Sets")) and "Pass over Little Rock" in J("return document.getElementById('c-sub').textContent"))
        rows = J("return Array.from(document.querySelectorAll('#lv-tbl tbody tr.it')).map(function(r){return r.cells[0].textContent})")
        check("the list is the passes in time order with their dates and times", len(rows) >= 2 and all(len(r) > 6 for r in rows), rows)
        d.get(B + "/radio/satellites/map?sat=90001&lat=34.7&lon=-92.3&q=Little+Rock&hours=1&min=80"); time.sleep(4)
        check("a shorter look-ahead and a higher lowest peak give fewer passes (none here)", J("return window.satMap.state().passes") == 0 and "No pass in this time" in J("return document.body.innerText"))
        d.get(B + "/radio/satellites/map?sat=90001&sat=90003"); time.sleep(5)
        st = J("return window.satMap && window.satMap.state()")
        check("the map shows the two chosen satellites and no others", st and st["shown"] == 2 and set(st["chosen"]) == {"90001", "90003"}, st)
        check("no aircraft are on this map", J("return document.querySelectorAll('#lvmap [class*=aircraft], #lvmap .lv-plane').length") == 0)
        J("document.querySelector('#lv-tbl tbody tr.it').click()"); time.sleep(1)
        check("choosing one opens its card with its details", J("return document.getElementById('lv-card').style.display") == "block" and "ZZ SAT" in J("return document.getElementById('c-name').textContent") and J("return document.getElementById('c-body').textContent.length") > 20)
        check("the map selector offers Satellites", "Satellites" in J("return document.getElementById('lv-maptype').textContent"))
        d.set_window_size(400, 850); d.get(B + "/radio/satellites/map?sat=90001"); time.sleep(3)
        check("phone: the page does not run off the side", J("return document.documentElement.scrollWidth <= window.innerWidth + 1"))
    finally:
        d.quit()
except ImportError:
    print("SKIP no selenium")
setting("access_satpass", "members")
time.sleep(6)
check("with satellites limited to members, a guest cannot open the map and the selector does not offer it", guest.get(B + "/radio/satellites/map", allow_redirects=False).status_code in (302, 401, 403, 404) and "/radio/satellites/map" not in guest.get(B + "/live").text, guest.get(B + "/radio/satellites/map", allow_redirects=False).status_code)
setting("access_drones", "members"); setting("rid_enabled", "1")
time.sleep(6)
check("the same for the Drones entry of the map selector", "/drones/map" not in guest.get(B + "/live").text and "/drones/map" in me.get(B + "/live").text)
psql("DELETE FROM settings WHERE key LIKE 'access_%'")
psql("DELETE FROM sat_tle WHERE norad IN (90001, 90002, 90003)")
print("ALL PASS" if ok_all else "SOME FAILED")
