"""The Code Plug Export page and its downloads, over HTTP, plus the in-container checks (plugexport_unit.py)."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout
out = ssh("echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker cp ~/adsb-portal/tests/plugexport_unit.py adsb-portal:/tmp/plugexport_unit.py; echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/plugexport_unit.py")
check("plugexport_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-700:])
p = requests.get(B + "/repeaters/export")
check("the page opens for a visitor with the filters, the downloads and the field table", p.status_code == 200 and all(x in p.text for x in ("Code Plug Export", 'name="band"', 'name="mode"', "What Goes In Each DM32 Field", "TX Contact")))
check("the repeater list links to it", "/repeaters/export" in requests.get(B + "/repeaters/").text)
for name, head in (("dm32-channels.csv", "No.,Channel Name,Channel Type,RX Frequency[MHz]"), ("dm32-talkgroups.csv", "No.,Name,ID,Type"), ("chirp.csv", "Location,Name,Frequency,Duplex"), ("rtsystems.csv", "Receive Frequency,Transmit Frequency")):
    r = requests.get(B + "/repeaters/export/" + name, params={"state": "AR", "band": ["2 m", "70 cm"]})
    check(f"{name} downloads as a CSV with the right header", r.status_code == 200 and r.text.startswith(head) and "attachment" in r.headers.get("Content-Disposition", ""), (r.status_code, r.text[:80]))
check("an unknown file is a 404", requests.get(B + "/repeaters/export/nothing.csv").status_code == 404)
check("a bad band or mode in the address is ignored, not an error", requests.get(B + "/repeaters/export", params={"band": "9 km", "mode": "XX", "lat": "abc"}).status_code == 200)
print("ALL PASS" if ok_all else "SOME FAILED")
