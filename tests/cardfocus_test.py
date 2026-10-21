"""Radiosonde Map: "Show on Radiosonde Map" lands on a flight that is far outside Arkansas (not on the home area), and picking a balloon or a launch site folds Layers and Filters away so the details show.
Needs selenium and Chrome on the PC, and the test box."""
import os, subprocess, sys, time
try:
    from selenium import webdriver
except ImportError:
    print("SKIP cardfocus_test: selenium not installed"); sys.exit(0)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
B = os.environ.get("MOBILE_BASE", f"http://{HOST}:18430")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", "docker exec -i adsb-portal-db psql -U portal -d portal -tA"], input=sql, capture_output=True, text=True, timeout=60).stdout
psql("DELETE FROM sondes WHERE serial LIKE 'ZZMF%'; INSERT INTO settings (key, value) VALUES ('map_panels_open', '1') ON CONFLICT (key) DO UPDATE SET value = '1';")
psql("INSERT INTO sondes (serial, kind, type, first_seen, last_seen, launch_time, status, frames, last_lat, last_lon, last_alt, agency) VALUES "
     "('ZZMFNM', 'radiosonde', 'RS41', now() - interval '3 hours', now() - interval '10 minutes', now() - interval '3 hours', 'aloft', 50, 33.04, -105.44, 12000, 'NWS'), "
     "('ZZMFAR', 'radiosonde', 'RS41', now() - interval '2 hours', now() - interval '5 minutes', now() - interval '2 hours', 'aloft', 50, 34.9, -92.3, 9000, 'NWS');")
time.sleep(35)   # settings are kept for a short while by each web worker
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    d.get(B + "/sondes/map?only=ZZMFNM"); time.sleep(5)
    c = d.execute_script("var c=smMap.getCenter(); return [c.lat, c.lng]")
    check("Show on Radiosonde Map for a flight in New Mexico opens on that flight, not on Arkansas", abs(c[0] - 33.04) < 1 and abs(c[1] + 105.44) < 1, c)
    d.get(B + "/sondes/map"); time.sleep(4)
    check("Layers and Filters are open before anything is picked", d.execute_script("return document.getElementById('lv-toolsbody').style.display") != "none")
    d.execute_script("document.querySelector('#lv-tbl tr.it').click()"); time.sleep(1.5)
    check("picking a balloon from the list folds Layers and Filters away", d.execute_script("return document.getElementById('lv-toolsbody').style.display") == "none")
    d.execute_script("document.getElementById('lv-filtbtn').click()"); time.sleep(.4)
    d.execute_script("document.querySelectorAll('#lv-tbl tr.it')[1].click()"); time.sleep(1.5)
    check("picking another balloon folds them again", d.execute_script("return document.getElementById('lv-toolsbody').style.display") == "none")
    d.execute_script("document.getElementById('c-close').click(); document.getElementById('lv-filtbtn').click()"); time.sleep(.4)
    check("with no details open, the Filters button opens Layers and Filters and they stay", d.execute_script("return document.getElementById('lv-toolsbody').style.display") != "none")
finally:
    d.quit()
    psql("DELETE FROM sondes WHERE serial LIKE 'ZZMF%'")
print("ALL PASS" if ok_all else "SOME FAILED")
