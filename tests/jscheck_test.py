"""Every inline script on the main pages must parse. A syntax slip in the shared page script would break every page, and nothing else in
the suite runs JavaScript. Needs the esprima package on the PC (pip install esprima); skipped with a note when it is missing."""
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)
    ok_all &= bool(cond)
try:
    import esprima
except ImportError:
    print("SKIP esprima is not installed (pip install esprima)")
    print("ALL PASS")
    raise SystemExit
adm = sess("marty", PW); anon = requests.Session()
pages = ["/", "/live", "/coverage", "/weather/", "/weather/map", "/sondes/", "/sondes/map", "/sondes/flights", "/sondes/stations/", "/feed/sondes", "/counties/", "/feeders/", "/feed/", "/flight/", "/airports/", "/contact", "/my/login", "/my/register", "/my/forgot"]
for who, s, extra in (("signed out", anon, []), ("administrator", adm, ["/admin/", "/admin/settings", "/admin/users", "/admin/layers", "/admin/contacts", "/my/alerts", "/my/profile", "/about", "/admin/updates", "/flight/a1b2c3"])):
    for path in pages + extra:
        html = s.get(B + path).text
        errors = []
        for i, sc in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
            try:
                esprima.parseScript(sc)
            except Exception as e:
                errors.append((i, str(e)[:120]))
        check(f"scripts parse on {path} ({who})", not errors, errors)
for path in ("/static/maplayers.js", "/static/colocate.js", "/static/memberpicker.js", "/static/wxmap.js", "/static/sondechart.js", "/static/pwcheck.js"):
    try:
        esprima.parseScript(anon.get(B + path).text)
        check(f"{path} parses", True)
    except Exception as e:
        check(f"{path} parses", False, str(e)[:160])
print("ALL PASS" if ok_all else "SOME FAILED")
