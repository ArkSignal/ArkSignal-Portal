"""The top menu, grouped by service. RULE: a service group opens its overview first (the group's own link goes there) and its map is the second entry (Aircraft Tracking / Live Map, Overview / Weather Map, Overview / Radiosonde Map); Places opens the County Map. Each group is a flyout; the group you are in is highlighted. A new service (weather, repeaters, mesh...) adds one group here
and it appears in the menu only when that service exists, so the bar never fills with links to things that are not built yet."""
from flask import request
from flask_login import current_user

from .settings import cfg

NETWORK_TOOLS = ()   # the aircraft and feeder statistics dashboards sit in the Aircraft menu with the rest of the aircraft (every dashboard is in the menu of what it describes)
WEATHER_TOOLS = ("weather-stats",)                  # the weather dashboard sits in the Weather menu
SONDE_TOOLS = ("balloon-stats",)                    # and the balloon dashboard in the Radiosondes menu


DASH = {"weather-stats": "Weather Dashboard", "balloon-stats": "Radiosonde Dashboard"}   # the embedded dashboards (Grafana); the Statistics pages beside them have the filters


def groups(tools):
    """[{key, label, home, items: [(label, href)], active}] for the current request."""
    mlat = cfg("mlat_enabled") == "1"
    uat = cfg("uat_enabled") == "1"
    H = lambda t: (t, None)   # a heading inside a flyout (no address): long flyouts are cut into labelled parts
    tool_items = [(t["name"], "/view/" + t["slug"]) for t in tools if t["slug"] not in NETWORK_TOOLS + WEATHER_TOOLS + SONDE_TOOLS]
    feeder_tools = [x for x in tool_items if x[1] == "/view/network-feeders"]
    view_tools = [x for x in tool_items if x not in feeder_tools]
    air = [H("Tracking"), ("Aircraft Tracking", "/aircraft"), ("Live Map", "/live"), ("Flights", "/flight/"), ("Flight History", "/history/"), H("Views")] + view_tools
    air += [H("Feeders and Network"), ("ADS-B Feeders", "/feeders/")] + feeder_tools   # the public list of aircraft feeders belongs with the aircraft (the Network menu is the coverage and the status of the whole network)
    if mlat:
        air.append(("MLAT Network", "/mlat/"))
    if cfg("acars_enabled") == "1":
        air += [H("Datalink"), ("ACARS", "/acars/"), ("My ACARS Stations", "/my/acars")]
    if cfg("rid_enabled") == "1":
        air += [H("Drones"), ("Drones", "/drones/"), ("Drone Map", "/drones/map"), ("My Remote ID Stations", "/my/drones")]
    air += [H("Mine"), ("My Feeders", "/my/")]   # the owner's own list of ADS-B feeders; the Weather group has the matching My Weather Stations
    places = [("County Map", "/counties/"), ("Trauma Map", "/trauma/"), ("Airports", "/airports/"), ("Hospitals", "/hospitals/")]   # facilities and counties on the map; later repeaters, mesh nodes
    net = [("Coverage Map", "/coverage")]
    net += [(t["name"], "/view/" + t["slug"]) for t in tools if t["slug"] in NETWORK_TOOLS]
    net.append(("Service Status", "/status"))
    if cfg("api_enabled") == "1":
        net.append(("API", "/api/docs"))
    feed = [("How to Feed", "/feed/")]
    if mlat:
        feed.append(("MLAT", "/feed/mlat"))
    if uat:
        feed.append(("UAT (978 MHz)", "/feed/uat"))
    from . import services
    feed += [x["feed"] for x in services.enabled() if x.get("feed")]   # one Feed entry per service, from app/services.py
    feed.append(("Am I Feeding?", "/feed/check"))
    radio = [("Overview", "/radio/")]
    if cfg("repeater_enabled") == "1":
        radio += [H("Repeaters"), ("Repeater List", "/repeaters/"), ("Repeater Map", "/repeaters/map")]
    if cfg("aprs_enabled") == "1":
        radio += [H("APRS"), ("APRS Stations", "/aprs/"), ("APRS Map", "/aprs/map"), ("APRS Weather", "/aprs/weather")]
    sat = []
    if cfg("satpass_enabled") == "1":
        sat.append(("Satellite Passes", "/radio/satellites"))
    if cfg("satimg_enabled") == "1":
        sat.append(("Satellite Images", "/radio/satellite-images"))
    if sat:
        radio += [H("Satellites")] + sat
    ham = []
    if cfg("radiolists_enabled") == "1":
        ham += [("Nets", "/radio/nets"), ("Winlink Gateways", "/radio/winlink")]
    if ham:
        radio += [H("Nets and Gateways")] + ham
    ref = [("Frequencies", "/frequencies")]
    if cfg("spaceweather_enabled") == "1":
        ref.append(("Space Weather", "/radio/space-weather"))
    if cfg("callsign_lookup_enabled") == "1":
        ref.append(("Call Signs and Talkgroups", "/callsign"))
    radio += [H("Reference")] + ref
    mine = []
    if cfg("repeater_enabled") == "1":
        mine.append(("My Repeaters", "/my/repeaters"))
    if cfg("satimg_enabled") == "1":
        mine.append(("My Satellite Receivers", "/my/sat-receivers"))
    if cfg("radiolists_enabled") == "1":
        mine.append(("My Nets and Gateways", "/my/nets"))
    if mine:
        radio += [H("Mine")] + mine
    out = [{"key": "aircraft", "label": "Aircraft", "home": "/aircraft", "items": air},
           {"key": "places", "label": "Places", "home": "/counties/", "items": places},
           *([{"key": "weather", "label": "Weather", "home": "/weather/", "items": [("Overview", "/weather/"), ("Weather Map", "/weather/map"), ("Weather for a Place", "/weather/place")] + ([("GOES Imagery", "/weather/goes")] if cfg("goes_enabled") == "1" else []) + [("Weather Statistics", "/weather/statistics")] + [(DASH.get(t["slug"], t["name"]), "/view/" + t["slug"]) for t in tools if t["slug"] in WEATHER_TOOLS] + [("My Weather Stations", "/my/weather")]}] if cfg("wx_enabled") == "1" else []),
           *([{"key": "sondes", "label": "Radiosondes", "home": "/sondes/", "items": [("Overview", "/sondes/"), ("Radiosonde Map", "/sondes/map"), ("Flights", "/sondes/flights"), ("Sonde Receiving Stations", "/sondes/stations/"), ("Radiosonde Statistics", "/sondes/statistics")] + [(DASH.get(t["slug"], t["name"]), "/view/" + t["slug"]) for t in tools if t["slug"] in SONDE_TOOLS] + [("My Sonde Stations", "/my/sondes")]}] if cfg("sonde_enabled") == "1" else []),
           *([{"key": "radio", "label": "Radio", "home": "/radio/", "items": radio}]),
           *([{"key": "ais", "label": "River Traffic", "home": "/ais/", "items": [("Overview", "/ais/"), ("River Traffic Map", "/ais/map"), ("My AIS Stations", "/my/ais")]}] if cfg("ais_enabled") == "1" else []),
           *([{"key": "mesh", "label": "Mesh", "home": "/mesh/", "items": [("Overview", "/mesh/"), ("Mesh Map", "/mesh/map"), ("Mesh Nodes", "/mesh/nodes"), ("Mesh Health", "/mesh/health"), ("How to Join", "/feed/mesh"), ("My Mesh Nodes", "/my/mesh")]}] if cfg("mesh_enabled") == "1" else []),
           *([{"key": "community", "label": "Community", "home": "/chat/" if cfg("chat_enabled") == "1" else ("/my/messages" if cfg("pm_enabled") == "1" else "/my/friends"),
               "items": ([("Chat", "/chat/")] if cfg("chat_enabled") == "1" else []) + ([("Messages", "/my/messages")] if cfg("pm_enabled") == "1" else []) + ([("Friends", "/my/friends")] if cfg("friends_enabled") == "1" else [])}]
             if current_user.is_authenticated and (cfg("chat_enabled") == "1" or cfg("pm_enabled") == "1" or cfg("friends_enabled") == "1") else []),
           {"key": "network", "label": "Network", "home": "/coverage", "items": net},
           {"key": "feed", "label": "Feed", "home": "/feed/", "items": feed}]
    path = request.path
    for g in out:
        g["active"] = any(path == href.rstrip("/") or path == href or (href not in ("/", "/aircraft", "/my/") and path.startswith(href.rstrip("/") + "/")) for _l, href in g["items"] if href)
    if not any(g["active"] for g in out) and (path == "/aircraft" or path.startswith(("/flight", "/skyview", "/cockpit", "/my/feeders", "/my/claim"))):
        out[0]["active"] = True
    return out
