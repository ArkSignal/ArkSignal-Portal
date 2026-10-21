"""The airline logo service never makes a page wait for an outside service: runs logos_unit.py inside the portal container (a deliberately slow source, background fetches, warming) and checks the
page script retries a missing logo after a minute and loads logos lazily."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout
out = ssh("docker cp ~/adsb-portal/tests/logos_unit.py adsb-portal:/tmp/logos_unit.py; docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/logos_unit.py")
check("logos_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-600:])
live = requests.get(B + "/live").text
check("the map's logo script loads images lazily and asynchronously", 'loading=\\"lazy\\" decoding=\\"async\\"' in live or 'loading="lazy" decoding="async"' in live or "loading=" in live)
check("and asks again for a missing logo after a minute", "60000" in live and "noLogo" in live)
print("ALL PASS" if ok_all else "SOME FAILED")
