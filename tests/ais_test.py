"""River Traffic (AIS): the decoder reads real sentences (checked against a published sample and against sentences built here), members add stations and send what they hear, vessels appear on the list,
the map data, the layer and their own pages, the area and the takedown list are kept to, and every shared page lists the service and its How to Feed page."""
import importlib.util, json, os, re, subprocess, time, requests
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
spec = importlib.util.spec_from_file_location("aisdecode", os.path.join(here, "..", "app", "aisdecode.py"))
ad = importlib.util.module_from_spec(spec); spec.loader.exec_module(ad)

# ---- building sentences (an encoder, so the decoder is tested on more than the one published sample)
def bits(v, n): return format(int(v) & ((1 << n) - 1), "0%db" % n)
def txt(s, n): return "".join(bits(ord(c) - 64 if ord(c) >= 64 else ord(c), 6) for c in s.upper().ljust(n, "@")[:n])
def armor(b):
    fill = (-len(b)) % 6
    b += "0" * fill
    out = ""
    for i in range(0, len(b), 6):
        c = int(b[i:i + 6], 2) + 48
        out += chr(c + 8 if c > 87 else c)
    return out, fill
def nmea(payload, fill, total=1, num=1, seq="", chan="A"):
    body = f"AIVDM,{total},{num},{seq},{chan},{payload},{fill}"
    x = 0
    for ch in body: x ^= ord(ch)
    return f"!{body}*{x:02X}"
def pos_a(mmsi, lat, lon, sog=0.0, cog=0.0, hdg=0, status=0):
    b = bits(1, 6) + bits(0, 2) + bits(mmsi, 30) + bits(status, 4) + bits(-128, 8) + bits(round(sog * 10), 10) + bits(0, 1) + bits(round(lon * 600000), 28) + bits(round(lat * 600000), 27) + bits(round(cog * 10), 12) + bits(hdg, 9) + bits(60, 6) + bits(0, 2) + bits(0, 3) + bits(0, 1) + bits(0, 19)
    p, f = armor(b); return nmea(p, f)
def static_a(mmsi, name, call, stype, bow, stern, port, star, dest, draught=2.4, imo=9123456):
    b = bits(5, 6) + bits(0, 2) + bits(mmsi, 30) + bits(0, 2) + bits(imo, 30) + txt(call, 7) + txt(name, 20) + bits(stype, 8) + bits(bow, 9) + bits(stern, 9) + bits(port, 6) + bits(star, 6) + bits(1, 4) + bits(0, 4) + bits(0, 5) + bits(24, 5) + bits(60, 6) + bits(round(draught * 10), 8) + txt(dest, 20) + bits(0, 1) + bits(0, 1)
    p, f = armor(b)
    return [nmea(p[:60], 0, 2, 1, "3"), nmea(p[60:], f, 2, 2, "3")]
def pos_b(mmsi, lat, lon, sog=0.0, cog=0.0):
    b = bits(18, 6) + bits(0, 2) + bits(mmsi, 30) + bits(0, 8) + bits(round(sog * 10), 10) + bits(0, 1) + bits(round(lon * 600000), 28) + bits(round(lat * 600000), 27) + bits(round(cog * 10), 12) + bits(511, 9) + bits(60, 6) + bits(0, 2) + bits(0, 7) + bits(0, 20)
    p, f = armor(b); return nmea(p, f)
def static_b(mmsi, name, call, stype):
    a = bits(24, 6) + bits(0, 2) + bits(mmsi, 30) + bits(0, 2) + txt(name, 20) + bits(0, 8)
    pa, fa = armor(a)
    bb = bits(24, 6) + bits(0, 2) + bits(mmsi, 30) + bits(1, 2) + bits(stype, 8) + bits(0, 42) + txt(call, 7) + bits(10, 9) + bits(3, 9) + bits(2, 6) + bits(2, 6) + bits(0, 6)
    pb, fb = armor(bb)
    return [nmea(pa, fa), nmea(pb, fb)]

