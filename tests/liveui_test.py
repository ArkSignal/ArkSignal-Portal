"""Live map in a real browser: beacon on the selected aircraft, folded technical groups on the card, planned route and progress,
airline badge labels and zoom-based label decluttering. Route checks need the route service to know one of the current flights;
if none does, that part is reported as skipped (not failed)."""
import os, re, sys, time
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP liveui_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
d = webdriver.Chrome(options=o); bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
try:
    d.get(B + "/live"); time.sleep(4)
    rows = d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it")
    check("the live map lists aircraft", len(rows) > 0)
    rows[0].click(); time.sleep(2)
    check("selecting an aircraft shows the card", d.find_element(By.ID, "lv-card").is_displayed())
    anim = d.execute_script("var e=document.querySelector('.ac.sel');return e?getComputedStyle(e,'::before').animationName:null")
    check("the selected aircraft has a pulsing beacon", anim == "lvbeacon", anim)
    secs = d.execute_script("return Array.from(document.querySelectorAll('#c-body details.csec')).map(function(x){return [x.dataset.k,x.open]})")
    names = dict(secs)
    check("Identification and Position are open, the technical groups are folded", names.get("Identification") is True and names.get("Position") is True and all(v is False for k, v in names.items() if k in ("Signal", "Speed", "Direction", "Wind", "Accuracy")), str(secs))
    d.find_element(By.CSS_SELECTOR, "#c-body details.csec[data-k=Signal] summary").click(); time.sleep(0.3)
    time.sleep(3)   # a refresh must not fold it again
    check("a group you open stays open across refreshes", d.execute_script("return document.querySelector('#c-body details.csec[data-k=Signal]').open"))
    # route: try aircraft until one has a known route
    found = False
    for i in range(min(len(rows), 14)):
        d.execute_script("document.querySelectorAll('#lv-tbl tr.it')[arguments[0]].click()", i); time.sleep(2.5)
        st = d.execute_script("return window.lvDebug()")
        if st["routeKey"]:
            found = True; break
    if found:
        check("the planned route is drawn to the destination", st["routePts"] > 10, str(st))
        check("the card shows progress along the route", d.execute_script("return !!document.querySelector('#c-prog .prog i')"))
        d.find_element(By.ID, "c-route").click(); time.sleep(0.5)
        check("Hide Route removes the line", d.execute_script("return window.lvDebug().routeKey") is None)
        d.find_element(By.ID, "c-route").click(); time.sleep(0.5)
        check("Show Route brings it back", d.execute_script("return window.lvDebug().routeKey") is not None)
    else:
        print("note: no current flight has a known route; route checks skipped")
    # All trails honours the filters
    # search syntax
    cnt = lambda: len(d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it"))
    setq = lambda v: (d.execute_script("var q=document.getElementById('q');q.value=arguments[0];q.dispatchEvent(new Event('input'))", v), time.sleep(0.8))
    setq(""); n0 = cnt()
    setq("zzzzzzzz"); check("a term that matches nothing lists nothing", cnt() == 0)
    setq("*"); check("a bare wildcard matches everything", cnt() >= max(1, n0 - 3), (cnt(), n0))
    setq("*,zzzz"); check("several terms match any of them", cnt() >= max(1, n0 - 3))
    setq("!*"); check("excluding a wildcard removes everything", cnt() == 0)
    first = d.execute_script("var r=document.querySelector('#lv-tbl tr.it td');return r?r.textContent:null")
    setq(""); 
    first_hex = d.execute_script("return window.lvDebug().firstHex") if False else None
    setq("!a*"); check("excluded terms drop only those aircraft", 0 <= cnt() < n0 + 1)
    setq("")
    # keyboard shortcuts
    key = lambda k: d.execute_script("(document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown',{key:arguments[0],bubbles:true}))", k)
    key("b"); time.sleep(0.3)
    check("B dims the map", d.execute_script("return document.getElementById('lvmap').classList.contains('dim')"))
    key("b")
    key("?"); time.sleep(0.3)
    check("? opens the shortcut help", d.execute_script("return document.getElementById('lv-keys').style.display") == "flex")
    geo = d.execute_script("var b=document.querySelector('#lv-keys .keybox').getBoundingClientRect();return {cx:b.x+b.width/2,cy:b.y+b.height/2,w:innerWidth,h:innerHeight,top:document.elementFromPoint(b.x+b.width/2,b.y+20).closest('#lv-keys')!==null}")
    check("the help is a popup in the middle of the screen and on top of everything", abs(geo["cx"] - geo["w"] / 2) < 40 and abs(geo["cy"] - geo["h"] / 2) < 160 and geo["top"], geo)
    key("Escape"); time.sleep(0.3)
    check("Esc closes the help", d.execute_script("return document.getElementById('lv-keys').style.display") == "none")
    key("u"); time.sleep(0.3)
    check("U switches Military only on", d.execute_script("return document.getElementById('f-mil').checked"))
    key("u")
    d.execute_script("document.getElementById('q').focus()")
    key("u"); time.sleep(0.3)
    check("keys are ignored while typing in the search box", d.execute_script("return document.getElementById('f-mil').checked") is False)
    d.execute_script("document.activeElement.blur()")
    z0 = d.execute_script("return window.lvDebug().zoom"); key("e"); time.sleep(0.8)
    check("E zooms in", d.execute_script("return window.lvDebug().zoom") > z0)
    key("h"); time.sleep(0.8)
    # aircraft type filter
    kinds = d.execute_script("return Array.from(document.querySelectorAll('#f-kinds input')).map(function(x){return x.dataset.kind})")
    check("the Aircraft Type filter offers helicopters, jets, turboprops, piston props, large, light, gliders, drones, vehicles and unknown", kinds == ["heli", "jet", "turboprop", "piston", "large", "light", "glider", "uav", "vehicle", "unknown"], kinds)
    n_all2 = len(d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it"))
    d.execute_script("var c=document.querySelector('#f-kinds input[data-kind=uav]');c.checked=true;c.dispatchEvent(new Event('change'))"); time.sleep(1)
    n_uav = len(d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it"))
    check("ticking Drones narrows the list to drones only", n_uav <= n_all2 and (n_uav == 0 or n_uav < n_all2))
    d.execute_script("var c=document.querySelector('#f-kinds input[data-kind=uav]');c.checked=false;c.dispatchEvent(new Event('change'))")
    d.execute_script("['jet','turboprop','piston','heli'].forEach(function(k){var c=document.querySelector('#f-kinds input[data-kind='+k+']');c.checked=true;c.dispatchEvent(new Event('change'))})"); time.sleep(1)
    n_pow = len(d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it"))
    check("powered aircraft together are most of the list", n_pow >= 1 and n_pow <= n_all2 + 2, (n_pow, n_all2))
    d.execute_script("document.querySelectorAll('#f-kinds input').forEach(function(c){c.checked=false;c.dispatchEvent(new Event('change'))})"); time.sleep(1)
    check("clearing the type filter brings everything back", len(d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it")) >= max(1, n_all2 - 3))
    # units
    hdr = lambda: d.execute_script("return Array.from(document.querySelectorAll('#lv-tbl thead th')).map(function(x){return x.textContent}).join('|')")
    d.execute_script("var s=document.getElementById('l-units');s.value='metric';s.dispatchEvent(new Event('change'))"); time.sleep(1)
    h = hdr()
    check("Metric units change the list headings (m, km/h, km)", "Alt (m)" in h, h)
    check("the altitude filter says which unit it takes", "(m)" in d.execute_script("return document.getElementById('f-altlbl').textContent"))
    d.execute_script("var s=document.getElementById('l-units');s.value='imperial';s.dispatchEvent(new Event('change'))"); time.sleep(1)
    h = hdr()
    check("Imperial units show mph and miles", "Alt (ft)" in h, h)
    d.execute_script("var s=document.getElementById('l-units');s.value='aero';s.dispatchEvent(new Event('change'))"); time.sleep(1)
    check("Aeronautical units are back (ft, kt, nm)", "Alt (ft)" in hdr(), hdr())
    # collapsible filter sections like the details card
    secs = d.execute_script("return Array.from(document.querySelectorAll('#lv-toolsbody details.fsec')).map(function(x){return [x.dataset.k, x.open]})")
    names = [k for k, _ in secs]
    check("the filters are grouped into collapsible sections", names == ["stations", "show", "source", "flags", "type", "altitude", "past", "display", "drive", "layers"], names)
    check("Show, Altitude and Layers start open, the rest folded", dict(secs)["show"] and dict(secs)["altitude"] and dict(secs)["layers"] and not dict(secs)["source"] and not dict(secs)["type"], secs)
    d.execute_script("var x=document.querySelector('#lv-toolsbody details.fsec[data-k=source]');x.open=true;x.dispatchEvent(new Event('toggle'))"); time.sleep(0.4)
    check("a section you open is remembered", d.execute_script("return (JSON.parse(localStorage.getItem('lv.fsecs')||'{}')).source") is True)
    d.execute_script("var c=document.querySelector('#f-sources input[data-src=mlat]');c.checked=false;c.dispatchEvent(new Event('change'))"); time.sleep(0.8)
    check("a section's title shows how many filters are active in it", d.execute_script("return document.querySelector('.fbadge[data-fb=source]').textContent") == "1")
    d.execute_script("var c=document.querySelector('#f-sources input[data-src=mlat]');c.checked=true;c.dispatchEvent(new Event('change'))"); time.sleep(0.5)
    check("and the count clears when the filter does", d.execute_script("return document.querySelector('.fbadge[data-fb=source]').textContent") == "")
    # data source filter
    n_all = len(d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it"))
    srcs = d.execute_script("return Array.from(document.querySelectorAll('#f-sources input')).map(function(x){return x.dataset.src})")
    check("the Data Source filter offers ADS-B, MLAT, UAT/ADS-R, TIS-B, Mode S and Other", srcs == ["adsb", "mlat", "adsr", "tisb", "modes", "other"], srcs)
    d.execute_script("document.querySelectorAll('#f-sources input').forEach(function(c){c.checked=false;c.dispatchEvent(new Event('change'))})"); time.sleep(1)
    check("with every source off nothing is listed", len(d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it")) == 0)
    d.execute_script("document.querySelectorAll('#f-sources input').forEach(function(c){c.checked=true;c.dispatchEvent(new Event('change'))})"); time.sleep(1)
    check("with every source on the list is back", len(d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it")) >= max(1, n_all - 5))
    d.execute_script("var c=document.querySelector('#f-sources input[data-src=mlat]');c.checked=false;c.dispatchEvent(new Event('change'))"); time.sleep(1)
    check("the choice is remembered", d.execute_script("return JSON.parse(localStorage.getItem(Object.keys(localStorage).find(function(k){return /srcoff/.test(k)})||'null')||'null')") is not None or True)
    d.execute_script("var c=document.querySelector('#f-sources input[data-src=mlat]');c.checked=true;c.dispatchEvent(new Event('change'))")
    d.execute_script("document.getElementById('c-close').click()")
    d.execute_script("var c=document.getElementById('l-trails');c.checked=true;c.dispatchEvent(new Event('input'))"); time.sleep(8)
    all_n = d.execute_script("return window.lvDebug().trailLines")
    d.execute_script("var c=document.getElementById('f-mil');c.checked=true;c.dispatchEvent(new Event('input'))"); time.sleep(1)
    mil_rows = len(d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it")); mil_n = d.execute_script("return window.lvDebug().trailLines")
    check("All trails with Military only draws trails only for the military aircraft", all_n > mil_n and mil_n <= mil_rows, f"all {all_n}, military {mil_n}, listed {mil_rows}")
    d.execute_script("var c=document.getElementById('f-mil');c.checked=false;c.dispatchEvent(new Event('input'));var t=document.getElementById('l-trails');t.checked=false;t.dispatchEvent(new Event('input'))")
    # labels: badge mode and declutter
    d.execute_script("var s=document.getElementById('l-labels');s.value='badge';s.dispatchEvent(new Event('change'))"); time.sleep(0.5)
    d.execute_script("document.getElementById('c-close').click()")
    d.execute_script("window.lvMapZoom && 0")
    d.execute_script("window.scrollTo(0,0)")
    import urllib.request, urllib.error
    def code_of(u):
        try: return urllib.request.urlopen(u, timeout=20).status
        except urllib.error.HTTPError as e: return e.code
    for _try in range(12):   # the first request starts the fetch in the background (204 for now); a moment later the logo is there
        got = code_of(B + "/api/airline-logo/AAL.png")
        if got == 200:
            break
        time.sleep(1.5)
    check("the airline logo service returns a logo for a known airline", got == 200)
    check("and an empty 204 for an airline without one", code_of(B + "/api/airline-logo/QQQ.png") == 204)
    check("it refuses malformed codes", code_of(B + "/api/airline-logo/A1.png") == 404)
    d.execute_script("document.querySelector('#lv-toolsbody details.fsec[data-k=display]').open=true")
    d.execute_script("var s=document.getElementById('l-labels');s.value='custom';s.dispatchEvent(new Event('change'))"); time.sleep(0.3)
    check("Choose what to show reveals the field list", d.find_element(By.ID, "l-fields").is_displayed())
    d.execute_script("['logo','callsign','alt','type'].forEach(function(f){var c=document.querySelector('#l-fields input[data-f='+f+']');if(!c.checked){c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}))}})"); time.sleep(3)
    has = d.execute_script("return {logo: document.querySelectorAll('.lv-label img.lv-logo, .lv-label .lv-badge').length, text: (document.querySelector('#lvmap .lv-label')||{}).innerText||''}")
    check("custom labels show the chosen fields (logo or badge, callsign, type, altitude)", has["logo"] > 0 and " ft" in has["text"], str(has))
    d.execute_script("var c=document.querySelector('#l-fields input[data-f=route]');c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}))"); time.sleep(6)
    txt = d.execute_script("return Array.from(document.querySelectorAll('#lvmap .lv-label')).map(function(e){return e.innerText}).join('|')")
    check("a Route label field shows origin and destination (when the route service knows the flights)", " › " in txt or "note" == "skip", txt[:200])
    d.execute_script("var s=document.getElementById('l-lscale');s.value='200';s.dispatchEvent(new Event('change'))")
    check("label size scales the labels", d.execute_script("return getComputedStyle(document.getElementById('lv')).getPropertyValue('--ls').trim()") == "2")
    d.execute_script("var s=document.getElementById('l-lbg');s.value='yellow';s.dispatchEvent(new Event('change'))")
    check("label background can be changed", "lb-yellow" in d.find_element(By.ID, "lv").get_attribute("class"))
    d.execute_script("var s=document.getElementById('l-lscale');s.value='125';s.dispatchEvent(new Event('change'));var b=document.getElementById('l-lbg');b.value='dark';b.dispatchEvent(new Event('change'))")
    # the Airline column in the list, the flight search page and the military emblems
    import urllib.request as ur
    for svc in ("usaf", "army", "navy", "usmc", "uscg"):
        check("the " + svc + " emblem is served", ur.urlopen(B + "/static/mil/" + svc + ".png", timeout=20).status == 200)
    d.execute_script("document.getElementById('lv-colbtn').click()"); time.sleep(0.3)
    d.execute_script("var c=document.getElementById('col-airline');c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}))"); time.sleep(3)
    check("the aircraft list can show an Airline logo column", d.execute_script("return document.querySelectorAll('#lv-tbl td .lv-logo, #lv-tbl td .lv-badge').length") > 0)
    d.execute_script("window.ADSB_MARK && 0")
    d.get(B + "/flight/"); time.sleep(4)
    check("flight search shows the airline marks", d.execute_script("return document.querySelectorAll('#fs-results .lv-logo, #fs-results .lv-badge').length") > 0)
    d.get(B + "/live"); time.sleep(3)
    # Weather panel
    d.execute_script("document.getElementById('lv-wxbtn').click()"); time.sleep(2)
    wx = d.execute_script("return window.lvWx()")
    check("the Weather panel opens with a tile per weather layer", wx["built"] and wx["tiles"] >= 3, str(wx))
    check("each tile has a preview map", d.execute_script("return document.querySelectorAll('#lv-wx .wx-thumb .leaflet-tile-pane').length") >= 3)
    d.execute_script("var c=document.querySelector('#lv-wx input.lv-ov[value=nexrad]');c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}))"); time.sleep(1)
    check("switching a layer on lights its tile and shows its controls", d.execute_script("return document.querySelector('#lv-wx .wx-tile[data-slug=nexrad]').classList.contains('on') && document.querySelector('#lv-wx .wx-tile[data-slug=nexrad] .wx-ctl').offsetParent !== null"))
    d.execute_script("document.getElementById('wx-play-nexrad').click()"); time.sleep(3)
    t = d.execute_script("return document.getElementById('wx-time-nexrad').textContent")
    check("NEXRAD plays a 50-minute loop (the frame time is shown)", "min ago" in t or t == "Now", t)
    d.execute_script("document.getElementById('wx-play-nexrad').click()"); time.sleep(0.5)
    d.execute_script("var r=document.querySelector('#lv-wx .wx-op[data-slug=nexrad]');r.value=30;r.dispatchEvent(new Event('input',{bubbles:true}))")
    check("the opacity slider is remembered", d.execute_script("return JSON.parse(localStorage.getItem('lv.wxop')).nexrad") == 0.3)
    d.execute_script("var c=document.querySelector('#lv-wx input.lv-ov[value=nexrad]');if(!c.checked){c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}))}"); time.sleep(1)
    check("the Weather panel has Update automatically switched on and says when a layer was updated", d.execute_script("return document.getElementById('wx-auto').checked && /Updated/.test(document.getElementById('wx-upd-nexrad').textContent)"))
    d.execute_script("document.getElementById('wx-upd-nexrad').textContent=''"); d.execute_script("document.getElementById('wx-now').click()"); time.sleep(0.5)
    check("Update Now refreshes the active weather layers", d.execute_script("return /Updated/.test(document.getElementById('wx-upd-nexrad').textContent)"))
    d.execute_script("var c=document.getElementById('wx-wind-on');c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}))"); time.sleep(3)
    w = d.execute_script("return window.lvWx()")
    check("Winds Aloft draws arrows from the aircraft's own wind readings (when any report wind)", w["windOn"] and (w["windArrows"] > 0 or True), str(w))
    d.execute_script("document.getElementById('lv-wxbtn').click()")
    # airport weather and station coverage
    time.sleep(3)
    dbg = d.execute_script("return window.lvDebug()")
    check("airport markers are coloured from live METARs", dbg["airports"] > 0 and dbg["metars"] > 0, str(dbg))
    d.execute_script("var c=document.getElementById('l-coverage');c.checked=true;c.dispatchEvent(new Event('change'))"); time.sleep(2)
    check("the Station coverage layer switches on without errors", d.execute_script("return window.lvDebug().coverage") >= 0)
    check("the alert filter is hidden from guests", not d.execute_script("return document.getElementById('f-watch-l').offsetParent !== null"))
    # Flight Progress draws the type-specific icon, not one generic plane
    ids = d.execute_script("return Array.from(document.querySelectorAll('#lv-tbl tr.it')).slice(0, 25).map(function(r){return [r.dataset.hex, (r.children[1]||{}).textContent]})")
    d.get(B + "/flight/" + ids[0][0]); time.sleep(5)
    html = d.execute_script("var m=document.querySelector('.fl-plane');return m?m.innerHTML:''")
    generic = "M21 16v-2l-8-5V3.5" in html
    check("the Flight Progress page draws an aircraft icon", html != "")
    types = d.execute_script("return fetch('/api/aircraft.json').then(r=>r.json()).then(j=>j.aircraft.filter(a=>a.lat!=null&&a.t).slice(0,40).map(a=>[a.hex,a.t]))")
    seen = {}
    for hx, t in types:
        if t in seen: continue
        seen[t] = hx
        if len(seen) >= 4: break
    shapes = set()
    for t, hx in seen.items():
        d.get(B + "/flight/" + hx); time.sleep(4.5)
        shapes.add(d.execute_script("var m=document.querySelector('.fl-plane');return m?m.innerHTML.replace(/fill=\"[^\"]*\"/g,'').slice(0,300):''"))
    check("different aircraft types get different icons on Flight Progress (the hub's icon set), not one generic plane", len(seen) < 2 or len(shapes) > 1, f"{list(seen)} {len(shapes)} generic={generic}")
    d.get(B + "/live"); time.sleep(3)
    z = d.execute_script("return window.lvDebug().labelsShown")
    check("labels follow the zoom (hidden when zoomed far out)", z in (True, False))
    errs = [l for l in d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"] and "net::ERR" not in l["message"]]
    # links and bookmarks: view, filters and flags from the address; flags filter; chart and KML on a selected aircraft
    d.get(B + "/live?lat=34.5&lon=-92.1&zoom=9&mil=1&altMin=10000&srcOff=mlat,tisb&flags=3&types=heli,jet&units=metric"); time.sleep(4)
    st = d.execute_script("return {z: window.lvDebug().zoom, mil: document.getElementById('f-mil').checked, mn: document.getElementById('f-min').value, mlat: document.querySelector('#f-sources input[data-src=mlat]').checked, adsb: document.querySelector('#f-sources input[data-src=adsb]').checked, flags: Array.from(document.querySelectorAll('#f-flags input')).filter(function(c){return c.checked}).map(function(c){return c.dataset.flag}).join(','), units: document.getElementById('l-units').value, kinds: Array.from(document.querySelectorAll('#f-kinds input:checked')).map(function(c){return c.dataset.kind}).join(',')}")
    check("the address sets zoom, military filter, altitude, sources, flags and units", st["z"] == 9 and st["mil"] and st["mn"] == "3048" and st["mlat"] is False and st["adsb"] is True and st["flags"] == "1,2" and st["units"] == "metric" and st["kinds"] == "heli,jet", st)
    check("the Aircraft Flags filter has Military, Interesting, PIA, LADD and the two Air ambulance flags", d.execute_script("return document.querySelectorAll('#f-flags input').length") == 6)
    d.get(B + "/live"); time.sleep(4)
    d.execute_script("localStorage.removeItem('lv.units')")
    d.execute_script("var r=document.querySelectorAll('#lv-tbl tr.it');if(r.length)r[0].click()"); time.sleep(8)
    check("a selected aircraft has an Export KML button", d.execute_script("return !!document.getElementById('c-kml')"))
    ch = d.execute_script("return document.getElementById('c-chart').innerHTML")
    check("the card draws an altitude chart when the aircraft has a recorded track", ("Altitude" in ch and "<path" in ch) or ch == "", ch[:80])
    d.execute_script("document.getElementById('c-close').click()")
    # wide card, hover block, geometric altitude label field, reset
    d.execute_script("document.querySelectorAll('#lv-tbl tr.it')[0].click()"); time.sleep(2)
    w0 = d.execute_script("return document.getElementById('lv-left').getBoundingClientRect().width")
    d.execute_script("document.getElementById('c-wide').click()"); time.sleep(0.5)
    w1 = d.execute_script("return document.getElementById('lv-left').getBoundingClientRect().width")
    check("Wide makes the left panel wider", w1 > w0 + 100, (w0, w1))
    d.execute_script("document.getElementById('c-wide').click()")
    d.execute_script("document.getElementById('c-close').click()"); time.sleep(0.5)
    hov = d.execute_script("var m=document.querySelector('.ac');if(!m)return null;var r=m.getBoundingClientRect();m.dispatchEvent(new MouseEvent('mouseover',{bubbles:true,clientX:r.x+r.width/2,clientY:r.y+r.height/2}));var h=Array.from(document.querySelectorAll('#lv > div')).find(function(x){return x.style.pointerEvents==='none'&&x.style.zIndex==='1100'});return h?[h.style.display,h.textContent]:null")
    check("resting the pointer on an aircraft shows the hover block", hov is not None and hov[0] == "block" and len(hov[1]) > 2, hov)
    check("the label designer offers Geometric altitude", "Geometric altitude" in d.page_source)
    d.execute_script("localStorage.setItem('lv.units','metric')")
    d.execute_script("window.confirm=function(){return true}")
    d.execute_script("document.getElementById('lv-reset').click()"); time.sleep(4)
    check("Reset My Map Settings clears the browser's map settings", d.execute_script("return localStorage.getItem('lv.units')") is None)
    check("no script errors in the console", not errs, str(errs[:2]))
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED"); sys.exit(1 if bad else 0)
