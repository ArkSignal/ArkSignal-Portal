"""Consistency pass (Marty, 2026-10-10): the Radio overview sorts by clicking a heading like the other lists, APRS Stations and APRS Weather sort and search, the APRS Map has labels and weather readings
like the other maps, a unit chosen in this browser (cookie) is followed by server-built pages even when the site default differs, opening the Legend closes the Layers and Filters panel on every map,
and the button says Layers & Filters."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()

t = requests.get(B + "/repeaters/").text
check("the Radio overview sorts by clicking a heading, not with a Sort by box", 'name="sort"' not in t and ("aria-sort=" in t or "No repeater matches" in t))
r = requests.get(B + "/repeaters/?sort=call&dir=desc").text
calls = re.findall(r'<td class="mono">([A-Z]{1,2}[0-9][A-Z]{1,4})</td>\s*<td>', r)
check("sorting by call sign descending puts them in reverse order", len(calls) < 2 or calls == sorted(calls, reverse=True), calls[:6])
check("an unknown sort column is ignored", requests.get(B + "/repeaters/?sort=bogus;drop").status_code == 200)

a = requests.get(B + "/aprs/").text
check("APRS Stations has a search box and sortable headings", 'data-table="#aprs-tbl"' in a and 'class="sortable" id="aprs-tbl"' in a or "No station matches" in a or 'id="aprs-tbl"' not in a)
w = requests.get(B + "/aprs/weather").text
check("APRS Weather has a search box and sortable headings (when it has stations)", ('data-table="#aprswx-tbl"' in w and 'id="aprswx-tbl"' in w) or "No APRS weather station" in w)
m = requests.get(B + "/aprs/map").text
check("the APRS Map has station labels and a weather reading choice", 'id="am-labels"' in m and 'id="am-reading"' in m and "/api/aprs/weather.json" in m)

# units: a browser's choice reaches server-built pages
psql("INSERT INTO settings (key, value) VALUES ('live_units','aero') ON CONFLICT (key) DO UPDATE SET value='aero'")
psql("INSERT INTO settings (key, value) VALUES ('units_aprs','') ON CONFLICT (key) DO UPDATE SET value=''")   # APRS has its own default (US units); blank hands it to the site default tested here
import time; time.sleep(8)
if "aprswx-tbl" in w:
    site = requests.get(B + "/aprs/weather").text
    imp = requests.get(B + "/aprs/weather", cookies={"ark_units": "imperial"}).text
    met = requests.get(B + "/aprs/weather", cookies={"ark_units": "metric"}).text
    check("with the site default aeronautical the page shows knots and degrees C", " kt" in site and "°C" in site)
    check("the cookie of a browser that chose imperial gives mph and degrees F", " mph" in imp and "°F" in imp and "°C" not in imp)
    check("and metric gives km/h", "km/h" in met)
    check("a nonsense cookie is ignored", " kt" in requests.get(B + "/aprs/weather", cookies={"ark_units": "bogus"}).text)
base = requests.get(B + "/").text
check("every page sets the cookie from the browser's saved choice", "ark_units=" in base)
psql("DELETE FROM settings WHERE key='live_units'")

# map panels
sh = requests.get(B + "/static/mapshell.css").text
for path in ("/counties/", "/aprs/map", "/repeaters/map", "/sondes/map", "/weather/map", "/live"):
    h = requests.get(B + path).text
    check(f"{path}: the button says Layers & Filters", "Layers &amp; Filters" in h or "Layers & Filters" in h, path)
    check(f"{path}: opening the Legend does not keep the Layers and Filters panel open", "filtersShow(true); }" not in re.sub(r"\s+", " ", h).split("function legOpen")[-1][:200] if "function legOpen" in h else True, path)
shell = requests.get(B + "/sondes/map").text
check("the shared shell closes the filters when the Legend opens", "AL.wxOpen(false); filtersShow(false); }" in shell)
sm = requests.get(B + "/sondes/map").text
check("a balloon launched from a known launch site gets no launch flag on top of the site icon", "atKnownSite" in sm and "!atKnownSite(f)" in sm)
live_html = requests.get(B + "/live").text; cty_html = requests.get(B + "/counties/").text
lab_a = "onClick: function () { select(r.call, false); }"
lab_c = "onClick: function () { select(s.geoid, false); }"
eng = requests.get(B + "/static/labelfit.js").text
check("clicking a label does what clicking its icon does: aircraft, APRS and county labels are clickable", "onClick: function () { select(hex); }" in live_html and "interactive: !!it.onClick" in eng and lab_a in m and lab_c in cty_html)
check("the Balloon Map has a Show Balloons choice with Live only (it replaces In the air now only), and old aloft=1 links still work", 'id="sm-show"' in sm and "Live only" in sm and "sm-aloft" not in sm and 'q.get("aloft") === "1"' in sm)
check("a forecast flight starts at its known launch site (and gets no flag on top of the site icon)", "function siteOf(p)" in sm and "var run = [start]" in sm and "if (!(site && $(\"sm-sites\").checked)) mk(\"launch\"" in sm)
psql("DELETE FROM settings WHERE key='units_aprs'")
print("ALL PASS" if ok_all else "SOME FAILED")
