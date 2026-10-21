"""The service registry: ONE place that says what each service adds to the shared pages. The home page (map layers and the Right Now card beside the map), the admin Overview (tiles,
things needing attention, data kept), the About page, the Feed menu, the roles and the status checks all read from here, so a new service is added once and shows up everywhere.
Adding a service (repeaters, mesh nodes, APRS...) means one new entry below plus the steps in NEW_SERVICE_CHECKLIST.md; tests/services_test.py fails when an enabled service is missing
from any of those pages.

Each entry is a dict. Every hook is optional and may raise (it is skipped, never breaking the page):
  key, label           short id and the name shown in the pages
  enabled()            whether the service is on (a Settings switch)
  permission           the role permission that administers it (permissions.py)
  status_key           the Service Status check that watches it (status.py)
  about                (address, label, text) for the About page list
  feed                 (label, address) for the Feed menu
  home_layers()        [{key, label, color, points: [{id, name, lat, lon, online, approx, href}]}] drawn on the home map, inside the home area
  home_now()           {key, title, summary (one line shown while the block is folded), href, rows: [(label, value, href or None)]} for the card beside the home map
  admin_tiles()        [(label, value, address, status check key or None)] for the admin Overview (the tile takes the color of that Service Status check)
  admin_attention()    [(level, text, address, label)] things an administrator should look at
  admin_data()         [(name, count, note, address, last refreshed text or "")] for "Data We Keep"
"""
from .db import get_db
from .settings import cfg, get_setting


def _on(key):
    return lambda: cfg(key) == "1"


def _inside(p):
    from . import arkansas
    return arkansas.inside(p["lat"], p["lon"])


# ------------------------------------------------------------------ weather stations

def _wx_layers():
    from . import weather
    pts = [{"id": p["id"], "name": p["name"] + (" " + p["temp"] + "°" if p["temp"] != "" else ""), "lat": p["lat"], "lon": p["lon"], "online": p["online"], "approx": p["approx"], "href": p["href"]}
           for p in weather.map_points() if _inside(p)]
    return [{"key": "wx", "label": "Weather Stations", "color": "#ffb454", "points": pts}] if pts else []


def _wx_now():
    from . import weather
    allp = [p for p in weather.map_points() if _inside(p)]
    live = [p for p in allp if p["online"]]
    rows = [("Stations reporting", f"{len(live)} of {len(allp)}", "/weather/")]
    t = [p for p in live if p.get("temp_f") is not None]
    if t:
        avg = weather.show("temp_f", sum(p["temp_f"] for p in t) / len(t))
        warm, cold = max(t, key=lambda p: p["temp_f"]), min(t, key=lambda p: p["temp_f"])
        rows += [("Average temperature", f"{avg[0]}{avg[1]}", None),
                 ("Warmest", f"{warm['id']} {warm['temp']}°", warm["href"]),
                 ("Coolest", f"{cold['id']} {cold['temp']}°", cold["href"])]
    w = [p for p in live if (p.get("r") or {}).get("wind_mph") is not None]
    if w:
        top = max(w, key=lambda p: p["r"]["wind_mph"])
        rows.append(("Windiest", f"{top['wind']} {top['wind_unit']} {top['wind_dir']}".strip(), top["href"]))
    summary = f"{len(live)} of {len(allp)} reporting" + (f" · average {avg[0]}{avg[1]}" if t else "")
    return {"key": "weather", "title": "Weather Right Now", "summary": summary, "href": "/weather/", "rows": rows}


def _wx_tiles():
    from . import weather
    c = weather.counts()
    return [("Weather stations", f"{c['online']} / {c['total']}", "/admin/weather", "weather")]


def _wx_attention():
    n = get_db().execute("SELECT COUNT(*) AS n FROM wx_stations WHERE last_error <> '' AND NOT disabled").fetchone()["n"]
    return [("warn", f"{n} weather station{'s' if n != 1 else ''} could not read the owner's account.", "/admin/weather", "Weather")] if n else []


# ------------------------------------------------------------------ radiosondes and balloons

