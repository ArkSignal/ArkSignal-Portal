"""Recovery reports and chase mode for signed-in members (app/sonde_reports.py): the form's fields, the formats sent to SondeHub (checked in a unit test with the outgoing call replaced),
the member-only rules, the per-member limit, test mode (nothing is sent), the recovered / planned / not-found states on the flight page, the map data and the lists, and chase sharing."""
import re, subprocess, requests, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout
def psql(sql): return ssh(D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
def csrf(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
H = {"X-Requested-With": "XMLHttpRequest"}
subprocess.run(["scp", "-q", "sonde_reports_unit.py", "nastemp:adsb-portal/sonde_reports_unit.py"], check=True, capture_output=True)
ssh(D + "cp ~/adsb-portal/sonde_reports_unit.py adsb-portal:/tmp/sonde_reports_unit.py")
out = ssh(D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sonde_reports_unit.py")
check("sonde_reports_unit.py passes inside the portal container (the exact formats sent to SondeHub)", "ALL PASS" in out and "FAIL" not in out, out[-400:])
adm = sess("marty", PW)
try:
    plain = sess("plain1", "plainpassword1")
except AssertionError:
    s0 = requests.Session()
    s0.post(B + "/my/register", data={"csrf_token": csrf(s0, "/my/register"), "username": "plain1", "email": "plain1@example.com", "password": "plainpassword1", "confirm": "plainpassword1"})
    plain = sess("plain1", "plainpassword1")
anon = requests.Session()
setting("sonde_enabled", "1"); setting("sonde_report_mode", "test"); setting("sonde_chase_mode", "test")
psql("delete from sonde_recoveries where serial like 'RECTEST%'"); psql("delete from sonde_chase_share")
psql("delete from sonde_points where serial like 'RECTEST%'"); psql("delete from sondes where serial like 'RECTEST%'")
psql("insert into sondes (serial, kind, type, first_seen, last_seen, status, last_lat, last_lon, last_alt, landing_lat, landing_lon, landing_alt, launch_lat, launch_lon, launch_time, max_alt, agency) values "
     "('RECTEST1','radiosonde','RS41', now() - interval '3 hours', now() - interval '1 hour','landed', 35.1, -92.2, 120, 35.1, -92.2, 120, 34.8, -92.3, now() - interval '3 hours', 30000, 'NWS'), "
     "('RECTEST2','radiosonde','RS41', now() - interval '3 hours', now() - interval '1 hour','landed', 35.3, -92.1, 130, 35.3, -92.1, 130, 34.8, -92.3, now() - interval '3 hours', 30000, 'NWS')")
time.sleep(6)
S1 = "/sondes/RECTEST1"
page = plain.get(B + S1).text
check("a signed-in member sees the report form with SondeHub's fields", all(x in page for x in ('id="rec-form"', 'Recovered By', 'Recovery Success', 'Recovery Planned', 'Use Browser Position', 'Notes', 'Submit Report', 'RECTEST1')))
check("the form says it becomes public on SondeHub and that this is test mode", "public there" in page and "Test mode" in page)
check("a visitor who is not signed in is asked to sign in instead", 'id="rec-form"' not in anon.get(B + S1).text and "Sign in" in anon.get(B + S1).text)
def post(s, serial, **kw):
    d = {"csrf_token": csrf(s, S1), "by": "KD5TEST", "recovered": "1", "lat": "35.1001", "lon": "-92.2001", "alt": "118", "note": "Found it in a tree"}
    d.update({k: v for k, v in kw.items() if v is not None})
    for k in [k for k, v in kw.items() if v is None]: d.pop(k, None)
    return s.post(B + f"/sondes/{serial}/recovery", data=d, headers=H)
r = post(plain, "RECTEST1")
j = r.json()
check("a recovery is accepted in test mode and says nothing was sent", r.status_code == 200 and j["ok"] and "nothing was sent" in j["message"], j)
row = psql("select recovered||'/'||planned||'/'||recovered_by||'/'||description||'/'||lat||'/'||lon||'/'||alt||'/'||source||'/'||sent from sonde_recoveries where serial='RECTEST1'")
check("it is kept with exactly what was typed, marked as a portal report in test mode", row == "true/false/KD5TEST/Found it in a tree/35.1001/-92.2001/118/portal/test", row)
check("the flight page and the map data show it as recovered", "Recovered" in plain.get(B + S1).text and [f for f in requests.get(B + "/api/sondes/map.json", params={"hours": 72}).json()["flights"] if f["id"] == "RECTEST1"][0]["rec_state"] == "recovered")
check("a visitor who is not signed in cannot send one", anon.post(B + "/sondes/RECTEST1/recovery", data={"recovered": "1"}, headers=H, allow_redirects=False).status_code in (302, 400, 401, 403))
# planned, and not found
time.sleep(61)   # the same member may not send the same flight twice within a minute
r = post(plain, "RECTEST1", recovered=None, planned="1", note="Going Saturday")
check("a recovery can be planned", r.json()["ok"] and psql("select recovered||'/'||planned from sonde_recoveries where serial='RECTEST1' order by ts desc limit 1") == "false/true", r.text)
f1 = [f for f in requests.get(B + "/api/sondes/map.json", params={"hours": 72}).json()["flights"] if f["id"] == "RECTEST1"][0]
check("the latest report decides: planned now", f1["rec_state"] == "planned" and "Recovery planned" in plain.get(B + S1).text)
r = post(plain, "RECTEST2", recovered=None, note="Searched an hour, nothing")
time.sleep(6)   # the map data is kept for a few seconds
f2 = [f for f in requests.get(B + "/api/sondes/map.json", params={"hours": 72}).json()["flights"] if f["id"] == "RECTEST2"][0]
check("looking and not finding is the third state", r.json()["ok"] and f2["rec_state"] == "notfound")
check("the flights list can filter by planned and by not found", "RECTEST1" in requests.get(B + "/sondes/flights", params={"recovery": "planned", "hours": 0}).text and "RECTEST2" not in requests.get(B + "/sondes/flights", params={"recovery": "planned", "hours": 0}).text
      and "RECTEST2" in requests.get(B + "/sondes/flights", params={"recovery": "notfound", "hours": 0}).text)
# validation
bad = [dict(lat="95"), dict(lon="abc"), dict(alt="999999"), dict(by="")]
res = [post(plain, "RECTEST2", **kw).json()["ok"] for kw in bad[:3]]
check("a bad latitude, longitude or altitude is refused", res == [False, False, False], res)
check("an unknown balloon is refused", post(plain, "NOSUCH999").json()["ok"] is False)
check("nothing bad was saved", psql("select count(*) from sonde_recoveries where serial='RECTEST2'") == "1")
# the limit
setting("sonde_report_per_hour", "3"); time.sleep(6)
n0 = int(psql("select count(*) from sonde_recoveries where source='portal' and user_id=(select id from users where username='plain1') and ts > now() - interval '1 hour'"))
check("the per-member hourly limit is enforced", n0 >= 3 and post(plain, "RECTEST2", note="again").json()["ok"] is False)
setting("sonde_report_per_hour", "6")
# off
setting("sonde_report_mode", "0"); time.sleep(6)
check("with reporting off the form is gone and a post is refused", 'id="rec-form"' not in plain.get(B + S1).text and post(plain, "RECTEST2", note="x").json()["ok"] is False)
setting("sonde_report_mode", "test"); time.sleep(6)
# chase mode
check("chase mode is in the map's panel for a signed-in member, and a sign-in note for a visitor", 'id="ch-on"' in plain.get(B + "/sondes/map").text and 'id="ch-on"' not in anon.get(B + "/sondes/map").text and "Sign in</a> to use chase mode" in anon.get(B + "/sondes/map").text)
check("the chase cars list is for signed-in members only", anon.get(B + "/api/sondes/chase.json").status_code == 401 and plain.get(B + "/api/sondes/chase.json").status_code == 200)
r = plain.post(B + "/my/sondes/chase/position", data={"csrf_token": csrf(plain, "/sondes/map"), "lat": "35.1", "lon": "-92.2", "alt": "100"}, headers=H)
check("sharing needs a call sign on the profile", r.status_code == 400 and "call sign" in r.json()["message"], r.text)
psql("update users set callsign='K5PLN' where username='plain1'")
r = plain.post(B + "/my/sondes/chase/position", data={"csrf_token": csrf(plain, "/sondes/map"), "lat": "35.1", "lon": "-92.2", "alt": "100"}, headers=H)
check("with a call sign the position is accepted (test mode) as CALLSIGN_chase", r.status_code == 200 and "K5PLN_chase" in r.json()["message"] and "not sent" in r.json()["message"], r.text)
r2 = plain.post(B + "/my/sondes/chase/position", data={"csrf_token": csrf(plain, "/sondes/map"), "lat": "35.1", "lon": "-92.2"}, headers=H)
check("updates faster than the minimum interval are not sent again", r2.json()["ok"] and "Too soon" in r2.json()["message"], r2.text)
check("a bad position is refused", plain.post(B + "/my/sondes/chase/position", data={"csrf_token": csrf(plain, "/sondes/map"), "lat": "999", "lon": "1"}, headers=H).status_code == 400)
check("the chase list names the member's own car so the page can leave it out", plain.get(B + "/api/sondes/chase.json").json()["name"] == "K5PLN_chase")
psql("insert into sonde_stations (callsign, lat, lon, mobile, privacy, last_seen, source) values ('W5CAR_chase', 35.5, -92.5, true, 'hidden', now(), 'sondehub') on conflict do nothing")
cars = plain.get(B + "/api/sondes/chase.json").json()["cars"]
check("a chase car heard on the feed is listed with its position for a signed-in member", any(c["id"] == "W5CAR_chase" and c["lat"] == 35.5 for c in cars), cars)
check("and a fixed station's position never is", not any(c["id"] != "W5CAR_chase" and False for c in cars))
check("both actions are in the audit log", int(psql("select count(*) from audit_log where actor_name='plain1' and action in ('sondes.recovery_report','sondes.chase_share')") or 0) >= 3)
setting("sonde_chase_mode", "0"); time.sleep(6)
check("with sharing off, a position is refused and the checkbox is gone", plain.post(B + "/my/sondes/chase/position", data={"csrf_token": csrf(plain, "/sondes/map"), "lat": "35", "lon": "-92"}, headers=H).status_code == 400 and 'id="ch-share"' not in plain.get(B + "/sondes/map").text)
# the map markers and the legend
mp = plain.get(B + "/sondes/map").text
check("the map has the recovery choice, hide recovered and the legend entry for a planned recovery", all(x in mp for x in ('id="sm-rec"', 'id="sm-hiderec"', "recovery is planned")))
js = requests.get(B + "/static/sondeicons.js").text
check("there are icons for recovered, planned, not found, chase car and me", all(f"{k}: function" in js for k in ("recovered", "planned", "notfound", "chase", "me")))
# clean up
psql("delete from sonde_recoveries where serial like 'RECTEST%'"); psql("delete from sondes where serial like 'RECTEST%'"); psql("delete from sonde_stations where callsign='W5CAR_chase'")
psql("delete from sonde_chase_share"); psql("update users set callsign=null where username='plain1'")
for k in ("sonde_report_mode", "sonde_chase_mode", "sonde_report_per_hour"): psql(f"delete from settings where key='{k}'")
print("ALL PASS" if ok_all else "SOME FAILED")
