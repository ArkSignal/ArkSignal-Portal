import json, re, requests, subprocess, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True


def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)
    ok_all &= bool(cond)


def sql(q):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -At -c \"{q}\""], capture_output=True, text=True).stdout


adm = sess("marty", PW)
anon = requests.Session()

# ---- home page
h = anon.get(B + "/aircraft").text
check("home page is the portal's own page with the shared menu", 'class="brand"' in h and 'href="/live"' in h)
check("home page has the radar, live numbers and explore cards", 'id="radar"' in h and 'id="ac"' in h and 'id="msg"' in h and "Explore" in h)
check("home page has no station tiles any more", ">Stations</h2>" not in h and "Rooftop mast" not in h)
check("home explore cards come from the tool list with descriptions", "Where traffic flies, for any period" in h or "Open in the portal" in h)
check("home page radar settings come from Admin Settings", '"ring": 50' in h and '"range": 150' in h)
check("brand link goes home", 'class="brand" href="/"' in h)

# ---- live data feeds
a = anon.get(B + "/api/aircraft.json")
d = a.json()
check("aircraft feed answers with totals and aircraft", a.status_code == 200 and d["totals"]["aircraft"] == len(d["aircraft"]) and len(d["aircraft"]) > 0, str(d)[:200])
ac = d["aircraft"][0]
check("each aircraft has the fields the map needs", all(k in ac for k in ("hex", "flight", "r", "t", "alt", "gnd", "gs", "track", "lat", "lon", "squawk", "mil", "emg")), list(ac))
time.sleep(1.2)
d2 = anon.get(B + "/api/aircraft.json").json()
check("message rate comes from the hub statistics, whichever worker answers", all(anon.get(B + "/api/aircraft.json").json()["totals"]["messages_per_second"] > 5 for _ in range(6)), d2["totals"])
check("feed is not cached by browsers", anon.get(B + "/api/aircraft.json").headers.get("Cache-Control") == "no-store")
check("highest aircraft summary present", d2["totals"]["highest"] and d2["totals"]["highest"]["alt"] > 0)

# ---- airports for the map
box = "south=34.3&west=-93&north=35.2&east=-91.8"
z7 = anon.get(B + f"/api/airports.json?{box}&z=7").json()["airports"]
check("zoom 7 shows major airports in view (KLIT) and no small fields", any(x["ident"] == "KLIT" for x in z7) and all(x["type"] in ("large_airport", "medium_airport") for x in z7), [x["ident"] for x in z7][:6])
check("zoomed far out shows no airports", anon.get(B + f"/api/airports.json?{box}&z=3").json()["airports"] == [])
z10 = anon.get(B + f"/api/airports.json?south=34.2&west=-92.6&north=34.5&east=-92.2&z=10").json()["airports"]
check("zoom 10 adds small airports (Sheridan K9M8)", any(x["ident"] == "K9M8" for x in z10), [x["ident"] for x in z10])
check("airport feed needs a bounding box", anon.get(B + "/api/airports.json").status_code == 400)
x = anon.get(B + "/api/airports/KLIT.json").json()
check("airport detail has frequencies and runways", x["ident"] == "KLIT" and len(x["frequencies"]) > 3 and len(x["runways"]) >= 2, str(x)[:120])
check("unknown airport detail is 404", anon.get(B + "/api/airports/ZZZZ.json").status_code == 404)

# ---- native live map page
lv = anon.get(B + "/live").text
check("live map page has our own panels, list, filters and layers", all(s in lv for s in ('id="lvmap"', 'id="lv-list"', 'id="f-mil"', 'id="l-airports"', "Aircraft")))
check("live map page uses the configured map center and zoom", '"lat": 34.8' in lv and '"zoom": 8' in lv)
check("live map nav link present", 'href="/live">Live Map</a>' in h)
check("live map accepts an aircraft in the link", anon.get(B + "/live?icao=a1b2c3").status_code == 200)

# ---- heatmap date and time pickers
hm = anon.get(B + "/view/heatmap?classic=1&from=2026-10-01T00:00&to=2026-10-02T12:00").text
check("exact range becomes duration and end offset", "heatDuration=36&amp;" in hm and re.search(r"heatEnd=\d", hm) is not None, re.findall(r'src="[^"]*"', hm))
check("custom range is shown back in the pickers", 'value="2026-10-01T00:00"' in hm and 'value="2026-10-02T12:00"' in hm)
check("end before start is explained", "at least 30 minutes after the start" in anon.get(B + "/view/heatmap?classic=1&from=2026-10-02T12:00&to=2026-10-01T00:00").text)
check("range over the maximum is explained", "longest range is 30 days" in anon.get(B + "/view/heatmap?classic=1&from=2026-01-01T00:00&to=2026-06-01T00:00").text)
import datetime as _dt
_f, _t = [(_dt.datetime.utcnow() + _dt.timedelta(days=d)).strftime("%Y-%m-%dT00:00") for d in (1, 3)]
check("future end is refused", "cannot be in the future" in anon.get(B + f"/view/heatmap?classic=1&from={_f}&to={_t}").text)
check("garbage dates are refused", "Enter the start and end" in anon.get(B + "/view/heatmap?classic=1&from=abc&to=def").text)
check("pickers default to the preset window", re.search(r'name="from" value="\d{4}-\d\d-\d\dT\d\d:\d\d"', anon.get(B + "/view/heatmap?classic=1").text) is not None)

