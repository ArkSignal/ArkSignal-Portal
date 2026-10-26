"""Runs inside the portal container (tests/codeql_test.py): the fixes made after the code scanning review. Prints one JSON line."""
import json, os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["DISABLE_MESH_SYNC"] = "1"
from app import create_app
app = create_app()
out = {}
with app.app_context():
    from app import backup as bk, siteaddress, update_check, units, places
    from app.settings import set_setting
    from app.db import get_db
    # the site address check never contacts a private or loopback address
    set_setting("webhook_allow_private", "0")
    msg = siteaddress.check("localhost", timeout=3)
    out["the_address_check_refuses_a_loopback_name"] = bool(msg) and "private or internal" in msg
    # a backup name can never leave the backup folder
    bad = 0
    for n in ("../x", "portal-backup-20260101-000000-manual.tar.gz/../../etc", "/etc/passwd", "a/b", "portal-backup-20260101-000000-manual.tar.gz\n"):
        try:
            bk.backup_path(n); bad += 1
        except ValueError:
            pass
    good = bk.backup_path("portal-backup-20260101-000000-manual.tar.gz")
    out["a_backup_name_cannot_leave_the_folder"] = bad == 0 and os.path.dirname(good) == os.path.realpath(bk.backup_dir())
    # a repository name is checked before it is put in an address
    set_setting("update_check_repo", "../../evil?x=1")
    r = update_check.check_now()
    out["a_bad_repository_name_is_refused"] = "not a valid name" in str(r.get("error"))
    set_setting("update_check_repo", "mallison01/adsb-portal")
    # a track file with an entity trick is refused, a normal one is read
    from app import myflightstracks as mt
    boom = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><gpx><trk><trkseg><trkpt lat="1" lon="2"><name>&b;</name></trkpt></trkseg></trk></gpx>'
    try:
        mt.read_track(boom); refused = False
    except ValueError:
        refused = True
    gpx = '<?xml version="1.0"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg><trkpt lat="35.0" lon="-92.0"><ele>100</ele><time>2024-01-01T10:00:00Z</time></trkpt><trkpt lat="35.1" lon="-92.1"><ele>200</ele><time>2024-01-01T10:05:00Z</time></trkpt></trkseg></trk></gpx>'
    try:
        pts, meta = mt.read_track(gpx); okread = len(pts) == 2
    except Exception as e:
        okread = str(e)
    out["a_track_with_an_entity_trick_is_refused_and_a_normal_one_is_read"] = refused and okread is True
    # long inputs are cut before a pattern runs
    import time
    t = time.time()
    units.parse("1" * 200000 + "!", "m"); places.search("a," + " " * 200000 + "x")
    out["very_long_input_is_handled_at_once"] = time.time() - t < 2
    # a restore refuses a backup file whose header names a column the table does not have
    import io, tarfile, tempfile
    from app import restorepart as rp
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(app.config["DATABASE_URL"]) as conn:
        tmp = tempfile.mkdtemp()
        plain = os.path.join(tmp, "p.tar.gz")
        with tarfile.open(plain, "w:gz") as tar:
            data = b'"id","name\\" TEXT); DROP TABLE users; --"\n"1","x"\n'
            ti = tarfile.TarInfo("tables/settings.csv"); ti.size = len(data); tar.addfile(ti, io.BytesIO(data))
        try:
            rp._load_temp(conn, plain, {"tables": ["settings"]}, "settings", {}); refused2 = False
        except ValueError:
            refused2 = True
        conn.rollback()
    out["a_backup_column_that_is_not_the_tables_is_refused"] = refused2
    n = get_db().execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    out["users_are_still_there"] = n >= 1
print(json.dumps(out))
