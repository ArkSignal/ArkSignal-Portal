
import sys, os, json
sys.path.insert(0, "/app")
os.environ["DATABASE_URL"] = "postgresql://predict:predict@predict-db:5432/predict"
import predictd as p, gfsdata as g
from datetime import timedelta
d = g._load(p.db)
col = g.column(p.db, 34.75, -92.3, d["cycle"] + timedelta(hours=7))
print(json.dumps({"n": len(col), "z": [round(c[0]) for c in col], "spd": [round((c[1] ** 2 + c[2] ** 2) ** .5, 1) for c in col]}))
