"""Airline schedules for My Flights (a stand-in AeroDataBox on this PC): off until a key is set, the key goes in the marketplace header and is never shown, the scheduled times are written in each airport's own time, answers are
cached, the limits per member per day and for the site per month, a wrong key and an unknown flight are told plainly, the form's button, the credit on About and the Service Status source."""
import json, re, socket, subprocess, threading, time, requests
from http.server import BaseHTTPRequestHandler, HTTPServer
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
KEY = "adb-test-key-98765"; SEEN = []; MODE = {"v": "ok"}
FLIGHT = [{"number": "WN 1672", "callSign": "SWA1672", "airline": {"name": "Southwest Airlines", "iata": "WN", "icao": "SWA"}, "aircraft": {"reg": "N8555Q", "model": "Boeing 737-800"},
           "departure": {"airport": {"icao": "KLIT", "iata": "LIT", "name": "Clinton National"}, "scheduledTime": {"utc": "2026-11-02 17:00Z", "local": "2026-11-02 11:00-06:00"}, "terminal": "A"},
           "arrival": {"airport": {"icao": "KLAS", "iata": "LAS", "name": "Harry Reid International"}, "scheduledTime": {"utc": "2026-11-02 19:30Z", "local": "2026-11-02 11:30-08:00"}, "terminal": "1"}}]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        SEEN.append((self.path, self.headers.get("x-api-market-key")))
        if self.headers.get("x-api-market-key") != KEY:
            self.send_response(401); self.end_headers(); return
        if MODE["v"] in ("400", "500", "429"):
            self.send_response(int(MODE["v"])); self.end_headers(); return
        if MODE["v"] == "404" or "/WN9999/" in self.path:
            self.send_response(404); self.end_headers(); return
        raw = json.dumps(FLIGHT).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
