"""Historical balloon flights through the web: the settings, the admin panel and its Start and Stop, the status data, the permissions, a real listing of the archive for one launch site and month,
and the in-container unit checks (history_unit.py)."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout

out = ssh("docker cp ~/adsb-portal/tests/history_unit.py adsb-portal:/tmp/history_unit.py; docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/history_unit.py")
check("history_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-600:])

adm = sess("marty", PW)
page = adm.get(B + "/admin/sondes").text
check("the admin page has the Historical Flights panel with Start and Stop", "Historical Flights" in page and "/admin/sondes/history/start" in page and "/admin/sondes/history/stop" in page)
js = adm.get(B + "/admin/sondes/history/status.json").json()
check("its status data has the switch, the sites, the counts and the progress", all(k in js for k in ("enabled", "sites", "since", "counts", "progress")) and "72340" in js["sites"] and js["enabled"] is False, js)
check("anonymous visitors cannot reach it", requests.get(B + "/admin/sondes/history/status.json", allow_redirects=False).status_code in (302, 401, 403, 404) and requests.post(B + "/admin/sondes/history/start", allow_redirects=False).status_code in (302, 400, 401, 403, 404))
t = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
r = adm.post(B + "/admin/sondes/history/start", data={"csrf_token": t}, allow_redirects=True)
check("Start switches the job on", adm.get(B + "/admin/sondes/history/status.json").json()["enabled"] is True)
r = adm.post(B + "/admin/sondes/history/stop", data={"csrf_token": t}, allow_redirects=True)
check("Stop switches it off", adm.get(B + "/admin/sondes/history/status.json").json()["enabled"] is False)
check("an unknown action is a 404", adm.post(B + "/admin/sondes/history/explode", data={"csrf_token": t}).status_code == 404)
audit = adm.get(B + "/admin/audit").text
check("starting and stopping are in the audit log", "sondes.history_start" in audit and "sondes.history_stop" in audit)
sp = adm.get(B + "/admin/settings").text
check("the settings are in Admin > Settings", all(f'name="{k}"' in sp for k in ("sonde_history_enabled", "sonde_history_sites", "sonde_history_since", "sonde_history_thin_seconds", "sonde_history_pause_seconds")))
mp = requests.get(B + "/sondes/map").text
check("the Balloon Map offers 1, 2 and 3 hour windows before 6 hours", all(f'<option value="{h}">' in mp.split('id="sm-hours"')[1].split("</select>")[0] for h in (1, 2, 3, 6)), mp.split('id="sm-hours"')[1][:300])
check("the data file accepts a one hour window", requests.get(B + "/api/sondes/map.json?hours=1").status_code == 200)
fl = requests.get(B + "/sondes/flights").text
check("the Flights page offers the short windows too", all(f'value="{h}"' in fl for h in (1, 2, 3)))
print("ALL PASS" if ok_all else "SOME FAILED")