# ---- the decoder
pub = "!AIVDM,1,1,,B,177KQJ5000G?tO`K>RA1wUbN0TKH,0*5C"
d0 = ad.decode(pub)
check("a published sample sentence decodes to its known values", ad.checksum_ok(pub) and d0["mmsi"] == 477553000 and d0["type"] == 1 and d0["status"] == 5 and d0["course"] == 51.0 and d0["heading"] == 181 and abs(d0["lat"] - 47.582833) < 1e-5 and abs(d0["lon"] + 122.345833) < 1e-5, d0)
check("a wrong check sum is refused", ad.decode(pub[:-2] + "00") is None and not ad.checksum_ok(pub[:-2] + "00") and ad.decode("not a sentence") is None)
m = ad.decode(pos_a(366123456, 34.7465, -92.2896, 5.4, 123.4, 120, 0))
check("a Class A position built here decodes back (place, speed, course, heading, status)", m and m["mmsi"] == 366123456 and abs(m["lat"] - 34.7465) < 1e-5 and abs(m["lon"] + 92.2896) < 1e-5 and m["speed"] == 5.4 and m["course"] == 123.4 and m["heading"] == 120 and m["status"] == 0, m)
m = ad.decode(pos_a(366123457, 91, 181))
check("a position that says it has no position gives none", m and m["lat"] is None and m["lon"] is None)
asm = ad.Assembler()
s1, s2 = static_a(366123456, "MISS QUEEN", "WDC1234", 31, 30, 100, 8, 8, "NEW ORLEANS", 2.4, 9123456)
check("a one-part sentence of a two-part message gives nothing until its partner comes", asm.feed(s1, 0) is None)
st = asm.feed(s2, 1)
check("the two parts of a voyage message join and decode (name, call sign, type, size, destination, draught)", st and st["name"] == "MISS QUEEN" and st["callsign"] == "WDC1234" and st["ship_type"] == 31 and st["to_bow"] == 30 and st["to_stern"] == 100 and st["to_port"] == 8
      and st["to_starboard"] == 8 and st["destination"] == "NEW ORLEANS" and abs(st["draught"] - 2.4) < 1e-9 and st["imo"] == 9123456, st)
m = ad.decode(pos_b(338123456, 35.1, -92.4, 7.3, 200.0))
check("a Class B position decodes", m and m["cls"] == "B" and m["speed"] == 7.3 and m["course"] == 200.0 and m["heading"] is None and abs(m["lat"] - 35.1) < 1e-5, m)
a2 = ad.Assembler()
sa, sb = static_b(338123456, "RIVER RAT", "WDD9999", 37)
na, nb = a2.feed(sa, 0), a2.feed(sb, 0)
check("a Class B craft's two static messages give its name, then its type, call sign and size", na and na["name"] == "RIVER RAT" and nb and nb["ship_type"] == 37 and nb["callsign"] == "WDD9999" and nb["to_bow"] == 10 and nb["to_stern"] == 3, (na, nb))
check("ship types and kinds are named", ad.ship_type_name(31) == "Towing" and ad.ship_type_name(79) == "Cargo" and ad.ship_type_name(89) == "Tanker" and ad.category(31) == "tow" and ad.category(52) == "tow" and ad.category(37) == "pleasure" and ad.category(0) == "unknown" and ad.category(60) == "passenger")

# ---- the service
psql("DELETE FROM ais_stations"); psql("DELETE FROM ais_vessels"); psql("DELETE FROM ais_positions"); psql("DELETE FROM ais_hidden")
for k in ("ais_enabled", "ais_area", "ais_hide_pleasure"):
    psql(f"DELETE FROM settings WHERE key = '{k}'")
adm = sess("marty", PW); me = sess("plain1", "plainpassword1"); other = sess("claimer1", "claimpassword1"); anon = requests.Session()

g = anon.get(B + "/ais/")
check("River Traffic is public and says nothing has been heard yet", g.status_code == 200 and "River Traffic" in g.text and "none has been heard yet" in g.text and "Search and Filters" in g.text)
check("the main menu has a River Traffic group with the overview, the map, How to Feed and My AIS Stations", all(x in MEM.get(B + "/").text for x in ('href="/ais/"', 'href="/ais/map"', 'href="/feed/ais"', 'href="/my/ais"')))
check("My AIS Stations needs a sign-in and My Account has the tab", anon.get(B + "/my/ais", allow_redirects=False).status_code in (302, 401, 403) and 'href="/my/ais"' in me.get(B + "/my/").text)
t = token(me, "/my/ais")
r = me.post(B + "/my/ais/add", data={"csrf_token": t, "name": "Pine Bluff river station", "place": "Arkansas River", "public": "1", "notes": "rtl_ais"}, headers=AJAX)
m = re.search(r"/ais/push/([A-Za-z0-9_-]{20,})", r.json().get("html", ""))
check("a member adds a station and sees its private upload address", r.status_code == 200 and r.json()["ok"] and m, r.text[:200])
URL = B + "/ais/push/" + m.group(1)
check("only a fingerprint of the address is searchable and the address is sealed", psql(f"select count(*) from ais_stations where token_hash = '{m.group(1)}' or token_sealed = '{m.group(1)}'") == "0")
check("a name that is too short is refused", not me.post(B + "/my/ais/add", data={"csrf_token": t, "name": "X"}, headers=AJAX).json()["ok"])
check("a wrong address is refused", requests.post(B + "/ais/push/notarealaddressnotarealaddress", data=pos_a(366123456, 34.7, -92.3)).status_code == 404)

