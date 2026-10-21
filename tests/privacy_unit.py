"""In-container checks of privacy.py and the places that use it: the choices, the rounding of each level, the circle radius, and a station of each kind shown at its own level.
docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/privacy_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app
from app import privacy as pv, feeders as fd, weather as wx, stations as st, sondes as sd
app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
LAT, LON = 34.74256, -92.28421
with app.app_context():
    check("every kind of station offers the same twelve choices, in the same order", list(pv.PRIVACY) == ["exact", "fine", "near", "block", "close", "area", "district", "general", "region", "broad", "wide", "hidden"] and st.PRIVACY is pv.PRIVACY and wx.PRIVACY is pv.PRIVACY)
    check("exact keeps five places", pv.position(LAT, LON, "exact") == (34.74256, -92.28421))
    check("near rounds to three places (about 400 feet)", pv.position(LAT, LON, "near") == (34.743, -92.284))
    check("close rounds to two places (about 0.7 mile)", pv.position(LAT, LON, "close") == (34.74, -92.28))
    check("general rounds to the site's setting, one place by default (about 6 miles)", pv.position(LAT, LON, "general") == (34.7, -92.3))
    check("wide rounds to whole degrees (about 60 miles)", pv.position(LAT, LON, "wide") == (35.0, -92.0))
    check("hidden and a missing position give nothing; an unknown level is treated as general", pv.position(LAT, LON, "hidden") is None and pv.position(None, LON, "exact") is None and pv.position(LAT, LON, "nonsense") == (34.7, -92.3))
    check("the circle drawn around a rounded position is half a step: near 55 m, close 556 m, general 5566 m, wide 55660 m, none for exact", [pv.radius_m(k) for k in ("near", "close", "general", "wide", "exact", "hidden")] == [55, 556, 5566, 55660, 0, 0], [pv.radius_m(k) for k in ("near", "close", "general", "wide")])
    f = {"lat": LAT, "lon": LON, "privacy": "close"}
    check("a feeder is shown at its own level", fd.public_location(f) == (34.74, -92.28) and fd.public_location(dict(f, privacy="wide")) == (35.0, -92.0) and fd.public_location(dict(f, privacy=None)) == (34.7, -92.3) and fd.public_location(dict(f, privacy="hidden")) is None)
    s = {"lat": LAT, "lon": LON, "privacy": "near", "mobile": False}
    check("a radiosonde receiving station is shown at its own level; a moving one only when exact", sd.public_position(s) == (34.743, -92.284) and sd.public_position(dict(s, privacy="wide")) == (35.0, -92.0) and sd.public_position(dict(s, mobile=True)) is None and sd.public_position(dict(s, mobile=True, privacy="exact")) == (34.74256, -92.28421))
print("ALL PASS" if ok_all else "SOME FAILED")
