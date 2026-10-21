"""Weather Alerts map layer: the National Weather Service's active alerts as shapes (app/wxalerts.py, /api/weather/alerts.geojson), kept small, a layer on the maps, settings in Admin.
The live part needs the test box to reach api.weather.gov; it passes with zero alerts too."""
import re, sys, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    sys.path.insert(0, "..")
    from app import wxalerts as wa
    pure = True
except Exception as e:   # the app's libraries are not installed on this PC
    pure = False
    print("SKIP the pure checks:", type(e).__name__)
if pure:
    check("alert events are sorted into kinds", [wa.kind_of(x) for x in ("Tornado Warning", "Severe Thunderstorm Watch", "Flash Flood Warning", "Winter Storm Warning", "Excessive Heat Warning", "Red Flag Warning", "Wind Advisory", "Air Quality Alert", "")] == ["TOR", "SVR", "FLD", "WIN", "HOT", "FIR", "WND", "OTH", "OTH"])
    ring = [[-92.0 + i * 0.0001, 34.0 + i * 0.0001] for i in range(2000)] + [[-92.0, 34.0]]
    slim = wa._slim_ring(ring)
    check("a very detailed outline is thinned to a few points and stays closed", slim is None or (len(slim) < 40 and slim[0] == slim[-1]), len(slim or []))
    check("a speck is dropped", wa._slim_ring([[-92, 34], [-92.0001, 34.0001], [-92, 34]]) is None)
r = requests.get(B + "/api/weather/alerts.geojson", timeout=120)
g = r.json()
check("the alerts layer data is GeoJSON", r.status_code == 200 and g.get("type") == "FeatureCollection" and isinstance(g.get("features"), list), r.status_code)
check("every alert has a shape and the fields the layer shows", all(f["geometry"]["type"] in ("Polygon", "MultiPolygon") and {"event", "KIND", "headline", "area", "ends"} <= set(f["properties"]) for f in g["features"]))
check("the data is small enough for a map layer (under 2 MB)", len(r.content) < 2_000_000, len(r.content))
check("it is kept briefly by the browser", "max-age" in r.headers.get("Cache-Control", ""))
adm = sess("marty", PW)
live = adm.get(B + "/live").text
check("the Weather Alerts layer is in the aircraft map's layers", "wx-alerts" in live and "/api/weather/alerts.geojson" in live)
wx = adm.get(B + "/weather/map").text
check("and on the Weather Map", "wx-alerts" in wx)
check("the alerts, earthquakes and river gauges sit in the Weather panel under Alerts and Hazards (not the Layers list)", live.count("Alerts and Hazards") >= 3 and "Alerts and Hazards" in wx, "")
check("alerts carry the text of the warning and a short area for the card", all("description" in f["properties"] and len(f["properties"]["area"]) <= 150 for f in g["features"]))
check("the shared map script has the legend and the card opener for these layers", all(x in adm.get(B + "/static/maplayers.js").text for x in ("dataLegend", "openLayerCard", "gaugeIcon")))
st = adm.get(B + "/admin/settings/aircraft").text + adm.get(B + "/admin/settings/weather").text + adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("Weather Alerts Layer", "Weather Alerts: States", "Weather Alerts: Refresh Every")))
print("ALL PASS" if ok_all else "SOME FAILED")
