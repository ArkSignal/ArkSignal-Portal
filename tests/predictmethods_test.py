"""The three forecast methods of the prediction service (statistical, our wind model, Tawhiri) and the choice between them: the service answers each, keeps and scores them separately, the portal passes the
choice through, and the Radiosonde Map offers it. Tawhiri is tested against a small stand-in server on this PC; the wind method reads the live Open-Meteo forecast (skipped when it cannot be reached).
Needs the prediction service (sh tests/deploy_predict.sh) and the test portal."""
import json, os, re, time, requests
from datetime import datetime, timedelta, timezone
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
S = f"http://{HOST}:18470"
try:
    requests.get(S + "/health", timeout=5).json()
except Exception as e:
    print("SKIP predictmethods_test: the prediction service is not running on the test box", type(e).__name__); raise SystemExit(0)
when = (datetime.now(timezone.utc) + timedelta(hours=20)).replace(minute=0, second=0, microsecond=0)
st = requests.get(S + "/stats").json()
requests.post(S + "/config", json={"tawhiri_url": "", "wind_source": "openmeteo"}); time.sleep(22)   # a setting is cached for 20 seconds
check("the service lists the methods it offers (Tawhiri only once it has an address)", "statistical" in st["methods"] and "wind" in st["methods"] and "tawhiri" not in st["methods"], st.get("methods"))
rs = requests.get(S + "/predict/at", params={"site": "72340", "when": when.isoformat(), "method": "statistical"}).json()
check("the statistical method answers as before and says which method it is", rs["prediction"] and rs["prediction"]["method"] == "statistical" and rs["method"] == "statistical")
rw = requests.get(S + "/predict/at", params={"site": "72340", "when": when.isoformat(), "method": "wind"}, timeout=90).json()
if rw["prediction"] is None:
    print("SKIP the wind method: no forecast winds could be had (Open-Meteo unreachable from the test box?)", rw.get("note"))
else:
    p = rw["prediction"]
    check("the wind method gives a landing, a drawn path from the launch site to it, the burst and the source", p["method"] == "wind" and len(p["path"]) > 20 and p["path"][0][3] < 400 and max(q[3] for q in p["path"]) > 20000 and "winds" in p["path_source"]
          and 0 < p["drift_km"] < 400 and p["burst"][2] > 20000, p)
    check("its path starts at the launch site and ends where it lands", abs(p["path"][0][1] - p["launch_lat"]) < 0.01 and abs(p["path"][-1][1] - p["lat"]) < 0.01 and abs(p["path"][-1][2] - p["lon"]) < 0.01)
    check("the two methods give different answers (one reads the winds)", abs(p["lat"] - rs["prediction"]["lat"]) + abs(p["lon"] - rs["prediction"]["lon"]) > 0.01)
    n1 = requests.get(S + "/stats").json()["outside"]
    requests.get(S + "/predict/at", params={"site": "72340", "when": when.isoformat(), "method": "wind"}, timeout=90)
    n2 = requests.get(S + "/stats").json()["outside"]
    meteo = lambda o: sum(v[0] for h in o if "open-meteo" in h["host"] for v in h["days"].values())
    check("the same forecast winds are not asked for twice, and the requests are counted by host", meteo(n1) >= 1 and meteo(n2) == meteo(n1), (meteo(n1), meteo(n2)))
check("an unknown method is refused by the list endpoint", requests.get(S + "/predict/next", params={"method": "magic"}).status_code == 400)
nx = requests.get(S + "/predict/next", params={"method": "wind"}, timeout=120).json()
check("the next launches come per method, with scores per method", all(x.get("method") == "wind" for x in nx["predictions"]) and "scores_by_method" in nx and nx["scores"]["method"] == "wind", nx.get("scores"))
sc = requests.get(S + "/scores").json()
check("the scores keep each method apart", "by_method" in sc and "statistical" in sc["by_method"] and "wind" in sc["by_method"])

