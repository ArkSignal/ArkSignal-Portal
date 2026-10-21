"""Satellite passes: orbit data from CelesTrak (Data Updates), passes worked out with sgp4 for a chosen place, the page, its choices and settings. The geometry is checked by properties that must hold (an
observer under the satellite sees it straight overhead, the pass edges are at the horizon, the peak is the peak); the data job reads the real CelesTrak service, so the test box needs the internet."""
import json, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
SSH = lambda cmd: subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True)
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def psql(sql):
    return SSH(f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").stdout.strip()

UNIT = r'''
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
'''
open("satpass_unit.py", "w").write(UNIT)
subprocess.run(["scp", "-q", "satpass_unit.py", "nastemp:adsb-portal/satpass_unit.py"], check=True, capture_output=True)
SSH(f"{DK} cp ~/adsb-portal/satpass_unit.py adsb-portal:/tmp/satpass_unit.py")
o = SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/satpass_unit.py").stdout.strip().splitlines()
try:
    R = json.loads(o[-1])
except Exception:
    R = {}
    print("unit output:", o[-5:])
check("the station's passes over Little Rock in 48 hours are a sensible number (4 to 14 above 10 degrees)", 4 <= R.get("n", 0) <= 14, R.get("n"))
check("each pass is in order, under 16 minutes, and peaks between 10 and 90 degrees", R.get("ok_order") and all(60 <= d <= 960 for d in R.get("dur", [0])) and all(10 <= p <= 90 for p in R.get("peaks", [0])), (R.get("dur"), R.get("peaks")))
check("the pass edges are at the horizon (within half a degree) and the peak is the peak", R.get("edge_err", 9) < 0.5 and R.get("peak_err", 9) < 1.0, (R.get("edge_err"), R.get("peak_err")))
check("an observer directly under the satellite sees it straight overhead", R.get("under_el", 0) > 89.5, R.get("under_el"))
check("the satellite is about 350 to 450 km up and below the horizon from the other side of the earth", 350 <= R.get("height", 0) <= 450 and R.get("anti_el", 0) < -50, (R.get("height"), R.get("anti_el")))
check("the point under it stays within the orbit's latitudes, the sky chart draws and compass points are right", R.get("lat_in_range") and R.get("svg") and R.get("compass") == ["N", "E", "SW", "N"], R)

# the data job and the page
adm = sess("marty", PW)
anon = requests.Session()
psql("DELETE FROM sat_tle")
pg = anon.get(B + "/radio/satellites")
check("with no orbit data the page says so and points to Data Updates", pg.status_code == 200 and "No orbit data has been loaded yet" in pg.text)
du = adm.get(B + "/admin/data-updates/").text
check("Data Updates has the Satellite Orbits job", "Satellite Orbits" in du)
tok = re.search(r'name="csrf_token" value="([^"]+)"', du).group(1)
r = adm.post(B + "/admin/data-updates/satpass/run", data={"csrf_token": tok}, headers={"X-Requested-With": "XMLHttpRequest"})
res = ""
for _ in range(30):
    time.sleep(4)
    h = adm.get(B + "/admin/data-updates/").text
    m = re.search(r"Satellite Orbits.*?(\d+ satellites.{1,8}orbit data|failed[^<]{0,120})", h, re.S)
    if m:
        res = m.group(1); break
check("the job reads the orbit data from CelesTrak (at least 4 of the tracked satellites)", "satellites" in res and int(res.split()[0]) >= 4, res)
check("the orbit data is kept in the database", int(psql("select count(*) from sat_tle") or 0) >= 4)
pg = anon.get(B + "/radio/satellites")
t = pg.text
check("the page lists passes with the satellite, time, direction, peak, length and a sky chart", pg.status_code == 200 and 'id="sp-passes"' in t and t.count("<svg") >= 3 and "Upcoming Passes" in t, pg.status_code)
check("it uses the site's folding sections and the Radio menu lists it", t.count('class="panel setsec"') >= 3 and 'href="/radio/satellites"' in t)
check("it names the orbit source and its age", "CelesTrak" in t and re.search(r"element set is \d+ day", t))
check("the passes are in time order", (lambda ts: ts == sorted(ts))([__import__("datetime").datetime.fromisoformat(x) for x in re.findall(r'<td data-v="(20\d\d-[^"]+)">', t)]))
check("How to Work the Chosen Satellites lists nothing while every satellite is ticked and has no search box of its own", 'id="sec-radio"' in t and 'id="sp-find"' not in t and t.count("sp-card") >= 4 and len(re.findall(r'class="panel sp-card"[^>]*\shidden', t)) == t.count('class="panel sp-card"'))
for q in ("sat=25544", "sel=1&sat=25544"):
    one_ = anon.get(B + "/radio/satellites?" + q).text
    check(f"choosing the ISS ({q}) lists how to work it (frequencies, mode, license, what you need) and only it", "145.825" in one_ and "What you need:" in one_ and "License needed" in one_ and len(re.findall(r'class="panel sp-card"[^>]*\shidden', one_)) == one_.count('class="panel sp-card"') - 1, q)
sel_ = anon.get(B + "/radio/satellites?sel=1&hours=24").text
check("the form's sel flag with nothing ticked means no satellites (not all of them): no passes, 0 chosen, and a plain message", "0 of" in sel_ and 'id="sp-passes"' not in sel_ and "No pass climbs" in sel_)
check("the picker has grouped toggle buttons, All, None, Only These, a filter box and a count", all(x in t for x in ('id="sp-all"', 'id="sp-none"', 'id="sp-filter"', "sp-only", 'class="sp-pill"', "Space Station", "Weather Satellites", "Amateur Radio Satellites", 'id="sp-count"')) and t.index('class="sp-pill"') < t.index('id="sp-passes"'))
check("every satellite's name in the passes opens its card", 'class="sp-open"' in t and "How to work" in t)
check("the passes carry the downlink and the Doppler shift from rise to set (high to low)", "Doppler (kHz)" in t and (lambda m: len(m) > 5 and sum(1 for a, b in m if not float(a) > 0 > float(b)) <= 1)(re.findall(r'class="mono">([+-]\d+\.\d) to ([+-]\d+\.\d)<', t)))
gj = anon.get(B + "/api/satellites.geojson").json()
ft = gj["features"]
pts = [f for f in ft if f["geometry"]["type"] == "Point"]
check("the Satellites map layer gives each satellite as a dot with a path ahead and the part of the earth it can see", len(pts) >= 4 and any(f["geometry"]["type"] == "LineString" for f in ft) and any(f["geometry"]["type"] == "Polygon" for f in ft), [f["geometry"]["type"] for f in ft][:6])
iss_ = [f["properties"] for f in pts if f["properties"]["event"].startswith("ISS")]
check("a dot's card has its height, speed, the next pass over the map center and how to work it (frequencies, license, equipment)", bool(iss_) and (lambda r: all(x in r for x in ("Height", "Speed", "Downlink", "To transmit", "What you need")))(dict((r[0], r[1]) for r in iss_[0]["rows"])), iss_[:1])
check("the dots are colored by kind and link to the pass page", all(f["properties"]["KIND"] in ("iss", "ham", "wx") and f["properties"]["link"].startswith("/radio/satellites?sat=") for f in pts))
ly = psql("select count(*) from map_layers where slug = 'satellites' and kind = 'geojson'")
check("the layer is in the layer list for every map (Radio group, off until chosen)", ly == "1" and psql("select default_on from map_layers where slug = 'satellites'") == "f")
check("its settings are in Admin > Settings", all(x in adm.get(B + "/admin/settings").text for x in ("Satellites Map Layer", "Satellites Map: Path Ahead", "Satellites Map: Show What Each Can See", "Satellites Map: Refresh Every")))
iss = anon.get(B + "/radio/satellites?sat=25544&hours=72&min=5").text
check("choosing one satellite shows only it, for the hours and lowest peak asked", "ISS" in iss and "NOAA" not in iss.split('id="sp-passes"')[1].split("</table>")[0] if 'id="sp-passes"' in iss else "ISS" in iss)
for q, want in (("lat=34.7&lon=-92.3", "34.700"), ("q=Conway+AR", "Conway"), ("lat=999&lon=0", "this site"), ("hours=abc&min=xyz", "Passes for"), ("hours=99999", "Passes for")):
    rr = anon.get(B + "/radio/satellites?" + q)
    check(f"the place or number '{q}' is handled", rr.status_code == 200 and want in rr.text, rr.status_code)
check("a higher lowest peak gives fewer passes", len(re.findall(r"<tr><td><b>", anon.get(B + "/radio/satellites?min=60").text)) <= len(re.findall(r"<tr><td><b>", t)))
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("Satellite Passes Page", "Satellites to Track", "Satellite Groups to Read from CelesTrak", "Update Satellite Orbits Every")))
psql("UPDATE settings SET value='0' WHERE key='satpass_enabled'") if psql("select count(*) from settings where key='satpass_enabled'") == "1" else psql("INSERT INTO settings (key, value) VALUES ('satpass_enabled','0')")
check("with the page switched off it is not found and the menu entry goes", anon.get(B + "/radio/satellites").status_code == 404 and 'href="/radio/satellites"' not in anon.get(B + "/").text)
psql("UPDATE settings SET value='1' WHERE key='satpass_enabled'")
print("ALL PASS" if ok_all else "SOME FAILED")
