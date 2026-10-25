"""Drone Remote ID: the decoder reads messages built here to the ASTM F3411 layout (Basic ID, Location, Self ID, System, Operator ID and message packs), members add stations and send what they hear (raw hex or
decoded fields), drones appear on the list, the map data, the layer and their own pages, the area, the operator-location rule and the takedown list are kept to, and every shared page lists the service."""
import importlib.util, json, os, re, struct, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
MEM = sess("plain1", "plainpassword1")   # Planefence, the code plug exports, downloads and the My ... pages are for signed-in members
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def token(s, path):
    return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
AJAX = {"X-Requested-With": "XMLHttpRequest"}
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ridodecode", os.path.join(here, "..", "app", "ridodecode.py"))
rd = importlib.util.module_from_spec(spec); spec.loader.exec_module(rd)

# ---- building messages to the layout in the standard
def alt(v): return round((v + 1000) * 2)
def basic(ident, id_type=1, ua=2):
    return bytes([0x02, (id_type << 4) | ua]) + ident.encode().ljust(20, b"\0") + b"\0\0\0"
def location(lat, lon, alt_geo, height, speed, heading, vspeed, status=2):
    ew = 1 if heading >= 180 else 0
    mult = 1 if speed > 63.75 else 0
    spd = round((speed - 63.75) / 0.75) if mult else round(speed / 0.25)
    return struct.pack("<BBBBbiiHHHBBHBB", 0x12, (status << 4) | (ew << 1) | mult, int(heading) - (180 if ew else 0), spd, round(vspeed / 0.5), round(lat * 1e7), round(lon * 1e7), alt(alt_geo), alt(alt_geo), alt(height), 0x33, 0x33, 600, 1, 0)
def system(op_lat, op_lon):
    return struct.pack("<BBiiHBHHBhIB", 0x42, 0, round(op_lat * 1e7), round(op_lon * 1e7), 1, 5, alt(200), alt(0), 0x11, alt(150), 123456, 0)
def opid(s): return bytes([0x52, 0]) + s.encode().ljust(20, b"\0") + b"\0\0\0"
def selfid(s): return bytes([0x32, 0]) + s.encode().ljust(23, b"\0")
def pack(*msgs): return bytes([0xF2, 25, len(msgs)]) + b"".join(msgs)
assert all(len(m) == 25 for m in (basic("X"), location(35, -92, 100, 50, 5, 90, 0), system(35, -92), opid("X"), selfid("X")))

# ---- the decoder
b = rd.decode(basic("1596F4A1B2C3D4E5F6", 1, 2))
check("a Basic ID message gives the ID, its type and the kind of aircraft", b == {"ua_type": 2, "id_type": 1, "id": "1596F4A1B2C3D4E5F6"}, b)
l = rd.decode(location(35.08123, -92.44567, 152.5, 48.0, 12.25, 225, -1.5, 2))
check("a Location message gives status, direction, speed, vertical speed, position and the three altitudes", l["status"] == 2 and l["heading"] == 225.0 and l["speed"] == 12.25 and l["vspeed"] == -1.5 and abs(l["lat"] - 35.08123) < 1e-6
      and abs(l["lon"] + 92.44567) < 1e-6 and l["alt_geo"] == 152.5 and l["alt_baro"] == 152.5 and l["height"] == 48.0, l)
