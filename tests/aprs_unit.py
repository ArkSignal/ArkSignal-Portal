"""In-container checks of the APRS service (aprsd.py): what is kept from a packet and what is dropped on purpose (messages, bulletins, status, telemetry), the kind of station from its symbol,
weather observations and the wind that rides in the course and speed fields, objects, hidden call signs, and the stored data (latest position, trail only for moving stations, latest weather).
docker cp this file to /tmp and run: docker exec -w /app -e PYTHONPATH=/app aprs python /tmp/aprs_unit.py"""
import sys
import time
sys.path.insert(0, "/app")
import aprsd as a

ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

a.init_db()

# ---- what a packet gives
r = a.parse_line("KI5LAW-R2>APSTAR,TCPIP*,qAC,T2LANE:!3312.37N/09239.23WrPHG5580/A=000225AllStar Repeater Hub 684953 444.625MHz T146.2 +5.0")
check("a repeater beacon gives its place, kind, altitude and comment", r and r["call"] == "KI5LAW-R2" and r["kind"] == "repeater" and abs(r["lat"] - 33.2062) < .001 and abs(r["alt"] - 68.58) < .1 and "AllStar" in r["comment"] and r["via"] == "T2LANE", r)
r = a.parse_line("N5IRU-1>APRS,TCPIP*,qAC,T2X:!3446.30N/09217.20W#PHG7360 W5DIGI Digipeater")
check("a digipeater symbol is a digipeater", r and r["kind"] == "digipeater", r)
r = a.parse_line("W5ABC-9>APRS,TCPIP*,qAC,T2X:!3446.30N/09217.20W>088/045 on the move")
check("a car symbol is a moving station with its course and speed", r and r["kind"] == "mobile" and r["course"] == 88 and r["speed"] and r["speed"] > 80, r)
r = a.parse_line("FSMEOC>BEACON,W5ANR-9,WIDE1,KK5I-4,WIDE2-1,qAR,N5IRU:;146.64-OK*111111z3504.56N/09440.91WrT088 R90m Net TH7PM")
check("an object is stored under its own name and marked an object", r and r["call"] == "146.64-OK" and r["object"] and r["kind"] == "repeater", r)
r = a.parse_line("CW1234>APRS,TCPXX*,qAX,CWOP-5:@101523z3446.30N/09217.20W_090/005g010t068r000p000P000h55b10132")
check("a weather station gives temperature (degrees C), humidity and pressure (hPa)", r and r["kind"] == "weather" and r["wx"] and abs(r["wx"]["temp_c"] - 20.0) < .1 and r["wx"]["humidity"] == 55 and abs(r["wx"]["pressure_hpa"] - 1013.2) < .1, r)
check("its wind rides in the course and speed fields: 090 degrees at 5 mph is 2.2 m/s, and it is not a moving station", r["wx"]["wind_dir"] == 90 and abs(r["wx"]["wind_ms"] - 2.24) < .05 and r["course"] is None and r["speed"] is None, r["wx"])
check("a gust of 10 mph is 4.5 m/s", abs(r["wx"]["gust_ms"] - 4.47) < .05, r["wx"])
check("a weather station that is not a car does not get a trail kind", r["kind"] == "weather")
check("messages are never kept", a.parse_line("N5IRU>APRS,TCPIP*::W5ABC-9  :hello there{12") is None)
check("bulletins are never kept", a.parse_line("N5IRU>APRS,TCPIP*::BLN1     :tornado warning") is None)
check("status text is never kept", a.parse_line("N5IRU>APRS,TCPIP*:>status text here") is None)
check("telemetry is never kept", a.parse_line("N5IRU>APRS,TCPIP*:T#005,199,000,255,073,123,01101001") is None)
check("server comments and rubbish give nothing", a.parse_line("# aprsc 2.1.21") is None and a.parse_line("") is None and a.parse_line("not a packet") is None)
check("a position outside the globe is dropped", a.parse_line("W5ABC>APRS:!9999.99N/09217.20W>") is None)

