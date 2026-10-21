"""Earthquakes map layer: recent earthquakes from the USGS as points (app/quakes.py, /api/quakes.geojson), a layer on the maps, settings in Admin.
The live part needs the test box to reach earthquake.usgs.gov; it passes with zero earthquakes too."""
import requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
r = requests.get(B + "/api/quakes.geojson", timeout=60)
g = r.json()
check("the earthquake data is GeoJSON points", r.status_code == 200 and g.get("type") == "FeatureCollection" and all(f["geometry"]["type"] == "Point" for f in g["features"]), r.status_code)
check("each earthquake has a magnitude, a step to color by, a place and a time", all({"event", "mag", "KIND", "headline", "time"} <= set(f["properties"]) and f["properties"]["KIND"] in ("M1", "M2", "M3", "M4") for f in g["features"]))
check("the earthquakes are inside the configured box", all(32.4 <= f["geometry"]["coordinates"][1] <= 38.6 and -95.1 <= f["geometry"]["coordinates"][0] <= -87.9 for f in g["features"]))
check("the USGS link, when there is one, is a USGS address", all((f["properties"].get("link") or "https://earthquake.usgs.gov/").startswith("https://earthquake.usgs.gov/") for f in g["features"]))
adm = sess("marty", PW)
check("the Earthquakes layer is on the maps", all("quakes.geojson" in adm.get(B + p).text for p in ("/live", "/weather/map")))
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("Earthquakes Layer", "Earthquakes: Area", "Earthquakes: Smallest Magnitude", "Earthquakes: How Many Days Back", "Earthquakes: Refresh Every")))
print("ALL PASS" if ok_all else "SOME FAILED")
