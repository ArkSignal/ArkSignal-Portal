
import os, json
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from app import create_app
app = create_app()
from app import bridge
out = {}
with app.app_context():
    out["calls"] = bridge.listen_calls()
out["added"] = bridge.listen_once(app, "127.0.0.1", 14592, 4)
print(json.dumps(out))
