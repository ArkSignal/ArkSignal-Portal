"""Radio > Space Weather: NOAA's storm scales, K index, solar flux, X-ray class and a rough HF band guide (app/spaceweather.py, /radio/space-weather).
The live part needs the test box to reach services.swpc.noaa.gov."""
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
r = requests.get(B + "/radio/space-weather", timeout=90)
t = r.text
check("the page opens for anyone", r.status_code == 200 and "Space Weather" in t, r.status_code)
check("it shows the solar flux, the K index, the flare class and the NOAA scales", all(x in t for x in ('id="sw-sfi"', 'id="sw-kp"', 'id="sw-xray"', 'id="sw-scales"')), "")
check("the numbers are real (a solar flux above 50 and a K index from 0 to 9)", (lambda s, k: s is not None and 50 < float(s.group(1)) < 400 and k is not None and 0 <= float(k.group(1)) <= 9)(re.search(r'id="sw-sfi">([\d.]+)<', t), re.search(r'id="sw-kp">([\d.]+)<', t)), "")
check("it has the K index bars and the rough band guide with its caveat", 'id="sw-kpbars"' in t and 'id="sw-bands"' in t and "not a forecast" in t, "")
check("each K index bar has its value and, under it, the time range and the date (UTC and the visitor's own time)", t.count('class="swk"') >= 8 and 'class="swl"' in t and "UTC<br>" in t and "toLocaleTimeString" in t, "")
check("it uses the site's folding sections", t.count('class="panel setsec"') >= 3 and '<div class="panel" style="overflow' not in t, "")
check("the Radio menu lists it", 'href="/radio/space-weather"' in t)
adm = sess("marty", PW)
check("its settings are in Admin > Settings", all(x in adm.get(B + "/admin/settings").text for x in ("Space Weather Page", "Space Weather: Refresh Every")))
print("ALL PASS" if ok_all else "SOME FAILED")
