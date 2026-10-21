"""The service registry (app/services.py) is the one place that says what each service adds to the shared pages. For every enabled service this checks that the pages that must show it do:
the home map and the card beside it, the admin Overview, the About page, the Feed menu, the roles, the Service Status checks, and the main menu. A new service that is added to the registry but
missing from one of them fails here; one that is not in the registry at all is caught by the checklist (NEW_SERVICE_CHECKLIST.md)."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
adm = sess("marty", PW)
for k in ("wx_enabled", "sonde_enabled"):
    setting(k, "1")
import time; time.sleep(6)
reg = adm.get(B + "/admin/services.json").json()["services"]
keys = [s["key"] for s in reg]
check("the registry lists weather, radiosondes and the county map", {"weather", "sondes", "counties"} <= set(keys), keys)
home = requests.get(B + "/").text
about = requests.get(B + "/about").text
nowhtml = requests.get(B + "/home/now").text
overview = adm.get(B + "/admin/").text
feedmenu = requests.get(B + "/feed/").text
menu = home
perms = adm.get(B + "/admin/roles").text
status = adm.get(B + "/admin/status").text
from_json = {}
for s in reg:
    k = s["key"]
    check(f"{k}: the About page has its line", s["about"] is None or (s["about"][0] in about and s["about"][1] in about), s["about"])
    if s["feed"]:
        check(f"{k}: the Feed menu has {s['feed'][0]}", 'href="' + s["feed"][1] + '"' in feedmenu, s["feed"])
    if s["permission"]:
        check(f"{k}: its permission {s['permission']} is a role permission", ('value="' + s["permission"] + '"') in perms, s["permission"])
    if s["status_key"]:
        check(f"{k}: Service Status has its check", ("status-" + s["status_key"]) in status or s["label"].split()[0] in status or s["status_key"] in status, s["status_key"])
    for ly in (s["home_layers"] or []):
        check(f"{k}: the home map has the layer {ly['label']} ({len(ly['points'])} points)", ly["label"] in home and f'"key": "{ly["key"]}"' in home.replace('"key":"', '"key": "'), ly["label"])
    for b in ([s["home_now"]] if s["home_now"] else []):
        check(f"{k}: the card beside the home map has {b['title']} with all its rows", b["title"] in home and b["title"] in nowhtml and all(r[0] in nowhtml for r in b["rows"]), b["title"])
    for t in (s["admin_tiles"] or []):
        check(f"{k}: the admin Overview has the tile {t[0]}", t[0] in overview, t[0])
    for d in (s["admin_data"] or []):
        check(f"{k}: the admin Overview lists the data {d[0]}", d[0] in overview, d[0])
    for a in (s["admin_attention"] or []):
        check(f"{k}: the admin Overview flags: {a[1][:40]}", a[1][:30] in overview, a[1])
# the main menu has a group for every service that has pages of its own
check("the main menu has Weather and Radiosondes groups when those services are on", ">Weather</a>" in menu and ">Radiosondes</a>" in menu)
# a service that is switched off leaves every shared page
setting("sonde_enabled", "0"); time.sleep(6)
home2 = requests.get(B + "/").text
check("with radiosondes off, the home map, the card and the Feed menu no longer show them", "Radiosondes Right Now" not in requests.get(B + "/home/now").text and "Sonde Receiving Stations" not in home2 and 'href="/feed/sondes"' not in requests.get(B + "/feed/").text)
setting("sonde_enabled", "1")
print("ALL PASS" if ok_all else "SOME FAILED")
