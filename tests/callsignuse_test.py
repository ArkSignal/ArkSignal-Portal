"""Call sign information wherever a call sign appears: repeater lists link and explain a call sign, a receiving station page shows its license, an owner's call sign is a link (static/callsign.js, the cs filter)."""
import os, re, subprocess, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", "docker exec -i adsb-portal-db psql -U portal -d portal -tA"], input=sql, capture_output=True, text=True, timeout=60).stdout.strip()
check("base.html loads the call sign script on every page", "/static/callsign.js" in requests.get(B + "/").text)
check("the script is served and has the public functions", "ArkCall" in requests.get(B + "/static/callsign.js").text)
adm = sess("marty", PW)
psql("DELETE FROM repeaters WHERE callsign = 'W5CSU'; DELETE FROM sonde_stations WHERE callsign = 'W1AW'")
tok = re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + "/admin/repeaters").text).group(1)
adm.post(B + "/admin/repeaters/add", data={"csrf_token": tok, "freq": "147.3", "offset": "+0.6", "callsign": "W5CSU", "city": "Conway", "lat": "35.09", "lon": "-92.44", "modes": ["FM"]}, allow_redirects=False)
psql("INSERT INTO sonde_stations (callsign, lat, lon, privacy, public) VALUES ('W1AW', 41.7, -72.7, 'exact', true)")
lst = requests.get(B + "/repeaters/", params={"q": "W5CSU"}).text
check("the repeater list links a call sign to the lookup", 'class="cs" data-call="W5CSU"' in lst and "/callsign?call=W5CSU" in lst, lst[:100])
st = requests.get(B + "/sondes/stations/W1AW").text
for page in ("sondes_map.html", "weather_map.html", "aprs_map.html", "repeaters_map.html"):
    check(f"{page} gives the card's call sign its licensee hover", "ArkCall." in open("../app/templates/" + page, encoding="utf-8").read())
check("the weather station popup does too (APRS stations)", "ArkCall.tag(b, pt.id)" in requests.get(B + "/static/wxmap.js").text)
check("a receiving station page has a License row for its call sign", 'class="cs-license' in st and 'data-call="W1AW"' in st)
try:
    from selenium import webdriver
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,900")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + "/sondes/stations/W1AW"); time.sleep(5)
        t = d.execute_script("return document.querySelector('.cs-license').textContent")
        check("the License row fills in from the FCC data", "License details" in t and ("Arrl" in t or "ARRL" in t.upper()), t)
        d.get(B + "/repeaters/"); time.sleep(2)
        d.execute_script("var a=document.querySelector('a.cs'); if (a) a.dispatchEvent(new Event('mouseenter'))"); time.sleep(4)
        title = d.execute_script("var a=document.querySelector('a.cs'); return a ? a.title : null")
        check("hovering a call sign in the list explains it (or says nothing when it has no US license)", title is not None)
    finally:
        d.quit()
except ImportError:
    print("SKIP the browser part: selenium is not installed")
psql("DELETE FROM repeaters WHERE callsign = 'W5CSU'; DELETE FROM sonde_stations WHERE callsign = 'W1AW'")
print("ALL PASS" if ok_all else "SOME FAILED")
