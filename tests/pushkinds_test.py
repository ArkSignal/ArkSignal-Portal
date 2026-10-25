"""Notifications for every kind of alert: the registry and its switches (unit script inside the portal with a recording sender and fixed layer data: weather warnings, earthquakes, rivers, satellite passes, followed balloons,
administrator items, limits, site switches), and the member's Notifications page (switches, places, followed balloons, who sees what), the site's list of kinds it does not offer, and the guests."""
import json, os, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker {cmd}"], capture_output=True, text=True).stdout
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")

# ---- the unit script: every kind with its carrier
subprocess.run(["scp", "-q", "pushkinds_unit.py", "nastemp:adsb-portal/pushkinds_unit.py"], check=True)
ssh("cp ~/adsb-portal/pushkinds_unit.py adsb-portal:/tmp/pushkinds_unit.py")
o = ssh("exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/pushkinds_unit.py").strip().splitlines()
try:
    res = json.loads(o[-1])
except Exception:
    res = {}
    print("unit output:", o[-6:])
names = {"keys_unique_and_known": "the registry lists thirteen kinds, each for members or for administrators", "defaults": "each kind starts the way it is meant to (chat and friends on, places and mesh off)",
         "switches_write_to_their_homes": "a switch is kept where it already lived for friends and mesh nodes, and in the new table for the rest", "admin_kind_only_for_admins": "the administrator kind is offered only to administrators",
         "notify_checks_switches": "nothing is sent when the member's switch, the kind's site switch or the whole site switch is off",
         "limit_per_kind_per_hour": "a kind stops at the hourly limit while another kind still goes", "weather_inside_and_near_only": "weather warnings: inside the outline or within the distance, only the kinds the site sends",
         "weather_not_repeated": "a warning already told is not told again", "weather_off_for_others": "a member with no places or no switch hears nothing",
         "quake_size_distance_and_age": "earthquakes: only big enough, near enough and recent", "quake_not_repeated": "an earthquake is told once", "river_level_and_distance": "rivers: only gauges at the flood level, near the place",
         "river_worse_level_is_new": "a gauge that gets worse is told again", "satpass_reminder": "a satellite pass reminder names the satellite, the place and when", "satpass_not_repeated": "a pass is told once",
         "satpass_not_before_the_lead_time": "no reminder before the lead time", "satpass_off_sends_nothing": "with the switch off no reminder", "balloon_landed_for_the_follower_with_it_on": "a landed balloon is told to those who follow it and have the switch on",
         "balloon_not_repeated": "a landing is told once", "admin_items": "administrators hear new items needing attention (warnings only), others do not", "admin_items_not_repeated": "an item is told once",
         "one_failing_kind_does_not_stop_the_pass": "one failing kind does not stop the others", "aircraft_master_switch": "aircraft alerts: the master switch starts on", "aircraft_master_switch_off": "and off when the member turns it off", "quiet_holds_a_normal_notice": "quiet hours hold a normal notice back", "quiet_lets_an_urgent_one_through": "an urgent warning still comes through when the member allows it", "quiet_without_the_urgent_allowance_holds_it": "and not when they do not", "outside_the_window_sends": "outside the window it is sent", "a_window_past_midnight_works": "a window past midnight works", "the_site_can_switch_quiet_hours_off": "the site can switch quiet hours off"}
for k, label in names.items():
    check("unit: " + label, res.get(k) is True, res.get(k))

# ---- the page
setting("push_enabled", "1"); setting("push_kinds_off", ""); setting("push_places_per_member", "5"); setting("push_follows_per_member", "20"); setting("push_place_max_radius_km", "300")
psql("DELETE FROM users WHERE username LIKE 'pkt%'")
for n in ("pktone", "pkttwo"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
U1, U2 = sess("pktone", "pktonepassword99"), sess("pkttwo", "pkttwopassword99")
adm = sess("marty", PW)
anon = requests.Session()
def tok(s, url="/my/notifications"):
    return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + url).text).group(1)
