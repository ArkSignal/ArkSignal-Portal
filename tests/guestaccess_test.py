"""What needs a sign-in is not offered to guests: every flight and balloon download is for signed-in members only (a guest is sent to sign in, and the pages show 'Sign in to download' instead of the buttons), the menu
has no My ... pages and no Mesh Channels for a guest (and no empty headings), and the About page leaves out the features that need a sign-in (chat, Matrix, My Flights, private mesh channels, weather bridge)."""
import re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
for k in ("myflights_enabled", "mesh_enabled", "mesh_channels_enabled", "sonde_enabled", "acars_enabled", "rid_enabled", "chat_enabled", "flightlog_enabled", "export_enabled"):
    setting(k, "1")
psql("DELETE FROM settings WHERE key LIKE 'access_%'")
psql("DELETE FROM flight_log WHERE hex LIKE 'ec%'")
psql("DELETE FROM sondes WHERE serial LIKE 'ZZGA%'")
psql("INSERT INTO flight_log (hex, callsign, reg, type, first_seen, last_seen, start_lat, start_lon, end_lat, end_lon, max_alt, max_gs, positions, mil) VALUES ('ec0001', 'GST1', 'N1GST', 'B738', now() - interval '3 hours', now() - interval '2 hours', 34.5, -92.5, 35.5, -91.5, 31000, 450, 80, false)")
psql("INSERT INTO sondes (serial, kind, type, first_seen, last_seen, status, frames, max_alt) VALUES ('ZZGA1', 'radiosonde', 'RS41', now() - interval '5 hours', now() - interval '4 hours', 'landed', 60, 28800)")
guest, me = requests.Session(), sess("plain1", "plainpassword1")
def sent_to_sign_in(s, path):
    r = s.get(B + path, allow_redirects=False)
    return r.status_code in (302, 401, 403) and (r.status_code != 302 or "login" in r.headers.get("Location", ""))
for path in ("/history/export.csv?q=GST1&duration=24", "/history/export.kml?q=GST1&duration=24", "/history/export.geojson?q=GST1&duration=24", "/flight/ec0001/track.csv", "/flight/ec0001/track.gpx", "/flight/ec0001/track.kml", "/flight/ec0001/track.geojson", "/sondes/ZZGA1/track.csv", "/sondes/ZZGA1/track.geojson", "/my/flights/export.csv"):
    check(f"a guest cannot download {path.split('?')[0]}", sent_to_sign_in(guest, path), guest.get(B + path, allow_redirects=False).status_code)
check("a signed-in member can download a balloon track", me.get(B + "/sondes/ZZGA1/track.csv").status_code in (200, 404) and me.get(B + "/sondes/ZZGA1/track.csv", allow_redirects=False).status_code != 302)
pg = guest.get(B + "/sondes/ZZGA1").text
check("the balloon page offers a guest 'Sign in to download', not the buttons", "to download this track" in pg and "track.csv" not in pg)
check("a member sees the balloon download buttons", "track.csv" in me.get(B + "/sondes/ZZGA1").text)
hp = guest.get(B + "/history/?q=GST1&duration=24").text
check("Flight History offers a guest to sign in for the downloads", "Sign in" in hp and "/history/export.csv" not in hp)
lv = guest.get(B + "/live").text
check("the live map hides Export KML from guests (and the click does nothing)", 'if (!M.signedIn) $("c-kml").style.display = "none"' in lv and "if (!M.signedIn) return;" in lv)
home = guest.get(B + "/").text
hidden = ("My Feeders", "My Mesh Nodes", "Mesh Channels", "My ACARS Stations", "My Remote ID Stations", "My Sonde Stations", "My Weather Stations", "My Repeaters")
check("the menu shows a guest none of the My ... pages or Mesh Channels", not [x for x in hidden if x in home], [x for x in hidden if x in home])
check("a guest still sees the public pages in the menu", all(x in home for x in ("Live Map", "Flight History", "Mesh Map")))
mh = me.get(B + "/").text
check("a signed-in member sees My Feeders, My Mesh Nodes and Mesh Channels", all(x in mh for x in ("My Feeders", "My Mesh Nodes", "Mesh Channels")))
check("no empty 'Mine' heading is left for a guest", not re.search(r'<[^>]*>\s*Mine\s*<', home))
ab = guest.get(B + "/about").text
check("the About page leaves a guest out of the features that need a sign-in", not [x for x in ("Matrix Chat", "Chat, Messages and Friends", "AirTrail", "Segno", "ArkSignal Weather", "Sign-in alerts", "Airport time zones") if x in ab], [x for x in ("Matrix Chat", "Chat, Messages and Friends", "AirTrail", "Segno", "ArkSignal Weather", "Airport time zones") if x in ab])
check("but still credits the public sources", all(x in ab for x in ("Leaflet", "OpenStreetMap", "adsbdb", "Meshtastic")))
ab2 = me.get(B + "/about").text
check("a member's About page has those credits", all(x in ab2 for x in ("AirTrail", "Segno", "Airport time zones")))
psql("DELETE FROM flight_log WHERE hex LIKE 'ec%'")
# ---- heavy tools and the radio code plug exports are for members
for path in ("/view/planefence", "/view/plane-alert", "/pf/", "/repeaters/export", "/repeaters/export/chirp.csv"):
    check(f"a guest is sent to sign in for {path}", sent_to_sign_in(guest, path), guest.get(B + path, allow_redirects=False).status_code)