# ---- storing: the latest position, a trail only for what moves, the latest weather
TEST_CALLS = ["W5CAR-9", "W5CAR-7", "W5DIG-1", "CW1234"]
def clean():
    with a.db() as c:
        for t in ("trail", "weather", "stations"):
            c.execute(f"DELETE FROM {t} WHERE call = ANY(%s)", (TEST_CALLS,))
        c.execute("DELETE FROM hidden WHERE call = 'W5CAR'")
clean()
recs = [a.parse_line("W5CAR-9>APRS,TCPIP*,qAC,T2X:!3446.30N/09217.20W>088/045 first"), a.parse_line("W5DIG-1>APRS,TCPIP*,qAC,T2X:!3500.00N/09200.00W#PHG7360 digi"),
        a.parse_line("CW1234>APRS,TCPXX*,qAX,CWOP-5:@101523z3446.30N/09217.20W_090/005g010t068r000p000P000h55b10132")]
for rec in recs:
    a.Q.put(rec)
import threading
threading.Thread(target=a.writer, daemon=True).start()
time.sleep(4)
with a.db() as c:
    n = c.execute("SELECT COUNT(*) AS n FROM stations WHERE call = ANY(%s)", (TEST_CALLS,)).fetchone()["n"]
    trail = c.execute("SELECT call, COUNT(*) AS n FROM trail WHERE call = ANY(%s) GROUP BY call", (TEST_CALLS,)).fetchall()
    wx = c.execute("SELECT * FROM weather WHERE call = ANY(%s)", (TEST_CALLS,)).fetchall()
check("every station is stored once with its latest position", n == 3, n)
check("only the moving station has a trail", [(t["call"], t["n"]) for t in trail] == [("W5CAR-9", 1)], trail)
check("the weather station's observation is stored", len(wx) == 1 and wx[0]["call"] == "CW1234" and wx[0]["humidity"] == 55)
a.Q.put(a.parse_line("W5CAR-9>APRS,TCPIP*,qAC,T2X:!3446.50N/09217.30W>088/045 second"))
time.sleep(3)
with a.db() as c:
    s = c.execute("SELECT lat, packets, comment, moved IS NOT NULL AS moved FROM stations WHERE call='W5CAR-9'").fetchone()
check("a second packet updates the same station (latest position, packet count, comment, moved)", s["packets"] == 2 and abs(s["lat"] - 34.775) < .001 and "second" in s["comment"] and s["moved"], s)

# ---- a hidden call sign (without its SSID) keeps every SSID of it from being stored
with a.db() as c:
    c.execute("INSERT INTO hidden (call, reason) VALUES ('W5CAR', 'test')")
a.Q.put(a.parse_line("W5CAR-9>APRS,TCPIP*,qAC,T2X:!3446.50N/09217.30W>088/045 third"))
time.sleep(3)
a.Q.put(a.parse_line("W5CAR-7>APRS,TCPIP*,qAC,T2X:!3446.50N/09217.30W>088/045 fourth"))
time.sleep(3)
with a.db() as c:
    new = c.execute("SELECT COUNT(*) AS n FROM stations WHERE call = 'W5CAR-7'").fetchone()["n"]
check("a new SSID of a hidden call sign is not stored", new == 0, new)

# the symbol table decides what a letter means: "a" is an ambulance only in the primary table (an overlay on the alternate table is ARES, RACES or WinLink)
k = a.kind_of
check("an ambulance symbol in the primary table is an ambulance", k("a", "/", 0, False) == "ambulance")
check("the same letter in the alternate table or with an overlay is an ordinary station", k("a", "L", 0, False) == "fixed" and k("a", "\\", 0, False) == "fixed" and k("a", "W", 0, False) == "fixed")
check("fire, police and repeater are those only in the primary table", (k("f", "/", 0, False), k("!", "/", 0, False), k("r", "/", 0, False)) == ("fire", "police", "repeater") and (k("f", "\\", 0, False), k("!", "D", 0, False), k("r", "H", 0, False)) == ("fixed", "fixed", "fixed"))
check("a digipeater is one in either table, and a moving station is mobile", k("#", "/", 0, False) == "digipeater" and k("#", "L", 0, False) == "digipeater" and k("a", "L", 20, False) == "mobile")

clean()
print("ALL PASS" if ok_all else "SOME FAILED")
