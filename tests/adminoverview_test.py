"""Admin > Overview is a real overview: what needs attention (each with a link), the network at a glance, the data we keep, the system, and recent activity."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
if int(psql("select count(*) from hospitals") or 0) < 100:   # a fresh database: load the hospitals the way an administrator would
    adm.post(B + "/admin/hospitals/import", data={"csrf_token": tok(adm, "/admin/hospitals")}, headers={"X-Requested-With": "XMLHttpRequest"})
check("the overview needs an administrator", requests.get(B + "/admin/", allow_redirects=False).status_code in (302, 401, 403, 404) and sess("plain1", "plainpassword1").get(B + "/admin/", allow_redirects=False).status_code in (302, 401, 403, 404))
t = adm.get(B + "/admin/").text
check("it has the four sections, collapsible", all(f'id="sec-{s}"' in t for s in ("attention", "data-health", "system", "recent-activity")) and t.count('class="panel setsec"') >= 4)
check("it has the at-a-glance tiles for aircraft, stations, flights and people", all(x in t for x in ("Aircraft now", "Stations online", "Flights recorded", "Accounts", "New accounts (7 days)", "People signed in (24 h)")))
check("it lists the data we keep with counts and when each was refreshed", all(x in t for x in ("Hospitals", "Airports", "Places", "Air ambulances", "Interesting aircraft", "Last refreshed")))
hosp = psql("select count(*) from hospitals")
check("the hospital count is the real one", f"{int(hosp):,}" in t, hosp)
check("it shows the services behind the site, with their state", "Website and Accounts" in t and "Address Search (Photon)" in t and re.search(r'class="tag (live|off)"', t))
check("it shows recent activity from the audit log", "Recent Activity" in t and re.search(r'<td class="mono">[a-z_.]+</td>', t) is not None)
check("it has the grouped admin menu", 'class="subnav adminnav"' in t)
# conditions that must raise an item, with a link to fix it
psql("update users set locked_until = now() + interval '1 hour' where username='plain1'")
fid = psql("select facility_id from hospitals where pos_check='' and facility_id not like 'imp-%' limit 1")
psql(f"update hospitals set pos_check='conflict' where facility_id='{fid}'")
t = adm.get(B + "/admin/").text
check("a locked account raises an item that links to Users", re.search(r"1 account is locked after failed sign-ins.*?href=\"/admin/users\"", t, re.S) is not None, re.findall(r"locked[^<]*", t))
check("a hospital position to review raises an item that links to the review list", re.search(r"hospital positions need a look.*?href=\"/admin/hospitals\?check=review\"", t, re.S) is not None, re.findall(r"need a look[^<]*", t))
psql("update users set locked_until = NULL where username='plain1'")
psql(f"update hospitals set pos_check='' where facility_id='{fid}'")
t = adm.get(B + "/admin/").text
check("the items go away when the condition does", "is locked after failed sign-ins" not in t and "need a look" not in t)
print("ALL PASS" if ok_all else "SOME FAILED")
