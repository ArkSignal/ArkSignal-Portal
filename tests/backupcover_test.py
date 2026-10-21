"""Backups and restore stay current with the features: every table in the database belongs to a restore area (app/restorepart.py AREAS) or is TRANSIENT, and a backup holds every table
(it discovers them itself). A new table that is in no area fails here, so it is given one when the feature is added (NEW_SERVICE_CHECKLIST.md)."""
import re, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout
tables = [t.strip() for t in psql("select tablename from pg_tables where schemaname='public' order by 1").splitlines() if t.strip()]
src = open(r"..\app\restorepart.py", encoding="utf-8").read()
areas = src[src.index("AREAS = ["):src.index("TRANSIENT")]
named = set(re.findall(r'"([a-z_0-9]+)"', areas))
trans = set(re.findall(r'"([a-z_]+)"', src[src.index("TRANSIENT"):src.index("\n", src.index("TRANSIENT"))]))
missing = [t for t in tables if t not in named and t not in trans]
check(f"every one of the {len(tables)} tables is in a restore area or is transient", not missing, missing)
adm = sess("marty", PW)
check("the Backups page loads (the restore screens read these areas)", "Restore" in adm.get(B + "/admin/backups").text)
print("ALL PASS" if ok_all else "SOME FAILED")