def _sonde_layers():
    from . import sondes
    out = []
    st = [{"id": s["id"], "name": "Sonde receiving station " + s["id"], "lat": s["lat"], "lon": s["lon"], "online": True, "approx": s["approx"], "href": s["href"]} for s in sondes.map_stations() if _inside(s)]
    if st:
        out.append({"key": "sonde_rx", "label": "Sonde Receiving Stations", "color": "#c58bff", "points": st})
    sites = [{"id": s["id"], "name": "Balloon launch site " + s["name"], "lat": s["lat"], "lon": s["lon"], "online": True, "approx": False, "href": s["href"]} for s in sondes.map_sites() if _inside(s)]
    if sites:
        out.append({"key": "sonde_sites", "label": "Balloon Launch Sites", "color": "#8fb8ff", "points": sites})
    return out


def _sonde_now():
    from . import sondes
    card = sondes.home_card()
    c = sondes.counts()
    rows = [("In the air over Arkansas", str(card["aloft"]), "/sondes/map")]
    if card["highest"]:
        rows.append(("Highest", f"{round(card['highest'] / 0.3048):,} ft", None))
    rows += [("National Weather Service flights, 24 hours", str(card["nws_24h"]), "/sondes/flights"),
             ("Balloon flights recorded, 24 hours", str(c["flights"]["day"]), "/sondes/flights"),
             ("Receiving stations active, 24 hours", f"{c['stations']['active']} of {c['stations']['total']}", "/sondes/stations/")]
    summary = f"{card['aloft']} in the air" + (f" · highest {round(card['highest'] / 0.3048):,} ft" if card["highest"] else "") + f" · {card['nws_24h']} National Weather Service flights today"
    return {"key": "sondes", "title": "Radiosondes Right Now", "summary": summary, "href": "/sondes/", "rows": rows}


def _sonde_tiles():
    from . import sondes
    c = sondes.counts()
    rows = [("Balloons in the air", str(c["flights"]["aloft"]), "/admin/sondes", "sondes"),
            ("Sonde receiving stations active", f"{c['stations']['active']} / {c['stations']['total']}", "/admin/sondes", "sondes")]
    from . import sonde_predict as sp
    if sp.enabled():
        st = sp.stats()
        rows.append(("Archive flights in the prediction service", f"{st['data']['flights']:,}" if st["ok"] else "not answering", "/admin/sondes#sec-predict", "sondes"))
    return rows


def _sonde_attention():
    from . import sondes
    n = sondes.counts()["stations"]["pending"]
    out = [("info", f"{n} balloon receiving station claim{'s are' if n != 1 else ' is'} waiting for approval.", "/admin/sondes", "Radiosondes")] if n else []
    from . import sonde_predict as sp
    if sp.enabled() and not sp.stats()["ok"]:
        out.append(("warn", "The balloon prediction service is not answering, so forecasts are not shown. Check that the predict container is running.", "/admin/sondes#sec-predict", "Radiosondes"))
    return out


def _sonde_data():
    from . import sondes
    return [("Balloon launch sites", f"{len(sondes.sites()):,}", get_setting("sondesites_last_result") or "", "/admin/data-updates/", get_setting("sondesites_last_import") or "")]


# ------------------------------------------------------------------ repeaters

def _rep_layers():
    from . import repeaters
    pts = [{"id": p["id"], "name": ((p["call"] + " ") if p["call"] else "") + f"{p['out'] / 1e6:.4f}".rstrip("0").rstrip(".") + " MHz " + "/".join(p["modes"]), "lat": p["lat"], "lon": p["lon"], "online": p["status"] == "active", "approx": False, "href": "/repeaters/" + str(p["id"])}
           for p in repeaters.map_points() if _inside(p) and p["kind"] == "repeater"]
    return [{"key": "repeaters", "label": "Repeaters", "color": "#4fb3ff", "points": pts}] if pts else []


def _rep_now():
    from . import repeaters
    c = repeaters.counts()
    rows = [("Repeaters listed", str(c["total"]), "/repeaters/"), ("Active", str(c["active"]), "/repeaters/?status=active"), ("DMR", str(c["dmr"]), "/repeaters/?mode=DMR")]
    return {"key": "repeaters", "title": "Repeaters", "summary": f"{c['total']} listed · {c['active']} active", "href": "/repeaters/", "rows": rows}


