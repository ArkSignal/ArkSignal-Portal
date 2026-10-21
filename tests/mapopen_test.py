"""Every map page opens on Arkansas whole (Settings > Home Page Map Area), like the home page, unless the link names a place: the balloon map, the weather map and the county map are given the
area and fit it; none of them fits to whatever balloons, stations or counties happen to be loaded, and in a real browser the view shows all of Arkansas and not much else."""
import re, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
for path in ("/sondes/map", "/weather/map", "/counties/"):
    h = requests.get(B + path).text
    check(f"{path} is given the Arkansas area", re.search(r"\[33(\.0)?, -94\.62, 36\.5, -89\.64\]", h) is not None and "fitBounds([[" in h)
    check(f"{path} does not fit to the data it loaded", "fitBounds(pts" not in h and "wx.points().map" not in h)
import subprocess
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def psql(sql): return subprocess.run(["ssh", "-q", "nastemp", D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
KEYS = ["home_map_area", "live_map_area", "coverage_map_area", "balloon_map_area", "weather_map_area", "county_map_area", "feeders_map_area"]
adm = sess("marty", PW)
sp = adm.get(B + "/admin/settings").text
vals = [re.search(r'name="%s"[^>]*value="([^"]*)"' % k, sp) for k in KEYS]
check("every map has its own setting on the Settings page (Map Areas)", all(vals), [k for k, v in zip(KEYS, vals) if not v])
norm = lambda t: [float(x) for x in t.split(",")]
check("and they all default to the same area, the whole of Arkansas", all(v for v in vals) and all(norm(v.group(1)) == [33.0, -94.62, 36.5, -89.64] for v in vals), [v.group(1) for v in vals if v])
pages = {"live_map_area": "/live", "coverage_map_area": "/coverage", "balloon_map_area": "/sondes/map", "weather_map_area": "/weather/map", "county_map_area": "/counties/", "feeders_map_area": "/feeders/"}
for k, path in pages.items():
    setting(k, "30.5,-100.5,40.5,-80.5"); 
time.sleep(6)
for k, path in pages.items():
    check(f"{path} follows its own setting ({k})", re.search(r"\[30\.5, -100\.5, 40\.5, -80\.5\]", requests.get(B + path).text) is not None)
for k in pages: psql(f"delete from settings where key='{k}'")
time.sleep(6)
# weather stations that are not reporting stay off the aircraft map (a setting; the visitor can switch it)
check("the aircraft map hides offline weather stations by default", '"wxHideOffline": true' in requests.get(B + "/live").text)
setting("wx_live_hide_offline", "0"); time.sleep(6)
check("and shows them when the setting says so", '"wxHideOffline": false' in requests.get(B + "/live").text)
psql("delete from settings where key='wx_live_hide_offline'"); time.sleep(6)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser checks"); print("ALL PASS" if not bad else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    for path, var in (("/sondes/map", "smMap"), ("/counties/", "cmMap")):
        d.get(B + path); time.sleep(5)
        b = d.execute_script(f"var b=window.{var}.getBounds(); return {{s: b.getSouth(), n: b.getNorth(), w: b.getWest(), e: b.getEast()}}")
        check(f"{path} shows all of Arkansas and not much more", b["s"] < 33.0 and b["n"] > 36.5 and b["w"] < -94.6 and b["e"] > -89.7 and (b["n"] - b["s"]) < 9, b)
    d.get(B + "/sondes/map?lat=40&lon=-100&zoom=5"); time.sleep(4)
    c = d.execute_script("var c=window.smMap.getCenter(); return [c.lat, c.lng]")
    check("a link that names a place is respected", abs(c[0] - 40) < 1 and abs(c[1] + 100) < 1, c)
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED")
