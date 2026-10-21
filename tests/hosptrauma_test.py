"""The Trauma column on the public Hospitals list: the level never wraps, and sorting puts the levels in order (I, II, III, IV, V) with hospitals that have none last."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
if int(psql("select count(*) from hospitals") or 0) < 100:
    adm = sess("marty", PW)
    adm.post(B + "/admin/hospitals/import", data={"csrf_token": tok(adm, "/admin/hospitals")}, headers={"X-Requested-With": "XMLHttpRequest"})
ids = psql("select facility_id from hospitals where state='AR' and facility_id not like 'imp-%' order by facility_id limit 8").split("\n")
keep = {f: psql(f"select trauma from hospitals where facility_id='{f}'") for f in ids}
levels = ["IV", "", "II", "I", "III", "", "IV", "V"]
for f, lv in zip(ids, levels):
    psql(f"update hospitals set trauma='{lv}' where facility_id='{f}'")
def column(direction):
    t = requests.get(B + "/hospitals/", params={"state": "AR", "sort": "trauma", "dir": direction, "q": ""}).text
    rows = re.findall(r"<tr>\s*<td>.*?</tr>", t, re.S)
    out = []
    for r in rows:
        if any(f"/hospitals/{f}" in r for f in ids):
            m = re.search(r'class="tag admin"[^>]*>Level ([IV]+)<', r)
            out.append(m.group(1) if m else "")
    return t, out
t, asc = column("asc")
rank = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
check("ascending: the levels run I to V with the hospitals that have none at the end", asc == ["I", "II", "III", "IV", "IV", "V", "", ""] or (asc and asc == sorted(asc, key=lambda x: rank.get(x, 9))), asc)
_, desc = column("desc")
check("descending: V down to I, and still none last", desc and desc == sorted([x for x in desc if x], key=lambda x: -rank[x]) + [x for x in desc if not x], desc)
check("the Trauma pill cannot wrap onto two lines", 'class="tag admin" style="white-space:nowrap"' in t)
for f, lv in keep.items():
    psql(f"update hospitals set trauma='{lv}' where facility_id='{f}'")
print("ALL PASS" if ok_all else "SOME FAILED")
