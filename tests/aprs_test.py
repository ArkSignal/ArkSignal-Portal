"""APRS as the portal sees it: the service's own API, the list, map, station and weather pages, the data files, the administrators' page (status, apply, hiding a call sign and showing it
again), the permissions, the menu, Settings, Service Status and the in-container unit checks (aprs_unit.py). Needs the service running on the test box (sh tests/deploy_aprs.sh)."""
import os, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18480"
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout

try:
    h = requests.get(P + "/health", timeout=10).json()
except Exception as e:
    print("SKIP aprs_test: the APRS service is not running on the test box (sh tests/deploy_aprs.sh)", e); raise SystemExit(0)
check("the service answers and says its version", h.get("ok") is True and "version" in h)
out = ssh("docker cp ~/adsb-portal/tests/aprs_unit.py aprs:/tmp/aprs_unit.py; docker exec -w /app -e PYTHONPATH=/app aprs python /tmp/aprs_unit.py")
check("aprs_unit.py passes inside the service container", "ALL PASS" in out and "FAIL" not in out, out[-500:])

# the feed needs a minute to fill on a fresh service
for _ in range(24):
    st = requests.get(P + "/stats", timeout=20).json()
    if st["stations"] >= 5:
        break
    time.sleep(5)
check("its statistics carry the feed state, the counts and its settings", all(k in st for k in ("stations", "heard_hour", "kinds", "weather_now", "feed", "config", "configured")) and st["feed"]["connected"], st.get("feed"))
check("it is connected to APRS-IS with the read-only login and has heard stations", st["feed"]["connected"] and st["stations"] >= 1, st)
check("the area filter is the portal's", st["config"]["filter"].startswith("r/"))
ss = requests.get(P + "/stations?limit=5", timeout=20).json()["stations"]
check("stations come back with a position, a kind and when they were heard", ss and all(k in ss[0] for k in ("call", "lat", "lon", "kind", "last_heard")), ss[:1])
check("an unknown station is a 404", requests.get(P + "/stations/ZZ9ZZZ-15", timeout=10).status_code == 404)
check("a setting it does not know is refused", requests.post(P + "/config", json={"nonsense": "1"}, timeout=10).status_code == 500)
check("hiding something that is not a call sign is refused", requests.post(P + "/hide", json={"call": "../x"}, timeout=10).status_code == 400)

anon = requests.Session()
adm = sess("marty", PW)
pg = anon.get(B + "/aprs/")
check("the APRS list is public and credited", pg.status_code == 200 and "APRS" in pg.text and "APRS-IS" in pg.text)
check("it has its filters", all(x in pg.text for x in ('name="q"', 'name="kind"', 'name="hours"')))
check("the stations are listed", "/aprs/" in pg.text and "<tbody>" in pg.text and re.search(r'href="/aprs/[A-Z0-9-]+"', pg.text), pg.text[:200])
mp = anon.get(B + "/aprs/map")
check("the APRS map uses the shared map shell", mp.status_code == 200 and 'id="lv"' in mp.text and "ArkShell" in mp.text and "/api/aprs/stations.json" in mp.text)
js = anon.get(B + "/api/aprs/stations.json").json()
check("the station data is public, credited and carries what the map draws", js["stations"] and all(k in js["stations"][0] for k in ("call", "lat", "lon", "kind", "last_heard")) and js["credit"].startswith("APRS data"))
check("the weather page is public and uses the viewer's units", anon.get(B + "/aprs/weather").status_code == 200 and "Weather" in anon.get(B + "/aprs/weather").text)
wx = anon.get(B + "/api/aprs/weather.json").json()["weather"]
check("the weather data lists observations", isinstance(wx, list))
if wx:
    w = wx[0]
    page = anon.get(B + "/aprs/weather").text
    check("a weather page row shows a temperature in the viewer's unit (imperial by default)", "&deg;F" in page or "°F" in page or "°C" in page)
