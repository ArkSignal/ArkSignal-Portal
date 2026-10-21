"""Feature access: an administrator can hide a feature from visitors, or limit it to signed-in members, or to members whose role has its See permission. Everything that shows the feature goes with it (menus,
the Feed menu and the feeding page, the About page, the pages and data routes, the map layers); people who run it always see it; uploads to a private address and API keys are judged on their own."""
import json, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def unset(*ks):
    for k in ks:
        psql(f"DELETE FROM settings WHERE key = '{k}'")
AJAX = {"X-Requested-With": "XMLHttpRequest"}
ACC = ("access_drones", "access_api", "access_weather")
unset(*ACC)
psql("DELETE FROM roles WHERE name = 'Drone Watchers'")
adm = sess("marty", PW); me = sess("plain1", "plainpassword1"); other = sess("claimer1", "claimpassword1"); anon = requests.Session()
def seen(s):
    """What a visitor gets for the Drones feature: the overview, the map, the feeding page, the data, the menu, the Feed menu and the About page."""
    home = s.get(B + "/").text
    return {"overview": s.get(B + "/drones/").status_code, "map": s.get(B + "/drones/map").status_code, "feed": s.get(B + "/feed/drones").status_code, "geo": len(s.get(B + "/api/drones.geojson").json()["features"]) if s.get(B + "/api/drones.geojson").status_code == 200 else -1,
            "json": s.get(B + "/api/drones/drones.json").status_code, "menu": 'href="/drones/"' in home, "feedmenu": 'href="/feed/drones"' in s.get(B + "/feed/").text, "about": "Remote ID" in s.get(B + "/about").text, "layer": '"slug": "drones"' in s.get(B + "/weather/map").text.replace('"slug":"', '"slug": "')}

# a station and a drone to look at, made as an administrator
psql("DELETE FROM rid_drones"); psql("DELETE FROM rid_stations")
ta = re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + "/admin/drones").text).group(1)
r = adm.post(B + "/admin/drones/add", data={"csrf_token": ta, "username": "", "name": "Access test receiver", "place": "Conway", "public": "1"}, headers=AJAX)
tok = re.search(r"/drones/push/([A-Za-z0-9_-]{20,})", r.json()["html"]).group(1)
URL = B + "/drones/push/" + tok
requests.post(URL, data=json.dumps({"id": "ACCESSTEST000001", "lat": 35.2, "lon": -92.3, "alt": 100, "status": 2, "ua_type": 2}))
import time; time.sleep(6)

base = seen(anon)
check("by default everyone sees the feature everywhere", base["overview"] == 200 and base["map"] == 200 and base["feed"] == 200 and base["geo"] == 1 and base["menu"] and base["feedmenu"] and base["about"] and base["layer"], base)
check("the Roles page has the See Drones permission and Settings has Who Can See Drones", "See Drones" in adm.get(B + "/admin/roles").text and "Feature Access: What This Role Can See" in adm.get(B + "/admin/roles").text and "Who Can See Drones" in adm.get(B + "/admin/settings").text)

# signed-in members only
setting("access_drones", "members")
a, m_ = seen(anon), seen(me)
check("limited to signed-in members, a visitor sees none of it: the pages, the data, the menus, the About page and the map layer are gone", a["overview"] == 404 and a["map"] == 404 and a["feed"] == 404 and a["geo"] == 0 and a["json"] == 404 and not a["menu"] and not a["feedmenu"] and not a["about"] and not a["layer"], a)
check("and a signed-in member sees all of it", m_["overview"] == 200 and m_["map"] == 200 and m_["feed"] == 200 and m_["geo"] == 1 and m_["menu"] and m_["feedmenu"] and m_["layer"], m_)
check("the sign-in page still works and a visitor is not told the feature exists (a plain 404)", anon.get(B + "/my/login").status_code == 200 and "Drones" not in anon.get(B + "/radio/").text.replace("Radio", ""))

