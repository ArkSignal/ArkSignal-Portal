"""Distances in Admin settings: stored in metres or kilometres (so nothing that reads them changes), shown and typed in feet and miles by default or in metres and
kilometres (Settings > Distances in Admin Settings). A value may carry its own unit when typed ("250 m", "0.5 mi") in either mode."""
import re

from .settings import cfg

M_PER = {"m": 1.0, "metre": 1.0, "metres": 1.0, "meter": 1.0, "meters": 1.0, "ft": 0.3048, "foot": 0.3048, "feet": 0.3048,
         "km": 1000.0, "kilometre": 1000.0, "kilometres": 1000.0, "kilometer": 1000.0, "kilometers": 1000.0, "mi": 1609.344, "mile": 1609.344, "miles": 1609.344}
BASE_M = {"m": 1.0, "km": 1000.0}


SYSTEMS = ("imperial", "aero", "metric")
# the services, each with its own unit set: a person's own choice for it, else the site's default for it (Settings > Units), else the standard unit (their own overall choice, else the site default)
AREAS = [("aircraft", "Aircraft"), ("weather", "Weather"), ("sondes", "Radiosondes"), ("aprs", "APRS"), ("acars", "ACARS"), ("mesh", "Mesh"), ("repeaters", "Repeaters"),
         ("places", "Places and Counties"), ("network", "Coverage, Feeders and MLAT"), ("home", "Home Page")]
BP_AREA = {"live": "aircraft", "histmap": "aircraft", "hems": "aircraft", "interesting": "aircraft", "wx": "weather", "sondes": "sondes", "aprs": "aprs", "acars": "acars", "mesh": "mesh",
           "repeaters": "repeaters", "counties": "places", "trauma": "places", "places": "places", "hospitals": "places", "public": "network", "status": "network"}
ENDPOINT_AREA = {"live.home": "home", "public.airports": "aircraft", "public.airport": "aircraft", "public.search": "home", "public.about": "home", "public.contact": "home"}


def imperial(area=None):
    """Feet and miles (also for the aeronautical set) unless the metric set is in use."""
    return system(area) != "metric"


def area_now():
    """The service the page being built belongs to ('' for the admin pages, which use the standard unit)."""
    try:
        from flask import request
        return ENDPOINT_AREA.get(request.endpoint or "", BP_AREA.get(request.blueprint or "", ""))
    except Exception:
        return ""


def site_default(area):
    """The unit set the site chose for a service ('' = the standard unit)."""
    v = cfg("units_" + area) if area else ""
    return v if v in SYSTEMS else ""


def _prefs():
    """The signed-in person's display preferences (a dict), read once per request."""
    try:
        from flask import g
        from flask_login import current_user
        if current_user.is_authenticated:
            if not hasattr(g, "_unit_prefs"):
                import json
                from .db import get_db
                row = get_db().execute("SELECT prefs FROM users WHERE id=%s", (current_user.id,)).fetchone()
                try:
                    g._unit_prefs = json.loads(row["prefs"]) if row and row["prefs"] else {}
                except ValueError:
                    g._unit_prefs = {}
            return g._unit_prefs
    except Exception:
        pass
    return {}


def _cookie_area(area):
    """A person who is not signed in may have chosen units for a service in the Display menu of this browser: the page script keeps those choices in a cookie (service:set|service:set) for server-built pages."""
    try:
        from flask import request
        for part in request.cookies.get("ark_ua", "").split("|"):
            k, _s, v = part.partition(":")
            if k == area and v in SYSTEMS:
                return v
    except Exception:
        pass
    return ""


def _own_overall():
    """The person's own overall choice (Display menu > Units), signed in or in this browser's cookie; '' when they have not made one."""
    p = _prefs().get("units", "")
    if p in SYSTEMS:
        return p
    try:
        from flask import request
        c = request.cookies.get("ark_units", "")
        if c in SYSTEMS:
            return c
    except Exception:
        pass
    return ""


def standard():
    """The standard unit set: the person's own overall choice (Display menu > Units), else the site default (Settings > Home Page and Live Map)."""
    own = _own_overall()
    if own:
        return own
    v = cfg("live_units")
    return v if v in SYSTEMS else "imperial"


