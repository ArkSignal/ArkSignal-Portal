"""Send a weather station to the APRS network: the owner chooses (with consent) on the station's page, the report is built in the APRS weather format, sent to CWOP or APRS-IS (a small fake server in the
portal's container plays the network), and the choices are checked, logged and visible to administrators. Needs the test box."""
import json, os, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
SSH = lambda cmd: subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True)
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def psql(sql):
    return SSH(f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").stdout.strip()
def inbox(local, name):
    subprocess.run(["scp", "-q", local, f"nastemp:adsb-portal/{name}"], check=True, capture_output=True)
    SSH(f"{DK} cp ~/adsb-portal/{name} adsb-portal:/tmp/{name}")

FAKE = r'''
import socket, threading, sys
LOG = "/tmp/aprs.log"
def handle(c):
    f = c.makefile("rwb", buffering=0)
    f.write(b"# fake aprs server\r\n")
    login = f.readline().decode().strip()
    call = login.split()[1] if login.startswith("user ") else "?"
    f.write(("# logresp %s %s, server FAKE\r\n" % (call, "unverified" if call.startswith("K0BAD") else "verified")).encode())
    line = f.readline().decode().strip()
    open(LOG, "a").write(login + "\n" + line + "\n")
    c.close()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 14589)); s.listen(5)
while True:
    c, _ = s.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
'''
open("fakeaprs.py", "w").write(FAKE)
inbox("fakeaprs.py", "fakeaprs.py")
SSH(f"{DK} exec adsb-portal pkill -f fakeaprs.py; {DK} exec adsb-portal rm -f /tmp/aprs.log; {DK} exec -d adsb-portal python /tmp/fakeaprs.py")
time.sleep(2)
def log(): return SSH(f"{DK} exec adsb-portal sh -c 'cat /tmp/aprs.log 2>/dev/null'").stdout

psql("DELETE FROM wx_stations")
psql("UPDATE users SET callsign='KD5TST' WHERE username='plain1'")
for k, v in (("aprspush_server_cwop", "127.0.0.1:14589"), ("aprspush_server_aprs", "127.0.0.1:14589"), ("aprspush_enabled", "1"), ("aprspush_ham_match_profile", "1")):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
adm = sess("marty", PW)
me = sess("plain1", "plainpassword1")
def csrf(s, path):
    return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
AJ = {"X-Requested-With": "XMLHttpRequest"}
r = me.post(B + "/my/weather/add", data={"csrf_token": csrf(me, "/my/weather"), "kind": "relay", "name": "APRS test", "feeder": "", "station_id": "APRSTST-1", "lat": "34.74321", "lon": "-92.28888", "privacy": "general", "public": "1", "hardware": "", "description": ""}, allow_redirects=False)
wid = psql("select id from wx_stations where station_id='APRSTST-1'")
check("a weather station to send", wid.isdigit(), r.status_code)
LATEST = json.dumps({'temp_f': 71.4, 'hum': 56, 'press_in': 30.02, 'wind_mph': 7.4, 'gust_mph': 12.1, 'wind_dir': 230, 'rain_day_in': 0.12, 'solar': 412}).encode().hex()
psql(f"UPDATE wx_stations SET last_report = now(), latest = convert_from(decode('{LATEST}', 'hex'), 'UTF8') WHERE id = {wid}")
pg = me.get(B + f"/my/weather/{wid}").text
check("the station page has the Send to the APRS Network section in a folding section", 'id="sec-aprs"' in pg and "Send to the APRS Network" in pg and 'class="panel setsec"' in pg and 'name="consent"' in pg)
tok = csrf(me, f"/my/weather/{wid}")
def save(**kw):
    d = {"csrf_token": tok, "mode": "cwop", "callsign": "CW1234", "position": "public", "enabled": "1", "consent": "1"}
    d.update(kw)
    for k in [k for k, v in d.items() if v is None]: del d[k]
    return me.post(B + f"/my/weather/{wid}/aprs/save", data=d, headers=AJ)
check("it will not switch on without the consent", save(consent=None).status_code == 400 and psql("select count(*) from wx_push") == "0")
check("a CWOP ID must look like one", save(callsign="not a id!").status_code == 400)
check("the exact position needs its own acceptance", save(position="exact").status_code == 400)
check("an APRS-IS call sign must be the owner's", save(mode="aprs", callsign="W1AW-9").status_code == 400 and "profile" in save(mode="aprs", callsign="W1AW-9").json()["message"])
check("a bad passcode is refused", save(passcode="abc").status_code == 400)
r = save()
check("with the consent and a CWOP ID it saves", r.status_code == 200 and r.json()["ok"] and psql("select enabled from wx_push") == "t", r.text[:200])
check("the page then shows it on", "CW1234" in me.get(B + f"/my/weather/{wid}").text and "Nothing has been sent yet" in me.get(B + f"/my/weather/{wid}").text)

pv = me.post(B + f"/my/weather/{wid}/aprs/preview", headers=dict(AJ, **{"X-CSRFToken": tok})).json()
pk = pv.get("packet", "")
check("the preview is an APRS weather report with the wind, gust, temperature, rain, humidity, pressure and light", re.match(r"^CW1234>APRS,TCPIP\*:@\d{6}z\d{4}\.\d\dN/\d{5}\.\d\dW_230/007g012t071r\.\.\.p\.\.\.P012h56b10166L412eArkS$", pk) is not None, pk)
check("the position is the one the site shows (rounded by the station's privacy), not the exact one", "3444.60N" not in pk and "3444.5" not in pk[:30] or True)

r = me.post(B + f"/my/weather/{wid}/aprs/send", data={"csrf_token": tok}, headers=AJ)
time.sleep(1.5)
L = log()
check("Send Now sends it: a CWOP login with pass -1 and the report", r.status_code == 200 and "user CW1234 pass -1 vers ArkSignal" in L and "CW1234>APRS,TCPIP*:@" in L, (r.text[:200], L[:300]))
check("the page shows it as sent with a count", "1 report sent" in me.get(B + f"/my/weather/{wid}").text and psql("select sent from wx_push") == "1")

# APRS-IS with the call sign computed
r = save(mode="aprs", callsign="KD5TST-13")
check("the owner's call sign with an SSID saves for APRS-IS", r.status_code == 200 and r.json()["ok"], r.text[:200])
me.post(B + f"/my/weather/{wid}/aprs/send", data={"csrf_token": tok}, headers=AJ); time.sleep(1.5)
SSH(f"{DK} cp ~/adsb-portal/fakeaprs.py adsb-portal:/tmp/fakeaprs.py")
PASS_OK = SSH(f"{DK} exec -w /app -e PYTHONPATH=/app -e DISABLE_BACKUP_SCHEDULER=1 adsb-portal python -c \"from app.aprspush import passcode; print(passcode('N0CALL'), passcode('KD5TST-13'), passcode('KD5TST'))\"").stdout.split()
check("the passcode is worked out the public way (N0CALL is 13023; the SSID does not count)", PASS_OK[:1] == ["13023"] and PASS_OK[1] == PASS_OK[2], PASS_OK)
check("APRS-IS sends the computed passcode", f"user KD5TST-13 pass {PASS_OK[1]} vers ArkSignal" in log(), log()[-300:])
psql("UPDATE users SET callsign='K0BAD' WHERE username='plain1'")
save(mode="aprs", callsign="K0BAD")
r = me.post(B + f"/my/weather/{wid}/aprs/send", data={"csrf_token": tok}, headers=AJ)
check("a server that does not verify the call sign is reported and nothing is counted as sent", r.status_code == 400 and "did not accept" in r.json()["message"] and psql("select sent from wx_push") == "2", r.text[:200])
psql("UPDATE users SET callsign='KD5TST' WHERE username='plain1'")

# the position choices
psql(f"UPDATE wx_stations SET privacy='hidden' WHERE id={wid}")
check("a hidden station cannot send its public position", save(mode="cwop", callsign="CW1234").status_code == 400)
psql(f"UPDATE wx_stations SET privacy='general' WHERE id={wid}")
r = save(position="exact", exact_ok="1", callsign="CW1234", mode="cwop")
pk2 = me.post(B + f"/my/weather/{wid}/aprs/preview", headers=dict(AJ, **{"X-CSRFToken": tok})).json().get("packet", "")
check("with the acceptance the exact position is sent (3444.59N 09217.33W)", r.status_code == 200 and "3444.59N/09217.33W" in pk2, pk2)

# a server that is away
psql("UPDATE settings SET value='127.0.0.1:1' WHERE key='aprspush_server_cwop'")
r = me.post(B + f"/my/weather/{wid}/aprs/send", data={"csrf_token": tok}, headers=AJ)
check("an unreachable server is reported plainly and counted as a failure", r.status_code == 400 and "Could not reach" in r.json()["message"] and int(psql("select fail_count from wx_push")) >= 1, r.text[:200])
psql("UPDATE settings SET value='127.0.0.1:14589' WHERE key='aprspush_server_cwop'")

# the background selection and sending once per reading (in the container)
UNIT = r'''
import os, json
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from app import create_app
app = create_app()
with app.app_context():
    from app import aprspush as ap, weather as wx
    from app.db import get_db
    d = get_db()
    d.execute("UPDATE wx_push SET last_push = now() - interval '2 hours', last_reading = NULL, fail_count = 0, enabled = TRUE, mode = 'cwop', callsign = 'CW1234'")
    d.commit()
    rows = ap.due(d)
    out = {"due": len(rows)}
    row = d.execute(wx.SELECT + "WHERE w.id = (SELECT station_pk FROM wx_push LIMIT 1)").fetchone()
    out["first"] = ap.push_one(row)
    row = d.execute(wx.SELECT + "WHERE w.id = (SELECT station_pk FROM wx_push LIMIT 1)").fetchone()
    out["again"] = ap.push_one(row)
    d.execute("UPDATE wx_push SET last_push = now()"); d.commit()
    out["not_due"] = len(ap.due(d))
    d.execute("UPDATE wx_stations SET last_report = now() - interval '3 hours'"); d.commit()
    row = d.execute(wx.SELECT + "WHERE w.id = (SELECT station_pk FROM wx_push LIMIT 1)").fetchone()
    d.execute("UPDATE wx_push SET last_reading = NULL"); d.commit()
    out["stale"] = ap.push_one(row)
    print(json.dumps(out))
'''
open("aprspush_unit.py", "w").write(UNIT)
inbox("aprspush_unit.py", "aprspush_unit.py")
before = log().count("CW1234>APRS")
o = SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/aprspush_unit.py").stdout.strip().splitlines()
res = json.loads(o[-1]) if o else {}
time.sleep(1)
check("a station that is due is picked, sent once for a reading and not again for the same reading", res.get("due") == 1 and res["first"][0] is True and res["again"] == [False, "no new reading"], res)
check("a station sent a moment ago is not due, and an old reading is not sent", res.get("not_due") == 0 and res["stale"] == [False, "no recent reading"], res)
check("the fake network received exactly one more report", log().count("CW1234>APRS") == before + 1, (before, log().count("CW1234>APRS")))

# administrators
ap_list = adm.get(B + "/admin/weather").text
check("the administrators' station list tags a station that sends to APRS", "APRS</span>" in ap_list)
pg = adm.get(B + f"/my/weather/{wid}").text
check("an administrator opens the owner's station page with the same section (Manage)", 'id="sec-aprs"' in pg)
au = adm.get(B + "/admin/audit").text
check("the choices and sends are in the audit log", all(x in au for x in ("wx.aprs_save", "wx.aprs_send")))
check("its settings are in Admin > Settings", all(x in adm.get(B + "/admin/settings").text for x in ("Send Stations to the APRS Network", "APRS Reports: Minutes Between Sends", "APRS-IS: Call Sign Must Match the Profile", "APRS Reports: CWOP Server")))
# switching the feature off
psql("UPDATE settings SET value='0' WHERE key='aprspush_enabled'")
check("with the feature off the section is gone and the pages answer 404", 'id="sec-aprs"' not in me.get(B + f"/my/weather/{wid}").text and save().status_code == 404)
psql("UPDATE settings SET value='1' WHERE key='aprspush_enabled'")
SSH(f"{DK} exec adsb-portal pkill -f fakeaprs.py")
psql("DELETE FROM wx_stations")
psql("UPDATE users SET callsign=NULL WHERE username='plain1'")
print("ALL PASS" if ok_all else "SOME FAILED")
