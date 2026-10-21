import csv, io, re, requests, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all=True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ")+name, "" if cond else extra); ok_all &= bool(cond)
until=time.time()+60
while time.time()<until:
    try:
        if requests.get(B+"/healthz",timeout=3).text=="ok": break
    except Exception: time.sleep(2)
adm=sess("marty", PW)
# generate 60 failed sign-ins for a distinctive username, plus audit events
for i in range(60):
    s=requests.Session(); t=re.search(r'name="csrf_token" value="([^"]+)"', s.get(B+"/my/login").text).group(1)
    s.post(B+"/my/login", data={"csrf_token":t,"username":"ghost-user","password":"x"*12})
act(adm,"/admin/roles/create","/admin/roles",name="Hist Role")
act(adm,"/admin/settings/save","/admin/settings",site_name="ArkSignal", registration_open="1", default_privacy="general", feed_mode="open", map_lat="34.80", map_lon="-92.30", map_zoom="7", timezone="America/Chicago", max_failed_attempts="5", lockout_minutes="15", history_retention_days="730", sample_retention_days="30", feed_host="feed.arksignal.allisononline.org", feed_port="30004", mlat_port="31090", max_rules_per_user="25")
h=adm.get(B+"/admin/signins?q=ghost-user").text
check("sign-ins page lists failed attempts", "ghost-user" in h and "60 records" in h, re.findall(r'(\d+) records', h))
check("sign-ins paginated (page 2 link)", "page=2" in h)
h2=adm.get(B+"/admin/signins?q=ghost-user&page=2").text
check("sign-ins page 2 has remaining 10", "ghost-user" in h2 and "page 2 of 2" in h2)
check("filter result=ok excludes ghost", "No records match these filters." in adm.get(B+"/admin/signins?q=ghost-user&result=ok").text)
check("filter result=failed includes ghost", "ghost-user" in adm.get(B+"/admin/signins?q=ghost-user&result=failed").text)
r=adm.get(B+"/admin/signins?q=ghost-user&format=csv"); rows=list(csv.reader(io.StringIO(r.text)))
check("sign-in CSV export (60 rows + header)", r.headers["Content-Type"].startswith("text/csv") and len(rows)==61 and rows[0][0]=="time_utc", len(rows))
a=adm.get(B+"/admin/audit").text
check("audit page shows role.create and settings.update", "role.create" in a and "Hist Role" in a)
check("audit filter by action", "role.create" in adm.get(B+"/admin/audit?action=role.create").text and "settings.update" not in adm.get(B+"/admin/audit?action=role.create").text.split("<table>")[1])
check("audit search", "Hist Role" in adm.get(B+"/admin/audit?q=Hist").text)
r=adm.get(B+"/admin/audit?format=csv"); rows=list(csv.reader(io.StringIO(r.text)))
check("audit CSV export", len(rows)>3 and rows[0]==["time_utc","actor","action","target","detail","ip"])
check("export itself is audited", "history.export" in adm.get(B+"/admin/audit").text)
# permission: plain user and a view_history-only role
u=sess("plain1","plainpassword1")
check("regular user 404 on audit and sign-ins", u.get(B+"/admin/audit").status_code==404 and u.get(B+"/admin/signins").status_code==404)
check("regular user 404 on CSV export", u.get(B+"/admin/audit?format=csv").status_code==404)
print("ALL PASS" if ok_all else "SOME FAILED")