fast = rd.decode(location(35, -92, 100, 50, 80.0, 45, 0))
check("a fast drone uses the speed multiplier and a heading under 180 uses no east-west flag", abs(fast["speed"] - 80.0) < 0.5 and fast["heading"] == 45.0, fast)
check("an unknown position, altitude and speed are left out", (lambda m: "lat" not in m and "alt_geo" not in m and "speed" not in m)(rd.decode(struct.pack("<BBBBbiiHHHBBHBB", 0x12, 0x20, 200, 255, 63, 0, 0, 0xFFFF, 0xFFFF, 0xFFFF, 0, 0, 0, 0, 0))))
check("System, Operator ID and Self ID messages are read", rd.decode(system(35.1, -92.5)).get("op_lat") == 35.1 and rd.decode(opid("USA-OP-12345"))["op_id"] == "USA-OP-12345" and rd.decode(selfid("Survey flight"))["desc"] == "Survey flight")
mp = rd.decode(pack(basic("DRONE0000000000001"), location(35.2, -92.3, 120, 30, 8, 90, 0), opid("USA-OP-1"), selfid("Mapping")))
check("a message pack is decoded and merged", mp["id"] == "DRONE0000000000001" and abs(mp["lat"] - 35.2) < 1e-6 and mp["op_id"] == "USA-OP-1" and mp["desc"] == "Mapping", mp)
check("hex with spaces, colons or 0x decodes and junk gives nothing", rd.decode_hex("0x" + basic("HEXTEST12345").hex()).get("id") == "HEXTEST12345" and rd.decode_hex(" ".join(f"{x:02x}" for x in basic("SPACED00001"))).get("id") == "SPACED00001" and rd.decode_hex("not hex") == {} and rd.decode(b"\x00" * 10) == {})
check("kinds and ID types are named", rd.UA_TYPES[2] == "Helicopter or multirotor" and rd.ID_TYPES[2] == "Registration ID" and rd.STATUS[2] == "In the air")

# ---- the service
for t in ("rid_stations", "rid_drones", "rid_positions", "rid_hidden"):
    psql(f"DELETE FROM {t}" if t != "rid_stations" else "DELETE FROM rid_stations")
for k in ("rid_enabled", "rid_area", "rid_store_operator"):
    psql(f"DELETE FROM settings WHERE key = '{k}'")
adm = sess("marty", PW); me = sess("plain1", "plainpassword1"); other = sess("claimer1", "claimpassword1"); anon = requests.Session()

g = anon.get(B + "/drones/")
check("Drones is public and says nothing has been heard yet", g.status_code == 200 and "Remote ID" in g.text and "none has been heard yet" in g.text and "Search and Filters" in g.text)
check("the main menu has a Drones group with the overview, the map, How to Feed and My Remote ID Stations", all(x in MEM.get(B + "/").text for x in ('href="/drones/"', 'href="/drones/map"', 'href="/feed/drones"', 'href="/my/drones"')))
check("My Remote ID Stations needs a sign-in and My Account has the tab", anon.get(B + "/my/drones", allow_redirects=False).status_code in (302, 401, 403) and 'href="/my/drones"' in me.get(B + "/my/").text)
t = token(me, "/my/drones")
r = me.post(B + "/my/drones/add", data={"csrf_token": t, "name": "Backyard receiver", "place": "Conway", "public": "1", "notes": "ESP32-S3"}, headers=AJAX)
m = re.search(r"/drones/push/([A-Za-z0-9_-]{20,})", r.json().get("html", ""))
check("a member adds a station and sees its private upload address", r.status_code == 200 and r.json()["ok"] and m, r.text[:200])
URL = B + "/drones/push/" + m.group(1)
check("only a fingerprint of the address is searchable and the address is sealed", psql(f"select count(*) from rid_stations where token_hash = '{m.group(1)}' or token_sealed = '{m.group(1)}'") == "0")
check("a name that is too short is refused", not me.post(B + "/my/drones/add", data={"csrf_token": t, "name": "X"}, headers=AJAX).json()["ok"])
check("a wrong address is refused", requests.post(B + "/drones/push/notarealaddressnotarealaddress", data=basic("X").hex()).status_code == 404)

