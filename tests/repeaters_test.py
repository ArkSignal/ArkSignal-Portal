"""The repeater directory through the web: the public list, a repeater's page and the map data, a member submitting one (it waits for an editor), reporting a problem and having it applied,
a sysop claim and the claimed edit, an administrator adding a repeater for a member, the permissions on every admin and member action, Settings, the menu, the home map layer, the
Data Updates job and the in-container unit checks (repeaters_unit.py)."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout
def register(user, pw="rpuserpass11"):
    s = requests.Session()
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/register").text).group(1)
    s.post(B + "/my/register", data={"csrf_token": t, "username": user, "email": user + "@example.com", "password": pw, "confirm": pw})
    try:
        return sess(user, pw)
    except AssertionError:
        return None

out = ssh("docker cp ~/adsb-portal/tests/repeaters_unit.py adsb-portal:/tmp/repeaters_unit.py; docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/repeaters_unit.py")
check("repeaters_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-500:])

adm = sess("marty", PW)
u1, u2 = register("rpuser1"), register("rpuser2")
check("two ordinary members exist for the test", u1 is not None and u2 is not None)
anon = requests.Session()

# start clean
for rid in re.findall(r'href="/repeaters/(\d+)"', adm.get(B + "/repeaters/?q=RPTEST").text):
    act(adm, f"/admin/repeaters/{rid}/delete", "/admin/repeaters")

# ---- public pages
pg = anon.get(B + "/repeaters/")
check("the repeater list is public", pg.status_code == 200 and "Repeaters" in pg.text and "Hearham" in pg.text and "RepeaterBook" in pg.text)
check("the list has its filters", all(x in pg.text for x in ('name="mode"', 'name="band"', 'name="kind"', 'name="status"', 'name="state"')))
mp = anon.get(B + "/repeaters/map")
check("the repeater map uses the shared map shell", mp.status_code == 200 and 'id="lv"' in mp.text and "ArkShell" in mp.text and "/api/repeaters/map.json" in mp.text)
check("the map data is public and credited", anon.get(B + "/api/repeaters/map.json").json().get("credit", "").startswith("Repeater listings"))
check("the menu has a Radio group with the directory and the map", 'href="/repeaters/map"' in pg.text and ">Radio<" in pg.text)

# ---- a member submits a repeater: it waits
form = {"freq": "147.255", "offset": "+0.6", "tone": "110.9", "callsign": "RPTEST1", "city": "Little Rock", "lat": "34.74", "lon": "-92.29", "kind": "repeater", "status": "active", "modes": ["FM"]}
check("posting without signing in is refused", anon.post(B + "/my/repeaters/new", data=form, allow_redirects=False).status_code in (302, 400, 401, 403))
ok, msg, _ = act(u1, "/my/repeaters/new", "/my/repeaters", **form)
check("a member's submission is taken and waits for an editor", ok and "editor" in msg.lower(), msg)
hits = re.findall(r'href="/repeaters/(\d+)">[^<]*</a>', adm.get(B + "/admin/repeaters").text)
adm_page = adm.get(B + "/admin/repeaters").text
check("the submission is in the administrators' queue", "RPTEST1" in adm_page and "Submissions Waiting (1)" in adm_page)
check("it is not in the public list yet", "RPTEST1" not in anon.get(B + "/repeaters/?q=RPTEST").text)
rid = re.search(r'href="/repeaters/(\d+)">147\.255 RPTEST1', adm_page).group(1)
check("nor on the map data", all(p["call"] != "RPTEST1" for p in anon.get(B + "/api/repeaters/map.json").json()["repeaters"]))
ok, msg, _ = act(u1, "/my/repeaters/new", "/my/repeaters", **form)
check("the same repeater again is refused as a duplicate", not ok and "already listed" in msg, msg)
check("an ordinary member cannot approve", u2.post(B + f"/admin/repeaters/{rid}/approve", data={"csrf_token": tok(u2, "/my/repeaters")}, allow_redirects=False).status_code in (302, 403, 404))
check("and it is still waiting", "RPTEST1" not in anon.get(B + "/repeaters/?q=RPTEST").text)
ok, msg, _ = act(adm, f"/admin/repeaters/{rid}/approve", "/admin/repeaters")
check("an administrator approves it", ok, msg)
check("it is in the public list now (the map data follows within half a minute)", "RPTEST1" in anon.get(B + "/repeaters/?q=RPTEST").text)
d = anon.get(B + f"/repeaters/{rid}").text
check("its page shows output, input, tone, band and source", "147.255" in d and "147.855" in d and "110.9" in d and "2 m" in d and "an Arkansas ham" in d, d[:300])

rs = anon.get(B + "/search?q=147.255", allow_redirects=False)
check("the one search box sends a frequency to the repeater (one hit goes straight to it) or the repeater list", rs.status_code == 302 and ("/repeaters/" in (rs.headers.get("Location") or "")), rs.headers.get("Location"))
rs = anon.get(B + "/search?q=RPTEST1", allow_redirects=False)
check("and a repeater's call sign too: straight to it, or a results page that lists it", "/repeaters/" in (rs.headers.get("Location") or "") or (rs.status_code == 200 and "RPTEST1" in rs.text), rs.status_code)

# ---- reports
ok, msg, _ = act(u2, f"/repeaters/{rid}/report", f"/repeaters/{rid}", kind="tone", note="it is 100.0 now", s_tone="100.0")
check("a member reports a wrong tone with the right one", ok, msg)
ok, msg, _ = act(u2, f"/repeaters/{rid}/report", f"/repeaters/{rid}", kind="tone", note="again")
check("the same open report twice is refused", not ok and "already" in msg, msg)
ok, msg, _ = act(u2, f"/repeaters/{rid}/report", f"/repeaters/{rid}", kind="frequency", s_freq="abc")
check("a suggested frequency that is not a number is refused", not ok, msg)
q = adm.get(B + "/admin/repeaters").text
rep = re.search(r'/admin/repeaters/report/(\d+)/apply', q)
check("the report is in the queue with the suggestion", rep and "Wrong tone" in q and "100.0" in q)
ok, msg, _ = act(adm, f"/admin/repeaters/report/{rep.group(1)}/apply", "/admin/repeaters")
check("applying it changes the tone", ok and "100.0" in anon.get(B + f"/repeaters/{rid}").text and "110.9" not in anon.get(B + f"/repeaters/{rid}").text)
check("anonymous visitors cannot report", anon.post(B + f"/repeaters/{rid}/report", data={"kind": "off"}, allow_redirects=False).status_code in (302, 400, 401, 403))

# ---- a claim
ok, msg, _ = act(u1, f"/repeaters/{rid}/claim", f"/repeaters/{rid}")
check("a sysop claims the repeater", ok, msg)
check("until it is approved they cannot edit it", u1.get(B + f"/my/repeaters/{rid}").status_code == 404)
ok, msg, _ = act(adm, f"/admin/repeaters/{rid}/claim-approve", "/admin/repeaters")
check("the administrator approves the claim", ok, msg)
check("now the keeper can open the edit page", u1.get(B + f"/my/repeaters/{rid}").status_code == 200)
ok, msg, _ = act(u1, f"/my/repeaters/{rid}", f"/my/repeaters/{rid}", **dict(form, status="off", city="Conway"))
d2 = anon.get(B + f"/repeaters/{rid}").text
check("the keeper's change is saved and shown", ok and "Off the air" in d2 and "Conway" in d2, msg)
ok, msg, _ = act(u1, f"/my/repeaters/{rid}", f"/my/repeaters/{rid}", **dict(form, callsign="HACKED", city="Conway", status="off"))
check("a keeper cannot change the call sign (only what is current: status, tone, notes)", "HACKED" not in anon.get(B + f"/repeaters/{rid}").text)
check("another member cannot edit it", u2.get(B + f"/my/repeaters/{rid}").status_code == 404)
hist = adm.get(B + f"/repeaters/{rid}").text
check("an editor sees the history with who changed what", "claim approved" in hist and "edited" in hist and "rpuser1" in hist)

# ---- the administrators add one for a member
add = dict(form, callsign="RPTEST2", freq="444.1", offset="+5", lat="35.2", lon="-92.7", username="rpuser2", status="active")
ok, msg, _ = act(adm, "/admin/repeaters/add", "/admin/repeaters", **add)
check("an administrator adds a repeater for a member", ok, msg)
mine = u2.get(B + "/my/repeaters").text
check("it is on that member's My Repeaters page, theirs to edit", "RPTEST2" in mine and "/my/repeaters/" in mine)
ok, msg, _ = act(adm, "/admin/repeaters/add", "/admin/repeaters", **dict(add, username="nosuchmember99", callsign="RPTEST3"))
check("an unknown member is refused", not ok and "No member" in msg, msg)
check("the admin page is for editors only", requests.get(B + "/admin/repeaters", allow_redirects=False).status_code in (302, 401, 403) and u2.get(B + "/admin/repeaters", allow_redirects=False).status_code in (302, 403, 404))
audit = adm.get(B + "/admin/audit").text
check("every step is in the audit log", all(k in audit for k in ("repeater.submit", "repeater.approve", "repeater.report_apply", "repeater.claim", "repeater.claim_approve", "repeater.admin_add", "repeater.edit")))
check("the Settings page has the Repeaters group", all(f'name="{k}"' in adm.get(B + "/admin/settings").text for k in ("repeater_enabled", "repeater_hearham_enabled", "repeater_hearham_url", "repeater_states", "repeater_reports_per_hour", "repeaters_update_days", "repeater_map_area")))
du = adm.get(B + "/admin/data-updates/").text
check("Data Updates has the Repeaters job", "Repeaters" in du)
check("the admin menu has Repeaters", "/admin/repeaters" in adm.get(B + "/admin/").text)
check("the About page lists the directory", "/repeaters/" in requests.get(B + "/about").text)

# ---- a list from a club, and the APRS source
NL = chr(10)
tpl = adm.get(B + "/admin/repeaters/template.csv")
check("the CSV template downloads with the column names", tpl.status_code == 200 and tpl.text.startswith("freq,offset,tone,callsign") and "text/csv" in tpl.headers.get("Content-Type", ""))
check("the template is for editors only", u2.get(B + "/admin/repeaters/template.csv", allow_redirects=False).status_code in (302, 403, 404))
tk = re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + "/admin/repeaters").text).group(1)
body = NL.join(["freq,offset,tone,callsign,city,state,lat,lon,modes,notes", "146.745,-0.6,100.0,RPTEST9,Searcy,AR,35.25,-91.74,FM,from a test list", "99.5,,,X,Nowhere,AR,35,-92,FM,not a ham band"]) + NL
r = adm.post(B + "/admin/repeaters/import-csv", data={"csrf_token": tk, "label": "Test council list"}, files={"file": ("list.csv", body.encode())}, headers={"X-Requested-With": "XMLHttpRequest"})
j = r.json()
check("a list is imported and the answer says what it could not take", j["ok"] and "1 added" in j["message"] and "1 rows not taken" in j["message"], j)
chk = adm.get(B + "/repeaters/?q=RPTEST9").text
check("the listed repeater is in the directory", "146.745" in chk and "RPTEST9" in chk)
rid9 = re.search(r'href="/repeaters/(\d+)">146\.745', chk).group(1)
check("its page names the list it came from? (source shown as this network's editors)", "this network" in anon.get(B + f"/repeaters/{rid9}").text)
r = adm.post(B + "/admin/repeaters/import-csv", data={"csrf_token": tk, "label": ""}, headers={"X-Requested-With": "XMLHttpRequest"})
check("without a file and a name it is refused", not r.json()["ok"])
r = u2.post(B + "/admin/repeaters/import-csv", data={"csrf_token": tok(u2, "/my/repeaters"), "label": "x"}, files={"file": ("l.csv", body.encode())}, allow_redirects=False)
check("an ordinary member cannot import a list", r.status_code in (302, 403, 404))
act(adm, f"/admin/repeaters/{rid9}/delete", "/admin/repeaters")
check("the Settings page has the APRS source switch", 'name="repeater_aprs_enabled"' in adm.get(B + "/admin/settings").text)

# ---- clean up
for r in re.findall(r'href="/repeaters/(\d+)"', adm.get(B + "/repeaters/?q=RPTEST").text):
    act(adm, f"/admin/repeaters/{r}/delete", "/admin/repeaters")
print("ALL PASS" if ok_all else "SOME FAILED")
