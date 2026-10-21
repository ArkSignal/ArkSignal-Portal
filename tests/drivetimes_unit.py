"""In-container checks of drivetimes.py and the new location precision list, with a made-up routing function (no routing server needed): near-ties ordered by trauma level then services,
hospitals without a road last, the per-visitor limit, and every precision step (rounding, circle size, labels in imperial and metric).
docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/drivetimes_unit.py"""
import math
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app
from app import drivetimes as dt
from app import privacy as pv
from app.settings import set_setting

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

def row(name, sec, trauma="", services=""):
    return {"name": name, "seconds": sec, "trauma": trauma, "services": services}

with app.app_context():
    rows = [row("far", 1800, "I", "a,b,c"), row("A", 600, "", ""), row("B", 660, "III", "a"), row("C", 700, "II", "a,b"), row("D", 1000, "IV", ""), row("none", None, "I", "a")]
    out = [r["name"] for r in dt.rank(rows, 2)]
    check("hospitals within two minutes of the quickest are ordered by trauma level (C level II before B level III before A none)", out[:3] == ["C", "B", "A"], out)
    check("then the next group starts at the next hospital's own time, and no road route comes last", out[3:] == ["D", "far", "none"], out)
    out0 = [r["name"] for r in dt.rank(rows, 0)]
    check("with no tie window it is plain drive-time order", out0 == ["A", "B", "C", "D", "far", "none"], out0)
    same = [row("x", 600, "II", "a"), row("y", 610, "II", "a,b,c")]
    check("equal trauma level: more services first", [r["name"] for r in dt.rank(same, 2)] == ["y", "x"])
    check("services may be a list or text", dt._nservices(["a", "b"]) == 2 and dt._nservices("a,b,c") == 3 and dt._nservices("") == 0 and dt._nservices(None) == 0)

    set_setting("drivetimes_per_minute", "3")
    ip = "203.0.113.9"
    got = [dt.allowed(ip, now=1000.0 + i) for i in range(5)]
    check("a visitor gets the set number of questions a minute, then is refused", got == [True, True, True, False, False], got)
    check("and may ask again a minute later", dt.allowed(ip, now=1100.0) is True)
    check("another visitor is not affected", dt.allowed("203.0.113.10", now=1001.0) is True)
    set_setting("drivetimes_per_minute", "20")

    from app.db import get_db
    h = get_db().execute("SELECT lat, lon FROM hospitals WHERE emergency AND lat IS NOT NULL LIMIT 1").fetchone()
    if h:
        res = dt.times(h["lat"], h["lon"], table=lambda src, pts: [i * 120.0 for i in range(len(pts))])
        check("times() returns minutes and the straight-line distance for each hospital", res and res[0]["minutes"] is not None and all("km" in r and "id" in r for r in res), res[:1])
    else:
        print("note: no hospitals in this database, times() check skipped")

    # location precision
    LAT, LON = 34.74256, -92.28421
    names = list(pv.LEVELS)
    check("twelve steps in one order", names == ["exact", "fine", "near", "block", "close", "area", "district", "general", "region", "broad", "wide", "hidden"])
    widths = {"fine": 11.1, "near": 111.3, "block": 400, "close": 1113, "area": 2414, "district": 4828, "general": 11132, "region": 24140, "broad": 48280, "wide": 111320}
    check("circle radius is half a step", all(abs(pv.radius_m(k) - widths[k] / 2) <= 1 for k in widths), {k: pv.radius_m(k) for k in widths})
    pos = {k: pv.position(LAT, LON, k) for k in names}
    check("each step rounds to its own grid", pos["fine"] == (34.7426, -92.2842) and pos["near"] == (34.743, -92.284) and pos["close"] == (34.74, -92.28) and pos["general"] == (34.7, -92.3) and pos["wide"] == (35.0, -92.0), pos)
    def err(k):
        p = pos[k]
        return math.hypot((p[0] - LAT) * 111320, (p[1] - LON) * 111320 * math.cos(math.radians(LAT)))
    check("grid steps keep the true place within about half a step", all(err(k) <= widths[k] * 0.75 for k in ("block", "area", "district", "region", "broad")), {k: round(err(k)) for k in ("block", "area", "district", "region", "broad")})
    check("a nearby place in the same cell gives the same point", pv.position(LAT + 0.0001, LON, "district") == pv.position(LAT, LON, "district"))
    check("hidden gives nothing, exact five places", pos["hidden"] is None and pos["exact"] == (34.74256, -92.28421))
    with app.test_request_context("/"):
        lab = dict(pv.PRIVACY.items())
        check("labels are in imperial words by default", lab["close"] == "Close (rounded to about 0.7 mile)" and lab["fine"].endswith("40 feet)") and lab["general"] == "General (rounded to about 6 miles)" and lab["exact"] == "Exact", lab)
        set_setting("live_units", "metric")
        lab = dict(pv.PRIVACY.items())
        check("and in metric words when the site (or the person) uses metric", lab["close"] == "Close (rounded to about 1.1 kilometers)" and lab["near"].endswith("120 meters)") and lab["general"].endswith("10 kilometers)"), lab)
        set_setting("live_units", "imperial")
        ch = pv.choices(skip=("exact",))
        check("choices() is a list of (key, label) that can leave steps out", [k for k, _l in ch][0] == "fine" and len(ch) == 11 and "exact" not in dict(ch) and "close" in dict(ch))
        check("every key is a valid level for the forms", all(k in pv.PRIVACY for k in names) and "bogus" not in pv.PRIVACY)
        check("the help text names the steps", "40 feet" in pv.help_text() and "Hidden" in pv.help_text())
    from app.settings import DEFAULTS
    check("the radiosonde default until claimed is Close", DEFAULTS["sonde_station_privacy"] == "close")
print("ALL PASS" if ok_all else "SOME FAILED")
