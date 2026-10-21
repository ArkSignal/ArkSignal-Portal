"""The portal's side of the APRS service (deploy/aprs, its own container and database): settings and the takedown list are sent to it, and the stations and weather it keeps are shown on
the APRS pages, the map and the home page. When the service is away every call returns a plain note and the rest of the site works as before. Terms: DATA_SOURCES_TERMS.md."""
import json
import urllib.error
import urllib.parse
import urllib.request

from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int

KIND_LABELS = {"fixed": "Fixed station", "mobile": "Mobile station", "weather": "Weather station", "digipeater": "Digipeater", "gateway": "Gateway", "igate": "IGate", "repeater": "Repeater",
               "object": "Object", "ambulance": "Ambulance", "fire": "Fire", "police": "Police", "balloon": "Balloon", "aircraft": "Aircraft"}
CREDIT = "APRS data: the amateur radio APRS-IS network, read-only"


def enabled():
    return cfg("aprs_enabled") == "1"


def call(path, method="GET", body=None, timeout=8):
    """(ok, data or message). Never raises."""
    if not enabled():
        return False, "The APRS service is switched off in Settings."
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(cfg("aprs_url").rstrip("/") + path, data=data, method=method, headers={"User-Agent": cfg("lookup_user_agent"), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read(20_000_000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"The APRS service answered with an error ({e.code})."
    except Exception as e:
        return False, "The APRS service did not answer (" + type(e).__name__ + ")."


def _filter():
    return "r/{:.3f}/{:.3f}/{}".format(float(cfg("map_lat")), float(cfg("map_lon")), max(20, min(1000, cfg_int("aprs_radius_km"))))


def service_config():
    return {"host": cfg("aprs_host"), "login": cfg("aprs_login"), "filter": _filter(), "trail_hours": cfg("aprs_trail_hours"), "keep_days": cfg("aprs_keep_days"), "window_hours": cfg("aprs_window_hours"), "enabled": "1"}


def push_config():
    """Send the settings and the takedown list (kept here so a rebuilt service forgets nobody) to the service."""
    ok, d = call("/config", "POST", service_config())
    if not ok:
        return False, d
    for r in get_db().execute("SELECT call, reason FROM aprs_hidden").fetchall():
        call("/hide", "POST", {"call": r["call"], "reason": r["reason"]})
    return True, "Settings and takedown list sent."


def stats():
    def load():
        ok, d = call("/stats", timeout=3)
        if ok and not d.get("configured"):
            push_config()   # a service that has never been configured gets the portal's settings once
            ok, d = call("/stats", timeout=3)
        return {"ok": ok, "data": d if ok else None, "note": "" if ok else d}
    return cached("aprs_stats", load, 15)


def stations(kind="", q="", hours=None, bbox="", limit=3000):
    """The service's stations (a list of dicts), or [] with the service away."""
    args = {"limit": limit}
    if kind:
        args["kind"] = kind
    if q:
        args["q"] = q
    if hours:
        args["hours"] = hours
    if bbox:
        args["bbox"] = bbox
    ok, d = call("/stations?" + urllib.parse.urlencode(args))
    rows = d.get("stations", []) if ok else []
    if cfg("aprs_show_mobile") != "1":
        rows = [r for r in rows if r["kind"] not in ("mobile", "balloon", "aircraft")]
    return rows


def stations_cached(kind="", q="", hours=None):
    return cached(("aprs_st", kind, q, hours), lambda: stations(kind, q, hours), 20)


def station(callsign):
    ok, d = call("/stations/" + urllib.parse.quote(callsign, safe=""))
    if not ok:
        return None
    if d["station"]["kind"] in ("mobile", "balloon", "aircraft") and cfg("aprs_show_mobile") != "1":
        return None
    return d


def weather():
    def load():
        ok, d = call("/weather?minutes=" + str(max(30, cfg_int("aprs_window_hours") * 60)))
        return d.get("weather", []) if ok else []
    return cached("aprs_wx", load, 60)


def hidden():
    return get_db().execute("SELECT h.call, h.reason, h.added, u.username FROM aprs_hidden h LEFT JOIN users u ON u.id = h.added_by ORDER BY h.call").fetchall()
