import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
def post(path, data):
    j = adm.post(B + path, data=dict(data, csrf_token=tok(adm, "/admin/hospitals")), headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"]
check("an ordinary user cannot use Locate", sess("plain1", "plainpassword1").post(B + "/admin/hospitals/locate", data={}, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
t = adm.get(B + "/admin/hospitals").text
check("the page offers Locate for approximate positions", "Locate Approximate Hospitals With OpenStreetMap" in t and "approximate position" in t)
fid = psql("select facility_id from hospitals where name ilike 'Baptist Health Medical Center-Little Rock%' limit 1")
if fid:
    psql(f"update hospitals set pos_source='zip', lat=34.7494, lon=-92.3514 where facility_id='{fid}'")
    ok, m = post("/admin/hospitals/locate", {"only": fid})
    check("Locate answers with what it did", "OpenStreetMap:" in m, m)
    row = psql(f"select pos_source||'|'||round(lat::numeric,3)||'|'||round(lon::numeric,3) from hospitals where facility_id='{fid}'")
    check("the hospital is either located within the city or left as it was", row.startswith("osm|34.7") or row.startswith("zip|34.749"), row)
    ok, m = post("/admin/hospitals/import", {})
    check("a new import keeps an OpenStreetMap position", ok and (psql(f"select pos_source from hospitals where facility_id='{fid}'") in ("osm", "census", "heliport")), m)
check("the setting is on the page", 'name="nominatim_url"' in adm.get(B + "/admin/settings").text)
print("ALL PASS" if ok_all else "SOME FAILED")