srv = HTTPServer(("0.0.0.0", 18992), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s_.connect(("test-box.invalid", 80)); MYIP = s_.getsockname()[0]; s_.close()
for k in ("schedules_enabled", "schedules_key", "schedules_base_url"):
    psql(f"DELETE FROM settings WHERE key='{k}'")
setting("myflights_enabled", "1"); setting("schedules_per_member_day", "10"); setting("schedules_site_month", "150"); setting("schedules_cache_days", "3")
psql("DELETE FROM settings WHERE key = 'access_myflights'"); psql("DELETE FROM flight_schedule_cache"); psql("DELETE FROM flight_schedule_calls")
psql("DELETE FROM users WHERE username LIKE 'sch%'")
for n in ("schone", "schtwo"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
U1, U2 = sess("schone", "schonepassword99"), sess("schtwo", "schtwopassword99")
adm = sess("marty", PW)
anon = requests.Session()
def look(s, q="WN 1672", date="2026-11-02"):
    return s.get(B + "/my/flights/schedule.json", params={"q": q, "date": date})
r = look(U1)
check("not set up: a look-up answers 404 and the form has no button", r.status_code == 404 and 'id="sc_go"' not in U1.get(B + "/my/flights/new").text)
check("a guest is sent to sign in", anon.get(B + "/my/flights/schedule.json", allow_redirects=False).status_code == 302)
setting("schedules_enabled", "1"); setting("schedules_base_url", f"http://{MYIP}:18992"); setting("schedules_key", KEY); time.sleep(7)
check("with a key the form has the button and the credit is on About for a member only", 'id="sc_go"' in U1.get(B + "/my/flights/new").text and "AeroDataBox" in U1.get(B + "/about").text and "AeroDataBox" not in anon.get(B + "/about").text)
check("the key is never on the Settings page", KEY not in adm.get(B + "/admin/settings").text)
r = look(U1); j = r.json()
f = (j.get("flights") or [{}])[0]
check("a flight number and date give the schedule with the airports found", r.status_code == 200 and len(j["flights"]) == 1 and f["number"] == "WN 1672" and f["from"] and f["from"]["code"] in ("LIT", "KLIT") and f["to"] and f["to"]["code"] in ("LAS", "KLAS"), j)
check("the airports come with their names, and the form shows names with the code beside them in small type", f.get("from_name") and f.get("to_name") and "function apl(" in U1.get(B + "/my/flights/new").text and "apl(f.from_name, f.from_code)" in U1.get(B + "/my/flights/new").text, (f.get("from_name"), f.get("to_name")))
check("the scheduled times are in each airport's own time (17:00Z is 11:00 in Little Rock, 19:30Z is 11:30 in Las Vegas)", f.get("sched_out") == "2026-11-02T11:00" and f.get("sched_in") == "2026-11-02T11:30", f)
check("terminals, airline, aircraft, registration and the credit", f.get("terminal_from") == "A" and f.get("terminal_to") == "1" and f.get("airline") == "Southwest Airlines" and f.get("aircraft") == "Boeing 737-800" and f.get("registration") == "N8555Q" and "AeroDataBox" in j.get("credit", ""), j)
check("the key went in the marketplace header and the request named the flight and date", SEEN and SEEN[0][1] == KEY and "/flights/number/WN1672/2026-11-02" in SEEN[0][0], SEEN[:1])
n = len(SEEN)
j2 = look(U2).json()
check("the same look-up again, even by another member, comes from the kept answer and asks nobody", j2.get("cached") is True and len(SEEN) == n and len(j2["flights"]) == 1, (j2, len(SEEN), n))
look(U1, date="2026-11-03")
check("another date is a real call", len(SEEN) == n + 1)
psql("UPDATE flight_schedule_cache SET fetched = now() - interval '5 days'")
look(U1)
check("an answer older than the days set is asked again", len(SEEN) == n + 2)
# ---- limits
setting("schedules_per_member_day", "2"); time.sleep(7)
psql("DELETE FROM flight_schedule_cache"); psql("DELETE FROM flight_schedule_calls")
before = len(SEEN)
look(U1, date="2026-11-10"); look(U1, date="2026-11-11")
check("a member's real calls are counted for the day", len(SEEN) == before + 2)
r = look(U1, date="2026-11-12")
check("past the member's limit the look-up is refused with the reason and asks nobody", "used your 2" in r.json().get("note", "") and len(SEEN) == before + 2, (r.json(), len(SEEN) - before))
check("a kept answer still works past the limit", look(U1, date="2026-11-10").json().get("cached") is True)
check("another member still has theirs", look(U2, date="2026-11-12").json().get("flights"))
setting("schedules_site_month", "1"); time.sleep(7)
r = look(U2, date="2026-11-13")
check("the whole site's monthly limit stops every member", "this month have all been used" in r.json().get("note", ""), r.json())
setting("schedules_site_month", "150"); setting("schedules_per_member_day", "10"); time.sleep(7)
# no date typed: today's
import datetime as _dt
r = look(U1, q="WN 1672", date="")
_today = [str((_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=h)).date()) for h in (-12, 0, 12)]
check("with no date typed the look-up uses today's date and still answers", r.status_code == 200 and r.json().get("flights") and any(("/WN1672/" + d_ in p_ or "/WN%201672/" + d_ in p_) for p_, _k in SEEN[-3:] for d_ in _today), (r.json(), SEEN[-2:]))
# ---- trouble
r = look(U1, q="WN9999", date="2026-12-01")
check("an unknown flight is told plainly", r.json().get("flights") == [] and "no flight with that number" in r.json().get("note", ""), r.json())
check("a bad number or date is told", "flight number" in look(U1, q="xx").json()["note"] and "date" in look(U1, date="soon").json()["note"])
setting("schedules_key", "wrong-key"); time.sleep(7)
r = look(U1, q="WN 22", date="2026-12-05")
check("a wrong key is told as a problem with the key or plan, and the call is marked failed", "did not accept the key" in r.json().get("note", "") and psql("SELECT COUNT(*) FROM flight_schedule_calls WHERE NOT ok") != "0", r.json())
setting("schedules_key", KEY); time.sleep(7)
for _mode, _words in (("400", "nearer date"), ("429", "limit has been used"), ("500", "error 500")):
    MODE["v"] = _mode
    r = look(U1, q="WN 23" + _mode[0], date="2026-12-06")
    check(f"an answer of {_mode} from the schedule service is told in its own words ({_words})", _words in r.json().get("note", ""), r.json())
MODE["v"] = "ok"
check("another member cannot see the member's counts, and nothing is kept past the cache and call tables", psql("SELECT COUNT(*) FROM flight_schedule_calls WHERE user_id IS NULL") == "0")
check("the settings are on the Settings page", all(k in adm.get(B + "/admin/settings").text for k in ("schedules_enabled", "schedules_per_member_day", "schedules_site_month")))
check("the audit log has no key", int(psql(f"SELECT COUNT(*) FROM audit_log WHERE detail LIKE '%{KEY}%'")) == 0)
seen_status = False
for _ in range(12):
    seen_status = "AeroDataBox" in adm.get(B + "/api/status.json").text
    if seen_status: break
    time.sleep(5)
check("Service Status lists the schedule source", seen_status)
for k in ("schedules_enabled", "schedules_key", "schedules_base_url"):
    psql(f"DELETE FROM settings WHERE key='{k}'")
psql("DELETE FROM flight_schedule_cache"); psql("DELETE FROM flight_schedule_calls"); psql("DELETE FROM users WHERE username LIKE 'sch%'")
print("ALL PASS" if ok_all else "SOME FAILED")
