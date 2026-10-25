"""Owner alerts for mesh nodes: the checking and sending rules (a script run inside the portal container with a made-up node list), and the member's page, the administrators' summary, the settings and the restore area. Needs the test box."""
import json, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
SSH = lambda cmd: subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300)
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def psql(sql):
    return SSH(f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").stdout.strip()
subprocess.run(["scp", "-q", "meshalerts_unit.py", "nastemp:adsb-portal/meshalerts_unit.py"], check=True, capture_output=True)
SSH(f"{DK} cp ~/adsb-portal/meshalerts_unit.py adsb-portal:/tmp/meshalerts_unit.py")
o = SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/meshalerts_unit.py").stdout.strip().splitlines()
try:
    res = json.loads(o[-1])
except Exception:
    res = {}
    print("unit output:", o[-5:])
for k in ("first_pass", "no_repeat_inside_cooldown", "repeat_after_cooldown", "stops_after_most_repeats", "unlisted_node_is_left_alone", "battery_hysteresis_and_back", "router_back_note", "pref_off_is_respected", "switch_off_stops_everything",
          "not_sent_before_the_combine_wait", "one_combined_email", "marked_sent_and_not_sent_twice", "daily_limit_holds_the_rest", "without_email_set_up_it_is_listed_only"):
    check("owner alerts: " + k.replace("_", " "), res.get(k) is True, res.get(k))
adm = sess("marty", PW); me = sess("plain1", "plainpassword1")
psql("INSERT INTO settings (key, value) VALUES ('mesh_enabled','1') ON CONFLICT (key) DO UPDATE SET value='1'")
page = me.get(B + "/my/mesh").text
check("My Mesh Nodes has a Node Alerts section with the choices and the limits in plain words", 'id="sec-node-alerts"' in page and 'name="quiet"' in page and 'name="by_webhook"' in page and "hours" in page.split("sec-node-alerts")[1])
t = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
r = me.post(B + "/my/mesh/alerts/save", data={"csrf_token": t, "quiet": "1", "recovered": "1"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("saving the choices works and the audit log records it", r.status_code == 200 and psql("select battery, by_email, quiet from mesh_alert_prefs mp join users u on u.id = mp.user_id where u.username = 'plain1'") == "f|f|t" and "mesh.alert_prefs" in psql("select action from audit_log order by id desc limit 3"))
page = me.get(B + "/my/mesh").text
check("the saved choices come back", re.search(r'name="quiet"[^>]*checked', page) and not re.search(r'name="battery"[^>]*checked', page))
psql("DELETE FROM mesh_alert_prefs")
check("the administrators' mesh page shows the Owner Alerts summary", "Owner Alerts" in adm.get(B + "/admin/mesh").text)
st = adm.get(B + "/admin/settings/radio").text
check("every tunable is in Settings > Mesh Nodes", all(x in st for x in ("Owner Alerts for Mesh Nodes", "Owner Alerts: Check Every (Seconds)", "Owner Alerts: Repeat Every (Hours)", "Owner Alerts: Most Repeats", "Owner Alerts: Battery Recovery Margin", "Owner Alerts: Combine Messages", "Owner Alerts: Most Messages per Person per Day", "Owner Alerts: Keep the List for")))
check("the new tables are in the mesh restore area", all(t in open("../app/restorepart.py", encoding="utf-8").read().split('("mesh",')[1].split("\n")[0] for t in ("mesh_alert_prefs", "mesh_alert_state", "mesh_alert_events", "mesh_alert_sends")))
print("ALL PASS" if ok_all else "SOME FAILED")
