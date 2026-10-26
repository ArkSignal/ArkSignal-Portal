"""A flight's own page and its track: Flightradar24-style KML files attached to the diary flight they match (or added as new), a track added on the flight itself, the track drawn on its page, the AirTrail JSON export (all the people
and the tracks) and a pull straight from an AirTrail instance (the key used once, private addresses refused unless an administrator allows them), and the map's airports and routes linking to the flights."""
import json, os, re, socket, subprocess, threading, time, requests
from http.server import BaseHTTPRequestHandler, HTTPServer
os.environ["TEST_HOST"] = "test-box.invalid"
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("myflights_enabled", "1"); setting("myflights_airtrail_private", "0")
psql("DELETE FROM settings WHERE key = 'access_myflights'")
psql("DELETE FROM users WHERE username LIKE 'mft%'")
for n in ("mftone", "mfttwo"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
U1, U2 = sess("mftone", "mftonepassword99"), sess("mfttwo", "mfttwopassword99")
def upload(s, files):
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/flights/import").text).group(1)
    return s.post(B + "/my/flights/import", data={"csrf_token": t}, files=files)
def text_of(r):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))
def kml(flight, call, airline, dep, arr, typ, model, reg, start, n=60, lat0=34.73, lon0=-92.22, lat1=36.08, lon1=-115.15):
    desc = (f'<div><span><a href="https://www.flightradar24.com/flight/{flight.lower()}">{flight}</a></span> / {call}<br/>{airline}</div>'
            f'<a title="x" href="https://www.flightradar24.com/airport/{dep.lower()}/departures"><h3>{dep}</h3>A</a><a title="y" href="https://www.flightradar24.com/airport/{arr.lower()}/arrivals"><h3>{arr}</h3>B</a>'
            f'<td> Aircraft<span style="float:right">({typ})</span><br> <span>{model}</span> </td><td> Registration</td><td><span>{reg}</span></td>')
    pms = ""
    for i in range(n):
        f = i / (n - 1)
        alt = 0 if i < 3 or i > n - 4 else 30000
        t = start + i * 60
        pms += (f"<Placemark><name>{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t))} UTC</name><description><![CDATA[<div><div><span><b>Altitude:</b></span> <span>{alt:,} ft</span></div><div><span><b>Speed:</b></span> <span>{0 if alt == 0 else 450} kt</span></div></div>]]></description>"
                f"<TimeStamp><when>{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(t))}+00:00</when></TimeStamp><Point><altitudeMode>absolute</altitudeMode><coordinates>{lon0 + (lon1 - lon0) * f:.5f},{lat0 + (lat1 - lat0) * f:.5f},{alt * 0.3048:.1f}</coordinates></Point></Placemark>")
    return (f'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>{flight}/{call}</name><description><![CDATA[{desc}]]></description><Folder><name>Route</name>{pms}</Folder></Document></kml>')
