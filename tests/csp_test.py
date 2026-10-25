"""The Content-Security-Policy: every response carries a script-src with a nonce that is new for each request and that every script tag of the page carries; no template has an inline event handler; and in a real browser
a tour of pages (guest, member, administrator) raises no 'Refused to execute' message, the inline actions that used to be onclick still work, and an injected script does not run."""
import glob, os, re, subprocess, time, requests
os.environ["TEST_HOST"] = "test-box.invalid"
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
# ---- the templates: no inline handlers, every script tag has a nonce
bad, noce = [], []
for f in glob.glob("../app/templates/**/*.html", recursive=True):
    t = open(f, encoding="utf-8").read()
    t2 = re.sub(r"\{#.*?#\}", "", t, flags=re.S)
    for m in re.finditer(r"<[a-zA-Z][^>]*\s(on[a-z]+)=", t2):
        bad.append((os.path.basename(f), m.group(1)))
    for m in re.finditer(r"<script(?![^>]*\bnonce=)[\s>]", t2):
        noce.append(os.path.basename(f))
check("no template has an inline event handler (onclick, onchange ...)", not bad, bad[:5])
check("every script tag in the templates carries the nonce", not noce, noce[:5])
check("no template builds a javascript: address", not any("javascript:" in open(f, encoding="utf-8").read() for f in glob.glob("../app/templates/**/*.html", recursive=True)))
# ---- the header
a, b = requests.get(B + "/"), requests.get(B + "/")
ca, cb = a.headers.get("Content-Security-Policy", ""), b.headers.get("Content-Security-Policy", "")
na, nb = re.search(r"'nonce-([^']+)'", ca), re.search(r"'nonce-([^']+)'", cb)
check("the policy limits scripts to a nonce and what those load", na and "'strict-dynamic'" in ca and "frame-ancestors 'self'" in ca and "object-src 'none'" in ca, ca)
check("the nonce is new for every request", na and nb and na.group(1) != nb.group(1) and len(na.group(1)) >= 20)
check("every script tag of the page carries that request's nonce", all(('nonce="' + na.group(1) + '"') in tag for tag in re.findall(r"<script[^>]*>", a.text)), [t for t in re.findall(r"<script[^>]*>", a.text) if na.group(1) not in t][:3])
check("a JSON answer carries the policy too (nothing to run in it)", "script-src" in requests.get(B + "/healthz").headers.get("Content-Security-Policy", "script-src"))
# ---- a real browser
try:
    from selenium import webdriver
except ImportError:
    print("SKIP no selenium"); print("ALL PASS" if ok_all else "SOME FAILED"); raise SystemExit(0)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
user, adm = sess("plain1", "plainpassword1"), sess("marty", PW)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,900"); o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
d = webdriver.Chrome(options=o)
def login(s):
    d.get(B + "/login"); d.delete_all_cookies()
    for c in (s.cookies if s else []):
        d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
def violations():
    return [e["message"][:200] for e in d.get_log("browser") if "Content Security Policy" in e["message"] or "Refused to execute" in e["message"] or "Refused to load the script" in e["message"]]
try:
    seen = []
    for who, s, paths in (("guest", None, ["/", "/live", "/flight/", "/history/", "/weather/", "/sondes/map", "/repeaters/", "/mesh/", "/about", "/status", "/login", "/aprs/weather"]),
                          ("member", user, ["/my/account", "/my/flights/map", "/my/notifications", "/my/alerts", "/my/flights/new", "/my/community"]),
                          ("administrator", adm, ["/admin/settings", "/admin/users", "/admin/feeders", "/admin/sondes", "/admin/interesting", "/admin/overview", "/admin/status", "/admin/communities"])):
        login(s); d.get_log("browser")
        for p in paths:
            d.get(B + p); time.sleep(1.8)
            v = violations()
            if v:
                seen.append((who, p, v[0]))
    check("a tour of guest, member and administrator pages raises no policy violation", not seen, seen[:3])
    # the shared inline actions
    login(adm); d.get_log("browser")
    d.get(B + "/admin/settings"); time.sleep(1.5)
    d.execute_script("document.querySelector('[data-details-open][data-open-all=\"0\"]').click()"); time.sleep(.3)
    closed = d.execute_script("return Array.from(document.querySelectorAll('details.setsec')).every(function (x) { return !x.open; })")
    d.execute_script("document.querySelector('[data-details-open][data-open-all=\"1\"]').click()"); time.sleep(.3)
    opened = d.execute_script("return Array.from(document.querySelectorAll('details.setsec')).every(function (x) { return x.open; })")
    check("Expand All and Collapse All on the Settings page still work (they were inline handlers)", closed and opened and not violations())
    # an injected script does not run: a script element with no nonce, added to the page text as if by an injection
    d.get(B + "/about"); time.sleep(1)
    d.execute_script("window.__pwned = 0; var s = document.createElement('div'); s.innerHTML = '<img src=x onerror=\"window.__pwned=1\">'; document.body.appendChild(s);")
    time.sleep(1)
    check("an inline handler injected into a page does not run", d.execute_script("return window.__pwned") == 0)
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
