"""Flight data downloads for signed-in members: the flights Flight History shows as CSV, KML and GeoJSON (the same search and period as the page, the same order, a limit per file and per member per day, the
file saying when it was cut short), the buttons on the page, audit and the settings; and the track of one flight in four formats (an in-container script with the hub's trace replaced)."""
import json, re, subprocess, xml.etree.ElementTree as ET, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("flightlog_enabled", "1"); setting("export_enabled", "1")
psql("DELETE FROM settings WHERE key IN ('export_max_flights','export_per_day','access_exports')")
psql("DELETE FROM flight_log WHERE hex LIKE 'ee%'")
psql("DELETE FROM export_log")
for i, (cs, reg, typ, lat2, mil) in enumerate((("EXP1", "N1EXP", "B738", "35.0", "false"), ("EXP2", "N2EXP", "C172", "NULL", "false"), ("EXP3", "N3EXP", "C17", "36.0", "true"), ("OTH9", "N9OTH", "A320", "34.0", "false"))):
    psql(f"INSERT INTO flight_log (hex, callsign, reg, type, first_seen, last_seen, start_lat, start_lon, end_lat, end_lon, max_alt, max_gs, positions, mil) VALUES ('ee000{i}', '{cs}', '{reg}', '{typ}', now() - interval '{3 - i} hours', now() - interval '{2 - i} hours' + interval '30 minutes', 34.5, -92.5, {lat2}, {'-91.5' if lat2 != 'NULL' else 'NULL'}, {30000 + i * 1000}, {400 + i}, {50 + i}, {mil})")
me, anon = sess("plain1", "plainpassword1"), requests.Session()
check("not signed in: the buttons ask for a sign in and the downloads send you to the login page", "Sign in</a> to download the flights" in anon.get(B + "/history/").text and anon.get(B + "/history/export.csv", allow_redirects=False).status_code == 302)
pg = me.get(B + "/history/?q=EXP&duration=24").text
check("signed in: CSV, KML and GeoJSON buttons with the count and the downloads left", all(f"/history/export.{f}?" in pg for f in ("csv", "kml", "geojson")) and "3 matches" in pg and "of 20 downloads left" in pg, pg[pg.find("Download These"):pg.find("Download These") + 300])
r = me.get(B + "/history/export.csv?q=EXP&duration=24")
lines = [l for l in r.text.splitlines() if l and not l.startswith("#")]
check("CSV: a header and a row for each match, with the file name and the credit line", r.status_code == 200 and len(lines) == 4 and lines[0].startswith("callsign,registration,icao_hex") and "attachment" in r.headers["Content-Disposition"] and ".csv" in r.headers["Content-Disposition"] and "# ArkSignal" in r.text and r.headers["X-Export-Total"] == "3", (r.status_code, lines[:2]))
check("CSV: the values are right (military yes, positions, times in UTC)", any(l.startswith("EXP3,N3EXP,ee0002,C17,yes,") for l in lines) and re.search(r",\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ,", lines[1]) is not None, lines[1:])
check("the search is the page's: only the callsign asked for", len([l for l in me.get(B + "/history/export.csv?q=OTH9&duration=24").text.splitlines() if l and not l.startswith("#")]) == 2)
check("the order is the page's (sorted by flight, descending)", [l.split(",")[0] for l in me.get(B + "/history/export.csv?q=EXP&duration=24&sort=flight&dir=desc").text.splitlines()[1:4]] == ["EXP3", "EXP2", "EXP1"])
g = json.loads(me.get(B + "/history/export.geojson?q=EXP&duration=24").text)
geoms = {f["properties"]["callsign"]: f["geometry"] for f in g["features"]}
check("GeoJSON: a line for a flight with both ends, a point for one with only its start, and a note that the line is not the path", len(g["features"]) == 3 and geoms["EXP1"]["type"] == "LineString" and geoms["EXP2"]["type"] == "Point" and "not the path flown" in g["properties"]["note"] and g["properties"]["truncated"] is False)
k = ET.fromstring(me.get(B + "/history/export.kml?q=EXP&duration=24").content)
ns = {"k": "http://www.opengis.net/kml/2.2"}
check("KML: a placemark for each flight, lines and a point", len(k.findall(".//k:Placemark", ns)) == 3 and len(k.findall(".//k:LineString", ns)) == 2 and len(k.findall(".//k:Point", ns)) == 1)
check("an unknown format is a 404 and a bad range is refused", me.get(B + "/history/export.pdf").status_code == 404 and me.get(B + "/history/export.csv?from=2026-10-02T10:00&to=2026-10-01T10:00").status_code == 400)
check("each download is counted and in the audit log", psql("SELECT COUNT(*) FROM export_log WHERE kind='flights'") == "5" and "flights" in psql("SELECT string_agg(action, ',') FROM audit_log WHERE action='export.flights'"), psql("SELECT COUNT(*) FROM export_log"))
setting("export_max_flights", "2")
r = me.get(B + "/history/export.csv?q=EXP&duration=24")
check("a file holds at most the limit and says it was cut short", len([l for l in r.text.splitlines() if l and not l.startswith("#")]) == 3 and "Only the first 2 of 3" in r.text and r.headers["X-Export-Rows"] == "2" and r.headers["X-Export-Total"] == "3")
check("and so does the page", "a file holds the first 2" in me.get(B + "/history/?q=EXP&duration=24").text)
setting("export_per_day", "7")
check("over the daily limit: told to wait (429) and the buttons say none are left", me.get(B + "/history/export.csv?q=EXP&duration=24").status_code == 200 and me.get(B + "/history/export.csv?q=EXP&duration=24").status_code == 429 and "0 of 7 downloads left" in me.get(B + "/history/?q=EXP").text)
psql("DELETE FROM export_log"); psql("DELETE FROM settings WHERE key IN ('export_max_flights','export_per_day')")

# the track of one flight, with the hub replaced
out = subprocess.run(["sh", "run_unit.sh", "flightexport_inner.py"], capture_output=True, text=True, timeout=300).stdout
print(out.strip())
check("the track downloads passed", "ALL PASS" in out and "FAIL " not in out, out[-300:])

# the switches
adm = sess("marty", PW)
st = adm.get(B + "/admin/settings/aircraft").text
check("the limits are settings", all(x in st for x in ("Downloads of Flight Data", "Downloads: Most Flights in One File", "Downloads: Most per Member per Day")))
setting("access_exports", "roles")
check("Feature Access can hide the downloads", me.get(B + "/history/export.csv", allow_redirects=False).status_code == 404 and "Download These Flights" not in me.get(B + "/history/").text)
psql("DELETE FROM settings WHERE key = 'access_exports'")
setting("export_enabled", "0")
check("switched off: no buttons and no downloads", "Download These Flights" not in me.get(B + "/history/").text and me.get(B + "/history/export.csv", allow_redirects=False).status_code == 404)
setting("export_enabled", "1")
setting("flightlog_enabled", "0")
check("with Flight History off there is nothing to download", me.get(B + "/history/export.csv", allow_redirects=False).status_code == 404)
setting("flightlog_enabled", "1")
psql("DELETE FROM flight_log WHERE hex LIKE 'ee%'")
print("ALL PASS" if ok_all else "SOME FAILED")
