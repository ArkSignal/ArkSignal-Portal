import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app
from app import repeaters as rp
app = create_app()
with app.app_context():
    from app.db import get_db
    d = get_db()
    d.execute("DELETE FROM repeaters"); d.commit()
    h1 = {"id": 5972, "callsign": "kg5qym", "latitude": 34.51, "longitude": -93.25, "city": "Royal", "group": "DMR", "mode": "DMR", "encode": "CC1", "frequency": 442100000, "offset": 5000000, "description": "x", "operational": 1}
    h2 = dict(h1, id=3000, callsign="W5T00", mode="FM", encode="100.0", frequency=147000000)
    rp.import_hearham(raw=[h1, h2])
    for kw in ({"q": "KG5QYM"}, {"q": "442.1"}, {"mode": "DMR"}, {"band": "70 cm"}, {"band": "2 m"}, {"q": "zzzzz"}):
        print(kw, rp.search(**kw)[0])
    d.execute("DELETE FROM repeaters"); d.commit()
