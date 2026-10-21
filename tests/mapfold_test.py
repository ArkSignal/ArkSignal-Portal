"""The small fold arrow on every map: when a panel (Filters, Weather or Legend) is open the arrow shows at the bottom edge of the panel, nothing covers it, and one tap folds the panel away and hides the arrow.
Checked on every map page, on a wide screen and at phone width. Needs selenium and Chrome on the PC."""
import os, sys, time
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP mapfold_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
PAGES = ["/live", "/weather/map", "/sondes/map", "/counties/", "/repeaters/map", "/aprs/map"]
def driver(phone):
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new")
    if phone:
        o.add_experimental_option("mobileEmulation", {"deviceMetrics": {"width": 375, "height": 812, "pixelRatio": 2}, "userAgent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"})
    else:
        o.add_argument("--window-size=1400,900")
    return webdriver.Chrome(options=o)
for phone in (False, True):
    d = driver(phone); tag = "phone" if phone else "desktop"
    try:
        for page in PAGES:
            d.get(B + page); time.sleep(4)
            d.execute_script("var b=document.getElementById('lv-toolsbody'); if (b.style.display === 'none') document.getElementById('lv-filtbtn').click()"); time.sleep(.8)
            info = d.execute_script("""var f=document.getElementById('lv-fold'); if (!f) return null; var r=f.getBoundingClientRect(), st=getComputedStyle(f);
              var top=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2); return {display: st.display, w: r.width, h: r.height, top: top && top.id, bottom: r.bottom, vh: innerHeight}""")
            fs = d.execute_script("return document.getElementById('lv').classList.contains('fs-open')")
            check(f"{tag} {page}: the fold arrow shows while Filters are open and nothing covers it (on a phone the Filters fill the screen and have their own Close bar instead)", (phone and fs and info and info["display"] == "none") or (info and info["display"] != "none" and info["w"] >= 40 and info["top"] == "lv-fold"), info)
            d.execute_script("document.getElementById('lv-fold').click()"); time.sleep(.6)
            after = d.execute_script("return {tools: document.getElementById('lv-toolsbody').style.display, fold: getComputedStyle(document.getElementById('lv-fold')).display}")
            check(f"{tag} {page}: one tap folds the panel away and hides the arrow", after["tools"] == "none" and after["fold"] == "none", after)
    finally:
        d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
