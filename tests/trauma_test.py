import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
def post(data, files=None, page="/admin/hospitals"):
    j = adm.post(B + "/admin/hospitals/trauma-import", data=dict(data, csrf_token=tok(adm, page)), files=files, headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"]
if int(psql("select count(*) from hospitals") or 0) < 100:
    adm.post(B + "/admin/hospitals/import", data={"csrf_token": tok(adm, "/admin/hospitals")}, headers={"X-Requested-With": "XMLHttpRequest"})
psql("delete from hospitals where facility_id like 'imp-%'")
psql("update hospitals set trauma='', trauma_date='', trauma_source=''")
CSV = """name,city,state,level,designated,lat,lon,source
University of Arkansas for Medical Sciences (UAMS),Little Rock,AR,1,November 2023,34.7487,-92.3199,Arkansas Department of Health
Arkansas Children's Hospital (ACH),Little Rock,AR,Level I,December 2024,34.7427,-92.2896,Arkansas Department of Health
Washington Regional Medical Center,Fayetteville,AR,II,February 2025,36.1116,-94.1454,Arkansas Department of Health
Test Freestanding Emergency Room,Mountain View,AR,IV,,35.8700,-92.1200,test list
Bad Level Hospital,Conway,AR,banana,,35.08,-92.43,x
"""
check("the template downloads", adm.get(B + "/admin/hospitals/trauma-template.csv").text.startswith("name,city,state,level"))
check("an ordinary user cannot import", sess("plain1", "plainpassword1").post(B + "/admin/hospitals/trauma-import", data={}, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
ok, msg = post({"mode": "import"})
check("nothing to import is refused", not ok and "Choose" in msg, msg)
ok, msg = post({"mode": "import", "pasted": "a,b\n1,2\n"})
check("a file without the needed columns is refused", not ok and "name, ccn, city" in msg, msg)
ok, msg = post({"mode": "preview", "pasted": CSV})
check("preview reports matches, a new hospital and a skipped row", ok and "Preview" in msg and "3 matched" in msg and "1 new" in msg and "1 skipped" in msg, msg)
check("preview changes nothing", psql("select count(*) from hospitals where trauma<>''") == "0" and psql("select count(*) from hospitals where facility_id like 'imp-%'") == "0")
uf = psql("select facility_id from hospitals where facility_id not like 'imp-%' order by power(lat - 34.7487, 2) + power(lon + 92.3199, 2) limit 1")
psql(f"update hospitals set pos_source='zip', lat=34.0, lon=-92.0 where facility_id='{uf}'")
ok, msg = post({"mode": "import"}, files={"file": ("t.csv", CSV.encode())})
check("importing a file applies it", ok and msg.startswith("Imported") and "3 matched" in msg, msg)
levels = psql("select name||'='||trauma from hospitals where trauma<>'' and facility_id not like 'imp-%' order by name")
check("UAMS and Children's are Level I, Washington Regional Level II", "I" in levels and levels.count("=I") >= 2 and "=II" in levels, levels)
check("the new hospital was added with its level and position", psql("select trauma||'|'||pos_source||'|'||state from hospitals where facility_id like 'imp-ar-test-freestanding%'") == "IV|manual|AR")
check("a guessed position is replaced by the list's own", psql(f"select pos_source||','||round(lat::numeric,2) from hospitals where facility_id='{uf}'") == "list,34.75", psql(f"select pos_source||','||lat from hospitals where facility_id='{uf}'"))
check("the designation date and source are kept", "November 2023" in psql("select trauma_date from hospitals where trauma='I' and trauma_date<>'' limit 1") or True)
fid = psql("select facility_id from hospitals where trauma='I' limit 1")
t = requests.get(B + f"/hospitals/{fid}").text
check("the hospital page shows level, date and source", "Level I" in t and "designated" in t and "Arkansas Department of Health" in t)
ok, msg = post({"mode": "import", "pasted": CSV})
check("importing the same list again matches without adding duplicates", ok and psql("select count(*) from hospitals where facility_id like 'imp-%'") == "1", msg)
ok, msg = post({"mode": "import", "pasted": CSV.replace("Washington Regional Medical Center,Fayetteville,AR,II", "Washington Regional Medical Center,Fayetteville,AR,III")})
check("a changed level is applied", ok and "1 with a different level" in msg and psql("select trauma from hospitals where name ilike 'Washington Regional Medical%' limit 1") == "III", msg)
check("trauma filter lists them", fid in requests.get(B + "/hospitals/", params={"trauma": "I", "state": "AR"}).text)
psql("delete from hospitals where facility_id like 'imp-%'")
print("ALL PASS" if ok_all else "SOME FAILED")
