"""In-container checks of the prediction service (predictd.py): flight summaries from archive frames, the usual launch times of a site, the forecast of a future launch from
past flights (season, hour, persistence), and the scoring of a forecast against the real flight. Uses the made-up launch site TESTSITE and removes it again.
docker cp this file to /tmp and run: docker exec -w /app -e PYTHONPATH=/app predict python /tmp/predict_unit.py"""
import math
import sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/app")
import predictd as p

ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

p.init_db()
NOW = datetime.now(timezone.utc).replace(microsecond=0)
iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
SITE = "TESTSITE"
LA, LO = 34.84, -92.26

# ---- a summary from the three archive frames
base = NOW - timedelta(days=10)
frames = [{"serial": "TSUM1", "datetime": iso(base), "lat": LA, "lon": LO, "alt": 170.0, "type": "RS41", "subtype": "RS41-SG", "manufacturer": "Vaisala", "launch_site": SITE},
          {"serial": "TSUM1", "datetime": iso(base + timedelta(minutes=95)), "lat": 35.1, "lon": -91.4, "alt": 33000.0, "type": "RS41"},
          {"serial": "TSUM1", "datetime": iso(base + timedelta(minutes=160)), "lat": 35.3, "lon": -91.0, "alt": 300.0, "type": "RS41"}]
s = p.summarize(frames)
check("a summary has the launch, the burst and the landing", s["serial"] == "TSUM1" and s["site"] == SITE and abs(s["launch_lat"] - LA) < 1e-6 and s["burst_alt"] == 33000.0 and abs(s["land_lat"] - 35.3) < 1e-6 and not s["lost"], s)
check("a flight that ended high is lost, with no landing", p.summarize(frames[:2])["lost"] and p.summarize(frames[:2]).get("land_lat") is None)
check("a flight joined late (first frame high) has no launch", "launch_lat" not in p.summarize(frames[1:]))
check("frames without a position give no summary", p.summarize([{"serial": "X", "datetime": "bad"}]) is None)

# ---- past flights of the test site: two launches a day (11:15 and 23:15), landing 50 km east in every season
with p.db() as c:
    c.execute("DELETE FROM flights WHERE site=%s", (SITE,)); c.execute("DELETE FROM predictions WHERE site=%s", (SITE,))
    n = 0
    for d in range(2, 200):
        for hh in (11, 23):
            t = (NOW - timedelta(days=d)).replace(hour=hh, minute=15, second=0)
            la, lo = p.move_km(LA, LO, 50 + (d % 7) - 3, (d % 5) - 2)
            p.store_flight(c, {"serial": f"T{SITE}{d}{hh}", "site": SITE, "launch_ts": t, "launch_lat": LA, "launch_lon": LO, "launch_alt": 170, "burst_ts": t + timedelta(minutes=95), "burst_lat": LA, "burst_lon": LO, "burst_alt": 32000 + d % 11 * 100,
                                  "land_ts": t + timedelta(minutes=160), "land_lat": la, "land_lon": lo, "land_alt": 250, "lost": False}, "test")
            n += 1
    sched = p.launch_schedule(c, SITE, NOW, 72)
check("the usual launch times are found: only the 11 and 23 UTC hours", sched and all(t.hour in (11, 23) and t.minute == 15 for t in sched), [t.isoformat() for t in sched][:4])
check("they run from now to the horizon, in order", sched == sorted(sched) and sched[-1] <= NOW + timedelta(hours=72) and sched[0] >= NOW - timedelta(hours=1))
with p.db() as c:
    pr = p.predict_one(c, SITE, sched[0], NOW)
check("the forecast lands about 50 km east of the site", pr is not None and 40 < pr["drift_km"] < 62 and 80 <= pr["bearing"] <= 100, pr)
check("it carries the burst height, the flight time and its radii", pr and 31000 < pr["burst_alt"] < 34000 and 155 <= pr["flight_min"] <= 165 and pr["r50_km"] >= 2 and pr["r90_km"] >= pr["r50_km"], pr)
with p.db() as c:
    check("with no history it makes no forecast", p.predict_one(c, "NOSUCHSITE", sched[0], NOW) is None)

