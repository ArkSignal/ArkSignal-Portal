import re, requests, time, os
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])  # helpers: sess, tok, act, B, PW
ok_all=True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ")+name, "" if cond else extra); ok_all &= bool(cond)
LOG="smtp_received.txt"
adm=sess("marty", PW)
HOST="test-box.invalid"
def save(**kw):
    d=dict(host=HOST, port="2527", security="none", username="", password="", **{"from":"portal@example.com"}); d.update(kw)
    return act(adm,"/admin/smtp/save","/admin/smtp",**d)
def test(to="you@example.com"):
    return act(adm,"/admin/smtp/test","/admin/smtp",recipient=to)
check("SMTP page loads", adm.get(B+"/admin/smtp").status_code==200)
ok,m,_=save(port="99999"); check("bad port rejected", not ok, m)
ok,m,_=save(**{"from":"nope"}); check("bad from rejected", not ok, m)
ok,m,_=save(); check("save plain config", ok, m)
ok,m,_=test(); check("test email (no encryption)", ok, m)
time.sleep(1)
log=open(LOG).read() if os.path.exists(LOG) else ""
check("fake server received it unencrypted", "[port 2527 plain encrypted=False]" in log and "test message" in log, log[-200:])
ok,m,_=save(port="2525", security="starttls", skip_cert="1", username="u", password="goodpass1"); check("save starttls", ok, m)
ok,m,_=test(); check("test email (STARTTLS, skip cert, login)", ok, m); time.sleep(1)
check("received via STARTTLS encrypted", "[port 2525 starttls encrypted=True]" in open(LOG).read())
ok,m,_=save(port="2525", security="starttls", username="u"); 
ok,m,_=test(); check("STARTTLS without skip-cert fails with cert hint", (not ok) and "Skip Certificate Check" in m, m)
ok,m,_=save(port="2526", security="ssl", skip_cert="1", username="u"); ok,m,_=test(); check("test email (SSL/TLS)", ok, m)
ok,m,_=save(port="2525", security="starttls", skip_cert="1", username="u", password="wrong"); ok,m,_=test(); check("wrong password reported", (not ok) and "rejected the username" in m, m)
ok,m,_=save(port="2526", security="starttls", skip_cert="1", username="u", password="goodpass1"); ok,m,_=test(); check("port/security mismatch gives hint", not ok and ("465" in m or "SSL" in m), m)
ok,m,_=save(host="no-such-host.invalid", port="587", security="starttls"); ok,m,_=test(); check("unknown host message", not ok and "Could not find a server" in m, m)
ok,m,_=save(); ok,m,_=test("bad"); check("bad recipient rejected", not ok, m)
page=adm.get(B+"/admin/smtp").text
check("password never shown in page", "goodpass1" not in page and "wrong" not in page)
check("saved-password hint shown", "Saved. Leave blank to keep it." in page)
ok,m,_=save(clear_password="1"); page=adm.get(B+"/admin/smtp").text
check("clear saved password", "Saved. Leave blank" not in page)
# permission check
s2=requests.Session()
t=re.search(r'name="csrf_token" value="([^"]+)"', s2.get(B+"/my/register").text).group(1)
s2.post(B+"/my/register", data={"csrf_token":t,"username":"plain1","email":"plain1@example.com","password":"plainpassword1","confirm":"plainpassword1"})
u=sess("plain1","plainpassword1")
check("regular user gets 404 on SMTP page", u.get(B+"/admin/smtp").status_code==404)
r=u.post(B+"/admin/smtp/save", data={"csrf_token":tok(u,"/my/"),"host":"evil"}, headers={"X-Requested-With":"XMLHttpRequest"}); check("regular user cannot save SMTP", r.status_code==404)
# audit
print("ALL PASS" if ok_all else "SOME FAILED")
