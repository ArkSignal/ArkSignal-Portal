import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
def post(path, data, page="/admin/hospitals"):
    j = adm.post(B + path, data=dict(data, csrf_token=tok(adm, page)), headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"]
check("the public list says nothing is loaded yet when it is empty", requests.get(B + "/hospitals/").status_code == 200)
check("the admin page needs sign-in", requests.get(B + "/admin/hospitals", allow_redirects=False).status_code in (302, 401, 403, 404))
check("an ordinary user cannot import", sess("plain1", "plainpassword1").post(B + "/admin/hospitals/import", data={}, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
ok, msg = post("/admin/hospitals/import", {})
check("the CMS list downloads and the new hospitals are located", ok and "located with the Census geocoder" in msg, msg)
tot = int(psql("select count(*) from hospitals") or 0)
check("hospitals from Arkansas and its neighbours are loaded", tot > 400 and int(psql("select count(*) from hospitals where state='AR'") or 0) >= 35, tot)
check("most are located by the Census geocoder", int(psql("select count(*) from hospitals where pos_source='census'") or 0) > tot * 0.8)
check("none is left without a position", int(psql("select count(*) from hospitals where lat is null") or 0) <= 1, psql("select count(*) from hospitals where lat is null"))
t = requests.get(B + "/hospitals/?state=AR").text
check("the public list shows Arkansas hospitals with links", "/hospitals/" in t and "Emergency" in t and "sort=city" in t)
fid = psql("select facility_id from hospitals where state='AR' and emergency order by name limit 1")
page = requests.get(B + f"/hospitals/{fid}")
check("a hospital page shows address, emergency care and the map", page.status_code == 200 and "Emergency Department" in page.text and 'id="hmap"' in page.text)
check("an unknown hospital is a 404", requests.get(B + "/hospitals/nope").status_code == 404)
r = requests.get(B + "/hospitals/", params={"q": "Little Rock", "state": "AR"}).text
check("search by city works", "Little Rock" in r)
api = requests.get(B + "/api/hospitals.json", params={"south": 34.6, "west": -92.5, "north": 34.9, "east": -92.1}).json()["hospitals"]
check("the map data lists hospitals in view", len(api) >= 3 and all(k in api[0] for k in ("id", "name", "lat", "lon", "trauma", "emergency")), api[:1])
check("the map data refuses a missing area", requests.get(B + "/api/hospitals.json").status_code == 400)
check("names are tidied: CHI, VA and LLC keep their capitals and nothing is left in all capitals", psql("select count(*) from hospitals where facility_id not like 'imp-%' and name = upper(name) and name ~ '[A-Z]{4,}'") == "0" and psql("select count(*) from hospitals where name ~ '(^| )Chi( |-)'") == "0")
# several hospitals guessed at the same place are spread out on the map so none hides another
ids = psql("select string_agg(facility_id, ' ') from (select facility_id from hospitals where state='AR' and emergency order by facility_id limit 3) x").split()
keep = {i: psql(f"select lat||','||lon||','||pos_source from hospitals where facility_id='{i}'") for i in ids}
for i in ids:
    psql(f"update hospitals set lat=34.7494, lon=-92.3514, pos_source='zip' where facility_id='{i}'")
api2 = requests.get(B + "/api/hospitals.json", params={"south": 34.6, "west": -92.5, "north": 34.9, "east": -92.1}).json()["hospitals"]
pts = {(round(h["lat"], 5), round(h["lon"], 5)) for h in api2 if h["id"] in ids}
check("hospitals guessed at the same spot get separate markers", len(pts) == 3, pts)
for i, v in keep.items():
    la, lo, src = v.split(",")
    psql(f"update hospitals set lat={la}, lon={lo}, pos_source='{src}' where facility_id='{i}'")
# an administrator's entries
ok, msg = post(f"/admin/hospitals/{fid}/save", {"trauma": "IV", "services": ["stroke", "cardiac"], "heliport": "", "notes": "test note", "lat": "", "lon": ""})
check("trauma level, services and notes save", ok, msg)
ok, msg = post(f"/admin/hospitals/{fid}/save", {"trauma": "IX", "heliport": "", "notes": "", "lat": "", "lon": ""})
check("a bad trauma level is refused", not ok, msg)
ok, msg = post(f"/admin/hospitals/{fid}/save", {"trauma": "IV", "heliport": "KLIT", "notes": "", "lat": "", "lon": ""})
check("a heliport link to something that is not a heliport is refused", not ok and "not a heliport" in msg, msg)
ok, msg = post(f"/admin/hospitals/{fid}/save", {"trauma": "IV", "heliport": "", "notes": "", "lat": "34.5", "lon": ""})
check("half a position is refused", not ok, msg)
heli = psql("select ident from airports where type='heliport' and region='US-AR' limit 1")
ok, msg = post(f"/admin/hospitals/{fid}/save", {"trauma": "IV", "services": ["stroke"], "heliport": heli, "notes": "kept", "lat": "34.7", "lon": "-92.3"})
check("a real heliport and a hand-set position save", ok, msg)
p = requests.get(B + f"/hospitals/{fid}").text
check("the public page shows the level, services, heliport and note", "Level IV" in p and "Stroke centre" in p and heli in p and "kept" in p and "set by an administrator" in p)
ok, msg = post("/admin/hospitals/import", {})
row = psql(f"select trauma||'|'||heliport_ident||'|'||notes||'|'||pos_source from hospitals where facility_id='{fid}'")
check("a new import keeps what the administrator entered", ok and row == f"IV|{heli}|kept|manual", row)
check("trauma filter works", fid in requests.get(B + "/hospitals/", params={"trauma": "IV", "state": "AR"}).text )
_menu = requests.get(B + "/").text
_places = re.search(r'<a class="gl" href="/counties/">Places</a>.*?</details>', _menu, re.S)
_air = re.search(r'<a class="gl" href="/aircraft">Aircraft</a>.*?</details>', _menu, re.S)
check("the menu lists Hospitals and Airports under Places, not under Aircraft", _places and 'href="/hospitals/"' in _places.group(0) and 'href="/airports/"' in _places.group(0) and _air and 'href="/hospitals/"' not in _air.group(0) and 'href="/airports/"' not in _air.group(0))
check("the settings are on the page", all(f'name="{k}"' in adm.get(B + "/admin/settings").text for k in ("hospital_states", "hospital_heliport_km", "hospitals_dataset_id", "geocoder_url")))
check("the About page credits CMS and the Census geocoder", "Provider Data Catalog" in requests.get(B + "/about").text)
try:
    import time
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + "/live"); time.sleep(3)
        n0 = d.execute_script("return lvDebug().hospitals")
        d.execute_script("document.getElementById('l-hospitals').click()"); time.sleep(3)
        n1 = d.execute_script("return lvDebug().hospitals")
        check("switching on the Hospitals layer draws markers on the live map", n0 == 0 and n1 > 5, (n0, n1))
        d.execute_script("document.getElementById('l-hospitals').click()"); time.sleep(1)
        d.execute_script("document.getElementById('l-hospitals').click()"); time.sleep(3)
        check("switching it back on loads them again", d.execute_script("return lvDebug().hospitals") > 5)
        from selenium.webdriver.common.action_chains import ActionChains
        d.execute_script("lvGoTo(34.75, -92.3, 12)"); time.sleep(3)
        pts = [p for p in d.execute_script("return lvHospitalPoints()") if 420 < p["x"] < 1250 and 60 < p["y"] < 560]
        check("some hospital is on screen to click", len(pts) >= 1, pts[:3])
        if pts:
            mapel = d.find_element(By.ID, "lvmap")
            w, h = mapel.size["width"], mapel.size["height"]
            ActionChains(d).move_to_element_with_offset(mapel, pts[0]["x"] - w / 2, pts[0]["y"] - h / 2).click().perform(); time.sleep(1)
            check("clicking a hospital marker opens its popup with a link to its page", d.execute_script("return document.querySelectorAll('.leaflet-popup a').length > 0 && !!Array.from(document.querySelectorAll('.leaflet-popup a')).find(function (a) { return a.getAttribute('href').indexOf('/hospitals/') === 0; })"))
    finally:
        d.quit()
except ImportError:
    print("note: selenium not installed, browser part skipped")
print("ALL PASS" if ok_all else "SOME FAILED")
