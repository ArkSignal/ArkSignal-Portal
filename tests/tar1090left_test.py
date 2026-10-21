"""tar1090 parity leftovers built on 2026-10-14: the 'without position only' filter (key G) and times in UTC."""
import requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
lv = requests.get(B + "/live").text
check("the live map has a Without position only filter beside With position only, with key G in the shortcuts", 'id="f-nopos"' in lv and 'flip("f-nopos")' in lv and "Only aircraft that send no position" in lv)
check("it overrides the with-position box (it shows exactly the aircraft that send no position) and is a chip and a badge count", 'if ($("f-nopos").checked) { if (a.lat != null) return false; }' in lv and 'tick("f-nopos"' in lv)
home = requests.get(B + "/").text
check("the display menu offers UTC for times", 'value="utc"' in home and "UTC (Zulu" in home)
import re
adm = sess("marty", PW)
audit = adm.get(B + "/admin/audit").text
hdr = {"X-CSRFToken": re.search(r'"X-CSRFToken": "([^"]+)"', audit).group(1)}
r = adm.post(B + "/my/prefs", json={"tz": "utc"}, headers=hdr)
check("the choice is accepted and kept with the account", r.status_code == 200, r.text[:100])
check("and it is kept (read back with the account's preferences)", '"tz": "utc"' in adm.get(B + "/admin/audit").text.replace('"tz":"utc"', '"tz": "utc"'))
adm.post(B + "/my/prefs", json={"tz": "browser"}, headers=hdr)
check("the live map can select an aircraft by itself (a wall display option, key A) and leaves the visitor's own choice alone", all(x in lv for x in ('id="f-auto"', "function autoPick()", 'flip("f-auto")', "the visitor chose one")))
check("the live map offers altitude corrected for QNH (as reported, the QNH the aircraft sends, or one you type)", all(x in lv for x in ('id="l-qnh"', 'value="plane"', 'id="l-qnh-v"', "function qAlt(a)")))
try:
    from selenium import webdriver
    import time
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + "/live"); time.sleep(3)
        d.execute_script("localStorage.setItem('lv.qnh','set'); localStorage.setItem('lv.qnhv','1023.25')"); d.get(B + "/live"); time.sleep(3)
        r = d.execute_script("return [qnhAltitude({alt:5000}), qnhAltitude({alt:20000}), qnhAltitude({alt:5000, gnd:true}), qnhAltitude({alt:null})]")
        check("with a QNH of 1023.25 hPa an aircraft at 5,000 ft pressure altitude is about 270 ft higher; above the transition altitude, on the ground and with no altitude nothing changes", 5240 <= r[0] <= 5300 and r[1] == 20000 and r[2] == 5000 and r[3] is None, r)
        d.execute_script("localStorage.setItem('lv.qnh','plane')"); d.get(B + "/live"); time.sleep(3)
        r = d.execute_script("return [qnhAltitude({alt:5000}), qnhAltitude({alt:5000, qnh:1003.25})]")
        check("with the aircraft's own QNH it is used when the aircraft sends one (a lower QNH gives a lower altitude) and nothing changes when it does not", r[0] == 5000 and 4700 <= r[1] <= 4760, r)
        d.execute_script("localStorage.removeItem('lv.qnh'); localStorage.removeItem('lv.qnhv')")
    finally:
        d.quit()
except ImportError:
    print("SKIP the altitude arithmetic: selenium not installed")
print("ALL PASS" if ok_all else "SOME FAILED")
