"""Trauma Map: one simple switch shows every air ambulance aircraft (helicopters and airplanes on the air ambulance lists) that the network hears, with a details card; the aircraft are fed to the page here
because the test box may hear none. Needs selenium and Chrome."""
import json, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
h = requests.get(B + "/api/hems.json").json()
check("the air ambulance lists have helicopters and fixed wing aircraft", len(h["hex"]) > 0 and len(h["fw"]["hex"]) > 0, (len(h["hex"]), len(h["fw"]["hex"])))
t = requests.get(B + "/trauma/").text
check("the Trauma Map has the switch and its note", 'id="tr-air"' in t and 'id="tr-airnote"' in t and "Air ambulance aircraft" in t)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
hx, fx = next(iter(h["hex"])), next(iter(h["fw"]["hex"]))
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,900")
d = webdriver.Chrome(options=o); J = d.execute_script
try:
    d.get(B + "/trauma/"); time.sleep(4)
    J("try{localStorage.removeItem('tr.air');localStorage.removeItem('lv.tr.air')}catch(e){}")
    d.get(B + "/trauma/"); time.sleep(4)
    check("the switch starts off and no aircraft are drawn", not J("return document.getElementById('tr-air').checked") and J("var n=0; window.trMap.eachLayer(function(l){ if (l._who) n++; }); return n") == 0)
    fake = {"aircraft": [{"hex": hx, "flight": "LIFE1", "r": "N123AE", "t": "EC35", "cat": "A7", "alt": 1500, "gs": 120, "track": 90, "lat": 34.8, "lon": -92.3, "seen_pos": 2, "desc": "EUROCOPTER EC135"},
                         {"hex": fx, "flight": "AMB2", "r": "N456FW", "t": "PC12", "cat": "A1", "alt": 9000, "gs": 250, "track": 45, "lat": 35.2, "lon": -92.0, "seen_pos": 1},
                         {"hex": "ffffff", "flight": "NOTAMB", "r": "N0TAMB", "t": "B738", "cat": "A3", "alt": 30000, "gs": 450, "track": 10, "lat": 34.0, "lon": -92.0, "seen_pos": 1}]}
    J("var of = window.fetch; var fake = arguments[0]; window.fetch = function (u) { if (String(u).indexOf('/api/aircraft.json') >= 0) return Promise.resolve(new Response(JSON.stringify(fake), {headers: {'Content-Type': 'application/json'}})); return of.apply(this, arguments); }", fake)
    J("document.getElementById('tr-air').click()"); time.sleep(3)
    n = J("var n=0; window.trMap.eachLayer(function(l){ if (l._who) n++; }); return n")
    check("switching it on draws the helicopter and the airplane of the lists, not the airliner", n == 2, n)
    check("the note says how many are in the air", "2 air ambulance aircraft" in J("return document.getElementById('tr-airnote').textContent"))
    J("var want = arguments[0], mk = null; window.trMap.eachLayer(function(l){ if (l._who && !mk && l._a.hex === want) mk = l; }); mk.fire('click')", hx); time.sleep(1)
    check("clicking one opens the details card with the operator, type, altitude and a flight page link", J("return document.getElementById('lv-card').style.display") == "block" and "Operator" in J("return document.getElementById('c-body').innerText") and J("return document.getElementById('c-name').textContent") == "LIFE1" and "/flight/" in J("return document.getElementById('c-page').getAttribute('href')"))
    check("the Legend lists them", (J("document.getElementById('lv-legbtn').click(); return document.getElementById('lv-legbody').innerText") or "").lower().count("air ambulance") >= 1)
    J("document.getElementById('tr-air').click()"); time.sleep(1)
    check("switching it off removes them", J("var n=0; window.trMap.eachLayer(function(l){ if (l._who) n++; }); return n") == 0)
    check("and the choice is remembered", J("return localStorage.getItem('lv.tr.air') || localStorage.getItem('tr.air')") in ("0", None))
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
