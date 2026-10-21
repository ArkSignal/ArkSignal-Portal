import os, sys, time
from selenium import webdriver
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); B = os.environ.get("MOBILE_BASE", f"http://{HOST}:18431")
PAGES = ["/live", "/weather/map", "/sondes/map", "/counties/", "/repeaters/map", "/aprs/map", "/coverage"]
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    for p in PAGES:
        d.get(B + p + "?lat=36.1&lon=-94.2&zoom=9"); time.sleep(5)
        info = d.execute_script("""var m=window.arkMap||window.map||window.smMap, c=m&&m.getCenter(); return {center:c?[+c.lat.toFixed(2),+c.lng.toFixed(2),m.getZoom()]:null, rows:document.querySelectorAll('#lv-tbl tr.it').length, listhidden:getComputedStyle(document.getElementById('lv-list')).display}""")
        d.execute_script("var b=document.getElementById('lv-toolsbody'); if(b.style.display==='none') document.getElementById('lv-filtbtn').click(); var l=document.getElementById('lv-list'); if(getComputedStyle(l).display==='none') document.getElementById('lv-hide').click()"); time.sleep(.5)
        before = d.execute_script("return document.getElementById('lv-toolsbody').style.display!=='none'")
        d.execute_script("var r=document.querySelector('#lv-tbl tr.it'); if(r) r.click()"); time.sleep(1.5)
        after = d.execute_script("return {tools:document.getElementById('lv-toolsbody').style.display, card:getComputedStyle(document.getElementById('lv-card')).display}")
        print("%-16s center/zoom=%s rows=%s filters_open_before=%s after_pick=%s" % (p, info["center"], info["rows"], before, after))
finally:
    d.quit()