# sending: a pack as hex, one drone as decoded fields, single messages joined by the transmitter's address
lines = [pack(basic("DRONE0000000000001", 1, 2), location(35.2, -92.3, 120, 30, 8, 90, 0.5), opid("USA-OP-1"), selfid("Mapping the creek"), system(35.19, -92.31)).hex(),
         json.dumps({"id": "JSONDRONE0000001", "lat": 35.3, "lon": -92.2, "alt": 90, "speed": 5, "heading": 270, "status": 1, "ua_type": 1}),
         json.dumps({"src": "aa:bb:cc:dd:ee:01", "raw": basic("SINGLES000000001", 2, 2).hex()}),
         json.dumps({"src": "aa:bb:cc:dd:ee:01", "raw": location(35.4, -92.1, 80, 20, 3, 180, 0).hex()}),
         json.dumps({"id": "OUTSIDE0000000001", "lat": 47.6, "lon": -122.3, "alt": 50, "status": 2})]
r = requests.post(URL, data="\n".join(lines), headers={"Content-Type": "text/plain"})
j = r.json()
check("a batch is accepted: items counted, drones inside the area updated, the one outside ignored", r.status_code == 200 and j["ok"] and j["items"] == 5 and j["drones"] == 3, j)
check("the drones are stored with their details", psql("select ua_type || '|' || op_id || '|' || description || '|' || status from rid_drones where drone_id = 'DRONE0000000000001'") == "2|USA-OP-1|Mapping the creek|2" and psql("select count(*) from rid_drones") == "3")
check("a position outside the area is not kept", psql("select count(*) from rid_drones where drone_id = 'OUTSIDE0000000001'") == "0")
check("a position message is joined to the ID the same transmitter sent before it", psql("select round(lat::numeric, 1) || '|' || id_type from rid_drones where drone_id = 'SINGLES000000001'") == "35.4|2")
check("the operator's location is not stored when the setting is off (the default)", psql("select op_lat is null from rid_drones where drone_id = 'DRONE0000000000001'") == "t")
time.sleep(6)   # the recent-drones answer is kept 5 seconds
ls = anon.get(B + "/drones/").text
check("the list shows the drones with ID, kind, status, altitude and speed", all(x in ls for x in ("DRONE0000000000001", "Helicopter or multirotor", "In the air", "394 ft", "18 mph", "JSONDRONE0000001", "On the ground", "Mapping the creek")), ls[:200])
check("the list filters by status and by search", "JSONDRONE0000001" not in anon.get(B + "/drones/?kind=air").text and "DRONE0000000000001" in anon.get(B + "/drones/?kind=air").text and "JSONDRONE0000001" in anon.get(B + "/drones/?q=json").text and "DRONE0000000000001" not in anon.get(B + "/drones/?q=json").text)
vp = anon.get(B + "/drones/DRONE0000000000001")
check("a drone has its own page with its details", vp.status_code == 200 and all(x in vp.text for x in ("DRONE0000000000001", "Helicopter or multirotor", "USA-OP-1", "Mapping the creek", "Backyard receiver")), vp.status_code)
check("an unknown drone and a bad ID are a 404", anon.get(B + "/drones/NOSUCHDRONE0001").status_code == 404 and anon.get(B + "/drones/a%3Cb").status_code == 404)
dj = anon.get(B + "/api/drones/drones.json").json()
check("the drones JSON for the map has them, with kinds", {v["id"] for v in dj["drones"]} == {"DRONE0000000000001", "JSONDRONE0000001", "SINGLES000000001"} and next(v for v in dj["drones"] if v["id"] == "DRONE0000000000001")["kind"] == "air")
check("the JSON never carries an operator position while the setting is off", all(v["op_lat"] is None for v in dj["drones"]))
tj = anon.get(B + "/api/drones/DRONE0000000000001.json").json()
check("a drone's trail has its position", len(tj["trail"]) == 1 and abs(tj["trail"][0][0] - 35.2) < 1e-4)
gj = anon.get(B + "/api/drones.geojson").json()
check("the Drones layer gives each drone as a dot colored by status with a card", len(gj["features"]) == 3 and {f["properties"]["KIND"] for f in gj["features"]} == {"air", "ground"} and all(f["properties"]["link"].startswith("/drones/") for f in gj["features"]))
check("the layer is in the layer list of every map (Radio group, off until chosen)", psql("select count(*) from map_layers where slug = 'drones' and kind = 'geojson'") == "1" and psql("select default_on from map_layers where slug = 'drones'") == "f")
mp_ = anon.get(B + "/drones/map")
check("the Drone Map is the same map page as the others", mp_.status_code == 200 and 'id="lvmap"' in mp_.text and "Drones" in mp_.text and 'value="/drones/map"' in mp_.text)

