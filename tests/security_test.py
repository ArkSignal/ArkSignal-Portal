import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)
    ok_all &= bool(cond)
adm = sess("marty", PW)
st = adm.get(B + "/admin/settings").text
sec = st.split('id="sec-security"')[1].split("</details>")[0]
for label in ("Sign Out After Inactivity (Minutes)", "Inactivity Warning (Seconds)", "Administrator Inactivity Limit (Minutes)", "Administrator Longest Session (Hours)",
              "Longest Sign-In Session (Hours)", "Passwords Need Letters and Numbers", "Passwords Expire After (Days)", "Minimum Password Length", "Failed Sign-In Attempts",
              "Visitor Address Headers", "Password Reset With a Station", "Password Reset Attempts per Hour", "Sign Everyone Out Now", "Failed Sign-Ins Allowed per Address", "Send Browser Security Headers", "Keep Ended Sign-Ins in the List For"):
    check("Security section has: " + label, label in sec, label)
check("the old Accounts section no longer holds sign-in settings", "Longest Sign-In Session" not in st.split('id="sec-accounts"')[1].split("</details>")[0])
def save(**kw):
    d = dict(max_failed_attempts="5", lockout_minutes="15", idle_timeout_minutes="120", idle_warning_seconds="60", admin_idle_timeout_minutes="30", admin_session_hours="8",
             session_hours="12", password_require_mix="0", password_max_age_days="0", min_password_length="12", client_ip_headers="CF-Connecting-IP, X-Real-IP",
             reset_by_station="1", reset_token_minutes="60", reset_attempts_per_hour="5", signin_ip_failures="0", signin_ip_window_minutes="15", security_headers="1", session_keep_days="30")
    d.update(kw)
    return act(adm, "/admin/settings/save", "/admin/settings", section="security", **d)
ok, m, _ = save(); check("Security saves as one section", ok, m)
for name, kw in (("idle too long", dict(idle_timeout_minutes="99999")), ("negative age", dict(password_max_age_days="-1")), ("warning too short", dict(idle_warning_seconds="1")), ("min length too small", dict(min_password_length="3"))):
    ok, m, _ = save(**kw); check("reject: " + name, not ok, m)
ok, m, _ = save(password_require_mix="1"); check("turn on the letters-and-numbers rule", ok, m)
r = requests.Session()
t = re.search(r'name="csrf_token" value="([^"]+)"', r.get(B + "/my/register").text).group(1)
reg = lambda pw: r.post(B + "/my/register", data={"csrf_token": t, "username": "sec_user1", "email": "sec_user1@example.com", "password": pw, "confirm": pw})
check("registration page states the rules", "letters and numbers" in requests.get(B + "/my/register").text)
check("a password without a number is refused", "letters and numbers" in " ".join(re.findall(r'class="flash[^>]*>([^<]*)', reg("onlyletterspassword").text)))
save(password_require_mix="0")
check("setting changes are audited", "password_require_mix" in adm.get(B + "/admin/audit?action=settings.update").text)
print("ALL PASS" if ok_all else "SOME FAILED")
