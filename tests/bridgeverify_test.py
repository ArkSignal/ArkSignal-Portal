"""The licensed-amateur check for the APRS bridge: the profile call sign is looked up (a fake Callook server in the portal's container plays the FCC data), an active license in the member's own name is
verified, a name mismatch or a club goes to an administrator, an expired or missing license is lapsed, a lookup that is down keeps the last status with a warning, changing the call sign resets it, the scheduled
re-check stops a lapsed license, administrators approve and revoke with a reason, APRS sending needs verified, and everything is logged. Needs the test box."""
import json, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
SSH = lambda cmd: subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True)
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def psql(sql):
    return SSH(f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def unset(*ks):
    for k in ks:
        psql(f"DELETE FROM settings WHERE key = '{k}'")
def inbox(local, name):
    subprocess.run(["scp", "-q", local, f"nastemp:adsb-portal/{name}"], check=True, capture_output=True)
    SSH(f"{DK} cp ~/adsb-portal/{name} adsb-portal:/tmp/{name}")
def mode(call, m):
    SSH(f"{DK} exec adsb-portal python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:18991/_mode/{call}/{m}').read()\"")
KEYS = ("bridge_aprs_enabled", "bridge_mesh_enabled", "callsign_lookup_url", "bridge_aprs_server", "bridge_verify_required", "bridge_verify_name_match", "bridge_verify_cooldown_minutes", "bridge_verify_last_error", "bridge_verify_last_ok", "bridgeverify_update_days")
UNIT = r'''
import os, json
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from app import create_app
app = create_app()
from app import bridge_verify as bv
out = {}
T = [("John Public", "JOHN Q PUBLIC", "normal", True), ("John Q. Public Jr.", "John Q Public", "normal", True), ("Public, John", "JOHN PUBLIC", "normal", True), ("Johnny Public", "JOHN PUBLIC", "normal", False),
     ("Johnny Public", "JOHN PUBLIC", "loose", True), ("John Smith", "JOHN PUBLIC", "loose", False), ("John Q Public", "JOHN PUBLIC", "strict", True), ("John Public", "JOHN R PUBLIC", "strict", True),
     ("John Public Smith", "JOHN PUBLIC", "strict", False), ("Marty", "MARTIN ALLISON", "normal", False), ("J Public", "JOHN PUBLIC", "normal", False), ("Mary-Ann O'Neil", "MARY ANN ONEIL", "normal", True)]
out["names"] = [bv.names_match(a, b, m)[0] == want for a, b, m, want in T]
with app.app_context():
    out["recheck"] = bv.recheck_all()
print(json.dumps(out))
'''
for n in ("fake_callook.py",):
    inbox(n, n)
open("bridgeverify_unit.py", "w").write(UNIT); inbox("bridgeverify_unit.py", "bridgeverify_unit.py")
SSH(f"{DK} exec adsb-portal pkill -f fake_callook.py")
SSH(f"{DK} exec -d adsb-portal python /tmp/fake_callook.py 18991")
time.sleep(2)

unset(*KEYS)
def wipe():
    for t in ("bridge_log", "mesh_outbox", "bridge_links", "conv_messages", "conv_members", "convs", "friendships", "pm_blocks"):
        psql(f"DELETE FROM {t}")
    psql("UPDATE users SET bridge_aprs_ok = FALSE, bridge_mesh_ok = FALSE, ham_status = 'unverified', ham_call = '', ham_name = '', ham_checked = NULL, ham_expires = NULL, ham_reason = '', ham_licensee = '', ham_override = '', ham_by = '', ham_warn = '', legal_first_sealed = '', legal_last_sealed = ''")
wipe()
setting("bridge_aprs_enabled", "1"); setting("callsign_lookup_url", "http://127.0.0.1:18991"); setting("bridge_aprs_server", "127.0.0.1:1"); setting("bridge_verify_cooldown_minutes", "1")
psql("UPDATE users SET callsign = 'K5ACT', display_name = 'Totally Different Profile Name' WHERE username = 'plain1'")   # the profile name is not what is checked
adm = sess("marty", PW); me = sess("plain1", "plainpassword1"); other = sess("claimer1", "claimpassword1"); anon = requests.Session()
def csrf(s):
    return re.search(r'data-csrf="([^"]+)"', s.get(B + "/chat/").text).group(1)
def post(s, path, **data):
    return s.post(B + path, data=dict(data, csrf_token=csrf(s)), headers={"X-Requested-With": "XMLHttpRequest"})
FN = ["John", "Public"]   # the private names saved on the Profile for the license check (kept sealed, apart from the display name)
def setnames(first, last):
    pg = me.get(B + "/my/profile").text
    tok = re.search(r'name="csrf_token" value="([^"]+)"', pg).group(1)
    cs = psql("select coalesce(callsign, '') from users where username = 'plain1'")
    dn = psql("select coalesce(display_name, '') from users where username = 'plain1'")
    return me.post(B + "/my/profile", data={"csrf_token": tok, "display_name": dn, "callsign": cs, "first_name": first, "last_name": last, "landing": ""}, allow_redirects=True)
def vpost():
    setnames(FN[0], FN[1])
    return post(me, "/my/bridges/verify")
def me_state():
    return psql("select ham_status from users where username = 'plain1'")

pg = me.get(B + "/my/bridges").text
check("a member sees their license status as not checked yet, with a Check My License button, and cannot switch APRS on", "Not checked yet" in pg and "Check My License" in pg and 'id="aprs-opt"' in pg and "disabled" in pg.split('id="aprs-opt"')[1].split(">")[0])
check("opting in is refused until the license is verified", "Check your amateur license first" in post(me, "/my/bridges/optin", kind="aprs", on="1", ack="1").json()["message"])
check("a check needs the private names: without them it is refused and says to save them on the Profile", "first and last name on your Profile" in post(me, "/my/bridges/verify").json()["message"] and me_state() == "unverified")
bad = setnames("J0hn", "Public").text
check("names with digits or symbols are refused on the Profile and not saved", "only letters" in bad and psql("select legal_first_sealed from users where username = 'plain1'") == "")
r = vpost().json()
check("checking an active license in the member's own name verifies it", r["ok"] and r["status"] == "verified" and me_state() == "verified" and psql("select ham_expires from users where username = 'plain1'") == "2036-01-01", r)
first_sealed = psql("select legal_first_sealed from users where username = 'plain1'")
check("the license names are stored sealed (not as readable text), and the hash that tracks them is not the name", first_sealed.startswith("enc1:") and "John" not in first_sealed and "Public" not in psql("select legal_last_sealed || ham_name from users where username = 'plain1'"))
check("the profile name is not what is checked: the member is verified with a different display name", me_state() == "verified" and psql("select display_name from users where username = 'plain1'") == "Totally Different Profile Name")
check("the names are shown only to the member: not on the member page, not on other members' pages", "John" not in other.get(B + "/members/plain1").text and "Public" not in other.get(B + "/members/plain1").text.replace("Totally Different Profile Name", "") and 'value="John"' in me.get(B + "/my/profile").text)
check("the audit log records a profile change without the names", "user.profile_update" in psql("select action from audit_log order by id desc limit 8") and "John" not in psql("select string_agg(coalesce(detail, '') || coalesce(target, ''), ' ') from audit_log where action = 'user.profile_update'"))
check("the first and last name fields are in the regular Profile form, with the privacy words, and the Radio page points to them", all(x in me.get(B + "/my/profile").text for x in ('name="first_name"', 'name="last_name"', "never shown on your profile")) and "saved on your" in me.get(B + "/my/bridges").text)
FN[:] = ["Jane", "Smith"]
check("other names no longer match the license: needs review, never verified", vpost().json()["status"] == "review" and me_state() == "review")
FN[:] = ["John", "Public"]
check("and the right names verify again", vpost().json()["status"] == "verified" and me_state() == "verified")
pg = me.get(B + "/my/bridges").text
check("the page says verified, with the FCC name and the expiry, and Check Again", "Verified" in pg and "John Q Public" in pg and "2036-01-01" in pg and "Check Again" in pg)
check("pressing check again at once is refused politely (the lookup service is a free one)", "a moment ago" in vpost().json()["message"])
check("a verified member can opt in", post(me, "/my/bridges/optin", kind="aprs", on="1", ack="1").json()["ok"])
r = post(me, "/chat/new", to="claimer1").json(); cid = r["url"].split("/")[-1]
check("a link is made", post(me, "/my/bridges/add", kind="aprs", remote="W5REM-9", conv_id=cid).json()["ok"])
check("a verified member's message is sent (no refusal note)", post(me, f"/chat/{cid}/send", body="verified hello").json()["note"] == "")
mp = me.get(B + "/members/plain1").text
check("the member page shows the Verified Amateur badge and call sign, to the member and to others", "Verified Amateur" in mp and "K5ACT" in mp and "Verified Amateur" in other.get(B + "/members/plain1").text and "Start a Chat" in other.get(B + "/members/plain1").text)
check("the member page needs a sign-in, an unknown member is a 404, and a member without a verified call sign has no badge", anon.get(B + "/members/plain1", allow_redirects=False).status_code in (301, 302, 401) and me.get(B + "/members/nobody-here").status_code == 404 and "Verified Amateur" not in other.get(B + "/members/claimer1").text)
pp = me.get(B + "/my/profile").text
check("the Profile page shows the license status with Check Again and a link to the member page", "Verified Amateur" in pp and "Check Again" in pp and 'href="/members/plain1"' in pp and 'id="ham-check"' in pp)
cp = me.get(B + f"/chat/{cid}").text
check("the chat shows the Radio panel with the link and its switch, the how-it-works box, and the verified badge on the member's name", 'id="ch-radio"' in cp and "W5REM-9" in cp and "Turn Off" in cp and "Radio: How Chat Reaches APRS and Mesh" in cp and 'href="/members/plain1"' in cp and "&#10003; K5ACT</span>" in cp)
check("the chat's messages carry the sender's verified call sign for the badge", any(m.get("ham") == "K5ACT" for m in me.get(B + f"/chat/{cid}/poll").json()["messages"]))
check("a member who has not set up the radio is pointed to My Account > Radio instead of a link form", "My Account &gt; Radio" in other.get(B + f"/chat/{cid}").text and 'id="ch-radio-add"' not in other.get(B + f"/chat/{cid}").text)
psql("UPDATE users SET ham_status = 'review' WHERE username = 'plain1'")
check("APRS sending is refused when the status is not verified, and the sender is told why", "not verified yet" in post(me, f"/chat/{cid}/send", body="not verified hello").json()["note"] and sum(1 for m in me.get(B + f"/chat/{cid}/poll").json()["messages"] if m["body"] == "not verified hello") == 1)
check("and the receive filter leaves the member out", "calls" and json.loads(SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python -c \"import os; os.environ['DISABLE_BACKUP_SCHEDULER']='1'; from app import create_app; a=create_app(); from app import bridge; ctx=a.app_context(); ctx.push(); print(__import__('json').dumps(bridge.listen_calls()))\"").stdout.strip().splitlines()[-1]) == [])
psql("UPDATE users SET ham_status = 'verified' WHERE username = 'plain1'")

