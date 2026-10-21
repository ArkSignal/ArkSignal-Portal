import os
os.environ["DISABLE_BACKUP_SCHEDULER"]="1"
from app import create_app
from app.db import get_db
from app.settings import set_setting
from app import history as hist
app=create_app()
with app.app_context():
    d=get_db()
    d.execute("INSERT INTO login_events (ts, username, success, detail) VALUES (now() - interval '800 days', 'old-ghost', false, 'old')")
    d.execute("INSERT INTO audit_log (ts, actor_name, action) VALUES (now() - interval '800 days', 'old', 'old.action')")
    d.execute("INSERT INTO audit_log (ts, actor_name, action) VALUES (now() - interval '10 days', 'recent', 'recent.action')")
    d.commit()
    set_setting("history_retention_days","0"); print("retention 0 removes:", hist.prune())
    set_setting("history_retention_days","730"); print("retention 730 removes:", hist.prune())
    print("old rows gone:", d.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action='old.action'").fetchone()["n"]==0,
          "| recent kept:", d.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action='recent.action'").fetchone()["n"]==1)
    d.execute("DELETE FROM audit_log WHERE action='recent.action'"); d.commit()
