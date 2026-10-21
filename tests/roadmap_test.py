"""The two roadmaps through the web: Admin > Full Roadmap is in the admin menu and opens for an administrator, the public one does not link to it, and the in-container unit checks (roadmap_unit.py)."""
import subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout
out = ssh("docker cp ~/adsb-portal/tests/roadmap_unit.py adsb-portal:/tmp/roadmap_unit.py; docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/roadmap_unit.py")
check("roadmap_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-600:])
adm = sess("marty", PW)
r = adm.get(B + "/admin/roadmap")
check("an administrator opens Admin > Full Roadmap", r.status_code == 200 and "Full" in r.text and "Roadmap" in r.text)
check("it is in the admin menu", 'href="/admin/roadmap"' in adm.get(B + "/admin/").text)
check("the public Roadmap page does not link to it", "/admin/roadmap" not in requests.get(B + "/roadmap").text)
print("ALL PASS" if ok_all else "SOME FAILED")
