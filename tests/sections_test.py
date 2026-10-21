import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all=True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ")+name, "" if cond else extra); ok_all &= bool(cond)
adm=sess("marty", PW)
def sec(section, **kw):
    return act(adm, "/admin/settings/save", "/admin/settings", section=section, **kw)
def page(): return adm.get(B+"/admin/settings").text

h=page()
for slug in ("general","feeders","map-defaults","display","security","history","feeders-and-claiming","public-pages","map-tiles-and-weather","hospitals","air-ambulance","interesting-aircraft","places-and-search","address-finders","home-page-and-live-map","aircraft-lookups","accounts","alerts","backups"):
    check("collapsible section present: "+slug, f'<details class="panel setsec" id="sec-{slug}" ' in h)
check("the old long section is split up and its settings sit with what they belong to", 'id="sec-maps-and-data-sources"' not in h and all(f'id="sec-{x}"' in h for x in ("map-tiles-and-weather", "hospitals", "air-ambulance", "interesting-aircraft", "places-and-search", "address-finders")))
check("they have their own tab and an old link to the long section still lands on the map settings", 'data-tab="map-and-data"' in h and '"#sec-maps-and-data-sources": "#sec-map-tiles-and-weather"' in h)
check("each regrouped setting is in its own section", all(re.search(rf'<details class="panel setsec" id="sec-{sec}".*?name="{key}"', h, re.S) for sec, key in (("hospitals", "hospital_types"), ("address-finders", "photon_url"), ("places-and-search", "places_year"), ("air-ambulance", "hems_operators"), ("interesting-aircraft", "plane_alert_url"), ("map-tiles-and-weather", "map_tile_url"))))
check("each section has its own Save and Reset", h.count(">Reset</button>")==h.count('class="panel setsec"') and "Save Security</button>" in h)
check("section form posts its section name", 'name="section" value="security"' in h)

# one section saves only its own keys
ok,m,_=sec("security", max_failed_attempts="7", lockout_minutes="20"); check("save Security alone", ok and m=="Security saved.", m)
h=page()
check("Security values stored", 'name="max_failed_attempts" value="7"' in h and 'name="lockout_minutes" value="20"' in h)
check("other sections untouched", 'name="site_name" value="ArkSignal"' in h and 'name="history_retention_days" value="730"' in h)
ok,m,_=sec("security", max_failed_attempts="7", lockout_minutes="20"); check("unchanged section save reports no changes", ok and "No changes" in m, m)
ok,m,_=sec("security", max_failed_attempts="1", lockout_minutes="20"); check("reject bad value in a section", not ok, m)
check("rejected save changed nothing", 'name="max_failed_attempts" value="7"' in page())
ok,m,_=sec("security"); check("missing fields are rejected, not blanked", not ok, m)

# checkbox section: unchecked means off, and only that section is affected
ok,m,_=sec("general", site_name="ArkSignal"); check("General save with box unchecked turns registration off", ok and 'name="registration_open" value="1" checked' not in page(), m)
ok,m,_=sec("general", site_name="ArkSignal", registration_open="1"); check("General save with box checked turns it on", ok and 'name="registration_open" value="1" checked' in page(), m)

# table-driven section
ok,m,_=sec("accounts", account_delete_enabled="0"); check("save table-driven Accounts section", ok and m=="Accounts saved.", m)
check("Accounts value stored", '<option value="0" selected>Off</option>' in page().split('name="account_delete_enabled"')[1].split("</select>")[0])
ok,m,_=sec("accounts", account_delete_enabled="maybe"); check("reject invalid Accounts value", not ok, m)
ok,m,_=sec("accounts", account_delete_enabled="1"); check("restore Accounts", ok, m)

# map section: full-page zoom, decimals
ok,m,_=sec("map-defaults", map_lat="34.8", map_lon="-92.3", map_zoom="7", map_zoom_page="7.5"); check("save Map Defaults", ok, m)
ok,m,_=sec("map-defaults", map_lat="34.8", map_lon="-92.3", map_zoom="7", map_zoom_page="30"); check("reject map zoom 30", not ok, m)
ok,m,_=sec("map-defaults", map_lat="34.8", map_lon="-92.3", map_zoom="7", map_zoom_page="8"); check("restore map zoom", ok, m)

# unknown section, and the audit trail
ok,m,_=sec("nonsense"); check("unknown section rejected", not ok and "Unknown" in m, m)
ok,m,_=sec("security", max_failed_attempts="5", lockout_minutes="15"); check("restore Security", ok, m)
au=adm.get(B+"/admin/audit?action=settings.update").text
check("section saves are audited", "max_failed_attempts" in au, au[:200])
# the all-sections form still works (no section field)
check("settings page still loads", page().count("<details")>=13)
print("ALL PASS" if ok_all else "SOME FAILED")
