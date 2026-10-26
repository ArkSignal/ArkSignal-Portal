"""The small off-site copy right after the nightly backup (it sends that backup: no second dump), and the set-time and by-hand copies that make a snapshot and remove it. Unit script inside the portal."""
import json, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd, timeout=400):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=timeout).stdout
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
subprocess.run(["scp", "-q", "offsiteafter_unit.py", "nastemp:adsb-portal/offsiteafter_unit.py"], check=True)
ssh(f"{DK} cp ~/adsb-portal/offsiteafter_unit.py adsb-portal:/tmp/offsiteafter_unit.py")
o = ssh(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/offsiteafter_unit.py", 600).strip().splitlines()
try:
    res = json.loads(o[-1])
except Exception:
    res = {}
    print("unit output:", o[-8:])
for k, label in (("nothing_due_before_a_nightly_backup_exists", "right after the nightly backup: nothing is due until a nightly backup exists"),
                 ("due_once_the_nightly_backup_exists", "once the nightly backup exists the small copy is due"),
                 ("the_scheduled_copy_sends_the_nightly_backup_itself", "the scheduled copy sends that nightly backup itself (no second dump)"),
                 ("and_it_is_left_in_the_list", "and the nightly backup stays in Available Backups"),
                 ("not_due_again_for_the_same_backup", "the same backup is not sent twice"),
                 ("the_days_between_copies_are_kept", "the days between copies still count"),
                 ("a_newer_nightly_backup_makes_it_due_again", "a newer nightly backup makes it due again"),
                 ("a_copy_by_hand_makes_its_own_snapshot", "a copy started by hand makes its own fresh snapshot"),
                 ("and_removes_it_when_the_run_ends", "and removes it when the run ends, leaving the nightly backups alone"),
                 ("at_a_set_time_it_makes_its_own_snapshot", "at a set time it makes its own snapshot"),
                 ("and_removes_that_too", "and removes that one too")):
    check("unit: " + label, res.get(k) is True, res.get(k))
print("ALL PASS" if ok_all else "SOME FAILED")