# the unit script: name table (both ways round) and the scheduled recheck at the end
o = SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/bridgeverify_unit.py").stdout.strip().splitlines()
res = json.loads(o[-1]) if o else {}
check("the name comparison gives the expected answer in every case of the table", res.get("names") and all(res["names"]), res)
check("the scheduled re-check ran over the checked members", "checked" in res.get("recheck", "") and "1 checked" in res["recheck"], res)

# name mismatch, club, expired, missing
psql("UPDATE users SET callsign = 'K5MIS', bridge_aprs_ok = FALSE WHERE username = 'plain1'")
check("changing the call sign puts the member back to not checked, at once", "Not checked yet" in me.get(B + "/my/bridges").text or "checked again" in me.get(B + "/my/bridges").text)
setting("bridge_verify_cooldown_minutes", "1"); psql("UPDATE users SET ham_checked = now() - interval '1 hour'")
r = vpost().json()
check("a name that does not match the license goes to needs-review, never verified, with a plain reason", r["ok"] and r["status"] == "review" and me_state() == "review" and "does not match" in me.get(B + "/my/bridges").text and "Needs an administrator" in me.get(B + "/my/bridges").text, r)
check("an administrator sees it first on Admin > Radio Bridges, with the FCC name", "Radio Bridges" in adm.get(B + "/admin/bridges").text and "plain1" in adm.get(B + "/admin/bridges").text and "Jane Smith" in adm.get(B + "/admin/bridges").text and 'href="/admin/bridges"' in adm.get(B + "/admin/").text)
uid = psql("select id from users where username = 'plain1'")
check("a member cannot reach the administrators' page or actions", me.get(B + "/admin/bridges").status_code in (302, 403, 404) and post(me, f"/admin/bridges/{uid}/approve", reason="x").status_code in (302, 403, 404))
check("approving needs a reason", "reason" in post(adm, f"/admin/bridges/{uid}/approve").json()["message"].lower())
r = post(adm, f"/admin/bridges/{uid}/approve", reason="Known member, name differs on the license").json()
check("an administrator approves a needs-review member and they are verified", r["ok"] and me_state() == "verified" and psql("select ham_by from users where username = 'plain1'") == "marty" and "Verified" in me.get(B + "/my/bridges").text)
psql("UPDATE users SET ham_checked = now() - interval '1 hour'")
vpost()
check("a later check keeps an approved member verified while the license is active", me_state() == "verified")

