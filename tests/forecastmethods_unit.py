"""The pure parts of the wind and Tawhiri forecast methods (deploy/predict/forecastmethods.py): no network, no database."""
import math, os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", "predict"))
import forecastmethods as fm
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
H = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
def doc(speed, dirn):
    h = {"time": ["2026-10-20T11:00", "2026-10-20T12:00"]}
    for i, lv in enumerate(fm.LEVELS):
        h[f"wind_speed_{lv}hPa"] = [0, speed(lv)]
        h[f"wind_direction_{lv}hPa"] = [0, dirn(lv)]
        h[f"geopotential_height_{lv}hPa"] = [0, 44330 * (1 - (lv / 1013.25) ** 0.1903)]
    return {"hourly": h}
col = fm.parse_column(doc(lambda lv: 10.0, lambda lv: 270), H)
check("the answer is read into a column by height, low to high", len(col) == len(fm.LEVELS) and col == sorted(col) and col[-1][0] > 25000)
check("a wind from the west (270) blows east: u > 0, v about 0", col[0][1] > 9.9 and abs(col[0][2]) < 0.01, col[0])
col_s = fm.parse_column(doc(lambda lv: 10.0, lambda lv: 180), H)
check("a wind from the south blows north: v > 0", col_s[0][2] > 9.9 and abs(col_s[0][1]) < 0.01)
check("a missing hour gives no column", fm.parse_column(doc(lambda lv: 1, lambda lv: 1), datetime(2026, 10, 21, 0, tzinfo=timezone.utc)) == [] and fm.parse_column({}, H) == [])
u, v = fm.wind_at(col, 5000)
check("wind between levels is interpolated and beyond the ends is held", abs(u - 10) < 0.01 and fm.wind_at(col, -100) == (col[0][1], col[0][2]) and fm.wind_at(col, 99999) == (col[-1][1], col[-1][2]))
check("the fall speed grows with height", fm.descent_speed(0, 6) == 6 and fm.descent_speed(30000, 6) > 40)
pts, burst = fm.fly(34.8, -92.3, 170, 30000, 5.0, 6.0, 250, col, col)
check("a steady west wind carries the balloon east, a long way, and it ends on the ground", pts[-1][2] > pts[0][2] + 0.5 and abs(pts[-1][1] - 34.8) < 0.05 and pts[-1][3] <= 260 and burst[2] >= 29900, (pts[-1], burst))
mins = pts[-1][0] / 60
check("the whole flight takes about the usual two hours", 90 <= mins <= 150, mins)
none_pts, _b = fm.fly(34.8, -92.3, 170, 30000, 5.0, 6.0, 250, [], [])
check("with no wind it comes down where it went up", abs(none_pts[-1][1] - 34.8) < 1e-3 and abs(none_pts[-1][2] + 92.3) < 1e-3)
check("the path can be thinned and keeps its first and last point", len(fm.thin(pts, 40)) == 40 and fm.thin(pts, 40)[0] == pts[0] and fm.thin(pts, 40)[-1] == pts[-1])
q = fm.tawhiri_query(34.8, -92.3, H, 170, 5, 30000, 6)
check("the Tawhiri request wants longitude 0 to 360 and a UTC time", abs(float(q["launch_longitude"]) - 267.7) < 1e-6 and q["launch_datetime"] == "2026-10-20T12:00:00Z" and q["profile"] == "standard_profile")
ans = {"prediction": [{"stage": "ascent", "trajectory": [{"latitude": 34.8, "longitude": 267.7, "altitude": 170, "datetime": "2026-10-20T12:00:00+00:00"}, {"latitude": 34.9, "longitude": 268.0, "altitude": 30000, "datetime": "2026-10-20T13:10:00+00:00"}]},
                          {"stage": "descent", "trajectory": [{"latitude": 35.0, "longitude": 268.4, "altitude": 250, "datetime": "2026-10-20T13:50:00+00:00"}]}]}
got = fm.parse_tawhiri(ans, H)
check("Tawhiri's answer is read: longitudes back to -180..180, burst at the end of the ascent, the landing last", got and got[0][-1][2] < 0 and abs(got[0][-1][2] + 91.6) < 1e-6 and got[1] == [34.9, -92.0, 30000] and got[0][-1][0] == 6600, got)
check("a broken answer gives nothing", fm.parse_tawhiri({"error": "x"}, H) is None and fm.parse_tawhiri({"prediction": []}, H) is None)
print("ALL PASS" if ok_all else "SOME FAILED")
