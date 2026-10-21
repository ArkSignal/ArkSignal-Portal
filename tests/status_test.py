"""Service Status: the page opens at once and the states are filled in from the last background run; checks run on a schedule in the background; only an administrator can force a run (Admin > Service Status > Check Now)."""
import re, requests, subprocess, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", "docker exec -i adsb-portal-db psql -U portal -d portal -tA"], input=sql, capture_output=True, text=True, timeout=60).stdout.strip()
adm = sess("marty", PW); plain = sess("plain1", "plainpassword1")
ajax = {"X-Requested-With": "XMLHttpRequest"}
t0 = time.time(); pub = requests.get(B + "/status"); dt = time.time() - t0
check("the public page loads for guests, at once, with no check names in it (they are filled in afterwards)", pub.status_code == 200 and "Service" in pub.text and "/api/status.json" in pub.text and "Website and Accounts" not in pub.text.split("<script>")[0] and dt < 2.0, (pub.status_code, round(dt, 2)))
check("it has no Refresh link (checks run in the background)", ">Refresh<" not in pub.text and 'href="">Refresh' not in pub.text)
adm.post(B + "/admin/status/run", data={}, headers=dict(ajax, **{"X-CSRFToken": re.search(r'CSRF_TOKEN = "([^"]+)"', adm.get(B + "/admin/status").text).group(1)}))
api = requests.get(B + "/api/status.json").json()
check("the JSON status has an overall state and services", api["overall"] in ("ok", "warn", "down") and len(api["services"]) >= 6 and api.get("at") and api.get("every"), api)
names = " | ".join(s["name"] for s in api["services"])
check("it names the main services in plain words", all(w in names for w in ("Website and Accounts", "Aircraft Data", "Station Tracking", "Flight History", "MLAT", "UAT")), names)
check("the public data does not show administrator detail", "Stack Updater" not in names and "Backups" not in names and all(set(s) == {"name", "state", "summary", "group"} for s in api["services"]) and all("detail" not in c for c in api["checks"]))
check("the footer links to Status", 'href="/status"' in pub.text)
check("the address search and Planefence are listed", "Address Search (Photon)" in names and "Planefence" in names)
srcs = [s for s in api["services"] if s["group"] == "sources"]
check("there is a section of outside data sources with the main ones named, each saying if it is reachable", all(w in " | ".join(s["name"] for s in srcs) for w in ("OurAirports", "US Census Locator", "CMS Hospital List", "OpenStreetMap Map Tiles", "Aviation Weather", "Plane-Alert Database")) and any("Reachable" in s["summary"] for s in srcs))
check("the public data shows no addresses or timings of the sources", all("answered in" not in s["summary"] and "https://" not in s["summary"] for s in srcs))
check("rows are marked services or sources", {s["group"] for s in api["services"]} == {"services", "sources"})
check("the admin page needs sign-in and an ordinary user cannot open it or its data", all(c in (302, 401, 403, 404) for c in (requests.get(B + "/admin/status", allow_redirects=False).status_code, plain.get(B + "/admin/status", allow_redirects=False).status_code, plain.get(B + "/admin/status/data.json", allow_redirects=False).status_code, requests.get(B + "/admin/status/data.json", allow_redirects=False).status_code)))
ad = adm.get(B + "/admin/status/data.json").json()
an = " | ".join(c["name"] for c in ad["checks"])
check("the admin data adds the in-depth rows, the reasons, the timings and the metrics database", all(w in an for w in ("Stack Updater", "Backups", "Disk Space", "Updates", "Airport Data", "Metrics Database")) and any("answered in" in c.get("detail", "") for c in ad["checks"]) and any("Database answered in" in c.get("detail", "") for c in ad["checks"]))
page = adm.get(B + "/admin/status").text
check("the admin page has the Check Now button", 'id="st-now"' in page)
check("a visitor and an ordinary user cannot force a run", requests.post(B + "/admin/status/run", allow_redirects=False).status_code in (302, 400, 401, 403, 404) and plain.post(B + "/admin/status/run", headers=ajax, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
# an outside source that stops answering: shows after the next run, only degrades the picture
psql("insert into settings (key, value) values ('plane_alert_url', 'https://no-such-host.invalid/x.csv') on conflict (key) do update set value=excluded.value")
tok = re.search(r'CSRF_TOKEN = "([^"]+)"', page).group(1)
adm.post(B + "/admin/status/run", headers={"X-CSRFToken": tok, "X-Requested-With": "XMLHttpRequest"})
bad = requests.get(B + "/api/status.json").json()
check("an outside source that does not answer shows as not working after a run, and only degrades the picture", any("Not answering right now" in s["summary"] for s in bad["services"] if s["group"] == "sources") and (bad["overall"] != "ok"), bad["message"])
psql("delete from settings where key='plane_alert_url'")
adm.post(B + "/admin/status/run", headers={"X-CSRFToken": tok, "X-Requested-With": "XMLHttpRequest"})
check("it recovers after the next run once the address answers", not any("Not answering right now" in s["summary"] for s in requests.get(B + "/api/status.json").json()["services"] if s["group"] == "sources"))
# the background schedule
psql("insert into settings (key, value) values ('status_check_seconds', '15') on conflict (key) do update set value='15'")
a0 = requests.get(B + "/api/status.json").json()["at"]
time.sleep(40)
a1 = requests.get(B + "/api/status.json").json()["at"]
psql("delete from settings where key='status_check_seconds'")
check("the checks run on their own on a schedule (no one opened a page to cause it)", a1 > a0, (a0, a1))
check("Service Status has its schedule in Settings", "status_check_seconds" in adm.get(B + "/admin/settings").text)
print("ALL PASS" if ok_all else "SOME FAILED")
