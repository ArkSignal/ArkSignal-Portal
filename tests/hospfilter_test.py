import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
def post(path, data, page):
    j = adm.post(B + path, data=dict(data, csrf_token=tok(adm, page)), headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"]
DEFAULT = ["Acute Care Hospitals", "Critical Access Hospitals", "Childrens", "Rural Emergency Hospital", "Acute Care - Veterans Administration", "Acute Care - Department of Defense"]
WORDS = "BEHAVIORAL,PSYCHIATRIC,MENTAL HEALTH,REHABILITATION,REHAB,RECOVERY,SPECIALTY,SURGICAL,LONG TERM,LTAC,SENIOR"
def save(types, words, emergency="0"):
    return post("/admin/settings/save", {"section": "hospitals", "hospital_types": types, "hospital_exclude_words": words, "hospital_emergency_only": emergency}, "/admin/settings")
t = adm.get(B + "/admin/settings").text
check("the filter settings are on the page", all(f'name="{k}"' in t for k in ("hospital_types", "hospital_exclude_words", "hospital_emergency_only")) and 'value="Psychiatric"' in t and "Psychiatric and behavioral health" in t)
ok, m = save(DEFAULT, WORDS)
check("the default filter saves", ok, m)
ok, m = post("/admin/hospitals/import", {}, "/admin/hospitals")
check("the import reports what the filter removed", ok and "removed by the type filter" in m, m)
check("no psychiatric or long-term facility is loaded", psql("select count(*) from hospitals where htype in ('Psychiatric','Long-term')") == "0", psql("select string_agg(name, '; ') from hospitals where htype in ('Psychiatric','Long-term') limit 5"))
check("no facility with a left-out word in its name is loaded", psql("select count(*) from hospitals where facility_id not like 'imp-%' and (name ilike '%behavioral%' or name ilike '%surgical%' or name ilike '%specialty%' or name ilike '%rehab%')") == "0")
check("real hospitals are still there", int(psql("select count(*) from hospitals where htype='Acute Care Hospitals'") or 0) > 300 and int(psql("select count(*) from hospitals where htype='Critical Access Hospitals'") or 0) > 50)
ok, m = save(DEFAULT + ["Psychiatric"], "")
check("psychiatric centres can be switched on", ok, m)
ok, m = post("/admin/hospitals/import", {}, "/admin/hospitals")
n_psy = int(psql("select count(*) from hospitals where htype='Psychiatric'") or 0)
check("then an import brings them in", ok and n_psy > 50, (m, n_psy))
psy = psql("select facility_id from hospitals where htype='Psychiatric' order by facility_id limit 1")
psql(f"update hospitals set trauma='III' where facility_id='{psy}'")
ok, m = save(DEFAULT, WORDS)
ok, m = post("/admin/hospitals/apply-filter", {}, "/admin/hospitals")
left = psql("select count(*) from hospitals where htype in ('Psychiatric','Long-term')")
check("applying the filter removes them again", ok and "removed" in m and left == "1", (m, left))
check("a facility an administrator gave a trauma level is kept", psql(f"select count(*) from hospitals where facility_id='{psy}'") == "1")
psql(f"delete from hospitals where facility_id='{psy}'")
ok, m = save(DEFAULT, WORDS, "1")
ok2, m2 = post("/admin/hospitals/apply-filter", {}, "/admin/hospitals")
check("emergency departments only leaves none without one", ok and ok2 and psql("select count(*) from hospitals where not emergency and trauma='' and not heliport_set and pos_source<>'manual' and facility_id not like 'imp-%'") == "0", m2)
ok, m = save(DEFAULT, WORDS, "0")
ok, m = post("/admin/hospitals/import", {}, "/admin/hospitals")
check("switched back off, the next import restores them", ok and int(psql("select count(*) from hospitals where not emergency") or 0) > 0, m)
check("a bad type list is refused", not save([], WORDS)[0])
ok, m = save(DEFAULT, WORDS, "1")   # the default: emergency departments only
ok, m = post("/admin/hospitals/import", {}, "/admin/hospitals")
check("with the default (emergency departments only) the next import keeps just those", ok and psql("select count(*) from hospitals where not emergency and trauma='' and facility_id not like 'imp-%'") == "0", m)
print("ALL PASS" if ok_all else "SOME FAILED")
