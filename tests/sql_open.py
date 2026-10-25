"""Runs inside the portal container for the test suites: runs one SELECT and prints each row with its sealed values opened (a test cannot read sealed text with plain SQL). Rows come out as 'a|b|c', one per line."""
import os, sys
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["DISABLE_MESH_SYNC"] = "1"
from app import create_app
app = create_app()
with app.app_context():
    from app import vault
    from app.db import get_db
    for r in get_db().execute(sys.argv[1]).fetchall():
        print("|".join("" if v is None else str(vault.unseal(v) if isinstance(v, str) else v) for v in r.values()))
