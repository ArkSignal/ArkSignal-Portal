import re, requests, time
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
_s=requests.Session(); _t=re.search(r'name="csrf_token" value="([^"]+)"', _s.get(B+"/my/register").text).group(1)
_s.post(B+"/my/register", data={"csrf_token":_t,"username":"locktest1","email":"locktest1@example.com","password":"lockpassword1","confirm":"lockpassword1"})
base=dict(site_name="ArkSignal", registration_open="1", default_privacy="general", feed_mode="open",
          map_lat="34.80", map_lon="-92.30", map_zoom="7", timezone="America/Chicago", max_failed_attempts="5", lockout_minutes="15", history_retention_days="730", sample_retention_days="30", feed_host="feed.arksignal.allisononline.org", feed_port="30004", mlat_port="31090", max_rules_per_user="25")
def save(**kw):
    d=dict(base); d.update(kw)
    for k in [k for k,v in d.items() if v is None]: d.pop(k)
    return act(adm,"/admin/settings/save","/admin/settings",**d)
check("settings page loads", adm.get(B+"/admin/settings").status_code==200)
ok,m,_=save(); check("save defaults", ok, m)
ok,m,_=save(); check("no-change save reports no changes", ok and "No changes" in m, m)
for name,kw in [("empty site name",dict(site_name="")),("bad privacy",dict(default_privacy="x")),("bad feed mode",dict(feed_mode="x")),
                ("lat out of range",dict(map_lat="95")),("lat not number",dict(map_lat="abc")),("zoom out of range",dict(map_zoom="30")),
                ("bad timezone",dict(timezone="Mars/Base")),("attempts too low",dict(max_failed_attempts="1")),("lockout not number",dict(lockout_minutes="x"))]:
    ok,m,_=save(**kw); check("reject: "+name, not ok, m)
ok,m,_=save(site_name="Test Network Name", timezone="UTC", max_failed_attempts="3", lockout_minutes="1"); check("save changes", ok, m)
check("site name used in page title/footer", "Test Network Name" in adm.get(B+"/admin/").text)
# registration closed
ok,m,_=save(site_name="Test Network Name", timezone="UTC", max_failed_attempts="3", lockout_minutes="1", registration_open=None); check("close registration", ok, m)
anon=requests.Session()
r=anon.get(B+"/my/register"); check("register page shows closed (403)", r.status_code==403 and "Registration Closed" in r.text)
check("Register link hidden when closed", "/my/register" not in anon.get(B+"/my/login").text)
t=re.search(r'name="csrf_token" value="([^"]+)"', anon.get(B+"/my/login").text).group(1)
r=anon.post(B+"/my/register", data={"csrf_token":t,"username":"late1","email":"late1@example.com","password":"latepassword1","confirm":"latepassword1"}); check("POST register blocked when closed", r.status_code==403)
# lockout uses 3 attempts (throwaway user so the shared test user is never locked)
s=requests.Session()

for i in range(3):
    t=re.search(r'name="csrf_token" value="([^"]+)"', s.get(B+"/my/login").text).group(1)
    h=s.post(B+"/my/login", data={"csrf_token":t,"username":"locktest1","password":"wrongwrongwrong"}).text
t=re.search(r'name="csrf_token" value="([^"]+)"', s.get(B+"/my/login").text).group(1)
h2=s.post(B+"/my/login", data={"csrf_token":t,"username":"locktest1","password":"lockpassword1"}).text
check("lockout after 3 attempts per setting (the right password is then refused too, with the same plain answer that never says whether the account exists)", "Incorrect username or password" in h and "Incorrect username or password" in h2 and "Sign Out" not in h2, re.findall(r'class="flash[^<]*',h2))
# last sign-in in UTC now; restore defaults
ok,m,_=save(); check("restore defaults", ok, m)
audit=adm.get(B+"/admin/settings").text
check("restored values shown", 'value="ArkSignal"' in audit)
pg = adm.get(B+"/admin/settings").text
check("county hover is a setting, off by default", 'name="county_hover"' in pg and 'name="county_hover"' in pg and re.search(r'name="county_hover".*?<option value="0" selected', pg, re.S) is not None)
cm = requests.get(B+"/counties/").text
check("the county map has a hover switch and the site default reaches the page", 'id="cm-hover"' in cm and '"hover": "0"' in cm)
print("ALL PASS" if ok_all else "SOME FAILED")
