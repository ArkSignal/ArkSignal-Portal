"""The portal's own Replay and Heatmap: the history API (raw half hours, heat grid) and, in a real browser, the Replay plays the hub's files onto the aircraft map and the heat map draws pixels."""
import os, sys, time, requests
from datetime import datetime, timedelta, timezone
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); B = os.environ.get("COUNTY_BASE", f"http://{HOST}:18431")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
day = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
idx = next((i for i in range(47, -1, -1) if requests.get(f"{B}/api/history/chunk/{day}/{i}.bin").status_code == 200), None)
check("the hub's half hour files are passed on", idx is not None)
if idx is None:
    print("SKIP the rest: the hub has no history for yesterday"); sys.exit(1)
r = requests.get(f"{B}/api/history/chunk/{day}/{idx}.bin")
check("a half hour is a whole number of 16-byte records with slice headers (the magic number)", len(r.content) % 16 == 0 and bytes.fromhex("9d7c7f0e") in r.content, len(r.content))
check("a finished half hour can be kept by the browser", "max-age" in r.headers.get("Cache-Control", ""), r.headers.get("Cache-Control"))
check("a half hour that does not exist is a 404, a bad one a 404, a bad date a 400", requests.get(f"{B}/api/history/chunk/{(datetime.now(timezone.utc) - timedelta(days=200)).date().isoformat()}/3.bin").status_code == 404 and requests.get(f"{B}/api/history/chunk/{day}/99.bin").status_code == 404 and requests.get(f"{B}/api/history/chunk/nope/3.bin").status_code == 400)
h = requests.get(f"{B}/api/history/heat.json", params={"day": day, "z": 8}).json()
check("the heat grid has squares with counts", h["cells"] and all(len(c) == 3 and c[2] > 0 for c in h["cells"]) and h["found"] >= 1, {k: h[k] for k in h if k != "cells"})
h2 = requests.get(f"{B}/api/history/heat.json", params={"day": day, "z": 8, "amin": 30000}).json()
check("an altitude floor leaves fewer positions", sum(c[2] for c in h2["cells"]) < sum(c[2] for c in h["cells"]), (sum(c[2] for c in h2["cells"]), sum(c[2] for c in h["cells"])))
h3 = requests.get(f"{B}/api/history/heat.json", params={"day": day, "z": 8, "south": 34, "north": 35, "west": -93, "east": -92}).json()
check("a view box limits the squares", all(33.9 < c[0] < 35.1 and -93.1 < c[1] < -91.9 for c in h3["cells"]) and len(h3["cells"]) <= len(h["cells"]))
rd = requests.get(B + "/view/heatmap", allow_redirects=False); rr = requests.get(B + "/view/replay", allow_redirects=False)
check("the menu's Heatmap and Replay open the portal's own on the live map", rd.status_code in (301, 302) and rd.headers["Location"].startswith("/live?heat=1") and rr.status_code in (301, 302) and rr.headers["Location"].startswith("/live?replay=1"), (rd.headers.get("Location"), rr.headers.get("Location")))
check("?classic=1 still shows the embedded tar1090 page", requests.get(B + "/view/heatmap?classic=1", allow_redirects=False).status_code == 200)
check("the live page carries the Replay bar and the heat map options", all(x in requests.get(B + "/live").text for x in ('id="lv-replay"', 'id="h-heat"', 'id="h-replay"', "/static/replay.js", "/static/heatlayer.js")))
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part: selenium is not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900"); o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
d = webdriver.Chrome(options=o)
try:
    # the moment: a minute into the busiest half hour found
    t0 = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()) + idx * 1800 + 70
    d.get(f"{B}/live?replay={t0}&replaySpeed=5&lat=34.8&lon=-92.3&zoom=7"); time.sleep(7)
    r = d.execute_script("return {bar: getComputedStyle(document.getElementById('lv-replay')).display, status: document.getElementById('lv-status').textContent, rows: document.querySelectorAll('#lv-tbl tbody tr').length, time: document.getElementById('rp-time').textContent, state: document.getElementById('rp-state').textContent, play: document.getElementById('rp-play').textContent}")
    check("the Replay bar opens at the moment in the link and plays", r["bar"] != "none" and r["status"].startswith("Replay") and r["rows"] >= 0 and "UTC" in r["time"] and r["play"] == "Pause", r)
    check("aircraft from the history are listed", r["rows"] > 0, r)
    t1 = d.execute_script("return document.getElementById('rp-time').textContent"); time.sleep(4)
    t2 = d.execute_script("return document.getElementById('rp-time').textContent")
    check("time moves on while playing", t1 != t2, (t1, t2))
    d.execute_script("document.getElementById('rp-play').click()"); time.sleep(.5)
    ta = d.execute_script("return document.getElementById('rp-time').textContent"); time.sleep(2)
    tb = d.execute_script("return document.getElementById('rp-time').textContent")
    check("Pause stops it", ta == tb and d.execute_script("return document.getElementById('rp-play').textContent") == "Play", (ta, tb))
    inside = d.execute_script("var b=document.getElementById('lv-replay').getBoundingClientRect(), m=document.getElementById('lv').getBoundingClientRect(); return b.bottom <= m.bottom + 1 && b.top >= m.top && b.bottom <= innerHeight + 1")
    check("the Replay bar sits on the map's bottom edge, on the screen", inside)
    d.execute_script("document.getElementById('rp-f5').click()"); time.sleep(3)
    tc = d.execute_script("return document.getElementById('rp-time').textContent")
    check("+5 min jumps forward", tc != tb, (tb, tc))
    url = d.execute_script("return arkViewURL()")
    check("the shared link carries the replay moment", "replay=" in url and "replaySpeed=" in url, url)
    d.execute_script("document.getElementById('rp-live').click()"); time.sleep(3)
    r = d.execute_script("return {bar: getComputedStyle(document.getElementById('lv-replay')).display, status: document.getElementById('lv-status').textContent}")
    check("Back to Live closes the bar and the live feed returns", r["bar"] == "none" and (r["status"].startswith("Live") or "offline" in r["status"]), r)
    # the heat map
    d.get(f"{B}/live?heat=1&heatHours=72&lat=34.8&lon=-92.3&zoom=7"); time.sleep(9)
    js = "var c=document.querySelector('.leaflet-heathist-pane canvas'); if(!c) return null; var x=c.getContext('2d').getImageData(0,0,c.width,c.height).data,n=0; for(var i=3;i<x.length;i+=4) if(x[i]>0) n++; return n"
    n = d.execute_script(js)
    check("the heat map draws pixels", n and n > 500, n)
    st = d.execute_script("return document.getElementById('h-status').textContent")
    check("the heat map says what it shows", "squares" in st, st)
    d.execute_script("var s=document.getElementById('f-max'); s.value='5000'; s.dispatchEvent(new Event('input'))"); time.sleep(6)
    n2 = d.execute_script(js)
    check("an altitude ceiling redraws it with less", n2 is not None and n2 < n, (n, n2))
    d.execute_script("document.getElementById('lv-legbtn').click()"); time.sleep(.6)
    check("the legend has the heat map's color bar", "Fewer flights" in d.execute_script("return document.getElementById('lv-legbody').innerText"))
    d.execute_script("var h=document.getElementById('h-heat'); h.checked=false; h.dispatchEvent(new Event('change'))"); time.sleep(1)
    check("unticking removes it", d.execute_script(js) in (0, None))
    d.execute_script("var h=document.getElementById('h-heat'); h.checked=true; h.dispatchEvent(new Event('change'))"); time.sleep(1.5)
    d.execute_script("var s=document.getElementById('lv-maptype'); s.value='/sondes/map'; s.dispatchEvent(new Event('change'))"); time.sleep(7)
    d.execute_script("var s=document.getElementById('lv-maptype'); s.value='/live'; s.dispatchEvent(new Event('change'))"); time.sleep(7)
    check("the heat map does not come back by itself after switching to another map and back", d.execute_script("var h=document.getElementById('h-heat'); return !!h && !h.checked"))
    errs = [l["message"][:200] for l in d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"]]
    check("no script errors", not errs, errs[:3])
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