# only roles that have the permission
setting("access_drones", "roles")
check("limited to roles, a member without the permission sees none of it", seen(me)["overview"] == 404 and not seen(me)["menu"] and seen(other)["feed"] == 404)
check("an administrator who runs the feature still sees it", seen(adm)["overview"] == 200 and seen(adm)["menu"] and adm.get(B + "/admin/drones").status_code == 200)
psql("INSERT INTO roles (name, description, is_super_admin) VALUES ('Drone Watchers', 'test role', FALSE)")
rid = psql("select id from roles where name = 'Drone Watchers'")
psql(f"INSERT INTO role_permissions (role_id, permission) VALUES ({rid}, 'use_drones')")
psql(f"INSERT INTO user_roles (user_id, role_id) SELECT id, {rid} FROM users WHERE username = 'plain1'")
check("a member whose role has See Drones sees all of it, and another member still does not", seen(me)["overview"] == 200 and seen(me)["menu"] and seen(me)["feedmenu"] and seen(me)["geo"] == 1 and seen(other)["overview"] == 404, (seen(me), seen(other)["overview"]))
check("the My Account page of the feature follows the same rule", me.get(B + "/my/drones").status_code == 200 and other.get(B + "/my/drones").status_code == 404)

# machines are not signed-in people
r = requests.post(URL, data=json.dumps({"id": "ACCESSTEST000002", "lat": 35.21, "lon": -92.31, "status": 2}))
check("a receiver posting to its private address still works while the feature is hidden from visitors", r.status_code == 200 and r.json()["drones"] == 1 and psql("select count(*) from rid_drones where drone_id = 'ACCESSTEST000002'") == "1", r.text[:200])

# the API judges the key's owner
setting("access_drones", "everyone")
tk = re.search(r'name="csrf_token" value="([^"]+)"', me.get(B + "/my/api-keys").text).group(1)
rk = me.post(B + "/my/api-keys/create", data={"csrf_token": tk, "name": "Access test", "note": ""}, headers=AJAX)
key = re.search(r"(ask_[A-Za-z0-9_-]{20,})", rk.json().get("html", "")).group(1)
H = {"Authorization": "Bearer " + key}
check("an API key works by default", requests.get(B + "/api/v1/me", headers=H).status_code == 200 and requests.get(B + "/api/v1/weather", headers=H).status_code in (200, 503))
psql("DELETE FROM user_roles WHERE role_id = " + rid)
setting("access_weather", "roles")
r1, r2 = requests.get(B + "/api/v1/weather", headers=H), requests.get(B + "/api/v1/me", headers=H)
check("with Weather limited to roles, the key of a member without the permission is refused for that endpoint only", r1.status_code == 403 and r1.json()["error"] == "not_allowed" and r2.status_code == 200, (r1.status_code, r1.text[:150], r2.status_code))
setting("access_api", "roles")
check("with the API limited to roles, the same key is refused everywhere", requests.get(B + "/api/v1/me", headers=H).status_code == 403 and me.get(B + "/api/docs").status_code == 404 and me.get(B + "/my/api-keys").status_code == 404)
psql(f"INSERT INTO user_roles (user_id, role_id) SELECT id, {rid} FROM users WHERE username = 'plain1'")
psql(f"INSERT INTO role_permissions (role_id, permission) VALUES ({rid}, 'use_api')")
check("a role with See API opens it again", requests.get(B + "/api/v1/me", headers=H).status_code == 200 and me.get(B + "/api/docs").status_code == 200)

# a value that is not a policy is treated as everyone; clean up
setting("access_drones", "nonsense")
check("an unknown policy value means everyone", seen(anon)["overview"] == 200)
unset(*ACC)
psql("DELETE FROM api_keys"); psql("DELETE FROM user_roles WHERE role_id = " + rid); psql("DELETE FROM roles WHERE name = 'Drone Watchers'"); psql("DELETE FROM rid_drones"); psql("DELETE FROM rid_stations")
print("ALL PASS" if ok_all else "SOME FAILED")
