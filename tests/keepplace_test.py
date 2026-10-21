"""A form that reloads the page keeps the scroll position and the sections that were open (no jump to the top)."""
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
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,700")
d = webdriver.Chrome(options=o)
try:
    d.get(B + "/"); 
    for c in adm.cookies: d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
    d.get(B + "/admin/repeaters"); time.sleep(2)
    d.execute_script("var s=Array.from(document.querySelectorAll('main details.setsec')); s.forEach(function(x){x.setAttribute('open','')}); window.scrollTo(0, document.body.scrollHeight)")
    y0 = d.execute_script("return window.scrollY")
    d.execute_script("var f=Array.from(document.querySelectorAll('form[method=post]')).filter(function(x){return !x.hasAttribute('data-async') && x.action.indexOf('/admin/repeaters/add')>=0})[0]; f.noValidate=true; f.requestSubmit()"); time.sleep(3)
    y1 = d.execute_script("return window.scrollY"); op = d.execute_script("var s=document.querySelectorAll('details.setsec'); return s[s.length-1].hasAttribute('open')")
    check("after a form reloads the page the scroll position is kept", y0 > 200 and abs(y1 - y0) < 60, (y0, y1))
    check("and the section that was open is still open", op)
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
