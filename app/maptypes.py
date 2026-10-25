"""The kinds of map. Every map is the same page (partials/_mapshell.html); the Map selector at the top of its panel switches between the types and keeps the view, and each type shows only its own
layers and filters. Add a new map type here and give its page `map_type="<key>"` in its shell() call."""

TYPES = [
    ("aircraft", "Aircraft", "/live"),
    ("weather", "Weather Stations", "/weather/map"),
    ("balloons", "Radiosondes", "/sondes/map"),
    ("counties", "Counties", "/counties/"),
    ("trauma", "Trauma System", "/trauma/"),
    ("coverage", "Coverage", "/coverage"),
    ("repeaters", "Repeaters", "/repeaters/map"),
    ("aprs", "APRS Stations", "/aprs/map"),
    ("mesh", "Mesh Nodes", "/mesh/map"),
    ("ais", "River Traffic", "/ais/map"),
    ("drones", "Drones", "/drones/map"),
    ("satellites", "Satellites", "/radio/satellites/map"),
]


# the map types that belong to a service that can be switched off or hidden (Feature Access): the selector offers only the maps the viewer may open
GATED = {"weather": "wx_enabled", "balloons": "sonde_enabled", "repeaters": "repeater_enabled", "aprs": "aprs_enabled", "mesh": "mesh_enabled", "ais": "ais_enabled", "drones": "rid_enabled", "satellites": "satpass_enabled"}


def visible_types():
    from .settings import cfg   # cfg answers "0" for a service that is off or hidden from this viewer
    return [t for t in TYPES if t[0] not in GATED or cfg(GATED[t[0]]) == "1"]


def section_tags():
    """{section key: [map types]} from Settings > Map Tiles and Weather > Map Sections By Map Type ("drive=counties,aircraft;display=*"). A section not named there shows on every map type."""
    from .settings import cfg
    out = {}
    for part in (cfg("map_section_tags") or "").split(";"):
        if "=" not in part:
            continue
        key, _, types = part.partition("=")
        key = key.strip()
        if key:
            out[key] = [t.strip() for t in types.split(",") if t.strip()]
    return out


def sections_off(map_type):
    """The section keys that are tagged for other map types only: the page hides them on this one."""
    known = {k for k, _l, _h in TYPES}
    off = sorted(k for k, types in section_tags().items() if "*" not in types and map_type not in types and (not types or all(t in known for t in types)))
    return [k for k in off if not (map_type == "trauma" and k == "drive")]   # the Trauma Map always has its drive times, even on a site that saved the tags before it existed
