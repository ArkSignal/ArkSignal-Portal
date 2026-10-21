"""Units per service (app/units.py), run inside the portal container: each service starts in its industry's own units (the site's default for it); a person's own choice for the service beats their overall choice,
which beats the site's default for the service."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
from app import create_app
from app import units
app = create_app()
def sysat(path, cookies=""):
    with app.test_request_context(path, headers={"Cookie": cookies} if cookies else {}):
        return units.system(), units.standard()
check("every service the pages belong to is listed", [a for a, _l in units.AREAS] == ["aircraft", "weather", "sondes", "aprs", "acars", "mesh", "repeaters", "places", "network", "home"])
check("the blueprints and the pages that differ map to their services", units.BP_AREA["sondes"] == "sondes" and units.BP_AREA["wx"] == "weather" and units.BP_AREA["live"] == "aircraft" and units.BP_AREA["counties"] == "places"
      and units.ENDPOINT_AREA["live.home"] == "home" and units.ENDPOINT_AREA["public.airports"] == "aircraft")
want = {"/live": "aero", "/weather/map": "imperial", "/sondes/": "metric", "/aprs/": "imperial", "/acars/": "aero", "/mesh/": "metric", "/repeaters/": "imperial", "/counties/": "imperial", "/hospitals/": "imperial",
        "/coverage": "aero", "/feeders/": "aero", "/mlat/": "aero", "/airports/": "aero", "/": "imperial"}
got = {p: sysat(p)[0] for p in want}
check("each service starts in its industry's own units: aviation, ACARS, coverage and feeders in feet, knots and nautical miles; weather, APRS, repeaters and places in US units; radiosondes and mesh in metric; the home page standard", got == want, {p: (got[p], w) for p, w in want.items() if got[p] != w})
check("a person who chose imperial for radiosondes gets it there and nowhere else", sysat("/sondes/", "ark_ua=sondes:imperial")[0] == "imperial" and sysat("/live", "ark_ua=sondes:imperial")[0] == "aero")
check("a person who chose metric for aircraft gets it for aircraft only", sysat("/live", "ark_ua=aircraft:metric")[0] == "metric" and sysat("/weather/map", "ark_ua=aircraft:metric")[0] == "imperial")
check("a person's overall choice applies to every service that they have not chosen for (radiosondes too)", sysat("/live", "ark_units=metric")[0] == "metric" and sysat("/weather/map", "ark_units=aero")[0] == "aero" and sysat("/sondes/", "ark_units=imperial")[0] == "imperial" and sysat("/", "ark_units=metric")[0] == "metric")
check("a service's own choice beats the overall one", sysat("/live", "ark_units=metric; ark_ua=aircraft:imperial")[0] == "imperial")
check("a made-up unit set is ignored", sysat("/live", "ark_ua=aircraft:furlongs")[0] == "aero" and sysat("/live", "ark_units=furlongs")[0] == "aero")
check("the standard unit (the admin pages and everything without a service) stays the site default, imperial", sysat("/admin/")[0] == "imperial" and sysat("/admin/", "ark_units=metric")[0] == "metric")
with app.test_request_context("/live"):
    check("values are written in the unit of the service asked for, whatever the page", units.text("alt", 11579, 0, "sondes") == "11,579 m" and units.text("alt", 11579, 0, "aircraft") == f"{11579 * 3.28084:,.0f} ft" and units.text("spd", 10, 0, "sondes") == "36 km/h" and units.text("spd", 10, 0, "aircraft") == "19 kt")
    check("distances too (nautical miles for aviation, kilometers for balloons, miles for places)", units.text("dist", 100, 0, "aircraft") == "54 nm" and units.text("dist", 100, 0, "sondes") == "100 km" and units.text("dist", 100, 0, "places") == "62 mi")
print("ALL PASS" if ok_all else "SOME FAILED")
