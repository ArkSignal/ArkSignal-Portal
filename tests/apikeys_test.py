"""API keys: members make keys on My Account > API Keys, a key opens the documented /api/v1 API, requests are counted and limited, administrators limit, revoke and make keys for members, and only a fingerprint
of a key is kept."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def token(s, path):
    return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
AJAX = {"X-Requested-With": "XMLHttpRequest"}
psql("DELETE FROM api_keys")
adm = sess("marty", PW)
me = sess("plain1", "plainpassword1")

d = requests.get(B + "/api/docs")
check("the API page is public and explains keys, endpoints, limits, errors and the rules", d.status_code == 200 and all(x in d.text for x in ("Getting Started", "Endpoints", "Limits", "Rules of Use", "/api/v1/aircraft", "Bearer")), d.status_code)
check("the Network menu lists it", 'href="/api/docs"' in requests.get(B + "/").text)
check("My API Keys needs a sign-in", requests.get(B + "/my/api-keys", allow_redirects=False).status_code in (302, 401, 403))
check("My Account has the tab", 'href="/my/api-keys"' in me.get(B + "/my/").text)
check("the v1 API needs a key", requests.get(B + "/api/v1/aircraft").status_code == 401 and requests.get(B + "/api/v1/aircraft").json()["error"] == "key_required")
check("a made-up key is refused", requests.get(B + "/api/v1/aircraft", headers={"Authorization": "Bearer ask_notarealkeynotarealkeynotarealkey"}).json().get("error") == "invalid_key")

t = token(me, "/my/api-keys")
r = me.post(B + "/my/api-keys/create", data={"csrf_token": t, "name": "Test display", "note": "wall"}, headers=AJAX)
j = r.json()
key = (re.search(r"(ask_[A-Za-z0-9_-]{20,})", j.get("html", "")) or [None, None])[1]
check("a member can make a key and sees it once", r.status_code == 200 and j["ok"] and key and "shown only this one time" in j["html"], j.get("message"))
check("only a fingerprint is kept: the key itself is nowhere in the database", psql(f"select count(*) from api_keys where key_hash = '{key}' or prefix = '{key}' or name = '{key}'") == "0" and len(psql("select key_hash from api_keys limit 1")) == 64)
check("the list afterwards shows the start of the key, not the key", key[:10] in me.get(B + "/my/api-keys").text and key not in me.get(B + "/my/api-keys").text)
H = {"Authorization": "Bearer " + key}
r = requests.get(B + "/api/v1/me", headers=H)
check("the key opens /api/v1/me and the answer says where the limits stand", r.status_code == 200 and r.json()["name"] == "Test display" and r.headers.get("X-RateLimit-Limit") == "60" and r.headers.get("X-RateLimit-Remaining") == "59" and r.headers.get("X-RateLimit-Day-Limit"), (r.status_code, dict(r.headers)))
check("X-API-Key works too", requests.get(B + "/api/v1/me", headers={"X-API-Key": key}).status_code == 200)
for path in ("/api/v1/", "/api/v1/aircraft", "/api/v1/feeders", "/api/v1/weather", "/api/v1/sondes", "/api/v1/repeaters"):
    rr = requests.get(B + path, headers=H, timeout=60)
    check(f"{path} answers with the key (data, or the part is off or its source away)", rr.status_code in (200, 404, 503) and (rr.status_code != 200 or isinstance(rr.json(), dict)) and "X-RateLimit-Remaining" in rr.headers or rr.status_code == 404, rr.status_code)
check("requests are counted on the member's page", "Test display" in me.get(B + "/my/api-keys").text and re.search(r'class="mono">(\d[\d,]*)</td>\s*<td class="mono">(\d[\d,]*)</td>', me.get(B + "/my/api-keys").text) is not None)
check("the creation is in the audit log", "apikey.create" in adm.get(B + "/admin/audit").text)

# limits
kid = psql("select id from api_keys limit 1")
t = token(adm, "/admin/api-keys")
r = adm.post(B + f"/admin/api-keys/{kid}/limits", data={"csrf_token": t, "rate_minute": "3", "rate_day": ""}, headers=AJAX)
check("an administrator can set one key's limits", r.status_code == 200 and r.json()["ok"], r.text[:200])
psql("DELETE FROM api_key_minute")
codes = [requests.get(B + "/api/v1/me", headers=H).status_code for _ in range(5)]
check("past the limit the answer is 429 with Retry-After", 429 in codes and codes[0] == 200, codes)
rr = requests.get(B + "/api/v1/me", headers=H)
check("and says how long to wait", rr.status_code == 429 and rr.headers.get("Retry-After") and rr.json()["error"] == "rate_limited", (rr.status_code, dict(rr.headers)))
r = adm.post(B + f"/admin/api-keys/{kid}/limits", data={"csrf_token": t, "rate_minute": "abc", "rate_day": ""}, headers=AJAX)
check("a bad limit is refused", r.status_code == 400)
adm.post(B + f"/admin/api-keys/{kid}/limits", data={"csrf_token": t, "rate_minute": "", "rate_day": "2"}, headers=AJAX)
psql("DELETE FROM api_key_minute")
day = [requests.get(B + "/api/v1/me", headers=H).json().get("error", "ok") for _ in range(4)]
check("the daily limit stops it too", "daily_limit" in day, day)
adm.post(B + f"/admin/api-keys/{kid}/limits", data={"csrf_token": t, "rate_minute": "", "rate_day": ""}, headers=AJAX)
psql("DELETE FROM api_key_minute")

# the number of keys
t = token(me, "/my/api-keys")
for n in range(2, 6):
    last = me.post(B + "/my/api-keys/create", data={"csrf_token": t, "name": f"Key {n}"}, headers=AJAX)
check("a member cannot have more working keys than the limit", last.status_code == 400 and "3 working keys" in last.json()["message"], last.text[:200])

# revoke
r = me.post(B + f"/my/api-keys/{kid}/revoke", data={"csrf_token": t}, headers=AJAX)
check("a member can revoke a key", r.status_code == 200 and r.json()["ok"])
check("a revoked key stops working at once", requests.get(B + "/api/v1/me", headers=H).json().get("error") == "invalid_key")
other = sess("claimer1", "claimpassword1")
check("nobody can revoke or see another member's key", other.post(B + f"/my/api-keys/{psql('select id from api_keys order by id desc limit 1')}/revoke", data={"csrf_token": token(other, '/my/api-keys')}, headers=AJAX).status_code == 404)

# administrators
check("a member cannot open the admin page", me.get(B + "/admin/api-keys", allow_redirects=False).status_code in (302, 401, 403, 404))
pg = adm.get(B + "/admin/api-keys")
check("the admin page lists every key with the member and a search box", pg.status_code == 200 and "Test display" in pg.text and "plain1" in pg.text and "tfilter" in pg.text and "data-memberpicker" in pg.text)
check("it uses the folding sections", pg.text.count('class="panel setsec"') >= 2)
t = token(adm, "/admin/api-keys")
r = adm.post(B + "/admin/api-keys/create", data={"csrf_token": t, "username": "claimer1", "name": "For the club", "note": ""}, headers=AJAX)
key2 = (re.search(r"(ask_[A-Za-z0-9_-]{20,})", r.json().get("html", "")) or [None, None])[1]
check("an administrator can make a key for a member", r.status_code == 200 and key2 and "claimer1" in r.json()["message"], r.text[:200])
check("it works, and belongs to the member", requests.get(B + "/api/v1/me", headers={"Authorization": "Bearer " + key2}).json().get("name") == "For the club" and "For the club" in other.get(B + "/my/api-keys").text)
r = adm.post(B + "/admin/api-keys/create", data={"csrf_token": t, "username": "nobody-here", "name": "x"}, headers=AJAX)
check("a key for a member that does not exist is refused", r.status_code == 400)
kid2 = psql("select id from api_keys where name='For the club'")
r = adm.post(B + f"/admin/api-keys/{kid2}/revoke", data={"csrf_token": t}, headers=AJAX)
check("an administrator can revoke a member's key", r.status_code == 200 and requests.get(B + "/api/v1/me", headers={"Authorization": "Bearer " + key2}).json().get("error") == "invalid_key")
adm.post(B + f"/admin/api-keys/{kid2}/revoke", data={"csrf_token": t}, headers=AJAX)
check("and restore it", requests.get(B + "/api/v1/me", headers={"Authorization": "Bearer " + key2}).status_code == 200)
# endpoints on and off
H2 = {"Authorization": "Bearer " + key2}
ep_all = {"csrf_token": t, "ep_aircraft": "1", "ep_feeders": "1", "ep_weather": "1", "ep_sondes": "1", "ep_repeaters": "1"}
check("the admin page has an Endpoints section with a switch for each endpoint", all(x in adm.get(B + "/admin/api-keys").text for x in ('name="ep_aircraft"', 'name="ep_weather"', 'name="ep_repeaters"', "Save Endpoints")))
before = int(psql(f"select coalesce(sum(calls),0) from api_key_daily where key_id = {kid2}"))
off = dict(ep_all); del off["ep_weather"]; del off["ep_sondes"]
r = adm.post(B + "/admin/api-keys/endpoints", data=off, headers=AJAX)
check("an administrator can switch endpoints off", r.status_code == 200 and "Weather" in r.json()["message"], r.text[:200])
rr = requests.get(B + "/api/v1/weather", headers=H2)
check("a switched-off endpoint answers 403 with endpoint_disabled", rr.status_code == 403 and rr.json()["error"] == "endpoint_disabled" and requests.get(B + "/api/v1/sondes", headers=H2).status_code == 403)
after = int(psql(f"select coalesce(sum(calls),0) from api_key_daily where key_id = {kid2}"))
check("and is not counted against the key", before == after, (before, after))
check("the others still answer", requests.get(B + "/api/v1/feeders", headers=H2).status_code in (200, 503) and requests.get(B + "/api/v1/me", headers=H2).status_code == 200)
idx = requests.get(B + "/api/v1/", headers=H2).json()["endpoints"]
check("the index lists only the endpoints that are on", "/api/v1/weather" not in [e["path"] for e in idx] and "/api/v1/sondes" not in [e["path"] for e in idx] and "/api/v1/aircraft" in [e["path"] for e in idx])
dd = requests.get(B + "/api/docs").text
tbl = dd.split('id="api-endpoints"')[1].split("</table>")[0]
check("the docs page does not show a switched-off endpoint at all, and still shows the others", "/api/v1/weather" not in tbl and "/api/v1/sondes" not in tbl and "/api/v1/aircraft" in tbl and "switched off" not in tbl)
check("the administrator's Endpoints section still lists every endpoint to switch back on", all(x in adm.get(B + "/admin/api-keys").text for x in ('name="ep_weather"', 'name="ep_sondes"')))
check("the change is in the audit log", "apikey.endpoints" in adm.get(B + "/admin/audit").text)
check("a switched-off endpoint without a key is still just 401 (nothing is revealed)", requests.get(B + "/api/v1/weather").status_code == 401)
adm.post(B + "/admin/api-keys/endpoints", data=ep_all, headers=AJAX)
check("switching them back on works", requests.get(B + "/api/v1/weather", headers=H2).status_code in (200, 404, 503))
au = adm.get(B + "/admin/audit").text
check("every admin action is in the audit log", all(x in au for x in ("apikey.admin_create", "apikey.admin_revoke", "apikey.admin_restore", "apikey.admin_limits", "apikey.revoke")))
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("Public API", "API: Working Keys per Member", "API: Requests per Minute per Key", "API: Requests per Day per Key", "API: Aircraft Endpoint", "API: Repeaters Endpoint")))
rl = adm.get(B + "/admin/roles").text
check("Manage API Keys is a permission a role can hold", "Manage API Keys" in rl)
# who may make keys
check("while members may make keys, My API Keys has the form", "Make the Key" in me.get(B + "/my/api-keys").text)
psql("INSERT INTO settings (key, value) VALUES ('api_member_keys', '0') ON CONFLICT (key) DO UPDATE SET value = '0'")
pg = me.get(B + "/my/api-keys").text
check("with only administrators making keys, the form is gone and the page says to ask", "Make the Key" not in pg and "Getting a Key" in pg and "administrator makes the API keys" in pg)
r = me.post(B + "/my/api-keys/create", data={"csrf_token": token(me, "/my/api-keys"), "name": "Sneaky", "note": ""}, headers=AJAX)
check("a member's attempt to make one is refused with a plain message and nothing is made", r.status_code in (200, 400) and not r.json()["ok"] and "administrator makes the API keys" in r.json()["message"] and psql("select count(*) from api_keys where name = 'Sneaky'") == "0", r.text[:200])
check("the API page tells a visitor to ask an administrator", "ask an administrator" in requests.get(B + "/api/docs").text)
r = adm.post(B + "/admin/api-keys/create", data={"csrf_token": token(adm, "/admin/api-keys"), "username": "plain1", "name": "Made by admin", "note": ""}, headers=AJAX)
check("an administrator can still make a key for the member", r.status_code == 200 and r.json()["ok"] and psql("select count(*) from api_keys where name = 'Made by admin'") == "1", r.text[:200])
check("and the member sees it, and can revoke it", "Made by admin" in me.get(B + "/my/api-keys").text)
psql("INSERT INTO settings (key, value) VALUES ('api_member_keys', '1') ON CONFLICT (key) DO UPDATE SET value = '1'")
check("the setting is in Admin > Settings", "API: Members Make Their Own Keys" in adm.get(B + "/admin/settings").text)
psql("DELETE FROM api_keys")
print("ALL PASS" if ok_all else "SOME FAILED")
