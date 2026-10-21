"""In-container check of sondes.rebuild_reach_sectors: the coverage outline comes from the flights a station really heard.
docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sondecoverage_unit.py"""
import os, json
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app
from app import sondes
app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
with app.app_context():
    from app.db import get_db
    d = get_db()
    for t in ("sonde_points", "sonde_heard"):
        d.execute(f"DELETE FROM {t} WHERE serial LIKE 'ZZCV%'")
    d.execute("DELETE FROM sondes WHERE serial LIKE 'ZZCV%'"); d.execute("DELETE FROM sonde_stations WHERE callsign LIKE 'ZZCVS%'")
    d.execute("INSERT INTO sonde_stations (callsign, lat, lon, privacy, public) VALUES ('ZZCVS1', 34.7, -92.3, 'exact', true), ('ZZCVS2', 34.7, -92.3, 'exact', true)")
    d.execute("INSERT INTO sondes (serial, kind, type, first_seen, last_seen, status, frames) VALUES ('ZZCV1', 'radiosonde', 'RS41', now() - interval '3 hours', now() - interval '1 hour', 'landed', 9)")
    # one flight: north of the station 100 km at 12:00, east 80 km at 12:10, a far point at 12:30 that station 1 did not hear (after its last packet)
    for ts, la, lo in (("12:00", 35.6, -92.3), ("12:10", 34.7, -91.45), ("12:30", 34.7, -88.0)):
        d.execute("INSERT INTO sonde_points (serial, ts, lat, lon, alt) VALUES ('ZZCV1', date_trunc('day', now()) + %s::time, %s, %s, 10000)", (ts, la, lo))
    d.execute("INSERT INTO sonde_heard (serial, callsign, packets, first_ts, last_ts) VALUES ('ZZCV1', 'ZZCVS1', 20, date_trunc('day', now()) + '11:55'::time, date_trunc('day', now()) + '12:15'::time)")
    d.commit()
    n = sondes.rebuild_reach_sectors()
    s1 = json.loads(d.execute("SELECT reach_sectors FROM sonde_stations WHERE callsign='ZZCVS1'").fetchone()["reach_sectors"])
    s2 = d.execute("SELECT reach_sectors FROM sonde_stations WHERE callsign='ZZCVS2'").fetchone()["reach_sectors"]
    check("a station that heard the flight gets an outline", n >= 1 and s1, n)
    check("north (sector 0) is about 100 km and east (sector 9) about 76 km, where the flight really was while heard", 95 < s1[0] < 105 and 70 < max(s1[8], s1[9]) < 82, (s1[0], s1[8], s1[9]))
    check("the far point after its last packet is not counted", max(s1) < 110, max(s1))
    check("a station that heard nothing has no outline", s2 is None)
    check("the map data carries them for the exact-position station", any(x["id"] == "ZZCVS1" and x["sectors"] for x in sondes.map_stations()))
    # a coverage job that has never run starts at the next pass after an update, whatever the time of day
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from app import dataupdates as du
    from app.settings import set_setting
    early = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)   # 02:00 or 03:00 in Arkansas, before the daily update time
    set_setting("airports_update_time", "05:00"); set_setting("sondecoverage_update_days", "1"); set_setting("sondecoverage_last_import", ""); set_setting("sondecoverage_last_attempt", "")
    check("never run: due at once, even before the daily update time", du.due("sondecoverage", early, ZoneInfo("America/Chicago")))
    check("other jobs still wait for the daily time", not du.due("places", early, ZoneInfo("America/Chicago")) or du.days_of("places") == 0)
    set_setting("sondecoverage_last_attempt", early.isoformat())
    check("a failed try is not repeated within six hours", not du.due("sondecoverage", early, ZoneInfo("America/Chicago")))
    set_setting("sondecoverage_last_attempt", "")
    for t in ("sonde_points", "sonde_heard"):
        d.execute(f"DELETE FROM {t} WHERE serial LIKE 'ZZCV%'")
    d.execute("DELETE FROM sondes WHERE serial LIKE 'ZZCV%'"); d.execute("DELETE FROM sonde_stations WHERE callsign LIKE 'ZZCVS%'"); d.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
