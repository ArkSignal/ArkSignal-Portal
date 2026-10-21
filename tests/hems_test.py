import re, requests, subprocess, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
def post(path, data):
    j = adm.post(B + path, data=dict(data, csrf_token=tok(adm, "/admin/air-ambulance")), headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"], j.get("html", "")
psql("delete from hems_aircraft")
check("the admin page needs sign-in", requests.get(B + "/admin/air-ambulance", allow_redirects=False).status_code in (302, 401, 403, 404))
check("it loads for an administrator and offers the actions", all(x in adm.get(B + "/admin/air-ambulance").text for x in ("Update From the Aircraft Database", "Add Tail Numbers")))
check("an ordinary user cannot use it", sess("plain1", "plainpassword1").post(B + "/admin/air-ambulance/update", data={}, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
check("bad tail numbers are refused", not post("/admin/air-ambulance/tails", {"tails": "!!! ??"})[0])
ok, msg, html = post("/admin/air-ambulance/tails", {"tails": "N452MT\nn390sf, N123AE ZZ!!", "note": "from the friend's list"})
check("pasted tail numbers are added and looked up", ok and "3 tail numbers added" in msg and "Looked up" in msg, msg)
n_reg = int(psql("select count(*) from hems_aircraft where source='registry'") or 0)
check("the owner-name search finds hundreds of US helicopters", n_reg > 300, n_reg)
check("known air ambulance operators are in it", int(psql("select count(*) from hems_aircraft where owner ilike '%AIR EVAC%'") or 0) > 50 and int(psql("select count(*) from hems_aircraft where owner ilike '%AIR METHODS%'") or 0) > 100)
check("only helicopters and only N registrations", psql("select count(*) from hems_aircraft where source='registry' and reg not like 'N%'") == "0")
check("a listed tail gets its hex code", len(psql("select hex from hems_aircraft where reg='N452MT'")) == 6 and psql("select source from hems_aircraft where reg='N452MT'") == "list")
check("a tail the database does not know stays waiting", psql("select count(*) from hems_aircraft where reg='N123AE' and hex is null") in ("0", "1"))
api = requests.get(B + "/api/hems.json").json()
check("the map feed lists hex codes and tail numbers with the operator", len(api["hex"]) > 300 and "N452MT" in api["reg"] and "Med-Trans" in api["reg"]["N452MT"], list(api["reg"].items())[:2])
nfix = int(psql("select count(*) from hems_aircraft where source='registry' and kind='fixed'") or 0)
check("fixed-wing medical transports are found too (PC-12 and King Air types)", nfix > 40 and int(psql("select count(*) from hems_aircraft where source='registry' and kind='fixed' and type in ('PC12','BE9L','BE20','B350')") or 0) > 30, nfix)
check("fixed wing and helicopters are kept apart", psql("select count(*) from hems_aircraft where kind='fixed' and type in ('EC35','B407','S76')") == "0" and psql("select count(*) from hems_aircraft where kind='heli' and type in ('PC12','BE9L')") == "0")
ok, msg, html = post("/admin/air-ambulance/tails", {"tails": "N991FW", "note": "fixed wing test", "kind": "fixed"})
check("tail numbers can be added as fixed wing", ok and psql("select kind from hems_aircraft where reg='N991FW'") == "fixed", msg)
api = requests.get(B + "/api/hems.json").json()
check("the map feed keeps fixed wing in its own lists", "N991FW" in api["fw"]["reg"] and "N991FW" not in api["reg"] and len(api["fw"]["hex"]) > 40, list(api["fw"]["reg"])[:2])
check("the admin list says which is which", "Fixed wing" in adm.get(B + "/admin/air-ambulance", params={"q": "N991FW"}).text and "Helicopters / Fixed Wing" in adm.get(B + "/admin/air-ambulance").text)
psql("delete from hems_aircraft where note='fixed wing test'")
ident = psql("select ident from hems_aircraft where source='registry' order by ident limit 1")
ok, msg, html = post(f"/admin/air-ambulance/{ident}/exclude", {})
import time as _t; _t.sleep(0)
check("excluding an entry is saved", ok and psql(f"select excluded from hems_aircraft where ident='{ident}'") == "t", msg)
ok, msg, html = post("/admin/air-ambulance/update", {})
check("an update keeps the exclusion", ok and psql(f"select excluded from hems_aircraft where ident='{ident}'") == "t", msg)
ok, msg, html = post(f"/admin/air-ambulance/{ident}/remove", {})
check("a registry entry cannot be removed, only excluded", not ok)
ok, msg, html = post(f"/admin/air-ambulance/{ident}/include", {})
check("including it again works", ok and psql(f"select excluded from hems_aircraft where ident='{ident}'") == "f")
check("the search box on the list works", "N452MT" in adm.get(B + "/admin/air-ambulance", params={"q": "N452MT"}).text)
check("the settings are on the page", all(f'name="{k}"' in adm.get(B + "/admin/settings").text for k in ("hems_operators", "hems_types", "hems_registrations", "hems_source_url")))
# the live map
ac = [a for a in requests.get(B + "/api/aircraft.json").json()["aircraft"] if a.get("r")]
if ac:
    regs = [a["r"].upper() for a in ac[:6]]
    reg = regs[0]
    post("/admin/air-ambulance/tails", {"tails": " ".join(regs), "note": "test aircraft"})
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
        d = webdriver.Chrome(options=o)
        try:
            d.get(B + "/live"); time.sleep(5)
            d.execute_script("document.querySelector('#lv-toolsbody details.fsec[data-k=flags]').open = true")
            check("the Aircraft Flags filter offers both Air ambulance flags", d.find_element(By.CSS_SELECTOR, '#f-flags input[data-flag="16"]').is_displayed() and d.find_element(By.CSS_SELECTOR, '#f-flags input[data-flag="32"]').is_displayed())
            d.execute_script("Array.from(document.querySelectorAll('#f-flags input')).find(function (c) { return c.dataset.flag === '16'; }).click()"); time.sleep(.8)
            for _ in range(12):   # the list redraws on the next refresh
                rows = d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it")
                if 1 <= len(rows) <= 6:
                    break
                time.sleep(1)
            check("choosing it leaves only the air ambulances", 1 <= len(rows) <= 6, (regs, [r.text[:30] for r in rows]))
            check("it shows as a chip", any("Air ambulance (helicopter)" in c.text for c in d.find_elements(By.CSS_SELECTOR, "#lv-chips .chip")))
        finally:
            d.quit()
    finally:
        psql("delete from hems_aircraft where note='test aircraft'")
else:
    print("note: no aircraft with a registration right now, live map part skipped")
psql("delete from hems_aircraft where note like 'from the friend%'")
print("ALL PASS" if ok_all else "SOME FAILED")
