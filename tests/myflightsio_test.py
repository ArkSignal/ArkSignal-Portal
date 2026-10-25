"""My Flights ways in and out: the CSV download (times in each airport's own time, people, no formula injection), the CSV upload (airports by code, simple and detailed rows, duplicates, bad lines named, limits), and the
look-up of a flight number or registration from what the site's receivers heard (the airports found near its first and last positions, the times written in each airport's own time)."""
import csv, io, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("myflights_enabled", "1")
psql("DELETE FROM settings WHERE key IN ('access_myflights','myflights_max_per_member')")
psql("DELETE FROM users WHERE username LIKE 'mio%'")
psql("DELETE FROM flight_log WHERE hex LIKE 'ee%'")
for n in ("mioone", "miotwo"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
U1, U2 = sess("mioone", "mioonepassword99"), sess("miotwo", "miotwopassword99")
anon = requests.Session()
def upload(s, text):
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/flights/import").text).group(1)
    return s.post(B + "/my/flights/import", data={"csrf_token": t}, files={"file": ("f.csv", text.encode("utf-8"), "text/csv")})
check("signed out: sent to sign in", anon.get(B + "/my/flights/export.csv", allow_redirects=False).status_code == 302 and anon.get(B + "/my/flights/import", allow_redirects=False).status_code == 302 and anon.get(B + "/my/flights/lookup.json?q=N123AB", allow_redirects=False).status_code == 302)
pg = U1.get(B + "/my/flights/import").text
check("the page has the download, the upload and the menu entry", "/my/flights/export.csv" in pg and 'type="file"' in pg and "Import and Export" in pg)
csv_text = ("date,from,to,flight,airline,aircraft,registration,act_out,act_in,notes,people\n"
            "2024-08-15,LIT,LAS,WN 1672,Southwest Airlines,B738,n8555q,2024-08-15 12:15,2024-08-15 14:00,=HYPERLINK(1),Marty|Business|Business|12A|Window; Blake\n"
            "2024-09-01,KLIT,DAL,,American Airlines,E175,,,,,\n"
            "2024-09-02,LIT,LIT,,,,,,,,\n"
            "2024-09-03,ZZZZ,LAS,,,,,,,,\n"
            "2024-09-04,LIT,LAS,,,,,2024-09-04 25:99,,,\n"
            ",LIT,LAS,,,,,,,,\n")
r = upload(U1, csv_text)
check("an upload adds the good rows and says what was skipped", "2 flights added" in r.text and "4 lines skipped" in r.text and "The two airports are the same" in r.text and "Airport not found: ZZZZ" in r.text and "A time was not understood" in r.text and "A flight needs a date" in r.text, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))[:300])
check("two flights are in the diary with airports found by IATA and ICAO code", psql("SELECT string_agg(from_code || '-' || to_code, ',' ORDER BY id) FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mioone')") == "LIT-LAS,LIT-DAL")
row = psql("SELECT act_out::text || '|' || act_in::text || '|' || registration || '|' || flight_no || '|' || mode FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mioone') ORDER BY id LIMIT 1")
check("times are read as each airport's own time (12:15 Chicago is 17:15Z), the registration in capitals", row.startswith("2024-08-15 17:15:00+00|2024-08-15 21:00:00+00|N8555Q|WN 1672"), row)
check("a date alone gives a simple flight at noon local time", psql("SELECT mode || '|' || act_out::text FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mioone') ORDER BY id DESC LIMIT 1") == "simple|2024-09-01 17:00:00+00")
check("the people came with it", opsql("SELECT name, klass, seat FROM my_flight_pax ORDER BY id") == [["Marty", "Business", "12A"], ["Blake", "Economy", ""]])
r = upload(U1, csv_text)
check("uploading the same file again adds nothing (already there)", "0 flights added" in r.text and "Already in the diary" in r.text)
exp = U1.get(B + "/my/flights/export.csv")
rows = list(csv.DictReader(io.StringIO(exp.text.lstrip("﻿"))))
check("the download is a CSV with a header and both flights", exp.headers["Content-Type"].startswith("text/csv") and len(rows) == 2 and rows[0]["from"] == "LIT" and rows[0]["act_out"] == "2024-08-15 12:15" and rows[0]["act_in"] == "2024-08-15 14:00", rows[:1])
check("a cell that starts like a formula is made safe", rows[0]["notes"] == "'=HYPERLINK(1)")
check("the people are in the download", "Marty|Business|Business|12A|Window" in rows[0]["people"])
check("round trip: the exported file loads into another member's diary unchanged", "2 flights added" in upload(U2, exp.text).text and psql("SELECT COUNT(*) FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='miotwo')") == "2")
check("another member's export holds only their own flights", len(list(csv.DictReader(io.StringIO(U2.get(B + "/my/flights/export.csv").text.lstrip("﻿"))))) == 2 and "WN 1672" in U2.get(B + "/my/flights/export.csv").text)
setting("myflights_max_per_member", "2")
r = upload(U1, "from,to,date\nLIT,LAS,2025-01-01\n")
check("the diary limit is respected", "0 flights added" in r.text and "Your diary is full" in r.text)
setting("myflights_max_per_member", "2000")
check("a file without from and to is refused with a reason", "must name the columns" in upload(U1, "a,b\n1,2\n").text)
big = U1.post(B + "/my/flights/import", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', U1.get(B + "/my/flights/import").text).group(1)}, files={"file": ("f.csv", b"x" * 1_100_000)}, allow_redirects=True)
check("a file over 1 MB is refused", "too large" in big.text)
# ---- look-up
psql("INSERT INTO flight_log (hex, callsign, reg, type, first_seen, last_seen, start_lat, start_lon, end_lat, end_lon, max_alt, max_gs, positions, mil) VALUES ('ee0001', 'SWA1672', 'N8555Q', 'B738', '2024-08-15 17:20:00+00', '2024-08-15 20:55:00+00', 34.76, -92.20, 36.09, -115.15, 36000, 470, 900, false), "
     "('ee0002', 'SWA1672', 'N9999Z', 'B738', '2024-01-15 17:20:00+00', '2024-01-15 20:55:00+00', 26.0, -90.0, 26.5, -90.5, 36000, 470, 900, false)")
j = U1.get(B + "/my/flights/lookup.json", params={"q": "N8555Q", "date": "2024-08-15"}).json()
f = j["flights"][0] if j["flights"] else {}
check("a registration finds the flight the receivers heard", len(j["flights"]) == 1 and f["callsign"] == "SWA1672" and f["type"] == "B738", j)
check("the airports are found near its first and last positions", f.get("from", {}).get("code") == "LIT" and f.get("to", {}).get("code") == "LAS", f)
check("and the times are written in each airport's own time (17:20Z is 12:20 in Chicago, 20:55Z is 13:55 in Las Vegas)", f.get("act_off") == "2024-08-15T12:20" and f.get("act_on") == "2024-08-15T13:55", f)
j = U1.get(B + "/my/flights/lookup.json", params={"q": "WN1672", "date": "2024-08-15"}).json()
check("a flight number finds it by its number, only on the date asked for (the January flight of the same number is left out)", len(j["flights"]) == 1 and j["flights"][0]["hex"] == "ee0001", j)
j = U1.get(B + "/my/flights/lookup.json", params={"q": "WN1672"}).json()
check("without a date every logged flight of that number is offered", {x["hex"] for x in j["flights"]} == {"ee0001", "ee0002"}, j)
check("a flight heard away from any airport gets no airports and no times", [x for x in j["flights"] if x["hex"] == "ee0002"][0]["from"] is None and "act_off" not in [x for x in j["flights"] if x["hex"] == "ee0002"][0])
check("a short or empty question is not searched, and a bad date is said so", U1.get(B + "/my/flights/lookup.json?q=N1").json()["flights"] == [] and "not understood" in U1.get(B + "/my/flights/lookup.json?q=N8555Q&date=zzz").json()["note"])
check("nothing found says so", "did not hear" in U1.get(B + "/my/flights/lookup.json?q=N0000A").json()["note"])
j = U1.get(B + "/my/flights/lookup.json", params={"q": "DL530"}).json()
sg = j.get("suggest", {})
if sg.get("airline"):   # the outside service answered
    check("a flight number alone suggests its airline and the route it usually flies", "Delta" in sg["airline"] and sg.get("flight_no") == "DL 530" and (not sg.get("from") or (sg["from"]["code"] and sg["to"]["code"])), sg)
j = U1.get(B + "/my/flights/lookup.json", params={"q": "N377DN"}).json()
sg = j.get("suggest", {})
if sg.get("aircraft_type"):
    check("a registration alone suggests the aircraft type and its owner", sg["aircraft_type"] == "A321" and "Delta" in sg.get("airline", ""), sg)
check("a registration is not mistaken for a flight number, and a flight number not for a registration", __import__("sys").modules.get("x") is None and "airline" not in U1.get(B + "/my/flights/lookup.json", params={"q": "N8555Q"}).json()["suggest"].get("flight_no", ""))
def upfile(sess_, name, text, ctype="application/octet-stream"):
    t = re.search(r'name="csrf_token" value="([^"]+)"', sess_.get(B + "/my/flights/import").text).group(1)
    return sess_.post(B + "/my/flights/import", data={"csrf_token": t}, files={"file": (name, text.encode("utf-8"), ctype)})
gpx = ('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><name>WN 1672</name><trkseg>'
       '<trkpt lat="34.7294" lon="-92.2243"><time>2024-10-01T17:00:00Z</time></trkpt><trkpt lat="35.5" lon="-100.0"><time>2024-10-01T18:30:00Z</time></trkpt>'
       '<trkpt lat="36.0840" lon="-115.1537"><time>2024-10-01T20:30:00Z</time></trkpt></trkseg></trk></gpx>')
r = upfile(U1, "flight.gpx", gpx)
check("a GPX track becomes one flight, the airports found near its first and last points", "1 flight added" in r.text and psql("SELECT from_code || '-' || to_code || '|' || act_off::text || '|' || act_on::text FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mioone') AND act_off='2024-10-01 17:00:00+00'") == "LIT-LAS|2024-10-01 17:00:00+00|2024-10-01 20:30:00+00", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))[:200])
check("the same track again is attached to the flight it made, not added twice", "the track is attached to your flight LIT to LAS" in upfile(U1, "flight.gpx", gpx).text and psql("SELECT COUNT(*) FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mioone') AND act_off='2024-10-01 17:00:00+00'") == "1")
kml = ('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2"><Document><Placemark><gx:Track>'
       '<when>2024-10-02T17:00:00Z</when><when>2024-10-02T20:30:00Z</when><gx:coord>-115.1537 36.0840 100</gx:coord><gx:coord>-92.2243 34.7294 100</gx:coord></gx:Track></Placemark></Document></kml>')