def _rep_tiles():
    from . import repeaters
    c = repeaters.counts()
    return [("Repeaters listed", str(c["total"]), "/admin/repeaters", None)]


def _rep_attention():
    from . import repeaters
    c = repeaters.counts()
    out = []
    for n, what in ((c["reports"], "repeater report"), (c["pending"], "new repeater"), (c["claims"], "sysop claim")):
        if n:
            out.append(("info", f"{n} {what}{'s are' if n != 1 else ' is'} waiting for an editor.", "/admin/repeaters", "Repeaters"))
    return out


def _rep_data():
    from . import repeaters
    return [("Repeaters", f"{repeaters.counts()['total']:,}", get_setting("repeaters_last_result") or "", "/admin/data-updates/", get_setting("repeaters_last_import") or "")]


# ------------------------------------------------------------------ APRS

def _aprs_layers():
    from . import aprs
    pts = [{"id": r["call"], "name": r["call"] + " (" + aprs.KIND_LABELS.get(r["kind"], r["kind"]) + ")", "lat": r["lat"], "lon": r["lon"], "online": True, "approx": False, "href": "/aprs/" + r["call"]}
           for r in aprs.stations_cached("", "", 6) if r["kind"] in ("weather", "digipeater", "gateway", "igate", "repeater") and _inside(r)]
    return [{"key": "aprs", "label": "APRS Digipeaters and Weather", "color": "#3ee6a8", "points": pts}] if pts else []


def _aprs_now():
    from . import aprs
    st = aprs.stats()
    if not st["ok"]:
        return None
    d = st["data"]
    return {"key": "aprs", "title": "APRS", "summary": f"{d['heard_hour']} heard this hour · {d['weather_now']} weather", "href": "/aprs/",
            "rows": [("Stations heard, last hour", str(d["heard_hour"]), "/aprs/?hours=1"), ("Weather stations reporting", str(d["weather_now"]), "/aprs/weather"), ("Digipeaters and gateways", str(d["kinds"].get("digipeater", 0) + d["kinds"].get("gateway", 0) + d["kinds"].get("igate", 0)), "/aprs/?kind=digipeater")]}


def _aprs_tiles():
    from . import aprs
    st = aprs.stats()
    return [("APRS stations heard this hour", str(st["data"]["heard_hour"]) if st["ok"] else "not answering", "/admin/aprs", "aprs")]


def _aprs_attention():
    from . import aprs
    st = aprs.stats()
    if not st["ok"]:
        return [("warn", "The APRS service is not answering, so APRS stations and weather are not shown. Check that the aprs container is running.", "/admin/aprs", "APRS")]
    if not st["data"]["feed"]["connected"]:
        return [("warn", "The APRS service is not connected to APRS-IS" + (": " + st["data"]["feed"]["error"] if st["data"]["feed"]["error"] else "") + ".", "/admin/aprs", "APRS")]
    return []


# ------------------------------------------------------------------ the county map

def _county_data():
    from . import counties
    return [("County outlines", f"{len(counties.shapes()):,}", "", "/admin/data-updates/", get_setting("counties_last_import") or "")]


def _mesh_now():
    from . import mesh
    st = mesh.stats()
    if not st["ok"]:
        return None
    d = st["data"]
    return {"key": "mesh", "title": "Mesh", "summary": f"{d['heard_hour']} nodes this hour", "href": "/mesh/",
            "rows": [("Nodes heard, last hour", str(d["heard_hour"]), "/mesh/?hours=1"), ("Nodes known", str(d["nodes"]), "/mesh/"), ("Routers and repeaters", str(d["routers"]), "/mesh/?role=2")]}


def _mesh_tiles():
    from . import mesh
    st = mesh.stats()
    return [("Mesh nodes heard this hour", str(st["data"]["heard_hour"]) if st["ok"] else "not answering", "/admin/mesh", "mesh")]


