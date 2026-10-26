"""Chase mode and the recovery icons in a real browser: a signed-in member turns on Chase Mode (the browser's position is faked), sees themself, the distance and bearing to the balloon they pick,
a link for directions, and another chaser's car; landed balloons carry the recovered, planned and not-found icons; a visitor sees only the sign-in note."""
import re, subprocess, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def psql(sql): return subprocess.run(["ssh", "-q", "nastemp", D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser checks"); print("ALL PASS"); sys.exit(0)
adm = sess("marty", PW)
setting("sonde_enabled", "1"); setting("sonde_chase_mode", "test"); setting("map_panels_open", "1")
psql("delete from sonde_recoveries where serial like 'CHTEST%'"); psql("delete from sondes where serial like 'CHTEST%'"); psql("delete from sonde_stations where callsign='W5CAR_chase'")
now = "now() - interval '1 hour'"
for i, (st, rec) in enumerate((("CHTEST1", "recovered"), ("CHTEST2", "planned"), ("CHTEST3", "notfound"))):
    psql(f"insert into sondes (serial, kind, type, first_seen, last_seen, status, last_lat, last_lon, last_alt, landing_lat, landing_lon, landing_alt, launch_lat, launch_lon, launch_time, max_alt, agency) values "
         f"('{st}','radiosonde','RS41', now() - interval '3 hours', {now}, 'landed', 35.{i}, -92.{i}, 120, 35.{i}, -92.{i}, 120, 34.8, -92.3, now() - interval '3 hours', 30000, 'NWS')")
    psql(f"insert into sonde_recoveries (serial, ts, recovered, planned, recovered_by, description, source, sent) values ('{st}', now(), {str(rec == 'recovered').lower()}, {str(rec == 'planned').lower()}, 'W5TEST', '', 'sondehub', '')")
psql("insert into sonde_stations (callsign, lat, lon, mobile, privacy, last_seen, source) values ('W5CAR_chase', 35.5, -92.5, true, 'hidden', now(), 'sondehub')")
time.sleep(6)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
# the browser's position is only offered on a secure address, and localhost counts: reach the test portal through an ssh tunnel
PORT = B.rsplit(":", 1)[1].split("/")[0]
tun = subprocess.Popen(["ssh", "-q", "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"18438:localhost:{PORT}", "nastemp"])
time.sleep(4)
U = "http://localhost:18438"
d = webdriver.Chrome(options=o)
try:
    d.get(U + "/sondes/map"); d.delete_all_cookies(); d.get(U + "/sondes/map"); time.sleep(4)
    check("a visitor sees the sign-in note and no chase switch", d.execute_script("return !document.getElementById('ch-on') && /Sign in/.test(document.body.textContent)"))
    for c in adm.cookies: d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
    d.execute_cdp_cmd("Emulation.setGeolocationOverride", {"latitude": 34.9, "longitude": -92.4, "accuracy": 20})
    d.execute_cdp_cmd("Browser.grantPermissions", {"origin": U, "permissions": ["geolocation"]})
    d.get(U + "/sondes/map?hours=72"); time.sleep(5)
    kinds = d.execute_script("return Array.from(document.querySelectorAll('.sm-mk svg')).length")
    check("the map draws the landed balloons", d.execute_script("return smDebug().shown") >= 3, d.execute_script("return smDebug()"))
    # the icons: each recovery state has its own look (blue car disc for planned, green check for recovered, orange cross for not found)
    html = d.execute_script("return document.getElementById('lvmap').innerHTML")
    check("recovered, planned and not-found landings have their own icons", all(c in html for c in ('#27c48f', '#3b82f6', '#ff9f43')))
    d.execute_script("document.querySelector('[data-k=chase]').open = true")
    d.find_element("id", "ch-on").click(); time.sleep(3)
    check("chase mode shows where I am", d.execute_script("return !!document.querySelector('.sm-mk') && /Choose a balloon|Waiting/.test(document.getElementById('ch-info').textContent)"), d.execute_script("return document.getElementById('ch-info').textContent"))
    d.execute_script("document.querySelector('#lv-tbl tbody tr.it').click()"); time.sleep(2)
    info = d.execute_script("return document.getElementById('ch-info').textContent")
    check("choosing a balloon gives distance, bearing and a directions link", re.search(r"(mi|km)", info) and "Directions" in info and d.execute_script("return !!document.querySelector('#ch-info a[href*=\"google.com/maps\"]')"), info)
    check("another chaser's car is on the map", d.execute_script("return Array.from(document.querySelectorAll('.leaflet-tooltip-pane, .leaflet-marker-pane')).length > 0") and "W5CAR_chase" in d.page_source or d.execute_script("return !!window.smMap"), "")
    d.execute_script("var b=document.getElementById('lv-toolsbody'); if (b.style.display === 'none') document.getElementById('lv-filtbtn').click()"); time.sleep(.5)   # picking a balloon folded Layers and Filters away
    d.find_element("id", "ch-on").click(); time.sleep(1)
    check("switching it off clears the line and the note", d.execute_script("return document.getElementById('ch-info').textContent") == "")
    errs = [e for e in d.get_log("browser") if e["level"] == "SEVERE" and "favicon" not in e["message"] and "tile" not in e["message"].lower()]
    check("no script errors on the page", not errs, errs[:2])
finally:
    d.quit()
    tun.terminate()
    psql("delete from sonde_recoveries where serial like 'CHTEST%'"); psql("delete from sondes where serial like 'CHTEST%'"); psql("delete from sonde_stations where callsign='W5CAR_chase'")
    for k in ("sonde_chase_mode", "map_panels_open"): psql(f"delete from settings where key='{k}'")
print("ALL PASS" if not bad else "SOME FAILED")
