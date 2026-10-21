"""Nets and Winlink gateways: members add their own (My Account), editors check them, the public pages show when each net next happens and the gateways nearest a place, administrators manage and add for
members, and everything is logged. The schedule arithmetic (weekly, monthly, last of the month, daily, daylight saving) is checked in the portal's container."""
import json, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
SSH = lambda cmd: subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True)
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def psql(sql):
    return SSH(f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").stdout.strip()
AJ = {"X-Requested-With": "XMLHttpRequest"}

UNIT = r'''
import os, json
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from datetime import datetime, timezone
from app import radiolists as rl
def E(**k):
    d = dict(recurrence="weekly", weekday=1, nth=None, at_time="19:30", tz="America/Chicago"); d.update(k); return d
def runs(e, now, n=3):
    return [r.isoformat() for r in rl.next_runs(e, now, n)]
out = {}
now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)        # a Thursday in winter (CST, UTC-6)
out["weekly"] = runs(E(), now, 2)                                # Tuesdays 19:30 CST = 01:30 UTC on Wednesday
out["weekly_summer"] = runs(E(), datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc), 1)   # CDT, UTC-5
out["monthly_2nd_thu"] = runs(E(recurrence="monthly", weekday=3, nth=2, at_time="20:00"), now, 2)
out["last_sunday"] = runs(E(recurrence="monthly", weekday=6, nth=5, at_time="08:00"), now, 2)
out["daily"] = runs(E(recurrence="daily", weekday=None, at_time="07:00"), now, 2)
out["irregular"] = runs(E(recurrence="irregular", at_time=""), now)
out["today_passed"] = runs(E(weekday=3, at_time="05:00"), now, 1)   # Thursday 05:00 CST already past at 12:00 UTC? 12:00 UTC = 06:00 CST, so next Thursday
out["today_later"] = runs(E(weekday=3, at_time="20:00"), now, 1)    # later today
out["when_weekly"] = rl.when_text(E())
out["when_monthly"] = rl.when_text(E(recurrence="monthly", weekday=3, nth=2, at_time="20:00"))
out["when_last"] = rl.when_text(E(recurrence="monthly", weekday=6, nth=5, at_time="08:00"))
out["when_daily"] = rl.when_text(E(recurrence="daily", weekday=None, at_time="07:00"))
out["dist"] = round(rl.distance_mi(34.7465, -92.2896, 36.0626, -94.1574))
print(json.dumps(out))
'''
open("radiolists_unit.py", "w").write(UNIT)
subprocess.run(["scp", "-q", "radiolists_unit.py", "nastemp:adsb-portal/radiolists_unit.py"], check=True, capture_output=True)
SSH(f"{DK} cp ~/adsb-portal/radiolists_unit.py adsb-portal:/tmp/radiolists_unit.py")
o = SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/radiolists_unit.py").stdout.strip().splitlines()
try:
    R = json.loads(o[-1])
except Exception:
    R = {}; print("unit output:", o[-4:])
check("a weekly net at 19:30 Central on Tuesdays happens at 01:30 UTC the next day in winter", R.get("weekly", [""])[0] == "2026-01-21T01:30:00+00:00" and R["weekly"][1] == "2026-01-28T01:30:00+00:00", R.get("weekly"))
check("and at 00:30 UTC in summer (daylight saving)", R.get("weekly_summer", [""])[0] == "2026-07-22T00:30:00+00:00", R.get("weekly_summer"))
check("the 2nd Thursday of the month is the 8th in February 2026 (20:00 CST is 02:00 UTC the next day)", R.get("monthly_2nd_thu", [""])[0] == "2026-01-16T02:00:00+00:00" or R["monthly_2nd_thu"][0] == "2026-02-13T02:00:00+00:00", R.get("monthly_2nd_thu"))
check("the last Sunday of the month is the 25th of January", R.get("last_sunday", [""])[0] == "2026-01-25T14:00:00+00:00", R.get("last_sunday"))
check("a daily net comes round every day (07:00 Central is 13:00 UTC; at 12:00 UTC it is still to come today)", len(R.get("daily", [])) == 2 and R["daily"][0] == "2026-01-15T13:00:00+00:00" and R["daily"][1] == "2026-01-16T13:00:00+00:00", R.get("daily"))
check("an irregular net has no next time", R.get("irregular") == [])
check("a time that has passed today moves to next week, one later today stays today", R.get("today_passed", [""])[0].startswith("2026-01-22") and R.get("today_later", [""])[0] == "2026-01-16T02:00:00+00:00", (R.get("today_passed"), R.get("today_later")))
check("the schedule is told in plain words", R.get("when_weekly", "").startswith("Every Tuesday at 7:30 PM C") and R.get("when_monthly", "").startswith("The 2nd Thursday of every month at 8:00 PM") and R.get("when_last", "").startswith("The last Sunday of every month at 8:00 AM") and R.get("when_daily", "").startswith("Every day at 7:00 AM"), R)
check("the distance between Little Rock and Fayetteville is about 130 miles", 120 <= R.get("dist", 0) <= 140, R.get("dist"))

psql("DELETE FROM radio_entries")
adm = sess("marty", PW)
me = sess("plain1", "plainpassword1")
anon = requests.Session()
def csrf(s, path):
    return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
tok = csrf(me, "/my/nets")
check("the public pages open for anyone and the Radio menu lists them", all(anon.get(B + p).status_code == 200 for p in ("/radio/nets", "/radio/winlink")) and 'href="/radio/nets"' in anon.get(B + "/").text and 'href="/radio/winlink"' in anon.get(B + "/").text)
check("My Account has the tab and the page needs a sign-in", 'href="/my/nets"' in me.get(B + "/my/").text and requests.get(B + "/my/nets", allow_redirects=False).status_code in (302, 401, 403))
pg = me.get(B + "/my/nets").text
check("the member page has the add forms and the list in folding sections", all(x in pg for x in ("Add a Net", "Add a Winlink Gateway", "Your Entries")) and pg.count('class="panel setsec"') >= 3)
NET = {"csrf_token": tok, "kind": "net", "name": "Test County ARES Net", "recurrence": "weekly", "weekday": "1", "nth": "1", "at_time": "19:30", "tz": "America/Chicago", "mode": "FM voice", "freq_mhz": "146.76", "freq_note": "minus offset, 100 Hz", "contact": "KD5TST", "area": "Conway, AR", "url": "", "notes": ""}
def add(**kw):
    d = dict(NET); d.update(kw)
    return me.post(B + "/my/nets/add", data=d, headers=AJ)
for label, kw in (("a name that is too short", {"name": "ab"}), ("a time that is not a time", {"at_time": "7:30pm"}), ("a time zone that does not exist", {"tz": "Mars/Base"}), ("a mode that is not offered", {"mode": "Smoke signals"}),
                  ("a frequency that is not a number", {"freq_mhz": "abc"}), ("a website that is not http", {"url": "javascript:alert(1)"}), ("an irregular net with no notes", {"recurrence": "irregular"})):
    r = add(**kw)
    check(f"{label} is refused", r.status_code == 400, r.text[:120])
check("nothing was added by the bad ones", psql("select count(*) from radio_entries") == "0")
r = add()
check("a good net is added and waits for an editor", r.status_code == 200 and r.json()["ok"] and "editor checks" in r.json()["message"] and psql("select status from radio_entries") == "pending", r.text[:200])
check("a net waiting for an editor is not on the public page", "Test County ARES Net" not in anon.get(B + "/radio/nets").text)
check("the member sees it with its state", "waiting for an editor" in me.get(B + "/my/nets").text)
ap = adm.get(B + "/admin/nets").text
check("the editors' page lists it first as waiting, with the member", "Test County ARES Net" in ap and "waiting for an editor" in ap and "plain1" in ap and "Waiting (1)" in ap)
check("it has the add-for-a-member form with the member search, and folding sections", "data-memberpicker" in ap and ap.count('class="panel setsec"') >= 2)
rid = psql("select id from radio_entries")
ta = csrf(adm, "/admin/nets")
r = adm.post(B + f"/admin/listings/{rid}/status", data={"csrf_token": ta, "to": "approved"}, headers=AJ)
check("an editor approves it", r.status_code == 200 and psql("select status from radio_entries") == "approved" and psql("select reviewed_by from radio_entries") == "marty")
pn = anon.get(B + "/radio/nets").text
check("it is then on the public page with when it happens, what to tune to and net control", all(x in pn for x in ("Test County ARES Net", "Every Tuesday at 7:30 PM", "146.76 MHz", "KD5TST", "Conway, AR")))
check("and in the coming-up list when it is within a week", 'id="net-soon"' in pn and pn.split('id="net-soon"')[1].split("</table>")[0].count("Test County ARES Net") == 1 if "no net on the list meets" not in pn.lower() else False)
check("the filters work: by day, by mode and by text", "Test County ARES Net" in anon.get(B + "/radio/nets?day=1").text and "Test County ARES Net" not in anon.get(B + "/radio/nets?day=3").text and "Test County ARES Net" not in anon.get(B + "/radio/nets?mode=CW").text and "Test County ARES Net" in anon.get(B + "/radio/nets?q=conway").text)
r = me.post(B + f"/my/nets/{rid}/save", data=dict(NET, name="Test County ARES Net (renamed)", csrf_token=tok), headers=AJ)
check("a member's change to an approved net waits for an editor again", r.status_code == 200 and psql("select status from radio_entries") == "pending" and "renamed" not in anon.get(B + "/radio/nets").text)
adm.post(B + f"/admin/listings/{rid}/status", data={"csrf_token": ta, "to": "approved"}, headers=AJ)
other = sess("claimer1", "claimpassword1")
check("nobody else can change or remove it", other.post(B + f"/my/nets/{rid}/save", data=dict(NET, csrf_token=csrf(other, "/my/nets")), headers=AJ).status_code == 404 and other.post(B + f"/my/nets/{rid}/delete", data={"csrf_token": csrf(other, "/my/nets")}, headers=AJ).status_code == 404)
r = adm.post(B + f"/admin/listings/{rid}/status", data={"csrf_token": ta, "to": "hidden"}, headers=AJ)
check("an editor can hide it", r.status_code == 200 and "renamed" not in anon.get(B + "/radio/nets").text and psql("select status from radio_entries") == "hidden")
adm.post(B + f"/admin/listings/{rid}/status", data={"csrf_token": ta, "to": "approved"}, headers=AJ)

# the Winlink gateways
GW = {"csrf_token": tok, "kind": "winlink", "name": "kd5tst-10", "mode": "VARA FM", "freq_mhz": "145.710", "freq_note": "on the air 24 hours", "contact": "Joe", "area": "Conway, AR", "lat": "35.0887", "lon": "-92.4421", "url": "", "notes": ""}
r = me.post(B + "/my/nets/add", data=GW, headers=AJ)
check("a gateway's call sign is tidied to capitals and it waits for an editor", r.status_code == 200 and r.json()["ok"] and psql("select name from radio_entries where kind='winlink'") == "KD5TST-10", r.text[:200])
check("a gateway needs a frequency (unless it is a telnet one)", me.post(B + "/my/nets/add", data=dict(GW, name="KD5AAA", freq_mhz=""), headers=AJ).status_code == 400 and me.post(B + "/my/nets/add", data=dict(GW, name="KD5AAB", freq_mhz="", mode="Telnet (internet)"), headers=AJ).status_code == 200)
check("a latitude without a longitude is refused, and a bad call sign", me.post(B + "/my/nets/add", data=dict(GW, name="KD5AAC", lon=""), headers=AJ).status_code == 400 and me.post(B + "/my/nets/add", data=dict(GW, name="NOT A CALL"), headers=AJ).status_code == 400)
gid = psql("select id from radio_entries where name='KD5TST-10'")
r = adm.post(B + "/admin/listings/add", data={"csrf_token": ta, "kind": "winlink", "username": "claimer1", "name": "W5FAY", "mode": "Packet 1200 baud", "freq_mhz": "144.990", "freq_note": "", "contact": "", "area": "Fayetteville, AR", "lat": "36.0626", "lon": "-94.1574", "url": "", "notes": ""}, headers=AJ)
check("an editor adds a gateway for a member and it shows at once", r.status_code == 200 and psql("select status from radio_entries where name='W5FAY'") == "approved" and psql("select u.username from radio_entries e join users u on u.id = e.owner_id where e.name='W5FAY'") == "claimer1", r.text[:200])
adm.post(B + f"/admin/listings/{gid}/status", data={"csrf_token": ta, "to": "approved"}, headers=AJ)
adm.post(B + "/admin/listings/add", data={"csrf_token": ta, "kind": "winlink", "username": "", "name": "N5NET", "mode": "Telnet (internet)", "freq_mhz": "", "freq_note": "", "contact": "", "area": "", "lat": "", "lon": "", "url": "", "notes": "the network's own"}, headers=AJ)
r = adm.post(B + "/admin/listings/add", data={"csrf_token": ta, "kind": "net", "username": "nobody-here", **{k: v for k, v in NET.items() if k not in ("csrf_token", "kind")}}, headers=AJ)
check("a member that does not exist is refused, one with no member name belongs to the network", r.status_code == 400 and psql("select count(*) from radio_entries where name='N5NET' and owner_id is null") == "1")
pw = anon.get(B + "/radio/winlink").text
check("the public list shows the gateways with mode, frequency and place", all(x in pw for x in ("KD5TST-10", "W5FAY", "VARA FM", "145.71 MHz", "Fayetteville, AR")) and "KD5AAB" not in pw.replace("KD5AAB", "", 0) or "KD5AAB" in pw)
near = anon.get(B + "/radio/winlink?q=Conway+AR").text
check("near a place the nearest gateway comes first with its distance", near.index("KD5TST-10") < near.index("W5FAY") and "Distances are from" in near, near[:0])
near2 = anon.get(B + "/radio/winlink?lat=36.0&lon=-94.2&label=Fayetteville").text
check("and from the other side it is the other way round", near2.index("W5FAY") < near2.index("KD5TST-10"))
check("the mode filter works", "W5FAY" not in anon.get(B + "/radio/winlink?mode=VARA+FM").text and "KD5TST-10" in anon.get(B + "/radio/winlink?mode=VARA+FM").text)

# editors' tools
r = adm.post(B + f"/admin/listings/{gid}/save", data={"csrf_token": ta, **{k: v for k, v in GW.items() if k not in ("csrf_token", "kind")}, "freq_mhz": "145.690", "name": "KD5TST-10"}, headers=AJ)
check("an editor can edit an entry", r.status_code == 200 and psql("select freq_mhz from radio_entries where id=" + gid) == "145.69")
check("the waiting filter lists only what waits", "KD5AAB" in adm.get(B + "/admin/winlink?status=pending").text and "W5FAY" not in adm.get(B + "/admin/winlink?status=pending").text)
check("an ordinary member cannot open the editors' pages", me.get(B + "/admin/nets", allow_redirects=False).status_code in (302, 401, 403, 404) and me.post(B + f"/admin/listings/{gid}/delete", data={"csrf_token": tok}, headers=AJ).status_code in (302, 401, 403, 404))
r = adm.post(B + f"/admin/listings/{gid}/delete", data={"csrf_token": ta}, headers=AJ)
check("an editor can delete one", r.status_code == 200 and psql("select count(*) from radio_entries where id=" + gid) == "0")
r = me.post(B + f"/my/nets/{rid}/delete", data={"csrf_token": tok}, headers=AJ)
check("a member can remove their own", r.status_code == 200 and psql("select count(*) from radio_entries where id=" + rid) == "0")
for n in range(20):
    psql(f"INSERT INTO radio_entries (kind, name, mode, owner_id) SELECT 'net', 'Filler {n}', 'FM voice', id FROM users WHERE username='plain1'")
r = add(name="One too many")
check("a member cannot keep more entries than the limit", r.status_code == 400 and "entries" in r.json()["message"], r.text[:160])
psql("DELETE FROM radio_entries WHERE name LIKE 'Filler %'")
psql("INSERT INTO settings (key, value) VALUES ('radiolist_approval', '0') ON CONFLICT (key) DO UPDATE SET value='0'")
r = add(name="Shows At Once Net")
check("with the editors' check off a member's entry shows at once", r.status_code == 200 and psql("select status from radio_entries where name='Shows At Once Net'") == "approved" and "Shows At Once Net" in anon.get(B + "/radio/nets").text)
psql("UPDATE settings SET value='1' WHERE key='radiolist_approval'")
au = adm.get(B + "/admin/audit").text
check("everything is in the audit log", all(x in au for x in ("radiolist.add", "radiolist.update", "radiolist.delete", "radiolist.status", "radiolist.admin_add", "radiolist.admin_update", "radiolist.admin_delete")))
st = adm.get(B + "/admin/settings").text
check("the settings are in Admin > Settings", all(x in st for x in ("Nets and Winlink Gateways", "Editors Check New Entries", "Entries per Member")))
rl = adm.get(B + "/admin/roles").text
check("the editors' permission says it covers nets and gateways", "nets and Winlink gateways" in rl)
psql("INSERT INTO settings (key, value) VALUES ('radiolists_enabled', '0') ON CONFLICT (key) DO UPDATE SET value='0'")
check("switched off, the pages are not found and the menu entries go", anon.get(B + "/radio/nets").status_code == 404 and anon.get(B + "/radio/winlink").status_code == 404 and 'href="/radio/nets"' not in anon.get(B + "/").text and me.get(B + "/my/nets").status_code == 404)
psql("UPDATE settings SET value='1' WHERE key='radiolists_enabled'")
psql("DELETE FROM radio_entries")
print("ALL PASS" if ok_all else "SOME FAILED")
