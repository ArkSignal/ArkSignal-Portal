
import os, json
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from datetime import datetime, timedelta, timezone
from app import satpass as sp
L1 = "1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927"
L2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537"
sat = sp.satrec(L1, L2)
t0 = datetime(2008, 9, 20, 12, 30, tzinfo=timezone.utc)
obs = sp.observer(34.746, -92.289)
out = {}
res = sp.passes(sat, obs, t0, 48, 10)
out["n"] = len(res)
out["ok_order"] = all(p["aos"] < p["tca"] < p["los"] for p in res)
out["dur"] = [p["duration"] for p in res]
out["peaks"] = [round(p["max_el"], 1) for p in res]
errs = []
for p in res:
    a, l, c = sp.look(sat, obs, p["aos"]), sp.look(sat, obs, p["los"]), sp.look(sat, obs, p["tca"])
    errs.append((abs(a[1]), abs(l[1]), abs(c[1] - p["max_el"])))
out["edge_err"] = max(max(e[0], e[1]) for e in errs)
out["peak_err"] = max(e[2] for e in errs)
# an observer directly under the satellite sees it overhead; the antipode sees it below the horizon
s = sp.look(sat, obs, t0)
under = sp.observer(s[3], s[4], 0)
u = sp.look(sat, under, t0)
anti = sp.observer(-s[3], (s[4] + 180 + 180) % 360 - 180, 0)
out["under_el"] = round(u[1], 2)
out["height"] = round(s[5], 1)
out["anti_el"] = round(sp.look(sat, anti, t0)[1], 1)
out["lat_in_range"] = abs(s[3]) <= 51.7 + 0.5
svg = sp.polar_svg(res[0]["track"]) if res else ""
out["svg"] = svg.startswith("<svg") and "<path" in svg
out["compass"] = [sp.compass(0), sp.compass(90), sp.compass(225), sp.compass(359)]
print(json.dumps(out))
