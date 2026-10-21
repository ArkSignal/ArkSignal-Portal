"""Our own NOAA GFS data in the prediction service: the interpolation and unit arithmetic (checked in the container with made-up fields whose answers are known), the real cycle read from NOAA, the surface
forecast and the winds by height it gives, the wind predictor using it, the Weather for a Place section, the admin status and button, and the settings. Needs the prediction service on the test box
(sh tests/deploy_predict.sh) with the internet; the first cycle takes a couple of minutes to read."""
import json, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
SSH = lambda cmd: subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True)
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
PRED = f"http://{__import__('os').environ.get('TEST_HOST', 'test-box.invalid')}:18470"

UNIT = r'''
import sys, os, json, array, math
sys.path.insert(0, "/app")
os.environ["DATABASE_URL"] = "postgresql://predict:predict@predict-db:5432/predict"
from datetime import datetime, timedelta, timezone
import gfsdata as g
out = {}
cyc = datetime(2026, 10, 16, 18, tzinfo=timezone.utc)
# a 3 x 3 grid: lat 30 down to 29 (dlat -0.5), lon -92 to -91 (dlon 0.5); value = 10*row + col
grid = {"nlat": 3, "nlon": 3, "lat0": 30.0, "lon0": -92.0, "dlat": -0.5, "dlon": 0.5}
def arr(f): return array.array("f", [f(i, j) for i in range(3) for j in range(3)])
F = {}
def put(step, name, level, f): F[(step, name, level)] = arr(f)
for s in (0, 3, 6):
    put(s, "t2", 0, lambda i, j: 280.0 + s)          # 280 K at step 0 (=44.33 F), warming 1 K per hour
    put(s, "u10", 0, lambda i, j: -3.0); put(s, "v10", 0, lambda i, j: -4.0)    # wind FROM the north-east at 5 m/s
    put(s, "rh2", 0, lambda i, j: 50.0); put(s, "gust", 0, lambda i, j: 10.0); put(s, "prmsl", 0, lambda i, j: 101325.0); put(s, "tcc", 0, lambda i, j: 40.0)
    for L, z, u, v in ((1000, 100.0, 1.0, 2.0), (850, 1500.0, 5.0, 6.0), (500, 5500.0, 20.0, -4.0)):
        put(s, "z", L, lambda i, j, z=z: z); put(s, "u", L, lambda i, j, u=u: u + 0.0); put(s, "v", L, lambda i, j, v=v: v)
put(0, "t2", 0, lambda i, j: 280.0)
put(3, "apcp", 0, lambda i, j: 3.0)       # a window that started at hour 0: 3 mm by hour 3
put(6, "apcp", 0, lambda i, j: 5.0)       # the same window by hour 6: 5 mm, so 2 mm in the second three hours
d = {"cycle": cyc, "hours": 6, "steps": [0, 3, 6], "fetched": cyc, "grid": grid, "fields": F}
g._load = lambda db: d
mk = array.array("f", [10.0 * i + j for i in range(3) for j in range(3)])
d["fields"][(0, "test", 0)] = mk
out["corner"] = g._at(d, 0, "test", 0, 30.0, -92.0)             # 0
out["far_corner"] = g._at(d, 0, "test", 0, 29.0, -91.0)         # 22
out["middle"] = round(g._at(d, 0, "test", 0, 29.5, -91.5), 3)   # 11
out["quarter"] = round(g._at(d, 0, "test", 0, 29.75, -91.75), 3)   # 5.5
out["outside"] = g._at(d, 0, "test", 0, 31.0, -91.5)
out["missing"] = g._at(d, 0, "nothing", 0, 29.5, -91.5)
d["fields"][(0, "nan", 0)] = array.array("f", [float("nan")] * 9)
out["nan"] = g._at(d, 0, "nan", 0, 29.5, -91.5)
out["bracket"] = [g._bracket(d, cyc + timedelta(hours=h)) for h in (0, 1.5, 3, 6, 7)]
out["rain_h3"] = g._rain3(d, 3, 29.5, -91.5)
out["rain_h6"] = g._rain3(d, 6, 29.5, -91.5)
col = g.column(None, 29.5, -91.5, cyc + timedelta(hours=1.5))
out["col_levels"] = len(col)
out["col_sorted"] = [round(z) for z, u, v in col]
out["col_u"] = [round(u, 2) for z, u, v in col]
out["col_outside"] = g.column(None, 40.0, -91.5, cyc + timedelta(hours=1.5))
out["col_late"] = g.column(None, 29.5, -91.5, cyc + timedelta(hours=20))
pf = g.point_forecast(None, 29.5, -91.5)["rows"]
out["pf_n"] = len(pf)
out["pf1"] = {k: pf[1][k] for k in ("hour", "temp_f", "rh", "wind_mph", "wind_dir", "gust_mph", "rain_in", "cloud", "pressure_hpa")}
out["pf2_rain"] = pf[2]["rain_in"]
class FakeCfg:
    def __init__(self, **k): self.k = k
    def __call__(self, key): return self.k[key]
cf = FakeCfg(gfs_box="27.0,-100.0,43.0,-84.0", gfs_levels="1000,850,500,10", gfs_url="https://example.test/f.pl")
out["box"] = g.box(cf)
out["box_bad"] = g.box(FakeCfg(gfs_box="nonsense"))
out["levels_short_list_falls_back"] = len(g.levels(cf))
out["cycle_a"] = g.latest_cycle(datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc)).isoformat()
out["cycle_b"] = g.latest_cycle(datetime(2026, 10, 17, 4, 30, tzinfo=timezone.utc)).isoformat()
out["cycle_c"] = g.latest_cycle(datetime(2026, 10, 17, 4, 29, tzinfo=timezone.utc)).isoformat()
cf2 = FakeCfg(gfs_box="27.0,-100.0,43.0,-84.0", gfs_levels="1000,925,850,700,600,500,400,300,250,200", gfs_url="https://example.test/f.pl")
u = g._url(cf2, cyc, 6, "p")
out["url_p"] = [("var_UGRD=on" in u, "var_HGT=on" in u, "lev_500_mb=on" in u, "leftlon=-100" in u, "toplat=43" in u, "file=gfs.t18z.pgrb2.0p25.f006" in u, "%2Fgfs.20261016%2F18%2Fatmos" in u)]
us = g._url(cf2, cyc, 6, "s")
out["url_s"] = ["var_TMP=on" in us, "lev_2_m_above_ground=on" in us, "var_APCP=on" in us]
class M:
    def __init__(self, sn, tl, lv, start=0, end=0, st="instant"): self.shortName, self.typeOfLevel, self.level, self.startStep, self.endStep, self.stepType = sn, tl, lv, start, end, st
out["classify"] = [g._classify(M("u", "isobaricInhPa", 500)), g._classify(M("gh", "isobaricInhPa", 850)), g._classify(M("2t", "heightAboveGround", 2)), g._classify(M("10u", "heightAboveGround", 10)),
                   g._classify(M("tp", "surface", 0, 0, 6, "accum")), g._classify(M("tcc", "atmosphere", 0, 3, 3, "instant")), g._classify(M("tcc", "atmosphere", 0, 0, 3, "avg")), g._classify(M("prmsl", "meanSea", 0)),
                   g._classify(M("t", "surface", 0)), g._classify(M("q", "isobaricInhPa", 500))]
print(json.dumps(out))
'''
open("gfs_unit.py", "w").write(UNIT)
subprocess.run(["scp", "-q", "gfs_unit.py", "nastemp:adsb-portal/gfs_unit.py"], check=True, capture_output=True)
SSH(f"{DK} cp ~/adsb-portal/gfs_unit.py predict:/tmp/gfs_unit.py")
o = SSH(f"{DK} exec predict python /tmp/gfs_unit.py").stdout.strip().splitlines()
try:
    R = json.loads(o[-1])
