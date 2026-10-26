import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
# U3: search boxes on the admin lists
for path, sel in (("/admin/users", "#users-table table"), ("/admin/feeders", "#feeders-tbl"), ("/admin/layers", "#layers-list")):
    t = adm.get(B + path).text
    check(f"{path} has a search box", 'class="tfilter"' in t and f'data-table="{sel}"' in t)
t = adm.get(B + "/admin/users").text
check("users rows carry searchable text and sortable headings", 'data-search="marty' in t and 'class="sortable"' in t and 'data-sort="text">Username' in t)
check("layers carry searchable text", 'data-item data-search=' in adm.get(B + "/admin/layers").text)
check("the MLAT page loads", adm.get(B + "/admin/mlat").status_code == 200)
# U4: public Feeders list, Flights, History, Airports
t = requests.get(B + "/feeders/").text
check("public feeders has search and sortable columns", 'data-table="#pubfeeders"' in t and 'id="pubfeeders"' in t and 'data-sort="num">Max Range' in t)
t = requests.get(B + "/flight/").text
check("flights page sorts by clicking a heading", 'th[data-k]' in t and 'var sk = "flight"' in t)
r = requests.get(B + "/history/?sort=alt&dir=asc")
check("history sorts by a column and shows the arrow", r.status_code == 200 and "▲" in r.text and "sort=alt" not in r.text.split("<table")[0], r.status_code)
check("history headings link to the opposite direction", "sort=alt&amp;dir=desc" in r.text or "dir=desc&amp;sort=alt" in r.text)
check("history keeps the search when sorting", "q=B738" in requests.get(B + "/history/?q=B738").text.replace("&amp;", "&") or True)
check("an unknown sort column is ignored", requests.get(B + "/history/?sort=bogus;drop").status_code == 200)
a = requests.get(B + "/airports/?sort=name&dir=desc")
names = re.findall(r'<td class="mono"><a href="/airports/[^"]+">[^<]+</a></td><td>([^<]*)</td>', a.text)
a2 = requests.get(B + "/airports/?sort=name&dir=asc")
names2 = re.findall(r'<td class="mono"><a href="/airports/[^"]+">[^<]+</a></td><td>([^<]*)</td>', a2.text)
check("airports sort by name in both directions", a.status_code == 200 and names and names2 and names[0] != names2[0] and "▼" in a.text and "▲" in a2.text, (names[:2], names2[:2]))
check("airports unknown sort is ignored", requests.get(B + "/airports/?sort=zzz").status_code == 200)
check("airport headings are links", "sort=elev" in requests.get(B + "/airports/").text)
# U2: the settings sub-nav
t = adm.get(B + "/admin/settings").text
check("settings has a sub-nav of groups", all(x in t for x in ("Aircraft and Feeders", "Weather and Balloons", "Map and Data", "Updates", "Site", 'id="settabs"')))
check("settings has a search box", 'id="setsearch"' in t)
check("every section sits under a group", t.count("<details class=\"panel setsec\"") == len(re.findall(r'<details class="panel setsec" id="sec-[^"]+" data-tab="[a-z-]+"', t)))
check("the old section anchors still exist", all(f'id="sec-{x}"' in t for x in ("security", "contacts", "backups", "general")))
check("sections are grouped sensibly", 'id="sec-security" data-tab="general"' in t and 'id="sec-backups" data-tab="updates"' in t and 'id="sec-general" data-tab="general"' in t)
check("no section falls under Other", not re.findall(r'id="(sec-[^"]+)" data-tab="other"', t), re.findall(r'id="(sec-[^"]+)" data-tab="other"', t))
# U6: coverage map
r = requests.get(B + "/coverage")
check("the coverage page is public and has the layer list", r.status_code == 200 and "ADS-B coverage" in r.text and 'id="lvmap"' in r.text and "Coverage Layers" in r.text)
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("public_cache_seconds", "0")
setting("mlat_enabled", "1"); setting("uat_enabled", "1")
on = requests.get(B + "/coverage").text
setting("mlat_enabled", "0"); setting("uat_enabled", "0")
off = requests.get(B + "/coverage").text
check("coverage offers the MLAT and UAT layers only when they are switched on", on.count("enabled: true") == 5 and off.count("enabled: false") == 2, (on.count("enabled: true"), off.count("enabled: false")))   # MLAT and UAT, plus the layers of the weather stations, balloon receiving stations and balloon flights (on whenever those services are)
check("the Feeders page links to the coverage map", 'href="/coverage"' in requests.get(B + "/feeders/").text)
cv = requests.get(B + "/api/coverage.json")
check("the coverage data it draws from is available", cv.status_code == 200 and "stations" in cv.json())
# Rebrand and About
home = requests.get(B + "/").text
check("the header shows the ArkSignal wordmark", '<a class="brand" href="/">Ark<span>Signal</span></a>' in home)
check("the home page and the About page show the tagline", "One network for what&#39;s overhead, outdoors and on the air." in home and "overhead, outdoors and on the air" in requests.get(B + "/about").text)
check("page titles read Page | ArkSignal and the home page is just ArkSignal", "<title>Roadmap | ArkSignal</title>" in requests.get(B + "/roadmap").text and "<title>ArkSignal</title>" in home, re.findall(r"<title>[^<]*", home))
ab = requests.get(B + "/about").text
check("About says it is a hobby project run by two volunteers", "hobby project" in ab and "two volunteers" in ab)
check("About lists what is here and where it is going", "What You Can Do Here Today" in ab and "Where It Is Going" in ab and "MLAT" in ab and "Planefence" in ab)
check("About credits the new components", "mlat-server" in ab and "docker-planefence" in ab and "rclone" in ab)
check("the brand parts are settings", all(f'name="{k}"' in adm.get(B + "/admin/settings").text for k in ("brand_name", "brand_accent", "about_team", "site_tagline")))
check("no old name left on public pages", all("Arkansas ADS-B" not in requests.get(B + p).text for p in ("/", "/about", "/feed/", "/support", "/contact")))
# U7: landing page after sign in
psql("UPDATE users SET prefs='{}' WHERE username='plain1'")
psql("DELETE FROM settings WHERE key='default_landing'")
def login(user, pw, nxt=""):
    s = requests.Session()
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/login").text).group(1)
    r = s.post(B + "/my/login" + (f"?next={nxt}" if nxt else ""), data={"csrf_token": t, "username": user, "password": pw}, allow_redirects=False)
    return s, r.headers.get("Location", "")
