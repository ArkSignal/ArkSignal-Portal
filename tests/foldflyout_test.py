"""Live map: the Filters and Weather flyouts fold up from a small arrow centred on their bottom edge; the choice is remembered; the buttons bring them back."""
import os, sys, time
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP foldflyout_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
def rect(d, sel):
    return d.execute_script("var e=document.querySelector(arguments[0]); if(!e) return null; var r=e.getBoundingClientRect(); return [r.left,r.top,r.right,r.bottom,getComputedStyle(e).display]", sel)
def js_click(d, sel): d.execute_script("document.querySelector(arguments[0]).click()", sel); time.sleep(.5)
def altbar(d, label, when):
    bar, mp = rect(d, "#lv-altbar"), rect(d, "#lv")
    vh = d.execute_script("return window.innerHeight")
    ok = bar and bar[4] != "none" and bar[3] <= vh + 1 and bar[1] >= mp[3] - 1 and bar[3] > bar[1] + 8
    check(f"{label}: the altitude bar is under the map and fully on screen {when}", ok, (bar, mp, vh))
    grad = d.execute_script("return getComputedStyle(document.getElementById('lv-altgrad')).backgroundImage")
    ticks = d.execute_script("return Array.from(document.querySelectorAll('#lv-alticks span')).map(function (x) { return x.textContent; }).join(' ')")
    check(f"{label}: it is a colour gradient with labels {when}", "linear-gradient" in grad and ticks.startswith("0 10k 20k 30k") and "40k" in ticks, (grad[:60], ticks))
def run(width, height, label):
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument(f"--window-size={width},{height}")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + "/live?nolocal=1"); time.sleep(2)
        d.execute_script("try{localStorage.clear()}catch(e){}"); d.get(B + "/live"); time.sleep(3)
        if width > 700:
            check(f"{label}: Filters start open on a wide screen and the arrow shows", rect(d, "#lv-toolsbody")[4] == "block" and rect(d, "#lv-fold")[4] == "block")
        else:
            js_click(d, "#lv-filtbtn")
            # on a phone the Filters fill the screen and have their own Close bar, so the arrow is hidden (static/mapfs.js)
            check(f"{label}: opening Filters fills the screen with a Close button instead of the arrow", d.execute_script("return document.getElementById('lv').classList.contains('fs-open')") and rect(d, "#lv-fold")[4] == "none")
        if width > 700:
            t, f = rect(d, "#lv-tools"), rect(d, "#lv-fold")
            check(f"{label}: the arrow is centred on the bottom edge of the Filters panel", abs((f[0] + f[2]) / 2 - (t[0] + t[2]) / 2) < 4 and abs((f[1] + f[3]) / 2 - t[3]) < 16, (t, f))
            check(f"{label}: the arrow is big enough to tap", f[3] - f[1] >= 20 and f[2] - f[0] >= 44, f)
            js_click(d, "#lv-fold")
            check(f"{label}: the arrow folds the Filters up and hides itself", rect(d, "#lv-toolsbody")[4] == "none" and rect(d, "#lv-fold")[4] == "none")
        else:
            js_click(d, "#fs-close")
            check(f"{label}: Close folds the Filters up", rect(d, "#lv-toolsbody")[4] == "none")
        check(f"{label}: the Filters button says it can bring them back", d.find_element(By.ID, "lv-filtbtn").text.startswith("Layers"))
        d.get(B + "/live"); time.sleep(3)
        check(f"{label}: the folded choice is remembered after a reload", rect(d, "#lv-toolsbody")[4] == "none" and rect(d, "#lv-fold")[4] == "none")
        js_click(d, "#lv-filtbtn")
        check(f"{label}: the Filters button opens them again (with the arrow, or full screen with a Close button on a phone)", rect(d, "#lv-toolsbody")[4] == "block" and (rect(d, "#lv-fold")[4] == "block" if width > 700 else d.execute_script("return document.getElementById('lv').classList.contains('fs-open')")))
        if width <= 700:
            js_click(d, "#fs-close")
        js_click(d, "#lv-wxbtn"); time.sleep(1.5)
        w, f = rect(d, "#lv-wx"), rect(d, "#lv-fold")
        check(f"{label}: Weather opens in place of Filters, the arrow sits on the Weather panel's bottom edge", w[4] == "block" and rect(d, "#lv-toolsbody")[4] == "none" and abs((f[0] + f[2]) / 2 - (w[0] + w[2]) / 2) < 4 and abs((f[1] + f[3]) / 2 - w[3]) < 16, (w, f))
        altbar(d, label, "with Weather open")
        js_click(d, "#lv-fold")
        altbar(d, label, "with every panel folded")
        d.execute_script("var s=document.getElementById('l-units'); s.value='metric'; s.dispatchEvent(new Event('change',{bubbles:true}))"); time.sleep(1)
        check(f"{label}: the labels follow the chosen units", d.find_element(By.ID, "lv-altname").text == "Altitude (m)" and d.execute_script("return document.getElementById('lv-alticks').textContent").startswith("0"), d.find_element(By.ID, "lv-altname").text)
        check(f"{label}: the altitude bar is not inside any panel that can fold", d.execute_script("return !document.getElementById('lv-altbar').closest('#lv-left, #lv-tools, #lv-wx, #lv-legend, #lv-list')"))
        check(f"{label}: the arrow folds Weather up too", rect(d, "#lv-wx")[4] == "none" and rect(d, "#lv-fold")[4] == "none")
    finally:
        d.quit()
run(1400, 900, "desktop")
run(390, 800, "phone")
print("ALL PASS" if not bad else "SOME FAILED")