check("a member opens the Planefence and Plane-Alert pages and the code plug export", me.get(B + "/view/planefence", allow_redirects=False).status_code == 200 and me.get(B + "/view/plane-alert", allow_redirects=False).status_code == 200 and me.get(B + "/repeaters/export", allow_redirects=False).status_code in (200, 404))
check("the Plane-Alert list file is refused when it comes through the proxy without a sign-in, and a member can read it", guest.get(B + "/api/plane-alert-list.csv", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 401 and me.get(B + "/api/plane-alert-list.csv", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200)
check("the Planefence container on the stack's own network still reads it (no forwarded address)", guest.get(B + "/api/plane-alert-list.csv").status_code == 200)
home = guest.get(B + "/aircraft").text
check("the menu does not offer a guest Planefence or Plane-Alert, and the repeater list has no Code Plug Export for them", "/view/planefence" not in home and "/view/plane-alert" not in home and "Code Plug Export" not in guest.get(B + "/repeaters/").text)
check("a member's menu has them", "/view/planefence" in me.get(B + "/aircraft").text and "/view/plane-alert" in me.get(B + "/aircraft").text)
check("the About page does not mention Planefence among the things a guest can do", "Planefence and Plane-Alert, Sky View" not in guest.get(B + "/about").text and "Planefence and Plane-Alert, Sky View" in me.get(B + "/about").text)
# ---- features that only work for a signed-in person default to members only
adm = sess("marty", PW)
st = adm.get(B + "/admin/settings/site").text
def selected(key):
    m = re.search(r'<select[^>]*name="' + key + r'"[^>]*>(.*?)</select>', st, re.S)
    return re.search(r'<option value="([^"]+)"\s*selected', m.group(1)).group(1) if m and re.search(r'<option value="([^"]+)"\s*selected', m.group(1)) else None
check("Feature Access starts at signed-in members only for My Flights, Chat, Private Messages, Friends Lists, Matrix Chat and Radio Bridges", all(selected("access_" + k) == "members" for k in ("myflights", "chat", "messages", "friends", "matrix", "bridges")), {k: selected("access_" + k) for k in ("myflights", "chat", "messages", "friends", "matrix", "bridges")})
check("and stays at everyone for the features guests can use (Drones, Radiosondes, Mesh Nodes)", all(selected("access_" + k) == "everyone" for k in ("drones", "sondes", "mesh")), {k: selected("access_" + k) for k in ("drones", "sondes", "mesh")})
for path in ("/my/flights", "/chat/", "/my/friends"):
    check(f"a guest does not get {path}", sent_to_sign_in(guest, path) or guest.get(B + path, allow_redirects=False).status_code == 404, guest.get(B + path, allow_redirects=False).status_code)
def choices(key):
    m = re.search(r'<select[^>]*name="' + key + r'"[^>]*>(.*?)</select>', st, re.S)
    return re.findall(r'<option value="([^"]+)"', m.group(1)) if m else None
check("a feature that belongs to each member offers Every signed-in member or a role, never Everyone (My Flights, Chat, Messages, Friends, Matrix, Radio Bridges)", all(choices("access_" + k) == ["members", "roles"] for k in ("myflights", "chat", "messages", "friends", "matrix", "bridges")), {k: choices("access_" + k) for k in ("myflights", "chat", "friends")})
check("and says so in its own words, with the Super Admin sentence on every one", "Who Can Use My Flights" in st and "Every signed-in member" in st and st.count("Super Admin role") >= 20, st.count("Super Admin role"))
check("the other features keep Everyone, signed-in members and role", choices("access_drones") == ["everyone", "members", "roles"], choices("access_drones"))
setting("access_chat", "everyone"); setting("access_drones", "roles"); setting("access_myflights", "roles"); setting("rid_enabled", "1")
time.sleep(7)
check("a stored 'everyone' on a member feature still means members only (a guest is not let in)", sent_to_sign_in(guest, "/chat/") or guest.get(B + "/chat/", allow_redirects=False).status_code == 404)
check("the Super Admin role overrides the settings: it opens My Flights and Drones when they are limited to a role it was not given, and an ordinary member cannot", adm.get(B + "/my/flights", allow_redirects=False).status_code == 200 and adm.get(B + "/drones/", allow_redirects=False).status_code == 200 and me.get(B + "/my/flights", allow_redirects=False).status_code == 404 and me.get(B + "/drones/", allow_redirects=False).status_code == 404 and guest.get(B + "/drones/", allow_redirects=False).status_code in (302, 404))
psql("DELETE FROM settings WHERE key LIKE 'access_%'")
psql("DELETE FROM sondes WHERE serial LIKE 'ZZGA%'")
print("ALL PASS" if ok_all else "SOME FAILED")
