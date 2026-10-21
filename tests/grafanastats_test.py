"""Grafana statistics through the updater: the Infinity plugin and datasource baked into the image, and the two statistics dashboards (variables, queries against the portal's public files).
(The image itself was built and run on the test box: both dashboards imported and a live query returned the portal's data.)"""
import json, os
here = os.path.dirname(os.path.abspath(__file__)); g = os.path.join(here, "..", "deploy", "grafana")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
df = open(os.path.join(g, "Dockerfile.grafana"), encoding="utf-8").read()
check("the Dockerfile installs the Infinity plugin into the bundled plugins folder and carries both dashboards", "yesoreyeram-infinity-datasource" in df and "data/plugins-bundled" in df and "weather-dashboard.json" in df and "sondes-dashboard.json" in df)
check("the datasource is provisioned without any secret", "portal-json" in open(os.path.join(g, "provisioning", "datasources", "portal-json.yaml"), encoding="utf-8").read() and "password" not in open(os.path.join(g, "provisioning", "datasources", "portal-json.yaml"), encoding="utf-8").read().lower())
check("setup.sh imports them at every start", "weather:Weather_Dashboard" in open(os.path.join(g, "setup.sh"), encoding="utf-8").read() and "sondes:Radiosonde_Dashboard" in open(os.path.join(g, "setup.sh"), encoding="utf-8").read())
for f, uid, names in (("weather-dashboard.json", "weather", {"sources", "reading", "period", "counties", "cities", "zips", "stations", "near"}), ("sondes-dashboard.json", "sondes", {"kinds", "status", "sites", "stations", "flights"})):
    d = json.load(open(os.path.join(g, f), encoding="utf-8"))
    check(f"{f}: uid, the filter variables and panels that read the portal's file", d["uid"] == uid and {v["name"] for v in d["templating"]["list"]} == names and len(d["panels"]) > 10 and any(p["targets"][0].get("url", "").startswith("http://adsb-portal:8430/api/") for p in d["panels"]) and d["title"] in ("Weather Dashboard", "Radiosonde Dashboard"), d["uid"])
check("the shell scripts have unix line endings", all(b"\r" not in open(os.path.join(g, f), "rb").read() for f in ("setup.sh", "entrypoint.sh")))
print("ALL PASS" if ok_all else "SOME FAILED")
