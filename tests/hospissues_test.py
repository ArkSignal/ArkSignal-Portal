"""Admin > Hospitals data issues: a flag and a filter for missing or doubtful information, and a check that a linked heliport is physically close to its hospital."""
import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
H = {"X-Requested-With": "XMLHttpRequest"}
rows = psql("select facility_id from hospitals where state='AR' and address<>'' and city<>'' and zip ~ '^[0-9]{5}' and phone<>'' and lat is not null and pos_source not in ('zip','') and facility_id not like 'imp-%' and trauma='' and heliport_ident='' order by facility_id limit 6").split("\n")
a, b, c, d, e, g = rows
keep = {f: psql(f"select zip||'~'||phone||'~'||heliport_ident||'~'||heliport_set||'~'||trauma||'~'||pos_source||'~'||pos_check from hospitals where facility_id='{f}'") for f in rows}
far = psql(f"select ident from airports a, hospitals h where h.facility_id='{b}' and a.type='heliport' and abs(a.lat - h.lat) > 0.6 and abs(a.lon - h.lon) < 3 order by ident limit 1")
near = psql(f"select a.ident from airports a, hospitals h where h.facility_id='{g}' and a.type='heliport' and abs(a.lat - h.lat) < 0.002 and abs(a.lon - h.lon) < 0.002 limit 1")
psql(f"update hospitals set zip='', phone='' where facility_id='{a}'")
psql(f"update hospitals set heliport_ident='{far}', heliport_set=true where facility_id='{b}'")
psql(f"update hospitals set heliport_ident='ZZZZ9', heliport_set=true where facility_id='{c}'")
psql(f"update hospitals set trauma='II' where facility_id='{d}'")
psql(f"update hospitals set pos_source='zip' where facility_id='{e}'")
if near:
    psql(f"update hospitals set heliport_ident='{near}', heliport_set=true where facility_id='{g}'")
t = adm.get(B + "/admin/hospitals").text
check("the page has a Data Issues section with a count for every kind", "Data Issues" in t and all(x in t for x in ("Address missing or incomplete", "Position approximate or not confirmed", "No phone number", "Trauma centre with no heliport linked", "Heliport far from the hospital", "Heliport not found in the airport data")))
def listed(issue):
    t = adm.get(B + "/admin/hospitals", params={"issue": issue, "q": ""}).text
    return set(re.findall(r'id="h-([^"]+)"', t)), t
s, t = listed("address")
check("the address filter lists a hospital with no ZIP and the flag says why", a in s and "Address missing or incomplete" in t, a)
s, _ = listed("phone")
check("the phone filter finds it too, and not the others", a in s and b not in s)
s, t = listed("far_heliport")
check("a heliport far from its hospital is flagged with the distance", b in s and re.search(r"Heliport far from the hospital \([\d.]+ miles?\)", t) is not None, (far, b))
s, _ = listed("bad_heliport")
check("a heliport that is not in the airport data is flagged", c in s)
s, _ = listed("no_heliport")
check("a trauma centre with no heliport is flagged", d in s)
s, _ = listed("position")
check("an approximate position is flagged", e in s)
s, t = listed("any")
check("'any' lists every flagged hospital and leaves out clean ones", {a, b, c, d, e} <= s and (not near or g not in s) and "&#9873;" in t, (near,))
counts = dict(re.findall(r'href="/admin/hospitals\?issue=(\w+)">(?:<b>)?(\d+)', adm.get(B + "/admin/hospitals").text))
check("the counts on the page match the lists", int(counts.get("far_heliport", 0)) >= 1 and int(counts.get("any", 0)) >= 5 and int(counts["any"]) == len(listed("any")[0]), counts)
check("the public hospital pages do not show data issues", "Data issues" not in requests.get(B + "/hospitals/").text and "&#9873;" not in requests.get(B + f"/hospitals/{a}").text)
if near:
    heli = psql(f"select a.ident from airports a where a.ident='{near}'")
    j = adm.post(B + f"/admin/hospitals/{g}/save", data={"trauma": "", "heliport": near, "notes": "", "lat": "", "lon": "", "csrf_token": tok(adm, "/admin/hospitals")}, headers=H).json()
    check("saving a heliport that is close gives no warning", j["ok"] and "check the heliport" not in j["message"], j)
j = adm.post(B + f"/admin/hospitals/{b}/save", data={"trauma": "", "heliport": far, "notes": "", "lat": "", "lon": "", "csrf_token": tok(adm, "/admin/hospitals")}, headers=H).json()
check("saving a heliport that is far away warns about it", j["ok"] and "check the heliport" in j["message"] and far in j["message"], j)
for f in rows:
    z, ph, hi, hs, tr, ps, pc = keep[f].split("~")
    psql(f"update hospitals set zip='{z}', phone='{ph}', heliport_ident='{hi}', heliport_set={hs}, trauma='{tr}', pos_source='{ps}', pos_check='{pc}' where facility_id='{f}'")
print("ALL PASS" if ok_all else "SOME FAILED")
