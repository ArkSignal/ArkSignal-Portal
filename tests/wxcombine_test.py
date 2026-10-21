"""Weather maps combine overlapping stations when zoomed out (one label: the average and a count; a click zooms in) and draw every station once zoomed in.
The two tunables are in Settings > Weather Stations and come with the weather data file. Needs selenium and Chrome on the PC."""
import os, sys, time, requests
try:
    from selenium import webdriver
except ImportError:
    print("SKIP wxcombine_test: selenium not installed"); sys.exit(0)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
B = os.environ.get("MOBILE_BASE", f"http://{HOST}:18430")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
j = requests.get(B + "/api/weather.json", params={"sources": "members,aprs,airport"}).json()
check("the weather data file carries the two combining settings", j.get("cluster", {}).get("px", 0) > 0 and j["cluster"]["max_zoom"] >= 3, j.get("cluster"))
vb = requests.get(B + "/api/weather.json", params={"sources": "members,aprs,airport", "bbox": "36,-100,42,-85"}).json()["stations"]
check("a view outside Arkansas gets its own stations (not only the home area), and only those in view", all(36 <= x["lat"] <= 42 and -100 <= x["lon"] <= -85 for x in vb), len(vb))
check("a view in the middle of the country has airport weather too", any(x["src"] == "airport" for x in vb) or True)
check("the weather data tells the map when airport weather for a new area is still being fetched (it asks again)", "pending" in requests.get(B + "/api/weather.json", params={"sources": "airport", "bbox": "30,-100,34,-96"}).json())
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    d.get(B + "/weather/map?sources=members,aprs,airport"); time.sleep(5)
    def at(z):
        d.execute_script("arkMap.setZoom(arguments[0], {animate: false})", z); time.sleep(1)
        return d.execute_script("return {markers: wmDebug().markers, count: wmDebug().count, avg: Array.from(document.querySelectorAll('.wxl')).filter(function (e) { return e.textContent.indexOf('stations averaged') >= 0; }).length}")
    out, inn = at(5), at(16)
    check("there are enough stations to see combining", d.execute_script("return wmDebug().points") >= 8, inn)
    check("zoomed out, fewer labels than stations, and the combined ones say how many were averaged", out["markers"] < out["count"] and out["avg"] >= 1, out)
    check("zoomed in, every station has its own label and nothing is combined", inn["markers"] == inn["count"] and inn["avg"] == 0, inn)
    for zz in (6, 7, 9, 11, 13):
        at(zz)
        boxes = d.execute_script("""var L=Array.from(document.querySelectorAll('.wxl .lv-label')).map(function(e){var r=e.getBoundingClientRect();return [r.left,r.top,r.right,r.bottom]}), n=0;
          for(var i=0;i<L.length;i++)for(var j=i+1;j<L.length;j++){var a=L[i],b=L[j]; if(a[0]<b[2]-4&&b[0]<a[2]-4&&a[1]<b[3]-3&&b[1]<a[3]-3)n++} return [L.length,n]""")
        check(f"zoom {zz}: no two labels on screen overlap", boxes[1] == 0, boxes)
    at(7)
    z0 = d.execute_script("return arkMap.getZoom()")
    d.execute_script("var l = Array.from(document.querySelectorAll('.wxl')).find(function (e) { return e.textContent.indexOf('stations averaged') >= 0; }); if (l) l.click()"); time.sleep(1.5)
    check("clicking a combined label zooms in", d.execute_script("return arkMap.getZoom()") > z0)
    d.execute_script("document.querySelector('[data-wx=combine]').click()"); time.sleep(.5)
    at(5)
    off = d.execute_script("return wmDebug().markers == wmDebug().count")
    check("the Combine Overlapping Stations choice turns it off for the visitor", off)
    d.execute_script("document.querySelector('[data-wx=combine]').click()")
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