def _mesh_attention():
    from . import mesh
    st = mesh.stats()
    if not st["ok"]:
        return [("warn", "The mesh service is not answering, so mesh nodes are not shown. Check that the mesh container is running.", "/admin/mesh", "Mesh")]
    if not st["data"]["feed"]["connected"]:
        return [("warn", "The mesh service is not connected to its MQTT broker" + (": " + st["data"]["feed"]["error"] if st["data"]["feed"]["error"] else "") + ".", "/admin/mesh", "Mesh")]
    return []


def _mesh_layers():
    from . import mesh
    pts = [{"id": r["id"], "name": (r.get("long_name") or r.get("short_name") or r["id"]) + " (" + (r.get("role_name") or "node") + ")", "lat": r["lat"], "lon": r["lon"], "online": True, "approx": bool(r.get("precision_bits") and r["precision_bits"] < 24), "href": "/mesh/" + r["id"][1:]}
           for r in mesh.nodes_cached("", "", 24, True) if r.get("lat") is not None and _inside(r)]
    return [{"key": "mesh", "label": "Mesh Nodes", "color": "#ff8c42", "points": pts}] if pts else []


def _acars_now():
    from . import acars
    st = acars.stats()
    if not st["ok"]:
        return None
    d = st["data"]
    return {"key": "acars", "title": "ACARS", "summary": f"{d['hour']} messages this hour", "href": "/acars/",
            "rows": [("Messages, last hour", str(d["hour"]), "/acars/?hours=1"), ("Messages, last 24 hours", str(d["day"]), "/acars/"), ("Stations", str(len(d["stations"])), "/acars/stations/")]}


def _acars_tiles():
    from . import acars
    st = acars.stats()
    return [("ACARS messages, last 24 hours", str(st["data"]["day"]) if st["ok"] else "not answering", "/admin/acars", "acars")]


def _acars_attention():
    from . import acars
    st = acars.stats()
    if not st["ok"]:
        return [("warn", "The ACARS service is not answering, so ACARS messages are not shown or kept. Check that the acars container is running.", "/admin/acars", "ACARS")]
    return []


# ------------------------------------------------------------------ satellites (passes, GOES pictures, members' receivers) and nets

def _sat_now():
    from . import satimages
    c = satimages.counts()
    return {"key": "satellites", "title": "Satellite Pictures", "summary": f"{c['pictures']} pictures · {c['active']} receiver{'s' if c['active'] != 1 else ''} sending", "href": "/radio/satellite-images",
            "rows": [("Pictures from member receivers", str(c["pictures"]), "/radio/satellite-images"), ("Received in the last 24 hours", str(c["day"]), "/radio/satellite-images"),
                     ("Receivers sending", f"{c['active']} of {c['receivers']}", "/feed/satellites")]}


def _sat_tiles():
    from . import satimages
    c = satimages.counts()
    return [("Satellite receivers sending", f"{c['active']} / {c['receivers']}", "/admin/sat-images", "satellites"), ("Satellite pictures kept", f"{c['pictures']:,}", "/admin/sat-images", None)]


def _sat_attention():
    from . import satimages
    c = satimages.counts()
    return [("info", f"{c['silent']} satellite receiver{'s have' if c['silent'] != 1 else ' has'} not sent a picture yet.", "/admin/sat-images", "Satellites")] if c["silent"] else []


def _sat_data():
    from . import satimages
    c = satimages.counts()
    return [("Satellite pictures from members", f"{c['pictures']:,}", f"{c['bytes'] / 1048576:,.0f} MB kept in the database", "/admin/sat-images", "")]


def _nets_tiles():
    n = get_db().execute("SELECT COUNT(*) AS n FROM radio_entries WHERE status='approved'").fetchone()["n"]
    return [("Nets and Winlink gateways listed", f"{n:,}", "/admin/nets", None)]


def _nets_attention():
    n = get_db().execute("SELECT COUNT(*) AS n FROM radio_entries WHERE status='pending'").fetchone()["n"]
    return [("info", f"{n} net or Winlink gateway {'entries are' if n != 1 else 'entry is'} waiting for an editor.", "/admin/nets?status=pending", "Nets")] if n else []


