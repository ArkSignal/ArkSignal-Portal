"""ONE units choice for the whole site: imperial by default (Settings > Units (Site Default)), and each person's own choice in the Display menu (imperial, aeronautical, metric) changes every
page, the admin settings included. Checked on the balloon flight page (server-written figures), the shared script, the Display menu and the weather pages."""
import re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def psql(sql): return subprocess.run(["ssh", "-q", "nastemp", D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def csrf(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
adm = sess("marty", PW)
try:
    me = sess("plain1", "plainpassword1")
except AssertionError:
    s0 = requests.Session()
    s0.post(B + "/my/register", data={"csrf_token": csrf(s0, "/my/register"), "username": "plain1", "email": "plain1@example.com", "password": "plainpassword1", "confirm": "plainpassword1"})
    me = sess("plain1", "plainpassword1")
anon = requests.Session()
psql("delete from settings where key='live_units'"); psql("delete from sondes where serial='UNITTEST1'")
psql("insert into settings (key, value) values ('units_sondes', '') on conflict (key) do update set value=''")   # radiosondes follow the standard unit for this test; their own default (metric) is checked at the end
psql("insert into sondes (serial, kind, type, first_seen, last_seen, status, last_lat, last_lon, last_alt, landing_lat, landing_lon, landing_alt, launch_lat, launch_lon, launch_alt, launch_time, max_alt, max_alt_ts, agency) values "
     "('UNITTEST1','radiosonde','RS41', now() - interval '3 hours', now() - interval '30 minutes','landed', 35.4, -91.8, 150, 35.4, -91.8, 150, 34.8, -92.3, 170, now() - interval '3 hours', 30000, now() - interval '1 hour', 'NWS')")
time.sleep(6)
def page(s): return s.get(B + "/sondes/UNITTEST1").text
def tiles(h): return re.sub(r"<[^>]+>", " ", h[h.index('class="tiles"'):h.index('class="tiles"') + 2600])
t = tiles(page(anon))
check("by default the flight page uses feet, ft/min and miles", "ft" in t and "ft/min" in t and " mi" in t and "m/s" not in t and " km" not in t, t[:400])
for who, s in (("a signed-in member with no choice", me),):
    check(f"{who} gets the site default", "ft/min" in tiles(page(s)))
H = {"X-CSRFToken": csrf(me, "/my/"), "Content-Type": "application/json"}
me.post(B + "/my/prefs", json={"units": "metric"}, headers=H)
t = tiles(page(me))
check("a member who chooses metric sees meters, m/s and kilometers", " m" in t and "m/s" in t and " km" in t and "ft/min" not in t, t[:400])
check("and anyone else still sees feet and miles", "ft/min" in tiles(page(anon)))
me.post(B + "/my/prefs", json={"units": "aero"}, headers=H)
t = tiles(page(me))
check("aeronautical gives feet and nautical miles", " ft" in t and " nm" in t and "ft/min" in t and " mi " not in t, t[:400])
me.post(B + "/my/prefs", json={"units": "bogus"}, headers=H)
check("a value that is not one of the three is ignored", "nm" in tiles(page(me)))
me.post(B + "/my/prefs", json={"units": ""}, headers=H)
check("clearing it returns to the site default", "ft/min" in tiles(page(me)) and " nm" not in tiles(page(me)))
# the site default can be changed
psql("insert into settings (key, value) values ('live_units','metric') on conflict (key) do update set value='metric'"); time.sleep(6)
check("when the site default is metric, people with no choice see metric and the admin distance settings follow", "m/s" in tiles(page(anon)) and "(kilometers)" in adm.get(B + "/admin/settings").text.lower() or "(kilometres)" in adm.get(B + "/admin/settings").text.lower())
psql("delete from settings where key='live_units'"); time.sleep(6)
# the shared script, the menu and the settings page
home = anon.get(B + "/").text
check("every page loads the shared units script and knows the site default", '/static/arkunits.js' in home and 'window.SITE_UNITS = "imperial"' in home)
check("the Display menu has the Units choice with all three sets", all(x in home for x in ('id="pf-units"', "Feet, mph, miles", "Feet, knots, nautical miles", "Meters, km/h, kilometers")))
js = anon.get(B + "/static/arkunits.js").text
check("the script has the three sets", all(x in js for x in ("imperial:", "aero:", "metric:", "ft/min", "km/h", "nm")))
sp = adm.get(B + "/admin/settings").text
check("the site default is one setting, named Units (Site Default), and the old separate distance setting is gone", "Units (Site Default)" in sp and "Distances in Admin Settings" not in sp)
# weather follows too
me.post(B + "/my/prefs", json={"units": "metric"}, headers=H)
check("the weather pages follow the person's units (metric: degrees C)", "&deg;C" in me.get(B + "/weather/").text or "°C" in me.get(B + "/weather/").text)
me.post(B + "/my/prefs", json={"units": ""}, headers=H)
check("and show degrees F by default", "°F" in anon.get(B + "/weather/").text or "&deg;F" in anon.get(B + "/weather/").text)
ap = anon.get(B + "/aircraft").text
check("the radar caption on the aircraft page gives the ring spacing in nautical miles by default (aviation)", re.search(r"Rings every \d+ nm", ap) is not None, re.findall(r"Rings every[^&<]*", ap))
me.post(B + "/my/prefs", json={"units": "imperial"}, headers=H)
check("and in miles for the imperial set", re.search(r"Rings every \d+ mi", me.get(B + "/aircraft").text) is not None)
me.post(B + "/my/prefs", json={"units": ""}, headers=H)
psql("delete from settings where key='units_sondes'"); time.sleep(3)
t = tiles(page(anon))
check("with the site's own default for radiosondes (metric, as on SondeHub) the flight page shows meters, m/s and kilometers to anyone who has not chosen", "m/s" in t and " km" in t and "ft/min" not in t, t[:300])
fl = re.sub(r"<[^>]+>", " ", anon.get(B + "/sondes/flights?q=UNITTEST1&hours=0").text)
check("the balloon flights list shows the highest point in the balloon units (meters) by default", "30,000 m" in fl and "98,425 ft" not in fl, fl[fl.find("UNITTEST1"):][:200])
home_t = re.sub(r"<[^>]+>", " ", anon.get(B + "/").text)
check("the home page's radiosonde figure is in balloon units too", "ft" not in home_t.split("RADIOSONDES")[-1][:200] if "RADIOSONDES" in home_t else True)
psql("delete from sondes where serial='UNITTEST1'")
print("ALL PASS" if ok_all else "SOME FAILED")
