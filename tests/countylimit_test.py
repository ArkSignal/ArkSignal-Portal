"""County Map: Limit To (everywhere, one state, one county, near a ZIP code or place) applies to every Show on the Map kind. Uses the persistent test portal (:18431, real county outlines and hospitals)."""
import os, sys, time, requests
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); P = os.environ.get("COUNTY_BASE", f"http://{HOST}:18431")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
h = requests.get(P + "/counties/").text
check("the Show on the Map section has a Limit To box with state, county and near choices", all(x in h for x in ('id="cm-lim"', 'value="state"', 'value="county"', 'value="near"', 'id="cm-lim-q"')))
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1500,950")
d = webdriver.Chrome(options=o); J = d.execute_script
def markers(): return J("return document.querySelectorAll('#lvmap .hico').length")
try:
    d.get(P + "/counties/?show=hospitals"); time.sleep(8)
    all_n = markers()
    check("with nothing limited the hospitals are all drawn", all_n >= 40, all_n)
    J("var s=document.getElementById('cm-lim'); s.value='state'; s.dispatchEvent(new Event('change'))"); time.sleep(.5)
    J("var s=document.getElementById('cm-lim-state'); s.value='AR'; s.dispatchEvent(new Event('change'))"); time.sleep(2)
    ar = markers()
    check("limited to Arkansas, fewer are drawn than everywhere, and the box says how many", 0 < ar <= all_n and "Showing" in J("return document.getElementById('cm-lim-note').textContent"), (ar, all_n))
    J("var s=document.getElementById('cm-lim'); s.value='county'; s.dispatchEvent(new Event('change'))"); time.sleep(.5)
    J("var s=document.getElementById('cm-lim-county'); var o=[].slice.call(s.options).filter(function(x){return x.text.indexOf('Pulaski, AR')===0})[0]; s.value=o.value; s.dispatchEvent(new Event('change'))"); time.sleep(2)
    pu = markers()
    check("limited to Pulaski County only its hospitals are drawn", 3 <= pu < ar, (pu, ar))
    J("var s=document.getElementById('cm-lim'); s.value='near'; s.dispatchEvent(new Event('change'))"); time.sleep(.5)
    J("document.getElementById('cm-lim-q').value='72201'; document.getElementById('cm-lim-go').click()"); time.sleep(5)
    ne = markers()
    check("near a ZIP code (25 miles) the hospitals around Little Rock are drawn", 3 <= ne < ar, (ne, ar))
    J("var s=document.getElementById('cm-lim-r'); s.value='5'; s.dispatchEvent(new Event('change'))"); time.sleep(1.5)
    check("a smaller distance draws fewer", 1 <= markers() < ne, (markers(), ne))
    link = J("return window.arkViewURL ? window.arkViewURL() : ''")
    check("the link to this view carries the limit", "limit=near" in link, link)
    d.get(link); time.sleep(8)
    check("opening that link restores the limit", J("return document.getElementById('cm-lim').value") == "near" and 1 <= markers() < ne + 1, markers())
    J("var s=document.getElementById('cm-lim'); s.value='all'; s.dispatchEvent(new Event('change'))"); time.sleep(2)
    check("Everywhere draws them all again", markers() == all_n, (markers(), all_n))
    J("localStorage.removeItem('lv.cm.lim')")
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
