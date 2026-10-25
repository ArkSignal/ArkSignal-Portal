"""Ask to join a community: a signed-in member who is not on the team can send a request with a note; the Managers and Owner approve (choosing the role, never above their own) or decline; the member can withdraw it;
one waiting request each, a limit per community, a switch, audit log, and a guest or a team member cannot ask."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("mesh_enabled", "1"); setting("mesh_communities_enabled", "1"); setting("mesh_community_join_requests", "1"); setting("mesh_community_max_requests", "20")
psql("DELETE FROM settings WHERE key LIKE 'access_%'")
psql("DELETE FROM communities WHERE slug LIKE 'cjr-%'")
psql("DELETE FROM users WHERE username LIKE 'cjr%'")
for n in ("cjrowner", "cjrmanager", "cjrasker", "cjrasker2", "cjrasker3"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
S = {n: sess(n, n + "password99") for n in ("cjrowner", "cjrmanager", "cjrasker", "cjrasker2", "cjrasker3")}
adm = sess("marty", PW); guest = requests.Session()
act(adm, "/admin/communities/create", "/admin/communities", name="CJR Join Mesh", owner="cjrowner")
SLUG = "cjr-join-mesh"; PG = f"/mesh/c/{SLUG}"
def post(s, path, **data):
    data["csrf_token"] = tok(s, PG)
    return s.post(B + path, data=data, allow_redirects=False)
post(S["cjrowner"], f"{PG}/members/add", who="cjrmanager", role="manager")
check("a guest is not offered the form and cannot ask", "Ask to Join" not in guest.get(B + PG).text and guest.post(B + f"{PG}/join-request", data={"message": "hi"}, allow_redirects=False).status_code in (302, 400, 401, 403))
check("a signed-in member who is not on the team is offered it", "Ask to Join" in S["cjrasker"].get(B + PG).text)
check("a team member is not", "Ask to Join" not in S["cjrmanager"].get(B + PG).text)
post(S["cjrasker"], f"{PG}/join-request", message="I run a router in Conway")
check("the request is kept with its note", psql(f"SELECT message || '|' || state FROM community_join_requests WHERE community_id=(SELECT id FROM communities WHERE slug='{SLUG}') AND user_id=(SELECT id FROM users WHERE username='cjrasker')") == "I run a router in Conway|pending")
check("the member then sees that it is waiting, with a way to withdraw it", "request to join the team is waiting" in S["cjrasker"].get(B + PG).text and "Withdraw It" in S["cjrasker"].get(B + PG).text)
post(S["cjrasker"], f"{PG}/join-request", message="again")
check("a second request while one waits is refused", psql("SELECT COUNT(*) FROM community_join_requests WHERE state='pending' AND user_id=(SELECT id FROM users WHERE username='cjrasker')") == "1")
tp = S["cjrmanager"].get(B + f"{PG}/team").text
check("the Manager sees the request with the note on the Team page", "Requests to Join (1)" in tp and "I run a router in Conway" in tp and "cjrasker" in tp)
check("an ordinary member cannot see or answer requests", "Requests to Join" not in S["cjrasker2"].get(B + f"{PG}/team").text and post(S["cjrasker2"], f"{PG}/join-requests/1/approve", role="viewer").status_code in (403, 404))
rid = psql("SELECT id FROM community_join_requests WHERE state='pending' LIMIT 1")
check("a Manager cannot give a role above their own (Owner)", post(S["cjrmanager"], f"{PG}/join-requests/{rid}/approve", role="owner").status_code in (302, 403) and psql(f"SELECT state FROM community_join_requests WHERE id={rid}") == "pending")
post(S["cjrmanager"], f"{PG}/join-requests/{rid}/approve", role="operator")
check("approving puts the member on the team with the chosen role", psql(f"SELECT role FROM community_members WHERE community_id=(SELECT id FROM communities WHERE slug='{SLUG}') AND user_id=(SELECT id FROM users WHERE username='cjrasker')") == "operator" and psql(f"SELECT state FROM community_join_requests WHERE id={rid}") == "approved")
post(S["cjrasker2"], f"{PG}/join-request", message="please")
rid2 = psql("SELECT id FROM community_join_requests WHERE state='pending' LIMIT 1")
post(S["cjrowner"], f"{PG}/join-requests/{rid2}/decline")
check("declining leaves them off the team", psql(f"SELECT state FROM community_join_requests WHERE id={rid2}") == "declined" and psql("SELECT COUNT(*) FROM community_members m JOIN users u ON u.id = m.user_id WHERE u.username='cjrasker2'") == "0")
post(S["cjrasker2"], f"{PG}/join-request", message="again later")
check("a member who was declined may ask again", psql("SELECT COUNT(*) FROM community_join_requests WHERE state='pending' AND user_id=(SELECT id FROM users WHERE username='cjrasker2')") == "1")
post(S["cjrasker2"], f"{PG}/join-request/cancel")
check("a member can withdraw a waiting request", psql("SELECT COUNT(*) FROM community_join_requests WHERE state='pending' AND user_id=(SELECT id FROM users WHERE username='cjrasker2')") == "0")
setting("mesh_community_max_requests", "1")
post(S["cjrasker2"], f"{PG}/join-request", message="one"); post(S["cjrasker3"], f"{PG}/join-request", message="two")
check("the limit on waiting requests is respected", psql("SELECT COUNT(*) FROM community_join_requests WHERE state='pending'") == "1")
setting("mesh_community_max_requests", "20"); setting("mesh_community_join_requests", "0")
check("with the switch off the form is gone and asking is refused", "Ask to Join" not in S["cjrasker3"].get(B + PG).text and post(S["cjrasker3"], f"{PG}/join-request", message="x").status_code == 404)
setting("mesh_community_join_requests", "1")
aud = psql("SELECT string_agg(detail, '|') FROM audit_log WHERE action='community.join_request'")
check("asking and answering are in the audit log", "cjrasker" in aud and "approved" in aud and "declined" in aud, aud)
check("the settings exist", all(x in adm.get(B + "/admin/settings/radio").text for x in ("Communities: Ask to Join", "Communities: Waiting Join Requests")))
psql("DELETE FROM communities WHERE slug LIKE 'cjr-%'")
psql("DELETE FROM users WHERE username LIKE 'cjr%'")
print("ALL PASS" if ok_all else "SOME FAILED")
