import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
import sys; sys.path.insert(0, ".")
from app.live import merge_trace
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
def pt(dt, lat, lon, flags=0):   # readsb trace point: [seconds after base, lat, lon, alt, gs, track, flags, ...]
    return [dt, lat, lon, 20000, 400, 90, flags, {}]
# one flight, then 3 hours of silence (landed out of range), then a second flight with no leg flag from readsb
f = {"timestamp": 1_800_000_000, "r": "N1", "t": "B738", "trace": [pt(0, 35, -92), pt(60, 35.1, -92), pt(120, 35.2, -92), pt(11000, 34, -93), pt(11060, 34.1, -93)]}
meta, pts, starts = merge_trace([f], 1800)
check("five points kept", len(pts) == 5)
check("a silence longer than the gap starts a new leg", starts == [3], starts)
meta, pts, starts = merge_trace([f], 0)
check("gap 0 switches it off", starts == [], starts)
g = {"timestamp": 1_800_000_000, "trace": [pt(0, 35, -92), pt(60, 35.1, -92, 2), pt(120, 35.2, -92)]}
check("readsb's own leg flag still counts", merge_trace([g], 1800)[2] == [1])
h = {"timestamp": 1_800_000_000, "trace": [pt(0, 35, -92), pt(2000, 35.1, -92, 2), pt(2060, 35.2, -92)]}
check("a flagged point after a gap is one leg start, not two", merge_trace([h], 1800)[2] == [1])
print("ALL PASS" if ok_all else "SOME FAILED")
