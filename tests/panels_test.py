"""Settings > Map Tiles and Weather > Panels When a Map Opens: the list, filters and legend start closed (the default) or open on every map page, and the setting is on the Settings page."""
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
check("the setting is on the Settings page", 'name="map_panels_open"' in adm.get(B + "/admin/settings").text)
PAGES = {"/live": r"PANELS_OPEN = (true|false)", "/counties/": r"panelsOpen = (true|false)", "/sondes/map": r"panelsOpen = (true|false)", "/weather/map": r"panelsOpen = (true|false)"}
for v, want in (("0", "false"), ("1", "true")):
    setting("map_panels_open", v)
    import time; time.sleep(6)
    for path, rx in PAGES.items():
        m = re.search(rx, requests.get(B + path).text)
        listcall = "listVisible(" + want + ")" in requests.get(B + path).text
        check(f"{path}: panels {'open' if v == '1' else 'closed'} when the setting is {v}", m and m.group(1) == want and listcall, m and m.group(0))
psql("DELETE FROM settings WHERE key='map_panels_open'")
time.sleep(6)
check("with nothing set the default is closed on every map", all((re.search(rx, requests.get(B + p).text) or [None, None])[1] == "false" for p, rx in PAGES.items()))
print("ALL PASS" if ok_all else "SOME FAILED")
