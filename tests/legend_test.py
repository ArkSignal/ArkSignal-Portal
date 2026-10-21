"""Live map legend: a collapsible Legend panel that explains the colours of whatever is switched on."""
import os, sys, time
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP legend_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o); bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
def text(): return d.find_element(By.ID, "lv-legbody").text.lower()
def js(code): return d.execute_script(code)
def setbox(i, on): js("var c=document.getElementById('%s'); if (c.checked !== %s) c.click()" % (i, "true" if on else "false")); time.sleep(.4)
try:
    d.get(B + "/live"); js("try{localStorage.clear()}catch(e){}"); d.get(B + "/live"); time.sleep(3)
    check("the legend starts folded with a count of what it explains", d.find_element(By.ID, "lv-legbtn").text.startswith("Legend") and not d.find_element(By.ID, "lv-legbody").is_displayed() and "(" in d.find_element(By.ID, "lv-legn").text)
    d.find_element(By.ID, "lv-legbtn").click(); time.sleep(.4)
    t = text()
    check("opening it explains the altitude colours and stations", d.find_element(By.ID, "lv-legbody").is_displayed() and "aircraft" in t and "40k+" in t and "stations" in t, t)
    check("layers that are off are not explained", "hospitals" not in t and "range rings" not in t, t)
    setbox('l-hospitals', True)
    t = text()
    check("switching on Hospitals adds the trauma colours", "hospitals" in t and "trauma level i" in t and "emergency department" in t and "approximate" in t, t)
    setbox('l-rings', True); setbox('l-wxair', True)
    t = text()
    check("range rings and airport weather are added too", "range rings" in t and "every" in t and "vfr" in t and "lifr" in t, t)
    setbox('l-hospitals', False)
    check("switching a layer off removes its entry", "hospitals" not in text())
    check("the count follows", d.find_element(By.ID, "lv-legn").text == "(%d)" % (1 + js("return ['l-stations','l-coverage','l-airports','l-wxair','l-hospitals','l-rings'].filter(function(i){return document.getElementById(i).checked}).length")), d.find_element(By.ID, "lv-legn").text)
    d.get(B + "/live"); time.sleep(3)
    check("an open legend stays open next time", d.find_element(By.ID, "lv-legbody").is_displayed())
    r = js("var a=document.getElementById('lv-legend').getBoundingClientRect(), t=document.getElementById('lv-tools').getBoundingClientRect(), l=document.getElementById('lv-left').getBoundingClientRect(); return [a.left >= l.left - 1 && a.right <= l.right + 1, a.top >= t.bottom - 2, a.bottom <= window.innerHeight]")
    check("it opens in the left column under the buttons, like Weather, and fits on screen", all(r), r)
    row = js("var l=document.getElementById('lv-legbtn').getBoundingClientRect(), f=document.getElementById('lv-filtbtn').getBoundingClientRect(), w=document.getElementById('lv-wxbtn').getBoundingClientRect(); return [Math.abs(l.top - f.top) < 3, Math.abs(l.top - w.top) < 3, l.left > f.left]")
    check("the Legend button is in the top row next to Weather and Filters", all(row), row)
    check("opening the legend closed the Filters (one flyout at a time)", js("return document.getElementById('lv-toolsbody').style.display === 'none'"))
    arrow = js("var f=document.getElementById('lv-fold').getBoundingClientRect(), a=document.getElementById('lv-legend').getBoundingClientRect(); return [getComputedStyle(document.getElementById('lv-fold')).display !== 'none', Math.abs((f.left + f.right) / 2 - (a.left + a.right) / 2) < 4, Math.abs((f.top + f.bottom) / 2 - a.bottom) < 16]")
    check("the fold arrow sits centred on the legend's bottom edge", all(arrow), arrow)
    js("document.getElementById('lv-fold').click()"); time.sleep(.4)
    check("the arrow folds the legend up", not d.find_element(By.ID, "lv-legbody").is_displayed() and d.find_element(By.ID, "lv-legbtn").get_attribute("aria-expanded") == "false")
    d.find_element(By.ID, "lv-legbtn").click(); time.sleep(.4)
    js("document.getElementById('lv-filtbtn').click()"); time.sleep(.4)
    check("opening the Filters closes the legend again", not d.find_element(By.ID, "lv-legbody").is_displayed() and js("return document.getElementById('lv-toolsbody').style.display === 'block'"))
    d.find_element(By.ID, "lv-legbtn").click(); time.sleep(.4)
    d.find_element(By.ID, "lv-legbtn").click(); time.sleep(.3)
    check("clicking again folds it", not d.find_element(By.ID, "lv-legbody").is_displayed())
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED")