# the operator's location only when an administrator turns that on
setting("rid_store_operator", "1")
requests.post(URL, data=pack(basic("DRONE0000000000001", 1, 2), location(35.21, -92.31, 121, 31, 8, 90, 0), system(35.19, -92.31)).hex())
check("with the setting on, the operator's location is kept and shown on the drone's page", psql("select op_lat is not null from rid_drones where drone_id = 'DRONE0000000000001'") == "t" and "Operator position" in anon.get(B + "/drones/DRONE0000000000001").text)
psql("DELETE FROM settings WHERE key = 'rid_store_operator'")

# the takedown list and the area
ta = token(adm, "/admin/drones")
r = adm.post(B + "/admin/drones/hide", data={"csrf_token": ta, "drone_id": "JSONDRONE0000001", "reason": "pilot asked"}, headers=AJAX)
check("an administrator hides a drone ID: it leaves every page, list and layer at once", r.json()["ok"] and anon.get(B + "/drones/JSONDRONE0000001").status_code == 404 and "JSONDRONE0000001" not in anon.get(B + "/drones/").text
      and all(f["properties"]["link"] != "/drones/JSONDRONE0000001" for f in anon.get(B + "/api/drones.geojson").json()["features"]))
requests.post(URL, data=json.dumps({"id": "JSONDRONE0000001", "lat": 35.3, "lon": -92.2, "status": 2}))
check("and its broadcasts are no longer stored", psql("select count(*) from rid_positions where drone_id = 'JSONDRONE0000001'") == "0")
check("a bad drone ID is refused", not adm.post(B + "/admin/drones/hide", data={"csrf_token": ta, "drone_id": "a<b"}, headers=AJAX).json()["ok"])
check("shown again", adm.post(B + "/admin/drones/unhide", data={"csrf_token": ta, "drone_id": "JSONDRONE0000001"}, headers=AJAX).json()["ok"] and psql("select count(*) from rid_hidden") == "0")
setting("rid_area", "10,-10,11,-9")
requests.post(URL, data=json.dumps({"id": "AREATEST00000001", "lat": 35.3, "lon": -92.2, "status": 2}))
check("with the area changed, a drone outside it is ignored", psql("select count(*) from rid_drones where drone_id = 'AREATEST00000001'") == "0")
psql("DELETE FROM settings WHERE key = 'rid_area'")
r = requests.post(URL, data="garbage\n\n12 34\n{not json\n")
check("garbage is ignored without an error", r.status_code == 200 and r.json()["drones"] == 0)

# the owner's pages and the administrator
rid = psql("select id from rid_stations limit 1")
check("nobody else can change or delete a member's station", other.post(B + f"/my/drones/{rid}/delete", data={"csrf_token": token(other, "/my/drones")}, headers=AJAX).status_code == 404)
r = adm.post(B + f"/admin/drones/{rid}/enabled", data={"csrf_token": ta, "on": "0"}, headers=AJAX)
check("a station switched off cannot send", r.json()["ok"] and requests.post(URL, data=json.dumps({"id": "OFFTEST000000001", "lat": 35.3, "lon": -92.2})).status_code == 404)
adm.post(B + f"/admin/drones/{rid}/enabled", data={"csrf_token": ta, "on": "1"}, headers=AJAX)
r = adm.post(B + "/admin/drones/add", data={"csrf_token": ta, "username": "claimer1", "name": "Club receiver", "place": "Little Rock", "public": "1"}, headers=AJAX)
check("an administrator adds a station for a member, who sees it", r.json()["ok"] and "Club receiver" in other.get(B + "/my/drones").text)
r = me.post(B + f"/my/drones/{rid}/newaddress", data={"csrf_token": token(me, "/my/drones")}, headers=AJAX)
newtok = re.search(r"/drones/push/([A-Za-z0-9_-]{20,})", r.json()["html"]).group(1)
check("a new upload address stops the old one and the new one works", requests.post(URL, data=json.dumps({"id": "NEWADDR000000001", "lat": 35.3, "lon": -92.2})).status_code == 404 and requests.post(B + "/drones/push/" + newtok, data=json.dumps({"id": "NEWADDR000000001", "lat": 35.3, "lon": -92.2})).status_code == 200)
r = me.post(B + f"/my/drones/{rid}/save", data={"csrf_token": token(me, "/my/drones"), "name": "Backyard receiver", "place": "Conway", "public": "0"}, headers=AJAX)
check("a station can hide its name from the drone pages", r.json()["ok"] and "Backyard receiver" not in anon.get(B + "/drones/DRONE0000000000001").text)