def system(area=None):
    """The units in use for a service (default: the service the page belongs to): imperial, aero or metric. In order: the person's own choice for the service, their overall choice (Display menu > Units),
    the site's default for the service (Settings > Units: each service's industry-standard units), then the site's overall default."""
    a = area if area is not None else area_now()
    if a:
        p = _prefs().get("u_" + a, "")
        if p in SYSTEMS:
            return p
        c = _cookie_area(a)
        if c:
            return c
    own = _own_overall()
    if own:
        return own
    if a:
        d = site_default(a)
        if d:
            return d
    return standard()


def unit_for(base):
    """The unit name a setting stored in `base` ('m' or 'km') is shown in: feet or metres for metres, miles or kilometres for kilometres."""
    return ("feet" if base == "m" else "miles") if imperial() else ("metres" if base == "m" else "kilometres")


def _per_unit(base):
    return {"feet": 0.3048, "miles": 1609.344, "metres": 1.0, "kilometres": 1000.0}[unit_for(base)]


def show(value, base):
    """The stored value as the number to put in the box."""
    try:
        x = float(value) * BASE_M[base] / _per_unit(base)
    except (TypeError, ValueError):
        return str(value)
    return f"{x:.0f}" if x >= 100 else f"{x:.2f}".rstrip("0").rstrip(".")


def fmt(value, base):
    """The stored value in words for a page: '820 feet', '0.62 miles'."""
    if value is None:
        return ""
    n = show(value, base)
    unit = unit_for(base)
    return f"{n} {unit[:-1] if n == '1' else unit}"


def parse(raw, base):
    """What was typed -> (value in the stored unit, error). A bare number is in the unit shown; '250 m', '0.5 mi', '800 ft' carry their own."""
    m = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*$", (raw or "")[:40])
    if not m:
        return None, "Enter a distance such as 800 or 0.5 mi."
    word = m.group(2).lower()
    if word and word not in M_PER:
        return None, f"Unknown unit '{m.group(2)}': use feet, miles, metres or km."
    metres = float(m.group(1)) * (M_PER[word] if word else _per_unit(base))
    v = metres / BASE_M[base]
    return float(f"{v:.6g}"), None


# ---- values of any kind in the viewer's own units (metric inputs), for the page templates
_SETS = {"imperial": {"alt": (3.28084, "ft"), "spd": (2.236936, "mph"), "vspd": (196.85, "ft/min"), "dist": (0.621371, "mi"), "nm": (1.150779, "mi"), "press": (0.02953, "inHg"), "rain": (0.03937, "in"), "temp": "F"},
         "aero": {"alt": (3.28084, "ft"), "spd": (1.943844, "kt"), "vspd": (196.85, "ft/min"), "dist": (0.539957, "nm"), "nm": (1.0, "nm"), "press": (1.0, "hPa"), "rain": (0.03937, "in"), "temp": "C"},
         "metric": {"alt": (1.0, "m"), "spd": (3.6, "km/h"), "vspd": (1.0, "m/s"), "dist": (1.0, "km"), "nm": (1.852, "km"), "press": (1.0, "hPa"), "rain": (1.0, "mm"), "temp": "C"}}


def text_ft(feet):
    """A length given in feet (airport elevation, runway length) in the viewer's altitude unit."""
    try:
        return text("alt", float(feet) * 0.3048, 0)
    except Exception:
        return "-"


def text_kt(knots):
    """A speed given in knots (the aircraft feed) in the viewer's speed unit."""
    try:
        return text("spd", float(knots) * 0.514444, 0)
    except Exception:
        return "-"


def unit_name(kind, area=None):
    """The unit word for a kind of value (alt, spd, vspd, dist, temp) in the viewer's units."""
    u = _SETS[system(area)]
    return ("\u00b0" + u["temp"]) if kind == "temp" else u[kind][1]


def text(kind, value, digits=None, area=None):
    """'12,345 ft', '5.1 m/s', '31 mi' ... from a metric value (meters, m/s, kilometers, degrees C) in the viewer's units; '-' when there is none."""
    try:
        value = float(value)
    except Exception:   # none, or a value the page does not have (an undefined template value too)
        return "-"
    u = _SETS[system(area)]
    if kind == "temp":
        v = value * 9 / 5 + 32 if u["temp"] == "F" else value
        return f"{v:.{1 if digits is None else digits}f} \u00b0{u['temp']}"
    f, name = u[kind]
    v = value * f
    d = digits if digits is not None else (1 if abs(v) < 10 else 0)
    return f"{v:,.{d}f} {name}"
