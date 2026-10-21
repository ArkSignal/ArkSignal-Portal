"""Switching the kind of map happens in place (static/maphost.js): every type opens from every other with the address bar, title and menu following, no script errors, one map on the page, nothing left running from the page put away, and Back returns."""
import os, sys, time
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); B = os.environ.get("COUNTY_BASE", f"http://{HOST}:18431")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP: selenium is not installed"); sys.exit(0)
TYPES = [("aircraft", "/live"), ("weather", "/weather/map"), ("balloons", "/sondes/map"), ("counties", "/counties/"), ("coverage", "/coverage"), ("repeaters", "/repeaters/map"), ("aprs", "/aprs/map"), ("mesh", "/mesh/map")]
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900"); o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
d = webdriver.Chrome(options=o)
def errors():
    return [l["message"][:220] for l in d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"] and "tile" not in l["message"].lower()]
def switch(path):
    d.execute_script("var p=arguments[0], s=document.getElementById('lv-maptype'); var o=[].slice.call(s.options).filter(function(x){return x.value.split('?')[0]===p})[0]; s.value=o.value; s.dispatchEvent(new Event('change'))", path)
try:
    d.get(B + "/live?lat=34.8&lon=-92.3&zoom=8"); time.sleep(5); errors()
    check("the host script is on the page", d.execute_script("return !!window.ArkMapHost"))
    for name, path in TYPES[1:] + [TYPES[0]]:
        n0 = d.execute_script("return ArkMapHost.swaps()")
        switch(path); time.sleep(6)
        r = d.execute_script("return {swaps: ArkMapHost.swaps(), path: location.pathname, sel: document.getElementById('lv-maptype').value.split('?')[0], maps: document.querySelectorAll('.leaflet-container').length, title: document.title, view: window.arkMap ? [window.arkMap.getCenter().lat.toFixed(1), window.arkMap.getZoom()] : null, lvs: document.querySelectorAll('#lv').length, count: (document.getElementById('lv-count')||{}).textContent}")
        check(f"{name}: opened in place at {path}", r["swaps"] == n0 + 1 and r["path"].rstrip("/") == path.rstrip("/") and r["sel"] == path and r["maps"] == 1 and r["lvs"] == 1, r)
        check(f"{name}: the view stayed where it was", r["view"] and r["view"][0] == "34.8" and r["view"][1] == 8, r["view"])
        e = errors(); check(f"{name}: no script errors", not e, e[:3])
    # nothing from a page put away keeps running: the live aircraft feed stops once we are away from the aircraft map
    switch("/repeaters/map"); time.sleep(3)
    d.execute_script("window.__t0 = performance.now()"); time.sleep(8)
    n = d.execute_script("return performance.getEntriesByType('resource').filter(function(e){return e.startTime > window.__t0 && e.name.indexOf('/api/aircraft.json') >= 0}).length")
    check("the aircraft feed is not polled while another map shows", n == 0, n)
    d.execute_script("history.back()"); time.sleep(6)
    check("Back returns to the earlier map", d.execute_script("return location.pathname") != "/repeaters/map" and d.execute_script("return document.querySelectorAll('.leaflet-container').length") == 1, d.execute_script("return location.pathname"))
    e = errors(); check("no script errors after Back", not e, e[:3])
    # a direct visit and a switched one have the same layers on the map
    d.get(B + "/repeaters/map?lat=34.8&lon=-92.3&zoom=8"); time.sleep(5)
    direct = d.execute_script("return Object.keys(arkMap._layers).length")
    d.get(B + "/live?lat=34.8&lon=-92.3&zoom=8"); time.sleep(4); switch("/repeaters/map"); time.sleep(6)
    sw = d.execute_script("return Object.keys(arkMap._layers).length")
    check("a switched Repeater Map has the layers of a direct one", abs(sw - direct) <= 3, (direct, sw))
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
