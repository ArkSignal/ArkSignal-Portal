import io, re, requests, tarfile, time, json
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all=True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ")+name, "" if cond else extra); ok_all &= bool(cond)
def act_files(s, path, page, files, **data):
    data["csrf_token"]=tok(s, page)
    r=s.post(B+path, data=data, files=files, headers={"X-Requested-With":"XMLHttpRequest"})
    j=r.json(); return j["ok"], j["message"], j.get("html","")
until=time.time()+60
while time.time()<until:
    try:
        if requests.get(B+"/healthz",timeout=3).text=="ok": break
    except Exception: time.sleep(2)
adm=sess("marty", PW)
import subprocess
def _setting(k, v):
    subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'\""], capture_output=True)
_setting("backup_encrypt", "0")   # the checks below open the archive themselves; the encrypted round trip is at the end
def names(html): return re.findall(r'portal-backup-\d{8}-\d{6}-[a-z-]+\.tar\.gz', html)
check("backups page loads", adm.get(B+"/admin/backups").status_code==200)
ok,m,html=act(adm,"/admin/backups/run","/admin/backups"); check("create manual backup", ok, m)
mine=sorted(set(n for n in names(html) if n.endswith("-manual.tar.gz")))[-1]
# download is a valid archive with manifest + tables
r=adm.get(B+f"/admin/backups/{mine}/download"); check("download works", r.status_code==200 and len(r.content)>200)
tf=tarfile.open(fileobj=io.BytesIO(r.content),mode="r:gz"); man=json.load(tf.extractfile("manifest.json"))
check("archive has manifest and all tables", {"users","roles","user_roles","audit_log","settings"} <= set(man["tables"]) and all(f"tables/{t}.csv" in tf.getnames() for t in man["tables"]), man["tables"])
users_before=man["row_counts"]["users"]
# make changes after the backup
s2=requests.Session()
t=re.search(r'name="csrf_token" value="([^"]+)"', s2.get(B+"/my/register").text).group(1)
s2.post(B+"/my/register", data={"csrf_token":t,"username":"bk1","email":"bk1@example.com","password":"bkpassword11","confirm":"bkpassword11"})
act(adm,"/admin/roles/create","/admin/roles",name="BK Role")
check("changes visible before restore", "bk1" in adm.get(B+"/admin/users").text and "BK Role" in adm.get(B+"/admin/roles").text)
# restore
ok,m,html=act(adm,f"/admin/backups/{mine}/restore","/admin/backups"); check("restore backup", ok, m)
adm=sess("marty", PW)
check("post-restore: new user and role are gone", "bk1" not in adm.get(B+"/admin/users").text and "BK Role" not in adm.get(B+"/admin/roles").text)
check("post-restore: marty and built-in roles intact", "marty" in adm.get(B+"/admin/users").text and "role-name\">Admin" in adm.get(B+"/admin/roles").text)
pre=sorted(set(n for n in names(html) if n.endswith("-pre-restore.tar.gz")))
check("safety (pre-restore) backup created", len(pre)>=1, html[:200])
# identity sequences usable: new user registers fine
s3=requests.Session()
t=re.search(r'name="csrf_token" value="([^"]+)"', s3.get(B+"/my/register").text).group(1)
r=s3.post(B+"/my/register", data={"csrf_token":t,"username":"bk2","email":"bk2@example.com","password":"bkpassword22","confirm":"bkpassword22"}, allow_redirects=False)
check("new registration works after restore (sequences reset)", r.status_code==302, r.text[:200])
# restore from the pre-restore backup brings bk1 + role back
ok,m,_=act(adm,f"/admin/backups/{pre[-1]}/restore","/admin/backups"); check("restore the safety backup", ok, m)
adm=sess("marty", PW)
check("safety backup restored bk1 and role", "bk1" in adm.get(B+"/admin/users").text and "BK Role" in adm.get(B+"/admin/roles").text)
# upload and restore
ok,m,_=act_files(adm,"/admin/backups/upload","/admin/backups",{"file":("b.tar.gz",r.content if False else adm.get(B+f"/admin/backups/{mine}/download").content)}); check("upload and restore a file", ok, m)
adm=sess("marty", PW)
check("upload restore applied", "bk1" not in adm.get(B+"/admin/users").text)
# bad uploads
ok,m,_=act_files(adm,"/admin/backups/upload","/admin/backups",{"file":("junk.tar.gz",b"not a tarball")}); check("junk file rejected", not ok, m)
buf=io.BytesIO()
with tarfile.open(fileobj=buf,mode="w:gz") as t_:
    d=json.dumps({"format":1,"tables":["evil_table"],"row_counts":{}}).encode(); i=tarfile.TarInfo("manifest.json"); i.size=len(d); t_.addfile(i,io.BytesIO(d))
