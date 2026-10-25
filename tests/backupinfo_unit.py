import os, json, tempfile
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["BACKUP_DIR"] = tempfile.mkdtemp()
from app import create_app
app = create_app()
from app import backup as bk
out = {}
with app.app_context():
    name, _m = bk.create_backup(app.config["DATABASE_URL"], "manual")
    p = bk.backup_path(name)
    out["info_written"] = os.path.exists(p + ".info")
    calls = []
    orig = bk.read_manifest
    bk.read_manifest = lambda x: (calls.append(x), orig(x))[1]
    a = bk.list_backups()
    b = bk.list_backups()
    out["no_reads"] = len(calls) == 0 and a[0]["rows"] > 0 and a == b
    os.remove(p + ".info")
    c = bk.list_backups()
    out["rebuilt"] = len(calls) == 1 and os.path.exists(p + ".info") and c[0]["rows"] == a[0]["rows"]
    open(p, "ab").write(b"x")
    d = bk.list_backups()
    out["changed_file_rechecked"] = len(calls) == 2
    bk.delete_backup(name)
    out["deleted_both"] = not os.path.exists(p) and not os.path.exists(p + ".info") and bk.list_backups() == []
print(json.dumps(out))
