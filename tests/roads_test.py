"""Highways: the in-container unit checks (roads_unit.py), the Data Updates job loading the real Interstates, US and state highways of Arkansas from the Census, the GeoJSON the map layer reads (and its
caching headers), the Highways layer on the aircraft map's Layers list, and the settings."""
import re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout

out = ssh("docker cp ~/adsb-portal/tests/roads_unit.py adsb-portal:/tmp/roads_unit.py; docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/roads_unit.py")
check("roads_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-500:])

adm = sess("marty", PW)
t = re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + "/admin/data-updates/").text).group(1)
r = adm.post(B + "/admin/data-updates/roads/run", data={"csrf_token": t}, headers={"X-Requested-With": "XMLHttpRequest"})
check("Run Now starts the Highways job", r.status_code == 200, r.status_code)
res = ""
for _ in range(40):
    time.sleep(4)
    h = adm.get(B + "/admin/data-updates/").text
    m = re.search(r'Highways.*?(\d+ routes \([^<]*\)|failed[^<]{0,120})', h, re.S)
    if m:
        res = m.group(1); break
check("it loads real routes from the Census", "routes" in res and int(res.split()[0]) > 40, res)
gj = requests.get(B + "/api/roads.geojson")
j = gj.json()
routes = {f["properties"]["ROUTE"] for f in j["features"]}
check("the layer data has Interstates, US highways and state highways of Arkansas", {"I-40", "I-30"} <= routes and any(x.startswith("US ") for x in routes) and any(x.startswith("AR ") for x in routes), sorted(routes)[:15])
check("it is cached by the browser and has a version tag", "max-age" in gj.headers.get("Cache-Control", "") and gj.headers.get("ETag"))
check("asking again with the tag gives 304", requests.get(B + "/api/roads.geojson", headers={"If-None-Match": gj.headers["ETag"]}).status_code == 304)
check("only Interstates when asked", {f["properties"]["KIND"] for f in requests.get(B + "/api/roads.geojson?kinds=I").json()["features"]} == {"I"})
check("the size is sensible for a map layer (under 6 MB)", len(gj.content) < 6_000_000, len(gj.content))
lay = adm.get(B + "/live").text
check("the aircraft map's layer list has Highways", '"slug": "highways"' in lay or "'slug': 'highways'" in lay or "highways" in lay)
check("the layer is in Admin > Map Layers", "Highways" in adm.get(B + "/admin/layers").text)
sp = adm.get(B + "/admin/settings").text
check("the settings are in Admin > Settings", all(f'name="{k}"' in sp for k in ("roads_source_url", "roads_bounds", "roads_update_days")))
print("ALL PASS" if ok_all else "SOME FAILED")
