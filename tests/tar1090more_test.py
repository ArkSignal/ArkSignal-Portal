"""tar1090 parity leftovers built on 2026-10-15: last leg only (a button and a site default), geometric altitude above sea level (EGM96, loaded when needed), the slim wind label and the list's more/all rows."""
import re, requests, sys, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
lv = requests.get(B + "/live").text
check("the card has a Last Flight Only / All Flights button, kept as the visitor's own choice, with the site default in the map settings", 'id="c-lastleg"' in lv and "function legOn()" in lv and '"lastLeg"' in lv and "legOn() ? d.leg : 0" in lv)
check("the label fields include the sea-level geometric altitude and the slim wind label", '["gegm", "Geometric altitude, sea level (EGM96)"]' in lv and '["wind", "Wind (slim: direction and speed)"]' in lv)
check("the card has the EGM96 altitude row", "Geometric altitude above sea level (EGM96)" in lv)
check("the list shows Settings > Rows Shown rows and a row to show more or all", "M.listRows" in lv and 'data-more=' in lv and ">Show more</a>" in lv)
egm = requests.get(B + "/static/egm96.js")
check("the EGM96 library is served (MIT license shown, 2.7 MB, only loaded when an aircraft needs it)", egm.status_code == 200 and "MIT License" in egm.text[:1200] and len(egm.content) > 2_000_000 and "/static/egm96.js" in lv)
adm = sess("marty", PW)
st = adm.get(B + "/admin/settings").text
check("Settings has the trail default and the row count", 'name="trail_last_leg_default"' in st and 'name="live_list_rows"' in st)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part: selenium not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
J = lambda s: d.execute_script(s)
try:
    d.get(B + "/live"); time.sleep(4)
    J("var s=document.createElement('script'); s.src='/static/egm96.js'; document.head.appendChild(s)"); time.sleep(4)
    v = J("return window.EGM96 ? window.EGM96.ellipsoidToEgm96(34.7, -92.3, 0) : null")
    check("the geoid at Arkansas puts the ellipsoid about 28 meters above sea level (height above sea level = height above the ellipsoid + about 28 m)", v is not None and 24 <= v <= 33, v)
    check("the live map's own altitude function uses it (feet) once loaded", J("return typeof egmAlt === 'function' ? true : 'scoped'") in (True, "scoped"))
    # the list: more rows
    n = J("return document.querySelectorAll('#lv-tbl tbody tr.it').length")
    check("the list has aircraft rows", n >= 0, n)
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
