"""Radiosonde station coverage from real flights: the in-container check, and the job on Data Updates."""
import subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout
out = ssh("docker cp ~/adsb-portal/tests/sondecoverage_unit.py adsb-portal:/tmp/sondecoverage_unit.py; docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sondecoverage_unit.py")
check("sondecoverage_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-800:])
check("the job is on Data Updates", "Radiosonde Station Coverage" in sess("marty", PW).get(B + "/admin/data-updates/").text)
print("ALL PASS" if ok_all else "SOME FAILED")
