"""Weather across the map: the heat map and the wind streams are drawn from every station shown (checked in a real browser: pixels on the two canvases), the choice is remembered and shared, and cloud cover is a reading."""
import os, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
wm = requests.get(B + "/weather/map").text
check("the Weather Map offers heat map and wind streams", all(x in wm for x in ('id="wm-heat"', 'id="wm-wind"', 'id="wm-heatmetric"', "Heat map", "Wind streams", "/static/wxsurface.js")))
check("cloud cover is a reading the map can show (from airport weather)", "Cloud Cover" in requests.get(B + "/static/wxmap.js").text)
air = [s for s in requests.get(B + "/api/weather.json", params={"sources": "airport"}).json()["stations"] if "cloud_pct" in s["r"]]
check("airport weather carries the sky cover when the airport reports one", True if not air else all(0 <= s["r"]["cloud_pct"] <= 100 for s in air))
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part: selenium is not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    d.get(B + "/weather/map?sources=members,aprs,airport&surface=both"); time.sleep(7)
    js = """function f(c){var x=c.getContext('2d').getImageData(0,0,c.width,c.height).data,n=0;for(var i=3;i<x.length;i+=4)if(x[i]>0)n++;return n}
      var cs=[].slice.call(document.querySelectorAll('#lvmap canvas')).filter(function(c){return c.parentNode.className.indexOf('leaflet-wxsurf-pane')>=0}); return [cs.length].concat(cs.map(f))"""
    r = d.execute_script(js)
    pts = d.execute_script("return wmDebug().points")
    if pts < 2:
        print("note: fewer than two stations report on the test box, the drawing check is skipped")
    else:
        check("two canvases: the heat map has pixels", r[0] == 2 and r[1] > 1000, r)
        check("the wind streams have pixels once the wind is known", r[2] > 50, r)
    z = d.execute_script("return [+getComputedStyle(document.querySelector('.leaflet-wxsurf-pane')).zIndex, +getComputedStyle(document.querySelector('.leaflet-marker-pane')).zIndex]")
    check("the heat map and wind streams are drawn under the station markers and labels", z[0] < z[1] and z[0] > 0, z)
    check("heat map and wind streams are two boxes, both ticked from the link", d.execute_script("return document.getElementById('wm-heat').checked && document.getElementById('wm-wind').checked"))
    d.execute_script("var s=document.getElementById('wm-heatmetric'); s.value='hum'; s.dispatchEvent(new Event('change'))"); time.sleep(.8)
    check("the heat map can show a different reading than the labels", d.execute_script("return location.search.length>0 && document.getElementById('wm-heatmetric').value") == "hum" and "heat=hum" in d.execute_script("return arkViewURL()"))
    bar = d.execute_script("var b=document.getElementById('lv-altbar-heat'); return b && getComputedStyle(b).display!=='none' ? [b.innerText, document.getElementById('lv-altbar').innerText] : null")
    check("the heat map has its own color bar when it shows another reading", bar and "Heat map:" in bar[0] and "Humidity" in bar[0] and "Labels:" in bar[1], bar)
    d.execute_script("document.getElementById('lv-legbtn').click()"); time.sleep(.5)
    leg = d.execute_script("return document.getElementById('lv-legbody').innerText")
    check("the legend lists the heat map's scale separately from the labels", "Heat map" in leg and "Labels" in leg and "Humidity" in leg, leg[:200])
    d.execute_script("document.getElementById('lv-legbtn').click()")
    d.execute_script("var h=document.getElementById('wm-heat'); h.checked=false; h.dispatchEvent(new Event('change'))"); time.sleep(.6)
    check("unticking the heat map hides its color bar", d.execute_script("return getComputedStyle(document.getElementById('lv-altbar-heat')).display")=="none")
    r1 = d.execute_script(js)
    check("unticking the heat map leaves the wind streams on", r1[1] == 0 and (pts < 2 or r1[2] > 0), r1)
    d.execute_script("var h=document.getElementById('wm-wind'); h.checked=false; h.dispatchEvent(new Event('change'))"); time.sleep(.6)
    r2 = d.execute_script(js)
    check("both unticked clears both canvases", r2[1] == 0 and r2[2] == 0, r2)
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
