"""In-container checks of sondes.py: the area filter, a whole simulated flight through the collector into the database (one stored point per interval, launch site and NWS recognition, highest point,
landed or lost, heard-by, predictions), late-joined flights filled in from history, receiving station privacy and claims, the county lookup of a landing.
docker cp this file to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sondes_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
import json
from datetime import datetime, timedelta, timezone
import psycopg
from psycopg.rows import dict_row
from app import create_app
from app import sondes as sd
from app import counties as co
from app.settings import set_setting, cfg

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
near = lambda a, b, t=0.05: a is not None and abs(a - b) <= t
NOW = datetime.now(timezone.utc).replace(microsecond=0)
iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000000Z")

with app.app_context():
    from app.db import get_db
    d = get_db()
    for t in ("sonde_heard", "sonde_points", "sondes", "sonde_stations", "sonde_sites", "sonde_recoveries"):
        d.execute(f"DELETE FROM {t}")
    d.commit()
    set_setting("sonde_recovery_minutes", "0")   # the tests never ask SondeHub for anything
    for k, v in (("sonde_enabled", "1"), ("sonde_amateur_enabled", "1"), ("sonde_keep_every_seconds", "5"), ("sonde_backfill", "1"), ("sonde_station_privacy", "general"), ("sonde_claim_mode", "match"),
                 ("sonde_stale_minutes", "20"), ("sonde_landed_below_m", "4000"), ("sonde_nws_pattern", "7[24][0-9]{3}"), ("sonde_nws_exclude", "74646"), ("sonde_site_match_km", "25")):
        set_setting(k, v)
    set_setting("public_cache_seconds", "0")
    d.execute("DELETE FROM users WHERE username IN ('sondeowner','sondeother')"); d.commit()
    # members whose profile call sign matches the test stations would be given those stations by the automatic claiming: set their call signs aside for the test
    saved_calls = [(r["id"], r["callsign"]) for r in d.execute("SELECT id, callsign FROM users WHERE callsign IS NOT NULL AND upper(callsign) ~ '^(KD5VCF|AE5AU|W1ABC|NG5P|W5XYZ|LEADCALL)'").fetchall()]
    d.execute("UPDATE users SET callsign = NULL WHERE id = ANY(%s)", ([i for i, _c in saved_calls],)); d.commit()

    # ---- the area and the helpers
    check("the default area covers Arkansas and its neighbors", sd.in_region(34.8, -92.3) and sd.in_region(32.4, -93.8) and sd.in_region(36.2, -86.6) and sd.in_region(35.2, -97.4) and sd.in_region(30.2, -90.1)
          and not sd.in_region(51.0, 0.0) and not sd.in_region(45.0, -122.0))
    check("dew point (20 C, 50 %: about 9.3 C)", near(sd.dew_point_c(20, 50), 9.26, .1) and sd.dew_point_c(None, 50) is None)
    check("distance (Little Rock to Memphis is about 210 km)", 195 < sd.km(34.75, -92.29, 35.15, -90.05) < 230)

    # ---- the launch sites: NWS recognized by the station number and the exclude list
    sd.import_sites({"72340": {"station_name": "North Little Rock, North Little Rock Airport (United States)", "position": [-92.2594, 34.835], "alt": 172, "rs_types": ["54"], "times": ["0:00:00", "0:12:00"]},
                     "72248": {"station_name": "Shreveport (United States)", "position": [-93.8414, 32.4511], "alt": 80, "rs_types": [["54", "403.01"]], "times": ["0:00:00", "0:12:00"]},
                     "74646": {"station_name": "ARM Southern Great Plains (United States)", "position": [-97.4877, 36.6079], "alt": 300, "rs_types": ["41"], "times": []},
                     "-90": {"station_name": "ARM Bankhead National Forest", "position": [-87.338, 34.345], "alt": 300, "rs_types": ["41"], "times": None},
                     "98753": {"station_name": "Davao (Philippines)", "position": [125.65, 7.12], "alt": 17, "rs_types": ["11"], "times": []}})
    rows = {s["site_id"]: s for s in sd.sites()}
    check("sites are stored; 72xxx is National Weather Service, 74646 (excluded), negative research numbers and foreign sites are not", len(rows) == 5 and rows["72340"]["nws"] and rows["72248"]["nws"]
          and not rows["74646"]["nws"] and not rows["-90"]["nws"] and not rows["98753"]["nws"])
    check("only sites inside the area are listed for the maps and county pages", {x["id"] for x in sd.site_items()} == {"72340", "72248"} and {x["id"] for x in sd.map_sites()} == {"72340", "72248", "74646"}
          and {x["id"] for x in sd.map_sites(nws_only=True)} == {"72340", "72248"})
    check("the nearest site within the match distance is found", sd.nearest_site(34.84, -92.26)["site_id"] == "72340" and sd.nearest_site(40.0, -100.0) is None)

    # ---- a whole flight: launched from North Little Rock, climbs 5 m/s to 30 km, bursts, descends 8 m/s, lands; heard by two stations
    conn = psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row)
    col = sd.Collector(); col.configure()
    launch = NOW - timedelta(hours=3)
    def msg(serial, t, lat, lon, alt, vv, up="KD5VCF-LR", extra=None):
        m = {"software_name": "radiosonde_auto_rx", "software_version": "1.9.0", "uploader_callsign": up, "uploader_position": [34.7425, -92.2842, 129.0], "uploader_antenna": "Commercial UHF omni",
             "datetime": iso(t), "manufacturer": "Vaisala", "type": "RS41", "subtype": "RS41-SG", "serial": serial, "frame": int((t - launch).total_seconds()), "lat": lat, "lon": lon, "alt": alt,
             "temp": 25 - alt / 150 if alt < 11000 else -56.5, "humidity": max(5, 70 - alt / 400), "vel_v": vv, "vel_h": 10.0, "heading": 90.0, "sats": 11, "batt": 2.7, "frequency": 403.01, "snr": 18.5}
        if extra: m.update(extra)
        return m
    n_msgs = 0
    for sec in range(0, 6000 + 3600):   # up for 6000 s (5 m/s to 30 km), down at 8 m/s
        t = launch + timedelta(seconds=sec)
        if sec <= 6000:
            alt, vv = 172 + 5 * sec, 5.0
        else:
            alt, vv = max(220, 30172 - 8 * (sec - 6000)), -8.0
        lat, lon = 34.835 + 0.00001 * sec, -92.2594 + 0.00002 * sec
        col.handle("sondes/TESTS001", json.dumps(msg("TESTS001", t, lat, lon, alt, vv)))
        n_msgs += 1
        if sec % 3 == 0:   # a second station hears every third frame
            col.handle("sondes/TESTS001", json.dumps(msg("TESTS001", t, lat, lon, alt, vv, up="AE5AU", extra={"snr": 9.0, "uploader_position": [34.686, -92.5243, 170.0]})))
        if sec % 600 == 0:
            col.flush(conn)
    col.handle("prediction/TESTS001", json.dumps({"serial": "TESTS001", "data": [{"time": 1, "lat": 34.9, "lon": -91.6, "alt": 2000}, {"time": 1791520000, "lat": 34.95, "lon": -91.55, "alt": 150}], "burst_altitude": 30172}))
    col.flush(conn)
    r = sd.get("TESTS001")
    check("the flight is recorded with its type", r is not None and r["type"] == "RS41" and r["manufacturer"] == "Vaisala" and r["kind"] == "radiosonde")
    pts = d.execute("SELECT COUNT(*) AS n, MIN(ts) AS a, MAX(ts) AS b FROM sonde_points WHERE serial='TESTS001'").fetchone()
    check("one position is stored per 5 seconds (9600 s of messages become about 1920 points, not 9600)", 1800 <= pts["n"] <= 1960, pts["n"])
    check("the flight starts at the launch and ends at the last message", abs((pts["a"] - launch).total_seconds()) < 1 and abs((pts["b"] - (launch + timedelta(seconds=9599))).total_seconds()) < 6)
    check("the highest point is 30172 m and its time is the burst", near(r["max_alt"], 30172, 1) and abs((r["max_alt_ts"] - (launch + timedelta(seconds=6000))).total_seconds()) < 6, (r["max_alt"], r["max_alt_ts"]))
    check("the launch site is the nearest launch site and the flight is National Weather Service", r["site_id"] == "72340" and r["agency"] == "NWS" and "North Little Rock" in r["site_name"] and r["launch_checked"] and near(r["launch_alt"], 172, 1))
    check("the first position is the launch position", near(r["launch_lat"], 34.835, .001) and near(r["launch_lon"], -92.2594, .001))
    check("two stations are recorded as having heard it, with their message counts", {h["callsign"]: h["packets"] for h in sd.heard_by("TESTS001")} == {"KD5VCF-LR": 9600, "AE5AU": 3200}, [(h["callsign"], h["packets"]) for h in sd.heard_by("TESTS001")])
    check("the predicted landing is the last point of the prediction", near(r["pred_lat"], 34.95, .001) and near(r["pred_lon"], -91.55, .001) and r["pred_time"] is not None)
    check("still in the air while it is being heard", r["status"] == "aloft")
    pp = json.loads(r["pred_path"] or "[]")
    check("the whole predicted path is kept, in order, ending on the landing", len(pp) == 2 and pp[0][1:3] == [34.9, -91.6] and pp[-1][1:3] == [34.95, -91.55] and pp[0][3] == 2000, pp)
    big = [{"time": i, "lat": 34 + i / 1000, "lon": -92, "alt": 1000 - i} for i in range(1000)]
    tp = json.loads(sd.thin_path(big))
    check("a long predicted path is thinned evenly to the limit and keeps both ends", len(tp) == sd.PRED_PATH_MAX and tp[0][0] == 0 and tp[-1][0] == 999, len(tp))

    # ---- the flight goes quiet: landed (it was low); a second flight that stops high is lost
    d.execute("UPDATE sondes SET last_seen = now() - interval '45 minutes' WHERE serial='TESTS001'"); d.commit()
    ts_hi = launch
    col2 = sd.Collector(); col2.configure()
    for sec in range(0, 3000, 5):
        col2.handle("sondes/TESTL002", json.dumps(msg("TESTL002", NOW - timedelta(hours=2) + timedelta(seconds=sec), 34.84, -92.26, 172 + 5 * sec, 5.0)))
    col2.flush(conn)
    d.execute("UPDATE sondes SET last_seen = now() - interval '60 minutes' WHERE serial='TESTL002'"); d.commit()
    sd.sweep(conn)
    a, b = sd.get("TESTS001"), sd.get("TESTL002")
    check("a quiet flight that ended low is landed, at its last position", a["status"] == "landed" and a["landing_lat"] is not None and near(a["landing_lat"], a["last_lat"], .0001) and a["landing_alt"] is not None and a["landing_alt"] < 4000)
    check("a quiet flight that ended high is lost, with no landing position", b["status"] == "lost" and b["landing_lat"] is None)
    check("the flight list finds both by serial prefix, source and status", sd.flights(q="TESTS")[0] == 1 and sd.flights(agency="NWS")[0] >= 1 and sd.flights(status="lost")[0] == 1 and sd.flights(station="AE5AU")[0] == 1)
    s = sd.summary(a)
    check("the summary has the climb, descent, drift and time", near(s["ascent_ms"], 5.0, .1) and s["drift_km"] > 5 and s["duration_min"] and s["to_burst_min"] in (99, 100, 101) and s["descent_ms"] is not None, s)
    tr = sd.track("TESTS001", max_points=500)
    check("a track is thinned evenly to the requested number of points, keeping the last", 499 <= len(tr) <= 502 and tr[-1]["ts"] == max(x["ts"] for x in tr))

    # ---- outside the area is ignored; a flight that starts outside and flies in is followed; the amateur switch
    col3 = sd.Collector(); col3.configure()
    for sec in range(0, 100, 5):
        col3.handle("sondes/TESTFAR", json.dumps(msg("TESTFAR", NOW + timedelta(seconds=sec), 51.0, 0.0, 1000 + 5 * sec, 5.0)))
    col3.flush(conn)
    check("a flight far outside the area is not stored", sd.get("TESTFAR") is None)
    col3.handle("sondes/TESTIN", json.dumps(msg("TESTIN", NOW, 33.0, -94.0, 9000, 5.0)))   # in Texas/Arkansas boxes: recorded
    col3.handle("sondes/TESTIN", json.dumps(msg("TESTIN", NOW + timedelta(seconds=60), 34.0, -80.0, 9300, 5.0)))   # now outside every box, but the flight is known
    col3.flush(conn)
    tin = d.execute("SELECT COUNT(*) AS n FROM sonde_points WHERE serial='TESTIN'").fetchone()["n"]
    check("a known flight is followed after it leaves the area", tin == 2, tin)
    col3.handle("amateur/N0CALL-11", json.dumps({"payload_callsign": "N0CALL-11", "datetime": iso(NOW), "lat": 35.0, "lon": -92.0, "alt": 12000.0, "uploader_callsign": "KD5VCF", "uploader_position": [34.7, -92.2, 100]}))
    col3.flush(conn)
    am = sd.get("N0CALL-11")
    check("an amateur balloon is recorded under its payload call sign, marked amateur and without an agency", am is not None and am["kind"] == "amateur" and am["agency"] == "")
    set_setting("sonde_amateur_enabled", "0"); col3.configure()
    col3.handle("amateur/N0CALL-12", json.dumps({"payload_callsign": "N0CALL-12", "datetime": iso(NOW), "lat": 35.0, "lon": -92.0, "alt": 12000.0}))
    col3.flush(conn)
    check("amateur balloons are ignored when switched off", sd.get("N0CALL-12") is None)
    set_setting("sonde_amateur_enabled", "1")

    # ---- a flight first heard in the air gets its start from history, once
    col4 = sd.Collector(); col4.configure()
    col4.handle("sondes/TESTLATE", json.dumps(msg("TESTLATE", NOW - timedelta(minutes=5), 34.9, -92.1, 12000, 5.0)))
    col4.flush(conn)
    check("a flight first heard high is queued once for its history", col4.backfill.qsize() == 1 and sd.get("TESTLATE")["launch_checked"] is False)
    col4.handle("sondes/TESTLATE", json.dumps(msg("TESTLATE", NOW - timedelta(minutes=4), 34.91, -92.1, 12300, 5.0)))
    col4.flush(conn)
    check("and not queued again", col4.backfill.qsize() == 1)
    hist = [{"datetime": iso(NOW - timedelta(minutes=125) + timedelta(seconds=s)), "lat": 32.451 + s * 1e-5, "lon": -93.841, "alt": 80 + 5 * s, "vel_v": 5, "temp": 20, "humidity": 60, "uploader_callsign": "W5XYZ"} for s in range(0, 1200, 2)]
    n = sd.backfill_one(conn, None, "TESTLATE", packets=hist)
    late = sd.get("TESTLATE")
    check("the history is stored at our interval and names the launch site (Shreveport, National Weather Service)", 195 <= n <= 205 and late["site_id"] == "72248" and late["agency"] == "NWS" and late["launch_checked"], (n, late["site_id"]))
    check("the first seen time moves back to the launch", (late["first_seen"] - (NOW - timedelta(minutes=125))).total_seconds() < 5)

    # ---- an amateur balloon that goes up beside a weather-service site is not an NWS launch and has no launch site
    col6 = sd.Collector(); col6.configure()
    col6.handle("amateur/W0TEST-1", json.dumps({"payload_callsign": "W0TEST-1", "datetime": iso(NOW - timedelta(minutes=30)), "lat": 32.452, "lon": -93.841, "alt": 80.0, "vel_v": 5.0}))
    col6.flush(conn)
    am = sd.get("W0TEST-1")
    check("an amateur balloon that began beside a National Weather Service site gets no launch site and is not NWS", am is not None and am["kind"] == "amateur" and am["launch_checked"] and am["site_id"] == "" and am["agency"] == "", dict(am) if am else None)
    hist2 = [{"datetime": iso(NOW - timedelta(minutes=90) + timedelta(seconds=s)), "lat": 32.451 + s * 1e-5, "lon": -93.841, "alt": 80 + 5 * s, "vel_v": 5} for s in range(0, 60)]
    d.execute("INSERT INTO sondes (serial, kind, first_seen, last_seen) VALUES ('W0TEST-2', 'amateur', now(), now()) ON CONFLICT DO NOTHING"); d.commit()
    sd.backfill_one(conn, None, "W0TEST-2", packets=hist2)
    am2 = sd.get("W0TEST-2")
    check("and a late-joined amateur flight filled in from history is the same", am2["site_id"] == "" and am2["agency"] == "" and am2["launch_lat"] is not None, dict(am2))
    d.execute("DELETE FROM sondes WHERE serial IN ('W0TEST-1', 'W0TEST-2')"); conn.commit()

    # ---- receiving stations: privacy, mobile, claims
    st1 = sd.station("KD5VCF-LR")
    check("receiving stations are recorded from the uploads with their software and antenna", st1 is not None and st1["software"] == "radiosonde_auto_rx" and st1["antenna"] == "Commercial UHF omni" and st1["privacy"] == "general" and st1["claim_status"] == "unclaimed")
    pp = sd.public_position(st1)
    places = int(cfg("general_precision_decimals"))
    check("an unclaimed station's position is rounded (General), never the exact upload", pp == (round(34.7425, places), round(-92.2842, places)) and pp != (34.7425, -92.2842), pp)
    col5 = sd.Collector(); col5.configure()
    col5.handle("listener/CHASER-9", json.dumps({"uploader_callsign": "CHASER-9", "uploader_position": [34.5, -92.5, 100], "mobile": True, "software_name": "rdzTTGOsonde"}))
    col5.handle("listener/FAR-AWAY", json.dumps({"uploader_callsign": "FAR-AWAY", "uploader_position": [51.5, -0.1, 10], "mobile": False}))
    col5.flush(conn)
    ch = sd.station("CHASER-9")
    check("a moving station is stored but hidden; a station outside the area is not stored", ch is not None and ch["mobile"] and ch["privacy"] == "hidden" and sd.public_position(ch) is None and sd.station("FAR-AWAY") is None)
    check("an exact choice is honored, and hidden gives nothing", sd.public_position({"lat": 34.7425, "lon": -92.2842, "privacy": "exact", "mobile": False}) == (34.7425, -92.2842) and sd.public_position({"lat": 34.7, "lon": -92.2, "privacy": "hidden", "mobile": False}) is None)
    check("a moving station shows only when its owner picks exact", sd.public_position({"lat": 34.7, "lon": -92.2, "privacy": "exact", "mobile": True}) == (34.7, -92.2) and sd.public_position({"lat": 34.7, "lon": -92.2, "privacy": "general", "mobile": True}) is None)

    uid = d.execute("INSERT INTO users (username, email, password_hash, callsign) VALUES ('sondeowner', 'so@example.com', 'x', 'KD5VCF') RETURNING id").fetchone()["id"]
    uid2 = d.execute("INSERT INTO users (username, email, password_hash, callsign) VALUES ('sondeother', 'so2@example.com', 'x', 'W1ABC') RETURNING id").fetchone()["id"]
    d.commit()
    class U:
        def __init__(self, i, c): self.id, self.callsign = i, c
    owner, other = U(uid, "KD5VCF"), U(uid2, "W1ABC")
    status, msg_ = sd.claim(owner, "KD5VCF-LR")
    check("a member whose profile call sign matches the station (with its SSID) claims it at once", status == "claimed" and sd.station("KD5VCF-LR")["owner_id"] == uid, msg_)
    status, msg_ = sd.claim(other, "KD5VCF-LR")
    check("a station that belongs to someone else cannot be claimed", status is None and "already belongs" in msg_)
    status, msg_ = sd.claim(other, "AE5AU")
    st2 = sd.station("AE5AU")
    check("a station that does not match the profile call sign waits for proof, with a code", status == "pending" and st2["claim_code"].startswith("ark-") and st2["claim_status"] == "pending" and st2["claim_code"] in msg_)
    col6 = sd.Collector(); col6.configure()
    col6.handle("listener/AE5AU", json.dumps({"uploader_callsign": "AE5AU", "uploader_position": [34.686, -92.5243, 170.0], "uploader_antenna": "Vinnant FE403-P " + st2["claim_code"].upper()}))
    col6.flush(conn)
    check("the code in the station's antenna description completes the claim by itself", sd.station("AE5AU")["claim_status"] == "claimed")
    set_setting("sonde_claim_mode", "proof")
    status, msg_ = sd.claim(U(uid, "KD5VCF"), "KD5VCF")
    check("with Proof required, even a matching call sign waits", status == "pending")
    set_setting("sonde_claim_mode", "match")
    check("an invalid call sign is refused", sd.claim(owner, "x")[0] is None and sd.claim(owner, "bad<script>")[0] is None)
    set_setting("sonde_max_per_user", "2")
    check("the per-member limit applies", sd.claim(owner, "KD5VCF-WX")[0] is None)
    set_setting("sonde_max_per_user", "5")

    # ---- counties: a landing is placed in its county (fixture shapes), the lists feed the county pages and counts
    sq = lambda w, s, e, n: {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
    co.import_counties({"05": {"features": [
        {"properties": {"GEOID": "05119", "NAME": "Pulaski County", "BASENAME": "Pulaski", "STATE": "05", "CENTLAT": "34.77", "CENTLON": "-92.31", "AREALAND": 2000000000}, "geometry": sq(-92.6, 34.6, -92.0, 35.0)},
        {"properties": {"GEOID": "05045", "NAME": "Faulkner County", "BASENAME": "Faulkner", "STATE": "05", "CENTLAT": "35.15", "CENTLON": "-92.33", "AREALAND": 1700000000}, "geometry": sq(-92.6, 35.0, -92.0, 35.4)}]}})
    land = sd.landing_items(30)
    c1 = co.locate(a["landing_lat"], a["landing_lon"])
    check("the landing of a flight is inside a county, and the county's balloon list has it", land and c1 is not None and any(x["id"] == "TESTS001" for x in co.index()[c1["geoid"]]["balloons"]), (a["landing_lat"], a["landing_lon"], c1 and c1["name"]))
    check("the launch site is in its county's list and the counts include it", any(x["id"] == "72340" for x in co.index()["05119"]["sonde_sites"]) and co.counts()["05119"]["sonde_sites"] == 1)
    check("a point outside every county has no county", co.locate(40.0, -100.0) is None and co.locate(34.8, -92.3)["slug"] == "pulaski")

    # ---- the numbers for the pages
    cnt = sd.counts()
    check("counts: flights, aloft, National Weather Service, stations", cnt["flights"]["total"] >= 5 and cnt["flights"]["nws"] >= 2 and cnt["stations"]["claimed"] >= 2 and cnt["stations"]["pending"] >= 1, cnt)
    mf = sd.map_flights(hours=24)
    check("the map data has the flights with their trails", any(f["id"] == "TESTIN" for f in mf) and all("trail" in f and "href" in f for f in mf))
    check("search finds a flight by serial, a station by call sign and a site by name", [r["kind"] for r in sd.search("TESTS")] == ["flight"] and any(r["kind"] == "station" for r in sd.search("KD5VCF"))
          and any(r["kind"] == "site" for r in sd.search("Shreveport")))
    hc = sd.home_card()
    check("the home card counts only balloons over Arkansas", isinstance(hc["aloft"], int) and hc["nws_24h"] >= 1)

    # ---- thinning keeps flights and drops points
    set_setting("sonde_full_days", "1"); set_setting("sonde_thin_seconds", "60")
    d.execute("UPDATE sonde_points SET ts = ts - interval '3 days' WHERE serial='TESTS001'"); d.commit()
    before = d.execute("SELECT COUNT(*) AS n FROM sonde_points WHERE serial='TESTS001'").fetchone()["n"]
    sd.thin(conn)
    after = d.execute("SELECT COUNT(*) AS n FROM sonde_points WHERE serial='TESTS001'").fetchone()["n"]
    check("old flights are thinned to one position per minute but stay in the list", after < before / 8 and after > 100 and sd.get("TESTS001") is not None, (before, after))
    set_setting("sonde_full_days", "30")

    # ---- positions arrive as a list or as text; text read as a list gave 3.0, 4.0
    check("a position given as text is read whole: 34.7425,-92.2842,129 is not 3.0, 4.0", sd.pos3("34.7425,-92.2842,129.0") == (34.7425, -92.2842, 129.0) and sd.pos3([34.7425, -92.2842, 129.0]) == (34.7425, -92.2842, 129.0)
          and sd.pos3(["34.7", "-92.3"]) == (34.7, -92.3, None))
    check("positions that cannot be right are refused: empty, 0,0, out of range, one number", sd.pos3(None) is None and sd.pos3("") is None and sd.pos3([0, 0, 0]) is None and sd.pos3([95, 10]) is None and sd.pos3([10, 200]) is None and sd.pos3("34.7") is None)
    colp = sd.Collector(); colp.configure()
    colp.handle("sondes/TESTSTR", json.dumps(msg("TESTSTR", NOW, 34.84, -92.26, 500, 5.0, up="TEXTPOS", extra={"uploader_position": "34.5555,-92.4444,111.0"})))
    colp.handle("listener/TEXTPOS2", json.dumps({"uploader_callsign": "TEXTPOS2", "uploader_position": "34.6,-92.5,90", "software_name": "radiosonde_auto_rx"}))
    colp.handle("listener/BADPOS", json.dumps({"uploader_callsign": "BADPOS", "uploader_position": "0,0,0"}))
    colp.flush(conn)
    tp = sd.station("TEXTPOS")
    check("a station that uploaded its position as text is stored at the right place", tp is not None and near(tp["lat"], 34.5555, 1e-6) and near(tp["lon"], -92.4444, 1e-6) and near(tp["alt"], 111, .1) and near(sd.station("TEXTPOS2")["lon"], -92.5, 1e-6) and sd.station("BADPOS") is None)
    d.execute("INSERT INTO sonde_stations (callsign, lat, lon, privacy) VALUES ('WRONGPOS', 3.0, 4.0, 'general'), ('GOODPOS', 35.2, -92.1, 'general')"); d.commit()
    sd.repair_positions(conn)
    check("positions read wrongly in the past (whole numbers under 10) are dropped and real ones are kept", sd.station("WRONGPOS")["lat"] is None and near(sd.station("GOODPOS")["lat"], 35.2, 1e-6))

    # a station that lost a bad position gets the real one from SondeHub's listener history (one request, tested with the answer it gives); one that has a position is left alone
    d.execute("INSERT INTO sonde_stations (callsign, lat, lon, privacy) VALUES ('LOSTPOS', NULL, NULL, 'general')"); d.commit()
    n = sd.backfill_station_positions(conn, raw={"LOSTPOS": {"2026-10-08T18:40:00.000Z": {"uploader_position": [30.0, -90.0, 5.0]}, "2026-10-09T01:20:00.000Z": {"uploader_position": [34.32, -92.41, 95.0]}},
                                                 "GOODPOS": {"2026-10-09T01:20:00.000Z": {"uploader_position": [1.5, 2.5, 3.0]}}, "NOPOS": {"2026-10-09T01:20:00.000Z": {"software_name": "x"}}, "JUNK": "x"})
    check("a station without a position gets the newest one SondeHub knows; a station that has one is not touched; junk is skipped", n == 1 and near(sd.station("LOSTPOS")["lat"], 34.32, 1e-6) and near(sd.station("LOSTPOS")["alt"], 95, .1) and near(sd.station("GOODPOS")["lat"], 35.2, 1e-6))
    d.execute("DELETE FROM sonde_stations WHERE callsign='LOSTPOS'"); d.commit()

    # ---- how far each station has heard a balloon (the coverage map)
    pts_k = d.execute("SELECT lat, lon FROM sonde_points WHERE uploader='KD5VCF-LR'").fetchall()
    want = max(sd.km(34.7425, -92.2842, r["lat"], r["lon"]) for r in pts_k)
    got = sd.station("KD5VCF-LR")["reach_km"]
    check("a station's reach is the farthest balloon it has heard, measured as the flight is stored", got is not None and abs(got - want) < 1.0 and got > 5, (got, want))
    check("and it is on the public map data as a distance only", all("reach_km" in x for x in sd.map_stations()) and not any(k in sd.map_stations()[0] for k in ("az", "heading", "bearing")))
    d.execute("UPDATE sonde_stations SET reach_km = NULL"); d.execute("DELETE FROM settings WHERE key='sonde_reach_done'"); d.commit()
    from flask import g as _g
    _g.pop("_settings", None)   # this context loaded the settings before the line above removed one
    sd.repair_positions(conn)
    got2 = sd.station("KD5VCF-LR")["reach_km"]
    check("stations that had heard balloons before reach was kept get it from the stored points", got2 is not None and abs(got2 - want) < 1.0, (got2, want))

    # ---- the phase of a flight
    P = lambda **k: dict({"status": "aloft", "last_vel_v": 5.0, "last_alt": 12000.0, "max_alt": 12000.0}, **k)
    check("climbing while the balloon goes up", sd.phase(P()) == "climbing" and sd.phase(P(last_vel_v=None)) == "climbing")
    check("falling once it has burst: a fast descent, or well below its highest point", sd.phase(P(last_vel_v=-30.0, last_alt=25000, max_alt=31000)) == "falling" and sd.phase(P(last_vel_v=None, last_alt=9000, max_alt=31000)) == "falling")
    check("landed and lost keep their own", sd.phase(P(status="landed")) == "landed" and sd.phase(P(status="lost")) == "lost")
    check("the label says where it burst", "Burst at 101,706 ft, falling" == sd.phase_label(P(last_vel_v=-9.0, last_alt=20000, max_alt=31000)) and sd.phase_label(P()) == "Climbing")

    # ---- look angles from a station to a balloon
    az, el, rng = sd.look_angles(34.7425, -92.2842, 129.0, 34.7425, -92.2842, 10129.0)
    check("a balloon straight overhead: elevation 90, range its height", near(el, 90, .2) and near(rng, 10.0, .05), (az, el, rng))
    az, el, rng = sd.look_angles(34.0, -92.0, 100.0, 34.0, -91.0, 100.0)
    check("a point due east at the same height: azimuth 90, a little below the horizon, range about 92 km", near(az, 90, .5) and -1.0 < el < 0 and 90 < rng < 94, (az, el, rng))

    # ---- claiming by call sign, with or without a suffix; by itself
    for u in ("autoone", "autotwo", "autothree"):
        d.execute("DELETE FROM users WHERE username=%s", (u,))
    ua = d.execute("INSERT INTO users (username, email, password_hash, callsign) VALUES ('autoone', 'a1@example.com', 'x', 'kd5zzz') RETURNING id").fetchone()["id"]
    ub = d.execute("INSERT INTO users (username, email, password_hash, callsign) VALUES ('autotwo', 'a2@example.com', 'x', 'W9DUP') RETURNING id").fetchone()["id"]
    uc = d.execute("INSERT INTO users (username, email, password_hash, callsign) VALUES ('autothree', 'a3@example.com', 'x', 'W9DUP') RETURNING id").fetchone()["id"]
    d.commit()
    check("the call sign part is taken whatever follows it", [sd.base_call(x) for x in ("KD5ZZZ-LR", "kd5zzz-10", "KD5ZZZ/P", "KD5ZZZ_4", "KD5ZZZ Home", " KD5ZZZ ")] == ["KD5ZZZ"] * 6 and sd.base_call("") == "" and sd.base_call("-x") == "")
    cola = sd.Collector(); cola.configure()
    for call in ("KD5ZZZ", "KD5ZZZ-10", "KD5ZZZ/P", "KD5ZZZ_4", "KD5ZZZX", "W9DUP-1"):
        cola.handle("listener/" + call, json.dumps({"uploader_callsign": call, "uploader_position": [34.7, -92.2, 100.0], "software_name": "radiosonde_auto_rx"}))
    cola.flush(conn)
    owned = {r["callsign"]: r["owner_id"] for r in d.execute("SELECT callsign, owner_id FROM sonde_stations WHERE callsign IN ('KD5ZZZ','KD5ZZZ-10','KD5ZZZ/P','KD5ZZZ_4','KD5ZZZX','W9DUP-1')").fetchall()}
    check("stations are claimed by themselves for the member whose profile call sign they start with, whatever the suffix", owned["KD5ZZZ"] == ua and owned["KD5ZZZ-10"] == ua and owned["KD5ZZZ/P"] == ua and owned["KD5ZZZ_4"] == ua, owned)
    check("a different call sign that only begins the same (KD5ZZZX) is not claimed, and neither is a call sign that two members have", owned["KD5ZZZX"] is None and owned["W9DUP-1"] is None, owned)
    set_setting("sonde_max_per_user", "2")
    d.execute("INSERT INTO sonde_stations (callsign) VALUES ('KD5ZZZ-LR'), ('KD5ZZZ-11')"); d.commit()
    class UU: id, callsign = ua, "KD5ZZZ"
    claimed = sd.auto_claim_for_user(UU)
    check("opening the page claims the rest, up to the member's limit", len(claimed) == 0 and d.execute("SELECT COUNT(*) AS n FROM sonde_stations WHERE owner_id=%s", (ua,)).fetchone()["n"] == 4)
    set_setting("sonde_max_per_user", "9")
    claimed = sd.auto_claim_for_user(UU)
    check("with room, the page claims the stations that were still free", sorted(claimed) == ["KD5ZZZ-11", "KD5ZZZ-LR"], claimed)
    set_setting("sonde_auto_claim", "0")
    d.execute("INSERT INTO sonde_stations (callsign) VALUES ('KD5ZZZ-99')"); d.commit()
    check("with automatic claiming off nothing is claimed by itself", sd.auto_claim_for_user(UU) == [] and sd.station("KD5ZZZ-99")["owner_id"] is None)
    set_setting("sonde_auto_claim", "1")
    for u in ("autoone", "autotwo", "autothree"):
        d.execute("DELETE FROM users WHERE username=%s", (u,))
    d.execute("DELETE FROM sonde_stations WHERE callsign ~ '^(KD5ZZZ|W9DUP|TEXTPOS|BADPOS|WRONGPOS|GOODPOS)'"); d.commit()

    # ---- recovery reports from SondeHub (one request; tested with the answer it gives)
    n = sd.fetch_recoveries(conn, raw=[{"serial": "TESTS001", "recovered": True, "recovered_by": "AE5ID,KT5KMF", "description": "Sonde on driveway", "datetime": iso(NOW), "lat": 34.9, "lon": -92.1, "alt": 147.4, "recovery_software": "sondehub.org/found"},
                                         {"serial": "TESTS001", "recovered": False, "recovered_by": "W5LOOK", "description": "not here", "datetime": iso(NOW - timedelta(hours=1))}, {"serial": "", "datetime": iso(NOW)}, "junk"])
    recs = sd.recoveries_for("TESTS001")
    check("recovery reports are stored newest first, bad entries skipped", n == 2 and len(recs) == 2 and recs[0]["recovered"] and recs[0]["recovered_by"] == "AE5ID,KT5KMF" and not recs[1]["recovered"])
    fl = sd.flights(recovery="recovered")
    check("flights can be filtered by recovery", fl[0] == 1 and sd.flights(recovery="notfound")[0] == 0 and sd.flights(status="falling")[0] >= 0)
    check("the list rows carry the latest report", [r["rec_recovered"] for r in sd.flights(q="TESTS001")[1]] == [True])

    # ---- the feed's owner loop, outside any application context (as in a web worker): a flight, its launch site and a station arrive through a stand-in connection and reach the database
    import threading, time
    def fake_connect(col_):
        for sec in range(0, 120, 5):
            col_.handle("sondes/TESTLEAD", json.dumps(msg("TESTLEAD", NOW - timedelta(minutes=10) + timedelta(seconds=sec), 34.84, -92.26, 172 + 5 * sec, 5.0, up="LEADCALL")))
        col_.handle("listener/LEADCALL2", json.dumps({"uploader_callsign": "LEADCALL2", "uploader_position": [34.7, -92.3, 100.0], "software_name": "radiosonde_auto_rx"}))
        class Stub:
            def loop_stop(self): pass
            def disconnect(self): pass
        return Stub()
    real_connect = sd._connect
    sd._connect = fake_connect
    was_live = cfg("sonde_live_enabled")
    set_setting("sonde_live_enabled", "1")
    t0 = time.time()
    th = threading.Thread(target=lambda: sd.run_leader(app, should_stop=lambda: time.time() - t0 > 8)); th.start(); th.join(30)
    sd._connect = real_connect
    set_setting("sonde_live_enabled", was_live)
    lead = sd.get("TESTLEAD")
    check("the owner loop writes what the feed delivers (a flight with its launch site and a station) with no error", lead is not None and lead["site_id"] == "72340" and lead["agency"] == "NWS" and sd.station("LEADCALL2") is not None and sd.station("LEADCALL") is not None and not sd.STATE["error"], (lead and lead["site_id"], sd.STATE["error"]))
    stats = json.loads(d.execute("SELECT value FROM settings WHERE key='sonde_feed_stats'").fetchone()["value"])   # read fresh: this context's settings were loaded before the loop ran
    check("the loop saves its statistics for the status page", stats.get("msgs", 0) >= 25 and stats.get("per_min", 0) > 0, stats)

    # ---- a flight loaded by serial from the archive
    from app import sonde_archive as sa
    base = NOW - timedelta(days=30)
    frames = [{"serial": "ARCH0001", "datetime": iso(base + timedelta(seconds=k * 2)), "lat": 51.0 + k * 0.0005, "lon": 10.0, "alt": 300 + k * 5.0 if k < 1500 else 7500 - (k - 1500) * 12.0,
               "type": "RS41", "manufacturer": "Vaisala", "uploader_callsign": "DL1TEST", "frequency": 403.5, "frame": k} for k in range(2400)]
    frames.append({"serial": "OTHER", "datetime": iso(base), "lat": 1, "lon": 1, "alt": 5})
    ok_, msg_, ser_ = sa.load_serial(conn, "ARCH0001", packets=frames)
    r = sd.get("ARCH0001")
    check("an archived flight outside our area is loaded and kept as source archive", ok_ and r is not None and r["source"] == "archive" and r["type"] == "RS41", msg_)
    npts = d.execute("SELECT COUNT(*) AS n FROM sonde_points WHERE serial='ARCH0001'").fetchone()["n"]
    check("its positions are thinned to our interval (2400 frames 2 s apart become about 800 points)", 750 <= npts <= 850, npts)
    check("it is marked over (landed or lost), not in the air", r["status"] in ("landed", "lost"), r["status"])
    check("another serial's frames in the same file are ignored", d.execute("SELECT 1 FROM sondes WHERE serial='OTHER'").fetchone() is None)
    check("loading the same serial again says it is already here", sa.load_serial(conn, "arch0001", packets=frames)[1] == "That flight is already here.")
    check("a bad serial is refused", not sa.load_serial(conn, "../etc", packets=frames)[0])
    set_setting("sonde_full_days", "7"); set_setting("sonde_keep_days", "10")
    sd.thin(conn)
    check("the thinning leaves an archived flight alone", d.execute("SELECT COUNT(*) AS n FROM sonde_points WHERE serial='ARCH0001'").fetchone()["n"] == npts)
    d.execute("DELETE FROM sonde_points WHERE serial='ARCH0001'"); d.execute("DELETE FROM sondes WHERE serial='ARCH0001'"); d.execute("DELETE FROM settings WHERE key IN ('sonde_full_days', 'sonde_keep_days')"); d.commit()

    # ---- cleanup of the test data
    for t in ("sonde_heard", "sonde_points", "sondes", "sonde_recoveries"):
        d.execute(f"DELETE FROM {t}")
    d.execute("DELETE FROM sonde_stations"); d.execute("DELETE FROM users WHERE username IN ('sondeowner','sondeother')"); d.execute("TRUNCATE sonde_sites"); d.execute("TRUNCATE counties")
    for uid_, call_ in saved_calls:
        d.execute("UPDATE users SET callsign=%s WHERE id=%s", (call_, uid_))
    d.commit()
    conn.close()
print("ALL PASS" if ok_all else "SOME FAILED")