except Exception:
    R = {}; print("unit output:", o[-6:])
check("bilinear interpolation is exact at the corners, in the middle and a quarter of the way", R.get("corner") == 0 and R.get("far_corner") == 22 and R.get("middle") == 11 and R.get("quarter") == 5.5, (R.get("corner"), R.get("far_corner"), R.get("middle"), R.get("quarter")))
check("a place outside the box, a missing field and a missing value give nothing", R.get("outside") is None and R.get("missing") is None and R.get("nan") is None)
check("the time brackets are right (hours 0, 1.5, 3, 6 and one past the end)", R.get("bracket") == [[0, 3, 0.0], [0, 3, 0.5], [0, 3, 1.0], [3, 6, 1.0], None] or R.get("bracket") == [[0, 3, 0.0], [0, 3, 0.5], [3, 6, 0.0], [3, 6, 1.0], None], R.get("bracket"))
check("rain: a window that started three hours ago is the amount, one that started six hours ago has the earlier half taken off", R.get("rain_h3") == 3.0 and R.get("rain_h6") == 2.0, (R.get("rain_h3"), R.get("rain_h6")))
check("the wind column has every level, lowest first, blended between the two forecast hours", R.get("col_levels") == 3 and R.get("col_sorted") == [100, 1500, 5500] and R.get("col_u") == [1.0, 5.0, 20.0], (R.get("col_levels"), R.get("col_sorted"), R.get("col_u")))
check("no column for a place outside the box or a time past the hours kept", R.get("col_outside") == [] and R.get("col_late") == [])
pf = R.get("pf1", {})
check("the surface forecast converts kelvin to F, the wind to mph and direction, gusts, rain, cloud and pressure", pf.get("hour") == 3 and abs(pf.get("temp_f", 0) - 49.73) < 0.1 and pf.get("rh") == 50 and abs(pf.get("wind_mph", 0) - 11.18) < 0.1 and pf.get("wind_dir") == 37 and abs(pf.get("gust_mph", 0) - 22.4) < 0.1 and abs(pf.get("rain_in", 0) - 0.12) < 0.01 and pf.get("cloud") == 40 and abs(pf.get("pressure_hpa", 0) - 1013.2) < 0.2, pf)
check("the first hour has no rain figure and every forecast hour is listed", R.get("pf_n") == 3 and R.get("pf2_rain") == 0.08, (R.get("pf_n"), R.get("pf2_rain")))
check("the area setting is read, a nonsense one falls back to the default, a short list of levels falls back to all", R.get("box") == [27.0, -100.0, 43.0, -84.0] and R.get("box_bad") == [27.0, -100.0, 43.0, -84.0] and R.get("levels_short_list_falls_back") == 26)
check("the newest cycle out is worked out from the clock (a cycle appears about 4.5 hours after its time)", R.get("cycle_a") == "2026-10-16T18:00:00+00:00" and R.get("cycle_b") == "2026-10-17T00:00:00+00:00" and R.get("cycle_c") == "2026-10-16T18:00:00+00:00", (R.get("cycle_a"), R.get("cycle_b"), R.get("cycle_c")))
check("the request to NOAA names the file, the area, the winds and the levels, and the surface request the surface fields", all(R.get("url_p", [[False]])[0]) and all(R.get("url_s", [False])), (R.get("url_p"), R.get("url_s")))
check("the message classifier keeps what we use and drops the rest", R.get("classify") == [["u", 500], ["z", 850], ["t2", 0], ["u10", 0], ["apcp", 0], ["tcc", 0], None, ["prmsl", 0], None, None], R.get("classify"))

