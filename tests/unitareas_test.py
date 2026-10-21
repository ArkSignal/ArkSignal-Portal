"""Units per service: the logic (unitareas_unit.py in the portal container), Settings > Units, the Display menu's units for each service, the Units box in every map's Layers and Filters, and what the pages do
(radiosonde pages metric by default, the others standard, a choice for one service changes only that service). Needs selenium and Chrome on the PC for the browser part."""
import json, os, re, subprocess, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def ssh(c): return subprocess.run(["ssh", "-q", "nastemp", c], capture_output=True, text=True, timeout=120).stdout
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
subprocess.run(["scp", "-q", "unitareas_unit.py", "nastemp:adsb-portal/unitareas_unit.py"], check=True)
out = ssh("cd ~/adsb-portal && " + D + "cp unitareas_unit.py adsb-portal:/tmp/unitareas_unit.py; " + D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/unitareas_unit.py")
check("unitareas_unit.py passes inside the portal container", "ALL PASS" in out, out[-500:])
adm = sess("marty", PW)
st = adm.get(B + "/admin/settings").text
check("Settings > Units has a default for every service, radiosondes on Metric", all(f'name="units_{a}"' in st or f'id="units_{a}"' in st for a in ("aircraft", "weather", "sondes", "aprs", "acars", "mesh", "repeaters", "places", "network", "home")) and re.search(r'units_sondes[^>]*>.*?<option value="metric" selected', st, re.S) is not None)
home = requests.get(B + "/").text
check("every page tells its script the service it belongs to and the site's defaults", "window.ARK_AREA" in home and "window.SITE_UNITS_BY" in home and '"sondes": "metric"' in home.replace('"sondes":"metric"', '"sondes": "metric"'))
check("the Display menu has Units for each service", 'id="pf-ua"' in home and 'id="pf-ua-list"' in home)
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP the browser part: selenium not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
J = lambda s: d.execute_script(s)
SYS = "return ArkUnits.system()"
try:
    d.get(B + "/sondes/map"); time.sleep(3)
    check("the Radiosonde Map is in metric by default", J(SYS) == "metric" and J("return ArkUnits.text.alt(11579)") == "11,579 m", (J(SYS), J("return ArkUnits.text.alt(11579)")))
    d.get(B + "/live"); time.sleep(3)
    check("the aircraft map starts in aviation units (feet, knots, nautical miles)", J(SYS) == "aero" and J("return ArkUnits.text.alt(3048)") == "10,000 ft" and J("return ArkUnits.text.spd(10)") == "19 kt")
    d.get(B + "/weather/map"); time.sleep(3)
    check("the weather map starts in US units", J(SYS) == "imperial")
    J("localStorage.setItem('adsb.prefs', JSON.stringify({u_sondes: 'imperial', units: 'metric'}))")
    d.get(B + "/sondes/map"); time.sleep(3)
    check("a person's own choice for radiosondes (imperial) beats their overall choice there", J(SYS) == "imperial" and J("return ArkUnits.text.alt(3048)") == "10,000 ft")
    d.get(B + "/live"); time.sleep(3)
    check("while their overall choice (metric) beats the site's default for the aircraft pages", J(SYS) == "metric" and J("return ArkUnits.text.alt(3048)") == "3,048 m")
    J("localStorage.setItem('adsb.prefs', JSON.stringify({units: 'imperial'}))")
    d.get(B + "/sondes/map"); time.sleep(3)
    check("an overall choice of imperial applies to radiosondes too (it is the person's own choice)", J(SYS) == "imperial")
    d.get(B + "/"); time.sleep(2)
    J("localStorage.clear()")
    d.get(B + "/"); time.sleep(2)
    d.find_element(By.CSS_SELECTOR, "#pf-theme").find_element(By.XPATH, "./ancestor::details").click() if False else None
    sels = J("return Array.prototype.map.call(document.querySelectorAll('#pf-ua-list select'), function (s) { return s.id })")
    check("the Display menu lists a units box for each of the nine services, the radiosonde one showing its default", len(sels) == 10 and "pf-u-sondes" in sels and "Meters" in J("return document.querySelector('#pf-u-sondes option').textContent"), sels)
    J("var s=document.getElementById('pf-u-weather'); s.value='metric'; s.dispatchEvent(new Event('change'))"); time.sleep(1)
    check("choosing units for one service saves just that service (and the cookie server pages read)", J("return JSON.parse(localStorage.getItem('adsb.prefs')).u_weather") == "metric" and "weather:metric" in J("return document.cookie"))
    d.get(B + "/weather/map"); time.sleep(3)
    check("and the weather pages follow it", J(SYS) == "metric")
    d.get(B + "/live"); time.sleep(3)
    check("while the aircraft pages do not", J(SYS) == "aero")
    J("localStorage.clear()")
    for page, hasown in (("/sondes/map", False), ("/counties/", False), ("/repeaters/map", False), ("/aprs/map", False), ("/live", True), ("/weather/map", True)):
        d.get(B + page); time.sleep(3)
        J("var t=document.getElementById('lv-toolsbody'), b=document.getElementById('lv-filtbtn'); if (t && b && t.style.display === 'none') b.click()"); time.sleep(.8)   # folded when another suite left Panels When a Map Opens on Closed
        if hasown:
            vis = J("var e=document.getElementById('l-units'); return !!e && e.offsetParent !== null")
            check(f"{page}: the map's Layers and Filters has a Units box", vis)
        else:
            vis = J("var e=document.getElementById('ark-units-sel'); return !!e && e.options.length === 4")
            check(f"{page}: the map's Layers and Filters has a Units box with the default and the three sets", vis)
    d.get(B + "/sondes/map"); time.sleep(3)
    J("var s=document.getElementById('ark-units-sel'); s.value='imperial'; s.dispatchEvent(new Event('change'))"); time.sleep(3)
    check("choosing Imperial in the Radiosonde Map's Layers and Filters changes the map's units and is kept for the service", J(SYS) == "imperial" and J("return JSON.parse(localStorage.getItem('adsb.prefs')).u_sondes") == "imperial")
    J("localStorage.clear()")
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
