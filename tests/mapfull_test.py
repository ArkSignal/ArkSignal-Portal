"""Full screen on every map: the button is on every map page (and after an in-place map switch), it fills the screen with the map and Esc or the button leaves it, wide and phone width.
Needs selenium and Chrome on the PC."""
import os, sys, time
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
except ImportError:
    print("SKIP mapfull_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
PAGES = ["/live", "/weather/map", "/sondes/map", "/counties/", "/repeaters/map", "/aprs/map"]
def driver(phone):
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new")
    if phone:
        o.add_experimental_option("mobileEmulation", {"deviceMetrics": {"width": 375, "height": 812, "pixelRatio": 2}, "userAgent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36"})
    else:
        o.add_argument("--window-size=1400,900")
    return webdriver.Chrome(options=o)
JS = "var lv=document.getElementById('lv'),r=lv.getBoundingClientRect();return {full: document.fullscreenElement===lv||lv.classList.contains('lv-full'), w:r.width, h:r.height, vw:innerWidth, vh:innerHeight}"
for phone in (False, True):
    d = driver(phone); tag = "phone" if phone else "desktop"
    try:
        for page in PAGES:
            d.get(B + page); time.sleep(4)
            btns = d.find_elements(By.ID, "lv-fullbtn")
            check(f"{tag} {page}: the full screen button is on the map", len(btns) == 1)
            if not btns: continue
            time.sleep(1.5)
            pos = d.execute_script("""var b=document.getElementById('lv-fullbtn'),r=b.getBoundingClientRect(),l=document.querySelector('#lv .lv-locate'),q=l&&l.getBoundingClientRect(),t=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return {onTop: b.contains(t), above: !q||r.bottom<=q.top+1, sameCol: !q||Math.abs(r.right-q.right)<3, w:r.width}""")
            check(f"{tag} {page}: the button is visible (nothing over it) and sits above the location target", pos["onTop"] and pos["above"] and pos["sameCol"], pos)
            vis = d.execute_script("""var b=document.getElementById('lv-fullbtn'),s=getComputedStyle(b),svg=b.querySelector('svg'),sr=svg&&svg.getBoundingClientRect();return {bg:s.backgroundColor,op:s.opacity,svg:!!svg,w:sr&&sr.width,col:s.color}""")
            check(f"{tag} {page}: the button is visible without hovering (its own background and an icon)", vis["svg"] and vis["w"] > 10 and vis["bg"] not in ("rgba(0, 0, 0, 0)", "transparent") and float(vis["op"]) == 1, vis)
            btns[0].click(); time.sleep(1.2)
            i = d.execute_script(JS)
            check(f"{tag} {page}: it fills the screen", i["full"] and i["w"] >= i["vw"] - 2 and i["h"] >= i["vh"] - 2, i)
            btns[0].click(); time.sleep(1)
            i = d.execute_script(JS)
            check(f"{tag} {page}: the button leaves it", not i["full"] and i["h"] < i["vh"], i)
            btns[0].click(); time.sleep(1)
            d.execute_script("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))"); time.sleep(.6)
            if d.execute_script("return !!document.fullscreenElement"): d.execute_script("document.exitFullscreen()"); time.sleep(.6)
            i = d.execute_script(JS)
            check(f"{tag} {page}: Esc leaves it", not i["full"], i)
        if not phone:
            d.get(B + "/live"); time.sleep(4)
            d.execute_script("var s=document.getElementById('lv-maptype'); s.value=[].slice.call(s.options).filter(function(o){return o.value.indexOf('/sondes/map')>=0})[0].value; s.dispatchEvent(new Event('change'))"); time.sleep(5)
            check("after switching the map in place the button is still there, once", len(d.find_elements(By.ID, "lv-fullbtn")) == 1)
    finally:
        d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
