"""Public channel presets: the list on Admin > Mesh Communities (Meshtastic default-key channels including the 500 kHz presets, the ham-band and MeshCore rows as information only), listing them on every
community or chosen ones, skipping what is there already, a manager's one-click listing on the community's own Channels page, the Public Channels setting following, and who may do it."""
import os, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("mesh_enabled", "1")
setting("mesh_communities_enabled", "1")
psql("DELETE FROM settings WHERE key = 'access_communities'")
setting('mesh_public_channels', 'LongFast,MediumFast')
psql("DELETE FROM communities WHERE slug LIKE 'cmp-%'")
psql("DELETE FROM users WHERE username LIKE 'cpre%'")
for n in ("cpreowner", "cprestranger"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
O, X = sess("cpreowner", "cpreownerpassword99"), sess("cprestranger", "cprestrangerpassword99")
adm = sess("marty", PW)
for nm in ("CMP Alpha", "CMP Beta"):
    act(adm, "/admin/communities/create", "/admin/communities", name=nm, owner="cpreowner" if nm.endswith("Alpha") else "")
def chans(slug):
    return psql(f"SELECT string_agg(concat_ws(':', ch.name, ch.visibility, ch.key_kind, ch.precision, CASE WHEN ch.uplink THEN 't' ELSE 'f' END), ',' ORDER BY ch.name) FROM community_channels ch JOIN communities c ON c.id = ch.community_id WHERE c.slug='{slug}'")

page = adm.get(B + "/admin/communities").text
check("the admin page lists the presets with the 500 kHz ones", all(w in page for w in ("LongFast", "LongTurbo", "MediumTurbo", "ShortTurbo", "MediumFast", "VLongSlow")) and "Expected US default" in page)
check("the ham-band and MeshCore rows are shown but cannot be ticked", "TinyFast" in page and "NarrowSlow" in page and "MeshCore" in page and 'value="mt-tinyfast"' not in page and 'value="mc-public"' not in page and 'value="mt-longturbo"' in page)
check("the 500 kHz rule is explained", "500 kHz" in page and "15.247" not in page or "902 to 928" in page)
check("a member cannot list presets", X.post(B + "/admin/communities/presets/add", data={"csrf_token": tok(X, "/my/mesh"), "preset": "mt-longturbo", "which": "all"}, allow_redirects=False).status_code in (302, 403, 404) and chans("cmp-alpha") == "")

ok, m, _ = act(adm, "/admin/communities/presets/add", "/admin/communities", which="all")
check("listing with nothing ticked is refused", not ok and "Tick at least one" in m, m)
ok, m, _ = act(adm, "/admin/communities/presets/add", "/admin/communities", preset="mt-tinyfast", which="all")
check("a ham-band row cannot be listed", not ok and "Tick at least one" in m, m)
ok, m, _ = act(adm, "/admin/communities/presets/add", "/admin/communities", preset="mt-longturbo", which="chosen")
check("choosing 'only the ones ticked' with none ticked is refused", not ok and "Choose All" in m, m)

ok, m, _ = act(adm, "/admin/communities/presets/add", "/admin/communities", preset="mt-longturbo", which="chosen", community="cmp-alpha")
check("one preset on one chosen community", ok and chans("cmp-alpha") == "LongTurbo:public:default:16:f" and chans("cmp-beta") == "", (m, chans("cmp-alpha")))
ok, m, _ = act(adm, "/admin/communities/presets/add", "/admin/communities", preset="mt-longturbo", which="chosen", community="cmp-alpha")
check("the same preset again is skipped, not doubled", ok and "1 skipped" in m and chans("cmp-alpha") == "LongTurbo:public:default:16:f", (m, chans("cmp-alpha")))
mark = psql("SELECT count(*) FROM community_channels")
ok, m, _ = act(adm, "/admin/communities/presets/add", "/admin/communities", preset=["mt-longturbo", "mt-mediumturbo"], which="all")
check("two presets on all communities add only what is missing", ok and chans("cmp-alpha") == "LongTurbo:public:default:16:f,MediumTurbo:public:default:16:f" and "LongTurbo" in chans("cmp-beta") and "MediumTurbo" in chans("cmp-beta"), (m, chans("cmp-alpha"), chans("cmp-beta")))
check("the names are in the Public Channels setting", all(n in psql("SELECT value FROM settings WHERE key='mesh_public_channels'") for n in ("LongTurbo", "MediumTurbo")))
check("the default of the setting has the new names", "LongTurbo" in psql("SELECT 1") + __import__("subprocess").run(["ssh", "-q", "nastemp", "grep -h mesh_public_channels ~/adsb-portal/app/settings.py"], capture_output=True, text=True).stdout)
audit = psql("SELECT count(*) FROM audit_log WHERE detail LIKE '%LongTurbo%' AND action='community.channel'")
check("it is in the audit log", audit.isdigit() and int(audit) >= 1, audit)

# the community's own page
cp = O.get(B + "/mesh/c/cmp-alpha/channels").text
check("the Owner sees the public channels on the page, with the QR code", "LongTurbo" in cp and "Add to the Meshtastic App" in cp and "List a Public Channel" in cp)
qr = O.get(B + "/mesh/c/cmp-alpha/channels/" + psql("SELECT ch.id FROM community_channels ch JOIN communities c ON c.id=ch.community_id WHERE c.slug='cmp-alpha' AND ch.name='LongTurbo'") + "/qr.svg")
check("its QR code opens", qr.status_code == 200 and "svg" in qr.headers.get("content-type", ""))
check("the quick-list form offers the Meshtastic presets and not the ham or MeshCore rows", 'value="mt-shortturbo"' in cp and 'value="mt-tinyfast"' not in cp and 'value="mc-public"' not in cp)
ok, m, _ = act(O, "/mesh/c/cmp-alpha/channels/add-preset", "/mesh/c/cmp-alpha/channels", preset="mt-mediumfast")
check("a Manager lists a preset with one click", ok and "MediumFast:public:default" in chans("cmp-alpha"), (m, chans("cmp-alpha")))
ok, m, _ = act(O, "/mesh/c/cmp-alpha/channels/add-preset", "/mesh/c/cmp-alpha/channels", preset="mt-mediumfast")
check("the same one again is refused", not ok and "already" in m, m)
ok, m, _ = act(O, "/mesh/c/cmp-alpha/channels/add-preset", "/mesh/c/cmp-alpha/channels", preset="mt-narrowslow")
check("a ham-band preset is refused there too", not ok and "Choose one" in m, m)
r = X.post(B + "/mesh/c/cmp-alpha/channels/add-preset", data={"csrf_token": tok(X, "/my/mesh"), "preset": "mt-shortturbo"}, allow_redirects=False)
check("someone outside the team cannot list one", r.status_code in (302, 403, 404) and "ShortTurbo" not in chans("cmp-alpha"), r.status_code)
check("the join form suggests the modem presets", 'list="jp-list"' in O.get(B + "/mesh/c/cmp-alpha/edit").text and "LongTurbo" in O.get(B + "/mesh/c/cmp-alpha/edit").text)
setting('mesh_public_channels', 'LongFast,MediumFast')

# ---- the current public channel, and the empty join address
setting("mesh_public_current", "mt-longturbo")
psql("DELETE FROM community_channels WHERE community_id IN (SELECT id FROM communities WHERE slug LIKE 'cmp-%')")
page = adm.get(B + "/admin/communities").text
check("the admin page names the current public channel and marks it", "The current public channel is <b>LongTurbo</b>" in page and '<span class="tag">current</span>' in page)
check("the community's quick-list box has it selected", re.search(r'<option value="mt-longturbo" selected>LongTurbo[^<]*\(current\)', O.get(B + "/mesh/c/cmp-alpha/channels").text) is not None)
ok, m, _ = act(adm, "/admin/communities/presets/current", "/admin/communities")
check("one click lists the current channel on every community", ok and "LongTurbo" in chans("cmp-alpha") and "LongTurbo" in chans("cmp-beta"), (m, chans("cmp-alpha")))
ok, m, _ = act(adm, "/admin/communities/presets/current", "/admin/communities")
check("again it skips what is there", ok and "had it already" in m and chans("cmp-alpha").count("LongTurbo") == 1, m)
setting("mesh_public_current", "gone")
check("a removed choice falls back to the first preset", "The current public channel is <b>LongFast</b>" in adm.get(B + "/admin/communities").text)
psql("DELETE FROM settings WHERE key = 'mesh_join_host'")
sp = adm.get(B + "/admin/settings/radio").text
check("Settings has the Current Public Channel choice", "Current Public Channel" in sp and 'value="mt-longturbo"' in sp)
check("an empty join address shows the Feed host name as grey text and the help says so", 'id="mesh_join_host"' in sp and re.search(r'id="mesh_join_host"[^>]*placeholder="[a-z0-9.-]+.[a-z]+"', sp) is not None and "Leave it empty to use the Feed host name" in sp and "Empty shows that the broker is not open yet" not in sp)
psql("DELETE FROM settings WHERE key = 'mesh_public_current'")
psql("DELETE FROM communities WHERE slug LIKE 'cmp-%'")
psql("DELETE FROM users WHERE username LIKE 'cpre%'")
psql("DELETE FROM settings WHERE key='mesh_public_channels'")
print("ALL PASS" if ok_all else "SOME FAILED")
