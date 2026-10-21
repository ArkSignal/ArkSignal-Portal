"""The Weather Statistics and Radiosonde Statistics pages: the menus, the filters on the page, the data files, the settings, and the in-container checks (stats_unit.py)."""
import subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout
out = ssh("docker cp ~/adsb-portal/tests/stats_unit.py adsb-portal:/tmp/stats_unit.py; docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/stats_unit.py")
check("stats_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-700:])
w = requests.get(B + "/weather/statistics")
check("Weather Statistics is a public page with source, reading, period and area filters", w.status_code == 200 and all(x in w.text for x in ('data-src="members"', 'data-src="aprs"', 'data-src="airport"', 'id="ws-metric"', 'id="ws-period"', 'id="ws-area"', 'id="ws-tbl"')))
s = requests.get(B + "/sondes/statistics")
check("Radiosonde Statistics has dates, kinds, status and a search for a flight, station or site", s.status_code == 200 and all(x in s.text for x in ('id="rs-from"', 'data-src="nws"', 'id="rs-status"', 'id="rs-find"', 'id="rs-tbl"')))
nav = requests.get(B + "/").text
check("both are in their menus, and the receiving stations are the sonde stations", "/weather/statistics" in nav and "/sondes/statistics" in nav and "My Sonde Stations" in nav)
j = requests.get(B + "/api/weather/stats.json", params={"sources": "members,aprs,airport", "metric": "temp_f", "period": "24h"}).json()
check("the weather data file has the series and the stations", all(k in j for k in ("series", "stations", "selected", "label")))
check("the pickers' data files answer", "counties" in requests.get(B + "/api/weather/stats-areas.json").json() and "stations" in requests.get(B + "/api/weather/stats-stations.json", params={"q": "zz"}).json())
j = requests.get(B + "/api/sondes/stats.json").json()
check("the radiosonde data file has the summary, the days, the histogram, the stations and the flights", all(k in j for k in ("summary", "per_day", "burst_hist", "top_stations", "flights")))
sp = sess("marty", PW).get(B + "/admin/settings").text
check("the statistics are switchable and tunable in Settings > Weather Stations", all(f'name="{k}"' in sp for k in ("wx_stats_enabled", "wx_stats_minutes", "wx_stats_keep_days")))
print("ALL PASS" if ok_all else "SOME FAILED")
