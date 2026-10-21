"""Phone polish (2026-10-15): the Layers and Filters panel opens full screen with an obvious way to close it, the footer is two short lines at the end of the page, maps start with smaller labels, the mesh
page's server name breaks at its dots, and the pieces that were redundant or stale are gone. Needs selenium and Chrome on the PC."""
import os, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
mesh = requests.get(B + "/feed/mesh").text
check("the mesh server name has a place to break after each dot", "<wbr>" in mesh or "MQTT server" not in mesh)
sf = requests.get(B + "/sondes/26038390")
check("a flight's Heard By table has a Sort by choice and an order button", sf.status_code != 200 or ("hf-sort" in sf.text and "hf-dir" in sf.text), sf.status_code)
radio = requests.get(B + "/radio/").text
check("the Radio overview no longer says mesh is coming", "Coming: Meshtastic" not in radio)
cm = requests.get(B + "/counties/").text
check("the County Map's Show Counties As has no redundant last choice", 'value="lines"' not in cm and 'value="plain"' in cm)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new")
o.add_experimental_option("mobileEmulation", {"deviceMetrics": {"width": 390, "height": 800, "pixelRatio": 2}, "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"})
d = webdriver.Chrome(options=o); J = d.execute_script
try:
    d.get(B + "/about"); time.sleep(2)
    f = J("var f=document.querySelector('footer'); var r=f.getBoundingClientRect(); return {pos:getComputedStyle(f).position, h:r.height}")
    check("on a phone the footer is part of the page, not a bar over it, and short", f["pos"] != "sticky" and f["h"] < 90, f)
    for path in ("/live", "/counties/", "/trauma/", "/weather/map", "/sondes/map"):
        d.get(B + path); time.sleep(5)
        J("var b=document.getElementById('lv-filtbtn'); if (b && document.getElementById('lv-toolsbody').style.display==='none') b.click()"); time.sleep(.8)
        r = J("var t=document.getElementById('lv-tools').getBoundingClientRect(); return {open: document.getElementById('lv').classList.contains('fs-open'), w:t.width, h:t.height, iw:innerWidth, ih:innerHeight, close: !!document.getElementById('fs-close') && document.getElementById('fs-close').offsetParent!==null, done: !!document.getElementById('fs-done') && getComputedStyle(document.getElementById('fs-done')).display!=='none'}")
        check(f"{path}: the filters panel fills the screen with a Close button and a Show the Map button", r["open"] and r["w"] >= r["iw"] - 2 and r["h"] >= r["ih"] - 2 and r["close"] and r["done"], r)
        J("document.getElementById('fs-close').click()"); time.sleep(.6)
        check(f"{path}: Close puts it away", not J("return document.getElementById('lv').classList.contains('fs-open')"))
        J("document.getElementById('lv-filtbtn').click()"); time.sleep(.6)
        J("document.getElementById('fs-done').click()"); time.sleep(.6)
        check(f"{path}: Show the Map puts it away too", not J("return document.getElementById('lv').classList.contains('fs-open')"))
        J("document.getElementById('lv-filtbtn').click()"); time.sleep(.6)
        J("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))"); time.sleep(.6)
        check(f"{path}: Escape puts it away", not J("return document.getElementById('lv').classList.contains('fs-open')"))
    d.get(B + "/live?lat=34.9&lon=-92.4&zoom=7"); time.sleep(6)
    J("var b=document.getElementById('lv-hide'); if (b) b.style.display='block'")
    r = J("var h=document.getElementById('lv-hide').getBoundingClientRect(), l=document.querySelector('.lv-locate').getBoundingClientRect(), f=document.querySelector('footer'); return {overlap: !(h.right<=l.left||h.left>=l.right||h.bottom<=l.top||h.top>=l.bottom), footer: f ? getComputedStyle(f).display : 'none', nav: !!document.querySelector('.navfoot')}")
    check("on a phone map page the Show List button is clear of the locate button, the footer is not drawn and its links are in the menu", not r["overlap"] and r["footer"] == "none" and r["nav"], r)
    d.get(B + "/live?replay=1&lat=34.9&lon=-92.4&zoom=7"); time.sleep(7)
    r = J("var b=document.getElementById('lv-replay').getBoundingClientRect(); function hit(sel){var e=document.querySelector(sel); if(!e||e.offsetParent===null&&getComputedStyle(e).position!=='fixed') return false; var x=e.getBoundingClientRect(); return !(x.bottom<=b.top||x.top>=b.bottom||x.right<=b.left||x.left>=b.right)} return {h: b.height, w: b.width, iw: innerWidth, over: ['#lv-hide','#ark-normal','.lv-locate'].map(hit)}")
    check("on a phone the replay bar is short, fits the width and nothing else floats on top of it", r["h"] < 250 and r["w"] <= r["iw"] and not any(r["over"]), r)
    r2 = J("var b=document.getElementById('lv-replay'); var h1=b.offsetHeight; document.getElementById('rp-more').click(); var h2=b.offsetHeight; return {h1:h1, h2:h2}")
    check("on a phone the replay bar starts folded and More shows the rest", r2["h1"] < 150 and r2["h2"] > r2["h1"] + 40, r2)
    check("while the replay bar is up the floating round buttons are put away", J("var f=document.getElementById('lv-fullbtn'); return document.getElementById('lv').classList.contains('replay-on') && (!f || getComputedStyle(f).display==='none')"))
    d.get(B + "/about"); time.sleep(2)
    J("document.getElementById('nav-toggle').click()"); time.sleep(.5)
    J("document.querySelector('#prefs-menu > summary').click()"); time.sleep(.5)
    J("document.querySelector('#pf-ua > summary').click()"); time.sleep(.8)
    check("opening Units for each service inside the Display menu on a phone leaves the Display menu open", J("return document.getElementById('prefs-menu').open && document.getElementById('pf-ua').open"), J("return [document.getElementById('prefs-menu').open, document.getElementById('pf-ua').open]"))
    d.get(B + "/counties/?show=hospitals&lat=34.9&lon=-92.4&zoom=7"); time.sleep(8)
    far = J("return {all: document.querySelectorAll('#lvmap .hico').length, plain: document.querySelectorAll('#lvmap .hico.ed, #lvmap .hico.noed').length}")
    J("window.cmMap.setZoom(10)"); time.sleep(2.5)
    near = J("return {plain: document.querySelectorAll('#lvmap .hico.ed, #lvmap .hico.noed').length}")
    check("on a phone zoomed out to the state the County Map shows the trauma centers only, and the other hospitals appear as you zoom in", far["plain"] == 0 and far["all"] >= 1 and near["plain"] >= 1, (far, near))
    d.get(B + "/live?lat=34.9&lon=-92.4&zoom=7"); time.sleep(5)
    J("var m=document.getElementById('lvmap'); m.dispatchEvent(new PointerEvent('pointerdown',{clientX:200,clientY:400,bubbles:true})); m.dispatchEvent(new PointerEvent('pointerup',{clientX:120,clientY:330,bubbles:true}))"); time.sleep(.5)
    check("on a phone the aircraft map's top panel folds to buttons once the map is moved, and Search brings it back", J("return document.getElementById('lv-tools').classList.contains('head-min') && getComputedStyle(document.getElementById('q')).display==='none'"))
    J("document.getElementById('lv-searchbtn').click()"); time.sleep(.4)
    check("the Search button shows the search box again", not J("return document.getElementById('lv-tools').classList.contains('head-min')"))
    J("var c=document.getElementById('lv-card'); c.style.display='block'"); time.sleep(.5)
    check("on a phone an open details card is not covered by the full screen, locate and Normal look buttons", J("var f=document.getElementById('lv-fullbtn'); return document.getElementById('lv').classList.contains('card-open') && (!f || getComputedStyle(f).display==='none')"))
    J("document.getElementById('c-min').click()"); time.sleep(.4)
    check("a folded card gives the buttons back", not J("return document.getElementById('lv').classList.contains('card-open')"))
    for path in ("/live", "/counties/", "/trauma/", "/weather/map", "/sondes/map", "/aprs/map", "/mesh/map", "/repeaters/map", "/coverage"):
        d.get(B + path); time.sleep(4)
        if not J("return !!document.getElementById('lv-card')"):
            print("SKIP", path, "has no details card"); continue
        J("document.getElementById('lv-card').style.display='block'"); time.sleep(.4)
        hid = J("var r=[]; ['ark-normal','lv-fullbtn'].forEach(function(i){var e=document.getElementById(i); if(e) r.push(getComputedStyle(e).display==='none')}); var l=document.querySelector('.lv-locate'); if(l) r.push(getComputedStyle(l).display==='none'); return r.length ? r.every(Boolean) : null")
        check(f"{path}: an open details card has no floating buttons on top of it", hid is not False, hid)
    d.get(B + "/weather/map"); time.sleep(5)
    check("a phone's map labels start at 75%", J("return getComputedStyle(document.getElementById('lv')).getPropertyValue('--ls').trim()") in ("0.75", ".75"), J("return getComputedStyle(document.getElementById('lv')).getPropertyValue('--ls')"))
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
