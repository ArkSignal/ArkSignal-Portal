import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
anon = requests.Session()
check("flight page rejects a bad address", anon.get(B + "/flight/zzz").status_code == 404)
check("flight page answers for any valid address", anon.get(B + "/flight/a1b2c3").status_code == 200 and "Flight" in anon.get(B + "/flight/a1b2c3").text)
check("trace endpoint rejects a bad address", anon.get(B + "/api/trace/xyz.json").status_code == 400)
ac = [a for a in anon.get(B + "/api/aircraft.json").json()["aircraft"] if a.get("lat") is not None]
if ac:
    t = {"points": []}
    for cand in ac[:12]:   # a freshly heard aircraft may not have a trace on the hub yet
        t = anon.get(B + f"/api/trace/{cand['hex']}.json").json()
        if t["points"]: break
    check("trace endpoint returns the hub's track points", len(t["points"]) >= 1 and len(t["points"][0]) == 7 and isinstance(t["leg"], int), t)
    check("trace times are epoch seconds", t["points"][-1][0] > 1.7e9)
import math, time
def _nm(a, b):
    r = math.pi / 180; x = math.sin((b[0] - a[0]) * r / 2) ** 2 + math.cos(a[0] * r) * math.cos(b[0] * r) * math.sin((b[1] - a[1]) * r / 2) ** 2
    return 3440.065 * 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))
live_now = {a["hex"]: a for a in anon.get(B + "/api/aircraft.json").json()["aircraft"] if a.get("lat") is not None}
checked = 0
for hx, a in list(live_now.items())[:8]:
    tr = anon.get(B + f"/api/trace/{hx}.json").json()["points"]
    if len(tr) > 5:
        gap = _nm((tr[-1][1], tr[-1][2]), (a["lat"], a["lon"]))
        check(f"track {hx} reaches the aircraft (full and recent traces merged)", gap < 3 and time.time() - tr[-1][0] < 180, (round(gap, 1), round(time.time() - tr[-1][0])))
        check(f"track {hx} is in time order", all(tr[i][0] < tr[i + 1][0] for i in range(len(tr) - 1)))
        checked += 1
        if checked >= 2:
            break
check("trail labels are off unless a visitor or the settings turn them on", '"trailLabels": false' in anon.get(B + "/live").text)
unknown = anon.get(B + "/api/trace/000000.json").json()
check("an unknown aircraft gives an empty track, not an error", unknown["points"] == [])
live = anon.get(B + "/live").text
check("live card has Labels and Flight Progress buttons", 'id="c-tlabels"' in live and 'id="c-flight"' in live and '"trailLabelSeconds": 45' in live)
m = anon.get(B + "/metrics")
check("metrics are Prometheus text", m.status_code == 200 and m.headers["Content-Type"].startswith("text/plain") and "adsb_portal_feeders_online" in m.text)
check("metrics carry no locations, addresses or UUIDs", not re.search(r"lat|lon|uuid|\d+\.\d+\.\d+\.\d+|[0-9a-f]{8}-[0-9a-f]{4}-", m.text.split("# HELP")[0] + re.sub(r"# (HELP|TYPE).*", "", m.text)), m.text[:300])
st = anon.get(B + "/view/statistics")
check("Aircraft Statistics is a tool and shows Grafana", st.status_code == 200 and "grafana" in st.text and "kiosk" in st.text)
check("old Network Graphs tool is gone", "Network Graphs" not in anon.get(B + "/feeders/").text)
check("home page links the message rate to the dashboard", 'href="/view/statistics"' in anon.get(B + "/").text)
fs = anon.get(B + "/flight/")
check("Flight Search page exists and is in the menu", fs.status_code == 200 and 'id="fs-q"' in fs.text and 'href="/flight/">Flights<' in anon.get(B + "/").text)
check("the search explains hex codes and airline callsigns", "hex code" in fs.text and "AAL" in fs.text)
check("the live map can hide other aircraft when one is selected", 'id="f-focus"' in live and 'id="c-focus"' in live)
check("menus close when you click elsewhere", "details.navmenu[open]" in anon.get(B + "/").text)
print("ALL PASS" if ok_all else "SOME FAILED")
