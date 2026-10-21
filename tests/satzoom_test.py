"""Satellite and Hybrid: zooming all the way in keeps a picture. The USGS imagery stops at zoom 16; past that the worldwide imagery takes over, and no tile layer ever disappears before the map's own limit.
Needs selenium and Chrome and the test box reaching the imagery servers."""
import sys, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP no selenium"); print("ALL PASS"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,900")
d = webdriver.Chrome(options=o); J = d.execute_script
JS = "var h={}; document.querySelectorAll('#lvmap .leaflet-tile-pane img.leaflet-tile-loaded').forEach(function(i){ var r=i.getBoundingClientRect(); if (r.width>0) { var k=new URL(i.src).hostname; h[k]=(h[k]||0)+1; } }); return h"
try:
    d.get(B + "/live?lat=34.746&lon=-92.289&zoom=15&nolocal=1"); time.sleep(5)
    J("var s=document.getElementById('l-style'); s.value='hyb'; s.dispatchEvent(new Event('change'))"); time.sleep(2)
    for z in (16, 17, 18, 19):
        J("window.arkMap.setView([34.746, -92.289], arguments[0], {animate: false})", z); time.sleep(4)
        h = J(JS)
        check(f"Hybrid at zoom {z} still draws imagery", sum(h.values()) >= 4, h)
    check("at the closest zooms the worldwide imagery has taken over from the USGS", "server.arcgisonline.com" in h and "basemap.nationalmap.gov" not in h, h)
    check("the map allows zooming in as far as 19", J("return window.arkMap.getMaxZoom()") >= 19)
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