# every shared page
fp = anon.get(B + "/feed/drones")
check("a How to Feed page exists for Remote ID, with the short version, what you need, software setup, troubleshooting and privacy", fp.status_code == 200 and all(x in fp.text for x in ("How to", "The Short Version", "What You Need", "Software Setup", "Troubleshooting", "Your Data and Privacy", "rid_bridge.py", "/drones/push/YOUR-KEY", "ESP32")))
check("the Feed menu and every other feeding page list it", 'href="/feed/drones"' in anon.get(B + "/feed/").text and all('href="/feed/drones"' in anon.get(B + p).text for p in ("/feed/acars", "/feed/weather", "/feed/satellites", "/feed/ais", "/feed/check")))
br = anon.get(B + "/static/rid_bridge.py")
check("the bridge script is served and is valid Python", br.status_code == 200 and compile(br.text, "rid_bridge.py", "exec") is not None)
chk = me.get(B + "/feed/check?check=1").text
check("Am I Feeding? lists the member's Remote ID stations with the last report", "Your Remote ID Stations" in chk and "Backyard receiver" in chk and "last report" in chk)
check("the About page, Service Status, the admin Overview and the registry have Drones", "Drones" in anon.get(B + "/about").text and '"key": "drones"' in adm.get(B + "/admin/status/data.json").text.replace('"key":"', '"key": "') and "Drones heard lately" in adm.get(B + "/admin/").text
      and any(x["key"] == "drones" and x["feed"] and x["feed"][1] == "/feed/drones" for x in adm.get(B + "/admin/services.json").json()["services"]))
check("the admin page lists stations and the takedown list", all(x in adm.get(B + "/admin/drones").text for x in ("Backyard receiver", "Takedown List", "Add a Station for a Member")) and 'href="/admin/drones"' in adm.get(B + "/admin/").text)
check("without the permission the admin page is refused", other.get(B + "/admin/drones", allow_redirects=False).status_code in (302, 401, 403, 404))
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("Drone Remote ID", "Drones: Stations per Member", "Drones: Area Kept", "Drones: Hours of Trail Kept", "Drones: Keep the Operator", "Drone Map Opens On")))
au = adm.get(B + "/admin/audit").text
check("every action is in the audit log", all(x in au for x in ("rid.add", "rid.admin_add", "rid.hide", "rid.unhide", "rid.admin_switch", "rid.newaddress", "rid.update")))
check("the tables are in a restore area", "rid_drones" in open("../app/restorepart.py", encoding="utf-8").read())
setting("rid_enabled", "0")
check("switched off, the pages and the upload address are gone", anon.get(B + "/drones/").status_code == 404 and requests.post(B + "/drones/push/" + newtok, data=json.dumps({"id": "OFF0000000000001"})).status_code == 404 and not anon.get(B + "/api/drones.geojson").json()["features"])
psql("DELETE FROM settings WHERE key = 'rid_enabled'")
for t_ in ("rid_drones", "rid_positions", "rid_hidden", "rid_stations"):
    psql(f"DELETE FROM {t_}")
print("ALL PASS" if ok_all else "SOME FAILED")
