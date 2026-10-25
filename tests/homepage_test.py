"""The home page: a tall station map that shows Arkansas (all kinds of stations, no aircraft), a live Right Now panel instead of the release notes, and on the flight page the map fills its section."""
import re, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
h = requests.get(B + "/").text
check("the panel beside the map is the live Right Now panel", all(x in h for x in ("Worth a Look Right Now", "Records Right Now", "The Network", 'id="ov-now"')))
check("it no longer leads with the release notes there", 'class="grp" style="margin-top:0">Latest Release' not in h)
check("the release and the ways to join are still on the page, lower down", "ov-join" in h and "How to Feed" in h and "Release Notes" in h and "/roadmap" in h)
check("the map is a station map with a legend, not an aircraft map", "ov-legend" in h and "ADS-B Feeders" not in h.split("ov-legend")[1][:400] and "altColor" not in h)
check("the area the map shows is a setting (Arkansas by default)", re.search(r"HOME_AREA = \[33(\.0)?, -94\.62, 36\.5, -89\.64\]", h) is not None)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser checks"); print("ALL PASS" if not bad else "SOME FAILED"); sys.exit(0)
def browser(w, hgt):
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument(f"--window-size={w},{hgt}")
    return webdriver.Chrome(options=o)
d = browser(1400, 900)
try:
    d.get(B + "/"); time.sleep(4)
    r = d.execute_script("""var m=document.getElementById('ovmap').getBoundingClientRect(), b=window.ovMap.getBounds(); return {h: Math.round(m.height), w: Math.round(m.width), s: b.getSouth(), n: b.getNorth(), w2: b.getWest(), e: b.getEast()}""")
    check("the map is tall (at least 440 px on a 900 px screen)", r["h"] >= 440, r)
    check("the whole of Arkansas is in view, and not much else", r["s"] < 33.0 and r["n"] > 36.5 and r["w2"] < -94.6 and r["e"] > -89.7 and (r["n"] - r["s"]) < 6.5, r)
    n = d.execute_script("return (function(){var n=0;window.ovMap.eachLayer(function(l){if(l instanceof L.CircleMarker)n++});return n})()")
    pts = d.execute_script("return Array.from(document.querySelectorAll('.ov-legend label i')).map(function (i) { return parseInt(i.textContent) })")
    check("the markers are the stations (as many as the legend counts) and nothing else", pts and n == sum(pts), (n, pts))
    box = d.execute_script("var cb=document.querySelector('.ov-legend input'); return !!cb")
    check("the legend has a switch for each kind of station", box)
    if pts and pts[0]:
        d.execute_script("document.querySelectorAll('.ov-legend input[data-k]').forEach(function (cb) { cb.checked=false; cb.dispatchEvent(new Event('change',{bubbles:true})); })"); time.sleep(.5)
        off = d.execute_script("return (function(){var n=0;window.ovMap.eachLayer(function(l){if(l instanceof L.CircleMarker)n++});return n})()")
        check("switching a kind off takes its stations off the map", off == 0, off)
        d.get(B + "/"); time.sleep(3)
        check("and the choice is remembered", d.execute_script("return (function(){var n=0;window.ovMap.eachLayer(function(l){if(l instanceof L.CircleMarker)n++});return n})()") == 0)
        d.execute_script("localStorage.removeItem('ov.hidden')")
    panel = d.execute_script("var p=document.getElementById('ov-now').getBoundingClientRect(), m=document.getElementById('ovmap').getBoundingClientRect(); return {ph: Math.round(p.height), mh: Math.round(m.height)}")
    check("the Right Now panel is as tall as the map beside it", abs(panel["ph"] - panel["mh"]) <= 40, panel)
    api = requests.get(B + "/api/aircraft.json").json()["aircraft"]
    if api:
        d.get(B + "/flight/" + api[0]["hex"]); time.sleep(4)
        f = d.execute_script("""var p=document.getElementById('fl-map').parentElement.getBoundingClientRect(), m=document.getElementById('fl-map').getBoundingClientRect(); return {panelBottom: Math.round(p.bottom), mapBottom: Math.round(m.bottom), panelH: Math.round(p.height), mapH: Math.round(m.height)}""")
        check("flight page: the map fills its section down to the padding", f["panelBottom"] - f["mapBottom"] <= 14 and f["panelH"] - f["mapH"] <= 80, f)
    else:
        print("SKIP the flight page check: no aircraft in the air right now")
finally:
    d.quit()
d = browser(390, 800)
try:
    d.get(B + "/"); time.sleep(3)
    r = d.execute_script("return {over: document.documentElement.scrollWidth - document.documentElement.clientWidth, h: Math.round(document.getElementById('ovmap').getBoundingClientRect().height)}")
    check("phone: no sideways scrolling and the map is still tall enough to use", r["over"] <= 1 and r["h"] >= 300, r)
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED")