check("a guest is sent to sign in", anon.get(B + "/my/notifications", allow_redirects=False).status_code == 302)
pg = U1.get(B + "/my/notifications").text
check("the page has a switch for every kind a member can have (twelve) and not the administrators' one", len(re.findall(r'name="kind_[a-z_]+"', pg)) == 12 and 'name="kind_admin_attention"' not in pg, re.findall(r'name="kind_[a-z_]+"', pg))
check("an administrator also has theirs", 'name="kind_admin_attention"' in adm.get(B + "/my/notifications").text)
check("the menu has Notifications in My Account", 'href="/my/notifications"' in pg)
check("the device box is on the page", 'id="push-box"' in pg and "ArkPush.init" in pg)
check("Community Settings points to the page instead of keeping its own friend switch", 'name="push_friends"' not in U1.get(B + "/my/community").text and "/my/notifications" in U1.get(B + "/my/community").text)
r = U1.post(B + "/my/notifications/save", data={"csrf_token": tok(U1), "kind_chat": "1", "kind_weather_alert": "1", "kind_quake": "1", "sats_sent": "1"}, allow_redirects=True)
check("saving turns the ticked kinds on and the rest off", psql("SELECT string_agg(kind || '=' || enabled::text, ',' ORDER BY kind) FROM push_prefs WHERE user_id=(SELECT id FROM users WHERE username='pktone')") .startswith("aircraft=f") and psql("SELECT enabled FROM push_prefs WHERE kind='quake' AND user_id=(SELECT id FROM users WHERE username='pktone')") == "t" and psql("SELECT push_friends FROM users WHERE username='pktone'") == "f", psql("SELECT string_agg(kind || '=' || enabled::text, ',') FROM push_prefs WHERE user_id=(SELECT id FROM users WHERE username='pktone')"))
pg = U1.get(B + "/my/notifications").text
check("and the page shows them that way", re.search(r'name="kind_quake" value="1" checked', pg) and not re.search(r'name="kind_friends" value="1" checked', pg) and re.search(r'name="kind_chat" value="1" checked', pg))
check("another member's choices are their own", not re.search(r'name="kind_quake" value="1" checked', U2.get(B + "/my/notifications").text))
check("the change is in the audit log", int(psql("SELECT COUNT(*) FROM audit_log WHERE action='push.prefs' AND target='pktone'")) >= 1)
# ---- quiet hours
check("the page has the Quiet Hours choice", 'name="start"' in U1.get(B + "/my/notifications").text and "Quiet Hours" in U1.get(B + "/my/notifications").text)
U1.post(B + "/my/notifications/quiet", data={"csrf_token": tok(U1), "start": "22", "end": "6", "urgent": "1"})
check("quiet hours are saved", psql("SELECT push_quiet_start || '-' || push_quiet_end || '-' || push_quiet_urgent FROM users WHERE username='pktone'") == "22-6-true")
check("and shown again", re.search(r'<option value="22" selected', U1.get(B + "/my/notifications").text) is not None)
U1.post(B + "/my/notifications/quiet", data={"csrf_token": tok(U1), "start": "-1", "end": "-1"})
check("no quiet hours turns them off", psql("SELECT push_quiet_start FROM users WHERE username='pktone'") == "-1")
check("a bad hour is refused", "Choose the hours" in U1.post(B + "/my/notifications/quiet", data={"csrf_token": tok(U1), "start": "40", "end": "2"}, allow_redirects=True).text)
setting("push_quiet_enabled", "0")
check("with the site switch off the choice is not offered", "Quiet Hours" not in U1.get(B + "/my/notifications").text)
setting("push_quiet_enabled", "1")
# ---- places
def add(s, **kw):
    return s.post(B + "/my/notifications/places/add", data=dict({"csrf_token": tok(s), "name": "Home", "lat": "34.75", "lon": "-92.29", "radius": "25"}, **kw), allow_redirects=True)
