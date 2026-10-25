"""Admin > Data Retention: the page and its menu entry, nothing thinned by default, a dry run that only counts, thinning of old weather readings (one per station and time slot, real readings kept, recent
ones untouched, in batches, and the same again removes nothing), the run log and audit entry, the settings, who may open it, and (when the Matrix service is running on the test box) the removal of old chat
files through the chat server's own admin interface."""
import os, re, sys, requests
os.environ["TEST_HOST"] = "test-box.invalid"
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mxon
psql = mxon.psql
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
psql("DELETE FROM settings WHERE key IN ('wx_thin_days','wx_thin_seconds','matrix_media_keep_days','retention_last_run')")
adm = sess("marty", PW)
plain = sess("plain1", "plainpassword1")
page = adm.get(B + "/admin/retention").text
check("the page opens and lists the stores that grow", all(x in page for x in ("Weather Readings", "Radiosonde Positions", "Feeder Statistics", "Audit Log", "Mesh Packets and Nodes", "Chat Pictures and Files")))
check("it is in the admin menu", 'href="/admin/retention"' in adm.get(B + "/admin/usage").text)
check("a member cannot open it or run it", plain.get(B + "/admin/retention", allow_redirects=False).status_code in (302, 403, 404) and plain.post(B + "/admin/retention/run", data={"csrf_token": tok(plain, "/my/")}, allow_redirects=False).status_code in (302, 403, 404))
st = adm.get(B + "/admin/settings/site").text
check("the choices are settings", all(x in st for x in ("Thin Weather Readings Older Than", "Thin Weather Readings To One Every", "Keep Chat Pictures and Files For", "Run Nightly At", "Rows per Batch", "Keep the Run Log For")))
ok, m, _ = act(adm, "/admin/retention/run", "/admin/retention", dry="0")
check("by default nothing is set, and it says so", ok and "Nothing is set" in m, m)

psql("DELETE FROM wx_obs WHERE sid LIKE 'ret%'")
psql("INSERT INTO wx_obs (ts, src, sid, temp_f) SELECT date_trunc('hour', now() - interval '40 days') + (i * interval '10 minutes'), 'test', 'retA', 60 + i FROM generate_series(0, 11) i")
psql("INSERT INTO wx_obs (ts, src, sid, temp_f) SELECT date_trunc('hour', now() - interval '40 days') + (i * interval '10 minutes'), 'test', 'retB', 50 + i FROM generate_series(0, 11) i")
psql("INSERT INTO wx_obs (ts, src, sid, temp_f) SELECT now() - interval '1 day' - (i * interval '10 minutes'), 'test', 'retA', 70 FROM generate_series(0, 4) i")
count = lambda where: int(psql(f"SELECT COUNT(*) FROM wx_obs WHERE sid LIKE 'ret%' AND {where}"))
check("test readings are in: 24 old, 5 recent", count("ts < now() - interval '30 days'") == 24 and count("ts > now() - interval '2 days'") == 5)
setting("wx_thin_days", "30")
setting("wx_thin_seconds", "3600")
ok, m, _ = act(adm, "/admin/retention/run", "/admin/retention", dry="1")
check("a dry run counts what would go (20 of the 24) and removes nothing", ok and "Dry run" in m and "20" in m and count("ts < now() - interval '30 days'") == 24, m)
ok, m, _ = act(adm, "/admin/retention/run", "/admin/retention", dry="0")
check("Run Now thins them: each station keeps one reading per hour", ok and count("ts < now() - interval '30 days'") == 4 and "20" in m, (m, count("ts < now() - interval '30 days'")))
check("the readings kept are the real first ones of each hour", psql("SELECT string_agg(temp_f::int::text, ',' ORDER BY sid, ts) FROM wx_obs WHERE sid IN ('retA','retB') AND ts < now() - interval '30 days'") == "60,66,50,56")
check("recent readings are untouched", count("ts > now() - interval '2 days'") == 5)
ok, m, _ = act(adm, "/admin/retention/run", "/admin/retention", dry="0")
check("running again removes nothing more", ok and count("ts < now() - interval '30 days'") == 4, m)
check("the run is in the log and the audit log", "thinned: 20" in adm.get(B + "/admin/retention").text and "retention.run" in psql("SELECT string_agg(action, ',') FROM audit_log WHERE action = 'retention.run'"))

# batches: more rows than one batch
psql("INSERT INTO wx_obs (ts, src, sid, temp_f) SELECT date_trunc('hour', now() - interval '45 days') + (i * interval '1 second'), 'test', 'retbig', 1 FROM generate_series(0, 3000) i")
setting("retention_batch_rows", "1000")
ok, m, _ = act(adm, "/admin/retention/run", "/admin/retention", dry="0")
check("a run bigger than one batch still finishes (3,000 rows in one hour slot leave one)", ok and count("sid = 'retbig'") == 1, (m, count("sid = 'retbig'")))
psql("DELETE FROM wx_obs WHERE sid LIKE 'ret%'")
psql("DELETE FROM settings WHERE key IN ('wx_thin_days','wx_thin_seconds','retention_batch_rows')")

# chat files: through the real chat server when it is running
mxon_loaded = True
if "service not answering" in adm.get(B + "/admin/matrix").text:
    print("SKIP chat file removal: the Matrix service is not running on the test box (sh tests/deploy_matrix.sh)")
else:
    setting("matrix_media_keep_days", "1")
    ok, m, _ = act(adm, "/admin/retention/run", "/admin/retention", dry="0")
    check("old chat files are removed through the chat server (none old enough here)", ok and "Chat files removed: 0" in m, m)
    check("the run log names it", "Chat files removed" in adm.get(B + "/admin/retention").text)
psql("DELETE FROM settings WHERE key IN ('matrix_media_keep_days','matrix_enabled')")
print("ALL PASS" if ok_all else "SOME FAILED")
