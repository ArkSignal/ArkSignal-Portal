"""An administrator adds things for a member: a weather station and an ADS-B feeder (a radiosonde station is in sondes_test.py). Each is created under the member's account, opened with
their own pages, limited to administrators, and recorded in the audit log."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout
def psql(sql): return ssh(D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
def csrf(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
H = {"X-Requested-With": "XMLHttpRequest"}
adm = sess("marty", PW)
try:
    plain = sess("plain1", "plainpassword1")
except AssertionError:
    s0 = requests.Session()
    s0.post(B + "/my/register", data={"csrf_token": csrf(s0, "/my/register"), "username": "plain1", "email": "plain1@example.com", "password": "plainpassword1", "confirm": "plainpassword1"})
    plain = sess("plain1", "plainpassword1")
p1 = psql("select id from users where username='plain1'")

# ---- the live member search (all three forms use it)
psql("update users set display_name='Plain Search Person', callsign='K5PLN' where username='plain1'")
UF = "aaaaaaaa-1111-4222-8333-000000000090"
psql(f"delete from feeders where uuid='{UF}'")
psql(f"insert into feeders (uuid, first_seen, last_seen, online, owner_id, station_id, name) values ('{UF}', now(), now(), false, {p1}, 'SEARCHFD', 'Search feeder')")
def srch(q, s=None): return (s or adm).get(B + "/admin/members/search.json", params={"q": q}).json()["members"]
for q in ("plain1", "Plain Search", "k5pln", "plain1@example"):
    m = [x for x in srch(q) if x["username"] == "plain1"]
    check(f"the member search finds a member by '{q}'", len(m) == 1, srch(q))
m = [x for x in srch("plain1") if x["username"] == "plain1"][0]
check("a match carries display name, call sign, email and the member's feeders", m["display_name"] == "Plain Search Person" and m["callsign"] == "K5PLN" and m["email"] and any(f["uuid"] == UF and f["station_id"] == "SEARCHFD" and f["has_wx"] is False for f in m["feeders"]), m)
check("a % in the search is taken literally, an empty search gives nothing", srch("%%%%") == [] and srch("") == [])
check("an ordinary member cannot use the member search", plain.get(B + "/admin/members/search.json", params={"q": "plain"}).status_code in (302, 401, 403, 404))
check("all three forms load the picker script and have no plain user list", all('/static/memberpicker.js' in adm.get(B + u).text for u in ("/admin/weather", "/admin/feeders", "/admin/sondes")) and adm.get(B + "/static/memberpicker.js").status_code == 200)
mp = adm.get(B + "/admin/weather").text
check("the weather form offers every option of the member's form", all(x in mp for x in ('name="feeder"', 'name="name"', 'name="station_id"', 'name="lat"', 'name="privacy"', 'name="application_key"', 'name="tempest_station"', 'name="hardware"', 'name="description"', 'name="public"')))
psql("update users set display_name=null, callsign=null where username='plain1'")   # (so "You are managing plain1" names the user)

# ---- a weather station for a member
pg = adm.get(B + "/admin/weather").text
check("the admin Weather Stations page has the form: member, how it sends, name, station ID, position, privacy", 'id="sec-add-for-member"' in pg and 'name="username"' in pg and 'name="kind"' in pg and 'name="station_id"' in pg and 'name="lat"' in pg and 'data-memberpicker' in pg)
def add(**kw):
    d = {"csrf_token": csrf(adm, "/admin/weather"), "username": "plain1", "kind": "relay", "name": "For Plain", "station_id": "ADMADD-WX", "lat": "34.8", "lon": "-92.3", "privacy": "general"}
    d.update(kw)
    return adm.post(B + "/admin/weather/add", data=d, allow_redirects=False)
r = add()
wid = psql("select id from wx_stations where station_id='ADMADD-WX'")
check("an administrator adds a station under the member's account and is sent to the member's page for it", r.status_code == 302 and f"/my/weather/{wid}" in r.headers["Location"] and psql(f"select owner_id from wx_stations where id={wid}") == p1 and psql(f"select public from wx_stations where id={wid}") == "f")
page = adm.get(B + f"/my/weather/{wid}").text
check("that page shows the upload address ready to copy and says whose station it is", "Upload Address" in page and "/wx/relay/" in page and "You are managing plain1" in page)
check("the member sees it in their own list", "ADMADD-WX" in plain.get(B + "/my/weather").text and plain.get(B + f"/my/weather/{wid}").status_code == 200)
check("an administrator is not held to the per-member station limit", (psql("update settings set value='1' where key='wx_max_per_user'") or True) and add(station_id="ADMADD-2").status_code == 302 and psql("select count(*) from wx_stations where owner_id=" + p1) == "2")
psql("delete from settings where key='wx_max_per_user'")
for kw in ({"username": "nobody-xyz", "station_id": "ADMADD-3"}, {"station_id": "ADMADD-WX"}, {"station_id": "x"}, {"station_id": "ADMADD-4", "lat": ""}, {"station_id": "ADMADD-5", "kind": "ecowitt_cloud"}, {"station_id": "ADMADD-6", "kind": "nonsense"}):
    add(**kw)
check("an unknown member, a taken or bad station ID, a missing position and a cloud station without keys are refused (nothing is created)", psql("select count(*) from wx_stations where station_id in ('ADMADD-3','ADMADD-4','ADMADD-5','ADMADD-6','x')") == "0" and psql("select count(*) from wx_stations where station_id='ADMADD-WX'") == "1")
check("an ordinary member cannot use it", plain.post(B + "/admin/weather/add", data={"csrf_token": csrf(plain, "/my/weather"), "username": "plain1", "kind": "relay", "station_id": "ADMADD-7", "lat": "1", "lon": "1"}, allow_redirects=False).status_code in (302, 400, 401, 403, 404) and psql("select count(*) from wx_stations where station_id='ADMADD-7'") == "0")
check("it is in the audit log under the administrator", int(psql("select count(*) from audit_log where actor_name='marty' and action='wx.create' and detail like '%added by an administrator%'") or 0) >= 2)
psql("delete from wx_stations where station_id like 'ADMADD-%'")
# ---- tied to one of the member's ADS-B feeders
r = add(feeder=UF, station_id="", lat="", lon="", name="Tied", public="1", hardware="GW2000", description="on the feeder")
twid = psql(f"select id from wx_stations where feeder_uuid='{UF}'")
check("a station can be tied to the member's feeder (shares its ID: none of its own), with name, hardware, about and public", r.status_code == 302 and twid != "" and psql(f"select coalesce(station_id,'none')||'/'||owner_id||'/'||name||'/'||hardware||'/'||description||'/'||public from wx_stations where id={twid}") == f"none/{p1}/Tied/GW2000/on the feeder/true", r.headers.get("Location"))
check("the member search then marks that feeder as having a station", [f for f in [x for x in srch("plain1") if x["username"] == "plain1"][0]["feeders"] if f["uuid"] == UF][0]["has_wx"] is True)
check("a second station on the same feeder is refused", add(feeder=UF, station_id="", lat="", lon="").status_code == 302 and psql(f"select count(*) from wx_stations where feeder_uuid='{UF}'") == "1")
add(feeder="aaaaaaaa-1111-4222-8333-000000000091", username="plain1", station_id="", lat="", lon="")
check("a feeder that is not the member's cannot be used", psql("select count(*) from wx_stations where feeder_uuid='aaaaaaaa-1111-4222-8333-000000000091'") == "0")
psql(f"delete from wx_stations where feeder_uuid='{UF}'")
psql("insert into settings (key, value) values ('wx_tempest_pull_enabled','1') on conflict (key) do update set value='1'")
r = add(station_id="ADMCLOUD-WX", kind="tempest_cloud", tempest_station="12345", token="tok-abc")
check("a Tempest account station keeps its keys sealed (not readable in the database)", r.status_code == 302 and psql("select length(secrets) from wx_stations where station_id='ADMCLOUD-WX'") not in ("", "0") and "tok-abc" not in psql("select secrets from wx_stations where station_id='ADMCLOUD-WX'"))
psql("delete from settings where key='wx_tempest_pull_enabled'"); psql("delete from wx_stations where station_id='ADMCLOUD-WX'")
# ---- the location choices: a station at each level is shown rounded that much, with a circle of the right size
for lvl, lat, rad in (("close", 34.8, 556), ("near", 34.799, 55), ("wide", 35.0, 55660)):
    add(station_id="ADMLVL-" + lvl.upper(), privacy=lvl, lat="34.7991", lon="-92.3017")
    psql(f"update wx_stations set public=true where station_id='ADMLVL-{lvl.upper()}'")
    psql(f"update wx_stations set last_report=now(), latest='{{\"temp_f\": 70}}' where station_id='ADMLVL-{lvl.upper()}'")
    pts = {x["id"]: x for x in requests.get(B + "/api/weather.json").json()["stations"]}
    x = pts.get("ADMLVL-" + lvl.upper())
    check(f"a weather station at level {lvl} is public at {lat} with a circle of {rad} meters", x is not None and x["lat"] == lat and x["radius_m"] == rad and x["approx"], x)
psql("delete from wx_stations where station_id like 'ADMLVL-%'")
check("the choices appear on the forms for members", all(k in plain.get(B + "/my/weather").text for k in ('value="near"', 'value="close"', 'value="general"', 'value="wide"', 'value="hidden"', 'value="exact"')))


# ---- an ADS-B feeder for a member, before its receiver has connected
fp = adm.get(B + "/admin/feeders").text
check("the admin Feeders page has the form: member, UUID, station ID, name", 'id="sec-add-feeder"' in fp and 'name="uuid"' in fp and 'data-memberpicker' in fp and 'name="station_id"' in fp)
UU = "aaaaaaaa-1111-4222-8333-000000000099"
def addf(**kw):
    d = {"csrf_token": csrf(adm, "/admin/feeders"), "username": "plain1", "uuid": UU, "station_id": "ADMFEED", "name": "Added for Plain"}
    d.update(kw)
    return adm.post(B + "/admin/feeders/add", data=d, allow_redirects=False)
r = addf()
check("an administrator registers a feeder for a member and lands on its admin page", r.status_code == 302 and UU in r.headers["Location"] and psql(f"select owner_id || '/' || station_id || '/' || claim_method from feeders where uuid='{UU}'") == p1 + "/ADMFEED/admin", r.headers.get("Location"))
check("the member has it under My Feeders from the start, offline until the receiver connects", "ADMFEED" in plain.get(B + "/my/").text and psql(f"select online from feeders where uuid='{UU}'") == "f")
for kw in ({}, {"uuid": "bbbbbbbb-1111-4222-8333-000000000098", "username": "nobody-xyz"}, {"uuid": "not-a-uuid"}, {"uuid": "bbbbbbbb-1111-4222-8333-000000000097", "station_id": "ADMFEED"}):
    addf(**kw)
check("the same UUID again, an unknown member, a bad UUID and a taken station ID are refused (nothing is created)", psql("select count(*) from feeders where uuid like 'bbbbbbbb-1111%'") == "0" and psql(f"select count(*) from feeders where uuid='{UU}'") == "1")
check("an ordinary member cannot use it", plain.post(B + "/admin/feeders/add", data={"csrf_token": csrf(plain, "/my/"), "username": "plain1", "uuid": "cccccccc-1111-4222-8333-000000000096"}, allow_redirects=False).status_code in (302, 400, 401, 403, 404) and psql("select count(*) from feeders where uuid like 'cccccccc-1111%'") == "0")
check("it is in the audit log", int(psql("select count(*) from audit_log where actor_name='marty' and action='feeder.assign' and detail like '%added by an administrator%'") or 0) >= 1)
r = addf(uuid="bbbbbbbb-1111-4222-8333-000000000089", station_id="ADMFEED2", name="Full options", description="about it", lat="34.5", lon="-92.5", antenna_height_ft="30", height_ref="agl", ground_elev_ft="300", privacy="close", setup_receiver="RTL-SDR V4", notes="private", mlat_user="MLATX", website="https://example.com", website_public="1")
row = psql("select name||'/'||description||'/'||lat||'/'||lon||'/'||antenna_height_ft||'/'||height_ref||'/'||ground_elev_ft||'/'||privacy||'/'||setup_receiver||'/'||notes||'/'||mlat_user||'/'||website||'/'||website_public from feeders where uuid='bbbbbbbb-1111-4222-8333-000000000089'")
check("an added feeder gets every detail the member can set", row == "Full options/about it/34.5/-92.5/30/agl/300/close/RTL-SDR V4/private/MLATX/https://example.com/true", row)
r = addf(uuid="bbbbbbbb-1111-4222-8333-000000000088", station_id="ADMFEED3", lat="34.5", lon="")
check("a bad detail (latitude without longitude) refuses the whole feeder", psql("select count(*) from feeders where uuid='bbbbbbbb-1111-4222-8333-000000000088'") == "0")
psql("delete from feeder_events where feeder_uuid like 'bbbbbbbb-1111%'"); psql("delete from feeders where uuid like 'bbbbbbbb-1111%'")
# ---- the same action buttons on the feeders list as on the weather and radiosonde lists
UU2 = "dddddddd-1111-4222-8333-000000000095"
psql(f"insert into feeders (uuid, first_seen, last_seen, online) values ('{UU2}', now(), now(), false)")
fl = adm.get(B + "/admin/feeders").text
check("every feeders row has an Actions column", "<th>Actions</th>" in fl)
check("an owned feeder has Manage (the owner's own page), Details, Release and Ban; an unowned one has Manage (the admin page), Assign and Ban", f'href="/my/feeders/{UU}"' in fl and f'/admin/feeders/{UU}/manage' in fl and ">Release<" in fl and f'href="/admin/feeders/{UU2}"' in fl and ">Assign<" in fl and ">Ban<" in fl)
mine_page = adm.get(B + f"/my/feeders/{UU}")
check("Manage opens the owner's own feeder page for an administrator, with a banner naming the owner", mine_page.status_code == 200 and "You are managing plain1" in mine_page.text and "Administrator page for this feeder" in mine_page.text)
reg2 = requests.Session()
reg2.post(B + "/my/register", data={"csrf_token": csrf(reg2, "/my/register"), "username": "plain2", "email": "plain2@example.com", "password": "plainpassword2", "confirm": "plainpassword2"})
other = sess("plain2", "plainpassword2")
check("another ordinary member still cannot open it", other.get(B + f"/my/feeders/{UU}").status_code == 404)
r = adm.post(B + f"/admin/feeders/{UU2}/manage", data={"csrf_token": csrf(adm, "/admin/feeders"), "action": "ban", "next": "list"}, allow_redirects=False)
check("Ban from the list comes back to the list", r.status_code == 302 and r.headers["Location"].endswith("/admin/feeders") and psql(f"select banned from feeders where uuid='{UU2}'") == "t", r.headers.get("Location"))
r = adm.post(B + f"/admin/feeders/{UU2}/manage", data={"csrf_token": csrf(adm, "/admin/feeders"), "action": "unban", "next": "list"}, allow_redirects=False)
check("and Unban", r.status_code == 302 and psql(f"select banned from feeders where uuid='{UU2}'") == "f")
addf(uuid=UU2, username="plain1", station_id="", name="")
check("Add or Assign hands a feeder the hub already knows to the member", psql(f"select owner_id from feeders where uuid='{UU2}'") == p1 and psql(f"select claim_method from feeders where uuid='{UU2}'") == "admin")
r = adm.post(B + f"/admin/feeders/{UU2}/manage", data={"csrf_token": csrf(adm, "/admin/feeders"), "action": "owner", "owner_id": "", "next": "list"}, allow_redirects=False)
check("Release from the list takes it away from the owner and comes back to the list", r.status_code == 302 and r.headers["Location"].endswith("/admin/feeders") and psql(f"select coalesce(owner_id::text, 'none') from feeders where uuid='{UU2}'") == "none")
r = adm.post(B + f"/my/feeders/{UU}/release", data={"csrf_token": csrf(adm, f"/my/feeders/{UU}")}, allow_redirects=False)
check("an administrator can release a member's feeder from the member's own page and returns to the admin list", r.status_code == 302 and r.headers["Location"].endswith("/admin/feeders") and psql(f"select coalesce(owner_id::text, 'none') from feeders where uuid='{UU}'") == "none", r.headers.get("Location"))
check("the MLAT page opens (its rows carry the same buttons when the server reports stations)", adm.get(B + "/admin/mlat").status_code == 200)
psql(f"delete from feeder_events where feeder_uuid='{UU2}'"); psql(f"delete from feeders where uuid='{UU2}'")
psql(f"delete from feeder_events where feeder_uuid='{UU}'"); psql(f"delete from feeders where uuid='{UU}'")

# ---- a radiosonde station for a member with the member's own options
def asg(**kw):
    d = {"csrf_token": csrf(adm, "/admin/sondes"), "callsign": "ADMSONDE-1", "username": "plain1", "mode": "assign", "display_name": "Roof", "notes": "north side", "privacy": "close"}
    d.update(kw)
    return adm.post(B + "/admin/sondes/assign", data=d, headers=H).json()
j = asg()
check("a station is given to a member with name, about and privacy, not listed because the box was left off", j["ok"] and psql("select display_name||'/'||notes||'/'||privacy||'/'||public||'/'||claim_status from sonde_stations where callsign='ADMSONDE-1'") == "Roof/north side/close/false/claimed")
j = asg(callsign="ADMSONDE-2", mode="claim", public="1")
check("a claim started for a member keeps the name and about; privacy waits until it is claimed", j["ok"] and psql("select display_name||'/'||notes||'/'||claim_status from sonde_stations where callsign='ADMSONDE-2'") == "Roof/north side/pending")
check("an unknown privacy choice is refused", not asg(callsign="ADMSONDE-3", privacy="bogus")["ok"] and psql("select count(*) from sonde_stations where callsign='ADMSONDE-3'") == "0")
psql("delete from sonde_stations where callsign like 'ADMSONDE-%'")
psql(f"delete from feeders where uuid='{UF}'")
psql("update users set display_name=null, callsign=null where username='plain1'")

print("ALL PASS" if ok_all else "SOME FAILED")