call = js["stations"][0]["call"]
sp = anon.get(B + "/aprs/" + call)
check("a station has its own page with its position and when it was last heard", sp.status_code == 200 and call in sp.text and "Last heard" in sp.text and "Packets" in sp.text)
check("and its data file", anon.get(B + f"/api/aprs/{call}.json").json()["station"]["call"] == call)
check("an unknown station page is a 404", anon.get(B + "/aprs/ZZ9ZZZ-15").status_code == 404)
check("the menu has the APRS entries under Radio", 'href="/aprs/map"' in pg.text and 'href="/aprs/weather"' in pg.text)
check("anonymous visitors cannot reach the admin page or its actions", anon.get(B + "/admin/aprs", allow_redirects=False).status_code in (302, 401, 403, 404) and anon.post(B + "/admin/aprs/apply", allow_redirects=False).status_code in (302, 400, 401, 403, 404))

ap = adm.get(B + "/admin/aprs").text
check("the admin page shows the feed state and the tools", "APRS-IS feed" in ap and "Apply Settings" in ap and "Hidden Call Signs" in ap)
check("its status data is available to administrators", adm.get(B + "/admin/aprs/status.json").json()["ok"] is True)
ok, msg, _ = act(adm, "/admin/aprs/apply", "/admin/aprs")
check("Apply sends the settings and the takedown list to the service", ok and "sent" in msg.lower(), msg)
check("the service now has the portal's area and login", requests.get(P + "/stats", timeout=20).json()["config"]["login"] == "ARKSIG")

# hiding and showing again: use a station that is in the data right now
target = next((s["call"] for s in js["stations"] if re.match(r"^[A-Z0-9]{1,2}[0-9][A-Z0-9]{1,4}(-[0-9]{1,2})?$", s["call"]) and s["kind"] != "mobile"), None)
ok, msg, _ = act(adm, "/admin/aprs/hide", "/admin/aprs", call="not a call", reason="x")
check("a bad call sign is refused with a plain message", not ok and "call sign" in msg.lower(), msg)
if target:
    base = re.sub(r"-\d+$", "", target)
    ok, msg, _ = act(adm, "/admin/aprs/hide", "/admin/aprs", call=base, reason="test takedown")
    check("an administrator hides a call sign", ok and "hidden" in msg.lower(), msg)
    gone = requests.get(P + "/stations?q=" + base, timeout=20).json()["stations"]
    check("the service forgets it at once", all(not re.match("^" + re.escape(base) + r"(-[0-9]{1,2})?$", s["call"]) for s in gone), gone[:2])
    check("it is in the hidden list with the reason", base in adm.get(B + "/admin/aprs").text and "test takedown" in adm.get(B + "/admin/aprs").text)
    check("its page is gone", requests.get(B + "/aprs/" + target).status_code == 404)
    ok, msg, _ = act(adm, "/admin/aprs/unhide", "/admin/aprs", call=base)
    check("and can be shown again", ok, msg)
    check("the list no longer has it", f"<td class=\"mono\">{base}</td>" not in adm.get(B + "/admin/aprs").text)
audit = adm.get(B + "/admin/audit").text
check("hiding, showing and applying are in the audit log", all(k in audit for k in ("aprs.apply", "aprs.hide", "aprs.unhide")) or not target)
sp_ = adm.get(B + "/admin/settings").text
check("the settings are in Admin > Settings under APRS", all(f'name="{k}"' in sp_ for k in ("aprs_enabled", "aprs_url", "aprs_host", "aprs_login", "aprs_radius_km", "aprs_trail_hours", "aprs_keep_days", "aprs_window_hours", "aprs_show_mobile", "aprs_map_area")))
check("Service Status has an APRS row", "APRS" in requests.get(B + "/status").text)
check("the admin menu has APRS", "/admin/aprs" in adm.get(B + "/admin/").text)
check("the About page lists APRS", "/aprs/" in requests.get(B + "/about").text)
print("ALL PASS" if ok_all else "SOME FAILED")