# ---- Tawhiri against a stand-in server (fake_tawhiri.py, started on the test box)
import subprocess
def ssh(c): return subprocess.run(["ssh", "-q", "nastemp", c], capture_output=True, text=True, timeout=60).stdout
subprocess.run(["scp", "-q", "fake_tawhiri.py", "nastemp:/tmp/fake_tawhiri.py"], check=True)
ssh("pkill -f '[f]ake_tawhiri'; rm -f /tmp/tawhiri_seen.log")
ssh("cd /tmp && (setsid nohup python3 fake_tawhiri.py 18999 >/tmp/ft.log 2>&1 < /dev/null &); sleep 1")
for _ in range(10):   # the stand-in must be answering before Tawhiri is switched on
    if ssh("curl -s -o /dev/null -w %{http_code} 'localhost:18999/x?launch_datetime=2026-10-20T12%3A00%3A00Z'").strip() == "200":
        break
    time.sleep(1)
me = ssh("hostname -I").split()[0]   # the test box's own address, as the service container reaches it
requests.post(S + "/config", json={"tawhiri_url": f"http://{me}:18999/api/v1/"})
time.sleep(22)
check("with an address the service offers Tawhiri", "tawhiri" in requests.get(S + "/stats").json()["methods"])
rt = requests.get(S + "/predict/at", params={"site": "72340", "when": when.isoformat(), "method": "tawhiri"}, timeout=90).json()
pt = rt["prediction"]
check("Tawhiri's trajectory becomes the forecast: landing, path, burst, source, drift", pt and pt["method"] == "tawhiri" and abs(pt["lat"] - 35.1) < 1e-3 and abs(pt["lon"] + 90.9) < 1e-3 and pt["burst"][2] == 30000 and "Tawhiri" in pt["path_source"] and pt["flight_min"] == 125, pt)
seen = [l for l in ssh("cat /tmp/tawhiri_seen.log").splitlines() if "profile=" in l]
check("it was asked for the launch the site really has (standard profile, longitude 0 to 360, UTC time)", seen and "profile=standard_profile" in seen[0] and "launch_longitude=26" in seen[0] and when.strftime("%Y-%m-%dT%H") in seen[0].replace("%3A", ":"), seen[:1])
requests.post(S + "/config", json={"tawhiri_url": ""}); ssh("pkill -f '[f]ake_tawhiri'")
time.sleep(22)
check("without an address Tawhiri is not offered again", "tawhiri" not in requests.get(S + "/stats").json()["methods"])

# ---- the portal and the map
anon = requests.Session()
a = anon.get(B + "/api/sondes/predictions.json?method=wind").json()
check("the portal passes the choice through and lists the methods to pick from", a.get("enabled") is False or (a.get("method") == "wind" and {m["id"] for m in a["methods"]} >= {"statistical", "wind"}), {k: a.get(k) for k in ("enabled", "method", "methods")})
b = anon.get(B + "/api/sondes/predictions.json?method=nonsense").json()
check("a made-up method falls back to the site default", b.get("enabled") is False or b.get("method") == b.get("default_method"), b.get("method"))
mp = anon.get(B + "/sondes/map").text
check("the Radiosonde Map keeps the Predictor choice in the browser and offers it in the launch site's Forecast block, beside the buttons that make forecasts", 'id="st-method"' in mp and "ark_sonde_method" in mp and 'id="sm-method"' not in mp and mp.index('id="st-method"') < mp.index('id="st-next"'))
adm = sess("marty", PW)
sp = adm.get(B + "/admin/settings").text
check("Settings > Prediction Service has the method settings", all(x in sp for x in ("predict_methods", "predict_default_method", "predict_wind_url", "predict_tawhiri_url")))
requests.post(S + "/config", json={"wind_source": "auto"})
print("ALL PASS" if ok_all else "SOME FAILED")
