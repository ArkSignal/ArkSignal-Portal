"""A Map button is always in the top bar next to Display (and next to the menu button on a phone), and opens the live map."""
import os, re, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
adm = sess("marty", PW)
for who, s in (("a guest", requests), ("a signed-in administrator", adm)):
    t = s.get(B + "/about").text
    nav = re.search(r'<nav id="mainnav">.*?</nav>', t, re.S).group(0)
    check(f"{who} has the Map button in the top bar, before Display", 'class="mapbtn' in nav and 'href="/live"' in nav and nav.index('class="mapbtn') < nav.index('id="prefs-menu"'))
check("it is also on the admin pages", 'class="mapbtn' in adm.get(B + "/admin/").text)
check("on the live map itself it is marked as the current page", 'class="mapbtn here"' in requests.get(B + "/live").text and 'class="mapbtn here"' not in requests.get(B + "/about").text)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser checks"); print("ALL PASS" if not bad else "SOME FAILED"); sys.exit(0)
def browser(width, height):
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument(f"--window-size={width},{height}")
    return webdriver.Chrome(options=o)
d = browser(1400, 800)
try:
    d.get(B + "/about"); time.sleep(1.5)
    r = d.execute_script("""var m = document.querySelector('.mapbtn').getBoundingClientRect(), p = document.querySelector('#prefs-menu > summary').getBoundingClientRect(); return {mx: m.right, py: p.left, w: m.width, h: m.height, top: m.top, ptop: p.top}""")
    check("wide screen: the button is visible, just left of Display, on the same bar", r["w"] > 30 and r["h"] > 20 and r["mx"] <= r["py"] + 2 and abs(r["top"] - r["ptop"]) < 14, r)
    d.execute_script("document.querySelector('.mapbtn').click()"); time.sleep(2)
    check("clicking it opens the live map", d.current_url.rstrip("/").endswith("/live"), d.current_url)
finally:
    d.quit()
d = browser(390, 800)
try:
    d.get(B + "/about"); time.sleep(1.5)
    r = d.execute_script("""var m = document.querySelector('.mapbtn').getBoundingClientRect(), t = document.querySelector('#nav-toggle').getBoundingClientRect(); return {vis: getComputedStyle(document.querySelector('.mapbtn')).display, mr: m.right, tl: t.left, top: m.top, inview: m.right <= innerWidth && m.left >= 0}""")
    check("phone: the button stays in the bar beside the menu button while the menu is folded", r["vis"] != "none" and r["inview"] and r["mr"] <= r["tl"] + 2 and r["top"] < 60, r)
    d.execute_script("document.getElementById('nav-toggle').click()"); time.sleep(.6)
    r = d.execute_script("var m = document.querySelector('.mapbtn').getBoundingClientRect(); return {vis: getComputedStyle(document.querySelector('.mapbtn')).display, w: m.width}")
    check("phone: it is also in the opened menu", r["vis"] != "none" and r["w"] > 100, r)
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED")
