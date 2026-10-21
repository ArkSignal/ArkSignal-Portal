"""Run inside the portal container (sondes_test): gives the demonstration flights and stations their state, as the feed's sweep does."""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
import psycopg
from psycopg.rows import dict_row
from app import create_app
from app import sondes as sd
app = create_app()
with app.app_context():
    conn = psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row)
    print("assigned", sd.assign_states(conn))