# ---- pinned network stations and opt-in site links (public test feeders)
subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/mkpublic.py"], check=True, capture_output=True)
order = re.findall(r'<a href="/feeders/(PUB-[A-Z]+)">', anon.get(B + "/feeders/").text)
check("without pins the list is ranked normally", len(order) == 3, order)
BRAVO, ALPHA = "pubaaaa2-0000-4000-8000-000000000002", "pubaaaa1-0000-4000-8000-000000000001"


def manage(uuid, **kw):
    kw["csrf_token"] = tok(adm, f"/admin/feeders/{uuid}")
    r = adm.post(B + f"/admin/feeders/{uuid}/manage", data=kw)
    return " ".join(re.findall(r'class="flash[^>]*>([^<]*)', r.text))


check("admin pins a station", "pinned to the top" in manage(BRAVO, action="official", official="1", official_order="1"))
check("admin pins a second with a later order", "pinned to the top" in manage(ALPHA, action="official", official="1", official_order="2"))
pl = anon.get(B + "/feeders/").text
order = re.findall(r'<a href="/feeders/(PUB-[A-Z]+)">', pl)
check("pinned stations come first, in pin order, then the ranking", order[:2] == ["PUB-BRAVO", "PUB-ALPHA"] and order[2] == "PUB-CHARLIE", order)
check("pinned stations carry the configured label", pl.count("Network Station</span>") == 2)
check("pin order must be a number", "whole number from 0 to 9999" in manage(BRAVO, action="official", official="1", official_order="x"))
check("admin sets a station's website and shows it publicly", "shown publicly" in manage(ALPHA, action="website", website="https://kd5vcf.adsb.allisononline.org/", website_public="1"))
pl = anon.get(B + "/feeders/").text
check("the website appears as a Visit link in the public list", 'href="https://kd5vcf.adsb.allisononline.org/"' in pl and ">Visit<" in pl)
check("and on the station page and in the JSON feed", 'href="https://kd5vcf.adsb.allisononline.org/"' in anon.get(B + "/feeders/PUB-ALPHA").text and any(f.get("website") == "https://kd5vcf.adsb.allisononline.org/" for f in anon.get(B + "/api/feeders.json").json()["feeders"]))
check("admin page shows the website", "kd5vcf.adsb.allisononline.org" in adm.get(B + f"/admin/feeders/{ALPHA}").text)
check("a bad website address is refused", "full http" in manage(ALPHA, action="website", website="javascript:alert(1)", website_public="1"))
check("saved but not public stays hidden", "not shown publicly" in manage(ALPHA, action="website", website="https://kg5dor.adsb.allisononline.org/") and 'href="https://kg5dor.adsb.allisononline.org/"' not in anon.get(B + "/feeders/").text)
check("admin clears the website", "cleared" in manage(ALPHA, action="website", website=""))
check("website changes are audited", "feeder.website" in adm.get(B + "/admin/audit?action=feeder.website").text)
base = dict(site_name="ArkSignal", registration_open="1", default_privacy="general", feed_mode="open", map_lat="34.80", map_lon="-92.30", map_zoom="7", map_zoom_page="8", timezone="America/Chicago",
            max_failed_attempts="5", lockout_minutes="15", history_retention_days="730", sample_retention_days="30", feed_host="feed.arksignal.allisononline.org", feed_port="30004", mlat_port="31090", max_rules_per_user="25")
ok, m, _ = act(adm, "/admin/settings/save", "/admin/settings", **dict(base, official_badge_text="Club Station"))
check("the label is a setting", ok and "Club Station</span>" in anon.get(B + "/feeders/").text, m)
act(adm, "/admin/settings/save", "/admin/settings", **dict(base, official_badge_text="Network Station"))
check("admin unpins a station", "No longer pinned" in manage(BRAVO, action="official", official_order="1"))
order = re.findall(r'<a href="/feeders/(PUB-[A-Z]+)">', anon.get(B + "/feeders/").text)
check("after unpinning, only the remaining pinned station leads", order[0] == "PUB-ALPHA", order)
manage(ALPHA, action="official", official_order="2")
sql(f"update feeders set website = 'https://alpha.example.org/', website_public = false where uuid = '{ALPHA}'")
check("a site link the owner has not shared is not shown", "alpha.example.org" not in anon.get(B + "/feeders/").text and "alpha.example.org" not in anon.get(B + "/feeders/PUB-ALPHA").text)
check("the JSON feed hides an unshared link", [f for f in anon.get(B + "/api/feeders.json").json()["feeders"] if f["station_id"] == "PUB-ALPHA"][0]["website"] is None)
sql(f"update feeders set website_public = true where uuid = '{ALPHA}'")
pl = anon.get(B + "/feeders/").text
check("a shared link shows in the list and opens in a new tab safely", re.search(r'<a href="https://alpha.example.org/" target="_blank" rel="noopener nofollow">Visit</a>', pl) is not None)
check("a shared link shows on the station page and in the JSON feed", "alpha.example.org" in anon.get(B + "/feeders/PUB-ALPHA").text and
      [f for f in anon.get(B + "/api/feeders.json").json()["feeders"] if f["station_id"] == "PUB-ALPHA"][0]["website"] == "https://alpha.example.org/")

