"""The admin menu: one definition, grouped by purpose, in a fixed order. Every admin page names itself with `active="key"`; a page that is not listed here fails
tests/adminnav_test.py, so a new page has to be given a group (and a place in it) before it can ship, and the menu cannot drift into a random order.

Each entry: (key, label, address, permission needed). A group with nothing the signed-in person may open is left out."""

GROUPS = [
    ("Start", [
        ("overview", "Overview", "/admin/", None),
    ]),
    ("People", [
        ("users", "Users", "/admin/users", "manage_users"),
        ("roles", "Roles", "/admin/roles", "manage_roles"),
        ("signins", "Sign-Ins", "/admin/signins", "view_history"),
        ("audit", "Audit Log", "/admin/audit", "view_history"),
        ("chat", "Chat and Messages", "/admin/chat", "moderate_chat"),
        ("matrix", "Matrix Chat", "/admin/matrix", "moderate_chat"),
        ("bridges", "Radio Bridges", "/admin/bridges", "moderate_chat"),
    ]),
    ("Aircraft and Feeders", [
        ("feeders", "Feeders", "/admin/feeders", "manage_feeders"),
        ("mlat", "MLAT", "/admin/mlat", "manage_feeders"),
        ("acars", "ACARS", "/admin/acars", "manage_feeders"),
        ("drones", "Drones", "/admin/drones", "manage_feeders"),
        ("planefence", "Planefence", "/admin/planefence/", "manage_settings"),
        ("hems", "Air Ambulance", "/admin/air-ambulance", "manage_map_data"),
        ("interesting", "Interesting Aircraft", "/admin/interesting", "manage_map_data"),
        ("set_aircraft", "Aircraft Settings", "/admin/settings/aircraft", "manage_settings"),
    ]),
    ("Weather and Balloons", [
        ("weather", "Weather Stations", "/admin/weather", "manage_weather"),
        ("sondes", "Radiosondes", "/admin/sondes", "manage_sondes"),
        ("set_weather", "Weather Settings", "/admin/settings/weather", "manage_settings"),
    ]),
    ("Radio", [
        ("repeaters", "Repeaters", "/admin/repeaters", "moderate_repeaters"),
        ("aprs", "APRS", "/admin/aprs", "manage_map_data"),
        ("ais", "River Traffic", "/admin/ais", "manage_feeders"),
        ("mesh", "Mesh Nodes", "/admin/mesh", "manage_map_data"),
        ("communities", "Mesh Communities", "/admin/communities", "manage_map_data"),
        ("nets", "Nets", "/admin/nets", "moderate_repeaters"),
        ("winlink", "Winlink Gateways", "/admin/winlink", "moderate_repeaters"),
        ("satimages", "Satellite Images", "/admin/sat-images", "manage_satimages"),
        ("set_radio", "Radio Settings", "/admin/settings/radio", "manage_settings"),
    ]),
    ("Map and Data", [
        ("layers", "Map Layers", "/admin/layers", "manage_map_data"),
        ("airports", "Airports", "/admin/airports", "manage_map_data"),
        ("places", "Places", "/admin/places", "manage_map_data"),
        ("hospitals", "Hospitals", "/admin/hospitals", "manage_map_data"),
        ("counties", "Counties", "/admin/counties", "manage_map_data"),
        ("set_mapdata", "Map Settings", "/admin/settings/mapdata", "manage_settings"),
    ]),
    ("Updates", [
        ("dataupdates", "Data Updates", "/admin/data-updates/", "manage_map_data"),
        ("updates", "Software Updates", "/admin/updates", "manage_settings"),
        ("backups", "Backups", "/admin/backups", "manage_settings"),
        ("set_updates", "Update Settings", "/admin/settings/updates", "manage_settings"),
    ]),
    ("Site", [
        ("settings", "General Settings", "/admin/settings/site", "manage_settings"),
        ("allsettings", "All Settings (Everything)", "/admin/settings", "manage_settings"),
        ("google", "Google Sign-In", "/admin/google", "manage_settings"),
        ("apikeys", "API Keys", "/admin/api-keys", "manage_api_keys"),
        ("themes", "Themes", "/admin/themes", "manage_settings"),
        ("tools", "Tools", "/admin/tools", "manage_settings"),
        ("contacts", "Contacts", "/admin/contacts", "manage_settings"),
        ("support", "Support", "/admin/support", "manage_settings"),
        ("smtp", "SMTP", "/admin/smtp", "manage_settings"),
        ("status", "Status", "/admin/status", "manage_settings"),
        ("usage", "Usage", "/admin/usage", "manage_settings"),
        ("retention", "Data Retention", "/admin/retention", "manage_settings"),
        ("roadmap", "Full Roadmap", "/admin/roadmap", "manage_settings"),
    ]),
]


def keys():
    return [k for _g, items in GROUPS for k, _l, _h, _p in items]


def visible(user):
    """[(group label, [(key, label, address)])] for what this person may open, in the fixed order."""
    out = []
    for label, items in GROUPS:
        shown = [(k, l, h) for k, l, h, perm in items if perm is None or user.has_permission(perm)]
        if shown:
            out.append((label, shown))
    return out


def can_open(user, link):
    """Whether this person may open an admin address (a link on the Overview, say). Addresses outside the admin area are open to anyone."""
    path = link.split("?")[0].rstrip("/") + "/"
    if not path.startswith("/admin/"):
        return True
    best = None
    for _g, items in GROUPS:
        for _k, _l, href, perm in items:
            h = href.rstrip("/") + "/"
            if path.startswith(h) and (best is None or len(h) > len(best[0])):
                best = (h, perm)
    return best is None or best[1] is None or user.has_permission(best[1])
