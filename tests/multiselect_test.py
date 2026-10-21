"""Classic map features on the live map: select several aircraft (M, T, Esc) with a track each and a link that carries them; persistence (P) keeps aircraft that went quiet, dimmed, and drops them again when switched off."""
import os, sys, time, requests
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); B = os.environ.get("COUNTY_BASE", f"http://{HOST}:18431")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
live = requests.get(B + "/live").text
check("the live page offers the new selections and persistence", all(x in live for x in ('id="f-multi"', 'id="f-persist"', 'id="f-selall"', "Keep aircraft that go quiet")))
check("the keys M, T and P are in the shortcut help", all(f'<span class="mono">{k}</span>' in live for k in "MTP"))
try:
    from selenium import webdriver
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP the browser part: selenium is not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900"); o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
d = webdriver.Chrome(options=o)
SEL = "return document.querySelectorAll('#lvmap .ac.sel').length"
try:
    d.get(B + "/live?lat=34.8&lon=-92.3&zoom=8"); time.sleep(5)
    n = d.execute_script("return document.querySelectorAll('#lv-tbl tr.it').length")
    check("aircraft are listed", n >= 3, n)
    body = d.find_element(By.TAG_NAME, "body")
    body.send_keys("m"); time.sleep(.3)
    check("M switches on selecting several", d.execute_script("return document.getElementById('f-multi').checked"))
    for i in range(3):
        d.execute_script("document.querySelectorAll('#lv-tbl tr.it')[arguments[0]].click()", i); time.sleep(.8)
    time.sleep(2)
    check("three clicks select three aircraft", d.execute_script(SEL) == 3, d.execute_script(SEL))
    check("the chip says how many are selected", "3 aircraft selected" in d.execute_script("return document.getElementById('lv-chips').innerText"))
    url = d.execute_script("return arkViewURL()")
    check("the shared link carries all three", url.count("%2C") >= 2 and "icao=" in url, url)
    time.sleep(3)
    tracks = d.execute_script("return performance.getEntriesByType('resource').filter(function(e){return e.name.indexOf('/api/trace/') >= 0}).length")
    check("the selected aircraft's tracks are fetched (one each)", tracks >= 3, tracks)
    d.execute_script("document.querySelectorAll('#lv-tbl tr.it')[1].click()"); time.sleep(1)
    check("clicking a selected aircraft again takes it out", d.execute_script(SEL) == 2, d.execute_script(SEL))
    body.send_keys("t"); time.sleep(2)
    allsel = d.execute_script(SEL); marks = d.execute_script("return document.querySelectorAll('#lvmap .ac').length")
    check("T selects every aircraft shown", allsel >= 3 and abs(allsel - min(marks, 40)) <= 2, (allsel, marks))
    body.send_keys(Keys.ESCAPE); time.sleep(1)
    check("Esc deselects all", d.execute_script(SEL) == 0 and not d.execute_script("return document.getElementById('f-multi').checked"), d.execute_script(SEL))
    # a link with several aircraft selects them
    hexes = d.execute_script("return Array.from(document.querySelectorAll('#lv-tbl tr.it')).slice(0,3).map(function(r){return r.dataset.hex})")
    d.get(B + "/live?lat=34.8&lon=-92.3&zoom=8&icao=" + ",".join(hexes)); time.sleep(6)
    check("a link with several aircraft opens with them selected", d.execute_script(SEL) >= 2, d.execute_script(SEL))
    # persistence: the feed is cut to one aircraft; with persistence on the others stay, dimmed
    d.get(B + "/live?lat=34.8&lon=-92.3&zoom=8"); time.sleep(5)
    before = d.execute_script("return document.querySelectorAll('#lvmap .ac').length")
    d.execute_script("var of=window.fetch; window.fetch=function(u,i){ if(String(u).indexOf('/api/aircraft.json')>=0) return of.apply(this,arguments).then(function(r){return r.json()}).then(function(j){ j.aircraft=j.aircraft.slice(0,1); return new Response(JSON.stringify(j)); }); return of.apply(this,arguments); }")
    body = d.find_element(By.TAG_NAME, "body"); body.send_keys("p"); time.sleep(.3)
    check("P switches persistence on", d.execute_script("return document.getElementById('f-persist').checked"))
    time.sleep(12)
    after = d.execute_script("return {n: document.querySelectorAll('#lvmap .ac').length, ghost: document.querySelectorAll('#lvmap .ac.ghost').length}")
    check("with persistence the aircraft that went quiet stay, dimmed", before >= 3 and after["n"] >= before - 1 and after["ghost"] >= 1, (before, after))
    body.send_keys("p"); time.sleep(4)
    off = d.execute_script("return {n: document.querySelectorAll('#lvmap .ac').length, ghost: document.querySelectorAll('#lvmap .ac.ghost').length}")
    check("switching it off drops them again", off["ghost"] == 0 and off["n"] <= 2, off)
    errs = [l["message"][:200] for l in d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"]]
    check("no script errors", not errs, errs[:3])
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