lines = [pos_a(366123456, 34.7465, -92.2896, 5.4, 123.4, 120), *static_a(366123456, "MISS QUEEN", "WDC1234", 31, 30, 100, 8, 8, "NEW ORLEANS"), pos_b(338123456, 35.1, -92.4, 7.3, 200.0), *static_b(338123456, "RIVER RAT", "WDD9999", 37), pub]
r = requests.post(URL, data="\n".join(lines), headers={"Content-Type": "text/plain"})
j = r.json()
check("a batch of sentences is accepted: all sentences counted, vessels in the area updated, the one outside ignored", r.status_code == 200 and j["ok"] and j["sentences"] == len(lines) and j["vessels"] == 2, j)
check("the vessels are stored with their details", psql("select name || '|' || callsign || '|' || ship_type || '|' || destination from ais_vessels where mmsi = 366123456") == "MISS QUEEN|WDC1234|31|NEW ORLEANS" and psql("select count(*) from ais_vessels") == "2")
check("a position outside the area is not kept (the sample in Seattle)", psql("select count(*) from ais_vessels where mmsi = 477553000") == "0")
check("the station shows its message count and last report", int(psql("select msgs from ais_stations")) >= 6 and psql("select last_report is not null from ais_stations") == "t")
ls = anon.get(B + "/ais/").text
check("the list shows the vessels with name, MMSI, kind, speed, course and destination", all(x in ls for x in ("MISS QUEEN", "366123456", "Towing", "5.4 kn", "NEW ORLEANS", "RIVER RAT", "Pleasure craft")))
check("the list filters by kind and by search", "RIVER RAT" not in anon.get(B + "/ais/?cat=tow").text and "MISS QUEEN" in anon.get(B + "/ais/?cat=tow").text and "MISS QUEEN" in anon.get(B + "/ais/?q=new+orleans").text and "RIVER RAT" not in anon.get(B + "/ais/?q=new+orleans").text)
vp = anon.get(B + "/ais/366123456")
check("a vessel has its own page with its details", vp.status_code == 200 and all(x in vp.text for x in ("MISS QUEEN", "WDC1234", "130 m long", "NEW ORLEANS", "Towing", "Pine Bluff river station")), vp.status_code)
check("an unknown vessel is a 404", anon.get(B + "/ais/366999999").status_code == 404)
time.sleep(16)   # the recent-vessels answer is kept 15 seconds
vj = anon.get(B + "/api/ais/vessels.json").json()
check("the vessels JSON for the map has them, with kinds and sizes", {v["mmsi"] for v in vj["vessels"]} == {366123456, 338123456} and next(v for v in vj["vessels"] if v["mmsi"] == 366123456)["category"] == "tow" and next(v for v in vj["vessels"] if v["mmsi"] == 366123456)["length"] == 130)
tj = anon.get(B + "/api/ais/366123456.json").json()
check("a vessel's trail has its position", len(tj["trail"]) == 1 and abs(tj["trail"][0][0] - 34.7465) < 1e-4)
gj = anon.get(B + "/api/ais.geojson").json()
check("the Marine Traffic layer gives each vessel as a dot colored by kind with a card", len(gj["features"]) == 2 and {f["properties"]["KIND"] for f in gj["features"]} == {"tow", "pleasure"} and all(f["properties"]["link"].startswith("/ais/") for f in gj["features"]))
check("the layer is in the layer list of every map (Radio group, off until chosen)", psql("select count(*) from map_layers where slug = 'ais' and kind = 'geojson'") == "1" and psql("select default_on from map_layers where slug = 'ais'") == "f")
mp = anon.get(B + "/ais/map")
check("the River Traffic Map is the same map page as the others", mp.status_code == 200 and 'id="lvmap"' in mp.text and "Vessels" in mp.text and 'value="/ais/map"' in mp.text)

