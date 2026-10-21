"""Long admin pages are split into tabs (one tab bar, one tab shown at a time, a #tab-... link opens a tab, the page the person was on stays on its tab): Radiosondes, Hospitals."""
import os, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
adm = sess("marty", PW)
s = adm.get(B + "/admin/sondes").text
check("Radiosondes marks its parts as tabs", all(x in s for x in ('data-tab-title="Stations"', 'data-tab-title="Historical Flights"', 'data-tab-title="Prediction Service"')))
h = adm.get(B + "/admin/hospitals").text
check("Hospitals marks its parts as tabs", all(x in h for x in ('data-tab-title="Load and Check"', 'data-tab-title="Data Issues"', 'data-tab-title="Edit a Hospital"')))
check("searching hospitals opens the Edit a Hospital tab first", 'data-tab-order="30" data-tab-default' in adm.get(B + "/admin/hospitals?q=a").text and 'data-tab-order="30" data-tab-default' not in h)
for pg, titles in (("/admin/repeaters", ("Review", "Import", "Linked Systems and Add")), ("/admin/feeders", ("Feeders", "Add a Feeder for a Member")), ("/admin/weather", ("Stations", "Add a Station for a Member"))):
    t = adm.get(B + pg).text
    check(f"{pg} marks its parts as tabs", all(f'data-tab-title="{x}"' in t for x in titles), pg)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part: selenium is not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    d.get(B + "/healthz"); d.delete_all_cookies()
    for c in adm.cookies: d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
    def vis(sel): return d.execute_script("var e=document.querySelector(arguments[0]); return !!e && e.offsetParent !== null", sel)
    d.get(B + "/admin/sondes"); time.sleep(2)
    tabs = d.execute_script("return Array.from(document.querySelectorAll('.admintabs a')).map(function(a){return a.textContent})")
    check("a tab bar lists the tabs, Stations first", tabs[:1] == ["Stations"] and len(tabs) == 3, tabs)
    check("Stations is shown and the other two are not", vis("#sec-assign") and not vis("#sec-history") and not vis("#sec-predict"))
    d.execute_script("document.querySelector('.admintabs a[href=\"#tab-historical-flights\"]').click()"); time.sleep(.4)
    check("clicking a tab shows it and hides the rest", vis("#sec-history") and not vis("#sec-assign"))
    d.get(B + "/admin/sondes#tab-prediction-service"); d.refresh(); time.sleep(2)
    check("a #tab- link opens that tab", vis("#sec-predict") and not vis("#sec-history"))
    d.get(B + "/admin/sondes#sec-history"); d.refresh(); time.sleep(2)
    check("a link to a section inside a tab opens its tab", vis("#sec-history"))
    d.get(B + "/admin/hospitals"); time.sleep(2)
    check("Hospitals opens on Load and Check", vis("#sec-update-hospitals") and not vis("#sec-data-issues"))
    d.get(B + "/admin/hospitals?q=a"); time.sleep(2)
    check("a hospital search stays on the Edit a Hospital tab", not vis("#sec-update-hospitals"))
    for pg, first, others in (("/admin/repeaters", "Review", ["Import", "Linked Systems and Add"]), ("/admin/feeders", "Feeders", ["Add a Feeder for a Member"]), ("/admin/weather", "Stations", ["Add a Station for a Member"])):
        d.get(B + pg); time.sleep(2)
        tabs = d.execute_script("return Array.from(document.querySelectorAll('.admintabs a')).map(function(a){return a.textContent})")
        check(f"{pg}: a tab bar lists {first} first", tabs[:1] == [first] and len(tabs) == 1 + len(others), tabs)
        shown = d.execute_script("return Array.from(document.querySelectorAll('[data-tab-title]')).filter(function(e){return e.offsetParent !== null}).map(function(e){return e.getAttribute('data-tab-title')})")
        check(f"{pg}: only {first} is shown to begin with", shown == [first], shown)
        d.execute_script("document.querySelectorAll('.admintabs a')[1].click()"); time.sleep(.5)
        shown = d.execute_script("return Array.from(document.querySelectorAll('[data-tab-title]')).filter(function(e){return e.offsetParent !== null}).map(function(e){return e.getAttribute('data-tab-title')})")
        check(f"{pg}: the second tab replaces it", len(shown) == 1 and shown != [first], shown)
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
