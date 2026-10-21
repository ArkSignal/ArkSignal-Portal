"""Seeds demonstration balloon data (flights, stations, launch sites, county shapes) through the real collector, or removes it again. Used by sondes_test.py and by hand to look at the pages.
docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app <portal container> python /tmp/sondes_seed.py [seed|clean]"""
import os, sys
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
import json
from datetime import datetime, timedelta, timezone
import psycopg
from psycopg.rows import dict_row
from app import create_app
from app import sondes as sd
from app import counties as co
from app.settings import set_setting

app = create_app()
NOW = datetime.now(timezone.utc).replace(microsecond=0)
iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def msg(serial, t, lat, lon, alt, vv, up, upos, ant="Commercial UHF omni", typ="RS41", extra=None):
    m = {"software_name": "radiosonde_auto_rx", "software_version": "1.9.0", "uploader_callsign": up, "uploader_position": upos, "uploader_antenna": ant, "datetime": iso(t), "manufacturer": "Vaisala",
         "type": typ, "subtype": typ + "-SG", "serial": serial, "frame": int(t.timestamp()) % 1000000, "lat": lat, "lon": lon, "alt": alt, "temp": 25 - alt / 150 if alt < 11000 else -56.5 - (alt - 11000) / 4000, "humidity": max(3, 75 - alt / 330),
         "pressure": 1013 * (1 - 2.25577e-5 * alt) ** 5.25588 if alt < 11000 else 226 * 2.718 ** (-(alt - 11000) / 6341), "vel_v": vv, "vel_h": 8 + alt / 2500, "heading": 80 + alt / 800, "sats": 11, "batt": 2.7,
         "frequency": 403.01, "snr": 16.0}
    if extra:
        m.update(extra)
    return m


def flight(col, serial, start, site, until_s, up="KD5VCF-LR", upos=(34.7425, -92.2842, 129.0), also=None, typ="RS41", burst_s=6000, drift=(0.000008, 0.000015)):
    for sec in range(0, until_s):
        t = start + timedelta(seconds=sec)
        if sec <= burst_s:
            alt, vv = site[2] + 5 * sec, 5.0
        else:
            alt, vv = max(site[2] + 40, site[2] + 5 * burst_s - 8 * (sec - burst_s)), -8.0
        lat, lon = site[0] + drift[0] * sec, site[1] + drift[1] * sec
        col.handle("sondes/" + serial, json.dumps(msg(serial, t, lat, lon, alt, vv, up, list(upos), typ=typ)))
        if also and sec % 4 == 0:
            col.handle("sondes/" + serial, json.dumps(msg(serial, t, lat, lon, alt, vv, also[0], list(also[1]), ant="Vinnant FE403-P", typ=typ, extra={"snr": 8.0})))


