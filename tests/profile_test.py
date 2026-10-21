import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)
    ok_all &= bool(cond)
def flashes(r): return " ".join(re.findall(r'class="flash[^>]*>([^<]*)', r.text))
u = requests.Session()
t = re.search(r'name="csrf_token" value="([^"]+)"', u.get(B + "/my/register").text).group(1)
u.post(B + "/my/register", data={"csrf_token": t, "username": "emailtest1", "email": "emailtest1@example.com", "password": "emailtestpass1", "confirm": "emailtestpass1"})
tl = re.search(r'name="csrf_token" value="([^"]+)"', u.get(B + "/my/login").text).group(1)
u.post(B + "/my/login", data={"csrf_token": tl, "username": "emailtest1", "password": "emailtestpass1"})
page = u.get(B + "/my/profile").text
check("profile offers Change Email Address", "Change Email Address" in page and 'name="new_email"' in page)
tok = lambda: re.search(r'name="csrf_token" value="([^"]+)"', u.get(B + "/my/profile").text).group(1)
def change(new, pw):
    return u.post(B + "/my/profile/email", data={"csrf_token": tok(), "new_email": new, "current_password": pw})
check("wrong password is refused", "current password is not correct" in flashes(change("emailtest1b@example.com", "wrong-password")))
check("not an address is refused", "valid email" in flashes(change("not-an-address", "emailtestpass1")))
check("the same address is refused", "already your email" in flashes(change("EmailTest1@example.com", "emailtestpass1")))
r = change("emailtest1b@example.com", "emailtestpass1")
check("a correct change succeeds", "has been changed" in flashes(r), flashes(r))
check("the profile shows the new address", 'value="emailtest1b@example.com"' in u.get(B + "/my/profile").text)
adm = sess("marty", PW)
au = adm.get(B + "/admin/audit?action=user.email_change").text
check("the change is audited with old and new address", "emailtest1@example.com -&gt; emailtest1b@example.com" in au or "emailtest1@example.com -> emailtest1b@example.com" in au, au[:200])
check("failed attempts are audited", "user.email_change_failed" in adm.get(B + "/admin/audit?action=user.email_change_failed").text)
other = requests.Session()
t2 = re.search(r'name="csrf_token" value="([^"]+)"', other.get(B + "/my/register").text).group(1)
other.post(B + "/my/register", data={"csrf_token": t2, "username": "emailtest2", "email": "emailtest2@example.com", "password": "emailtestpass2", "confirm": "emailtestpass2"})
tl2 = re.search(r'name="csrf_token" value="([^"]+)"', other.get(B + "/my/login").text).group(1)
other.post(B + "/my/login", data={"csrf_token": tl2, "username": "emailtest2", "password": "emailtestpass2"})
t3 = re.search(r'name="csrf_token" value="([^"]+)"', other.get(B + "/my/profile").text).group(1)
r = other.post(B + "/my/profile/email", data={"csrf_token": t3, "new_email": "EMAILTEST1B@example.com", "current_password": "emailtestpass2"})
check("another account cannot take an address in use (any capitalization)", "already exists" in flashes(r), flashes(r))
check("signed-out visitors cannot change anything", requests.post(B + "/my/profile/email", data={}, allow_redirects=False).status_code in (302, 400, 401))
print("ALL PASS" if ok_all else "SOME FAILED")
