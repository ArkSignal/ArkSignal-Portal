"""Import Hospital Data in a real browser: the Import button must import (the page once always sent a preview because the pressed button's value was not sent)."""
import os, subprocess, sys, time
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP hospimport_ui_test: selenium not installed"); sys.exit(0)
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
adm = sess("marty", PW)
fid = psql("select facility_id from hospitals where state='AR' and emergency and facility_id not like 'imp-%' order by facility_id limit 1")
psql(f"update hospitals set services='' where facility_id='{fid}'")
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    d.get(B + "/about")
    for c in adm.cookies:
        d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
    d.get(B + "/admin/hospitals"); time.sleep(1)
    d.execute_script("document.querySelector('#sec-import-hospital-data').open = true")
    box = d.find_element(By.ID, "trpaste"); box.clear(); box.send_keys(f"name,ccn,services\nX,{fid},surgery;ct\n")
    def press(value):
        btn = d.find_element(By.CSS_SELECTOR, f'#sec-import-hospital-data button[value="{value}"]')
        d.execute_script("arguments[0].scrollIntoView({block:'center'})", btn); btn.click(); time.sleep(3)
        return d.find_element(By.ID, "action-feedback").text
    m = press("preview")
    check("Preview says it only previews and changes nothing", "Preview" in m and psql(f"select services from hospitals where facility_id='{fid}'") == "", m)
    m = press("import")
    check("Import really imports", m.startswith("Imported") and psql(f"select services from hospitals where facility_id='{fid}'") == "surgery,ct", m)
finally:
    d.quit()
    psql(f"update hospitals set services='' where facility_id='{fid}'")
print("ALL PASS" if not bad else "SOME FAILED")