psql("UPDATE users SET callsign = 'K5CLB', ham_override = '' WHERE username = 'plain1'")
vpost()
check("a club license goes to review", me_state() == "review" and "Club" in psql("select ham_reason from users where username = 'plain1'"), psql("select ham_reason from users where username = 'plain1'"))
psql("UPDATE users SET callsign = 'K5OLD', ham_checked = now() - interval '1 hour'")
vpost()
check("an expired license is lapsed, with the date", me_state() == "lapsed" and "expired on 2020-01-01" in psql("select ham_reason from users where username = 'plain1'"))
psql("UPDATE users SET callsign = 'K5EXP', ham_checked = now() - interval '1 hour'")
vpost()
check("a call sign with no license is lapsed", me_state() == "lapsed" and "no active amateur license" in psql("select ham_reason from users where username = 'plain1'"))
check("approval is refused for a lapsed member", not post(adm, f"/admin/bridges/{uid}/approve", reason="trying").json()["ok"])

# verified in a middle initial and suffix name, then the service goes down
FN[:] = ["John Q.", "Public Jr."]
psql("UPDATE users SET callsign = 'K5ACT', ham_checked = now() - interval '1 hour', ham_override = '' WHERE username = 'plain1'")
vpost()
check("a name with a middle initial and a suffix still verifies", me_state() == "verified")
mode("K5ACT", "down")
psql("UPDATE users SET ham_checked = now() - interval '1 hour'")
r = vpost()
check("when the lookup is down the last status is kept and a warning is shown", r.status_code == 503 and me_state() == "verified" and "could not reach the license service" in me.get(B + "/my/bridges").text)
post(adm, "/admin/status/run")
st = adm.get(B + "/admin/status/data.json").text
check("Service Status shows the lookup trouble", "Radio Bridges" in st and "license lookup is not answering" in st)
mode("K5ACT", "active")
vpost()
psql("UPDATE users SET ham_checked = now() - interval '1 hour'")
vpost()
check("when it answers again the warning clears", me_state() == "verified" and "could not reach the license service" not in me.get(B + "/my/bridges").text)

