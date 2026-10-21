"""NOAA visible satellite, water vapor and lightning layers (nowCOAST WMS) in the Weather panel: seeded as layers, in the right groups, and the NOAA server draws tiles for them.
The tile part needs the test box to reach nowcoast.noaa.gov."""
import requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
adm = sess("marty", PW)
live = adm.get(B + "/live").text
check("the three layers are on the maps", all(x in live for x in ("noaa-vis", "noaa-wv", "noaa-lightning")), "")
import re, json
def layer(slug):
    m = re.search(r'\{[^{}]*"slug": ?"' + slug + r'"[^{}]*\}', live)
    return json.loads(m.group(0)) if m else {}
check("visible and water vapor are under Satellite and lightning under Alerts and Hazards", layer("noaa-vis").get("wx_group") == "Satellite" and layer("noaa-wv").get("wx_group") == "Satellite" and layer("noaa-lightning").get("wx_group") == "Alerts and Hazards", [layer(s).get("wx_group") for s in ("noaa-vis", "noaa-wv", "noaa-lightning")])
for url, name in (("satellite/wms", "global_visible_imagery_mosaic"), ("satellite/wms", "global_water_vapor_imagery_mosaic"), ("lightning_detection/wms", "ldn_lightning_strike_density")):
    r = requests.get("https://nowcoast.noaa.gov/geoserver/" + url, params={"service": "WMS", "version": "1.1.1", "request": "GetMap", "layers": name, "styles": "", "format": "image/png", "transparent": "true", "srs": "EPSG:3857", "bbox": "-10600000,3500000,-9800000,4300000", "width": 256, "height": 256}, headers={"User-Agent": "ArkSignalPortal/1.0"}, timeout=60)
    check(f"NOAA draws a tile for {name}", r.status_code == 200 and r.content[:4] == b"\x89PNG", r.status_code)
print("ALL PASS" if ok_all else "SOME FAILED")