# ---- the drawn path of a forecast: a climb to the burst point and a fall to the forecast landing
check("a forecast carries its path, the burst point and where the path came from", pr["path"] and len(pr["path"]) >= 30 and len(pr["burst"]) == 3 and "no past track" in pr["path_source"], pr["path_source"])
check("the path starts at the launch site and ends on the forecast landing", abs(pr["path"][0][1] - LA) < .01 and abs(pr["path"][0][2] - LO) < .01 and abs(pr["path"][-1][1] - pr["lat"]) < .01 and abs(pr["path"][-1][2] - pr["lon"]) < .01, (pr["path"][0], pr["path"][-1]))
check("it climbs to the burst height and the burst point is the highest point", max(p_[3] for p_ in pr["path"]) == pr["burst"][2] and 31000 < pr["burst"][2] < 34000 and pr["path"][0][3] < 500 and pr["path"][-1][3] < 600, (pr["burst"], pr["path"][-1]))
check("times run forward from zero", pr["path"][0][0] == 0 and all(a[0] <= b[0] for a, b in zip(pr["path"], pr["path"][1:])))
with p.db() as c:   # give every past flight of the site a real-shaped track: climb east and a little north to 32 km in 100 minutes, fall to 50 km east in 160
    for r in c.execute("SELECT serial FROM flights WHERE site=%s", (SITE,)).fetchall():
        pts = []
        for k in range(71):
            f = k / 70.0
            up = min(1.0, f / 0.62)
            alt = 170 + 32000 * up if f <= 0.62 else 32170 - (32170 - 250) * ((f - 0.62) / 0.38) ** 0.5
            la, lo = p.move_km(LA, LO, 50 * f ** 0.9, 3 * f)
            pts.append([round(f * 9600), round(la, 5), round(lo, 5), round(alt)])
        c.execute("INSERT INTO tracks (serial, pts) VALUES (%s,%s) ON CONFLICT (serial) DO UPDATE SET pts=EXCLUDED.pts", (r["serial"], p.json.dumps(pts)))
    pr3 = p.predict_one(c, SITE, sched[0], NOW)
check("with a past flight's track at hand the path takes its shape", "past flight" in pr3["path_source"] and len(pr3["path"]) >= 60, pr3["path_source"])
check("and still starts at the launch site and ends on the forecast landing", abs(pr3["path"][0][1] - LA) < .01 and abs(pr3["path"][-1][1] - pr3["lat"]) < .02 and abs(pr3["path"][-1][2] - pr3["lon"]) < .02, (pr3["path"][-1], pr3["lat"], pr3["lon"]))
check("its highest point is the burst point", max(p_[3] for p_ in pr3["path"]) == pr3["burst"][2] and pr3["burst"][2] > 30000)
with p.db() as c:
    c.execute("DELETE FROM tracks WHERE serial LIKE %s", ("T" + SITE + "%",))

# ---- persistence: the last flights went far north-east, so the forecast moves that way
with p.db() as c:
    for k in range(4):
        t = NOW - timedelta(hours=10 + k * 12)
        la, lo = p.move_km(LA, LO, 80, 80)
        p.store_flight(c, {"serial": f"TRECENT{k}", "site": SITE, "launch_ts": t, "launch_lat": LA, "launch_lon": LO, "launch_alt": 170, "land_ts": t + timedelta(minutes=160), "land_lat": la, "land_lon": lo, "land_alt": 250, "lost": False}, "test")
    pr2 = p.predict_one(c, SITE, sched[0], NOW)
check("recent flights pull the forecast toward where they went (north of the seasonal east)", pr2["bearing"] < pr["bearing"] and pr2["recent"] >= 4, (pr["bearing"], pr2["bearing"]))
with p.db() as c:
    c.execute("DELETE FROM flights WHERE serial LIKE 'TRECENT%'")

# ---- stored forecasts and their scoring
out = p.predictions([SITE])
check("the forecast list covers the coming launches of the site", len(out) >= 4 and all(x["site"] == SITE for x in out), len(out))
with p.db() as c:
    stored = c.execute("SELECT COUNT(*) AS n FROM predictions WHERE site=%s", (SITE,)).fetchone()["n"]
    check("each upcoming forecast is stored once", stored >= 3, stored)
    # a forecast for a launch eight hours ago, and the real flight that launched then
    when = (NOW - timedelta(hours=9)).replace(minute=15, second=0)
    c.execute("INSERT INTO predictions (site, launch_ts, lat, lon, r50_km, r90_km) VALUES (%s,%s,%s,%s,30,60) ON CONFLICT (site, launch_ts, method) DO NOTHING", (SITE, when, LA + 0.1, LO + 0.6))
    la, lo = p.move_km(LA, LO, 55, 5)
    p.store_flight(c, {"serial": "TSCORED", "site": SITE, "launch_ts": when + timedelta(minutes=2), "launch_lat": LA, "launch_lon": LO, "land_ts": when + timedelta(minutes=170), "land_lat": la, "land_lon": lo, "lost": False}, "test")
n = p.score_predictions()
with p.db() as c:
    r = c.execute("SELECT * FROM predictions WHERE site=%s AND launch_ts=%s", (SITE, when)).fetchone()
check("a past forecast is scored against the real flight", n >= 1 and r["actual_serial"] == "TSCORED" and r["err_km"] is not None and r["err_km"] < 30, r)
sc = p.scores(SITE)
check("the error history reports the count, the median and how often the area held the landing", sc["count"] >= 1 and sc["median_km"] is not None and sc["inside_r90"] is not None)

# ---- the track on demand refuses bad serials
check("a bad serial is refused before any request", p.fetch_track("../x")[0] is None)

# ---- clean up
with p.db() as c:
    c.execute("DELETE FROM flights WHERE site=%s OR serial LIKE 'TSUM%%'", (SITE,)); c.execute("DELETE FROM predictions WHERE site=%s", (SITE,))
print("ALL PASS" if ok_all else "SOME FAILED")
