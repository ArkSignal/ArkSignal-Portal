"""Every admin section starts collapsed: no details element is open when an admin page has loaded (a link to #section may open that one)."""
import sys, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP: selenium not installed"); sys.exit(0)
adm = sess("marty", PW)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    d.get(B + "/"); 
    for c in adm.cookies: d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
    pages = ["/admin/overview", "/admin/settings", "/admin/repeaters", "/admin/airports", "/admin/aprs", "/admin/backups", "/admin/hospitals", "/admin/data-updates/", "/admin/weather", "/admin/sondes"]
    for p in pages:
        d.get(B + p); time.sleep(1.8)
        n = d.execute_script("return Array.from(document.querySelectorAll('main details[open], .content details[open]')).filter(function(x){return !x.closest('.adminnav')}).map(function(x){return x.id||x.querySelector('summary').textContent.trim().slice(0,30)})")
        title = d.execute_script("return document.title")
        check(f"{p}: no section starts open", not n, n)
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
