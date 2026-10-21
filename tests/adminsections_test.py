"""Every admin page uses the site's folding sections (details.setsec) for its content, not loose panels, and renders (the rule Marty set 2026-10-16).
Notices (a panel with an accent border) are not sections and are allowed."""
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
adm = sess("marty", PW)
PAGES = ("/admin/acars", "/admin/aprs", "/admin/audit", "/admin/feeders", "/admin/mlat", "/admin/signins", "/admin/themes", "/admin/updates", "/admin/weather", "/admin/mesh", "/admin/sondes", "/admin/usage", "/admin/api-keys")
for path in PAGES:
    r = adm.get(B + path)
    if r.status_code == 404:
        print("SKIP", path, "is not available on this box"); continue
    t = r.text
    loose = re.findall(r'<(?:div|form) class="panel[^"]*"(?![^>]*border-color)[^>]*>', t)
    loose = [x for x in loose if "setsec" not in x and "role-card" not in x and "panel tile" not in x]
    title = re.search(r"<title>(.*?)</title>", t, re.S)
    check(f"{path}: opens", r.status_code == 200, r.status_code)
    check(f"{path}: content is in folding sections", "setsec" in t, "")
    check(f"{path}: no loose panels", not loose, loose[:2])
    check(f"{path}: the page title is clean", bool(title) and "<" not in title.group(1).replace("&lt;", ""), title.group(1) if title else None)
print("ALL PASS" if ok_all else "SOME FAILED")
