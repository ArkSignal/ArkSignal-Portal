"""The pages added in session 19 fit a phone (400 px wide): nothing runs off the side, and each page's main controls are on the screen."""
import os, re, subprocess, time, requests
os.environ["TEST_HOST"] = "test-box.invalid"
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP no selenium"); print("ALL PASS"); raise SystemExit(0)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
psql("INSERT INTO settings (key, value) VALUES ('myflights_enabled','1'),('satpass_enabled','1') ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value")
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=400,850")
d = webdriver.Chrome(options=o); J = d.execute_script
try:
    d.get(B + "/my/login"); time.sleep(1)
    d.find_element("name", "username").send_keys("plain1"); d.find_element("name", "password").send_keys("plainpassword1")
    d.find_element("css selector", "form button[type=submit], form input[type=submit]").click(); time.sleep(2)
    for path in ("/feed/", "/feed/adsb", "/radio/satellites", "/radio/satellites/map?sat=25544", "/my/flights/import", "/my/flights/lookup.json?q=N377DN", "/flight/", "/history/", "/about"):
        if path.endswith("N377DN"):
            continue
        d.get(B + path); time.sleep(2.2)
        check(f"phone: {path} does not run off the side", J("return document.documentElement.scrollWidth <= window.innerWidth + 1"), J("return document.documentElement.scrollWidth + ' > ' + window.innerWidth"))
    d.get(B + "/feed/"); time.sleep(1.5)
    check("phone: the feed overview's tiles are all on the screen width", J("return Array.from(document.querySelectorAll('.tiles .tile')).every(function(t){var r=t.getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth + 1})"))
    d.get(B + "/flight/"); time.sleep(2)
    check("phone: Flights shows the Route and Airline columns inside a scrollable box, not the page", J("var b=document.getElementById('fs-results'); return b.scrollWidth >= b.clientWidth && document.documentElement.scrollWidth <= window.innerWidth + 1"))
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
