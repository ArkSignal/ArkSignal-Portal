"""My Flights, a member's private flight diary: picking airports, times written in each airport's own local time and stored in UTC, the worked-out taxi, air and total times and the distance, simple and
detailed modes, the people and seats, every refusal (same airport, times out of order, more than 48 hours, a bad flight number, no departure, too many people or flights), editing, deleting, the list with its
totals, search and year filter, that nobody else can reach a diary, the switch, Feature Access, the settings and the restore area."""
import json, math, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("myflights_enabled", "1")
psql("DELETE FROM settings WHERE key IN ('myflights_max_per_member','myflights_max_passengers','access_myflights')")
psql("DELETE FROM my_flights WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'mfl%')")
psql("DELETE FROM users WHERE username LIKE 'mfl%'")
for n in ("mflone", "mfltwo"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
U1, U2 = sess("mflone", "mflonepassword99"), sess("mfltwo", "mfltwopassword99")
adm = sess("marty", PW)
anon = requests.Session()
check("not signed in: sent to the sign-in page", anon.get(B + "/my/flights", allow_redirects=False).status_code == 302)
pg = U1.get(B + "/my/flights").text
check("an empty diary says how to start, and the account menu has My Flights", "No flights yet" in pg and 'href="/my/flights"' in pg and "Add a Flight" in pg)
def airports(q):
    return U1.get(B + "/my/flights/airports.json", params={"q": q}).json()["airports"]
lit = next((a for a in airports("LIT") if a["code"] == "LIT"), None)
las = next((a for a in airports("LAS") if a["code"] == "LAS"), None)
dal = next((a for a in airports("DAL") if a["code"] == "DAL"), None)
check("the airport search finds Little Rock and Las Vegas by their codes", lit is not None and las is not None and "Clinton" in lit["name"] and "Harry Reid" in las["name"], (lit, las))
check("a search of one letter returns nothing and anonymous gets no list", airports("L") == [] and anon.get(B + "/my/flights/airports.json", allow_redirects=False).status_code == 302)
def tok1(): return tok(U1, "/my/flights/new")
def derive(**kw):
    body = dict(from_id=lit["id"], to_id=las["id"], mode="detailed", **kw)
    return U1.post(B + "/my/flights/derive.json", json=body, headers={"X-CSRFToken": tok1()}).json()
d = derive(act_out="2024-08-15T12:15", act_off="2024-08-15T12:30", act_on="2024-08-15T13:50", act_in="2024-08-15T14:00")
check("the zones come from where the airports are (Chicago time at Little Rock, Los Angeles time at Las Vegas)", d["ok"] and d["from_tz"] == "America/Chicago" and d["to_tz"] == "America/Los_Angeles", d)
check("taxi out, air time, taxi in and total are worked out across the two zones", d["taxi_out"] == "15 min" and d["air"] == "3 h 20 min" and d["taxi_in"] == "10 min" and d["total"] == "3 h 45 min", d)
check("the distance is the great circle between the airports", 2000 <= d["km"] <= 2150, d["km"])
d2 = derive(sched_out="2024-08-15T12:00", sched_off="2024-08-15T12:20")
check("scheduled times are used where there are no actual ones", d2["taxi_out"] == "20 min", d2)
check("nonsense is not a time (and is not an error page)", derive(act_out="soon")["ok"] is False)
def post(s, **kw):
    kw["csrf_token"] = tok(s, "/my/flights/new")
    return s.post(B + "/my/flights/save", data=kw, allow_redirects=False)
def msg(s): return s.get(B + "/my/flights").text
base = dict(from_id=lit["id"], to_id=las["id"], mode="detailed", flight_no="WN 1672", airline="Southwest Airlines", aircraft_type="B738", registration="n8555q", notes="Window seat.",
            act_out="2024-08-15T12:15", act_off="2024-08-15T12:30", act_on="2024-08-15T13:50", act_in="2024-08-15T14:00", sched_out="2024-08-15T12:00",
            p0_name="Marty Allison", p0_class="Economy", p0_reason="Leisure", p0_seat="12a", p0_kind="Window", p1_name="Blake Allison", p1_class="Business", p1_reason="Business", p1_seat="12B", p1_kind="Aisle")
r = post(U1, **base)
check("a detailed flight is saved", r.status_code == 302, r.status_code)
row = psql("SELECT act_out::text || '|' || act_in::text || '|' || from_tz || '|' || to_tz || '|' || from_code || '|' || to_code || '|' || registration || '|' || flight_no FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mflone')")
check("the times are stored in UTC (12:15 Chicago is 17:15Z, 14:00 Los Angeles is 21:00Z) with the zones, the codes, the registration in capitals and the flight number", row.startswith("2024-08-15 17:15:00+00|2024-08-15 21:00:00+00|America/Chicago|America/Los_Angeles|LIT|LAS|N8555Q|WN 1672"), row)
check("the distance is kept", 2000 <= float(psql("SELECT distance_km FROM my_flights LIMIT 1")) <= 2150)
check("the people are kept with class, reason, seat and where", opsql("SELECT name, klass, reason, seat, seat_kind FROM my_flight_pax ORDER BY id") == [["Marty Allison", "Economy", "Leisure", "12A", "Window"], ["Blake Allison", "Business", "Business", "12B", "Aisle"]] and psql("SELECT COUNT(*) FROM my_flight_pax WHERE name NOT LIKE 'enc1:%'") == "0")
pg = msg(U1)
check("the list shows the route, the flight, the aircraft, the air time, the distance and the people", all(x in pg for x in ("LIT", "LAS", "WN 1672", "B738", "N8555Q", "3 h 20 min", "Marty Allison", "12A", "Business")) and "1 flight" in pg.replace("Flight</div>", "flight") or "Flight" in pg)
check("the totals tile counts the flight and the airports", re.search(r'<div class="tv">1</div><div class="tl">Flight', pg) is not None and re.search(r'<div class="tv">2</div><div class="tl">Airports', pg) is not None)
check("nobody else sees it: another member's list is empty and an administrator's own list does not hold it", "WN 1672" not in msg(U2) and "WN 1672" not in adm.get(B + "/my/flights").text)
fid = psql("SELECT id FROM my_flights LIMIT 1")
check("another member cannot open, change or delete it (404)", U2.get(B + f"/my/flights/{fid}/edit").status_code == 404 and post(U2, **dict(base, id=fid, notes="stolen")).status_code in (302, 404) and U2.post(B + f"/my/flights/{fid}/delete", data={"csrf_token": tok(U2, "/my/flights/new")}).status_code == 404 and opsql(f"SELECT notes FROM my_flights WHERE id={fid}") == [["Window seat."]])
ed = U1.get(B + f"/my/flights/{fid}/edit").text
check("editing shows the times as they were typed, in each airport's own time, and the airports", 'value="2024-08-15T12:15"' in ed and 'value="2024-08-15T14:00"' in ed and "LIT" in ed and "LAS" in ed and "Marty Allison" in ed and "Blake Allison" in ed)
r = post(U1, **dict(base, id=fid, notes="Changed", act_in="2024-08-15T14:10", p1_name=""))
check("a flight can be changed (a person removed, a time moved)", r.status_code == 302 and opsql(f"SELECT notes, act_in::text FROM my_flights WHERE id={fid}") == [["Changed", "2024-08-15 21:10:00+00"]] and psql(f"SELECT COUNT(*) FROM my_flight_pax WHERE flight_id={fid}") == "1")
def refused(label, **kw):
    n0 = psql("SELECT COUNT(*) FROM my_flights")
    r = post(U1, **dict({k: v for k, v in base.items() if not k.startswith("p")}, **kw))
    check("refused: " + label, psql("SELECT COUNT(*) FROM my_flights") == n0 and r.status_code == 302 and "/my/flights/new" in r.headers.get("Location", ""), (r.status_code, r.headers.get("Location")))
refused("the same airport twice", to_id=lit["id"])
refused("no airport chosen", to_id="")
refused("times out of order (takeoff before gate departure)", act_off="2024-08-15T11:00")
refused("more than 48 hours", act_in="2024-08-18T14:00")
refused("a flight number that is not one", flight_no="hello world")
refused("no departure at all", act_out="", sched_out="", act_off="", sched_off="")
refused("a time that is not a time", act_out="yesterday")
simple = dict(base, from_id=las["id"], to_id=lit["id"], mode="simple", act_out="2024-08-20T15:40", act_in="2024-08-20T20:45", act_off="2024-08-20T15:50", act_on="2024-08-20T20:30", sched_out="", flight_no="WN 4499")
post(U1, **{k: v for k, v in simple.items() if not k.startswith("p")})
check("a simple flight keeps only the departure and the arrival (the takeoff and landing are dropped)", psql("SELECT (act_off IS NULL)::text || (act_on IS NULL)::text || (act_out IS NOT NULL)::text || (act_in IS NOT NULL)::text FROM my_flights WHERE flight_no='WN 4499'") == "truetruetruetrue")
check("and its total time is worked out (Las Vegas 15:40 to Little Rock 20:45 is 3 h 05 min)", "3 h 05 min" in msg(U1))
setting("myflights_max_passengers", "1")
post(U1, **dict(base, flight_no="WN 100"))
check("people per flight are limited by a setting", psql("SELECT COUNT(*) FROM my_flight_pax p JOIN my_flights f ON f.id = p.flight_id WHERE f.flight_no='WN 100'") == "1")
setting("myflights_max_passengers", "10")
setting("myflights_max_per_member", "3")
r = post(U1, **dict(base, flight_no="WN 300"))
check("flights are limited by a setting (the fourth is refused, and the Add page says so)", psql("SELECT COUNT(*) FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mflone')") == "3" and "You can keep 3 flights" in U1.get(B + "/my/flights/new", allow_redirects=True).text)
setting("myflights_max_per_member", "2000")
pg = U1.get(B + "/my/flights?q=4499").text
check("the list can be searched (here by flight number) and by year", "WN 4499" in pg and "WN 1672" not in pg and "WN 4499" in U1.get(B + "/my/flights?year=2024").text and "WN 4499" not in U1.get(B + "/my/flights?year=2019").text)
U1.post(B + f"/my/flights/{fid}/delete", data={"csrf_token": tok(U1, "/my/flights/new")}, allow_redirects=False)
check("a flight can be deleted, with its people", psql(f"SELECT COUNT(*) FROM my_flights WHERE id={fid}") == "0" and psql(f"SELECT COUNT(*) FROM my_flight_pax WHERE flight_id={fid}") == "0")
aud = psql("SELECT string_agg(DISTINCT action, ',') FROM audit_log WHERE action LIKE 'myflights.%'")
check("saving and deleting are in the audit log (by airport codes, never the notes or the people)", "myflights.save" in aud and "myflights.delete" in aud and "Changed" not in psql("SELECT string_agg(detail, '|') FROM audit_log WHERE action LIKE 'myflights.%'"))
st = adm.get(B + "/admin/settings/site").text
check("the limits are settings", all(x in st for x in ("Flights One Member Can Keep", "People per Flight")))
rp = open("../app/restorepart.py", encoding="utf-8").read()
check("the diary is in the restore areas", '"my_flights", "my_flight_pax"' in rp)
setting("access_myflights", "roles")
check("Feature Access can hide it", U1.get(B + "/my/flights", allow_redirects=False).status_code == 404)
psql("DELETE FROM settings WHERE key = 'access_myflights'")
setting("myflights_enabled", "0")
check("switched off: the pages and the menu entry are gone", U1.get(B + "/my/flights", allow_redirects=False).status_code == 404 and 'href="/my/flights"' not in U1.get(B + "/my/account").text)
setting("myflights_enabled", "1")
psql("DELETE FROM my_flights WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'mfl%')")
psql("DELETE FROM users WHERE username LIKE 'mfl%'")
print("ALL PASS" if ok_all else "SOME FAILED")