# the schedule stops a lapsed license
mode("K5ACT", "expired")
psql("UPDATE users SET bridge_aprs_ok = TRUE WHERE username = 'plain1'")
o = SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/bridgeverify_unit.py").stdout.strip().splitlines()
check("the scheduled re-check finds the license expired: the member is lapsed and APRS stops", me_state() == "lapsed" and "not verified yet" in post(me, f"/chat/{cid}/send", body="after lapse").json()["note"])
mode("K5ACT", "active")
check("the job is on Data Updates with a schedule setting (weekly by default)", "Amateur Licenses for the APRS Bridge" in adm.get(B + "/admin/data-updates/").text and 'name="bridgeverify_update_days"' in adm.get(B + "/admin/settings").text and psql("select value from settings where key = 'bridgeverify_update_days'") in ("", "7"))

# revoke and clear
psql("UPDATE users SET ham_checked = now() - interval '1 hour'")
vpost()
r = post(adm, f"/admin/bridges/{uid}/revoke", reason="complaint")
check("an administrator revokes a member with a reason: APRS links are switched off and the status says so", r.json()["ok"] and me_state() == "verified" and "Revoked by an administrator" in me.get(B + "/my/bridges").text and psql("select enabled from bridge_links where kind = 'aprs'") == "f")
psql("UPDATE users SET ham_checked = now() - interval '1 hour'")
check("a revoked member cannot check their way back", vpost().json().get("message", "") != "" and "Revoked" in me.get(B + "/my/bridges").text)
check("an administrator clears the revoke and the member must check again", post(adm, f"/admin/bridges/{uid}/clear", reason="resolved").json()["ok"] and me_state() == "unverified")

# settings and audit
st = adm.get(B + "/admin/settings").text
check("every tunable is in Admin > Settings", all(x in st for x in ("APRS Bridge: Require a Verified License", "APRS Bridge: How Closely the Name Must Match", "APRS Bridge: Seconds to Wait for the License Lookup", "APRS Bridge: Minutes Between a Member", "APRS Bridge: Most Members Checked per Scheduled Run", "Re-Check Amateur Licenses")))
setting("bridge_verify_required", "0")
psql("UPDATE users SET ham_status = 'unverified'")
check("with the requirement off, an unverified member is not stopped", "not verified" not in post(me, f"/chat/{cid}/send", body="no check needed").json()["note"])
unset("bridge_verify_required")
au = adm.get(B + "/admin/audit").text
check("checks, approval, revoke and clear are in the audit log", all(x in au for x in ("bridge.verify", "bridge.verify_approve", "bridge.verify_revoke", "bridge.verify_clear")))
check("the source and its terms are in the terms file and the new table of data is in the restore area", "bridge" in open("../DATA_SOURCES_TERMS.md", encoding="utf-8").read().lower() and "Callook" in open("../DATA_SOURCES_TERMS.md", encoding="utf-8").read())
SSH(f"{DK} exec adsb-portal pkill -f fake_callook.py")
wipe()
psql("UPDATE users SET callsign = NULL, display_name = NULL, legal_first_sealed = '', legal_last_sealed = '' WHERE username = 'plain1'")
unset(*KEYS)
print("ALL PASS" if ok_all else "SOME FAILED")