# ------------------------------------------------------------------ river traffic (AIS)

def _ais_layers():
    from . import ais
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    pts = [{"id": str(v["mmsi"]), "name": (v["name"] or "MMSI " + str(v["mmsi"])) + " (" + v["type_name"] + ")", "lat": v["lat"], "lon": v["lon"], "online": True, "approx": False, "href": "/ais/" + str(v["mmsi"])}
           for v in ais._cached_vessels() if v["lat"] is not None and (now - datetime.fromisoformat(v["last_seen"])).total_seconds() < ais.cfg_int("ais_underway_minutes") * 360 and _inside(v)]
    return [{"key": "ais", "label": "River Traffic", "color": "#4fb3ff", "points": pts}] if pts else []


def _ais_now():
    from . import ais
    c = ais.counts()
    return {"key": "ais", "title": "River Traffic", "summary": f"{c['now']} vessel{'s' if c['now'] != 1 else ''} heard lately · {c['active']} receiver{'s' if c['active'] != 1 else ''} reporting", "href": "/ais/",
            "rows": [("Vessels heard lately", str(c["now"]), "/ais/map"), ("Heard in the last 24 hours", str(c["day"]), "/ais/"), ("Receivers reporting", f"{c['active']} of {c['stations']}", "/feed/ais")]}


def _ais_tiles():
    from . import ais
    c = ais.counts()
    return [("AIS vessels heard lately", str(c["now"]), "/admin/ais", "ais"), ("AIS receivers reporting", f"{c['active']} / {c['stations']}", "/admin/ais", None)]


def _ais_attention():
    from . import ais
    c = ais.counts()
    return [("info", f"{c['silent']} AIS station{'s have' if c['silent'] != 1 else ' has'} not reported yet.", "/admin/ais", "River Traffic")] if c["silent"] else []


def _ais_data():
    from . import ais
    c = ais.counts()
    n = get_db().execute("SELECT COUNT(*) AS n FROM ais_positions").fetchone()["n"]
    return [("AIS vessels known", f"{c['total']:,}", f"{n:,} trail positions kept", "/admin/ais", "")]


# ------------------------------------------------------------------ drones (Remote ID)

def _rid_layers():
    from . import drones
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    pts = [{"id": v["id"], "name": "Drone " + v["id"] + " (" + v["ua_name"] + ")", "lat": v["lat"], "lon": v["lon"], "online": v["kind"] == "air", "approx": False, "href": "/drones/" + v["id"]}
           for v in drones._cached_drones() if v["lat"] is not None and (now - datetime.fromisoformat(v["last_seen"])).total_seconds() < drones.cfg_int("rid_current_minutes") * 360 and _inside(v)]
    return [{"key": "drones", "label": "Drones", "color": "#ffb454", "points": pts}] if pts else []


def _rid_now():
    from . import drones
    c = drones.counts()
    return {"key": "drones", "title": "Drones", "summary": f"{c['now']} drone{'s' if c['now'] != 1 else ''} heard lately · {c['active']} receiver{'s' if c['active'] != 1 else ''} reporting", "href": "/drones/",
            "rows": [("Drones heard lately", str(c["now"]), "/drones/map"), ("Heard in the last 24 hours", str(c["day"]), "/drones/"), ("Receivers reporting", f"{c['active']} of {c['stations']}", "/feed/drones")]}


def _rid_tiles():
    from . import drones
    c = drones.counts()
    return [("Drones heard lately", str(c["now"]), "/admin/drones", "drones"), ("Remote ID receivers reporting", f"{c['active']} / {c['stations']}", "/admin/drones", None)]


def _rid_attention():
    from . import drones
    c = drones.counts()
    return [("info", f"{c['silent']} Remote ID station{'s have' if c['silent'] != 1 else ' has'} not reported yet.", "/admin/drones", "Drones")] if c["silent"] else []


def _rid_data():
    from . import drones
    c = drones.counts()
    n = get_db().execute("SELECT COUNT(*) AS n FROM rid_positions").fetchone()["n"]
    return [("Drones known", f"{c['total']:,}", f"{n:,} trail positions kept", "/admin/drones", "")]


