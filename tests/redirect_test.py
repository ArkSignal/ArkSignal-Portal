import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
adm = sess("marty", PW)
def save(**kw):
    d = dict(kw, section="general", csrf_token=tok(adm, "/admin/settings"))
    j = adm.post(B + "/admin/settings/save", data=d, headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"]
def setdb(k, v):   # the redirect itself is tested here; the address check that guards the form is tested in addresscheck_test (it needs the real name to answer as this site)
    subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'\""], capture_output=True)
def get(path, host, method="get", **kw):
    return getattr(requests, method)(B + path, headers={"Host": host}, allow_redirects=False, **kw)
OLD, NEW = "adsb.allisononline.org", "arksignal.allisononline.org"
save(main_address="", redirect_addresses=OLD, site_name="ArkSignal", registration_open="1")
check("no redirect while the Main Address is empty", get("/about", OLD).status_code == 200)
ok, m = save(main_address="not a host", redirect_addresses=OLD, site_name="ArkSignal", registration_open="1")
check("a bad Main Address is refused", not ok and "host name" in m, m)
setdb("main_address", NEW); setdb("redirect_addresses", OLD)
ok, m = save(main_address=NEW, redirect_addresses=OLD, site_name="ArkSignal", registration_open="1")
check("saving the Main Address unchanged works (a new one is checked first: addresscheck_test)", ok, m)
try:
    r = get("/airports/?q=lit&page=2", OLD)
    check("an old address is sent to the main address with the path and query kept", r.status_code == 301 and r.headers["Location"] == f"https://{NEW}/airports/?q=lit&page=2", (r.status_code, r.headers.get("Location")))
    check("the main address itself is not redirected", get("/about", NEW).status_code == 200)
    check("an address that is not listed is not redirected", get("/about", "test-box.invalid:18430").status_code == 200)
    check("the health check is never redirected", get("/healthz", OLD).status_code == 200)
    check("posts are never redirected", get("/my/login", OLD, "post", data={}).status_code != 301)
    check("HEAD requests are redirected like pages", get("/about", OLD, "head").status_code == 301)
    check("the port in the host header does not matter", get("/about", OLD + ":443").status_code == 301)
    setdb("redirect_addresses", OLD + ", old.example.org")
    check("several old addresses can be listed", get("/about", "old.example.org").status_code == 301)
finally:
    setdb("main_address", ""); setdb("redirect_addresses", OLD)
check("switched off again, nothing is redirected", get("/about", OLD).status_code == 200)
check("both settings are on the General tab", all(f'name="{k}"' in adm.get(B + "/admin/settings").text for k in ("main_address", "redirect_addresses")))
print("ALL PASS" if ok_all else "SOME FAILED")
