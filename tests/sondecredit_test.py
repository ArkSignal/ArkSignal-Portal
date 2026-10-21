"""SondeHub is credited wherever its data is shown: a link in the page of every radiosonde page, and in the map's credits (bottom right) on the Radiosonde Map always and on the aircraft map, the County Map and
the Coverage Map while their balloon or receiving station layers are on. Needs selenium and Chrome on the PC for the map part."""
import os, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
adm = sess("marty", PW)
for path in ("/sondes/", "/sondes/map", "/sondes/flights", "/sondes/stations/", "/sondes/statistics", "/my/sondes", "/feed/sondes"):
    r = adm.get(B + path)
    check(f"{path} credits SondeHub with a link", r.status_code == 200 and 'href="https://sondehub.org"' in r.text, r.status_code)
for f in ("sonde", "sonde_site", "sonde_station", "sonde_stations", "sondes_flights", "sondes_stats", "my_sondes", "my_sonde_station"):   # pages that need flights or stations to exist: checked in the template
    t = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "templates", f + ".html"), encoding="utf-8").read()
    check(f"{f}.html credits SondeHub with a link", "_sondecredit" in t or 'href="https://sondehub.org"' in t)
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP the map credits: selenium not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
def credits():
    return d.execute_script("var e=document.querySelector('.leaflet-control-attribution');return e?e.textContent:''")
try:
    d.get(B + "/sondes/map"); time.sleep(4)
    check("the Radiosonde Map's credits name SondeHub", "SondeHub" in credits(), credits())
    d.get(B + "/live"); time.sleep(4)
    check("the aircraft map does not credit SondeHub while the balloons are off", "SondeHub" not in credits())
    d.execute_script("var c=document.getElementById('l-sondes'); if(!c.checked){c.checked=true;c.dispatchEvent(new Event('change'))}"); time.sleep(2)
    check("and does once the weather balloons layer is on", "SondeHub" in credits(), credits())
    d.execute_script("var c=document.getElementById('l-sondes'); c.checked=false;c.dispatchEvent(new Event('change'))"); time.sleep(1)
    check("and stops when it is switched off again", "SondeHub" not in credits())
    d.get(B + "/coverage"); time.sleep(4)
    check("the Coverage Map does not credit SondeHub until a balloon layer is on", "SondeHub" not in credits())
    d.execute_script("var c=document.getElementById('cv-sonde'); if(c){c.checked=true;c.dispatchEvent(new Event('change'))}"); time.sleep(3)
    check("and does once the receiving stations layer is on", "SondeHub" in credits(), credits())
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
