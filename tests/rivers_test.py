"""River Gauges map layer: river and lake gauges with their flood category as points (app/rivers.py, /api/rivers.geojson), a layer on the maps, settings in Admin.
The live part needs the test box to reach api.water.noaa.gov."""
import requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
r = requests.get(B + "/api/rivers.geojson", timeout=90)
g = r.json()
check("the gauge data is GeoJSON points", r.status_code == 200 and g.get("type") == "FeatureCollection" and all(f["geometry"]["type"] == "Point" for f in g["features"]), r.status_code)
check("the test box can read the gauges (a few hundred in the box)", len(g["features"]) > 50 and not g.get("stale"), len(g["features"]))
check("each gauge has a name, a flood category step, a size, the stage and a time", all({"event", "KIND", "size", "stage", "headline", "time"} <= set(f["properties"]) and f["properties"]["KIND"] in ("OK", "ACT", "MIN", "MOD", "MAJ") for f in g["features"]))
check("the NOAA link, when there is one, is a NOAA address", all((f["properties"].get("link") or "https://water.noaa.gov/").startswith("https://water.noaa.gov/gauges/") or not f["properties"].get("link") for f in g["features"]))
check("the data is small enough for a map layer (under 1 MB)", len(r.content) < 1_000_000, len(r.content))
adm = sess("marty", PW)
check("the River Gauges layer is on the maps", all("rivers.geojson" in adm.get(B + p).text for p in ("/live", "/weather/map")))
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("River Gauges Layer", "River Gauges: Area", "River Gauges: Refresh Every")))
print("ALL PASS" if ok_all else "SOME FAILED")
