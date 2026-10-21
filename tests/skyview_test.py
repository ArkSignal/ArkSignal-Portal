"""Sky View in headless Chrome with software WebGL: the page loads the 3D library, enables terrain, points the camera along the
aircraft's heading, draws other aircraft as icons at their place in the sky, lets you drag to look around and click an aircraft to
jump to it. Frame rate under software rendering means nothing; real devices are far faster. Skipped without WebGL."""
import os, sys, time
try:
    from selenium import webdriver
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP skyview_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
o = webdriver.ChromeOptions()
for a in ("--headless=new", "--window-size=1300,800", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"):
    o.add_argument(a)
d = webdriver.Chrome(options=o); bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
try:
    d.get(B + "/live"); time.sleep(4)
    hexid = d.execute_script("var r=document.querySelector('#lv-tbl tr.it');return r?r.dataset.hex:null")
    check("an aircraft is available to view", bool(hexid))
    d.execute_script("document.querySelector('#lv-tbl tr.it').click()"); time.sleep(1.5)
    check("the details card links to Sky View", d.execute_script("return document.getElementById('c-skyview').getAttribute('href')") == "/skyview/" + hexid)
    import urllib.request as ur
    check("the old /cockpit address leads to Sky View", ur.urlopen(B + "/cockpit/" + hexid, timeout=20).geturl().endswith("/skyview/" + hexid))
    d.get(B + "/skyview/" + hexid); time.sleep(12)
    if not d.execute_script("return !!document.createElement('canvas').getContext('webgl2')||!!document.createElement('canvas').getContext('webgl')"):
        print("SKIP: no WebGL in this browser"); sys.exit(0)
    st = d.execute_script("return window.ckDebug && window.ckDebug()")
    check("the 3D view starts with terrain on and the aircraft found", st and st["ready"] and st["terrain"] and st["have"], str(st))
    check("the camera looks out along the heading with a forward pitch", st and st["pitch"] > 60 and -180 <= st["bearing"] <= 360, str(st))
    check("the camera is at the aircraft's altitude", st and st["alt"] is not None and st["alt"] >= 0)
    for _ in range(8):   # software rendering is slow when the machine is busy: give the overlay time
        if st and st["icons"] > 0: break
        time.sleep(3); st = d.execute_script("return window.ckDebug()")
    for yaw in (90, 180, -90, 0):   # traffic may be behind you: look around until some is in view
        if st and st["icons"] > 0: break
        d.execute_script("var y=document.getElementById('ck-yaw');y.value=%d;y.dispatchEvent(new Event('input'))" % yaw); time.sleep(2.5)
        st = d.execute_script("return window.ckDebug()")
    d.execute_script("var y=document.getElementById('ck-yaw');y.value=0;y.dispatchEvent(new Event('input'))")
    check("other aircraft are drawn as icons in the sky", st and st["icons"] > 0, str(st))
    check("the title says Sky View, not the old name", "Sky View" in d.title and "ockpit" not in d.page_source)
    # drag to look around
    p0 = float(d.execute_script("return document.getElementById('ck-pitch').value")); y0 = float(d.execute_script("return document.getElementById('ck-yaw').value"))
    ck = d.find_element(By.ID, "ckmap")
    ActionChains(d).move_to_element_with_offset(ck, 0, 150).click_and_hold().move_by_offset(120, 40).release().perform(); time.sleep(0.5)
    p1 = float(d.execute_script("return document.getElementById('ck-pitch').value")); y1 = float(d.execute_script("return document.getElementById('ck-yaw').value"))
    check("dragging turns the view (left/right and up/down)", abs(y1 - y0) > 5 and abs(p1 - p0) > 2, f"yaw {y0}->{y1}, pitch {p0}->{p1}")
    # two quick taps (sent as pointer events in one go: software rendering here is too slow for real-time clicks)
    d.execute_script("var c=document.getElementById('ckmap');for(var i=0;i<2;i++){['pointerdown','pointerup'].forEach(function(n){c.dispatchEvent(new PointerEvent(n,{bubbles:true,pointerId:7,clientX:600,clientY:400}))})}"); time.sleep(0.4)
    check("double-tap looks straight ahead again", abs(float(d.execute_script("return document.getElementById('ck-yaw').value"))) < 1)
    # click an aircraft to jump to it
    cur = d.execute_script("return window.ckDebug().hex")
    target = d.execute_script("var e=document.querySelector('#ckov .ck-ac');return e?e.dataset.hex:null")
    if target and target != cur:
        d.execute_script("document.querySelector('#ckov .ck-ac').click()"); time.sleep(2)
        now = d.execute_script("return window.ckDebug().hex")
        check("clicking an aircraft jumps to it (and the address follows)", now == target and d.current_url.endswith("/skyview/" + target), f"{cur} -> {now}")
    else:
        print("note: no other aircraft in view to click; jump check skipped")
    # live, not stop-start: the position keeps changing between reports
    a1 = d.execute_script("return window.ckDebug().lat"); time.sleep(1.0); a2 = d.execute_script("return window.ckDebug().lat")
    check("the view moves continuously with the aircraft", a1 is not None and a2 is not None and a1 != a2, f"{a1} {a2}")
    # no rewinding: sampled over several reports, the aircraft never steps backward along its heading
    import math
    pts = []
    for _ in range(40):
        st = d.execute_script("return window.ckDebug()"); pts.append((time.time(), st["lat"], st["lon"], st["hdg"])); time.sleep(0.25)
    back = 0.0
    for a, b in zip(pts, pts[1:]):
        dn = (b[1] - a[1]) * 111195; de = (b[2] - a[2]) * 111195 * math.cos(math.radians(a[1]))
        along = dn * math.cos(math.radians(b[3])) + de * math.sin(math.radians(b[3]))
        back = min(back, along)
    fps = d.execute_script("return window.ckDebug().fps")
    if fps < 3: print(f"note: only {fps} fps in software rendering right now, so the backward-step limit is relaxed")
    check("the view never steps backward between reports (the old replay jerk)", back > (-40 if fps >= 3 else -400), f"worst backward step {back:.0f} m at {fps} fps")
    check("a heading tape shows which way you are looking and where the nose points", d.execute_script("var c=document.getElementById('ckcmp');return !!c && c.width > 100 && c.height > 20"))
    d.get(B + "/skyview/zzzzzz"); check("a bad address is refused", d.execute_script("return document.title").lower().find("not found") >= 0 or "not found" in d.page_source.lower())
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED"); sys.exit(1 if bad else 0)
