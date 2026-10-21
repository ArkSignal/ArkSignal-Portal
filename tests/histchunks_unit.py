"""In-container check of histchunks.py (the hub's heatmap file format): a hand-built file decodes to the right values, and a real half hour from the test hub decodes sensibly.
docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/histchunks_unit.py"""
import os, struct
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from app import create_app
from app import histchunks as hc
from datetime import date, datetime, timezone
app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
def rec(*w): return struct.pack("<IIII", *[x & 0xFFFFFFFF for x in w])
def w3(alt, gs10):   # low 16 bits altitude / 25 ft (signed), high 16 bits ground speed * 10 (-1 = unknown)
    return ((gs10 & 0xFFFF) << 16) | (alt & 0xFFFF)
t0 = 1760446800.0   # 2025-10-14 13:00:00 UTC
def header(t_s, ival_ms):   # the time is word1 * 4294967.296 + word2 / 1000 seconds: a millisecond count split the way readsb does
    total = int(round(t_s * 1000)); return rec(hc.MAGIC, total >> 32, total & 0xFFFFFFFF, ival_ms)
asc = lambda t: struct.unpack("<I", t)[0]
blob = header(t0, 15000)
blob += rec(0xA40797, 0x40000000 | 2728, asc(b"AAL2"), asc(b"728 "))                    # information: squawk and call sign AAL2728
blob += rec(0xA40797 | (5 << 27), 34651016, -92368563, w3(35000 // 25, 375))            # MLAT, 35000 ft, 37.5 kt
blob += rec(0xABCDEF | 0x1000000, 35000000, -91000000, w3(-123, -1))                    # non-ICAO address, on the ground, no speed
blob += header(t0 + 15, 15000)
blob += rec(0xA40797, 34652000, -92368000, w3(400, 3000))                               # 10000 ft, 300 kt
sl = hc.decode(blob)
check("two slices", len(sl) == 2, len(sl))
check("time and interval of the first slice", abs(sl[0]["t"] - t0) < 0.01 and sl[0]["ival"] == 15.0, (sl[0]["t"], sl[0]["ival"]))
check("the information record gives the call sign and squawk", sl[0]["info"].get("a40797") == ("AAL2728", "2728"), sl[0]["info"])
p = sl[0]["pts"]
check("the position record: address, place, altitude, source", p[0][0] == "a40797" and abs(p[0][1] - 34.651016) < 1e-9 and abs(p[0][2] + 92.368563) < 1e-9 and p[0][3] == 35000 and p[0][5] == "mlat", p[0])
check("the non-ICAO address starts with ~, ground and unknown speed", p[1][0] == "~abcdef" and p[1][3] == "ground" and p[1][4] is None, p[1])
check("the ground speed is tenths of a knot", sl[1]["pts"][0][4] == 300.0 and sl[1]["pts"][0][3] == 10000, sl[1]["pts"][0])
check("cell size shrinks as the zoom grows", hc.cell_degrees(10) < hc.cell_degrees(8) / 3)
with app.app_context():
    if not app.config.get("HUB_URL"):
        print("note: no hub address, the real file check is skipped")
    else:
        from datetime import timedelta
        day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        got = None
        for idx in range(47, -1, -1):
            raw = hc.fetch(day, idx)
            if raw: got = (idx, raw); break
        if not got:
            print("note: the hub has no heatmap file for yesterday, the real file check is skipped")
        else:
            idx, raw = got
            s = hc.decode(raw)
            pts = [q for x in s for q in x["pts"]]
            check(f"a real half hour ({idx}) has slices 15 s apart inside that half hour", len(s) > 10 and all(abs((s[i+1]['t'] - s[i]['t']) - s[i]['ival']) < 1.5 for i in range(len(s)-1)), len(s))
            check("its positions are on the globe and its altitudes sensible", pts and all(-90 <= q[1] <= 90 and -180 <= q[2] <= 180 for q in pts) and all(q[3] in (None, "ground") or -2000 <= q[3] <= 70000 for q in pts))
            g = hc.half_hour_heat(day, idx, 8, None, None)
            check("the heat grid adds up to the number of positions", g is not None and sum(g.values()) == len(pts), (sum(g.values()) if g else None, len(pts)))
            hi = hc.half_hour_heat(day, idx, 8, 20000, None)
            check("an altitude floor leaves fewer or equal positions", sum(hi.values()) <= sum(g.values()))
print("ALL PASS" if ok_all else "SOME FAILED")