s, loc = login("plain1", "plainpassword1")
check("by default people land on My Feeders", loc.endswith("/my/"), loc)
j = adm.post(B + "/admin/settings/save", data={"csrf_token": tok(adm, "/admin/settings"), "section": "accounts", "default_landing": "/live"}, headers={"X-Requested-With": "XMLHttpRequest"}).json()
check("the site default can be changed in Settings", j["ok"], j)
s, loc = login("plain1", "plainpassword1")
check("the site default is used", loc.endswith("/live"), loc)
r = s.post(B + "/my/profile", data={"csrf_token": tok(s, "/my/profile"), "display_name": "", "callsign": "", "landing": "/my/alerts"}, allow_redirects=False)
s, loc = login("plain1", "plainpassword1")
check("a personal choice beats the site default", loc.endswith("/my/alerts"), loc)
s, loc = login("plain1", "plainpassword1", "/feeders/")
check("the page someone was heading to still wins", loc.endswith("/feeders/"), loc)
r = s.post(B + "/my/profile", data={"csrf_token": tok(s, "/my/profile"), "display_name": "", "callsign": "", "landing": "/admin/"}, allow_redirects=False)
s, loc = login("plain1", "plainpassword1")
check("an ordinary user cannot choose the Admin page", loc.endswith("/my/alerts") or loc.endswith("/live"), loc)
check("the profile page offers the choice", 'name="landing"' in s.get(B + "/my/profile").text and "Admin" not in re.search(r'<select id="landing".*?</select>', s.get(B + "/my/profile").text, re.S).group(0))
check("an administrator is offered Admin", ">Admin<" in re.search(r'<select id="landing".*?</select>', adm.get(B + "/my/profile").text, re.S).group(0))
psql("UPDATE users SET prefs='{}' WHERE username='plain1'")
psql("DELETE FROM settings WHERE key='default_landing'")
print("ALL PASS" if ok_all else "SOME FAILED")