check("a KML gx:Track works the same way (here the other direction)", "1 flight added" in upfile(U1, "flight.kml", kml).text and psql("SELECT from_code || '-' || to_code FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mioone') AND act_off='2024-10-02 17:00:00+00'") == "LAS-LIT")
check("a track that starts or ends away from any airport is refused with a reason", "no airport was found for the end" in upfile(U1, "x.gpx", gpx.replace('lat="36.0840" lon="-115.1537"', 'lat="26.0" lon="-90.0"').replace("2024-10-01", "2024-10-03")).text)
check("a track without times is refused", "no points with times" in upfile(U1, "y.gpx", '<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg><trkpt lat="34.7" lon="-92.2"/><trkpt lat="36.0" lon="-115.1"/></trkseg></trk></gpx>').text)
check("a file that declares entities is refused", "features this site does not read" in upfile(U1, "z.gpx", '<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "b">]><gpx/>').text)
check("a file that is not XML is refused", "not a GPX or KML" in upfile(U1, "w.gpx", "hello").text)
lst = U1.get(B + "/my/flights").text
check("a diary flight the site's receivers heard (same registration around the time) links to its track and replay", "/flight/ee0001?at=" in lst and "icao=ee0001&amp;only=1&amp;replay=" in lst, lst.count("Track"))
check("and one they did not hear has no such link", lst.count("/flight/ee0001?at=") == 1)
at = open("airtrail_sample.csv", encoding="utf-8").read()
r = upfile(U2, "airtrail.csv", at)
check("an AirTrail export is recognized and its flights imported (airports found by their names); the one already in the diary from the CSV test is skipped", "3 flights added" in r.text and "Already in the diary" in r.text, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))[:300])
rows_ = ";".join(f"{r[0]}-{r[1]} {r[2]} {r[3]} {r[4]} {r[5]} {r[6]}" for r in opsql("SELECT from_code, to_code, flight_no, registration, aircraft_type, act_off::text, act_on::text, notes FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='miotwo') AND airline='Southwest Airlines' ORDER BY act_off") if r[7] == "SAMPLE")
check("with the flight number, registration, aircraft type and the actual takeoff and landing times in UTC", "WN 3181 N8536Z B738 2024-08-20 17:04:00+00 2024-08-20 19:23:00+00" in rows_ and "WN 2120 N457WN B737 2024-10-17 17:09:00+00 2024-10-17 17:56:00+00" in rows_, rows_)
check("and the seat class, reason and the person who flew", psql("SELECT string_agg(DISTINCT klass || ':' || reason, ',') FROM my_flight_pax WHERE flight_id IN (" + ",".join(r[0] for r in opsql("SELECT id, notes FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='miotwo')") if r[1] == "SAMPLE") + ")") == "Economy:Leisure")
check("importing the AirTrail file again adds nothing", "0 flights added" in upfile(U2, "airtrail.csv", at).text)
check("the form has the look-up", 'id="lk_go"' in U1.get(B + "/my/flights/new").text)
setting("myflights_enabled", "0")
check("switched off: gone", U1.get(B + "/my/flights/import", allow_redirects=False).status_code == 404 and U1.get(B + "/my/flights/export.csv", allow_redirects=False).status_code == 404)
setting("myflights_enabled", "1")
aud = psql("SELECT string_agg(DISTINCT action, ',') FROM audit_log WHERE action LIKE 'myflights.%'")
check("import and export are in the audit log", "myflights.import" in aud and "myflights.export" in aud, aud)
psql("DELETE FROM flight_log WHERE hex LIKE 'ee%'")
psql("DELETE FROM users WHERE username LIKE 'mio%'")
print("ALL PASS" if ok_all else "SOME FAILED")
