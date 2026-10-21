"""My Account > Display and Maps: every Display menu choice and the saved map views in one page of the account (needs selenium and Chrome for the choices and the views list)."""
import json, re, sys, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
import requests
check("the page needs a sign-in", requests.get(B + "/my/display", allow_redirects=False).status_code in (302, 401, 403))
adm = sess("marty", PW)
h = adm.get(B + "/my/display").text
check("the page uses the site's folding sections, like every other page, not loose panels", h.count('class="panel setsec"') >= 6 and 'id="sec-look"' in h and '<div class="panel"' not in h)
check("the page has the look, time, units, aircraft map, saved views and start-over sections", all(x in h for x in ("Look", "Time", "Units", "Aircraft Map", "Saved Map Views", "Start Over", 'id="d-theme"', 'id="d-u-aircraft"' if False else 'id="d-ua"')))
check("My Account has the link and the Display menu points to it", 'href="/my/display"' in h and 'href="/my/display"' in adm.get(B + "/my/profile").text)
tk = re.search(r'"X-CSRFToken": "([^"]+)"', h).group(1) if '"X-CSRFToken"' in h else re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + "/my/profile").text).group(1)
H = {"X-CSRFToken": tk, "Content-Type": "application/json"}
for nm in ("Zz View A", "Zz View T"):
    adm.post(B + "/my/map-views/delete", json={"name": nm, "url": "/live"}, headers=H)
r = adm.post(B + "/my/map-views", json={"name": "Zz View A", "url": "/live?lat=34.8&lon=-92.3&zoom=8", "layers": ""}, headers=H)
r2 = adm.post(B + "/my/map-views", json={"name": "Zz View T", "url": "/trauma/?shade=zone", "layers": ""}, headers=H)
check("a view of the Trauma Map can be saved too", r.status_code == 200 and r2.status_code == 200, (r.text[:100], r2.text[:100]))
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,1000")
d = webdriver.Chrome(options=o); J = d.execute_script
try:
    d.get(B + "/my/login"); time.sleep(1)
    J("document.querySelector('input[name=username]').value='marty'; document.querySelector('input[name=password]').value=arguments[0]; document.querySelector('form').submit()", PW); time.sleep(3)
    d.get(B + "/my/display"); time.sleep(3)
    check("the views are listed with the map they belong to", "Zz View A" in J("return document.getElementById('d-views').innerText") and "Aircraft Map" in J("return document.getElementById('d-views').innerText") and "Trauma Map" in J("return document.getElementById('d-views').innerText"))
    J("var s=document.getElementById('d-hour'); s.value='24'; s.dispatchEvent(new Event('change'))"); time.sleep(1.5)
    J("var s=document.getElementById('d-u-sondes'); s.value='imperial'; s.dispatchEvent(new Event('change'))"); time.sleep(1.5)
    d.get(B + "/my/display"); time.sleep(3)
    check("a choice made here is kept with the account (clock and units for one service)", J("return document.getElementById('d-hour').value") == "24" and J("return document.getElementById('d-u-sondes').value") == "imperial")
    J("window.confirm = function () { return true }; window.prompt = function () { return 'Zz Renamed' }")
    J("document.querySelector('#d-views button[data-ren]').click()"); time.sleep(2.5)
    check("a view can be renamed", "Zz Renamed" in J("return document.getElementById('d-views').innerText"))
    for _ in range(4):
        if "Zz" not in J("return document.getElementById('d-views').innerText"): break
        J("var b=Array.from(document.querySelectorAll('#d-views tr')).filter(function(r){return r.innerText.indexOf('Zz')>=0})[0]; if (b) b.querySelector('button[data-del]').click()"); time.sleep(2.5)
    check("and deleted", "Zz" not in J("return document.getElementById('d-views').innerText"))
    J("document.getElementById('d-reset').click()"); time.sleep(2)
    d.get(B + "/my/display"); time.sleep(3)
    check("Reset puts the choices back to the site defaults", J("return document.getElementById('d-hour').value") == "auto" and J("return document.getElementById('d-u-sondes').value") == "")
finally:
    d.quit()
    for nm in ("Zz View A", "Zz View T", "Zz Renamed"):
        adm.post(B + "/my/map-views/delete", json={"name": nm, "url": "/live"}, headers=H); adm.post(B + "/my/map-views/delete", json={"name": nm, "url": "/trauma/?shade=zone"}, headers=H)
print("ALL PASS" if ok_all else "SOME FAILED")
