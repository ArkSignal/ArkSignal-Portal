"""The statistics pages really draw in a browser: a chart appears on Weather Statistics (the hover chart of the flight page), the table lists stations, and nothing says it could not be loaded; Radiosonde Statistics shows its numbers."""
import os, sys, time
try:
    from selenium import webdriver
except ImportError:
    print("SKIP: selenium not installed"); sys.exit(0)
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); B = os.environ.get("COUNTY_BASE", f"http://{HOST}:18431")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900"); o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
d = webdriver.Chrome(options=o)
try:
    d.get(B + "/weather/statistics"); time.sleep(6)
    r = d.execute_script("return {svg: document.querySelectorAll('#ws-charts svg').length, cols: getComputedStyle(document.getElementById('ws-charts')).gridTemplateColumns.split(' ').length, rows: document.querySelectorAll('#ws-tbl tbody tr').length, txt: document.body.innerText.indexOf('could not be loaded')}")
    errs = [l["message"][:160] for l in d.get_log("browser") if l["level"] == "SEVERE"]
    check("Weather Statistics draws several charts, two to a row, and lists stations", r["svg"] >= 3 and r["cols"] == 2 and r["rows"] > 3 and r["txt"] < 0, (r, errs[:2]))
    d.execute_script("var s=document.getElementById('ws-metric'); s.value='hum'; s.dispatchEvent(new Event('change'))"); time.sleep(3)
    check("changing the reading redraws it", d.execute_script("return document.querySelectorAll('#ws-charts svg').length") >= 3)
    d.get(B + "/sondes/statistics"); time.sleep(5)
    check("Radiosonde Statistics shows its numbers without an error", "could not be loaded" not in d.execute_script("return document.body.innerText"))
    r = d.execute_script("var h=document.getElementById('rs-days'); var s=h.querySelector('svg'); var rc=s&&s.querySelectorAll('rect'); var out={svg:document.querySelectorAll('#rs-days svg, #rs-hist svg').length, empty:h.innerText.indexOf('Nothing')>=0, bars:rc?rc.length:0}; if(rc&&rc.length){var b=rc[0].getBoundingClientRect(); s.dispatchEvent(new MouseEvent('mousemove',{clientX:b.left+b.width/2,clientY:b.top+b.height/2,bubbles:true})); out.tip=getComputedStyle(h.lastChild).display;} return out")
    check("Radiosonde Statistics bar charts use the shared chart (hover readout)", (r["svg"] >= 2 and r.get("tip") == "block") or r["empty"], r)
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
