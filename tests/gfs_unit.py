
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
