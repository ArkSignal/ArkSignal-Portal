"""The Services section of the home page has a card for every service that is on (not only the few that had hand-made cards): its name, its numbers, a line about it and a link; chat only for someone signed in; a service
that is switched off has no card."""
import re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
for k, v in (("repeater_enabled", "1"), ("mesh_enabled", "1"), ("acars_enabled", "1"), ("aprs_enabled", "1"), ("ais_enabled", "1"), ("rid_enabled", "1"), ("satimg_enabled", "1"), ("radiolists_enabled", "1"), ("chat_enabled", "1")):
    setting(k, v)
psql("DELETE FROM settings WHERE key LIKE 'access_%'")
time.sleep(7)
anon = requests.Session()
user = sess("plain1", "plainpassword1")
def cards(s):
    t = s.get(B + "/").text
    sec = t[t.index('<section class="svc">'):t.index("</section>", t.index('<section class="svc">'))]
    return re.findall(r'<h3><span class="ic"><svg[^>]*>.*?</svg></span>([^<]+)</h3>', sec, re.S), sec
names, sec = cards(anon)
for want in ("Aircraft Tracking", "Coverage", "Feeders", "Repeaters", "Mesh Nodes", "ACARS", "APRS", "River Traffic", "Drones", "Satellites", "County Map", "Trauma Map", "Network Status", "Feed the Network"):
    check(f"the home page has a {want} card", want in names, names)
check("each new card links to its service and carries a line about it", 'href="/mesh/"' in sec and 'href="/repeaters/"' in sec and "Meshtastic nodes around Arkansas" in sec)
check("a card shows the service's numbers when it has them", re.search(r'Repeaters</h3>\s*<div><span class="numl">\d+ listed', sec) is not None, sec[:0])
check("chat is not offered to a guest", "Chat, Messages and Friends" not in names)
check("but is to someone signed in", "Chat, Messages and Friends" in cards(user)[0])
check("the Matrix card (for administrators) is never a home card", "Matrix Chat" not in names and "Matrix Chat" not in cards(user)[0])
setting("mesh_enabled", "0"); time.sleep(7)
check("a service that is switched off has no card", "Mesh Nodes" not in cards(anon)[0])
setting("mesh_enabled", "1")
print("ALL PASS" if ok_all else "SOME FAILED")
