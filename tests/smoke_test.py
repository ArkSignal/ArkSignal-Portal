import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ADMIN=["/admin/","/admin/users","/admin/roles","/admin/feeders","/admin/audit","/admin/signins","/admin/settings","/admin/smtp","/admin/airports","/admin/backups","/admin/updates","/admin/tools","/view/statistics",
       "/my/","/my/password","/feeders/","/feed/","/airports/","/airports/KLIT","/healthz","/","/live"]
PUBLIC=["/history/","/about","/","/live","/view/statistics","/feeders/","/feed/","/airports/","/airports/KLIT","/my/login","/my/register","/healthz"]
adm=sess("marty",PW); bad=[]
for p in ADMIN:
    r=adm.get(B+p)
    if r.status_code!=200: bad.append(("admin",p,r.status_code))
anon=requests.Session()
for p in PUBLIC:
    r=anon.get(B+p)
    if r.status_code not in (200,403): bad.append(("anon",p,r.status_code))
for p in ["/admin/","/admin/users","/my/"]:
    r=anon.get(B+p,allow_redirects=False)
    if r.status_code!=302: bad.append(("anon-protected",p,r.status_code))
print("SMOKE PASS: %d admin pages, %d public pages"%(len(ADMIN),len(PUBLIC)) if not bad else "SMOKE FAIL: %s"%bad)
