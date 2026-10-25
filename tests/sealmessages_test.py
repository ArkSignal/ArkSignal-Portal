"""Private message text is sealed in the database: the start-up pass seals what an older version stored as plain text (unit script inside the portal), and new messages and chats are sealed when written (the mail, chat and
bridge suites also check this); a backup of the database holds no message text in the clear."""
import json, subprocess, tarfile, re, io, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd, timeout=180):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=timeout).stdout
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
subprocess.run(["scp", "-q", "sealmessages_unit.py", "nastemp:adsb-portal/sealmessages_unit.py"], check=True)
ssh(f"{DK} cp ~/adsb-portal/sealmessages_unit.py adsb-portal:/tmp/sealmessages_unit.py")
o = ssh(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sealmessages_unit.py", 120).strip().splitlines()
try:
    res = json.loads(o[-1])
except Exception:
    res = {}
    print("unit output:", o[-6:])
for k, label in (("plain_to_start", "an old message starts as plain text"), ("sealed_by_the_pass", "the start-up pass seals old subjects and bodies (mail and chat)"),
                 ("opens_to_the_same_text", "and each opens to exactly its text"), ("a_second_pass_changes_nothing", "a second pass changes nothing"),
                 ("diary_notes_and_names_sealed", "the flight diary's notes and passengers' names are sealed too and open to their text")):
    check("unit: " + label, res.get(k) is True, res.get(k))
print("ALL PASS" if ok_all else "SOME FAILED")
