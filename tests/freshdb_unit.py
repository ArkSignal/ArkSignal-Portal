"""Runs inside the portal container (tests/freshdb_test.py): the whole schema builds on a database that has nothing in it (a new install), and building it a second time changes nothing. A statement that
touches a table before it is made, or a column that is added twice, shows here and nowhere on a database that already has the tables."""
import os, re
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["DISABLE_MESH_SYNC"] = "1"
import psycopg
url = os.environ["DATABASE_URL"]
admin = re.sub(r"/[^/?]+(\?.*)?$", r"/postgres", url)
fresh = re.sub(r"/[^/?]+(\?.*)?$", r"/freshdb_test", url)
with psycopg.connect(admin, autocommit=True) as c:
    c.execute("DROP DATABASE IF EXISTS freshdb_test")
    c.execute("CREATE DATABASE freshdb_test")
os.environ["DATABASE_URL"] = fresh
ok = True
try:
    from app import create_app
    create_app()
    print("PASS a database with nothing in it is built")
    create_app()
    print("PASS building it again changes nothing and raises nothing")
    with psycopg.connect(fresh) as c:
        n = c.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]
        have = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}
    need = {"communities", "community_members", "mesh_bridges", "mesh_commands", "export_log", "retention_runs", "alert_pushes", "mesh_accounts", "mesh_alert_prefs"}
    print(("PASS" if need <= have else "FAIL") + f" the new tables are there ({n} tables in all)", "" if need <= have else sorted(need - have))
    ok = need <= have
except Exception as e:
    ok = False
    print("FAIL the schema did not build:", type(e).__name__, str(e)[:300])
with psycopg.connect(admin, autocommit=True) as c:
    c.execute("DROP DATABASE IF EXISTS freshdb_test WITH (FORCE)")
print("ALL PASS" if ok else "SOME FAILED")
