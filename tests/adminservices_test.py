"""The admin side by service: the menu groups (a service's pages and its Settings page together, an Updates group), one Settings page per service showing only its sections, the page with every
setting still complete, saving returns to the page it came from, and the search box on every admin page finds a setting anywhere."""
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)

adm = sess("marty", PW)
o = adm.get(B + "/admin/").text
for g in ("Aircraft and Feeders", "Weather and Balloons", "Radio", "Map and Data", "Updates", "Site"):
    check(f"the admin menu has the group {g}", f'<span>{g}</span><span class="my-side-caret"' in o, g)
check("Data Updates, Software Updates and Backups are together under Updates", all(x in o for x in ("Data Updates", "Software Updates", "Backups")) and o.index('<span>Updates</span><span class="my-side-caret"') < o.index("Software Updates") < o.index('<span>Site</span><span class="my-side-caret"'))
for sv in ("aircraft", "weather", "radio", "mapdata", "updates", "site"):
    r = adm.get(B + "/admin/settings/" + sv)
    check(f"/admin/settings/{sv} opens", r.status_code == 200 and "Settings</h1>" in r.text, r.status_code)
check("an unknown service is a 404", adm.get(B + "/admin/settings/nonsense").status_code == 404)
radio = adm.get(B + "/admin/settings/radio").text
check("the Radio settings hold the repeater and APRS sections and nothing from other services", 'id="sec-repeaters"' in radio and 'id="sec-aprs"' in radio and 'id="sec-radiosondes"' not in radio and 'id="sec-security"' not in radio)
wx = adm.get(B + "/admin/settings/weather").text
check("Weather and Balloons holds weather stations, radiosondes and the prediction service", all(f'id="sec-{x}"' in wx for x in ("weather-stations", "radiosondes", "prediction-service")))
allp = adm.get(B + "/admin/settings").text
check("the page with every setting still has every section and every field", all(f'id="sec-{x}"' in allp for x in ("radiosondes", "security", "routing", "aprs", "general", "scheduled-updates")) and 'name="osrm_url"' in allp and 'name="site_name"' in allp)
check("each tab is a service on the all-settings page", all(l in allp for l in ("Aircraft and Feeders", "Weather and Balloons", "Radio", "Map and Data", "Updates", "Site")))
check("a service page's tabs are its own sections", 'data-tab="repeaters"' in radio or 'data-tab="aprs"' in radio)
check("an old link to a section opens the service page that holds it (script map)", "sec-routing" in allp and "/admin/settings/mapdata" in allp)

# search
r = adm.get(B + "/admin/settings/search.json?q=ring").json()["results"]
check("searching 'ring' finds the ring times setting with its service page and field", any(x["label"].startswith("Ring Times") and x["href"].startswith("/admin/settings/mapdata") and "field=osrm_ring_minutes" in x["href"] for x in r), r[:3])
r = adm.get(B + "/admin/settings/search.json?q=site+name").json()["results"]
check("a hand-built setting is found too (site name)", any("/admin/settings/site" in x["href"] for x in r), r[:3])
check("one letter finds nothing", adm.get(B + "/admin/settings/search.json?q=a").json()["results"] == [])
check("the search box is on every admin page", 'id="adminfind"' in adm.get(B + "/admin/users").text and 'id="adminfind"' in adm.get(B + "/admin/hospitals").text)
check("search and the service pages are closed to the public", requests.get(B + "/admin/settings/search.json?q=ring", allow_redirects=False).status_code in (302, 401, 403, 404) and requests.get(B + "/admin/settings/radio", allow_redirects=False).status_code in (302, 401, 403, 404))

# saving returns to the page it came from
tok = re.search(r'name="csrf_token" value="([^"]+)"', radio).group(1)
r = adm.post(B + "/admin/settings/save", data={"csrf_token": tok, "section": "repeaters", "service": "radio"}, allow_redirects=False)
check("saving a section on a service page returns to that service page", r.status_code in (302, 303) and r.headers.get("Location", "").endswith("/admin/settings/radio"), (r.status_code, r.headers.get("Location")))
print("ALL PASS" if ok_all else "SOME FAILED")
