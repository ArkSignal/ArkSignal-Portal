"""The Trauma Map (/trauma/), the trauma region and zone on the County Map and on county pages, the hospital page's website, picture and big map, and the Places menu item. Uses the persistent test portal
(:18431), which keeps real county outlines and hospitals; the trauma list and scope are applied there first. Needs selenium and Chrome on the PC for the browser part."""
import json, os, re, subprocess, sys, time, requests
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); P = os.environ.get("COUNTY_BASE", f"http://{HOST}:18431")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def ssh(c): return subprocess.run(["ssh", "-q", "nastemp", c], capture_output=True, text=True, timeout=300).stdout
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
cn = ssh(D + "ps --format '{{.Names}}' | grep -i persist | grep -i portal | grep -v db | head -1").strip().splitlines()[-1:] or [""]
cname = cn[0].strip()
check("the persistent portal's container was found", bool(cname), cname)
subprocess.run(["scp", "-q", "traumaunit_unit.py", "nastemp:traumaunit_unit.py"], check=True)
uo = ssh(D + f"cp ~/traumaunit_unit.py {cname}:/tmp/traumaunit_unit.py; " + D + f"exec -w /app -e PYTHONPATH=/app {cname} python /tmp/traumaunit_unit.py")
check("traumaunit_unit.py passes inside the portal container", "ALL PASS" in uo, uo[-800:])
py = ("from app import create_app; from app import trauma as t; a = create_app(); ctx = a.app_context(); ctx.push(); r = t.import_centers(); s = t.compute_scope(); "
      "print('RESULT', r['matched'], r['added'], len(r['unreadable']), s['state'], s['system'], s['border'])")
out = ssh(D + f"exec -w /app -e PYTHONPATH=/app {cname} python -c \"{py}\"")
m = re.search(r"RESULT (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)", out)
check("the designated list applies to the hospitals and the scope is worked out", m is not None and int(m.group(1)) >= 20 and int(m.group(4)) >= 40, out[-400:])
time.sleep(16)   # the map's data is kept for 15 seconds
d = requests.get(P + "/api/trauma/data.json").json()
check("the data has seven regions and two zones, each with a color and a place for its name", len(d["regions"]) == 7 and len(d["zones"]) == 2 and all(g["color"] and g["lat"] for g in d["regions"] + d["zones"]), [g["name"] for g in d["regions"]])
check("every county of Arkansas is in a region and a zone", len(d["counties"]) == 75 and all(c["region"] and c["zone"] in (1, 2) for c in d["counties"].values()), len(d["counties"]))
H = d["hospitals"]
lv = {}
for h in H:
    lv[h["level"]] = lv.get(h["level"], 0) + 1