# more ways to send, a second request finishing a two-part message, the takedown list and the area
s1, s2 = static_a(366222222, "SECOND TOW", "WDX0002", 52, 20, 40, 6, 6, "LITTLE ROCK")
requests.post(URL, data=s1)
r = requests.post(URL, data="\n".join([s2, pos_a(366222222, 34.8, -92.2, 3.0, 90.0)]))
check("the two parts of a message can arrive in separate uploads", psql("select name from ais_vessels where mmsi = 366222222") == "SECOND TOW", r.text[:200])
r = requests.post(URL, data=json.dumps({"msgs": [pos_a(366333333, 34.9, -92.1, 1.0, 10.0)]}), headers={"Content-Type": "application/json"})
check("a JSON list of sentences is accepted", r.json()["ok"] and psql("select count(*) from ais_vessels where mmsi = 366333333") == "1")
r = requests.post(URL, data=json.dumps({"ships": [{"mmsi": 366444444, "lat": 35.0, "lon": -92.0, "speed": 4.5, "course": 45, "heading": 44, "shipname": "JSON BOAT", "shiptype": 70}]}), headers={"Content-Type": "application/json"})
check("AIS-catcher's JSON ship list is accepted", r.json()["ok"] and psql("select name || '|' || ship_type from ais_vessels where mmsi = 366444444") == "JSON BOAT|70")
r = requests.post(URL, data="garbage\n!AIVDM,1,1,,A,15M67FC000G?ufbE`>hCKaP00<0n,0*00\n\n")
check("garbage and bad check sums are ignored without an error", r.status_code == 200 and r.json()["messages"] == 0)
ta = token(adm, "/admin/ais")
r = adm.post(B + "/admin/ais/hide", data={"csrf_token": ta, "mmsi": "366333333", "reason": "owner asked"}, headers=AJAX)
check("an administrator hides a vessel: it leaves every page and list", r.json()["ok"] and anon.get(B + "/ais/366333333").status_code == 404 and "366333333" not in anon.get(B + "/ais/").text and all(f["properties"]["link"] != "/ais/366333333" for f in anon.get(B + "/api/ais.geojson").json()["features"]))
requests.post(URL, data=pos_a(366333333, 34.9, -92.1, 1.0, 10.0))
check("and its broadcasts are no longer stored", psql("select count(*) from ais_positions where mmsi = 366333333") == "0")
check("a bad MMSI is refused", not adm.post(B + "/admin/ais/hide", data={"csrf_token": ta, "mmsi": "123"}, headers=AJAX).json()["ok"])
r = adm.post(B + "/admin/ais/unhide/366333333", data={"csrf_token": ta}, headers=AJAX)
check("and shown again", r.json()["ok"] and psql("select count(*) from ais_hidden") == "0")
setting("ais_area", "10,-10,11,-9")
requests.post(URL, data=pos_a(366555555, 34.9, -92.1))
check("with the area changed, a vessel outside it is ignored", psql("select count(*) from ais_vessels where mmsi = 366555555") == "0")
psql("DELETE FROM settings WHERE key = 'ais_area'")
setting("ais_hide_pleasure", "1")
check("with pleasure craft hidden by the setting they leave the list and the map data at once", "RIVER RAT" not in anon.get(B + "/ais/").text and all(v["category"] != "pleasure" for v in anon.get(B + "/api/ais/vessels.json").json()["vessels"]))
psql("DELETE FROM settings WHERE key = 'ais_hide_pleasure'")

