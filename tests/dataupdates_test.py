import re, requests, subprocess, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
P = "/admin/data-updates/"
def post(path, data):
    j = adm.post(B + P + path, data=dict(data, csrf_token=tok(adm, P)), headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"]
check("the page needs sign-in", requests.get(B + P, allow_redirects=False).status_code in (302, 401, 403, 404))
check("an ordinary user cannot use it", sess("plain1", "plainpassword1").post(B + P + "places/run", data={}, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
t = adm.get(B + P).text
labels = ["Places", "Airports", "Hospitals", "Trauma Levels and Services", "Hospital Positions Check", "Interesting Aircraft", "Air Ambulances"]
pos = [t.index(f'<span class="role-name">{l}</span>') for l in labels]
check("every source is listed, in the order that suits their dependencies", pos == sorted(pos), pos)
check("each has its schedule, a Run Now button and its last result", t.count("Run Now") >= 18 and t.count("Update every (days, 0 = off)") == 18 and t.count("Next automatic run") == 18)
check("it is in the admin menu", 'href="/admin/data-updates/"' in t)
psql("delete from settings where key in ('places_update_days','hospitals_update_days','hems_update_days','trauma_update_days','trauma_source_url','airports_update_time')")
t = adm.get(B + P).text
check("sensible defaults: Places 365, Hospitals 14, Trauma 7, Air Ambulances 14, Airports 30", all(re.search(rf'id="du-d-{k}" name="days" value="{v}"', t) for k, v in (("places", 365), ("hospitals", 14), ("trauma", 7), ("hems", 14), ("airports", 30))))
ok, m = post("places/schedule", {"days": "30"})
check("a schedule saves and says so", ok and "every 30 days" in m, m)
ok, m = post("places/schedule", {"days": "0"})
check("0 turns it off", ok and "off" in m and "Automatic updates are off." in adm.get(B + P).text)
check("a bad number is refused", not post("places/schedule", {"days": "-1"})[0] and not post("places/schedule", {"days": "abc"})[0] and not post("places/schedule", {"days": "400"})[0])
check("an unknown source is refused", not post("nope/schedule", {"days": "1"})[0])
ok, m = post("time", {"time": "04:15"})
check("the time of day saves", ok and 'value="04:15"' in adm.get(B + P).text, m)
check("a bad time is refused", not post("time", {"time": "25:99"})[0])
post("time", {"time": "03:30"})
check("the same settings are in Settings > Scheduled Updates", all(f'name="{k}"' in adm.get(B + "/admin/settings").text for k in ("places_update_days", "hospitals_update_days", "trauma_update_days", "hems_update_days")))
check("a source address that is not https is refused", not post("trauma-url", {"url": "http://example.com/a.csv"})[0])
ok, m = post("trauma-url", {"url": "https://example.com/trauma.csv"})
check("an https address saves", ok and "https://example.com/trauma.csv" in adm.get(B + P).text, m)
post("trauma-url", {"url": ""})
ok, m = post("trauma/run", {})
check("a source with no address has nothing to do", ok and "started" in m)
for _ in range(30):
    time.sleep(2)
    if not psql("select value from settings where key='trauma_running'"):
        break
check("and says so when it finishes", "Nothing to do" in psql("select value from settings where key='trauma_last_result'"), psql("select value from settings where key='trauma_last_result'"))
ok, m = post("places/run", {})
check("Run Now starts the update in the background", ok and "started" in m, m)
for _ in range(60):
    time.sleep(3)
    if not psql("select value from settings where key='places_running'") and psql("select value from settings where key='places_last_import'"):
        break
res = psql("select value from settings where key='places_last_result'")
check("the update finishes and reports what it loaded", "cities and towns" in res and "ZIP areas" in res, res)
t = adm.get(B + P).text
check("the page shows the result and the last success", "cities and towns" in t and 'class="tag live">ok' in t)
check("the run is in the audit log", "dataupdate.run" in psql("select string_agg(distinct action, ',') from audit_log") and "dataupdate.run_now" in psql("select string_agg(distinct action, ',') from audit_log"))
check("the admin status page includes the data updates check", "Data Updates" in adm.get(B + "/admin/status/data.json").text)
psql("insert into settings (key, value) values ('hems_last_error', 'boom') on conflict (key) do update set value='boom'")
import re as _re
_tok = _re.search(r'CSRF_TOKEN = "([^"]+)"', adm.get(B + "/admin/status").text).group(1)
adm.post(B + "/admin/status/run", headers={"X-CSRFToken": _tok, "X-Requested-With": "XMLHttpRequest"})   # the checks run in the background; an administrator forces a run
check("a failed source shows on the status page", "boom" in adm.get(B + "/admin/status/data.json").text and 'tag off">last try failed' in adm.get(B + P).text)
psql("delete from settings where key='hems_last_error'")
# different sources may run together; the same source, or one it depends on or that depends on it, may not
def run_button(page, key):
    m = re.search(r'action="/admin/data-updates/' + key + r'/run".*?<button[^>]*>Run Now</button>', page, re.S)
    return m.group(0) if m else ""
psql("insert into settings (key, value) values ('hospitals_running', now()::text) on conflict (key) do update set value=now()::text")
page = adm.get(B + P).text
check("the page says what is running", "Running now: Hospitals" in page, re.findall(r"Running now[^<]*", page))
check("the source itself and the ones it uses or that use it wait (Places, Hospitals, Trauma Levels, Positions Check)", all("disabled" in run_button(page, k) for k in ("hospitals", "places", "trauma", "hospverify")), [(k, "disabled" in run_button(page, k)) for k in ("hospitals", "places", "trauma", "hospverify")])
check("unrelated sources can still be started (County Outlines, Airports, Air Ambulances, Highways, Repeaters)", all("disabled" not in run_button(page, k) for k in ("counties", "airports", "hems", "roads", "repeaters")), [(k, "disabled" in run_button(page, k)) for k in ("counties", "airports", "hems", "roads", "repeaters")])
ok, msg = post("hospitals/run", {})
check("the same source cannot be started twice", not ok and "already running" in msg, msg)
ok, msg = post("trauma/run", {})
check("a source that uses its data cannot start while it runs, and the answer says why", not ok and "Hospitals is running" in msg and "linked" in msg, msg)
ok, msg = post("places/run", {})
check("nor the one it uses", not ok and "Hospitals is running" in msg, msg)
psql("update settings set value=(now() - interval '5 hours')::text where key='hospitals_running'")
page = adm.get(B + P).text
check("a running mark older than three hours is a leftover and is ignored", "Running now" not in page and "disabled" not in run_button(page, "hospitals"))
psql("delete from settings where key in ('hospitals_running')")
psql("delete from settings where key in ('places_update_days')")
print("ALL PASS" if ok_all else "SOME FAILED")