# ------------------------------------------------------------------ chat and private messages

def _chat_tiles():
    from . import chat
    c = chat.counts()
    return [("Chat messages, last 24 hours", str(c["chat_day"]), "/admin/chat", None), ("Private messages sent, last 24 hours", str(c["pm_day"]), "/admin/chat", None), ("Friendships", str(c["friends"]), "/admin/chat", None)] + _bridge_tiles()


def _bridge_tiles():
    from . import bridge
    if cfg("bridge_aprs_enabled") != "1" and cfg("bridge_mesh_enabled") != "1":
        return []
    c = bridge.counts()
    return [("Radio bridge links on", str(c["links"]), "/admin/chat", None), ("Bridge messages, last 24 hours", str(c["day"]), "/admin/chat", None), ("Messages waiting for the mesh gateway", str(c["waiting"]), "/admin/chat", None)]


def _chat_attention():
    from . import chat
    n = chat.counts()["reports"]
    return [("warn", f"{n} chat or private message report{'s are' if n != 1 else ' is'} waiting for a moderator.", "/admin/chat", "Chat")] if n else []


SERVICES = [
    {"key": "weather", "label": "Weather Stations", "enabled": _on("wx_enabled"), "permission": "manage_weather", "status_key": "weather",
     "about": ("/weather/", "Weather Stations", "members' own stations (Tempest, Ecowitt, Ambient, Davis, Home Assistant and more) with live conditions, charts and records, and a weather map."),
     "feed": ("Weather Stations", "/feed/weather"),
     "home_layers": _wx_layers, "home_now": _wx_now, "admin_tiles": _wx_tiles, "admin_attention": _wx_attention},
    {"key": "sondes", "label": "Radiosondes", "enabled": _on("sonde_enabled"), "permission": "manage_sondes", "status_key": "sondes",
     "about": ("/sondes/", "Radiosondes and Balloons", "the National Weather Service balloons (and amateur balloons) over Arkansas and its neighbors, tracked live from launch to landing, with charts, launch sites and the stations that receive them."),
     "feed": ("Radiosondes", "/feed/sondes"),
     "home_layers": _sonde_layers, "home_now": _sonde_now, "admin_tiles": _sonde_tiles, "admin_attention": _sonde_attention, "admin_data": _sonde_data},
    {"key": "repeaters", "label": "Repeaters", "enabled": _on("repeater_enabled"), "permission": "moderate_repeaters", "status_key": None,
     "about": ("/repeaters/", "Repeaters", "amateur radio repeaters, linked nodes and gateways in every mode (FM, DMR, D-STAR, Fusion, P25) from Hearham's open data and Arkansas hams, with a map, reports and sysop claims."),
     "home_layers": _rep_layers, "home_now": _rep_now, "admin_tiles": _rep_tiles, "admin_attention": _rep_attention, "admin_data": _rep_data},
    {"key": "mesh", "label": "Mesh Nodes", "enabled": _on("mesh_enabled"), "permission": "manage_map_data", "status_key": "mesh",
     "about": ("/mesh/", "Mesh Nodes", "Meshtastic nodes around Arkansas that publish themselves, on a list, a map and a page for each, with who hears whom."),
     "feed": ("Mesh Nodes", "/feed/mesh"),
     "home_layers": _mesh_layers, "home_now": _mesh_now, "admin_tiles": _mesh_tiles, "admin_attention": _mesh_attention},
    {"key": "acars", "label": "ACARS", "enabled": _on("acars_enabled"), "permission": "manage_feeders", "status_key": "acars",
     "about": ("/acars/", "ACARS", "the short text messages between airliners and the ground (ACARS, VDL Mode 2, HF datalink) that members' receivers hear, with the latest ones on an aircraft's card and a station page for each receiver."),
     "feed": ("ACARS", "/feed/acars"),
     "home_now": _acars_now, "admin_tiles": _acars_tiles, "admin_attention": _acars_attention},
    {"key": "aprs", "label": "APRS", "enabled": _on("aprs_enabled"), "permission": "manage_map_data", "status_key": "aprs",
     "about": ("/aprs/", "APRS", "the amateur radio packet network read around Arkansas: digipeaters and gateways, weather stations (many in the Citizen Weather Observer Program) and, optionally, moving stations, on a list, a map and station pages."),
     "home_layers": _aprs_layers, "home_now": _aprs_now, "admin_tiles": _aprs_tiles, "admin_attention": _aprs_attention},
    {"key": "ais", "label": "River Traffic", "enabled": _on("ais_enabled"), "permission": "manage_feeders", "status_key": "ais",
     "about": ("/ais/", "River Traffic", "the towboats, barges and boats on the Arkansas, White and Mississippi rivers and the lakes, heard by members' own AIS receivers, on a list, a map and a page for each vessel."),
     "feed": ("AIS", "/feed/ais"),
     "home_layers": _ais_layers, "home_now": _ais_now, "admin_tiles": _ais_tiles, "admin_attention": _ais_attention, "admin_data": _ais_data},
    {"key": "drones", "label": "Drones", "enabled": _on("rid_enabled"), "permission": "manage_feeders", "status_key": "drones",
     "about": ("/drones/", "Drones", "the drones near members' receivers that broadcast Remote ID, on a list, a map and a page for each drone (the operator's own location is never shown unless an administrator turns that on)."),
     "feed": ("Drone Remote ID", "/feed/drones"),
     "home_layers": _rid_layers, "home_now": _rid_now, "admin_tiles": _rid_tiles, "admin_attention": _rid_attention, "admin_data": _rid_data},
    {"key": "satellites", "label": "Satellites", "enabled": _on("satimg_enabled"), "permission": "manage_satimages", "status_key": "satellites",
     "about": ("/radio/satellite-images", "Satellites", "weather satellite pictures decoded by members' own receivers (Meteor-M, GOES), NOAA's newest GOES pictures, and when the space station and amateur radio satellites pass over, with their frequencies."),
     "feed": ("Satellite Receivers", "/feed/satellites"),
     "home_now": _sat_now, "admin_tiles": _sat_tiles, "admin_attention": _sat_attention, "admin_data": _sat_data},
    {"key": "chat", "label": "Chat and Messages", "enabled": lambda: cfg("chat_enabled") == "1" or cfg("pm_enabled") == "1" or cfg("friends_enabled") == "1", "permission": "moderate_chat", "status_key": None,
     "about": ("/chat/", "Chat, Messages and Friends", "live chats between two or more members, email-style private messages and friends lists, with moderators, mutes and reports."),
     "admin_tiles": _chat_tiles, "admin_attention": _chat_attention},
    {"key": "nets", "label": "Nets and Winlink Gateways", "enabled": _on("radiolists_enabled"), "permission": "moderate_repeaters", "status_key": None,
     "about": ("/radio/nets", "Nets and Winlink Gateways", "amateur radio nets with the time of the next one in your own time zone, and Winlink gateways with how far each is from you, kept by the network's members."),
     "admin_tiles": _nets_tiles, "admin_attention": _nets_attention},
    {"key": "counties", "label": "County Map", "enabled": lambda: True, "permission": "manage_map_data", "status_key": None,
     "about": ("/counties/", "County Map", "every Arkansas county shaded by what is in it, with a page for each county."),
     "admin_data": _county_data},
    {"key": "trauma", "label": "Trauma Map", "enabled": lambda: True, "permission": "manage_map_data", "status_key": None,
     "about": ("/trauma/", "Trauma Map", "Arkansas' trauma regions, zones and hospitals in one simple map, with each county and hospital labeled the way the Department of Health does.")},
]


def enabled():
    out = []
    for s in SERVICES:
        try:
            if s["enabled"]():
                out.append(s)
        except Exception:
            get_db().rollback()
    return out


def collect(hook):
    """Everything the enabled services' `hook` returns, in order; a hook that fails is skipped."""
    out = []
    for s in enabled():
        fn = s.get(hook)
        if not fn:
            continue
        try:
            r = fn()
        except Exception:
            get_db().rollback()
            continue
        if r:
            out.extend(r) if isinstance(r, list) else out.append(r)
    return out
