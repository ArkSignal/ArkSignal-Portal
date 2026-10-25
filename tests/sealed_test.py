"""Secrets are stored encrypted: settings that hold a secret (the mesh broker password, the mesh bridge gateway token) are sealed in the database, never shown on the Settings page, never written to the audit log,
and a plain value left by an older version is sealed by the start-up pass; a private mesh channel's key is sealed and never in the audit log or the administrators' pages; and a guard fails when a new column that
looks like a secret (secret, token, passcode, psk, password) is stored as plain text. Needs the test box."""
import json, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
SSH = lambda cmd: subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300)
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def psql(sql):
    return SSH(f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").stdout.strip()
adm = sess("marty", PW); me = sess("plain1", "plainpassword1")
psql("DELETE FROM settings WHERE key IN ('mesh_password','bridge_mesh_token')")

def section_form(key):
    html = adm.get(B + "/admin/settings").text
    forms = re.findall(r"<form[^>]*>.*?</form>", html, re.S)
    f = next(x for x in forms if f'name="{key}"' in x)
    return html, f, re.search(r'name="section" value="([^"]+)"', f).group(1), re.search(r'name="csrf_token" value="([^"]+)"', f).group(1)
def save(key, value=None, clear=False):
    html, f, sec, tok = section_form(key)
    d = {"csrf_token": tok, "section": sec}
    if value is not None:
        d[key] = value
    if clear:
        d[key + "__clear"] = "1"
    r = adm.post(B + "/admin/settings/save", data=d, headers={"X-Requested-With": "XMLHttpRequest"})
    return r.json()

SECRET = "Zq7-Tok3n-sealed-check-91"
for key in ("mesh_password", "bridge_mesh_token"):
    r = save(key, SECRET + key[:4])
    check(f"{key}: saving works", r["ok"], r)
    stored = psql(f"select value from settings where key = '{key}'")
    check(f"{key}: the database holds it sealed, not as plain text", stored.startswith("enc1:") and SECRET not in stored, stored[:20])
    html, f, _s, _t = section_form(key)
    check(f"{key}: the Settings page never shows the value and says it is saved", SECRET not in html and 'type="password"' in f and "saved: leave blank to keep it" in f and "Remove the saved value" in f)
check("the audit log says a secret changed but never holds its value", SECRET not in psql("select detail from audit_log where action = 'settings.update' order by id desc limit 5") and "mesh_password: changed" in psql("select detail from audit_log where action = 'settings.update' order by id desc limit 5"))
check("leaving the box blank keeps the saved value", save("mesh_password", "")["ok"] and psql("select value from settings where key = 'mesh_password'").startswith("enc1:"))
before = psql("select value from settings where key = 'bridge_mesh_token'")
psql("INSERT INTO settings (key, value) VALUES ('bridge_mesh_enabled','1'),('chat_enabled','1') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
ok_t = requests.get(B + "/bridge/mesh/outbox", headers={"X-Bridge-Token": SECRET + "brid"}).status_code
bad_t = requests.get(B + "/bridge/mesh/outbox", headers={"X-Bridge-Token": "wrong"}).status_code
check("the portal still reads the sealed token: the gateway address accepts it and refuses a wrong one", ok_t == 200 and bad_t != 200, (ok_t, bad_t))
psql("DELETE FROM settings WHERE key IN ('bridge_mesh_enabled')")
check("the Remove box clears it", save("mesh_password", clear=True)["ok"] and psql("select value from settings where key = 'mesh_password'") == "")
# a plain value left by an older version is sealed by the start-up pass
psql("INSERT INTO settings (key, value) VALUES ('mesh_password', 'old-plain-password') ON CONFLICT (key) DO UPDATE SET value = 'old-plain-password'")
UNIT = """import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
import psycopg
from psycopg.rows import dict_row
from app import create_app, vault
app = create_app()
with app.app_context(), psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row) as c:
    n = vault.encrypt_existing(c); c.commit()
    r = c.execute("SELECT value FROM settings WHERE key='mesh_password'").fetchone()["value"]
    from app.settings import get_setting
    print(True, r.startswith("enc1:"), vault.unseal(r) == "old-plain-password", vault.encrypt_existing(c) == 0)
"""
open("sealed_unit.py", "w").write(UNIT)
subprocess.run(["scp", "-q", "sealed_unit.py", "nastemp:adsb-portal/sealed_unit.py"], check=True, capture_output=True)
SSH(f"{DK} cp ~/adsb-portal/sealed_unit.py adsb-portal:/tmp/sealed_unit.py")
o = SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sealed_unit.py").stdout.strip().splitlines()
check("a plain secret setting left by an older version is sealed by the start-up pass, and a second pass changes nothing", o and o[-1] == "True True True True", o[-3:])
check("the start-up pass is committed (it ran inside a transaction that was rolled back before)", "conn.commit()" in open("../app/__init__.py", encoding="utf-8").read().split("encrypt_existing(conn)")[1].split("finally")[0])
psql("DELETE FROM settings WHERE key IN ('mesh_password','bridge_mesh_token')")

# a mesh channel's key
psql("INSERT INTO settings (key, value) VALUES ('mesh_enabled','1'),('mesh_channels_enabled','1') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
psql("DELETE FROM mesh_channels")
def post(s, path, **data):
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/mesh/channels").text).group(1)
    return s.post(B + path, data=dict(data, csrf_token=t), headers={"X-Requested-With": "XMLHttpRequest"})
post(me, "/my/mesh/channels/add", name="SealTest", key_bits="256", precision="0")
cid = psql("select id from mesh_channels")
k = post(me, f"/my/mesh/channels/{cid}/key").json()
stored = psql("select psk_sealed from mesh_channels")
check("a channel key is stored sealed (not its hex, base64 or address)", stored.startswith("enc1:") and k["hex"] not in stored and k["base64"] not in stored)
alltext = psql("select string_agg(detail || coalesce(target, ''), ' ') from audit_log")
check("the key is never in the audit log", k["hex"] not in alltext and k["base64"] not in alltext and k["address"].split("#")[1] not in alltext)
check("nothing in the database stores a QR code or the channel address", psql("select count(*) from information_schema.columns where table_schema = 'public' and (column_name ~* '(qr|svg|address_url)')") == "0")
check("the administrators' pages never show the key", k["hex"] not in adm.get(B + "/admin/mesh").text and k["base64"] not in adm.get(B + "/admin/mesh").text)
psql("DELETE FROM mesh_channels")
psql("DELETE FROM settings WHERE key IN ('mesh_channels_enabled')")

# the guard: no new plain secret column
cols = psql("select table_name || '.' || column_name from information_schema.columns where table_schema = 'public' and data_type in ('text', 'character varying', 'bytea') and column_name ~* '(secret|token|passcode|psk|password|private_key)' order by 1").splitlines()
ALLOWED_PLAIN = {"wx_stations.secrets", "users.totp_secret"}   # an unused legacy column (empty everywhere); the TOTP secret is sealed (checked below) though its name does not say so
bad = [c for c in cols if not re.search(r"(_hash|_sealed)$", c) and c not in ALLOWED_PLAIN]
check("every column that looks like a secret holds a hash or a sealed value (a new one must be sealed, or added here with a reason)", not bad, bad)
check("the legacy secrets column is empty", psql("select count(*) from wx_stations where secrets <> '' and secrets <> '{}'") == "0")
sealed_cols = [("acars_stations", "token_sealed"), ("ais_stations", "token_sealed"), ("rid_stations", "token_sealed"), ("sat_receivers", "token_sealed"), ("wx_stations", "token_sealed"), ("sonde_stations", "push_sealed"), ("mesh_accounts", "password_sealed"), ("wx_push", "passcode_sealed"), ("mesh_channels", "psk_sealed")]
check("every sealed column in the database really holds sealed values", all(psql(f"select count(*) from {t} where {c} <> '' and {c} not like 'enc1:%'") == "0" for t, c in sealed_cols))
check("TOTP secrets and webhook addresses are sealed", psql("select count(*) from users where (totp_secret <> '' and totp_secret not like 'enc1:%') or (webhook_url <> '' and webhook_url not like 'enc1:%')") == "0")
print("ALL PASS" if ok_all else "SOME FAILED")
