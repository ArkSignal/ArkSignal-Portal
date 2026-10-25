import os
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
