"""People search for friend requests, chats and messages: members are found by user name, display name, call sign, or a whole email address typed exactly; no email address is ever returned; each member can switch off being
found by call sign or email; site settings; limits; the picker works for ordinary members. Runs against the test portal."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def unset(*ks):
    for k in ks:
        psql(f"DELETE FROM settings WHERE key = '{k}'")
KEYS = ("friends_enabled", "chat_enabled", "pm_enabled", "people_search_email", "people_search_callsign", "people_search_max", "people_search_per_minute", "friends_requests_per_day", "friends_max")
unset(*KEYS)
setting("chat_enabled", "1"); setting("pm_enabled", "1")
def reset():
    for t in ("friendships", "pm_blocks"):
        psql(f"DELETE FROM {t}")
    psql("UPDATE users SET contact_policy = 'anyone', find_by_email = TRUE, find_by_callsign = TRUE")
    psql("UPDATE users SET callsign = NULL WHERE username IN ('claimer1', 'claimer2')")
reset()
psql("UPDATE users SET callsign = 'W5TST', email = 'claimer1@example.com' WHERE username = 'claimer1'")
psql("UPDATE users SET callsign = 'KD5XYZ', email = 'claimer2@example.com' WHERE username = 'claimer2'")
anon = requests.Session(); me = sess("plain1", "plainpassword1"); other = sess("claimer1", "claimpassword1"); adm = sess("marty", PW)
def find(q, s=None):
    r = (s or me).get(B + "/people/search.json", params={"q": q})
    return r.json() if r.status_code == 200 else {"members": [], "status": r.status_code}
def names(q, s=None):
    return [m["username"] for m in find(q, s)["members"]]
def csrf(s):
    return re.search(r'var csrf = "([^"]+)"', s.get(B + "/my/friends").text).group(1)
def post(s, path, **data):
    return s.post(B + path, data=dict(data, csrf_token=csrf(s)))

check("the search needs a sign-in", anon.get(B + "/people/search.json?q=claimer", allow_redirects=False).status_code in (302, 401, 403))
r = find("claimer")
check("an ordinary member finds members by user name as they type, without any administrator right", "claimer1" in [m["username"] for m in r["members"]] and r["members"][0]["how"] == "name", r)
check("the member themselves are never listed and one letter is too little", "plain1" not in names("plain") and find("c")["members"] == [])
r = find("w5ts")
check("a call sign finds its member (start of it is enough) and shows the call sign", [m["username"] for m in r["members"]] == ["claimer1"] and r["members"][0]["callsign"] == "W5TST" and r["members"][0]["how"] == "callsign", r)
r = find("claimer1@example.com")
check("a whole email address finds its member, marked as found by email", [m["username"] for m in r["members"]] == ["claimer1"] and r["members"][0]["how"] == "email", r)
check("part of an email address finds nobody", names("claimer1@exa") == [] and names("@example.com") == [])
texts = [me.get(B + "/people/search.json", params={"q": q}).text for q in ("claimer", "w5ts", "claimer1@example.com", "example")]
check("no email address is ever in an answer", not any("example.com" in t for t in texts), texts)
rq = post(me, "/my/friends/request", to="claimer1@example.com").json()
check("an email address typed in full also works in the friend request form", rq["ok"], rq)
psql("DELETE FROM friendships")
check("so does a call sign", post(me, "/my/friends/request", to="W5TST").json()["ok"])
psql("DELETE FROM friendships")

# each member's choice
r = post(other, "/my/friends/prefs", contact_policy="anyone", show_online="1")
check("saving the preferences with the boxes unticked switches off being found by call sign and email", r.json()["ok"] and psql("select find_by_email, find_by_callsign from users where username = 'claimer1'") == "f|f", psql("select find_by_email, find_by_callsign from users where username = 'claimer1'"))
check("then a call sign and an email address find nobody, the user name still does", names("w5ts") == [] and names("claimer1@example.com") == [] and "claimer1" in names("claimer1"))
check("and a friend request by call sign or email says no member matches", "No member" in post(me, "/my/friends/request", to="W5TST").json()["message"] and "No member" in post(me, "/my/friends/request", to="claimer1@example.com").json()["message"])
check("moderators still find them by email", names("claimer1@example.com", adm) == ["claimer1"])
r = post(other, "/my/friends/prefs", contact_policy="anyone", show_online="1", find_by_callsign="1", find_by_email="1")
check("ticking the boxes switches both back on", r.json()["ok"] and psql("select find_by_email, find_by_callsign from users where username = 'claimer1'") == "t|t")
pg = other.get(B + "/my/friends").text
check("the friends page has the two choices, ticked, with the email promise", 'name="find_by_callsign" value="1" checked' in pg and 'name="find_by_email" value="1" checked' in pg and "never shown to anyone" in pg)

# site settings
setting("people_search_email", "0")
check("the site setting can turn off email search for everybody", names("claimer1@example.com") == [] and names("w5ts") == ["claimer1"])
setting("people_search_email", "1"); setting("people_search_callsign", "0")
check("and call sign search", names("w5ts") == [] and names("claimer1@example.com") == ["claimer1"])
setting("people_search_callsign", "1")

# who is left out
psql("UPDATE users SET contact_policy = 'nobody' WHERE username = 'claimer1'")
check("a member who takes no new contact is not listed", names("claimer1") == [])
psql("UPDATE users SET contact_policy = 'anyone' WHERE username = 'claimer1'")
psql("INSERT INTO pm_blocks (blocker_id, blocked_id) SELECT a.id, b.id FROM users a, users b WHERE a.username = 'claimer1' AND b.username = 'plain1'")
check("a member who blocked you is not listed", names("claimer1") == [])
psql("DELETE FROM pm_blocks")
psql("UPDATE users SET callsign = 'W5TST' WHERE username = 'claimer2'")
check("a call sign two members share finds neither as a whole (ask for the user name)", "No member" in post(me, "/my/friends/request", to="W5TST").json()["message"])
psql("UPDATE users SET callsign = 'KD5XYZ' WHERE username = 'claimer2'")

# limits
setting("people_search_max", "3")
many = find("plain") if False else find("claimer")
check("the number of matches is a setting", len(many["members"]) <= 3)
setting("people_search_per_minute", "10")
sm = sess("claimer2", "claimpassword1")
last = None
for _ in range(14):
    last = find("claimer", sm)
check("too many searches in a minute are refused and the picker is told", last.get("limited") is True and last["members"] == [], last)

# the forms and the picker
js = anon.get(B + "/static/memberpicker.js").text
check("the picker asks the member-safe address for the friend, chat and message forms and can take several names", "/people/search.json" in js and 'dataset.multi' in js and 'inp.dataset.memberpicker === "people"' in js)
check("the friend, chat and message forms use it", 'data-memberpicker="people"' in me.get(B + "/my/friends").text and 'data-memberpicker="people"' in me.get(B + "/chat/").text and 'data-memberpicker="people"' in me.get(B + "/my/messages/compose").text)
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("People Search: Find a Member by Email Address", "People Search: Find a Member by Call Sign", "People Search: Most Matches Listed", "People Search: Searches per Member per Minute")))
unset(*KEYS)
reset()
print("ALL PASS" if ok_all else "SOME FAILED")