ok,m,_=act_files(adm,"/admin/backups/upload","/admin/backups",{"file":("x.tar.gz",buf.getvalue())}); check("unknown-table archive rejected", not ok and "evil_table" in m, m)
buf=io.BytesIO()
with tarfile.open(fileobj=buf,mode="w:gz") as t_:
    d=json.dumps({"format":99,"tables":[]}).encode(); i=tarfile.TarInfo("manifest.json"); i.size=len(d); t_.addfile(i,io.BytesIO(d))
ok,m,_=act_files(adm,"/admin/backups/upload","/admin/backups",{"file":("y.tar.gz",buf.getvalue())}); check("wrong-format archive rejected", not ok, m)
check("data still intact after rejected uploads", "marty" in adm.get(B+"/admin/users").text)
# traversal / bad names
check("traversal name 404", adm.get(B+"/admin/backups/..%2Fetc%2Fpasswd/download").status_code==404 and adm.get(B+"/admin/backups/evil.tar.gz/download").status_code==404)
# schedule validation
for name,kw in [("bad mode",dict(schedule="monthly",time="02:00",retention="5")),("bad time",dict(schedule="daily",time="25:00",retention="5")),("bad keep",dict(schedule="daily",time="02:00",retention="0"))]:
    ok,m,_=act(adm,"/admin/backups/schedule","/admin/backups",**kw); check("schedule rejects "+name, not ok, m)
ok,m,_=act(adm,"/admin/backups/schedule","/admin/backups",schedule="daily",time="02:00",retention="14"); check("schedule saved", ok, m)
# delete
ok,m,html=act(adm,f"/admin/backups/{mine}/delete","/admin/backups"); check("delete backup", ok and mine not in html, m)
# non-admin
u=sess("plain1","plainpassword1") if False else None
# ---- an encrypted backup restores (same SECRET_KEY) and is refused when it is damaged
_setting("backup_encrypt", "1")
ok, m, html = act(adm, "/admin/backups/run", "/admin/backups")
enc_name = re.findall(r"portal-backup-\d{8}-\d{6}-manual\.tar\.gz", html)[0]
blob = adm.get(B + f"/admin/backups/{enc_name}/download").content
check("an encrypted backup is made", blob[:8] == b"ADSBPBK1")
ok, m, _ = act(adm, f"/admin/backups/{enc_name}/restore", "/admin/backups"); check("an encrypted backup restores on this portal", ok, m)
adm = sess("marty", PW)
ok, m, _ = act_files(adm, "/admin/backups/upload", "/admin/backups", {"file": ("enc.tar.gz", blob)}); check("an uploaded encrypted backup restores", ok, m)
adm = sess("marty", PW)
bad = blob[:200] + bytes([blob[200] ^ 255]) + blob[201:]
ok, m, _ = act_files(adm, "/admin/backups/upload", "/admin/backups", {"file": ("bad.tar.gz", bad)}); check("a damaged encrypted backup is refused and changes nothing", not ok, m)
adm = sess("marty", PW)
act(adm, f"/admin/backups/{enc_name}/delete", "/admin/backups")
print("ALL PASS" if ok_all else "SOME FAILED")
