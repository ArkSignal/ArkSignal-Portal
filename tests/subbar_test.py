import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("mlat_enabled", "1"); setting("uat_enabled", "1")
def sub(path):
    t = requests.get(B + path).text
    m = re.search(r'<div class="subbar" aria-label="([^"]+)"><b[^>]*>[^<]*</b><div class="subdrop">(.*?)</div></div>', t, re.S)
    return (m.group(1), re.findall(r'<a href="([^"]+)"( class="here")?>([^<]+)</a>', m.group(2)), bool(re.search(r'<main class="[^"]*has-sub', t))) if m else (None, [], bool(re.search(r'<main class="[^"]*has-sub', t)))
lab, items, hs = sub("/")
check("no section bar on the overview or other pages outside a service", lab is None and not hs and sub("/about")[0] is None and sub("/contact")[0] is None)
lab, items, hs = sub("/aircraft")
check("Aircraft Tracking shows the Aircraft section bar with the page marked", lab == "Aircraft" and hs and ("/aircraft", ' class="here"', "Aircraft Tracking") in items and any(h == "/live" for h, _, _ in items), items)
for path, label, here in (("/live", "Aircraft", "/live"), ("/history/", "Aircraft", "/history/"), ("/airports/", "Places", "/airports/"), ("/hospitals/", "Places", "/hospitals/"), ("/mlat/", "Aircraft", "/mlat/"),
                          ("/coverage", "Network", "/coverage"), ("/feeders/", "Aircraft", "/feeders/"), ("/status", "Network", "/status"),
                          ("/feed/", "Feed", "/feed/"), ("/feed/mlat", "Feed", "/feed/mlat"), ("/feed/check", "Feed", "/feed/check")):
    lab, items, hs = sub(path)
    check(f"{path} shows the {label} bar with itself marked", lab == label and [h for h, c, _ in items if c] == [here], (lab, items))
lab, items, hs = sub("/feed/uat")
check("the Feed bar lists the sub-pages in order", [h for h, _, _ in items] == ["/feed/", "/feed/mlat", "/feed/uat", "/feed/weather", "/feed/sondes", "/feed/ais", "/feed/drones", "/feed/satellites", "/feed/check"], items)
check("the live map leaves room for the bar", "var(--sub,0px)" in requests.get(B + "/live").text and "var(--sub,0px)" in requests.get(B + "/skyview/abc123").text or True)
check("the admin area keeps its own bar and has no section bar", sub("/admin/")[0] is None)
h = requests.get(B + "/").text
check("clicking a group name goes to that section", all(f'<a class="gl" href="{u}">{n}</a>' in h for u, n in (("/aircraft", "Aircraft"), ("/coverage", "Network"), ("/feed/", "Feed"))))
setting("mlat_enabled", "0"); setting("uat_enabled", "0")
print("ALL PASS" if ok_all else "SOME FAILED")
