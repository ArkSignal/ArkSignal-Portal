"""Granular roles: permissions per service (weather, radiosondes, map data), an administrator limited to one of them sees and opens only that, the broad permissions still include
the narrow ones (so roles made before the split keep working), and nobody can grant a permission they do not hold."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout
def psql(sql): return ssh(D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
def csrf(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
adm = sess("marty", PW)

def member(name):
    try:
        return sess(name, name + "password1")
    except AssertionError:
        s0 = requests.Session()
        s0.post(B + "/my/register", data={"csrf_token": csrf(s0, "/my/register"), "username": name, "email": name + "@example.com", "password": name + "password1", "confirm": name + "password1"})
        return sess(name, name + "password1")

for r in ("RS Weather Admin", "RS Weather Admin 2", "RS Sonde Admin", "RS Map Data", "RS Feeders Broad", "RS Settings Broad", "RS Roles Editor"):
    psql(f"delete from roles where name='{r}'")
pg = adm.get(B + "/admin/roles").text
check("the Roles page offers the new permissions", all(x in pg for x in ("Manage Weather Stations", "Manage Radiosondes", "Manage Map Data", "Moderate Repeaters")))

def make(role, perms, user):
    ok, msg, html = act(adm, "/admin/roles/create", "/admin/roles", name=role)
    rid = int(re.search(r'id="role-(\d+)"[^>]*>\s*<summary>\s*<span class="role-name">' + re.escape(role), html).group(1))
    ok2, msg2, _ = act(adm, f"/admin/roles/{rid}/save", "/admin/roles", name=role, description="test", permissions=perms)
    s = member(user)
    uid = psql(f"select id from users where username='{user}'")
    ok3, msg3, _ = act(adm, f"/admin/users/{uid}/roles", "/admin/users", action="assign", role_id=rid)
    assert ok and ok2 and ok3, (msg, msg2, msg3)
    return member(user)

w = make("RS Weather Admin", ["manage_weather"], "rsweather")
s_ = make("RS Sonde Admin", ["manage_sondes"], "rssonde")
m = make("RS Map Data", ["manage_map_data"], "rsmap")
fb = make("RS Feeders Broad", ["manage_feeders"], "rsfeed")
sb = make("RS Settings Broad", ["manage_settings"], "rsset")

def can(s, path): return s.get(B + path).status_code == 200
check("the weather administrator opens Weather Stations, not Radiosondes, Feeders, Airports or Settings", can(w, "/admin/weather") and not can(w, "/admin/sondes") and not can(w, "/admin/feeders") and not can(w, "/admin/airports") and not can(w, "/admin/settings"))
check("the radiosonde administrator opens Radiosondes only", can(s_, "/admin/sondes") and not can(s_, "/admin/weather") and not can(s_, "/admin/feeders") and not can(s_, "/admin/hospitals"))
check("the map data administrator opens the map data pages and Data Updates, not Settings, Weather or Feeders", all(can(m, p) for p in ("/admin/airports", "/admin/places", "/admin/hospitals", "/admin/air-ambulance", "/admin/interesting", "/admin/layers", "/admin/data-updates/")) and not can(m, "/admin/settings") and not can(m, "/admin/weather") and not can(m, "/admin/feeders"))
check("they all see the admin area, each with only their own pages in the menu", can(w, "/admin/") and 'href="/admin/weather"' in w.get(B + "/admin/").text and 'href="/admin/sondes"' not in w.get(B + "/admin/").text and 'href="/admin/settings"' not in w.get(B + "/admin/").text, re.findall(r'href="(/admin/[^"]*)"', w.get(B + "/admin/").text))
check("the member search works for a weather or radiosonde administrator, not for the map data one", w.get(B + "/admin/members/search.json", params={"q": "rs"}).status_code == 200 and s_.get(B + "/admin/members/search.json", params={"q": "rs"}).status_code == 200 and m.get(B + "/admin/members/search.json", params={"q": "rs"}).status_code == 404)
# actions, not just pages
tw = csrf(w, "/admin/weather")
r = w.post(B + "/admin/weather/add", data={"csrf_token": tw, "username": "rsweather", "kind": "relay", "station_id": "RSW-1", "lat": "34.8", "lon": "-92.3"}, allow_redirects=False)
check("the weather administrator can add a station for a member, and the radiosonde one cannot", r.status_code == 302 and psql("select count(*) from wx_stations where station_id='RSW-1'") == "1" and
      s_.post(B + "/admin/weather/add", data={"csrf_token": csrf(s_, "/admin/sondes"), "username": "rsweather", "kind": "relay", "station_id": "RSW-2", "lat": "1", "lon": "1"}, allow_redirects=False).status_code == 404 and psql("select count(*) from wx_stations where station_id='RSW-2'") == "0")
wid = psql("select id from wx_stations where station_id='RSW-1'")
check("the weather administrator manages that station with the owner's own page; the feeders-only role is not needed", can(w, f"/my/weather/{wid}") and not can(s_, f"/my/weather/{wid}"))
H = {"X-Requested-With": "XMLHttpRequest"}
j = s_.post(B + "/admin/sondes/assign", data={"csrf_token": csrf(s_, "/admin/sondes"), "callsign": "RSSONDE-1", "username": "rssonde", "mode": "assign"}, headers=H).json()
check("the radiosonde administrator can assign a station; the weather one cannot", j["ok"] and w.post(B + "/admin/sondes/assign", data={"csrf_token": csrf(w, "/admin/weather"), "callsign": "RSSONDE-2", "username": "rssonde", "mode": "assign"}, headers=H).status_code == 404 and psql("select count(*) from sonde_stations where callsign='RSSONDE-2'") == "0")
# the broad permissions still include the narrow ones
check("Manage Feeders still opens Weather Stations and Radiosondes; Manage Settings still opens the map data pages", can(fb, "/admin/weather") and can(fb, "/admin/sondes") and can(fb, "/admin/feeders") and not can(fb, "/admin/airports") and can(sb, "/admin/airports") and can(sb, "/admin/data-updates/") and not can(sb, "/admin/weather"))
# nobody grants what they do not hold: a role editor who holds only Manage Roles and Manage Weather cannot hand out Manage Radiosondes
ok, msg, html = act(adm, "/admin/roles/create", "/admin/roles", name="RS Weather Admin 2")
rid2 = int(re.search(r'id="role-(\d+)"[^>]*>\s*<summary>\s*<span class="role-name">RS Weather Admin 2', html).group(1))
rr = make("RS Roles Editor", ["manage_roles", "manage_users", "manage_weather"], "rsroles")
ok, msg, _ = act(rr, f"/admin/roles/{rid2}/save", "/admin/roles", name="RS Weather Admin 2", description="", permissions=["manage_sondes"])
check("a role editor cannot grant a permission they do not hold", not ok, msg)
ok, msg, _ = act(rr, f"/admin/roles/{rid2}/save", "/admin/roles", name="RS Weather Admin 2", description="", permissions=["manage_weather"])
check("but can grant one they do hold", ok, msg)
# clean up
psql("delete from wx_stations where station_id like 'RSW-%'")
psql("delete from sonde_stations where callsign like 'RSSONDE-%'")
for r in ("RS Weather Admin", "RS Weather Admin 2", "RS Sonde Admin", "RS Map Data", "RS Feeders Broad", "RS Settings Broad", "RS Roles Editor"):
    psql(f"delete from roles where name='{r}'")
print("ALL PASS" if ok_all else "SOME FAILED")
