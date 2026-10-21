"""All My Stations: one page of everything a member runs, by service, with counts, and the services that are switched off are left out."""
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    import subprocess
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
check("a visitor is sent to sign in", requests.get(B + "/my/stations", allow_redirects=False).status_code in (302, 401))
u = sess("plain1", "plainpassword1")
pg = u.get(B + "/my/stations")
check("the page opens with a card for feeders, weather stations and radiosonde stations", pg.status_code == 200 and all(f'id="ms-{k}"' in pg.text for k in ("feeders", "weather", "sondes")))
check("the card for a service that is off is left out (ACARS and mesh are off by default)", 'id="ms-acars"' not in pg.text and 'id="ms-mesh"' not in pg.text)
setting("acars_enabled", "1"); setting("mesh_enabled", "1")
import time; time.sleep(3)
pg = u.get(B + "/my/stations")
check("and they appear once the service is on", 'id="ms-acars"' in pg.text and 'id="ms-mesh"' in pg.text)
check("every card has a count and a link to its own page", re.findall(r'class="tv"[^>]*>(\d+)<', pg.text) and all(x in pg.text for x in ('href="/my/"', 'href="/my/weather"', 'href="/my/sondes"', 'href="/my/acars"', 'href="/my/mesh"')))
check("My Feeders and the other My pages link to it", 'href="/my/stations"' in u.get(B + "/my/").text and 'href="/my/stations"' in u.get(B + "/my/weather").text)
setting("acars_enabled", "0"); setting("mesh_enabled", "0")
print("ALL PASS" if ok_all else "SOME FAILED")