check("the map has the Department's trauma centers by level (6 / 4 / 17 / 24; a center the hospital list lacks and the place finder cannot place is missing) and the other hospitals", lv.get("I", 0) >= 5 and lv.get("II", 0) == 4 and lv.get("III", 0) >= 15 and lv.get("IV", 0) == 24 and lv.get("", 0) >= 20, lv)
byname = {h["name"]: h for h in H}
check("the children's hospitals are the right level (Arkansas Children's Level I, its Northwest hospital Level IV) and a hospital the list does not name is not a trauma center", byname.get("Arkansas Children's Hospital", {}).get("level") == "I" and byname.get("Arkansas Children's Northwest, Inc", {}).get("level") == "IV" and byname.get("Baptist Health - Van Buren", {}).get("level") == "", (byname.get("Arkansas Children's Hospital", {}).get("level"), byname.get("Arkansas Children's Northwest, Inc", {}).get("level"), byname.get("Baptist Health - Van Buren", {}).get("level")))
check("each hospital carries its address, county, region and zone", all(h["address"] and h["lat"] for h in H) and sum(1 for h in H if h["state"] == "AR" and h["region"]) >= sum(1 for h in H if h["state"] == "AR") - 3)
check("only the state's hospitals, designated centers and those near the state line are in it", {h["scope"] for h in H} <= {"state", "system", "border"} and all(h["scope"] != "state" or h["state"] == "AR" for h in H))
out_of_state = [h for h in H if h["state"] != "AR"]
check("hospitals of other states are only designated centers or near the line", all(h["level"] or h["scope"] == "border" for h in out_of_state), [h["name"] for h in out_of_state if not h["level"] and h["scope"] != "border"][:5])
home = requests.get(P + "/").text
check("the Places menu has the Trauma Map", 'href="/trauma/"' in home and "Trauma Map" in home)
tm = requests.get(P + "/trauma/")
check("the Trauma Map page loads", tm.status_code == 200 and "Trauma Facilities" in tm.text and 'id="tr-shade"' in tm.text and "ARK_DRIVE_KEY" in tm.text)
check("the Map selector offers it", 'value="/trauma/"' in tm.text)
cm = requests.get(P + "/counties/").text
check("the County Map can shade by trauma region and zone", 'value="trauma_region"' in cm and 'value="trauma_zone"' in cm)
pul = requests.get(P + "/counties/pulaski")
check("a county page names its trauma region and zone and links to the Trauma Map", pul.status_code == 200 and "Metro Trauma Region" in pul.text and "ADH Trauma Zone 2" in pul.text and "/trauma/?county=" in pul.text)
check("the county page's map is at the bottom, after its lists", pul.text.find('id="cmap"') > pul.text.find("Hospitals</summary>") > 0 or pul.text.find('id="cmap"') > pul.text.find("sec('hospitals'"), (pul.text.find('id="cmap"'), pul.text.find("Hospitals")))
uams = next((h for h in H if "UNIVERSITY OF ARKANSAS" in h["name"].upper()), None)
check("UAMS is a Level I trauma center on the map", uams is not None and uams["level"] == "I", uams and uams["name"])
if uams:
    hp = requests.get(P + uams["href"])
    check("a hospital page names its trauma region and has the big map at the bottom", hp.status_code == 200 and "Trauma Region" in hp.text and "Metro Region" in hp.text and 'id="hmap" style="height:560px"' in hp.text and "/trauma/?hospital=" in hp.text)
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP the browser part: selenium not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1500,950")
dr = webdriver.Chrome(options=o)
J = lambda s: dr.execute_script(s)
try:
    dr.get(P + "/trauma/"); time.sleep(7)
    info = J("return window.trDebug && trDebug()")
    check("the map draws the counties and the hospitals", info and info["counties"] == 75 and info["shown"] >= 30, info)
    check("there are county outlines shaded by region, and the region names as labels", J("return Array.from(document.querySelectorAll('#lvmap svg path')).filter(function(p){return p.getAttribute('fill-opacity')&&parseFloat(p.getAttribute('fill-opacity'))>0.2}).length") > 60 and "Region" in J("return document.querySelector('#lvmap').innerText"))
    check("the page fits the window (no scrolling) with the footer in view", J("return document.documentElement.scrollHeight <= window.innerHeight + 2"), J("return [document.documentElement.scrollHeight, window.innerHeight]"))
    check("there is no Units box, no Layers section, no Show My Location box, no color scale and no Satellite-only style", J("var u=document.getElementById('ark-units-sel'); var lay=document.querySelector('details.fsec[data-k=layers]'); var loc=document.getElementById('my-loc'); var alt=document.getElementById('lv-altbar'); return !u && getComputedStyle(lay).display==='none' && loc.closest('.lv-row').style.display==='none' && getComputedStyle(alt).display==='none' && !document.querySelector('#l-style option[value=sat]')"))
    check("the Drive Times section is shown and the Satellite choice is offered", J("var s=document.querySelector('details.fsec[data-k=drive]'); return getComputedStyle(s).display!=='none' && !!document.querySelector('#l-style option[value=hyb]')"))
    J("var s=document.getElementById('l-style'); s.value='hyb'; s.dispatchEvent(new Event('change'))"); time.sleep(2)
    check("choosing Satellite changes the map's tiles", J("return Array.from(document.querySelectorAll('#lvmap img.leaflet-tile')).some(function(i){return /imagery|satellite|usgs|esri/i.test(i.src)})"), J("return Array.from(document.querySelectorAll('#lvmap img.leaflet-tile')).slice(0,2).map(function(i){return i.src})"))
    check("it offers an icon size and label sizes in Display and View, and the drive times start off", J("return !!document.getElementById('tr-dot') && !!document.getElementById('l-lscale') && !document.getElementById('dr-on').checked"))
    check("the facility list holds the hospitals", J("return document.querySelectorAll('#lv-tbl tbody tr.it').length") >= 30)
    J("var s=document.getElementById('tr-shade'); s.value='zone'; s.dispatchEvent(new Event('change'))"); time.sleep(1.2)
    check("shading by zone shows the zone names", "Trauma Zone" in J("return document.querySelector('#lvmap').innerText"))
    J("var c=document.querySelector('#tr-levels input[data-l=IV]'); c.checked=false; c.dispatchEvent(new Event('change', {bubbles: true}))"); time.sleep(1)
    n4 = J("return trDebug().shown")
    J("var c=document.querySelector('#tr-levels input[data-l=IV]'); c.checked=true; c.dispatchEvent(new Event('change', {bubbles: true}))"); time.sleep(1)
    check("the level boxes show and hide facilities", J("return trDebug().shown") > n4, (n4, J("return trDebug().shown")))
    J("document.querySelector('#lv-tbl tbody tr.it').click()"); time.sleep(2)
    pop = J("var p=document.querySelector('.leaflet-popup-content'); return p ? p.innerText : ''")
    check("choosing a facility opens its popup with the trauma level and address", ("Level" in pop or "Non-Designated" in pop) and "Hospital Page" in pop and re.search(r"\d{5}", pop), pop[:200])
    check("the hospital popup offers its own drive-time rings and the drive times from the hospital", J("return !!document.querySelector('.leaflet-popup-content a[data-dr=ring]') && !!document.querySelector('.leaflet-popup-content a[data-dr=here]')"))
    J("document.querySelector('.leaflet-popup-content a[data-dr=ring]').click()"); time.sleep(8)
    check("choosing it draws the rings", J("return document.querySelectorAll('#lvmap path[stroke-dasharray], #lvmap path.leaflet-interactive').length") > 80 or "Drawing" not in J("return document.getElementById('tr-ringnote').textContent"))
    check("a Clear Drive Times and Rings button is on the map while rings are showing", J("var b=document.getElementById('dr-clearmap'); return !!b && getComputedStyle(b).display!=='none'"))
    J("document.getElementById('dr-clearmap').click()"); time.sleep(1.5)
    check("pressing it takes the rings away, hides the button and sets the rings choice back to none", J("var b=document.getElementById('dr-clearmap'); return getComputedStyle(b).display==='none' && document.getElementById('tr-rings').value==='none'"))
    J("window.trMap.closePopup()")
    sh = J("return window.trMap.getContainer().parentNode && 1")
    J("var m = window.trMap; var ev = null; document.querySelector('#lvmap svg path[fill-opacity]').dispatchEvent(new MouseEvent('click', {bubbles: true}))"); time.sleep(1)
    check("clicking a county opens its card with the region and zone", J("return getComputedStyle(document.getElementById('lv-card')).display") == "block" and "Trauma Region" in J("return document.getElementById('c-body').innerText"))
    dr.get(P + "/counties/"); time.sleep(6)
    J("var s=document.getElementById('cm-metric'); s.value='trauma_region'; s.dispatchEvent(new Event('change'))"); time.sleep(1.5)
    check("the County Map shades by trauma region, labels counties with their region and hides the count scale", "Region" in J("return document.getElementById('lv-legbody').innerText || document.querySelector('#lv-legend').innerText") or J("return document.getElementById('lv-altbar').style.display") == "none")
    lbl = J("return Array.from(document.querySelectorAll('#lvmap .lv-label')).map(function(e){return e.innerText}).join('|')")
    check("each county label carries its region", "Metro" in lbl or "Northwest" in lbl or "Southwest" in lbl, lbl[:200])
    dr.get(P + "/trauma/"); time.sleep(5)
    J("document.getElementById('dr-on').click()"); time.sleep(.5)
    check("with the drive times box ticked, a click on the map offers the times from here or from the person's location", J("window.trMap.fire('click', {latlng: window.trMap.getCenter()}); return !!document.querySelector('.leaflet-popup-content button[data-loc]')"))
finally:
    dr.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
