import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
ME = sess("plain1", "plainpassword1")   # a signed-in member sees the My ... entries (guests do not: guestaccess_test)
def nav(path):
    t = ME.get(B + path).text
    return re.search(r'<nav id="mainnav">.*?</nav>', t, re.S).group(0)
def groups(n):
    return {m.group(2): (m.group(1), re.findall(r'<a href="([^"]+)"', m.group(3))) for m in re.finditer(r'<details class="navmenu( active)?"><summary><a class="gl" href="[^"]+">([^<]+)</a></summary>\s*<div class="navdrop">(.*?)</div>', n, re.S)}
setting("mlat_enabled", "1"); setting("uat_enabled", "1")
check("the name is two colours on the home and About pages", "<h1>Ark<span>Signal</span></h1>" in requests.get(B + "/").text and "<h1>About Ark<span>Signal</span></h1>" in requests.get(B + "/about").text)
n = nav("/")
g = groups(n)
check("the menu has Aircraft, Places, Weather, Radiosondes, Radio, River Traffic, Network and Feed flyouts", [x for x in g if x != "Community"][:8] == ["Aircraft", "Places", "Weather", "Radiosondes", "Radio", "River Traffic", "Network", "Feed"], list(g))
check("Radiosondes holds the balloon map, overview, flights, receiving stations and the member's own page", g["Radiosondes"][1] == ["/sondes/", "/sondes/map", "/sondes/flights", "/sondes/stations/", "/sondes/statistics", "/view/balloon-stats", "/my/sondes"], g["Radiosondes"][1])
check("Weather holds the overview, the map, GOES Imagery, the dashboard and the member's own page", g["Weather"][1] == ["/weather/", "/weather/map", "/weather/place", "/weather/goes", "/weather/statistics", "/view/weather-stats", "/my/weather"], g["Weather"][1])
check("Aircraft holds the aircraft pages", all(h in g["Aircraft"][1] for h in ("/aircraft", "/live", "/flight/", "/history/", "/mlat/", "/my/")) and "/airports/" not in g["Aircraft"][1] and "/hospitals/" not in g["Aircraft"][1], g["Aircraft"][1])
check("Aircraft ends with My Feeders, matching My Weather Stations in the Weather group", g["Aircraft"][1][-1] == "/my/" and '>My Feeders<' in n)
check("Places holds the county map, the trauma map, the airports and the hospitals", g["Places"][1] == ["/counties/", "/trauma/", "/airports/", "/hospitals/"], g["Places"][1])
homes = dict(re.findall(r'<a class="gl" href="([^"]+)">([^<]+)</a>', n)[i][::-1] for i in range(len(re.findall(r'<a class="gl"', n))))
check("RULE: every service group opens its overview first and has its map second (Aircraft, Weather, Radiosondes); Places opens the County Map", homes["Aircraft"] == "/aircraft" and g["Aircraft"][1][:2] == ["/aircraft", "/live"] and homes["Weather"] == "/weather/" and g["Weather"][1][:2] == ["/weather/", "/weather/map"] and homes["Radiosondes"] == "/sondes/" and g["Radiosondes"][1][:2] == ["/sondes/", "/sondes/map"] and homes["Places"] == "/counties/", homes)
check("Network holds coverage, stations and status; the aircraft and feeder statistics are in the Aircraft menu", all(h in g["Network"][1] for h in ("/coverage", "/status")) and "/feeders/" not in g["Network"][1] and "/feeders/" in g["Aircraft"][1] and "/view/statistics" not in g["Network"][1] and "/view/statistics" in g["Aircraft"][1], (g["Network"][1], g["Aircraft"][1]))
check("Feed holds the Feed sub-pages", g["Feed"][1] == ["/feed/", "/feed/adsb", "/feed/mlat", "/feed/uat", "/feed/weather", "/feed/sondes", "/feed/ais", "/feed/drones", "/feed/satellites", "/feed/check"], g["Feed"][1])
check("the Tools menu is gone, its tools sit in the flyouts", ">Tools<" not in n and "/view/heatmap" in g["Aircraft"][1] or "/live?heat=1" in str(g["Aircraft"]) and 'href="/view/plane-alert"' in n)
check("no group is highlighted on the overview", all(not v[0] for v in g.values()))
for path, key in (("/aircraft", "Aircraft"), ("/live", "Aircraft"), ("/flight/", "Aircraft"), ("/history/", "Aircraft"), ("/airports/KLIT", "Places"), ("/hospitals/", "Places"), ("/mlat/", "Aircraft"),
                  ("/coverage", "Network"), ("/feeders/", "Aircraft"), ("/status", "Network"), ("/feed/", "Feed"), ("/feed/check", "Feed"), ("/weather/", "Weather"), ("/feed/weather", "Feed")):
    gg = groups(nav(path))
    check(f"{path} highlights {key}", gg[key][0].strip() == "active" and sum(1 for v in gg.values() if v[0]) == 1, {k: v[0] for k, v in gg.items()})
check("the current page is marked inside the flyout", 'href="/coverage" class="here"' in nav("/coverage"))
setting("mlat_enabled", "0"); setting("uat_enabled", "0")
g = groups(nav("/"))
check("MLAT and UAT entries disappear when switched off", "/mlat/" not in g["Aircraft"][1] and g["Feed"][1] == ["/feed/", "/feed/adsb", "/feed/weather", "/feed/sondes", "/feed/ais", "/feed/drones", "/feed/satellites", "/feed/check"], (g["Aircraft"][1], g["Feed"][1]))
print("ALL PASS" if ok_all else "SOME FAILED")
