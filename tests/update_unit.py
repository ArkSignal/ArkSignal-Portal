"""Runs inside the container: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/update_unit.py"""
from app.update_check import latest_stable_version, _version_tuple
ok_all = True
def check(name, cond):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name); ok_all &= bool(cond)
def tag(name, digest): return {"name": name, "images": [{"digest": digest}]}
tags = [tag("latest", "sha:new"), tag("2026.10.05", "sha:new"), tag("stable", "sha:a"), tag("v1.0.0", "sha:old"), tag("v1.2.0", "sha:a"), tag("v1.10.0", "sha:a"), tag("v1.3.0", "sha:zzz")]
check("the version that shares stable's digest wins, compared as numbers (1.10.0 over 1.2.0)", latest_stable_version(tags) == "1.10.0")
check("no stable tag means no answer", latest_stable_version([tag("latest", "x"), tag("v1.0.0", "x")]) is None)
check("stable with no version tag means no answer", latest_stable_version([tag("stable", "x"), tag("latest", "x")]) is None)
check("version parsing", _version_tuple("1.2.3") == (1, 2, 3) and _version_tuple("1.2") is None and _version_tuple("unreleased") is None)
print("ALL PASS" if ok_all else "SOME FAILED")
