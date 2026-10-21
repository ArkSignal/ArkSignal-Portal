
import os, json
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from app import create_app
app = create_app()
with app.app_context():
    from app import aprspush as ap, weather as wx
    from app.db import get_db
    d = get_db()
    d.execute("UPDATE wx_push SET last_push = now() - interval '2 hours', last_reading = NULL, fail_count = 0, enabled = TRUE, mode = 'cwop', callsign = 'CW1234'")
    d.commit()
    rows = ap.due(d)
    out = {"due": len(rows)}
    row = d.execute(wx.SELECT + "WHERE w.id = (SELECT station_pk FROM wx_push LIMIT 1)").fetchone()
    out["first"] = ap.push_one(row)
    row = d.execute(wx.SELECT + "WHERE w.id = (SELECT station_pk FROM wx_push LIMIT 1)").fetchone()
    out["again"] = ap.push_one(row)
    d.execute("UPDATE wx_push SET last_push = now()"); d.commit()
    out["not_due"] = len(ap.due(d))
    d.execute("UPDATE wx_stations SET last_report = now() - interval '3 hours'"); d.commit()
    row = d.execute(wx.SELECT + "WHERE w.id = (SELECT station_pk FROM wx_push LIMIT 1)").fetchone()
    d.execute("UPDATE wx_push SET last_reading = NULL"); d.commit()
    out["stale"] = ap.push_one(row)
    print(json.dumps(out))
