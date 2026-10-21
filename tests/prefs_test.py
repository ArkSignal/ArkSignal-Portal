import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout
anon = requests.Session(); adm = sess("marty", PW)

# ---- display preferences
home = anon.get(B + "/aircraft").text
check("Display menu with theme, clock and time zone is on every page", all(s in home for s in ('id="pf-theme"', 'id="pf-hour"', 'id="pf-tz"')))
check("pages start in the site's theme (dark on an ordinary day, a seasonal one on its days) unless a preference says otherwise", "ArkThemes.apply(p)" in home and 'prefs && prefs.theme) || "site"' in open("../app/static/themes.js", encoding="utf-8").read())
check("light theme variables exist", "html[data-theme=light]" in home)
audit = adm.get(B + "/admin/audit").text
check("stored times are <time> elements the browser can re-write", re.search(r'<time class="ts" datetime="[^"]+" data-fmt="dts?">', audit) is not None)
check("unauthenticated visitors cannot save account preferences", anon.post(B + "/my/prefs", json={"theme": "light"}).status_code in (302, 400, 401))
hdr = {"X-CSRFToken": re.search(r'"X-CSRFToken": "([^"]+)"', audit).group(1)}
FULL = {"theme": "light", "hour": "24", "tz": "site", "iconScale": "1.7"}
r = adm.post(B + "/my/prefs", json=FULL, headers=hdr)
sub = lambda d: {k: d.get(k) for k in FULL}     # other saved preferences (for example from a person trying the map) do not matter here
check("signed-in user saves preferences", r.status_code == 200 and sub(r.json()) == FULL, r.text)
r = adm.post(B + "/my/prefs", json={"theme": "neon", "hour": "99", "evil": "x"}, headers=hdr)
check("invalid values and unknown keys are ignored", sub(r.json()) == FULL and "evil" not in r.json(), r.text)
page = adm.get(B + "/admin/audit").text
check("saved preferences follow the account to any page", '"theme": "light"' in page and '"hour": "24"' in page)
adm.post(B + "/my/prefs", json={"theme": "dark", "hour": "auto", "tz": "browser", "iconScale": ""}, headers=hdr)

# ---- visitor address
def feed(ip):
    return requests.get(B + "/feed/?check=1", headers={"CF-Connecting-IP": ip} if ip else {}).text
check("the visitor address comes from the configured proxy header", "203.0.113.9" in feed("203.0.113.9"))
check("a private address gets an explanation", "private network address" in feed("10.1.2.3"))
check("a forged header value is ignored", "not-an-ip" not in feed("not-an-ip"))

# ---- home page
check("home page counts stations that are really online", re.search(r'<span id="stations">\d+</span> station', home) is not None)
check("home radar has the map of Arkansas behind it, in the radar's own range", "/background.svg?radar=150&u=" in home)
svg = anon.get(B + "/background.svg?radar=150").text
check("radar picture includes the surrounding states", all(f">{c}<" in svg for c in ("MO", "TN", "MS", "LA", "TX", "OK")))
check("main background does not carry them", ">TX<" not in anon.get(B + "/background.svg").text)

# ---- caching, icons, types
psql("INSERT INTO settings (key,value) VALUES ('public_cache_seconds','30') ON CONFLICT (key) DO UPDATE SET value='30'")
a1 = anon.get(B + "/api/airports.json?south=34&west=-93&north=35&east=-92&z=9")
check("airport data is cacheable by browsers for the configured time", a1.headers.get("Cache-Control") == "public, max-age=30" and len(a1.json()["airports"]) > 0, a1.headers.get("Cache-Control"))
a2 = anon.get(B + "/api/airports.json?south=34.01&west=-92.99&north=34.99&east=-92.01&z=9")
check("a nearby map view gets the same cached answer", a2.json() == a1.json())
psql("INSERT INTO settings (key,value) VALUES ('public_cache_seconds','0') ON CONFLICT (key) DO UPDATE SET value='0'")
live = anon.get(B + "/live").text
check("the classic map's icon sizing is in the page settings", '"iconDivide": 8.5' in live and '"iconBoost": 1.18' in live and '"iconScale": 1.3' in live, re.findall(r'"icon\w+": [\d.]+', live))
check("visitors can pick their own icon size", 'id="l-icons"' in live and "Extra large" in live)
ac = anon.get(B + "/api/aircraft.json").json()["aircraft"]
check("aircraft carry the type description the icon is chosen from", not ac or any(a.get("td") for a in ac if a.get("t")), ac[:2])
check("aircraft carry the long make and model", not ac or any(a.get("tl") for a in ac if a.get("t")))
check("network totals are in the live feed", "stations_online" in anon.get(B + "/api/aircraft.json").json()["totals"])
st = adm.get(B + "/admin/settings").text
check("new settings are on the settings page", all(s in st for s in ("Enlarge Icons From Zoom", "Public Data Cache (Seconds)", "Update Airport Data Every (Days)", "Alert Emails per User per Hour", "Visitor Address Headers")))
al = adm.get(B + "/my/alerts").text
check("the alerts page tells the user the email limits", "Email limits." in al and "per hour" in al and "of 4 this hour" in al, re.findall(r"Email limits.{0,200}", al))
print("ALL PASS" if ok_all else "SOME FAILED")
