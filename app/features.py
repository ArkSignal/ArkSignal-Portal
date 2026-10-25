"""The features an administrator can hide from some visitors (data only, so settings.py, permissions.py and access.py can all read it without importing each other).

Each feature: key, name shown to people, the Settings switches that turn it on (any of them), and the permission that always lets an administrator through to it. Settings > Site > Feature Access sets, for each
one, who can see it: everyone (the default), signed-in members only, or only members whose role holds the permission "See <name>" (see app/access.py)."""

FEATURES = [
    ("myflights", "My Flights", ("myflights_enabled",), "manage_settings"),
    ("exports", "Flight Data Downloads", ("export_enabled",), "manage_settings"),
    ("weather", "Weather Stations", ("wx_enabled",), "manage_weather"),
    ("sondes", "Radiosondes", ("sonde_enabled",), "manage_sondes"),
    ("repeaters", "Repeaters", ("repeater_enabled",), "moderate_repeaters"),
    ("aprs", "APRS", ("aprs_enabled",), "manage_map_data"),
    ("mesh", "Mesh Nodes", ("mesh_enabled",), "manage_map_data"),
    ("communities", "Mesh Communities", ("mesh_communities_enabled",), "manage_map_data"),
    ("acars", "ACARS", ("acars_enabled",), "manage_feeders"),
    ("ais", "River Traffic", ("ais_enabled",), "manage_feeders"),
    ("drones", "Drones", ("rid_enabled",), "manage_feeders"),
    ("satpass", "Satellite Passes", ("satpass_enabled", "satmap_enabled"), "manage_settings"),
    ("satimages", "Satellite Images", ("satimg_enabled",), "manage_satimages"),
    ("goes", "GOES Imagery", ("goes_enabled",), "manage_settings"),
    ("spaceweather", "Space Weather", ("spaceweather_enabled",), "manage_settings"),
    ("nets", "Nets and Winlink Gateways", ("radiolists_enabled",), "moderate_repeaters"),
    ("wxalerts", "Weather Alerts Layer", ("wxalerts_enabled",), "manage_map_data"),
    ("quakes", "Earthquakes Layer", ("quakes_enabled",), "manage_map_data"),
    ("rivers", "River Gauges Layer", ("rivers_enabled",), "manage_map_data"),
    ("api", "API", ("api_enabled",), "manage_api_keys"),
    ("kiosk", "Kiosk Display", ("kiosk_enabled",), "manage_settings"),
    ("achievements", "Feeder Achievements", ("achv_enabled",), "manage_feeders"),
    ("chat", "Chat", ("chat_enabled",), "moderate_chat"),
    ("messages", "Private Messages", ("pm_enabled",), "moderate_chat"),
    ("friends", "Friends Lists", ("friends_enabled",), "moderate_chat"),
    ("matrix", "Matrix Chat", ("matrix_enabled",), "moderate_chat"),
    ("bridges", "Radio Bridges", ("bridge_aprs_enabled", "bridge_mesh_enabled"), "moderate_chat"),
]

# features that only work for a signed-in person (a diary, chat, messages, friends): "members only" until an administrator chooses otherwise, so a guest is never offered a page that only asks for a sign-in
MEMBERS_ONLY = ("myflights", "chat", "messages", "friends", "matrix", "bridges")

KEYS = [f[0] for f in FEATURES]
LABEL = {f[0]: f[1] for f in FEATURES}
MANAGE = {f[0]: f[3] for f in FEATURES}
ENABLE_KEYS = {k: f[0] for f in FEATURES for k in f[2]}   # a Settings switch -> the feature it turns on
POLICIES = [("everyone", "Everyone, signed in or not"), ("members", "Signed-in members only"), ("roles", "Only members whose role has the See permission")]

# the map layers that belong to a feature (by layer slug): hidden from the layer list of anyone who may not see the feature
LAYER_FEATURE = {"ais": "ais", "drones": "drones", "satellites": "satpass", "quakes": "quakes", "rivers": "rivers", "wx-alerts": "wxalerts"}
