import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
def imp(text, mode):
    j = adm.post(B + "/admin/hospitals/trauma-import", data={"mode": mode, "pasted": text, "csrf_token": tok(adm, "/admin/hospitals")}, headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"]
if int(psql("select count(*) from hospitals") or 0) < 100:
    adm.post(B + "/admin/hospitals/import", data={"csrf_token": tok(adm, "/admin/hospitals")}, headers={"X-Requested-With": "XMLHttpRequest"})
psql("update hospitals set services=''")
fid = psql("select facility_id from hospitals where state='AR' and emergency and facility_id not like 'imp-%' order by facility_id limit 1")
fid2 = psql("select facility_id from hospitals where state='AR' and emergency and facility_id not like 'imp-%' order by facility_id desc limit 1")
before = psql(f"select trauma from hospitals where facility_id='{fid}'")
CSV = f"name,ccn,services\nA Completely Different Name,{fid},Stroke; OB; neuro; NICU; widget\nAnother Wrong Name,{fid2},cardiac;pediatric\n"
ok, m = imp(CSV, "preview")
check("preview matches by CMS number whatever the name says and flags unknown words", ok and "2 matched" in m and "widget" in m and "Preview" in m, m)
check("preview changes nothing", psql("select count(*) from hospitals where services<>''") == "0")
ok, m = imp(CSV, "import")
check("import sets the services", ok and psql(f"select services from hospitals where facility_id='{fid}'") == "stroke,obstetric,neuro,nicu" and psql(f"select services from hospitals where facility_id='{fid2}'") == "cardiac,pediatric", m)
check("a services-only list leaves the trauma level alone", psql(f"select trauma from hospitals where facility_id='{fid}'") == before)
t = requests.get(B + "/hospitals/", params={"service": "stroke", "state": "AR"}).text
check("the list can be filtered by service and shows the services", f"/hospitals/{fid}" in t and "Stroke centre" in t and "Neurosurgery" in t and "Obstetrics" in t)
check("the filter leaves others out", f"/hospitals/{fid2}" not in t)
p = requests.get(B + f"/hospitals/{fid}").text
check("the hospital page shows the services", "Newborn intensive care" in p and "Neurosurgery" in p)
check("the service choices are on the list page", all(x in requests.get(B + "/hospitals/").text for x in ("Stroke centre", "Neurosurgery", "Obstetrics", "Cancer care", "Orthopedics")))
ok, m = imp("name,ccn\nOnly a name,040001\n", "preview")
check("a list with neither level nor services is refused", not ok and "level and services" in m, m)
ok, m = imp(f"name,ccn,level\nWrong Name,{fid},II\n", "import")
check("a level by CMS number works too", ok and psql(f"select trauma from hospitals where facility_id='{fid}'") == "II" and psql(f"select services from hospitals where facility_id='{fid}'") == "stroke,obstetric,neuro,nicu", m)
# Import Hospital Data: new capability tags, and a file adds to the services on record
ok, m = imp(f"name,ccn,services\nX,{fid},surgery;ICU;CT;vascular;IR;facial;eye;stroke\n", "import")
sv = psql(f"select services from hospitals where facility_id='{fid}'")
check("capability tags are understood and added to the ones on record", ok and "widget" not in m and sv == "stroke,obstetric,neuro,nicu,surgery,icu,ct,vascular,ir,facial,eye", (m, sv))
check("the admin box is named Import Hospital Data", "Import Hospital Data" in adm.get(B + "/admin/hospitals").text and "Import Trauma Levels" not in adm.get(B + "/admin/hospitals").text)
psql(f"update hospitals set trauma='{before}' where facility_id='{fid}'")
psql("update hospitals set services=''")
print("ALL PASS" if ok_all else "SOME FAILED")
