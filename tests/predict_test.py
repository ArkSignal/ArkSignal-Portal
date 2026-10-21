"""The prediction service as the portal sees it: the service's own API, the forecast on the Radiosondes overview and the Balloon Map, the admin panel and its actions,
the Service Status row, the settings, and the in-container unit checks (predict_unit.py). Needs the service running on the test box (sh tests/deploy_predict.sh)."""
import os, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18470"
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout

try:
    h = requests.get(P + "/health", timeout=10).json()
except Exception as e:
    print("SKIP predict_test: the prediction service is not running on the test box (sh tests/deploy_predict.sh)", e); raise SystemExit(0)
check("the service answers and says its version", h.get("ok") is True and "version" in h)
st = requests.get(P + "/stats", timeout=20).json()
check("its statistics carry flights, sites, months, forecasts and the import state", all(k in st for k in ("flights", "sites", "by_site", "months", "predictions", "import", "config")), list(st))
check("its settings come from the database with defaults", st["config"]["sites"].startswith("72340") and st["config"]["since"] >= "2018-01-01")
check("an unknown path is a plain 404", requests.get(P + "/nope", timeout=10).status_code == 404)
check("a flight that is not in the archive is a 404 with a reason", requests.get(P + "/flights/NOSUCHSERIAL99/track", timeout=60).status_code == 404)
check("a setting it does not know is refused", requests.post(P + "/config", json={"nonsense": "1"}, timeout=10).status_code == 500)
fl = requests.get(P + "/flights?limit=5", timeout=20).json()["flights"]
check("flights come back newest first with their summaries", (not fl) or ("launch_ts" in fl[0] and "site" in fl[0]))
fc = requests.get(P + "/predict/next?site=72340", timeout=60).json()
check("the forecast answers with predictions and an error history", "predictions" in fc and "scores" in fc)

out = ssh("docker cp ~/adsb-portal/tests/predict_unit.py predict:/tmp/predict_unit.py; docker exec -w /app -e PYTHONPATH=/app predict python /tmp/predict_unit.py")
check("predict_unit.py passes inside the service container", "ALL PASS" in out and "FAIL" not in out, out[-400:])

# ---- the portal
adm = sess("marty", PW)
j = requests.get(B + "/api/sondes/predictions.json").json()
check("the portal serves the forecast to everyone", j.get("enabled") is True and isinstance(j.get("predictions"), list), j)
if st["flights"] > 50:
    check("with history imported it forecasts launches, each with a landing area", len(j["predictions"]) >= 1 and all(k in j["predictions"][0] for k in ("lat", "lon", "r50_km", "r90_km", "name")), j["predictions"][:1])
home = requests.get(B + "/sondes/").text
check("the Radiosondes overview has the Next Launch Forecast card", "Next Launch Forecast" in home and "/api/sondes/predictions.json" in home)
bm = requests.get(B + "/sondes/map").text
check("the Balloon Map has the forecast layer switch", 'id="sm-fc"' in bm and "loadForecast" in bm)
check("anonymous visitors cannot reach the admin status or actions", requests.get(B + "/admin/sondes/predict/status.json", allow_redirects=False).status_code in (302, 401, 403) and requests.post(B + "/admin/sondes/predict/start", allow_redirects=False).status_code in (302, 400, 401, 403))
js = adm.get(B + "/admin/sondes/predict/status.json").json()
check("an administrator sees the service status", js["enabled"] and js["ok"] and js["stats"]["flights"] >= 0, js)
page = adm.get(B + "/admin/sondes").text
check("the admin page has the Prediction Service panel with its buttons", "Prediction Service" in page and "/admin/sondes/predict/apply" in page and "Start Archive Import" in page)
t = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
r = adm.post(B + "/admin/sondes/predict/apply", data={"csrf_token": t}, allow_redirects=True)
check("Apply sends the settings to the service", "sent to the prediction service" in r.text.lower(), re.findall(r'class="flash[^<]*', r.text)[:2])
cfg_now = requests.get(P + "/stats", timeout=20).json()["config"]
check("the service now holds the portal's settings", cfg_now["rate_per_second"] == "4" and cfg_now["sites"].startswith("72340"), cfg_now)
r = adm.post(B + "/admin/sondes/predict/push", data={"csrf_token": t}, allow_redirects=True)
check("Send Flights Recorded Here hands over the portal's own flights", "service" in r.text.lower() and "stored" in r.text.lower(), re.findall(r'class="flash[^<]*', r.text)[:2])
check("an unknown action is a 404", adm.post(B + "/admin/sondes/predict/explode", data={"csrf_token": t}).status_code == 404)
audit = adm.get(B + "/admin/audit").text if adm.get(B + "/admin/audit").status_code == 200 else ""
check("the actions are in the audit log", (not audit) or "sondes.predict_apply" in audit)
stt = requests.get(B + "/api/status.json").text
check("Service Status has a Balloon Forecasts row", "Balloon Forecasts" in stt)
sp = adm.get(B + "/admin/settings").text
check("the settings are in Admin > Settings under Prediction Service", all(f'name="{k}"' in sp for k in ("predict_enabled", "predict_url", "predict_sites", "predict_since", "predict_rate", "predict_date_mode", "predict_horizon_hours")))
print("ALL PASS" if ok_all else "SOME FAILED")
