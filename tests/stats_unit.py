"""In-container checks of wxstats.py and sondestats.py: the filters (sources, counties, cities, ZIP codes, a center and a radius, stations, dates, flights, receiving stations, sites, kinds) and the numbers.
docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/stats_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from datetime import datetime, timedelta, timezone
from app import create_app
from app import wxstats, sondestats

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

with app.app_context():
    from app.db import get_db
    d = get_db()
    d.execute("DELETE FROM wx_obs WHERE sid LIKE 'ZZ%'"); d.execute("DELETE FROM wx_station_dir WHERE sid LIKE 'ZZ%'")
    rows = [("members", "ZZ1", "Pulaski County", "Little Rock", "72201", 34.74, -92.29, [60, 64, 70]), ("members", "ZZ2", "Saline County", "Benton", "72015", 34.56, -92.59, [80, 82, 84]),
            ("aprs", "ZZA1", "Pulaski County", "Maumelle", "72113", 34.86, -92.40, [75, 75]), ("airport", "ZZAP", "Faulkner County", "Conway", "72032", 35.08, -92.46, [90, 88])]
    for src, sid, county, city, zp, la, lo, temps in rows:
        d.execute("INSERT INTO wx_station_dir (src, sid, name, lat, lon, state, county, city, zip) VALUES (%s,%s,%s,%s,%s,'AR',%s,%s,%s)", (src, sid, sid + " name", la, lo, county, city, zp))
        for i, t in enumerate(temps):
            d.execute("INSERT INTO wx_obs (ts, src, sid, temp_f, hum) VALUES (now() - make_interval(mins => %s), %s, %s, %s, 50)", (10 * (len(temps) - i), src, sid, t))
    d.commit()
    ids = lambda r: sorted(s["sid"] for s in r["stations"] if s["sid"].startswith("ZZ"))
    check("every source by default", ids(wxstats.stats({})) == ["ZZ1", "ZZ2", "ZZA1", "ZZAP"], ids(wxstats.stats({})))
    check("sources: members only, and APRS plus airport", ids(wxstats.stats({"sources": "members"})) == ["ZZ1", "ZZ2"] and ids(wxstats.stats({"sources": "aprs,airport"})) == ["ZZA1", "ZZAP"])
    check("counties (several), cities and ZIP codes", ids(wxstats.stats({"counties": "Pulaski County"})) == ["ZZ1", "ZZA1"] and ids(wxstats.stats({"counties": "Saline County|Faulkner County"})) == ["ZZ2", "ZZAP"]
          and ids(wxstats.stats({"cities": "Benton"})) == ["ZZ2"] and ids(wxstats.stats({"zips": "72113|72201"})) == ["ZZ1", "ZZA1"])
    check("a county and a source together narrow it", ids(wxstats.stats({"counties": "Pulaski County", "sources": "aprs"})) == ["ZZA1"])
    check("a center and a radius", ids(wxstats.stats({"near": "34.74,-92.29,8", "sources": "members,aprs"})) == ["ZZ1"] and ids(wxstats.stats({"near": "34.74,-92.29,30", "sources": "members,aprs"})) == ["ZZ1", "ZZ2", "ZZA1"], ids(wxstats.stats({"near": "34.74,-92.29,30", "sources": "members,aprs"})))
    check("particular stations", ids(wxstats.stats({"stations": "members:ZZ2|airport:ZZAP"})) == ["ZZ2", "ZZAP"])
    s = wxstats.stats({"stations": "members:ZZ1", "metric": "temp_f"})
    z = [x for x in s["stations"] if x["sid"] == "ZZ1"][0]
    check("the numbers of a station: latest, lowest, average, highest, readings", z["latest"] == 70 and z["min"] == 60 and z["max"] == 70 and abs(z["avg"] - 64.667) < .01 and z["n"] == 3, z)
    check("the series is a list of buckets with min, average and max", s["series"] and all(k in s["series"][0] for k in ("ts", "min", "avg", "max", "n")), s["series"][:1])
    check("an unknown reading falls back to temperature, an unknown period to 24 hours", wxstats.stats({"metric": "bogus"})["metric"] == "temp_f" and wxstats.stats({"period": "bogus"})["period"] == "24h")
    check("injection in a filter is just a value that matches nothing", wxstats.stats({"counties": "x'; drop table wx_obs; --"})["selected"] == 0 and d.execute("SELECT count(*) AS n FROM wx_obs").fetchone()["n"] > 0)
    check("the pickers list what is kept", any(c["name"] == "Pulaski County" and c["n"] >= 2 for c in wxstats.areas()["counties"]) and any(x["sid"] == "ZZ1" for x in wxstats.find_stations("ZZ1")))
    n = wxstats.collect(app)
    check("a snapshot of every reporting station can be taken", isinstance(n, int) and n >= 0, n)

    # radiosondes
    d.execute("DELETE FROM sondes WHERE serial LIKE 'ZZS%'"); d.commit()
    now = datetime.now(timezone.utc)
    flights = [("ZZSA", "radiosonde", "NWS", "72340", "North Little Rock", 30000, 4), ("ZZSB", "radiosonde", "", "", "", 20000, 3), ("ZZSC", "amateur", "", "", "", 12000, 2)]
    for serial, kind, agency, site, sname, alt, hours in flights:
        d.execute("INSERT INTO sondes (serial, kind, type, first_seen, last_seen, launch_time, launch_alt, max_alt, max_alt_ts, status, agency, site_id, site_name, frames) VALUES (%s,%s,'RS41',%s,%s,%s,100,%s,%s,'landed',%s,%s,%s,10)",
                  (serial, kind, now - timedelta(hours=hours + 2), now - timedelta(hours=hours), now - timedelta(hours=hours + 2), alt, now - timedelta(hours=hours + 1), agency, site, sname))
    d.execute("INSERT INTO sonde_heard (serial, callsign, packets) VALUES ('ZZSA', 'ZZSTN-1', 5), ('ZZSB', 'ZZSTN-1', 2), ('ZZSB', 'ZZSTN-2', 3)"); d.commit()
    sid = lambda r: sorted(f["serial"] for f in r["flights"] if f["serial"].startswith("ZZS"))
    check("flights: everything in the dates, then by kind, site, station and serial", sid(sondestats.stats({})) == ["ZZSA", "ZZSB", "ZZSC"] and sid(sondestats.stats({"sources": "nws"})) == ["ZZSA"] and sid(sondestats.stats({"sources": "amateur"})) == ["ZZSC"]
          and sid(sondestats.stats({"sources": "other"})) == ["ZZSB"] and sid(sondestats.stats({"sites": "72340"})) == ["ZZSA"] and sid(sondestats.stats({"stations": "ZZSTN-1"})) == ["ZZSA", "ZZSB"]
          and sid(sondestats.stats({"stations": "ZZSTN-2"})) == ["ZZSB"] and sid(sondestats.stats({"flights": "zzsc|zzsa"})) == ["ZZSA", "ZZSC"])
    r = sondestats.stats({"flights": "ZZSA|ZZSB|ZZSC"})
    check("the summary: flights, average burst height, kinds", r["summary"]["flights"] == 3 and abs(r["summary"]["burst_avg"] - 20666.7) < 1 and r["summary"]["nws"] == 1 and r["summary"]["amateur"] == 1, r["summary"])
    check("the stations that heard the most, the flights per day and the burst histogram", r["top_stations"][0] == {"callsign": "ZZSTN-1", "flights": 2} and sum(x["n"] for x in r["per_day"]) == 3 and sum(x["n"] for x in r["burst_hist"]) == 3)
    check("dates outside the flights hold none, and bad dates become the last 30 days", sid(sondestats.stats({"from": "2020-01-01", "to": "2020-01-31", "flights": "ZZSA"})) == [] and sondestats.stats({"from": "x"})["to"] >= sondestats.stats({"from": "x"})["from"])
    check("the picker finds flights", "ZZSA" in sondestats.find("ZZS")["flights"])
    d.execute("DELETE FROM sondes WHERE serial LIKE 'ZZS%'"); d.execute("DELETE FROM wx_obs WHERE sid LIKE 'ZZ%'"); d.execute("DELETE FROM wx_station_dir WHERE sid LIKE 'ZZ%'"); d.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