# owner consent checkbox through the real form (claimer1 owns PUB-ALPHA in the fixtures)
c1 = sess("claimer1", "claimpassword1")
page = c1.get(B + f"/my/feeders/{ALPHA}").text
check("owner form has the consent checkbox, ticked when shared", 'name="website_public"' in page and re.search(r'name="website_public" value="1" checked', page) is not None)
form = dict(name="Alpha Station", description="", notes="", website="https://alpha.example.org/", lat="34.315231", lon="-92.410357", antenna_height_ft="", height_ref="", ground_elev_ft="", privacy="general", csrf_token=tok(c1, f"/my/feeders/{ALPHA}"))
r = c1.post(B + f"/my/feeders/{ALPHA}/save", data=form, headers={"X-Requested-With": "XMLHttpRequest"})
check("saving without the checkbox makes the link private", r.json()["ok"] and "alpha.example.org" not in anon.get(B + "/feeders/").text)
form["website_public"] = "1"
form["csrf_token"] = tok(c1, f"/my/feeders/{ALPHA}")
c1.post(B + f"/my/feeders/{ALPHA}/save", data=form, headers={"X-Requested-With": "XMLHttpRequest"})
check("ticking it shares the link again", "alpha.example.org" in anon.get(B + "/feeders/").text)
form["website"] = ""
form["csrf_token"] = tok(c1, f"/my/feeders/{ALPHA}")
c1.post(B + f"/my/feeders/{ALPHA}/save", data=form, headers={"X-Requested-With": "XMLHttpRequest"})
check("no link means nothing to share even with the box ticked", "alpha.example.org" not in anon.get(B + "/feeders/").text and "Visit</a>" not in anon.get(B + "/feeders/").text)

# ---- classic aircraft icons and the My-area navigation
mk = anon.get(B + "/api/markers.js")
check("classic icon set is served from the hub for the live map", mk.status_code == 200 and "javascript" in mk.headers["Content-Type"] and "function getBaseMarker" in mk.text and "TypeDesignatorIcons" in mk.text, mk.text[:100])
check("icon file is cacheable", "max-age=3600" in mk.headers.get("Cache-Control", ""))
check("aircraft feed carries the category used to pick an icon", "cat" in anon.get(B + "/api/aircraft.json").json()["aircraft"][0])
lv = anon.get(B + "/live").text
check("live map loads the icon set and uses the configured icon size", 'src="/api/markers.js"' in lv and '"iconScale": 1.3' in lv and "getBaseMarker" in lv and "svgShapeToSVG" in lv)
ok, m, _ = act(adm, "/admin/settings/save", "/admin/settings", **dict(base, live_icon_scale="1.7"))
check("icon size is a setting", ok and '"iconScale": 1.7' in anon.get(B + "/live").text, m)
ok, m, _ = act(adm, "/admin/settings/save", "/admin/settings", **dict(base, live_icon_scale="9"))
check("bad icon size rejected", not ok, m)
act(adm, "/admin/settings/save", "/admin/settings", **dict(base, live_icon_scale="1"))
c = sess("claimer1", "claimpassword1")
for url, tab, label in [("/my/", "/my/", "My Feeders"), ("/my/claim", "/my/", "My Feeders"), ("/my/alerts", "/my/alerts", "Aircraft Alerts"), ("/my/profile", "/my/profile", "Profile"), ("/my/password", "/my/password", "Password")]:
    h = c.get(B + url).text
    menu = h.split('id="my-side-list"')[1].split("</script>")[0] if 'id="my-side-list"' in h else ""
    check(f"{url} shows the My Account side menu with {label} marked", f'<a class="active" href="{tab}" aria-current="page">{label}</a>' in menu and menu.count("<a ") >= 10, menu[:200])   # the claim and add forms are on the same page as each list, not entries of their own
check("My Feeders has the Claim a Feeder form on the same page", "Claim a Feeder" in c.get(B + "/my/").text and "Claim by UUID" in c.get(B + "/my/").text)
check("station page keeps My Feeders marked in the account menu", re.search(r'<a class="active" href="/my/"(?: aria-current="page")?>My Feeders</a>', c.get(B + "/my/feeders/pubaaaa1-0000-4000-8000-000000000001").text) is not None)
check("no leftover back-link buttons on My pages", "&larr; My Feeders" not in c.get(B + "/my/profile").text and "&larr; My Feeders" not in c.get(B + "/my/claim").text)
print("ALL PASS" if ok_all else "SOME FAILED")