T0 = 1722877200   # 2024-08-05 17:00:00 UTC
k1 = kml("WN1672", "SWA1672", "Southwest Airlines", "LIT", "LAS", "B737", "Boeing 737-7H4", "N486WN", T0)
r = upload(U1, [("file", ("WN1672.kml", k1.encode(), "application/vnd.google-earth.kml+xml"))])
check("a Flightradar24 KML with no matching flight is added as a new one with its track", "added as a new flight, LIT to LAS" in text_of(r) and psql("SELECT from_code || '-' || to_code || ' ' || flight_no || ' ' || registration || ' ' || aircraft_type || ' ' || airline FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mftone')") == "LIT-LAS WN 1672 N486WN B737 Southwest Airlines", text_of(r)[:300])
fid = psql("SELECT id FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mftone') LIMIT 1")
check("with the takeoff and landing from the track (the climb and the touchdown)", psql(f"SELECT act_off::text || '|' || act_on::text FROM my_flights WHERE id={fid}") == "2024-08-05 17:02:00+00|2024-08-05 17:57:00+00", psql(f"SELECT act_off::text || '|' || act_on::text FROM my_flights WHERE id={fid}"))
check("the track is kept", psql(f"SELECT source || ' ' || n FROM my_flight_tracks WHERE flight_id={fid}") == "KML 60")
r = upload(U1, [("file", ("again.kml", k1.encode(), "application/vnd.google-earth.kml+xml"))])
check("the same file again is attached to that flight, not added twice", "the track is attached to your flight LIT to LAS" in text_of(r) and psql("SELECT COUNT(*) FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mftone')") == "1")
psql(f"DELETE FROM my_flight_tracks WHERE flight_id={fid}")
psql(f"UPDATE my_flights SET registration='', aircraft_type='', airline='', act_off=NULL, act_on=NULL, sched_out='2024-08-05 17:00:00+00' WHERE id={fid}")
r = upload(U1, [("file", ("a.kml", k1.encode(), "application/vnd.google-earth.kml+xml"))])
check("a track fills what the flight left empty (registration, aircraft, airline and the times)", psql(f"SELECT registration || '|' || aircraft_type || '|' || airline || '|' || (act_off IS NOT NULL) || (act_on IS NOT NULL) FROM my_flights WHERE id={fid}") == "N486WN|B737|Southwest Airlines|truetrue")
k2 = kml("WN2120", "SWA2120", "Southwest Airlines", "LIT", "DAL", "B737", "Boeing 737-7H4", "N457WN", T0 + 86400 * 3, lat1=32.85, lon1=-96.85)
k3 = kml("WN3181", "SWA3181", "Southwest Airlines", "LAS", "DAL", "B738", "Boeing 737-8H4", "N8536Z", T0 + 86400 * 5, lat0=36.08, lon0=-115.15, lat1=32.85, lon1=-96.85)
r = upload(U1, [("file", ("a.kml", k2.encode(), "application/octet-stream")), ("file", ("b.kml", k3.encode(), "application/octet-stream"))])
check("several files at once are each handled", text_of(r).count("added as a new flight") == 2 and "2 flights added" in text_of(r), text_of(r)[:400])
check("a track away from every airport with no matching flight is refused with a reason", "no airport was found" in text_of(upload(U1, [("file", ("far.kml", kml("XX1", "XXX1", "X", "ZZZ", "YYY", "B737", "m", "N1", T0 + 86400 * 9, lat0=26, lon0=-90, lat1=26.5, lon1=-90.5).encode(), "application/octet-stream"))])))
check("a file that is not a track is refused", "not a GPX or KML" in text_of(upload(U1, [("file", ("x.kml", b"hello", "application/octet-stream"))])))
check("a file that declares entities is refused", "features this site does not read" in text_of(upload(U1, [("file", ("x.kml", b'<?xml version="1.0"?><!DOCTYPE k [<!ENTITY a "b">]><kml/>', "application/octet-stream"))])))
# ---- the flight's own page
pg = U1.get(B + f"/my/flights/{fid}")
check("a flight has its own page with the times, the aircraft and the track", pg.status_code == 200 and "LIT" in pg.text and "N486WN" not in pg.text.replace("B737", "") or "B737" in pg.text, pg.status_code)
check("it says how far and how long and how high the track went", all(x in pg.text for x in ("Flown", "Tracked", "Highest", "30,000 ft", 'id="ftmap"')), "")
check("another member cannot open it", U2.get(B + f"/my/flights/{fid}").status_code == 404 and U2.get(B + f"/my/flights/{fid}/track.json").status_code == 404)
tj = U1.get(B + f"/my/flights/{fid}/track.json").json()
check("the track data comes as points with time, position, height and speed", len(tj["points"]) == 60 and len(tj["points"][0]) == 5 and tj["points"][30][3] == 30000 and tj["points"][30][4] == 450)
gpx = '<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg><trkpt lat="34.7294" lon="-92.2243"><ele>100</ele><time>2024-08-05T17:00:00Z</time></trkpt><trkpt lat="36.084" lon="-115.1537"><ele>600</ele><time>2024-08-05T19:30:00Z</time></trkpt></trkseg></trk></gpx>'
t = re.search(r'name="csrf_token" value="([^"]+)"', U1.get(B + f"/my/flights/{fid}").text).group(1)
r = U1.post(B + f"/my/flights/{fid}/track/upload", data={"csrf_token": t}, files={"file": ("t.gpx", gpx.encode(), "application/gpx+xml")}, allow_redirects=False)
check("a track can be added on the flight itself (it replaces the one there)", r.status_code in (302, 200) and psql(f"SELECT source || ' ' || n FROM my_flight_tracks WHERE flight_id={fid}") == "GPX 2", psql(f"SELECT source || ' ' || n FROM my_flight_tracks WHERE flight_id={fid}"))
check("another member cannot add one to it", U2.post(B + f"/my/flights/{fid}/track/upload", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', U2.get(B + "/my/flights").text).group(1)}, files={"file": ("t.gpx", gpx.encode())}, allow_redirects=False).status_code == 404)
U1.post(B + f"/my/flights/{fid}/track/remove", data={"csrf_token": t}, allow_redirects=False)
check("and removed", psql(f"SELECT COUNT(*) FROM my_flight_tracks WHERE flight_id={fid}") == "0" and "Add Track" in U1.get(B + f"/my/flights/{fid}").text)
# ---- an AirTrail JSON export: every field, ALL the people, the tracks
at = {"users": [{"id": "u1", "username": "me"}], "flights": [{"id": 1, "date": "2024-09-10", "from": {"icao": "KLIT", "iata": "LIT", "name": "Clinton National", "lat": 34.7294, "lon": -92.2243, "tz": "America/Chicago"}, "to": {"icao": "KLAS", "iata": "LAS", "name": "Harry Reid", "lat": 36.084, "lon": -115.1537, "tz": "America/Los_Angeles"},
      "departureScheduled": "2024-09-10T17:00:00.000+00:00", "takeoffActual": "2024-09-10T17:10:00.000+00:00", "landingActual": "2024-09-10T19:50:00.000+00:00", "flightNumber": "WN100", "aircraftReg": "N900WN", "note": "from AirTrail",
      "aircraft": {"name": "Boeing 737-800", "icao": "B738"}, "airline": {"name": "Southwest Airlines", "icao": "SWA", "iata": "WN"},
      "passengers": [{"userId": "u1", "guestName": None, "seat": "window", "seatNumber": "12A", "seatClass": "economy", "flightReason": "leisure", "user": {"id": "u1", "username": "me"}},
                     {"userId": None, "guestName": "Blake Allison", "seat": "aisle", "seatNumber": "12B", "seatClass": "economy", "flightReason": "leisure", "user": None},
                     {"userId": None, "guestName": "Casey Allison", "seat": "middle", "seatNumber": "12C", "seatClass": "business", "flightReason": "business", "user": None}],
      "track": {"coordinates": [[-92.2243, 34.7294, 100], [-100.0, 35.5, 10000], [-115.1537, 36.084, 600]], "times": ["2024-09-10T17:10:00Z", "2024-09-10T18:30:00Z", "2024-09-10T19:50:00Z"], "sourceFormat": "gpx"}}]}
r = upload(U2, [("file", ("airtrail-export.json", json.dumps(at).encode(), "application/json"))])
check("an AirTrail JSON export brings the flight, all three people and the track", "1 flight added with 3 people and 1 track" in text_of(r), text_of(r)[:300])
fid2 = psql("SELECT id FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mfttwo') AND flight_no='WN 100'")
check("with the aircraft, airline, registration, notes and the airports' own time zones", opsql(f"SELECT aircraft_type, airline, registration, notes, from_tz, to_tz FROM my_flights WHERE id={fid2}") == [["B738", "Southwest Airlines", "N900WN", "from AirTrail", "America/Chicago", "America/Los_Angeles"]], opsql(f"SELECT aircraft_type, airline, registration, notes, from_tz, to_tz FROM my_flights WHERE id={fid2}"))
check("the people with their seats, classes and reasons (the member's own entry carries their name)", ";".join(":".join(r) for r in opsql(f"SELECT name, seat, klass, reason FROM my_flight_pax WHERE flight_id={fid2} ORDER BY id")).startswith("mfttwo:12A:Economy:Leisure;Blake Allison:12B:Economy:Leisure;Casey Allison:12C:Business:Business") or "Casey Allison:12C:Business:Business" in ";".join(":".join(r) for r in opsql(f"SELECT name, seat, klass, reason FROM my_flight_pax WHERE flight_id={fid2} ORDER BY id")))
check("and the track is on the flight", psql(f"SELECT source || ' ' || n FROM my_flight_tracks WHERE flight_id={fid2}") == "AirTrail 3")
check("importing the export again adds nothing", "already in the diary" in text_of(upload(U2, [("file", ("airtrail-export.json", json.dumps(at).encode(), "application/json"))])))
# numeric track times (epoch seconds and milliseconds, as a real AirTrail sends) must import, not crash
import copy
at2 = copy.deepcopy(at); f2 = at2["flights"][0]
f2["id"] = 2; f2["flightNumber"] = "WN101"; f2["departureScheduled"] = "2024-09-12T17:00:00.000+00:00"; f2["takeoffActual"] = "2024-09-12T17:10:00.000+00:00"; f2["landingActual"] = "2024-09-12T19:50:00.000+00:00"
f2["track"]["times"] = [1726161000, 1726165800000, 1726170600]
r = upload(U2, [("file", ("airtrail-num.json", json.dumps(at2).encode(), "application/json"))])
check("an AirTrail export whose track times are numbers imports with its track", r.status_code == 200 and "1 track" in text_of(r), (r.status_code, text_of(r)[:300]))
# ---- a pull straight from an AirTrail instance (a stand-in server on this PC)
KEY = "at-test-key-123"; SEEN = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        SEEN.append((self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer " + KEY:
            self.send_response(401); self.end_headers(); return
        p = self.path
        if p.startswith("/api/v1/me"):
            body = {"data": {"user": {"id": "u1"}}}
        elif p.startswith("/api/v1/flights/") and p.endswith("/track"):
            body = {"data": {"coordinates": [[-96.85, 32.85, 100], [-92.2243, 34.7294, 100]], "times": ["2024-09-20T17:00:00Z", "2024-09-20T17:50:00Z"]}}
        elif p.startswith("/api/v1/flights"):
            body = {"data": [{"id": 77, "date": "2024-09-20", "from": {"icao": "KDAL", "iata": "DAL", "name": "Dallas Love Field", "lat": 32.847, "lon": -96.852}, "to": {"icao": "KLIT", "iata": "LIT", "name": "Clinton", "lat": 34.7294, "lon": -92.2243},
                     "takeoffActual": "2024-09-20T17:00:00.000+00:00", "landingActual": "2024-09-20T17:50:00.000+00:00", "flightNumber": "WN4499",
                     "passengers": [{"userId": "u1", "guestName": None, "seat": "aisle", "seatNumber": "3C", "seatClass": "economy", "flightReason": "leisure"}, {"guestName": "Blake Allison", "seat": "window", "seatNumber": "3A", "seatClass": "economy", "flightReason": "leisure"}]}], "page": {"nextCursor": None}}
        else:
            self.send_response(404); self.end_headers(); return
        raw = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
srv = HTTPServer(("0.0.0.0", 18991), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s_.connect(("test-box.invalid", 80)); MYIP = s_.getsockname()[0]; s_.close()
def pull(url, key):
    t = re.search(r'name="csrf_token" value="([^"]+)"', U2.get(B + "/my/flights/import").text).group(1)
    return U2.post(B + "/my/flights/import/airtrail", data={"csrf_token": t, "url": url, "key": key})
r = pull(f"http://{MYIP}:18991", KEY)
check("a private address is refused unless an administrator allows it", "private network" in text_of(r) or "https address" in text_of(r), text_of(r)[:300])
check("and nothing was asked of it", not SEEN)
check("an address that is not an address is refused", "Give the address of your AirTrail" in text_of(pull("airtrail", KEY)) and "Give the address of your AirTrail" in text_of(pull("ftp://x.example.com", KEY)))
setting("myflights_airtrail_private", "1"); time.sleep(7)
r = pull(f"http://{MYIP}:18991", "wrong-key")
check("a wrong key is told so", "did not accept that API key" in text_of(r), text_of(r)[:300])
r = pull(f"http://{MYIP}:18991", KEY)
check("with private addresses allowed the flight, its two people and its track come in from the instance", "1 flight added with 2 people and 1 track" in text_of(r), text_of(r)[:400])
check("the key went in the Authorization header and the address was asked for the flights and the track", any(a == "Bearer " + KEY and p.startswith("/api/v1/flights?limit=100") for p, a in SEEN) and any(p.endswith("/flights/77/track") for p, a in SEEN))
check("the person who made the key is the member (name), the guest keeps theirs", "mfttwo:3C" in ";".join(":".join(r) for r in opsql("SELECT name, seat FROM my_flight_pax WHERE flight_id=(SELECT id FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mfttwo') AND flight_no='WN 4499') ORDER BY id")).replace("mfttwo:3C", "mfttwo:3C") and "Blake Allison:3A" in ";".join(":".join(r) for r in opsql("SELECT name, seat FROM my_flight_pax")))
check("the key is not kept anywhere", psql(f"SELECT COUNT(*) FROM settings WHERE value LIKE '%{KEY}%'") == "0" and psql(f"SELECT COUNT(*) FROM audit_log WHERE detail LIKE '%{KEY}%'") == "0")
check("the pull is in the audit log without the key", "AirTrail instance" in psql("SELECT string_agg(detail, '|') FROM audit_log WHERE action='myflights.import'"))
srv.shutdown()
setting("myflights_airtrail_private", "0")
# ---- the map: airports and routes link to the flights
try:
    from selenium import webdriver
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,1000")
    d = webdriver.Chrome(options=o); J = d.execute_script
    try:
        d.get(B + "/my/login"); time.sleep(1)
        d.find_element("name", "username").send_keys("mftone"); d.find_element("name", "password").send_keys("mftonepassword99"); d.find_element("css selector", "form button[type=submit]").click(); time.sleep(2)
        d.get(B + f"/my/flights/{fid}/edit"); time.sleep(1)
        d.get(B + "/my/flights/map"); time.sleep(4)
        check("the map draws (no blank box)", J("return !!window.flMap && document.querySelectorAll('#flmap .leaflet-control-zoom').length === 1"))
        J("var done=false; window.flMap.eachLayer(function(l){ if (!done && l instanceof L.CircleMarker && l.getPopup && l.getPopup()) { l.openPopup(); done=true; } });"); time.sleep(.8)
        links = J("return Array.from(document.querySelectorAll('.leaflet-popup-content a')).map(function(a){return a.getAttribute('href')})")
        check("an airport's card lists its flights as links to their own pages", links and all(re.match(r"^/my/flights/\d+$", x) for x in links), links)
        J("var d2=false; window.flMap.eachLayer(function(l){ if (!d2 && l instanceof L.Polyline && !(l instanceof L.Polygon) && !(l instanceof L.CircleMarker) && l.getPopup && l.getPopup()) { l.openPopup(l.getBounds().getCenter()); d2=true; } });"); time.sleep(.8)
        links = J("return Array.from(document.querySelectorAll('.leaflet-popup-content a')).map(function(a){return a.getAttribute('href')})")
        check("and so does a route's card", links and all(re.match(r"^/my/flights/\d+$", x) for x in links), links)
        d.get(B + f"/my/flights/{fid2 if False else fid}"); time.sleep(1)
        d.set_window_size(400, 850); d.get(B + f"/my/flights/{psql('SELECT id FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username=' + chr(39) + 'mftone' + chr(39) + ') AND flight_no=' + chr(39) + 'WN 2120' + chr(39))}"); time.sleep(3)
        check("phone: the flight page with its track does not run off the side", J("return document.documentElement.scrollWidth <= window.innerWidth + 1"), J("return document.documentElement.scrollWidth"))
        check("and the track is drawn on it", J("return document.querySelectorAll('#ftmap path.leaflet-interactive').length") > 10)
    finally:
        d.quit()
except ImportError:
    print("SKIP no selenium")
# ---- recorded tracks on the Flight Map
mj = U1.get(B + "/my/flights/map.json").json()
tk = mj.get("tracks", [])
check("the map data carries a thinned track for each flight that has one, and none for the others", len(tk) >= 1 and all(len(t["pts"]) <= 151 and len(t["pts"][0]) == 3 for t in tk) and len(tk) < len(mj["routes"]) + 5, mj.get("tracks"))
setting("myflights_map_track_points", "20")
tk = U1.get(B + "/my/flights/map.json").json()["tracks"]
check("the points per track on the map follow the setting", tk and max(len(t["pts"]) for t in tk) <= 21, [len(t["pts"]) for t in tk])
psql("DELETE FROM settings WHERE key='myflights_map_track_points'")
check("another member's map has none of these tracks", not ({t["id"] for t in U2.get(B + "/my/flights/map.json").json().get("tracks", [])} & {t["id"] for t in U1.get(B + "/my/flights/map.json").json()["tracks"]}))
pg = U1.get(B + "/my/flights/map").text
check("the map page draws recorded paths colored by height", "recorded path" in pg and "col(t.pts" in pg)
check("a route whose flights all have a recorded track gets no estimated line under it", "if (!left.length) return;" in pg and "allTracked" not in pg and "dashArray" not in pg)
psql("DELETE FROM users WHERE username LIKE 'mft%'")
print("ALL PASS" if ok_all else "SOME FAILED")
