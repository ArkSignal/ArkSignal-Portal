"""My Flights map and statistics: the route and airport data for the map (routes counted both ways round, airport circles by flights), the year filter, privacy, and the statistics boards."""
import json, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("myflights_enabled", "1")
psql("DELETE FROM settings WHERE key = 'access_myflights'")
psql("DELETE FROM users WHERE username LIKE 'mfs%'")
for n in ("mfsone", "mfstwo"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
U1, U2 = sess("mfsone", "mfsonepassword99"), sess("mfstwo", "mfstwopassword99")
anon = requests.Session()
check("signed out: sent to sign in", anon.get(B + "/my/flights/map", allow_redirects=False).status_code == 302 and anon.get(B + "/my/flights/stats", allow_redirects=False).status_code == 302)
check("empty diary: both pages say there is nothing yet", "Nothing to show yet" in U1.get(B + "/my/flights/map").text and "Nothing to count yet" in U1.get(B + "/my/flights/stats").text)
def ap(q, code): return next(a for a in U1.get(B + "/my/flights/airports.json", params={"q": q}).json()["airports"] if a["code"] == code)
lit, las, dal = ap("LIT", "LIT"), ap("LAS", "LAS"), ap("DAL", "DAL")
def post(s, **kw):
    kw["csrf_token"] = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/flights/new").text).group(1)
    return s.post(B + "/my/flights/save", data=kw, allow_redirects=False)
def fl(a, b, day, **kw):
    return post(U1, from_id=a["id"], to_id=b["id"], mode="simple", act_out=day + "T12:00", act_in=day + "T14:00", **kw)
fl(lit, las, "2023-05-01", airline="Southwest Airlines", aircraft_type="B738", p0_name="Marty", p0_class="Economy", p0_reason="Leisure")
fl(las, lit, "2023-05-08", airline="Southwest Airlines", aircraft_type="B737")
fl(lit, dal, "2024-02-01", airline="American Airlines", aircraft_type="B738", p0_name="Marty", p0_class="Business", p0_reason="Business")
j = U1.get(B + "/my/flights/map.json").json()
check("the map data has three airports and two routes (a route and its return count as one)", len(j["airports"]) == 3 and len(j["routes"]) == 2, j)
lit_a = next(a for a in j["airports"] if a["code"] == "LIT")
check("LIT is touched by all three flights, and the busier route has two", lit_a["count"] == 3 and max(r["count"] for r in j["routes"]) == 2 and j["years"] == [2024, 2023])
j24 = U1.get(B + "/my/flights/map.json?year=2024").json()
check("the year filter narrows the map", len(j24["routes"]) == 1 and len(j24["airports"]) == 2)
pg = U1.get(B + "/my/flights/map").text
check("the map page carries the data and the map", 'id="flmap"' in pg and 'id="fldata"' in pg and "Routes (2) and Airports (3)" in pg)
sp = U1.get(B + "/my/flights/stats").text
check("statistics: totals, most visited airport, airlines, types, countries", re.search(r'<div class="tv">3</div><div class="tl">Flights', sp) and "Southwest Airlines" in sp and "B738" in sp and "US" in sp and "LIT" in sp)
check("statistics: people by class and reason, by year", "Business" in sp and "Leisure" in sp and "<td>2023</td>" in sp and "<td>2024</td>" in sp)
check("statistics: the year filter", re.search(r'<div class="tv">1</div><div class="tl">Flights', U1.get(B + "/my/flights/stats?year=2024").text) is not None)
check("another member sees none of it", "Nothing to show yet" in U2.get(B + "/my/flights/map").text and U2.get(B + "/my/flights/map.json").json()["routes"] == [])
check("the account menu has Flight Map and Flight Statistics", "/my/flights/map" in U1.get(B + "/my/flights").text and "/my/flights/stats" in U1.get(B + "/my/flights").text)
setting("myflights_enabled", "0")
check("switched off: gone", U1.get(B + "/my/flights/map", allow_redirects=False).status_code == 404 and U1.get(B + "/my/flights/stats", allow_redirects=False).status_code == 404)
setting("myflights_enabled", "1")
psql("DELETE FROM users WHERE username LIKE 'mfs%'")
print("ALL PASS" if ok_all else "SOME FAILED")
