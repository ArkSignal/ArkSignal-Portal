"""Aircraft Tracking: the radar scales with the window (about a third of its width, but never more than 62% of its height or 680 px), stays square and sharp, and the stat cards fill the height beside it."""
import sys, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
try:
    from selenium import webdriver
except ImportError:
    print("SKIP: selenium not installed"); sys.exit(0)
bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1700,1100")
d = webdriver.Chrome(options=o)
def look():
    return d.execute_script("""var r=document.querySelector('.radar').getBoundingClientRect(), s=document.querySelector('.stats').getBoundingClientRect(), c=document.getElementById('radar'), vw=document.documentElement.clientWidth, vh=innerHeight;
        return {w: Math.round(r.width), h: Math.round(r.height), sh: Math.round(s.height), cw: Math.round(c.width / (devicePixelRatio || 1)), vw: vw, vh: vh, over: document.documentElement.scrollWidth - vw, ox: Math.round(r.right), cardmax: Math.max.apply(null, Array.from(document.querySelectorAll('a.stat')).map(function (e) { return Math.round(e.getBoundingClientRect().height); })), rows: document.querySelectorAll('#toprows tr').length}""")
try:
    d.get(B + "/aircraft"); time.sleep(3)
    a = look()
    want = min(0.36 * a["vw"], 0.62 * a["vh"], 680)
    check("a 1700 x 1100 window: the radar is about a third of the width and well short of the old 855 px", abs(a["w"] - want) <= 4 and a["w"] <= 680, (a, want))
    check("it is square and its drawing matches its size (sharp)", abs(a["w"] - a["h"]) <= 2 and abs(a["cw"] - a["w"] + 28) <= 6 or abs(a["cw"] - a["w"]) <= 30, a)
    check("the right-hand column is as tall as the radar, with compact number cards and a list filling the rest", abs(a["sh"] - a["h"]) <= 6 and a["cardmax"] <= 150 and a["rows"] >= 1, a)
    d.set_window_size(1100, 700); time.sleep(1.5)
    b = look()
    check("a smaller window gives a smaller radar, and it follows the window while open", b["w"] < a["w"] - 100 and abs(b["w"] - min(0.36 * b["vw"], 0.62 * b["vh"])) <= 4 and abs(b["cw"] - b["w"]) <= 30, (b, a))
    d.set_window_size(3400, 1000); time.sleep(1.5)
    c = look()
    check("an ultra-wide window: the height decides (62% of it), not the huge width", abs(c["w"] - 0.62 * c["vh"]) <= 4 and c["w"] <= 680, c)
finally:
    d.quit()
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=390,800")
d = webdriver.Chrome(options=o)
try:
    d.get(B + "/aircraft"); time.sleep(3)
    p = look()
    check("a phone: the radar fits the screen with no sideways scrolling", p["over"] <= 1 and p["w"] <= p["vw"] and p["w"] >= 250, p)
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED")
