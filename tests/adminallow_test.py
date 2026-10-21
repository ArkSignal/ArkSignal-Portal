"""Admin area allow-list: the logic (adminallow_unit.py in the portal container) and the Settings > Security box (a list that leaves the saver out is refused; one that includes them is saved and the Admin area still opens)."""
import re, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def ssh(c): return subprocess.run(["ssh", "-q", "nastemp", c], capture_output=True, text=True, timeout=120).stdout
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
subprocess.run(["scp", "-q", "adminallow_unit.py", "nastemp:adsb-portal/adminallow_unit.py"], check=True)
out = ssh("cd ~/adsb-portal && " + D + "cp adminallow_unit.py adsb-portal:/tmp/adminallow_unit.py; " + D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/adminallow_unit.py")
check("adminallow_unit.py passes inside the portal container", "ALL PASS" in out, out[-600:])
adm = sess("marty", PW)
st = adm.get(B + "/admin/settings").text
check("Settings > Security has the Allowed Addresses box", 'name="admin_allow_addresses"' in st and "Admin Area: Allowed Addresses" in st)
class R:
    def __init__(self, ok, msg): self.ok, self.text, self.status_code = ok, msg, 200
def save(value):
    base = dict(site_name="ArkSignal", registration_open="1", default_privacy="general", feed_mode="open", map_lat="34.80", map_lon="-92.30", map_zoom="7", map_zoom_page="8", timezone="America/Chicago", max_failed_attempts="5", lockout_minutes="15", history_retention_days="730", sample_retention_days="30", feed_host="feed.arksignal.allisononline.org", feed_port="30004", mlat_port="31090", max_rules_per_user="25")
    ok, msg, _ = act(adm, "/admin/settings/save", "/admin/settings", admin_allow_addresses=value, **base)
    return R(ok, msg)
r = save("203.0.113.0/24")
check("a list that leaves the person who saves it out is refused", "lock you out" in r.text, r.text[:200])
r = save("not an address")
check("an unreadable list is refused", "not addresses" in r.text or "not an address" in r.text, r.text[:200])
r = save("private")
check("a list that includes the saver is saved", r.ok and "saved" in r.text.lower(), r.text[:200])
check("and the Admin area still opens for them", adm.get(B + "/admin/settings").status_code == 200)
save("")
check("it is emptied again", adm.get(B + "/admin/settings").status_code == 200 and 'name="admin_allow_addresses"' in adm.get(B + "/admin/settings").text)
print("ALL PASS" if ok_all else "SOME FAILED")
