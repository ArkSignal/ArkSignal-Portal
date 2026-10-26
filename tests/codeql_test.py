"""Fixes made after the code scanning review: the unit script inside the portal, and the pages that changed (two-factor set-up draws its own QR code and loads nothing from another site)."""
import json, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd, timeout=180):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=timeout).stdout
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
subprocess.run(["scp", "-q", "codeql_unit.py", "nastemp:adsb-portal/codeql_unit.py"], check=True)
ssh(f"{DK} cp ~/adsb-portal/codeql_unit.py adsb-portal:/tmp/codeql_unit.py")
o = ssh(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/codeql_unit.py", 180).strip().splitlines()
try:
    res = json.loads(o[-1])
except Exception:
    res = {}
    print("unit output:", o[-8:])
for k, label in (("the_address_check_refuses_a_loopback_name", "the Main Address check does not contact a loopback or private address"),
                 ("a_backup_name_cannot_leave_the_folder", "a backup name cannot point outside the backup folder"),
                 ("a_bad_repository_name_is_refused", "an image repository name that is not owner/name is refused before any request"),
                 ("a_track_with_an_entity_trick_is_refused_and_a_normal_one_is_read", "a track file with an entity trick is refused and a normal GPX is read"),
                 ("very_long_input_is_handled_at_once", "very long typed input is cut before any pattern runs"),
                 ("a_backup_column_that_is_not_the_tables_is_refused", "a restore refuses a column name that the table does not have"),
                 ("email_check_answers_as_before", "the hand-written email check accepts and refuses the same addresses as the pattern did"),
                 ("distance_check_answers_as_before", "the hand-written distance reader accepts and refuses the same typing"),
                 ("place_search_with_a_state_still_narrows", "a place search with a state still works"),
                 ("users_are_still_there", "and nothing was damaged")):
    check("unit: " + label, res.get(k) is True, res.get(k))
print("ALL PASS" if ok_all else "SOME FAILED")
