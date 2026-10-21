"""Audit (not a test): which parts of the one map engine each map page has, side by side, and where they differ."""
import os, sys, time, json
from selenium import webdriver
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); B = os.environ.get("MOBILE_BASE", f"http://{HOST}:18430")
PAGES = ["/live", "/weather/map", "/sondes/map", "/counties/", "/repeaters/map", "/aprs/map", "/coverage"]
IDS = ["lv", "lvmap", "lv-left", "lv-tools", "lv-toolsbody", "lv-filtbtn", "lv-legbtn", "lv-wxbtn", "lv-fold", "lv-list", "lv-hide", "lv-listhide", "lv-card", "c-min", "c-follow", "c-close", "c-page", "lv-chips", "lv-count", "lv-reset", "lv-sharelink", "l-style", "l-lscale", "l-lbg", "my-loc", "l-county", "dr-on", "lv-drive", "lv-maptype", "q", "lv-altbar", "lv-legend", "lv-layers"]
JS = """var ids=arguments[0], o={}; ids.forEach(function(i){o[i]=!!document.getElementById(i)});
o._zoomctl=!!document.querySelector('.leaflet-control-zoom'); o._scale=!!document.querySelector('.leaflet-control-scale'); o._attr=!!document.querySelector('.leaflet-control-attribution');
o._maptype=(function(){var s=document.getElementById('lv-maptype'); return s?Array.from(s.options).map(function(x){return x.text}).join('|'):''})();
o._arkmap=!!window.arkMap; o._viewurl=!!window.arkViewURL; o._saved=!!document.querySelector('#lv-views, .mv-btn, [data-views]'); o._search=!!document.querySelector('#ms-results, .ms-box, #q');
o._sections=Array.from(document.querySelectorAll('details.fsec')).map(function(d){return d.getAttribute('data-k')}).join(',');
o._style=(function(){var s=document.getElementById('l-style'); return s?Array.from(s.options).map(function(x){return x.value}).join(','):''})();
o._keys=!!document.querySelector('[data-keys], .kbd-help');
return o"""
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o); res = {}
try:
    for p in PAGES:
        d.get(B + p); time.sleep(4); res[p] = d.execute_script(JS, IDS)
finally:
    d.quit()
keys = list(res[PAGES[0]].keys())
print("%-16s" % "feature" + " ".join("%-9s" % p.strip("/")[:9] for p in PAGES))
for k in keys:
    vals = [res[p][k] for p in PAGES]
    if len({json.dumps(v) for v in vals}) > 1:
        print("%-16s" % k + " ".join("%-9s" % (("yes" if v else "NO") if isinstance(v, bool) else str(v)[:9]) for v in vals))
print("\nsections by page:")
for p in PAGES: print(" ", p, res[p]["_sections"])
print("map type list:", {res[p]["_maptype"] for p in PAGES})
print("styles:", {p: res[p]["_style"] for p in PAGES})
