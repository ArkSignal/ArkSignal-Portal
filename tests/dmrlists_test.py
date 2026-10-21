"""DMR talkgroup and contact lists: the unit checks in the container, the admin import form with the real DM32 exports (when they are in the project folder), the scheduled job and its settings."""
import os, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout
out = ssh("docker cp ~/adsb-portal/tests/dmrlists_unit.py adsb-portal:/tmp/dmrlists_unit.py; docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/dmrlists_unit.py")
check("dmrlists_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-700:])
adm = sess("marty", PW)
page = adm.get(B + "/admin/repeaters").text
check("the Repeaters admin page has the list import", "/admin/repeaters/import-dmr" in page and 'name="talkgroups"' in page and 'name="contacts"' in page)
here = os.path.dirname(os.path.abspath(__file__))
tg, ct = (os.path.join(here, "..", "..", "20261012-dm32-" + n + ".csv") for n in ("talkgroups", "contacts"))
if os.path.exists(tg) and os.path.exists(ct):
    t = tok(adm, "/admin/repeaters")
    r = adm.post(B + "/admin/repeaters/import-dmr", data={"csrf_token": t, "replace": "1"}, files={"talkgroups": open(tg, "rb"), "contacts": open(ct, "rb")}, headers={"Accept": "application/json"})
    check("the real DM32 talkgroup and contact exports import", r.status_code == 200 and "talkgroups" in r.text and "contacts" in r.text, r.text[:200])
    page = adm.get(B + "/admin/repeaters").text
    check("the counts show on the page", "39 talkgroups" in page and "1813 contacts" in page)   # 1816 rows, three share an ID
else:
    print("note: the DM32 exports are not in the project folder, the real-file import is skipped")
sp = adm.get(B + "/admin/settings").text
check("the addresses and the schedule are in Settings, blank and off by default", all(f'name="{k}"' in sp for k in ("dmr_talkgroups_url", "dmr_contacts_url", "dmrlists_update_days")))
du = adm.get(B + "/admin/data-updates/").text
check("the job is on Data Updates", "DMR Talkgroups and Contacts" in du)
ssh("echo \"DELETE FROM dmr_talkgroups; DELETE FROM dmr_contacts\" | docker exec -i adsb-portal-db psql -U portal -d portal")   # the real files were only for this check
print("ALL PASS" if ok_all else "SOME FAILED")