# the real data
st = requests.get(PRED + "/gfs/status", timeout=10).json()
for _ in range(60):
    if st.get("ready") or not st.get("running"):
        break
    time.sleep(5)
    st = requests.get(PRED + "/gfs/status", timeout=10).json()
if not st.get("ready"):
    requests.post(PRED + "/gfs/refresh", json={"force": True}, timeout=10)
    for _ in range(80):
        time.sleep(5)
        st = requests.get(PRED + "/gfs/status", timeout=10).json()
        if st.get("ready") and not st.get("running"):
            break
check("a real GFS cycle is stored: every third hour out to 72, a day old at most", st.get("ready") and st.get("steps") == 25 and st.get("age_hours", 99) <= 24 and st.get("bytes", 0) > 1_000_000, st)
fc = requests.get(PRED + "/gfs/forecast", params={"lat": 34.75, "lon": -92.3}, timeout=30).json()
rows = fc.get("rows", [])
check("the surface forecast for Little Rock has 25 hours in time order, three hours apart", fc.get("ok") and len(rows) == 25 and [r["hour"] for r in rows] == list(range(0, 75, 3)) and rows == sorted(rows, key=lambda r: r["time"]), (fc.get("ok"), len(rows)))
check("its numbers are plausible for Arkansas (temperature -20 to 120 F, humidity 0 to 100, pressure 950 to 1060 hPa, wind under 80 mph)", all(-20 <= r["temp_f"] <= 120 and 0 <= (r["rh"] or 0) <= 100 and 950 <= (r["pressure_hpa"] or 1000) <= 1060 and 0 <= (r["wind_mph"] or 0) <= 80 and 0 <= (r["wind_dir"] or 0) <= 360 for r in rows), rows[:2])
check("rain is there for the later hours and never negative, cloud is a percentage", all((r["rain_in"] is None) == (r["hour"] == 0) or r["rain_in"] is not None for r in rows) and all((r["rain_in"] or 0) >= 0 for r in rows) and all(r["cloud"] is None or 0 <= r["cloud"] <= 100 for r in rows), [(r["hour"], r["rain_in"], r["cloud"]) for r in rows[:5]])
out_of = requests.get(PRED + "/gfs/forecast", params={"lat": 10, "lon": 0}, timeout=10).json()
check("a place outside the area kept is told so", out_of.get("ok") is False and "outside the area" in out_of.get("note", ""))
check("a request without a place is refused", requests.get(PRED + "/gfs/forecast", timeout=10).status_code == 400)
COL = r'''
import sys, os, json
sys.path.insert(0, "/app")
os.environ["DATABASE_URL"] = "postgresql://predict:predict@predict-db:5432/predict"
import predictd as p, gfsdata as g
from datetime import timedelta
d = g._load(p.db)
col = g.column(p.db, 34.75, -92.3, d["cycle"] + timedelta(hours=7))
print(json.dumps({"n": len(col), "z": [round(c[0]) for c in col], "spd": [round((c[1] ** 2 + c[2] ** 2) ** .5, 1) for c in col]}))
'''
open("gfs_col.py", "w").write(COL)
subprocess.run(["scp", "-q", "gfs_col.py", "nastemp:adsb-portal/gfs_col.py"], check=True, capture_output=True)
SSH(f"{DK} cp ~/adsb-portal/gfs_col.py predict:/tmp/gfs_col.py")
c = json.loads(SSH(f"{DK} exec predict python /tmp/gfs_col.py").stdout.strip().splitlines()[-1])
check("the wind column over Little Rock has all 26 levels, rising from near the ground to above 25 km, with sensible speeds", c["n"] == 26 and c["z"] == sorted(c["z"]) and c["z"][0] < 600 and c["z"][-1] > 25000 and max(c["spd"]) < 120, c)
sites = requests.get(PRED + "/stats", timeout=10).json()
check("the service's stats carry the GFS status", "gfs" in sites and sites["gfs"].get("ready"))
w = requests.get(PRED + "/predict/at", params={"site": "72340", "when": (__import__("datetime").datetime.utcnow() + __import__("datetime").timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"), "method": "wind"}, timeout=90).json()
pr = w.get("prediction")
check("the wind predictor flies the balloon through our own GFS winds (its note says so)", pr is None or "NOAA GFS (read by this site)" in json.dumps(pr), json.dumps(pr)[:300] if pr else w.get("note"))

# the portal
adm = sess("marty", PW)
anon = requests.Session()
pg = anon.get(B + "/weather/place?q=Conway+AR", timeout=60).text
check("Weather for a Place has the hour-by-hour model forecast (25 rows) in a folding section with its credit", 'id="sec-model"' in pg and pg.count("<tr>") >= 25 and "NOAA GFS weather model" in pg and 'id="model-fc"' in pg, len(pg))
check("it follows the visitor's units", "°F" in pg.split('id="model-fc"')[1][:900] and "°C" in anon.get(B + "/weather/place?q=Conway+AR&units=metric", timeout=60).text.split('id="model-fc"')[1][:900])
check("a place outside the area has no model section", 'id="sec-model"' not in anon.get(B + "/weather/place?lat=10&lon=0", timeout=60).text)
j = adm.get(B + "/admin/sondes/predict/status.json").json()
check("the admin panel's data carries the GFS status", j.get("ok") and j["stats"].get("gfs", {}).get("ready"))
check("the admin Radiosondes page has the Read the Newest GFS Cycle Now button and shows the GFS line", "Read the Newest GFS Cycle Now" in adm.get(B + "/admin/sondes").text and "NOAA GFS (read by this site)" in adm.get(B + "/admin/sondes").text)
st = adm.get(B + "/admin/settings").text
check("the settings are in Admin > Settings", all(x in st for x in ("Forecast Winds From", "Read NOAA GFS Ourselves", "GFS Area (South, West, North, East)", "GFS Forecast Hours Kept", "Look for a New GFS Cycle Every", "Show the Model Forecast on Weather for a Place")))
tok = re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + "/admin/sondes").text).group(1)
r = adm.post(B + "/admin/sondes/predict/gfs", data={"csrf_token": tok}, headers={"X-Requested-With": "XMLHttpRequest"})
check("the admin button starts a read of the newest cycle", r.status_code == 200 and r.json()["ok"], r.text[:200])
for _ in range(80):
    time.sleep(5)
    if not requests.get(PRED + "/gfs/status", timeout=10).json().get("running"):
        break
check("and the read finishes with the data still good", requests.get(PRED + "/gfs/status", timeout=10).json().get("ready"))
print("ALL PASS" if ok_all else "SOME FAILED")
