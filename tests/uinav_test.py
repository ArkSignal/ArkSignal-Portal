"""U1: the top navigation and the bottom bar stay in view on long pages, on desktop and phone, and never push the live map off screen.
Needs selenium and Chrome on the PC. UINAV_BASE picks another instance."""
import os, re, sys, time
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP uinav_test: selenium not installed"); sys.exit(0)
B = os.environ.get("UINAV_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
bad = []; ok = 0
def check(name, cond, detail=""):
    global ok
    if cond: ok += 1
    else: bad.append(f"{name} {detail}")
    print(("PASS " if cond else "FAIL ") + name, detail if not cond else "")

def make(mobile):
    o = webdriver.ChromeOptions()
    o.add_argument("--headless=new"); o.add_argument("--hide-scrollbars")
    if mobile:
        o.add_experimental_option("mobileEmulation", {"deviceMetrics": {"width": 375, "height": 812, "pixelRatio": 2}, "userAgent":
            "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"})
    else:
        o.add_argument("--window-size=1280,800")
    return webdriver.Chrome(options=o)

RECTS = """var n=document.getElementById('mainnav').getBoundingClientRect(),f=document.querySelector('footer').getBoundingClientRect();
return {navTop:n.top,navBottom:n.bottom,footTop:f.top,footBottom:f.bottom,h:window.innerHeight,sy:window.scrollY,sh:document.documentElement.scrollHeight}"""

for mobile in (False, True):
    d = make(mobile); tag = "phone" if mobile else "desktop"
    try:
        for p in ("/release-notes", "/roadmap", "/feed/"):
            d.get(B + p); time.sleep(0.8)
            r0 = d.execute_script(RECTS)
            check(f"{tag} {p} is long enough to scroll", r0["sh"] > r0["h"] + 200, str(r0))
            d.execute_script("window.scrollTo(0, 600)"); time.sleep(0.4)
            r = d.execute_script(RECTS)
            check(f"{tag} {p} nav stays at the top after scrolling", abs(r["navTop"]) < 1 and r["sy"] > 100, str(r))
            if not mobile:   # on a phone the footer is two short lines at the end of the page (it used to be a bar over the content), so it is only checked at the end of the page
                check(f"{tag} {p} footer bar is on screen while scrolled up the page", r["footBottom"] <= r["h"] + 1 and r["footTop"] > r["navBottom"], str(r))
            d.execute_script("window.scrollTo(0, document.documentElement.scrollHeight)"); time.sleep(0.4)
            r = d.execute_script(RECTS)
            check(f"{tag} {p} footer bar sits at the very bottom", abs(r["footBottom"] - r["h"]) < 2, str(r))
        # a link inside the page is not hidden under the bars when jumped to
        d.get(B + "/live"); time.sleep(2.5)
        r = d.execute_script(RECTS)
        m = d.execute_script("var m=document.getElementById('lv').getBoundingClientRect();return {top:m.top,bottom:m.bottom,h:window.innerHeight}")
        check(f"{tag} live map starts below the nav", m["top"] >= r["navBottom"] - 1, str(m))
        check(f"{tag} live map fits the screen with no page scroll hiding it", m["bottom"] <= m["h"] + 1, str(m))
        if mobile:
            d.get(B + "/status"); time.sleep(0.6)
            d.find_element(By.ID, "nav-toggle").click(); time.sleep(0.3)
            h = d.execute_script("return document.getElementById('mainnav').getBoundingClientRect().height <= window.innerHeight")
            check("phone menu drawer never taller than the screen", h)
    finally:
        d.quit()
print(f"{ok} passed, {len(bad)} failed")
if not bad: print("ALL PASS")
if bad:
    print("\n".join(bad)); sys.exit(1)
