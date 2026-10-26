"""The admin side is organized by service: each group in the admin menu holds a service's pages and its Settings page, which shows only that service's setting sections.
`/admin/settings` still lists every section (grouped in tabs by service), and the search box on every admin page finds a setting anywhere and opens it in its service's Settings page.
Add a new settings section to the service it belongs to here; one that is not listed lands under Site."""

# key, label, the settings sections of the service (names as on the Settings page)
SERVICES = [
    ("aircraft", "Aircraft and Feeders", ["ACARS", "Feeders", "Feeders and Claiming", "Achievements", "Kiosk Display", "Feeder Filter", "Aircraft Lookups", "Flight History", "History", "Range Statistics", "Alerts", "Interesting Aircraft", "Air Ambulance", "Drones"]),
    ("weather", "Weather and Balloons", ["Weather Stations", "Radiosondes", "Prediction Service"]),
    ("radio", "Radio", ["Repeaters", "APRS", "Mesh Nodes", "Space Weather", "Satellites", "Nets and Gateways", "River Traffic"]),
    ("mapdata", "Map and Data", ["Map Defaults", "Map Areas", "Home Page and Live Map", "Map Tiles and Weather", "Hospitals", "Places and Search", "Address Finders", "Counties", "Map Data", "Routing"]),
    ("updates", "Updates", ["Scheduled Updates", "Backups"]),
    ("site", "General", ["General", "Display", "Units", "Themes", "Public Pages", "Contacts", "Accounts", "Security", "Service Status", "Data Retention", "Shopping Links", "My Flights", "API"]),
]
LABELS = {k: label for k, label, _s in SERVICES}
_OF = {name: key for key, _l, names in SERVICES for name in names}


def service_of(section_name):
    return _OF.get(section_name, "site")


def slug(name):
    return name.lower().replace(" ", "-")


def settings_href(service, section_slug="", field=""):
    h = "/admin/settings/" + service if service else "/admin/settings"
    if field:
        h += "?field=" + field
    return h + ("#sec-" + section_slug if section_slug else "")