def seed():
    with app.app_context():
        from app.db import get_db
        d = get_db()
        for k, v in (("sonde_enabled", "1"), ("sonde_live_enabled", "0"), ("sonde_recovery_minutes", "0"), ("sonde_keep_every_seconds", "5"), ("public_cache_seconds", "0")):
            set_setting(k, v)
        sd.import_sites({"72340": {"station_name": "North Little Rock, North Little Rock Airport (United States)", "position": [-92.2594, 34.835], "alt": 172, "rs_types": ["54"], "times": ["0:00:00", "0:12:00"]},
                         "72248": {"station_name": "Shreveport, Shreveport Regional Airport (United States)", "position": [-93.8414, 32.4511], "alt": 80, "rs_types": [["54", "403.01"]], "times": ["0:00:00", "0:12:00"]},
                         "72440": {"station_name": "Springfield, Springfield Regional Airport (United States)", "position": [-93.4014, 37.2347], "alt": 394, "rs_types": ["54"], "times": ["0:00:00", "0:12:00"]},
                         "74646": {"station_name": "ARM Southern Great Plains (United States)", "position": [-97.4877, 36.6079], "alt": 300, "rs_types": ["41"], "times": []}})
        if "--real-counties" in sys.argv:
            co.import_counties()
        else:
            sq = lambda w, s, e, n: {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
            co.import_counties({"05": {"features": [
                {"properties": {"GEOID": "05119", "NAME": "Pulaski County", "BASENAME": "Pulaski", "STATE": "05", "CENTLAT": "34.77", "CENTLON": "-92.31", "AREALAND": 2000000000}, "geometry": sq(-92.6, 34.6, -92.0, 35.0)},
                {"properties": {"GEOID": "05045", "NAME": "Faulkner County", "BASENAME": "Faulkner", "STATE": "05", "CENTLAT": "35.15", "CENTLON": "-92.33", "AREALAND": 1700000000}, "geometry": sq(-92.6, 35.0, -92.0, 35.4)}]}})
        conn = psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row)
        col = sd.Collector(); col.configure()
        flight(col, "TESTAAA1", NOW - timedelta(minutes=40), (34.835, -92.2594, 172), 2400, also=("AE5AU", (34.686, -92.5243, 170.0)))          # in the air now, climbing
        flight(col, "TESTBBB2", NOW - timedelta(hours=5), (34.835, -92.2594, 172), 10000, also=("AE5AU", (34.686, -92.5243, 170.0)))              # landed hours ago
        flight(col, "TESTCCC3", NOW - timedelta(hours=7), (32.4511, -93.8414, 80), 4500, up="W5XYZ", upos=(32.6, -93.7, 90.0), burst_s=5000)    # signal lost high
        flight(col, "TESTDDD4", NOW - timedelta(minutes=120), (34.835, -92.2594, 172), 7200, burst_s=6000)   # burst 20 minutes ago, falling now
        col.handle("amateur/N0TEST-11", json.dumps({"payload_callsign": "N0TEST-11", "datetime": iso(NOW - timedelta(minutes=3)), "lat": 35.5, "lon": -92.0, "alt": 21000.0, "vel_v": 5.0, "uploader_callsign": "KD5VCF-LR", "uploader_position": [34.7425, -92.2842, 129.0]}))
        col.handle("listener/CHASER-9", json.dumps({"uploader_callsign": "CHASER-9", "uploader_position": [34.5123, -92.5321, 100], "mobile": True, "software_name": "rdzTTGOsonde"}))
        col.handle("prediction/TESTAAA1", json.dumps({"serial": "TESTAAA1", "data": [{"time": 1, "lat": 34.9, "lon": -92.2, "alt": 15000}, {"time": int((NOW + timedelta(hours=2)).timestamp()), "lat": 35.05, "lon": -91.4, "alt": 150}], "burst_altitude": 31000}))
        col.flush(conn)
        d.execute("UPDATE sondes SET last_seen = now() - interval '3 hours' WHERE serial IN ('TESTBBB2','TESTCCC3')"); d.commit()
        d.execute("UPDATE sondes SET last_seen = now() - interval '4 hours' WHERE serial = 'TESTCCC3'"); d.commit()
        sd.sweep(conn)
        d.execute("UPDATE sondes SET last_seen = now() - interval '3 hours' WHERE serial='TESTBBB2'"); d.commit()
        sd.fetch_recoveries(conn, raw=[{"serial": "TESTBBB2", "recovered": True, "recovered_by": "KD5TEST", "description": "In a field by the road", "datetime": iso(NOW - timedelta(hours=1)), "lat": 34.9, "lon": -92.1, "alt": 150, "recovery_software": "sondehub.org/found"},
                                       {"serial": "TESTCCC3", "recovered": False, "recovered_by": "W5LOOK", "description": "Looked both sides of the road", "datetime": iso(NOW - timedelta(hours=2)), "lat": 32.8, "lon": -93.0, "alt": 0}])
        conn.close()
        print("seeded", sd.counts())


def clean():
    with app.app_context():
        from app.db import get_db
        d = get_db()
        for t in ("sonde_heard", "sonde_points", "sondes", "sonde_stations", "sonde_sites", "sonde_recoveries"):
            d.execute(f"DELETE FROM {t}")
        d.execute("TRUNCATE counties")
        d.commit()
        print("cleaned")


if __name__ == "__main__":
    clean() if "clean" in sys.argv else seed()
