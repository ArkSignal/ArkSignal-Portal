"""Hospital drive-time rings through the web: the hospital page has the layer and its honest label, the data file gives nested rings around the hospital (a real routing answer when the routing
server is reachable from the test box, otherwise skipped), the settings, the Data Updates job, and the in-container unit checks (driveranges_unit.py)."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout

out = ssh("docker cp ~/adsb-portal/tests/driveranges_unit.py adsb-portal:/tmp/driveranges_unit.py; docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/driveranges_unit.py")
check("driveranges_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-500:])

adm = sess("marty", PW)
hs = requests.get(B + "/api/hospitals.json?south=34.6&west=-92.5&north=34.9&east=-92.0").json()["hospitals"]
fid = next((h["id"] for h in hs if h["emergency"] and not h["approx"]), "")
check("a hospital with a position is available for the test", bool(fid), fid)
if fid:
    pg = requests.get(B + f"/hospitals/{fid}")
    check("the hospital page has the Drive-time rings layer", pg.status_code == 200 and 'id="h-rings"' in pg.text and f"/api/hospitals/{fid}/rings.json" in pg.text)
    check("and says it is an estimate, not an ambulance's route", "estimate" in pg.text.lower() and "ambulance" in pg.text.lower())
    check("an unknown hospital's rings are a 404", requests.get(B + "/api/hospitals/NOSUCH/rings.json").status_code == 404)
    # the test box reaches the routing server through the WireGuard tunnel address; production uses the hub-side address (the setting default)
    ssh("docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('osrm_url', 'http://osrm-tunnel.example.net:5000') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value\"")
    try:
        reach = requests.get("http://test-box.invalid:18430/healthz", timeout=5).ok and subprocess.run(["ssh", "-q", "nastemp", "curl -s -m 6 -o /dev/null -w %{http_code} http://osrm-tunnel.example.net:5000/"], capture_output=True, text=True, timeout=30).stdout.strip() in ("200", "400", "404")
    except Exception:
        reach = False
    if reach:
        j = requests.get(B + f"/api/hospitals/{fid}/rings.json", timeout=120).json()
        if j.get("busy"):
            import time; time.sleep(8); j = requests.get(B + f"/api/hospitals/{fid}/rings.json", timeout=120).json()
        check("the data file gives the rings (a real routing answer)", j.get("enabled") and sorted(map(int, j["rings"])) == [15, 30, 60, 90] and all(len(p) == 48 for p in j["rings"].values()), j.get("note"))
        h = adm.get(B + f"/hospitals/{fid}").text
        check("the second answer comes from what was kept", requests.get(B + f"/api/hospitals/{fid}/rings.json", timeout=20).json()["rings"].keys() == j["rings"].keys())
    else:
        print("note: the routing server is not reachable from the test box, real routing check skipped")
ssh("docker exec adsb-portal-db psql -U portal -d portal -c \"DELETE FROM settings WHERE key = 'osrm_url'\"")
sp = adm.get(B + "/admin/settings").text
check("the settings are in Admin > Settings under Routing", all(f'name="{k}"' in sp for k in ("osrm_enabled", "osrm_url", "osrm_ring_minutes", "osrm_ring_cache_days", "osrm_ring_states", "hospital_rings_update_days")))
check("Service Status has a Routing (OSRM) row", "Routing (OSRM)" in requests.get(B + "/api/status.json").text)
check("Data Updates has the Hospital Drive-Time Rings job", "Hospital Drive-Time Rings" in adm.get(B + "/admin/data-updates/").text)
print("ALL PASS" if ok_all else "SOME FAILED")