# the owner's pages and the administrator
check("nobody else can change or delete a member's station", other.post(B + f"/my/ais/{psql('select id from ais_stations limit 1')}/delete", data={"csrf_token": token(other, "/my/ais")}, headers=AJAX).status_code == 404)
rid = psql("select id from ais_stations limit 1")
r = adm.post(B + f"/admin/ais/{rid}/enabled", data={"csrf_token": ta, "on": "0"}, headers=AJAX)
check("a station switched off cannot send", r.json()["ok"] and requests.post(URL, data=pos_a(366666666, 34.9, -92.1)).status_code == 404)
adm.post(B + f"/admin/ais/{rid}/enabled", data={"csrf_token": ta, "on": "1"}, headers=AJAX)
r = adm.post(B + "/admin/ais/add", data={"csrf_token": ta, "username": "claimer1", "name": "Club station", "place": "Little Rock", "public": "1"}, headers=AJAX)
check("an administrator adds a station for a member, who sees it", r.json()["ok"] and "Club station" in other.get(B + "/my/ais").text)
r = me.post(B + f"/my/ais/{rid}/newaddress", data={"csrf_token": token(me, "/my/ais")}, headers=AJAX)
newtok = re.search(r"/ais/push/([A-Za-z0-9_-]{20,})", r.json()["html"]).group(1)
check("a new upload address stops the old one and the new one works", requests.post(URL, data=pos_a(366666666, 34.9, -92.1)).status_code == 404 and requests.post(B + "/ais/push/" + newtok, data=pos_a(366666666, 34.9, -92.1)).status_code == 200)
r = me.post(B + f"/my/ais/{rid}/save", data={"csrf_token": token(me, "/my/ais"), "name": "Pine Bluff river station", "place": "Arkansas River", "public": "0"}, headers=AJAX)
check("a station can hide its name from the vessel pages", r.json()["ok"] and "Pine Bluff river station" not in anon.get(B + "/ais/366123456").text)

# every shared page
fp = anon.get(B + "/feed/ais")
check("a How to Feed page exists for AIS, with the short version, what you need, software setup, troubleshooting and privacy", fp.status_code == 200 and all(x in fp.text for x in ("How to", "The Short Version", "What You Need", "Software Setup", "Troubleshooting", "Your Data and Privacy", "ais_bridge.py", "/ais/push/YOUR-KEY", "AIS-catcher")))
check("the Feed menu and every other feeding page list it", 'href="/feed/ais"' in anon.get(B + "/feed/").text and all('href="/feed/ais"' in anon.get(B + p).text for p in ("/feed/acars", "/feed/weather", "/feed/satellites", "/feed/check")))
br = anon.get(B + "/static/ais_bridge.py")
check("the bridge script is served and is valid Python", br.status_code == 200 and compile(br.text, "ais_bridge.py", "exec") is not None)
chk = me.get(B + "/feed/check?check=1").text
check("Am I Feeding? lists the member's AIS stations with the last report", "Your AIS Stations" in chk and "Pine Bluff river station" in chk and "last report" in chk)
check("the About page, Service Status, the admin Overview and the registry have River Traffic", "River Traffic" in anon.get(B + "/about").text and '"key": "ais"' in adm.get(B + "/admin/status/data.json").text.replace('"key":"', '"key": "') and "AIS vessels heard lately" in adm.get(B + "/admin/").text
      and any(x["key"] == "ais" and x["feed"] and x["feed"][1] == "/feed/ais" for x in adm.get(B + "/admin/services.json").json()["services"]))
check("the admin page lists stations and the takedown list", all(x in adm.get(B + "/admin/ais").text for x in ("Pine Bluff river station", "Takedown List", "Add a Station for a Member")) and 'href="/admin/ais"' in adm.get(B + "/admin/").text)
check("without the permission the admin page is refused", other.get(B + "/admin/ais", allow_redirects=False).status_code in (302, 401, 403, 404))
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("River Traffic (AIS)", "River Traffic: Stations per Member", "River Traffic: Area Kept", "River Traffic: Hours of Trail Kept", "River Traffic: Hide Pleasure Craft", "River Traffic Map Opens On")))
au = adm.get(B + "/admin/audit").text
check("every action is in the audit log", all(x in au for x in ("ais.add", "ais.admin_add", "ais.hide", "ais.unhide", "ais.admin_switch", "ais.newaddress", "ais.update")))
check("the tables are in a restore area", "ais_vessels" in open("../app/restorepart.py", encoding="utf-8").read())
setting("ais_enabled", "0")
check("switched off, the pages and the upload address are gone", anon.get(B + "/ais/").status_code == 404 and requests.post(B + "/ais/push/" + newtok, data=pos_a(366666666, 34.9, -92.1)).status_code == 404 and not any(x["properties"] for x in anon.get(B + "/api/ais.geojson").json()["features"]))
psql("DELETE FROM settings WHERE key = 'ais_enabled'")
psql("DELETE FROM ais_stations"); psql("DELETE FROM ais_vessels"); psql("DELETE FROM ais_positions"); psql("DELETE FROM ais_hidden")
print("ALL PASS" if ok_all else "SOME FAILED")
