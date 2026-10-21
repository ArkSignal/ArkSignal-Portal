import os
import re, requests, sys
B=f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430"
PW=re.search(r"password: (\S+)", open(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..", *r"TEST_CREDENTIALS.md".split("\\"))).read()).group(1)
def sess(user, pw):
    s=requests.Session()
    t=re.search(r'name="csrf_token" value="([^"]+)"', s.get(B+"/my/login").text).group(1)
    r=s.post(B+"/my/login", data={"csrf_token":t,"username":user,"password":pw}, allow_redirects=False)
    assert r.status_code==302, (user, r.status_code)
    return s
def tok(s, path):
    return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B+path).text).group(1)
def act(s, path, page, **data):
    data["csrf_token"]=tok(s, page)
    r=s.post(B+path, data=data, headers={"X-Requested-With":"XMLHttpRequest"})
    j=r.json(); return j["ok"], j["message"], j.get("html","")
ok_all=True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ")+name, extra if not cond else "")
    ok_all &= bool(cond)
adm=sess("marty", PW)
# roles page
html=adm.get(B+"/admin/roles").text
check("roles page lists User and Admin", "role-name\">User" in html and "role-name\">Admin" in html)
# create role
ok,msg,html=act(adm,"/admin/roles/create","/admin/roles",name="Feeder Moderator")
check("create role", ok, msg)
ok,msg,_=act(adm,"/admin/roles/create","/admin/roles",name="feeder moderator")
check("duplicate role name rejected (case-insensitive)", not ok, msg)
rid=int(re.search(r'id="role-(\d+)"[^>]*>\s*<summary>\s*<span class="role-name">Feeder Moderator', html).group(1))
# save with permissions
ok,msg,html=act(adm,f"/admin/roles/{rid}/save","/admin/roles",name="Feeder Moderator",description="Moderates feeders",permissions=["manage_feeders","view_history"])
check("save role perms", ok and "2 permissions" in html, msg)
# make a test user; register fresh
s2=requests.Session()
t=re.search(r'name="csrf_token" value="([^"]+)"', s2.get(B+"/my/register").text).group(1)
s2.post(B+"/my/register", data={"csrf_token":t,"username":"mod1","email":"mod1@example.com","password":"modpassword11","confirm":"modpassword11"})
# find mod1 id via users page
uh=adm.get(B+"/admin/users").text
uid=int(re.search(r'<td>(?:<a [^>]*>)?mod1(?:</a>)?</td>.*?/admin/users/(\d+)/roles', uh, re.S).group(1))
ok,msg,_=act(adm,f"/admin/users/{uid}/roles","/admin/users",action="assign",role_id=rid)
check("assign role to user", ok, msg)
mod=sess("mod1","modpassword11")
check("mod1 sees admin overview", mod.get(B+"/admin/").status_code==200)
check("mod1 cannot open Users (no manage_users)", mod.get(B+"/admin/users").status_code==404)
check("mod1 cannot open Roles", mod.get(B+"/admin/roles").status_code==404)
# add manage_roles to role; then mod1 can open roles page but cannot escalate
ok,msg,_=act(adm,f"/admin/roles/{rid}/save","/admin/roles",name="Feeder Moderator",description="",permissions=["manage_feeders","view_history","manage_roles"])
mod=sess("mod1","modpassword11")
check("mod1 opens Roles after manage_roles", mod.get(B+"/admin/roles").status_code==200)
ok,msg,_=act(mod,f"/admin/roles/{rid}/save","/admin/roles",name="Feeder Moderator",description="",permissions=["manage_feeders","view_history","manage_roles","manage_settings"])
check("mod1 cannot grant a permission they lack", not ok, msg)
ok,msg,_=act(mod,f"/admin/roles/{rid}/save","/admin/roles",name="Feeder Moderator",description="",permissions=["manage_roles"],is_super_admin="1")
check("mod1 cannot make a role Super Admin", not ok, msg)
admin_rid=int(re.search(r'id="role-(\d+)"[^>]*>\s*<summary>\s*<span class="role-name">Admin', adm.get(B+"/admin/roles").text).group(1))
ok,msg,_=act(mod,f"/admin/roles/{admin_rid}/members","/admin/roles",action="add",user_id=uid)
check("mod1 cannot add themselves to Admin", not ok, msg)
# last super admin guards
marty_id=int(re.search(r'<td>(?:<a [^>]*>)?marty(?:</a>)?</td>.*?/admin/users/(\d+)', adm.get(B+"/admin/users").text, re.S).group(1))
ok,msg,_=act(adm,f"/admin/users/{marty_id}/roles","/admin/users",action="remove",role_id=admin_rid)
check("cannot remove last Super Admin's Admin role", not ok, msg)
ok,msg,_=act(adm,f"/admin/roles/{admin_rid}/delete","/admin/roles")
check("built-in Admin role cannot be deleted", not ok, msg)
ok,msg,_=act(adm,f"/admin/roles/{admin_rid}/save","/admin/roles",name="Admin",description="",permissions=[])
check("cannot unset Super Admin on the last super admin role", not ok, msg)
# delete confirmation names members
h=adm.get(B+"/admin/roles").text
check("delete confirm names members", "mod1" in re.search(r'action="/admin/roles/%d/delete"[^>]*data-confirm="([^"]*)"'%rid, h).group(1))
# member remove + delete
ok,msg,_=act(adm,f"/admin/roles/{rid}/members","/admin/roles",action="remove",user_id=uid)
check("remove member", ok, msg)
ok,msg,_=act(adm,f"/admin/roles/{rid}/delete","/admin/roles")
check("delete role", ok, msg)
# cleanup user mod1
act(adm,f"/admin/users/{uid}","/admin/users",action="disable")
print("ALL PASS" if ok_all else "SOME FAILED")
