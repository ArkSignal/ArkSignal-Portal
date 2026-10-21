"""Every map page fits the window: no page scroll, the footer in view, at a laptop, a small window and a phone. Needs selenium and Chrome on the PC."""
import os, sys, time
try:
    from selenium import webdriver
except ImportError:
    print("SKIP mapfit_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
PAGES = ["/live", "/weather/map", "/sondes/map", "/counties/", "/repeaters/map", "/aprs/map"]
SIZES = [(1700, 1300, False), (1400, 900, False), (1100, 650, False), (375, 812, True)]
for w, h, phone in SIZES:
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new")
    if phone:
        o.add_experimental_option("mobileEmulation", {"deviceMetrics": {"width": w, "height": h, "pixelRatio": 2}, "userAgent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36"})
    else:
        o.add_argument(f"--window-size={w},{h}")
    d = webdriver.Chrome(options=o)
    try:
        for p in PAGES:
            d.get(B + p); time.sleep(5)
            m = d.execute_script("var f=document.querySelector('footer'),r=f?f.getBoundingClientRect():null,lv=document.getElementById('lv').getBoundingClientRect();return {sh:document.documentElement.scrollHeight, ih:innerHeight, fb:r?r.bottom:null, ft:r?r.top:null, lvb:lv.bottom, lvh:lv.height}")
            check(f"{w}x{h} {p}: the page is no taller than the window (nothing to scroll), the footer is in view and the map ends above it", m["sh"] <= m["ih"] + 2 and m["fb"] is not None and m["fb"] <= m["ih"] + 2 and (m["lvb"] <= m["ft"] + 2 or (phone and m["ft"] == 0 and m["lvb"] <= m["ih"] + 2)) and m["lvh"] >= 170, m)   # on a phone the footer is not drawn on a map page (its links are in the menu): the map then runs to the bottom
    finally:
        d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
