"""The trauma system (app/trauma.py), run inside the portal container: the Department of Health's county data and list of designated trauma centers are complete and agree with themselves, the
Trauma Map's hospital scope (in the state, designated anywhere, near the state line) is worked out the way Settings says, and what an administrator types in for a website or picture is kept."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
from app import create_app
from app import trauma as tr
from app.db import get_db
from app.settings import cfg, set_setting
app = create_app()
with app.app_context():
    t = tr.county_table()
    check("all 75 counties of Arkansas have a trauma region and a zone", len(t) == 75 and all(v["region"] and v["zone"] in (1, 2) for v in t.values()), len(t))
    sizes = {}
    for v in t.values():
        sizes[v["region"]] = sizes.get(v["region"], 0) + 1
    check("the seven regions have the counties the Department lists", sizes == {"Northwest": 6, "Arkansas Valley": 9, "Metro": 6, "North Central": 13, "Northeast": 14, "Southeast": 10, "Southwest": 17}, sizes)
    check("the two zones split the state (34 north, 41 south)", sum(1 for v in t.values() if v["zone"] == 1) == 34 and sum(1 for v in t.values() if v["zone"] == 2) == 41)
    check("a few counties are where the Department's map has them", t["Pulaski"] == {"region": "Metro", "zone": 2} and t["Benton"] == {"region": "Northwest", "zone": 1} and t["Union"]["region"] == "Southwest" and t["Craighead"]["region"] == "Northeast")
    check("a county is found by its plain or full name, any capitals", tr.county_trauma("Hot Spring County")["region"] == "Southwest" and tr.county_trauma("van buren")["region"] == "North Central" and tr.county_trauma("Pulaski", "MO") is None)
    c = json.load(open(os.path.join(os.path.dirname(tr.__file__), "data_trauma_ar_centers.json"), encoding="utf-8"))["centers"]
    lv = {}
    for r in c:
        lv[r["level"]] = lv.get(r["level"], 0) + 1
    check("the Department's list has 51 designated trauma centers (6 Level I, 4 Level II, 17 Level III, 24 Level IV)", lv == {"I": 6, "II": 4, "III": 17, "IV": 24} and len(c) == 51, lv)
    check("each center in the Department's list names its CMS certification number, except the two the CMS list does not have", sum(1 for r in c if not r.get("ccn")) == 2 and all(len(r["ccn"]) == 6 for r in c if r.get("ccn")), [r["name"] for r in c if not r.get("ccn")])
    check("regions and zones come with where to put their names", all(g["lat"] and g["lon"] for g in tr.regions()) and len(tr.regions()) == 7 and len(tr.zones()) == 2 or not __import__("app.counties", fromlist=["x"]).shapes())
    # ---- which hospitals the Trauma Map shows
    d = get_db()
    d.execute("DELETE FROM hospitals WHERE facility_id LIKE 'zz-%'")
    def add(fid, name, state, lat, lon, emergency, trauma="", county=""):
        d.execute("INSERT INTO hospitals (facility_id, name, city, state, county, emergency, lat, lon, pos_source, trauma) VALUES (%s,%s,'X',%s,%s,%s,%s,%s,'manual',%s)", (fid, name, state, county, emergency, lat, lon, trauma))
    add("zz-ar-er", "Test AR Emergency", "AR", 34.7, -92.3, True, county="PULASKI")
    add("zz-ar-noer", "Test AR Clinic Only", "AR", 34.8, -92.4, False)
    add("zz-mo-l1", "Test MO Level One", "MO", 37.2, -93.3, True, "I")
    add("zz-tn-near", "Test TN Near", "TN", 35.14, -90.04, True)
    add("zz-tn-far", "Test TN Far", "TN", 36.16, -86.78, True)
    add("zz-ok-noer", "Test OK No ER Near", "OK", 35.3, -94.5, False)
    d.commit()
    old = {k: cfg(k) for k in ("osrm_enabled", "trauma_emergency_only", "trauma_border_minutes", "trauma_border_fallback_km")}
    try:
        set_setting("osrm_enabled", "0"); set_setting("trauma_emergency_only", "1"); set_setting("trauma_border_minutes", "60"); set_setting("trauma_border_fallback_km", "60")
        out = tr.compute_scope()
        sc = {r["facility_id"]: r["trauma_scope"] for r in d.execute("SELECT facility_id, trauma_scope FROM hospitals WHERE facility_id LIKE 'zz-%'").fetchall()}
        check("a hospital of the state with an emergency department is shown", sc["zz-ar-er"] == "state", sc)
        check("one of the state without an emergency department is not (Settings: only hospitals with an emergency department)", sc["zz-ar-noer"] == "", sc)
        check("a designated trauma center of another state is shown", sc["zz-mo-l1"] == "system", sc)
        check("a hospital of a neighboring state close to the state line is shown (a straight line when routing is off)", sc["zz-tn-near"] == "border", sc)
        check("a far one, and one without an emergency department, are not", sc["zz-tn-far"] == "" and sc["zz-ok-noer"] == "", sc)
        set_setting("trauma_emergency_only", "0")
        tr.compute_scope()
        check("with the setting off every hospital of the state is shown", d.execute("SELECT trauma_scope FROM hospitals WHERE facility_id='zz-ar-noer'").fetchone()["trauma_scope"] == "state")
        set_setting("trauma_emergency_only", "1"); set_setting("trauma_border_minutes", "0")
        tr.compute_scope()
        check("zero minutes turns the near-the-state-line hospitals off", d.execute("SELECT trauma_scope FROM hospitals WHERE facility_id='zz-tn-near'").fetchone()["trauma_scope"] == "")
        set_setting("trauma_border_minutes", "60")
        tr.compute_scope()
        hm = {h["id"]: h for h in tr.hospitals_for_map()}
        check("the map's list carries the level name, address, region and the website fields", "zz-ar-er" in hm and hm["zz-ar-er"]["region"] == "Metro" and hm["zz-ar-er"]["zone"] == 2 and hm["zz-mo-l1"]["levelName"] == "Level I" and hm["zz-ar-er"]["website"] == "", hm.get("zz-ar-er"))
        # ---- websites and pictures: a person's own entries are kept
        check("a bad web address is refused", tr.save_media("hospital", "zz-ar-er", "ftp://x", "", "").startswith("The website"))
        check("an administrator's website and picture are saved", tr.save_media("hospital", "zz-ar-er", "https://example.org/h", "https://example.org/h.jpg", "Someone, CC BY") == "" and tr.media_for("hospital", "zz-ar-er")["website"] == "https://example.org/h")
        check("a lookup does not replace them", tr._save_media("hospital", "zz-ar-er", "https://other.example", ("https://other/p.jpg", "Other", ""), "Wikidata") is False and tr.media_for("hospital", "zz-ar-er")["photo_credit"] == "Someone, CC BY")
        check("the map's list then carries them", {h["id"]: h for h in tr.hospitals_for_map()}["zz-ar-er"]["photo"] == "https://example.org/h.jpg")
        tr.save_media("hospital", "zz-ar-er", "", "", "")
        check("clearing all three hands it back to the lookup", tr._save_media("hospital", "zz-ar-er", "https://found.example", None, "Wikidata") and tr.media_for("hospital", "zz-ar-er")["website"] == "https://found.example")
        d.execute("DELETE FROM place_media WHERE kind='hospital' AND key LIKE 'zz-%'")
    finally:
        for k, v in old.items():
            set_setting(k, v)
        d.execute("DELETE FROM hospitals WHERE facility_id LIKE 'zz-%'")
        d.commit()
        tr.compute_scope()
print("ALL PASS" if ok_all else "SOME FAILED")
