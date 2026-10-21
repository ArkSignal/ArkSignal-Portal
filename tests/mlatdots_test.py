"""The MLAT Network page's station dots are big and easy to see: each station is a dot at least 20 px across with a white edge (skipped, not failed, when the test box has no MLAT stations). Needs selenium and Chrome."""
import os, sys, time
try:
    from selenium import webdriver
except ImportError:
    print("SKIP mlatdots_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    d.get(B + "/mlat/"); time.sleep(4)
    dots = d.execute_script("""return Array.from(document.querySelectorAll('#mlatmap path.leaflet-interactive')).map(function (p) { var r = p.getBoundingClientRect(); return [Math.round(r.width), p.getAttribute('stroke'), p.getAttribute('stroke-width')]; })""")
    if not dots:
        print("note: no MLAT stations on this test box, dot size check skipped")
    else:
        check("every station dot is at least 20 px across", all(w >= 20 for w, _s, _k in dots), dots[:5])
        check("with a white edge three pixels wide", all(s == "#ffffff" and k == "3" for _w, s, k in dots), dots[:5])
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
