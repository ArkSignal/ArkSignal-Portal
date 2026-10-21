"""Check All Positions / Check This Position: Census and Photon agreeing confirms a hospital's position; disagreement, one finder or none sends it to review; a hand-set position is kept."""
import json, re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def psql_file(sql):
    # sudo reads its password from the first line of stdin and the rest goes to psql
    return subprocess.run(["ssh", "-q", "nastemp", "sudo -S -p '' docker exec -i adsb-portal-db psql -U portal -d portal -tA"], input="$TEST_SUDO_PASSWORD" + chr(10) + sql, capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
H = {"X-Requested-With": "XMLHttpRequest"}
def check_one(fid):
    j = adm.post(B + f"/admin/hospitals/{fid}/check", data={"csrf_token": tok(adm, "/admin/hospitals")}, headers=H).json(); return j["ok"], j["message"]
rows = psql("select facility_id from hospitals where state='AR' and address<>'' and lat is not null and pos_source not in ('manual') and facility_id not like 'imp-%' order by facility_id limit 5").split("\n")
h1, h2, h3, h4, h5 = rows
keep = {f: psql(f"select lat||'|'||lon||'|'||pos_source||'|'||pos_check from hospitals where facility_id='{f}'") for f in rows}
info = {}
for f in rows:
    r = psql(f"select address||'~'||city||'~'||state||'~'||zip||'~'||name||'~'||lat||'~'||lon from hospitals where facility_id='{f}'").split("~")
    addr = ", ".join(x for x in (r[0], r[1], f"{r[2]} {r[3]}".strip()) if x)
    info[f] = dict(q=" ".join(addr.split()).lower(), byname=" ".join(f"{r[4]}, {r[1]}, {r[2]}".split()).lower(), lat=float(r[5]), lon=float(r[6]))
def cache(f, finder, hits, which="q"):
    ans = json.dumps(hits).replace("'", "''")
    return f"insert into geocode_cache (finder, query, answer) values ('{finder}', '{info[f][which].replace(chr(39), chr(39) * 2)}', '{ans}') on conflict (finder, query) do update set answer=excluded.answer, fetched=now();\n"
def pt(f, dlat=0.0):
    return {"label": "test answer", "lat": info[f]["lat"] + dlat, "lon": info[f]["lon"]}
sql = "update hospitals set pos_check='', pos_source='census' where facility_id in (" + ",".join(f"'{f}'" for f in rows[:4]) + ");\n"
sql += f"update hospitals set pos_source='manual' where facility_id='{h5}';\n"
sql += cache(h1, "census", [pt(h1)]) + cache(h1, "photon", [pt(h1, 0.0005)])               # about 55 m apart: agree
sql += cache(h2, "census", [pt(h2)]) + cache(h2, "photon", [pt(h2, 0.02)])                 # about 2.2 km apart: conflict
sql += cache(h3, "census", [pt(h3)]) + cache(h3, "photon", []) + cache(h3, "photon", [], "byname")   # only the Census knows it
sql += cache(h4, "census", []) + cache(h4, "photon", []) + cache(h4, "photon", [], "byname")        # neither
psql_file(sql)
t = adm.get(B + "/admin/hospitals").text
check("the page offers Check All Positions with its counts", "Check All Positions" in t and "Need review" in t and "Not checked yet" in t and "/admin/hospitals/verify-all" in t)
check("each hospital has Check This Position", re.search(r"/admin/hospitals/[^/\"]+/check", t) is not None)
check("an ordinary user cannot run the check", all(sess("plain1", "plainpassword1").post(B + p, data={}, allow_redirects=False).status_code in (302, 400, 401, 403, 404) for p in (f"/admin/hospitals/{h1}/check", "/admin/hospitals/verify-all")))
ok, m = check_one(h1)
row = psql(f"select pos_check||'|'||pos_source||'|'||round(lat::numeric,4) from hospitals where facility_id='{h1}'").split("|")
check("when the Census and Photon agree the position is confirmed and kept as verified (Photon's point)", ok and row[0] == "agreed" and row[1] == "verified" and abs(float(row[2]) - (info[h1]["lat"] + 0.0005)) < 0.0002, (m, row))
ok, m = check_one(h2)
row = psql(f"select pos_check||'|'||pos_source||'|'||round(lat::numeric,4) from hospitals where facility_id='{h2}'").split("|")
check("when they disagree it needs review and the position is not changed", not ok and row[0] == "conflict" and row[1] == "census" and abs(float(row[2]) - info[h2]["lat"]) < 0.0002, (m, row))
ok, m = check_one(h3)
check("when only one finder knows the address it needs review", not ok and psql(f"select pos_check from hospitals where facility_id='{h3}'") == "single", m)
ok, m = check_one(h4)
check("when neither knows it, it needs review", not ok and psql(f"select pos_check from hospitals where facility_id='{h4}'") == "none", m)
ok, m = check_one(h5)
check("a position set by hand is kept and counted as set by hand", ok and psql(f"select pos_check||'|'||pos_source from hospitals where facility_id='{h5}'") == "manual|manual", m)
t = adm.get(B + "/admin/hospitals", params={"check": "review"}).text
check("the review list shows the three that need a look and not the confirmed one", all(f'id="h-{x}"' in t for x in (h2, h3, h4)) and f'id="h-{h1}"' not in t, "")
check("a hospital that needs review is tagged on the page", "needs review" in t)
t = adm.get(B + "/admin/hospitals").text
check("the counts reflect the results", re.search(r"Need review</th><td><b>(\d+)</b>", t) and int(re.search(r"Need review</th><td><b>(\d+)</b>", t).group(1)) >= 3 and "Confirmed</th><td>" in t)
check("Data Updates lists the Hospital Positions Check", "Hospital Positions Check" in adm.get(B + "/admin/data-updates/").text)
restore = ""
for f in rows:
    la, lo, src, chk = keep[f].split("|")
    restore += f"update hospitals set lat={la}, lon={lo}, pos_source='{src}', pos_check='{chk}' where facility_id='{f}';\n"
restore += "delete from geocode_cache where answer like '%test answer%' or query in (" + ",".join("'" + info[f][k].replace("'", "''") + "'" for f in rows for k in ("q", "byname")) + ");\n"
psql_file(restore)
print("ALL PASS" if ok_all else "SOME FAILED")