r = add(U1)
check("a place is added with its distance", "Home" in r.text and "34.7500, -92.2900, 25 km around" in r.text, r.text[:0])
check("a place with a bad position is refused", "Give the place as numbers" in add(U1, lat="abc").text and "position on the earth" in add(U1, lat="95").text)
r = add(U1, name="Big", radius="9999")
check("a distance beyond the site's longest is cut to it", psql("SELECT radius_km FROM member_places WHERE name='Big'") == "300")
setting("push_places_per_member", "2")
check("the number of places is limited", "can keep 2 places" in add(U1, name="Third").text)
setting("push_places_per_member", "5")
pid = psql("SELECT id FROM member_places WHERE name='Home' AND user_id=(SELECT id FROM users WHERE username='pktone')")
check("another member cannot remove it", U2.post(B + f"/my/notifications/places/{pid}/remove", data={"csrf_token": tok(U2)}).status_code == 404)
U1.post(B + f"/my/notifications/places/{pid}/remove", data={"csrf_token": tok(U1)})
check("the owner removes it", psql(f"SELECT COUNT(*) FROM member_places WHERE id={pid}") == "0" and int(psql("SELECT COUNT(*) FROM audit_log WHERE action='push.place_remove'")) >= 1)
# ---- following a balloon
psql("DELETE FROM sondes WHERE serial='PKTEST2'")
psql("INSERT INTO sondes (serial, type, status, first_seen, last_seen) VALUES ('PKTEST2','RS41','aloft', now(), now())")
bp = U1.get(B + "/sondes/PKTEST2").text
check("a balloon's page offers Follow to a member and not to a guest", "Follow This Balloon" in bp and "Follow This Balloon" not in anon.get(B + "/sondes/PKTEST2").text)
r = U1.post(B + "/my/notifications/balloons/PKTEST2/follow", data={"csrf_token": tok(U1)}, allow_redirects=True)
check("following works and the page then offers Stop Following", psql("SELECT COUNT(*) FROM sonde_follows WHERE serial='PKTEST2'") == "1" and "Stop Following" in r.text)
check("and the balloon is listed on the Notifications page", "PKTEST2" in U1.get(B + "/my/notifications").text)
check("a balloon that does not exist cannot be followed", U1.post(B + "/my/notifications/balloons/NOPE/follow", data={"csrf_token": tok(U1)}).status_code == 404)
setting("push_follows_per_member", "1")
psql("INSERT INTO sondes (serial, type, status, first_seen, last_seen) VALUES ('PKTEST3','RS41','aloft', now(), now())")
check("the number of followed balloons is limited", "can follow 1 balloons" in U1.post(B + "/my/notifications/balloons/PKTEST3/follow", data={"csrf_token": tok(U1)}, allow_redirects=True).text)
setting("push_follows_per_member", "20")
U1.post(B + "/my/notifications/balloons/PKTEST2/unfollow", data={"csrf_token": tok(U1)})
check("and unfollowed", psql("SELECT COUNT(*) FROM sonde_follows WHERE serial='PKTEST2'") == "0")
psql("DELETE FROM sondes WHERE serial IN ('PKTEST2','PKTEST3')")
# ---- the site's own list
setting("push_kinds_off", "quake,river")
pg = U1.get(B + "/my/notifications").text
check("a kind the site does not offer is shown as not offered and cannot be ticked", pg.count("not offered by this site") == 2 and re.search(r'name="kind_quake" value="1" [^>]*disabled', pg))
setting("push_kinds_off", "")
setting("push_enabled", "0")
check("with notifications off for the whole site the page says so and the menu entry is gone", "The site has notifications switched off" in U1.get(B + "/my/notifications").text and 'href="/my/notifications"' not in U1.get(B + "/my/account").text)
setting("push_enabled", "1")
check("the settings are on the Settings page", all(k in adm.get(B + "/admin/settings").text for k in ("push_kinds_off", "push_watch_seconds", "push_quake_min_mag", "push_places_per_member")))
# ---- phone width
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    o = Options(); o.add_argument("--headless=new"); o.add_argument("--window-size=412,900")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + "/login")
        for c in U1.cookies:
            d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
        d.get(B + "/my/notifications")
        w = d.execute_script("return [document.documentElement.scrollWidth, window.innerWidth]")
        check("the Notifications page fits a 412 px screen", w[0] <= w[1] + 2, w)
    finally:
        d.quit()
except ImportError:
    print("SKIP no selenium")
psql("DELETE FROM users WHERE username LIKE 'pkt%'")
print("ALL PASS" if ok_all else "SOME FAILED")
