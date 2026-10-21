"""In-container checks of roads.py with made-up TIGERweb answers: routes are grouped with all their pieces, labeled (I-40, US 67, AR 7), pieces of other kinds and without a route number are
dropped, a pages-less empty answer changes nothing, the GeoJSON carries what the map layer reads, and the kinds filter works.
docker cp this file to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/roads_unit.py"""
import json
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app
from app import roads

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

def feat(rt, base, coords, name="x"):
    return {"type": "Feature", "properties": {"RTTYP": rt, "BASENAME": base, "NAME": name}, "geometry": {"type": "LineString", "coordinates": coords}}

with app.app_context():
    from app.db import get_db
    d = get_db()
    keep = d.execute("SELECT route, kind, state, name, geometry FROM roads").fetchall()
    # I-40 in two pieces, US 67 in Arkansas, state highway 7 in Arkansas and in Missouri, a county road (dropped), a piece with no route number (dropped)
    p1 = {"type": "FeatureCollection", "features": [feat("I", "40", [[-92.5, 35.1], [-92.4, 35.2], [-92.3, 35.3]], "I- 40"), feat("I", "40", [[-92.3, 35.3], [-92.2, 35.35]], "I- 40"),
          feat("C", "1234", [[-92.0, 35.0], [-92.1, 35.1]]), feat("U", "", [[-92.0, 35.0], [-92.1, 35.1]])]}
    p2 = {"type": "FeatureCollection", "features": [feat("U", "67", [[-91.9, 34.9], [-91.8, 35.0]], "US Hwy 67"), feat("S", "7", [[-93.0, 34.5], [-93.0, 34.6]], "State Hwy 7"), feat("S", "7", [[-93.0, 37.2], [-93.0, 37.3]], "State Hwy 7")]}
    c = roads.import_roads(pages=[p1, p2])
    rows = {(r["kind"], r["route"], r["state"]): r for r in d.execute("SELECT * FROM roads").fetchall()}
    check("routes are grouped: I-40 is one record with both of its pieces", ("I", "I-40", "") in rows and len(json.loads(rows[("I", "I-40", "")]["geometry"])) == 2, list(rows))
    check("a US highway is labeled US 67", ("U", "US 67", "") in rows)
    check("a state highway is labeled with its state, and the same number in another state is its own route", ("S", "AR 7", "AR") in rows and ("S", "MO 7", "MO") in rows, list(rows))
    check("a county road and a piece without a number are left out", c["routes"] == 4 and not any(r[0] == "C" for r in rows), c)
    check("the counts say what was loaded", c["interstates"] == 1 and c["us"] == 1 and c["state"] == 2 and c["segments"] == 5, c)
    gj = roads.geojson()
    f = next(x for x in gj["features"] if x["properties"]["ROUTE"] == "I-40")
    check("the GeoJSON has one multi-line feature per route with its route, kind and a readable name", gj["type"] == "FeatureCollection" and f["geometry"]["type"] == "MultiLineString" and f["properties"]["KIND"] == "I" and "Interstate" in f["properties"]["NAME"])
    check("the kinds filter gives only what is asked for", {x["properties"]["KIND"] for x in roads.geojson("U")["features"]} == {"U"} and len(roads.geojson("IUS")["features"]) == 4)
    v1 = roads.version()
    try:
        roads.import_roads(pages=[{"type": "FeatureCollection", "features": []}])
        empty_ok = False
    except RuntimeError:
        empty_ok = True
    check("an empty answer from the Census changes nothing and says so", empty_ok and len(roads.geojson()["features"]) == 4 and roads.version() == v1)
    d.execute("DELETE FROM roads")
    for r in keep:
        d.execute("INSERT INTO roads (route, kind, state, name, geometry) VALUES (%s,%s,%s,%s,%s)", (r["route"], r["kind"], r["state"], r["name"], r["geometry"]))
    d.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
