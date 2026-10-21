import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
adm = sess("marty", PW)
for path, mime, minsize in (("/favicon.ico", "image/x-icon", 500), ("/favicon.svg", "image/svg+xml", 200), ("/favicon-32.png", "image/png", 100), ("/favicon-16.png", "image/png", 50),
                            ("/apple-touch-icon.png", "image/png", 500), ("/icon-192.png", "image/png", 500), ("/icon-512.png", "image/png", 1000)):
    r = requests.get(B + path)
    check(f"{path} is served as {mime}", r.status_code == 200 and r.headers.get("Content-Type", "").startswith(mime) and len(r.content) >= minsize, (r.status_code, r.headers.get("Content-Type"), len(r.content)))
png = requests.get(B + "/icon-512.png").content
check("the large icon is a real 512 pixel PNG", png[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(png[16:20], "big") == 512 and int.from_bytes(png[20:24], "big") == 512)
m = requests.get(B + "/site.webmanifest")
check("the web manifest names the site and lists icons", m.status_code == 200 and m.json()["name"] and len(m.json()["icons"]) == 2 and m.json()["theme_color"] == "#070d17")
h = requests.get(B + "/").text
check("every page points to the icon, the touch icon and the manifest", all(x in h for x in ('rel="icon" href="/favicon.svg"', 'rel="apple-touch-icon"', 'rel="manifest"', 'name="theme-color"')))
pages = ["/", "/aircraft", "/live", "/flight/", "/history/", "/feeders/", "/feed/", "/feed/check", "/airports/", "/hospitals/", "/coverage", "/status", "/about", "/contact", "/release-notes", "/roadmap", "/mlat/", "/my/login", "/my/register"]
adminp = ["/admin/", "/admin/users", "/admin/feeders", "/admin/settings", "/admin/updates", "/admin/data-updates/", "/admin/hospitals", "/admin/air-ambulance", "/admin/places", "/admin/airports", "/admin/backups", "/admin/tools", "/admin/layers", "/admin/roles", "/admin/audit", "/admin/status", "/admin/smtp", "/admin/contacts", "/my/", "/my/profile", "/my/alerts"]
bad = []
for s, plist in ((requests, pages), (adm, adminp)):
    for p in plist:
        r = s.get(B + p)
        t = re.search(r"<title>(.*?)</title>", r.text, re.S)
        if r.status_code == 200 and (not t or "<" in t.group(1) or "\n" in t.group(1).strip() or len(t.group(1)) > 80):
            bad.append((p, (t.group(1)[:60] if t else None)))
check("every page title is a short plain name with no markup in it", not bad, bad)
u = adm.get(B + "/admin/updates").text
body = u.split("</head>", 1)[1]
check("the Live Content section is in the page itself, not in the title", 'id="sec-live-content"' in body and "<title>Updates | " in u)
print("ALL PASS" if ok_all else "SOME FAILED")
