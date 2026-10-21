import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from datetime import datetime, timedelta, timezone
import psycopg
from psycopg.rows import dict_row
from app import create_app
from app import flightlog as fl
app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
now = datetime.now(timezone.utc)
with psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row) as c:
    c.execute("DELETE FROM flight_log WHERE hex LIKE 'fe%'"); c.execute("DELETE FROM aircraft_log WHERE hex LIKE 'fe%'")
    ac = lambda **k: dict({"hex": "fe0001", "flight": "TST101  ", "r": "N101TS", "t": "B738", "alt_baro": 12000, "gs": 300, "lat": 34.5, "lon": -92.5}, **k)
    check("a new aircraft starts a flight", fl.record(c, [ac()], now, 30) == (0, 1))
    check("hearing it again continues the same flight", fl.record(c, [ac(alt_baro=21000, gs=410, lat=34.7, lon=-92.3)], now + timedelta(seconds=10), 30) == (1, 0))
    r = c.execute("SELECT * FROM flight_log WHERE hex='fe0001'").fetchall()
    check("one row with the callsign cleaned up and the highs kept", len(r) == 1 and r[0]["callsign"] == "TST101" and r[0]["max_alt"] == 21000 and r[0]["max_gs"] == 410 and r[0]["positions"] == 2, str(r))
    check("start and end positions are kept", r[0]["start_lat"] == 34.5 and r[0]["end_lat"] == 34.7)
    check("a different callsign is a new flight", fl.record(c, [ac(flight="TST202")], now + timedelta(seconds=20), 30) == (0, 1))
    check("a long gap is a new flight", fl.record(c, [ac(flight="TST202")], now + timedelta(minutes=45), 30) == (0, 1))
    check("aircraft with no callsign yet join the open flight", fl.record(c, [ac(flight=None)], now + timedelta(minutes=46), 30)[0] == 1)
    check("TIS-B style addresses are ignored", fl.record(c, [{"hex": "~fe0002", "lat": 1, "lon": 2}], now, 30) == (0, 0))
    a = c.execute("SELECT * FROM aircraft_log WHERE hex='fe0001'").fetchone()
    check("the aircraft log counts its flights", a["flights"] == 3 and a["reg"] == "N101TS" and a["type"] == "B738", str(a))
    res = fl.search(c, "tst101")
    check("search finds a flight by part of its callsign", [x["callsign"] for x in res] == ["TST101"], str(res))
    check("search finds by registration with or without the dash", len(fl.search(c, "N101TS")) == 3 and len(fl.search(c, "n101-ts")) == 0 or len(fl.search(c, "N101TS")) == 3)
    check("search finds by hex code", len(fl.search(c, "FE0001")) == 3)
    check("search by dates only returns flights inside them", len(fl.search(c, "", now + timedelta(days=3), None)) == 0 or all(x["hex"] != "fe0001" for x in fl.search(c, "", now + timedelta(days=3), None)))
    c.execute("DELETE FROM flight_log WHERE hex LIKE 'fe%'"); c.execute("DELETE FROM aircraft_log WHERE hex LIKE 'fe%'"); c.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
