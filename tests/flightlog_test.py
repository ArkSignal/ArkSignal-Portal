import re, subprocess, requests, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def psql(sql): return ssh(D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
anon = requests.Session(); adm = sess("marty", PW)
# the logic, inside the container
subprocess.run(["scp", "-q", "flightlog_unit.py", "nastemp:adsb-portal/flightlog_unit.py"], check=True, capture_output=True)
ssh(D + "cp ~/adsb-portal/flightlog_unit.py adsb-portal:/tmp/flightlog_unit.py")
out = ssh(D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/flightlog_unit.py")
print(out.strip())
check("the flight log logic passes its unit checks", "ALL PASS" in out and "FAIL" not in out, out[-300:])
# the log fills from the live feed by itself (background job)
n = "0"
for _ in range(40):   # the job starts about 20 seconds after the portal does, then looks every 10
    n = psql("SELECT COUNT(*) FROM flight_log WHERE hex NOT LIKE 'fe%' AND hex <> 'fd0001'")
    if n.isdigit() and int(n) > 0: break
    time.sleep(3)
check("the portal has been recording live aircraft on its own", n.isdigit() and int(n) > 0, n)
# the search API
psql("DELETE FROM flight_log WHERE hex='fd0001'")
psql("INSERT INTO flight_log (hex, callsign, reg, type, first_seen, last_seen, start_lat, start_lon, end_lat, end_lon, max_alt, max_gs, positions) VALUES ('fd0001','ZZQ777','N777ZQ','C172', now() - interval '3 days', now() - interval '3 days' + interval '50 minutes', 34.1, -92.1, 34.9, -92.9, 4500, 120, 40)")
d = anon.get(B + "/api/history/flights.json?q=zzq7").json()
check("an earlier flight is found by part of its callsign", len(d["flights"]) == 1 and d["flights"][0]["callsign"] == "ZZQ777" and d["flights"][0]["max_alt"] == 4500, str(d)[:200])
check("and by registration, hex, and aircraft type", all(len(anon.get(B + f"/api/history/flights.json?q={q}").json()["flights"]) >= 1 for q in ("N777ZQ", "fd0001", "C172")))
day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 86400))
check("dates narrow it down", len(anon.get(B + f"/api/history/flights.json?q=zzq777&from={day}&to={day}").json()["flights"]) == 1 and len(anon.get(B + "/api/history/flights.json?q=zzq777&from=2001-01-01&to=2001-01-02").json()["flights"]) == 0)
check("bad dates are ignored, not errors", anon.get(B + "/api/history/flights.json?q=zzq777&from=notadate").status_code == 200)
check("the answer says since when the history runs", d.get("since") and d.get("enabled") is True)
# the pages: Flights is live only; history has its own page with the heatmap-style period pickers
fp = anon.get(B + "/flight/").text
check("the Flights page is for aircraft in the air now and points to Flight History", "Earlier Flights" not in fp and 'href="/history/"' in fp)
check("the menu has a History link", 'href="/history/"' in anon.get(B + "/").text)
hp = anon.get(B + "/history/").text
check("Flight History has a search box, 'last N / ending' pickers and exact date and time pickers", all(x in hp for x in ('name="q"', 'name="duration"', 'name="end"', 'type="datetime-local"', 'name="from"', 'name="to"')))
w = anon.get(B + "/history/?q=zzq777&duration=168&end=0").text
check("a period that covers the flight lists it with a Flight Progress link", "ZZQ777" in w and "/flight/fd0001?at=" in w, re.findall(r"No flights.{0,40}", w))
check("a period that does not cover it does not", "ZZQ777" not in anon.get(B + "/history/?q=zzq777&duration=24&end=0").text)
t0 = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(time.time() - 4 * 86400))
t1 = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(time.time() - 2 * 86400))
check("exact dates and times work", "ZZQ777" in anon.get(B + f"/history/?q=zzq777&from={t0}&to={t1}").text)
check("a backwards range says so", "must be after" in anon.get(B + f"/history/?from={t1}&to={t0}").text)
check("the live map links its search suggestions to Flight History", "More in Flight History" in anon.get(B + "/live").text and "lv-hist" in anon.get(B + "/live").text)
tr = anon.get(B + "/api/trace/fd0001.json?at=" + str(int(time.time()) - 3 * 86400)).json()
check("opening an earlier flight asks the hub for that day and degrades quietly when it has none", "points" in tr and tr.get("end") is not None, str(tr)[:200])
check("the flight page accepts an earlier flight", anon.get(B + "/flight/fd0001?at=123").status_code == 200)
st = adm.get(B + "/admin/settings").text
check("Flight History settings exist", all(x in st for x in ("Keep a History of Flights", "Keep Flight History For", "Start a New Flight After a Gap", "Look at the Live Aircraft Every")))
psql("DELETE FROM flight_log WHERE hex='fd0001'")
print("ALL PASS" if ok_all else "SOME FAILED")
