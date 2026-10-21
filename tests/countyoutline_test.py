"""County outlines are a box under Display and View on every map except the County Map (which has its own Look choice); ticking it draws a thin line around every county without catching clicks.
The drawing check needs county outlines in the database (the second test portal has them) and selenium with Chrome."""
import os, sys, time, requests
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
B = os.environ.get("MOBILE_BASE", f"http://{HOST}:18430")
B2 = os.environ.get("COUNTY_BASE", f"http://{HOST}:18431")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
for page in ("/live", "/weather/map", "/sondes/map", "/aprs/map", "/repeaters/map", "/coverage"):
    t = requests.get(B + page).text
    check(f"{page}: has the County outlines box and its script", 'id="l-county"' in t and "/static/countyoutline.js" in t)
check("the County Map has no such box (it has its own Look choice)", 'id="l-county"' not in requests.get(B + "/counties/").text)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part: selenium is not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
n = len(requests.get(B2 + "/api/counties/shapes.json").json().get("counties", []))
if n == 0:
    print("note: the second test portal has no county outlines, the drawing check is skipped"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    for page in ("/weather/map", "/sondes/map", "/live"):
        d.get(B2 + page); time.sleep(4)
        d.execute_script("try{localStorage.removeItem('lv.countyoutline')}catch(e){}")
        paths0 = d.execute_script("return document.querySelectorAll('#lvmap path.leaflet-interactive, #lvmap svg path').length")
        d.execute_script("var b=document.getElementById('l-county'); b.checked=true; b.dispatchEvent(new Event('change'))"); time.sleep(3)
        paths1 = d.execute_script("return document.querySelectorAll('#lvmap svg path').length")
        check(f"{page}: ticking County outlines draws the county lines", (page == "/live" or paths1 >= paths0 + n - 1) and  # the aircraft map draws on a canvas, not svg paths
           d.execute_script("return window.arkCountyOutline.loaded()"), (paths0, paths1, n))
        d.get(B2 + page); time.sleep(4)
        check(f"{page}: the choice is remembered", d.execute_script("return document.getElementById('l-county').checked && window.arkCountyOutline.loaded()"))
        d.execute_script("var b=document.getElementById('l-county'); b.checked=false; b.dispatchEvent(new Event('change'))"); time.sleep(.5)
        check(f"{page}: unticking removes them", d.execute_script("return document.querySelectorAll('#lvmap svg path').length") <= paths0 + 1)
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
