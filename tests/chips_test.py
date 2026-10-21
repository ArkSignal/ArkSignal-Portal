"""Live map: active-filter chips appear for each filter that is on, clicking a chip switches that filter off, Clear All removes them all."""
import os, sys, time
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP chips_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o); bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
def chips(): return [c.text.replace("×", "").strip() for c in d.find_elements(By.CSS_SELECTOR, "#lv-chips .chip")]
def js_click(sel): d.execute_script("document.querySelector(arguments[0]).click()", sel); time.sleep(.4)
try:
    d.get(B + "/live?nolocal=1"); time.sleep(3)
    d.execute_script("try{localStorage.clear()}catch(e){}"); d.get(B + "/live"); time.sleep(3)
    n_mil = d.execute_script("return Array.from(document.querySelectorAll('#lv-toolsbody label')).filter(function (l) { return l.textContent.trim().indexOf('Military') === 0; }).length")
    check("Military is offered once, under Aircraft Flags", n_mil == 1 and d.execute_script("return !!document.querySelector('#f-flags #f-mil')"), n_mil)
    check("no chips while no filter is on", chips() == [], chips())
    js_click("#f-mil")
    check("switching on Military (a flag) adds a chip", chips() == ["Military"], chips())
    js_click('#f-sources input[data-src="mlat"]')
    js_click('#f-kinds input[data-kind="jet"]')
    check("a source turned off and an aircraft type each get a chip", "No MLAT" in chips() and "Jets" in chips(), chips())
    d.execute_script("var q=document.getElementById('q'); q.value='AAL'; q.dispatchEvent(new Event('input',{bubbles:true}))"); time.sleep(.5)
    check("a search gets a chip", any(c.startswith("Search: AAL") for c in chips()), chips())
    check("Clear All appears when there are several", "Clear All" in chips(), chips())
    d.find_element(By.XPATH, "//button[contains(@class,'chip') and starts-with(normalize-space(.), 'Military')]").click(); time.sleep(.5)
    check("clicking a chip switches that filter off", not d.find_element(By.ID, "f-mil").is_selected() and "Military" not in chips(), chips())
    d.find_element(By.CSS_SELECTOR, "#lv-chips .chipall").click(); time.sleep(.6)
    check("Clear All switches every filter off", chips() == [] and d.find_element(By.CSS_SELECTOR, '#f-sources input[data-src="mlat"]').is_selected()
          and not d.find_element(By.CSS_SELECTOR, '#f-kinds input[data-kind="jet"]').is_selected() and d.find_element(By.ID, "q").get_attribute("value") == "", chips())
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED")
