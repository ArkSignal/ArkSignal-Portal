"""The round arrow at the bottom right of long pages: hidden at the top, shown after scrolling a screenful, one tap returns to the top; not shown on a short page. Wide screen and phone width. Needs selenium and Chrome."""
import os, sys, time
try:
    from selenium import webdriver
except ImportError:
    print("SKIP totop_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def driver(phone):
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new")
    if phone:
        o.add_experimental_option("mobileEmulation", {"deviceMetrics": {"width": 375, "height": 812, "pixelRatio": 2}, "userAgent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"})
    else:
        o.add_argument("--window-size=1400,800")
    return webdriver.Chrome(options=o)
for phone in (False, True):
    d = driver(phone); tag = "phone" if phone else "desktop"
    try:
        d.get(B + "/status"); time.sleep(3)
        vis = lambda: d.execute_script("var b=document.getElementById('to-top'); return !!b && getComputedStyle(b).display !== 'none'")
        check(f"{tag}: the arrow is hidden at the top of a long page", not vis())
        long = d.execute_script("return document.documentElement.scrollHeight") > 1600
        check(f"{tag}: the test page is long enough", long, d.execute_script("return document.documentElement.scrollHeight"))
        d.execute_script("window.scrollTo(0, 1200)"); time.sleep(.6)
        check(f"{tag}: it shows after scrolling a screenful down", vis())
        info = d.execute_script("var r=document.getElementById('to-top').getBoundingClientRect(); var t=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2); return {right: innerWidth - r.right, bottom: innerHeight - r.bottom, w: r.width, top: t && t.id}")
        check(f"{tag}: it sits at the bottom right, is big enough to tap and nothing covers it", info["right"] < 40 and info["bottom"] < 120 and info["w"] >= 44 and info["top"] == "to-top", info)
        d.execute_script("document.getElementById('to-top').click()"); time.sleep(1.5)
        check(f"{tag}: one tap returns to the top and the arrow hides", d.execute_script("return window.pageYOffset") < 5 and not vis(), d.execute_script("return window.pageYOffset"))
        d.get(B + "/my/login"); time.sleep(2)
        check(f"{tag}: a short page never shows it", not vis() and d.execute_script("return document.documentElement.scrollHeight") <= d.execute_script("return innerHeight") + 600)
    finally:
        d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
