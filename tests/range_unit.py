"""Runs inside the container: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/range_unit.py"""
import os, time
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["HUB_URL"] = ""
import psycopg
from psycopg.rows import dict_row
from datetime import date
from app import create_app, receivers, rangestats
from app.receivers import Parser, receiver_key

app = create_app()
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)

# geometry
nm, b = rangestats.distance_bearing(34.8, -92.3, 34.8, -91.3)
check("one degree of longitude at 34.8 N is about 49 nm, due east", 48 < nm < 50.5 and 89 < b < 91, (nm, b))
nm, b = rangestats.distance_bearing(34.8, -92.3, 35.8, -92.3)
check("one degree north is 60 nm at bearing 0", 59.9 < nm < 60.1 and (b < 0.5 or b > 359.5), (nm, b))
check("sectors are 10 degrees centred on north", rangestats.sector_of(0) == 0 and rangestats.sector_of(4.9) == 0 and rangestats.sector_of(5.1) == 1 and rangestats.sector_of(359) == 0 and rangestats.sector_of(180) == 18)

# the parser tells position messages from the rest
def frame(t, data): return b"\x1a" + bytes([t]) + data.replace(b"\x1a", b"\x1a\x1a")
RID = bytes.fromhex("aaaaaaaaaaaaaa01")
def msg(tc, icao="abc123"): return bytes([17 << 3 | 5]) + bytes.fromhex(icao) + bytes([tc << 3]) + bytes(9)
stream = frame(0xE3, RID) + frame(0x33, bytes(7) + msg(11)) + frame(0x33, bytes(7) + msg(4, "abc124")) + frame(0x33, bytes(7) + msg(19, "abc125")) + frame(0x33, bytes(7) + msg(20, "abc126"))
got = {icao: pos for _r, icao, pos in Parser().feed(stream)}
check("airborne position types (9-18, 20-22) are flagged, identification and velocity are not", got == {"abc123": True, "abc124": False, "abc125": False, "abc126": True}, got)

# pairing what a station heard with where the aircraft are
rid = receiver_key("aaaaaaaa-aaaa-aa01-0000-000000000001")
now = time.time()
receivers._seen_pos.clear(); rangestats.take_samples()
receivers._seen_pos[rid] = {"abc123": now, "old001": now - 60, "far001": now, "gone01": now}
station = {rid: ("aaaaaaaa-aaaa-aa01-0000-000000000001", 34.8, -92.3)}
aircraft = {"abc123": (35.8, -92.3, 30000), "old001": (35.0, -92.3, 1000), "far001": (45.0, -92.3, 30000)}
n = receivers.sample_ranges(station, aircraft, now, window=5, max_nm=450)
samples = dict(rangestats.take_samples())
check("a recent position within range is measured, old and too-far ones are not", n == 1 and len(samples) == 1, (n, samples))
(k, v), = samples.items()
check("the sample is the distance (60 nm) in the north sector with its altitude", abs(v[0] - 60) < 0.2 and k[2] == 0 and v[1] == 30000, (k, v))
check("an unknown station is ignored", receivers.sample_ranges({}, aircraft, now) == 0)

# saving keeps the farthest per station, day and sector
U = "aaaaaaaa-aaaa-aa01-0000-000000000001"
conn = psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row)
conn.execute("DELETE FROM feeders WHERE uuid=%s", (U,))
conn.execute("INSERT INTO feeders (uuid, station_id, name) VALUES (%s, 'RNG-1', 'Range Test')", (U,))
conn.commit()
today = date.today()
rangestats.save(conn, [((U, today, 0), (60.0, 30000)), ((U, today, 9), (120.0, 35000))])
rangestats.save(conn, [((U, today, 0), (80.0, 31000)), ((U, today, 9), (100.0, 1000))])
rows = {r["sector"]: (r["max_nm"], r["max_alt"]) for r in conn.execute("SELECT * FROM feeder_range WHERE feeder_uuid=%s", (U,)).fetchall()}
check("the farthest sample per sector wins, with its altitude", rows == {0: (80.0, 31000), 9: (120.0, 35000)}, rows)
with app.app_context():
    st = rangestats.station(U, 30)
    check("summary figures", st["period_max"] == 120.0 and st["all_max"] == 120.0 and st["today_max"] == 120.0 and st["sectors"][0] == 80.0 and len(st["sectors"]) == 36, st)
    check("network summary by station", rangestats.summary(30).get(U) == 120.0)
    svg = str(rangestats.polar_svg(st["sectors"]))
    check("the polar chart is an SVG with rings, directions and a shape", svg.startswith("<svg") and "<polygon" in svg and ">N<" in svg and "nm</text>" in svg)
    check("with no data the chart explains itself", "No range data yet" in str(rangestats.polar_svg([0.0] * 36)))
rangestats.prune(conn, 90)
check("recent days are kept by the retention", conn.execute("SELECT COUNT(*) AS n FROM feeder_range WHERE feeder_uuid=%s", (U,)).fetchone()["n"] == 2)
conn.execute("UPDATE feeder_range SET day = day - 400 WHERE feeder_uuid=%s", (U,)); conn.commit()
rangestats.prune(conn, 90)
check("and old days are removed", conn.execute("SELECT COUNT(*) AS n FROM feeder_range WHERE feeder_uuid=%s", (U,)).fetchone()["n"] == 0)
conn.execute("DELETE FROM feeders WHERE uuid=%s", (U,)); conn.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
