"""Checking a new site address before it is saved: the site's own identifier is public and stable, a Main Address that does not resolve, does not answer over https or answers from another server is refused with a
plain sentence (the old value stays), the Feed Address is checked for DNS, and an unchanged address saves. Runs against the test portal (the 'another server' check needs the internet from the test box)."""
import re, socket, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
a = requests.get(B + "/.well-known/arksignal.json"); b = requests.get(B + "/.well-known/arksignal.json")
check("the site publishes its own identifier, public and the same every time", a.status_code == 200 and re.match(r"^[0-9a-f]{16}$", a.json()["id"]) and a.json()["id"] == b.json()["id"] and a.json()["site"], a.text)
BASE = dict(site_name="ArkSignal", registration_open="1", default_privacy="general", feed_mode="open", map_lat="34.80", map_lon="-92.30", map_zoom="7", map_zoom_page="8", timezone="America/Chicago", max_failed_attempts="5", lockout_minutes="15", history_retention_days="730", sample_retention_days="30", feed_host="feed.arksignal.allisononline.org", feed_port="30004", mlat_port="31090", max_rules_per_user="25")
def save(**kw):
    ok, msg, _ = act(adm, "/admin/settings/save", "/admin/settings", **dict(BASE, **kw))
    return ok, msg
cur = psql("select value from settings where key = 'main_address'")
ok, msg = save(main_address="no-such-host-zz.invalid")
check("a Main Address that does not resolve is refused and says to add the DNS record", "does not resolve" in msg and psql("select value from settings where key = 'main_address'") == cur, msg[:200])
try:
    socket.gethostbyname("example.com"); net = True
except OSError:
    net = False
if net:
    ok, msg = save(main_address="example.com")
    check("a name that answers from another server is refused (not this site) and nothing is saved", ("not this site" in msg or "different server" in msg or "not as this site" in msg) and psql("select value from settings where key = 'main_address'") == cur, msg[:200])
else:
    print("SKIP the other-server check: the test box has no internet")
ok, msg = save(feed_host="no-such-feed-zz.invalid")
check("a Feed Address that does not resolve is refused", "does not resolve" in msg and psql("select value from settings where key = 'feed_host'") in ("", "feed.arksignal.allisononline.org"), msg[:200])
ok, msg = save(main_address=cur)
check("saving with the address unchanged is not held up by the check", ok and "does not" not in msg, msg[:200])
st = adm.get(B + "/admin/settings").text
check("the settings page says a new address is checked first and has the wait setting", "checked before it is saved" in st and "Address Check: Seconds to Wait" in st)
print("ALL PASS" if ok_all else "SOME FAILED")
