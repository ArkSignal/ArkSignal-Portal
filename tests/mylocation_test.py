"""Show my location: it never moves a map that opened somewhere else (Show on Map keeps working), the bullseye button on the map goes to you on request, and Drive Times From My Location is offered.
Needs selenium and a secure address for the browser (http://localhost through the test box tunnel)."""
import os, sys, time, requests
try:
    from selenium import webdriver
except ImportError:
    print("SKIP: selenium not installed"); sys.exit(0)
B = os.environ.get("LOCATE_BASE", "http://localhost:18431")
try:
    requests.get(B + "/", timeout=5)
except Exception:
    print("SKIP: the localhost tunnel to the test box is not up"); sys.exit(0)
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    d.execute_cdp_cmd("Emulation.setGeolocationOverride", {"latitude": 34.2, "longitude": -92.0, "accuracy": 30})
    d.execute_cdp_cmd("Browser.grantPermissions", {"permissions": ["geolocation"], "origin": B})
    for page in ("/live", "/weather/map", "/counties/", "/sondes/map"):
        d.get(B + "/"); d.execute_script("localStorage.setItem('lv.myloc','1')")
        d.get(B + page + "?lat=36.5&lon=-94.5&zoom=8"); time.sleep(6)
        c = d.execute_script("var c=arkMap.getCenter(); return [+c.lat.toFixed(1), +c.lng.toFixed(1), arkMap.getZoom()]")
        dot = d.execute_script("return !!document.querySelector('.leaflet-overlay-pane path[stroke=\"#fff\"], #lvmap svg path[stroke=\"#fff\"]')")
        check(f"{page}: with Show my location on, the map stays where the link put it", c == [36.5, -94.5, 8], c)
        check(f"{page}: the bullseye button is on the map", d.execute_script("return !!document.querySelector('.lv-locate a')"))
    d.get(B + "/counties/?lat=36.5&lon=-94.5&zoom=8"); time.sleep(5)
    d.execute_script("document.querySelector('.lv-locate a').click()"); time.sleep(3)
    c = d.execute_script("var c=arkMap.getCenter(); return [+c.lat.toFixed(1), +c.lng.toFixed(1)]")
    check("pressing the bullseye goes to your position", c == [34.2, -92.0], c)
    check("Drive Times From My Location is in the Drive Times section", d.execute_script("return !!document.getElementById('dr-here')"))
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
