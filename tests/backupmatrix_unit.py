"""The off-site backup takes the Matrix server's data (checked on this PC, no Docker needed): the small copy holds the database dump and the small files of the server (its signing key and secrets) and not the media,
the big copy holds the media, and nothing is listed when the Matrix folders do not exist."""
import importlib.util, os, sys, tempfile
base = tempfile.mkdtemp()
os.environ["BACKUP_BASE"] = base
spec = importlib.util.spec_from_file_location("backupd", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", "backup", "backupd.py"))
bd = importlib.util.module_from_spec(spec); spec.loader.exec_module(bd)
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
conf = {"big": {"prometheus": False, "globe": False}}
check("without Matrix folders nothing about Matrix is listed", not [p for p in bd.small_paths(None) + bd.big_paths(conf) if "matrix" in p])
os.makedirs(os.path.join(base, "matrix/dumps")); os.makedirs(os.path.join(base, "matrix/data/media_store"))
open(os.path.join(base, "matrix/dumps/matrix.dump"), "w").write("x")
for n in ("example.signing.key", "secrets.json", "admin.json", "applied.json"):
    open(os.path.join(base, "matrix/data", n), "w").write("x")
open(os.path.join(base, "matrix/data/media_store/pic"), "w").write("x")
small = bd.small_paths(None)
check("the small copy holds the dump and the server's small files", "matrix/dumps" in small and all(f"matrix/data/{n}" in small for n in ("example.signing.key", "secrets.json", "admin.json", "applied.json")), small)
check("the small copy does not hold the media", "matrix/data/media_store" not in small and not [p for p in small if p.startswith("matrix/data/media_store")], small)
check("the big copy holds the media", "matrix/data/media_store" in bd.big_paths(conf), bd.big_paths(conf))
os.remove(os.path.join(base, "matrix/dumps/matrix.dump"))
check("with no dump yet the dump folder is not listed (the other files still are)", "matrix/dumps" not in bd.small_paths(None) and "matrix/data/secrets.json" in bd.small_paths(None))
print("ALL PASS" if ok_all else "SOME FAILED")
