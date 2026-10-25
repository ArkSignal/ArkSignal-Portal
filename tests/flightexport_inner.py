"""Runs inside the portal container (driven by flightexport_test.py): the track of one flight as CSV, GPX, KML and GeoJSON, with the hub's trace replaced by a made-up flight of four positions."""
import json, os, re, xml.etree.ElementTree as ET
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from app import create_app
app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
from app import live
from app.db import get_db
fails = 0
def check(n, c, x=""):
    global fails
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    fails += 0 if c else 1
T0 = 1792000000
PTS = [[T0, 34.7, -92.2, 0, 0, 90, 0], [T0 + 60, 34.72, -92.18, 3000, 160, 91, 1500], [T0 + 120, 34.75, -92.1, 9000, 240, 92, 2000], [T0 + 180, 34.8, -92.0, "ground", None, None, None]]
PTS[3][3] = 0
live.trace_data = lambda hex_, at=None: {"flight": "EXP1", "r": "N1EXP", "points": PTS + [[T0 + 9999, 1, 1, 1, 1, 1, 1]], "leg": 0, "end": 4} if hex_ == "abc123" else {"points": [], "leg": 0}
with app.app_context():
    d = get_db()
    uid = d.execute("SELECT id FROM users WHERE username='plain1'").fetchone()["id"]
    d.execute("DELETE FROM export_log WHERE user_id=%s", (uid,))
    d.commit()
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(uid)
    s["_fresh"] = True
anon = app.test_client()
check("not signed in: sent to sign in", anon.get("/flight/abc123/track.csv").status_code == 302)
r = c.get("/flight/abc123/track.csv")
rows = [l for l in r.get_data(as_text=True).splitlines() if l and not l.startswith("#")]
check("CSV: a header and one row for each position of the leg (the point after the leg is not included)", r.status_code == 200 and len(rows) == 5 and rows[0].startswith("time_utc,latitude,longitude,altitude_ft") and rows[2].split(",")[3] == "3000", rows[:3])
check("CSV: times are UTC ISO, the credit line is there, and it downloads as a file", rows[1].startswith("2026-") and "ArkSignal" in r.get_data(as_text=True) and "attachment" in r.headers["Content-Disposition"] and "abc123" in r.headers["Content-Disposition"])
g = json.loads(c.get("/flight/abc123/track.geojson").get_data(as_text=True))
co = g["features"][0]["geometry"]["coordinates"]
check("GeoJSON: one line of four positions as longitude, latitude, metres", g["features"][0]["geometry"]["type"] == "LineString" and len(co) == 4 and co[0][:2] == [-92.2, 34.7] and abs(co[2][2] - 2743.2) < 0.1, co)
k = ET.fromstring(c.get("/flight/abc123/track.kml").get_data())
ns = {"k": "http://www.opengis.net/kml/2.2"}
check("KML: a placemark named for the flight with four coordinates", k.find(".//k:Placemark/k:name", ns).text == "EXP1" and len(k.find(".//k:coordinates", ns).text.split()) == 4)
x = ET.fromstring(c.get("/flight/abc123/track.gpx").get_data())
pts = x.findall(".//{http://www.topografix.com/GPX/1/1}trkpt")
check("GPX: a track of four points with elevation and time", len(pts) == 4 and pts[1].get("lat") == "34.72" and pts[1].find("{http://www.topografix.com/GPX/1/1}ele").text == "914.4" and pts[1].find("{http://www.topografix.com/GPX/1/1}time").text.endswith("Z"))
check("an unknown format, a bad address and a flight with no track are refused", c.get("/flight/abc123/track.pdf").status_code == 404 and c.get("/flight/zzz/track.csv").status_code == 404 and c.get("/flight/def456/track.csv").status_code == 404)
with app.app_context():
    d = get_db()
    n = d.execute("SELECT COUNT(*) AS n FROM export_log WHERE user_id=%s AND kind='track'", (uid,)).fetchone()["n"]
    check("each download is counted and in the audit log", n == 4 and d.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action='export.track'").fetchone()["n"] >= 4, n)
    d.execute("UPDATE settings SET value='4' WHERE key='export_per_day'") if d.execute("SELECT 1 FROM settings WHERE key='export_per_day'").fetchone() else d.execute("INSERT INTO settings (key, value) VALUES ('export_per_day', '4')")
    d.commit()
check("over the daily limit: told to wait (429)", c.get("/flight/abc123/track.csv").status_code == 429)
with app.app_context():
    d = get_db()
    d.execute("DELETE FROM settings WHERE key='export_per_day'")
    d.execute("DELETE FROM export_log WHERE user_id=%s", (uid,))
    d.commit()
print("ALL PASS" if not fails else "SOME FAILED")
