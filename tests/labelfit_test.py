"""One label engine (static/labelfit.js) on the APRS, Repeater and Radiosonde maps: labels appear and no two ever overlap, at several zooms; the label choice is a setting that works."""
import os, sys, time, requests
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); B = os.environ.get("COUNTY_BASE", f"http://{HOST}:18431")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
check("the engine script is served", "ArkLabels" in requests.get(B + "/static/labelfit.js").text)
for path in ("/aprs/map", "/repeaters/map", "/sondes/map", "/live", "/counties/"):
    check(path + " loads the shared label engine", "/static/labelfit.js" in requests.get(B + path).text)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part: selenium is not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900"); o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
d = webdriver.Chrome(options=o)
JS = """var r=[].slice.call(document.querySelectorAll('#lvmap .lv-label')).map(function(e){var b=e.getBoundingClientRect();return [b.left,b.top,b.right,b.bottom]}); var bad=0;
 for(var i=0;i<r.length;i++)for(var j=i+1;j<r.length;j++){var a=r[i],c=r[j]; if(a[0]<c[2]-1&&c[0]<a[2]-1&&a[1]<c[3]-1&&c[1]<a[3]-1)bad++}
 return {n:r.length,bad:bad}"""
try:
    for path, zooms, minn in (("/aprs/map", (8, 10, 12), 1), ("/repeaters/map", (9, 10, 12), 1), ("/sondes/map?hours=720", (6, 7, 9), 0), ("/live", (7, 9, 11), 1), ("/counties/", (7, 8, 9), 1)):
        d.get(B + path)
        if path == "/live": d.execute_script("localStorage.setItem('lv.labels', 'detail')"); d.get(B + path)   # aircraft labels are the visitor's choice: ask for the detailed ones
        time.sleep(6)
        total = 0
        for z in zooms:
            d.execute_script("var m=null; for (var k in window) { try { if (window[k] && window[k]._leaflet_id && window[k].setZoom) { m = window[k]; break; } } catch(e){} }; if (m) m.setZoom(arguments[0]);", z)
            time.sleep(1.5)
            r = d.execute_script(JS); total += r["n"]
            check(f"{path} zoom {z}: no two labels overlap ({r['n']} shown)", r["bad"] == 0, r)
        check(f"{path} draws labels at some zoom", total >= minn, total)
    errs = [l["message"][:160] for l in d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"]]
    check("no script errors", not errs, errs[:2])
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
