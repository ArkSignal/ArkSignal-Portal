"""A launch site's flyout on the Radiosonde Map: past flights from that site for chosen dates, the next launches' forecast flights switched on from there, and a forecast for a launch at a date and time you pick (like SondeHub). Needs the prediction service on the test box for the forecast parts."""
import os, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
sm = requests.get(B + "/sondes/map").text
check("Show These Flights becomes Hide These Flights once the history is shown, and puts the map back", all(x in sm for x in ("function histShown()", "Hide These Flights", "Show These Flights", "The history flights are hidden.")))
check("forecast flights are numbered in the order they launch, on the landing and in every tooltip", all(x in sm for x in ("p._n = i + 1", "Forecast #", 'className: "fcnum"')))
check("a forecast can be cleared: a Clear Forecast button on the site flyout and a Clear Forecasts button on the map", all(x in sm for x in ('id="st-clear"', 'id="sm-fcclear"', "function clearForecasts()", "customFc = []")))
check("the site flyout has past flights with dates, next launches and a launch date and time forecast", all(x in sm for x in ("function wireSite(it)", 'id="st-from"', 'id="st-show"', 'id="st-next"', 'id="st-when"', 'id="st-fc"', "/api/sondes/forecast.json")))
check("forecast flights are no longer a switch in the filters (they start off and come from a site)", 'id="sm-fc" checked hidden' in sm and "> Forecast flights</label>" not in sm)

psql("DELETE FROM sondes WHERE serial IN ('ZZSITE-A', 'ZZSITE-B')")
psql("INSERT INTO sondes (serial, kind, first_seen, last_seen, last_lat, last_lon, status, site_id, site_name) VALUES ('ZZSITE-A', 'radiosonde', now() - interval '20 days', now() - interval '20 days', 35.0, -92.0, 'landed', '72340', 'North Little Rock'), ('ZZSITE-B', 'radiosonde', now() - interval '20 days', now() - interval '20 days', 32.5, -93.8, 'landed', '72248', 'Shreveport')")
try:
    from datetime import date, timedelta
    a, b = (date.today() - timedelta(days=22)).isoformat(), (date.today() - timedelta(days=18)).isoformat()
    j = requests.get(B + "/api/sondes/map.json", params={"from": a, "to": b, "site": "72340", "static": 0}).json()
    ids = [f["id"] for f in j["flights"]]
    check("history for one site holds that site's flights and not another's", "ZZSITE-A" in ids and "ZZSITE-B" not in ids, ids)
    j2 = requests.get(B + "/api/sondes/map.json", params={"from": a, "to": b, "static": 0}).json()
    check("without a site both are there", {"ZZSITE-A", "ZZSITE-B"} <= {f["id"] for f in j2["flights"]})
finally:
    psql("DELETE FROM sondes WHERE serial IN ('ZZSITE-A', 'ZZSITE-B')")

try:
    h = requests.get(f"http://{HOST}:18470/health", timeout=8).json()
except Exception:
    h = None
if h:
    r = requests.get(f"http://{HOST}:18470/predict/at", params={"site": "72340"}, timeout=20)
    check("the prediction service wants a site and a time", r.status_code == 400)
    r = requests.get(f"http://{HOST}:18470/predict/at", params={"site": "72340", "when": "2026-12-01T12:00:00Z"}, timeout=40)
    check("and answers a forecast (or says why not) for any date", r.status_code == 200 and "prediction" in r.json(), r.text[:200])
    f = requests.get(B + "/api/sondes/forecast.json", params={"site": "72340", "when": "2026-12-01T12:00:00Z"}, timeout=40).json()
    check("the portal passes it on with the site's name", "enabled" in f and "prediction" in f and ("note" in f), f)
    bad = requests.get(B + "/api/sondes/forecast.json", params={"site": "x;drop table", "when": "2026-12-01T12:00:00Z"}).json()
    check("a site that is not a site code is refused politely", bad.get("prediction") is None)
else:
    print("note: the prediction service is not running on the test box, the forecast parts are skipped")
print("ALL PASS" if ok_all else "SOME FAILED")
