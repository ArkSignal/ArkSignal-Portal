"""Extra map layers for the live map (weather radar, aeronautical charts, airspace ...). The catalog is edited in
Admin > Map Layers; nothing about a layer is fixed in code except the kinds the map knows how to draw."""
import re
from urllib.parse import urlparse

from .db import get_db

KINDS = [("tile", "Map Tiles"), ("wms", "WMS Tiles"), ("geojson", "Shapes (GeoJSON)"), ("rainviewer", "RainViewer Radar")]
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")

# The same sources tar1090 uses (its layers.js), so the layers match the classic map.
# name, group, kind, url, options, attribution, opacity, min zoom, max zoom, color, order, on by default, base map, refresh minutes
# Shape options: "label=PROP,PROP" writes those properties on the shape; "colorby=PROP:A|B=#hex;C=#hex" colors by a property.
DEFAULTS = [
    ("osm-de", "OpenStreetMap DE", "Worldwide", "tile", "https://tile.openstreetmap.de/{z}/{x}/{y}.png", "", "&copy; OpenStreetMap contributors", 1, 0, 19, "", 10, False, True, 0),
    ("esri-satellite", "ESRI.com Sat.", "Worldwide", "tile", "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "", "Powered by Esri.com", 1, 0, 19, "", 20, False, True, 0),
    ("esri-gray", "ESRI.com Gray", "Worldwide", "tile", "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", "", "Powered by Esri.com", 1, 0, 16, "", 30, False, True, 0),
    ("esri-streets", "ESRI.com Streets", "Worldwide", "tile", "https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", "", "Powered by Esri.com", 1, 0, 19, "", 40, False, True, 0),
    ("gibs-relief", "GIBS Relief", "Worldwide", "tile", "https://gibs-{s}.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/EPSG3857_500m/{z}/{y}/{x}.jpeg", "", "NASA GIBS", 1, 0, 8, "", 50, False, True, 0),
    ("gibs-clouds", "GIBS Clouds (Yesterday)", "Worldwide", "tile", "https://gibs-{s}.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/{date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg", "", "MODIS Terra, NASA GIBS", 1, 0, 9, "", 60, False, True, 0),
    ("vfr-sectional", "VFR Sectional Chart", "US", "tile", "https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/VFR_Sectional/MapServer/tile/{z}/{y}/{x}", "", "Tiles courtesy of arcgis.com (FAA)", 1, 0, 12, "", 100, False, True, 0),
    ("vfr-terminal", "VFR Terminal Chart", "US", "tile", "https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/VFR_Terminal/MapServer/tile/{z}/{y}/{x}", "", "Tiles courtesy of arcgis.com (FAA)", 1, 0, 12, "", 110, False, True, 0),
    ("ifr-low", "IFR Enroute Chart Low", "US", "tile", "https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/IFR_AreaLow/MapServer/tile/{z}/{y}/{x}", "", "Tiles courtesy of arcgis.com (FAA)", 1, 0, 12, "", 120, False, True, 0),
    ("ifr-high", "IFR Enroute Chart High", "US", "tile", "https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/IFR_High/MapServer/tile/{z}/{y}/{x}", "", "Tiles courtesy of arcgis.com (FAA)", 1, 0, 12, "", 130, False, True, 0),
    ("rainviewer", "RainViewer Radar", "Worldwide", "rainviewer", "https://api.rainviewer.com/public/weather-maps.json", "", "RainViewer.com", 0.7, 0, 12, "", 200, False, False, 2),
    ("tfrs", "TFRs", "US", "geojson", "https://raw.githubusercontent.com/wiedehopf/tar1090-aux/master/tfrs.geojson", "label=NOTAM_KEY,LEGAL", "TFRs via FAA, hosted on GitHub: tar1090-aux", 0.12, 0, 19, "#ff0000", 300, False, False, 5),
    ("sua", "Special Use Airspace", "US", "geojson", "https://opendata.arcgis.com/datasets/dd0d1b726e504137ab3c41b21835d05b_0.geojson", "colorby=TYPE_CODE:P|R|W=#4895ef;A|MOA=#852d45", "Airspace data: FAA", 0.3, 0, 19, "#4895ef", 310, False, False, 0),
    ("nexrad", "NEXRAD", "US", "tile", "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png", "", "NEXRAD courtesy of IEM", 0.7, 0, 12, "", 320, False, False, 5),
    ("noaa-sat", "NOAA Infrared Sat", "US", "wms", "https://nowcoast.noaa.gov/geoserver/satellite/wms", "global_longwave_imagery_mosaic", "NOAA", 0.5, 0, 12, "", 330, False, False, 0),
    ("noaa-radar", "NOAA Radar", "US", "wms", "https://nowcoast.noaa.gov/geoserver/weather_radar/wms", "base_reflectivity_mosaic", "NOAA", 0.7, 0, 12, "", 340, False, False, 5),
    ("us-a2a", "US A2A Refueling", "US", "geojson", "https://raw.githubusercontent.com/wiedehopf/tar1090/master/html/geojson/US_A2A_refueling.geojson", "label=name", "tar1090 geojson", 0.3, 0, 19, "#3432a8", 350, False, False, 0),
    ("us-artcc", "US ARTCC Boundaries", "US", "geojson", "https://raw.githubusercontent.com/wiedehopf/tar1090/master/html/geojson/US_ARTCC_boundaries.geojson", "", "tar1090 geojson", 0.3, 0, 19, "#ff00ff", 360, False, False, 0),
]


def seed_defaults(conn):
    """First start only; a settings flag stops deleted layers coming back."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layers_seeded'").fetchone():
        return
    for r in DEFAULTS:
        conn.execute("INSERT INTO map_layers (slug, name, grp, kind, url, options, attribution, opacity, min_zoom, max_zoom, color, sort_order, default_on, is_base, refresh_minutes) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", r)
    conn.execute("INSERT INTO settings (key, value) VALUES ('layers_seeded', '1') ON CONFLICT DO NOTHING")


# Which layers belong in the live map's Weather panel, under what heading, and the one-line description shown beside them.
# Applied once (a settings flag), then edited in Admin > Map Layers.
WEATHER_DEFAULTS = {
    "nexrad": ("Radar", "Rain and storm intensity from the US NEXRAD network. Press Play to loop the last 50 minutes."),
    "noaa-radar": ("Radar", "NOAA's blended radar reflectivity mosaic for the US."),
    "rainviewer": ("Radar", "Worldwide radar from RainViewer, with a short forecast. Press Play to loop."),
    "noaa-sat": ("Satellite", "Infrared satellite imagery: bright, cold cloud tops are tall storms."),
}


def apply_weather_defaults(conn):
    if conn.execute("SELECT 1 FROM settings WHERE key='layers_wx_seeded'").fetchone():
        return
    for slug, (grp, text) in WEATHER_DEFAULTS.items():
        conn.execute("UPDATE map_layers SET wx_group=%s, description=%s WHERE slug=%s AND wx_group=''", (grp, text, slug))
    conn.execute("INSERT INTO settings (key, value) VALUES ('layers_wx_seeded', '1') ON CONFLICT DO NOTHING")


def ensure_local_layers(conn):
    """Layers this site serves itself (added once per install, so a deleted one does not come back; existing installs get them on the next start)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_highways_seeded'").fetchone():
        return
    conn.execute("INSERT INTO map_layers (slug, name, grp, kind, url, options, attribution, opacity, min_zoom, max_zoom, color, sort_order, default_on, is_base, refresh_minutes, description) "
                 "VALUES ('highways', 'Highways', 'US', 'geojson', '/api/roads.geojson', 'label=NAME;colorby=KIND:I=#ff7a59;U=#ffd24f;S=#8fb8ff', 'Highways: US Census Bureau (TIGERweb), kept by this site', 0.5, 5, 19, '#ffd24f', 400, FALSE, FALSE, 0, "
                 "'Interstates (orange), US highways (yellow) and state highways (blue). Loaded from the Census by Data Updates.') ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_highways_seeded', '1') ON CONFLICT DO NOTHING")


def ensure_alerts_layer(conn):
    """The Weather Alerts layer: the Weather Service's active warnings, watches and advisories (once per install, so a deleted one does not come back; existing installs get it on the next start)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_wxalerts_seeded'").fetchone():
        return
    conn.execute("INSERT INTO map_layers (slug, name, grp, kind, url, options, attribution, opacity, min_zoom, max_zoom, color, sort_order, default_on, is_base, refresh_minutes, description) "
                 "VALUES ('wx-alerts', 'Weather Alerts', 'US', 'geojson', '/api/weather/alerts.geojson', "
                 "'label=event;colorby=KIND:TOR=#ff2d2d;SVR=#ff9f1a;FLD=#2fbf5b;WIN=#ff7ad9;HOT=#c1272d;FIR=#d9822b;WND=#c9b037;OTH=#8aa4ff', "
                 "'Alerts: National Weather Service (public domain)', 0.3, 4, 19, '#8aa4ff', 410, FALSE, FALSE, 2, "
                 "'Active National Weather Service warnings, watches and advisories: tornado (red), severe storm (orange), flood (green), winter (pink), heat, fire weather and wind. Click one for the details.') ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_wxalerts_seeded', '1') ON CONFLICT DO NOTHING")


def fix_alerts_label(conn):
    """The Weather Alerts layer's hover label was the event and every county it covers: only the event now (once per install; a label an administrator changed is left alone)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_wxalerts_label_fixed'").fetchone():
        return
    conn.execute("UPDATE map_layers SET options = REPLACE(options, 'label=event,area;', 'label=event;') WHERE slug='wx-alerts'")
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_wxalerts_label_fixed', '1') ON CONFLICT DO NOTHING")


HAZARD_LAYERS = {
    "wx-alerts": "Active National Weather Service warnings, watches and advisories, colored by kind. Click an area for the full text.",
    "quakes": "Recent earthquakes (US Geological Survey), bigger circles for stronger ones.",
    "rivers": "River and lake gauges (NOAA) colored by flood category. Click a drop for the stage and flow.",
}


def place_hazard_layers(conn):
    """The Weather Alerts, Earthquakes and River Gauges layers belong in the Weather panel under Alerts and Hazards, with the radar and satellite (once per install; a heading an administrator set is left alone)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_hazards_wxgroup'").fetchone():
        return
    for slug, text in HAZARD_LAYERS.items():
        conn.execute("UPDATE map_layers SET wx_group='Alerts and Hazards', description=%s WHERE slug=%s AND wx_group=''", (text, slug))
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_hazards_wxgroup', '1') ON CONFLICT DO NOTHING")


def ensure_satellite_extra_layers(conn):
    """Visible and water vapor satellite imagery and lightning from NOAA's nowCOAST, in the Weather panel (once per install, so a deleted one does not come back; existing installs get them on the next start)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_sat_extra_seeded'").fetchone():
        return
    rows = (
        ("noaa-vis", "NOAA Visible Satellite", "https://nowcoast.noaa.gov/geoserver/satellite/wms", "global_visible_imagery_mosaic", 0.6, 331, "Satellite",
         "Visible satellite imagery in daylight: real clouds as the eye sees them (dark at night)."),
        ("noaa-wv", "NOAA Water Vapor", "https://nowcoast.noaa.gov/geoserver/satellite/wms", "global_water_vapor_imagery_mosaic", 0.6, 332, "Satellite",
         "Water vapor imagery: moisture in the middle and upper air, which shows the jet stream and storms forming before there are clouds."),
        ("noaa-lightning", "Lightning", "https://nowcoast.noaa.gov/geoserver/lightning_detection/wms", "ldn_lightning_strike_density", 0.8, 333, "Alerts and Hazards",
         "Where lightning has struck in the last minutes (NOAA, strike density). Red and yellow are the busiest areas."),
    )
    for slug, name, url, layer, op, order, grp, text in rows:
        conn.execute("INSERT INTO map_layers (slug, name, grp, kind, url, options, attribution, opacity, min_zoom, max_zoom, color, sort_order, default_on, is_base, refresh_minutes, description, wx_group) "
                     "VALUES (%s,%s,'US','wms',%s,%s,'NOAA nowCOAST (public domain)',%s,0,12,'',%s,FALSE,FALSE,0,%s,%s) ON CONFLICT DO NOTHING", (slug, name, url, layer, op, order, text, grp))
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_sat_extra_seeded', '1') ON CONFLICT DO NOTHING")


def ensure_quakes_layer(conn):
    """The Earthquakes layer: recent earthquakes from the USGS (once per install, so a deleted one does not come back; existing installs get it on the next start)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_quakes_seeded'").fetchone():
        return
    conn.execute("INSERT INTO map_layers (slug, name, grp, kind, url, options, attribution, opacity, min_zoom, max_zoom, color, sort_order, default_on, is_base, refresh_minutes, description) "
                 "VALUES ('quakes', 'Earthquakes', 'US', 'geojson', '/api/quakes.geojson', "
                 "'label=event,headline;colorby=KIND:M1=#7fd1ff;M2=#ffe14d;M3=#ff9f1a;M4=#ff2d2d', "
                 "'Earthquakes: US Geological Survey (public domain)', 0.6, 4, 19, '#ffe14d', 420, FALSE, FALSE, 5, "
                 "'Recent earthquakes in and around Arkansas (the New Madrid zone) from the USGS: bigger circles are stronger, colors step up at magnitude 2, 3 and 4. Click one for the time, depth and the USGS page.') ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_quakes_seeded', '1') ON CONFLICT DO NOTHING")


def ensure_satellites_layer(conn):
    """The Satellites layer: where the tracked satellites are now (once per install, so a deleted one does not come back; existing installs get it on the next start)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_satellites_seeded'").fetchone():
        return
    conn.execute("INSERT INTO map_layers (slug, name, grp, kind, url, options, attribution, opacity, min_zoom, max_zoom, color, sort_order, default_on, is_base, refresh_minutes, description) "
                 "VALUES ('satellites', 'Satellites', 'Radio', 'geojson', '/api/satellites.geojson', "
                 "'label=event,headline;colorby=KIND:iss=#7fd1ff;ham=#4dff88;wx=#ffb347', "
                 "'Orbits: CelesTrak (US Space Force tracking), positions worked out here', 0.6, 0, 19, '#4dff88', 430, FALSE, FALSE, 1, "
                 "'Where the space station, weather satellites and amateur radio satellites are right now. Dots are the satellites (blue is the space station, green amateur radio, orange weather), the dashed line is the path ahead and the faint circle is the part of the earth each can see. Click one for its height, the next pass over the map center and how to work it (frequencies, mode, what you need).') ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_satellites_seeded', '1') ON CONFLICT DO NOTHING")


def ensure_ais_layer(conn):
    """The Marine Traffic layer: vessels heard by members' AIS receivers (once per install, so a deleted one does not come back; existing installs get it on the next start)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_ais_seeded'").fetchone():
        return
    conn.execute("INSERT INTO map_layers (slug, name, grp, kind, url, options, attribution, opacity, min_zoom, max_zoom, color, sort_order, default_on, is_base, refresh_minutes, description) "
                 "VALUES ('ais', 'Marine Traffic', 'Radio', 'geojson', '/api/ais.geojson', "
                 "'label=event,headline;colorby=KIND:tow=#ffb454;cargo=#4fb3ff;tanker=#ff6b6b;passenger=#c58bff;pleasure=#3ee6a8;fishing=#8fe36e;service=#ffd24f;other=#9fb0c8;unknown=#7b8794', "
                 "'AIS: vessels heard by members'' own receivers', 0.6, 5, 19, '#4fb3ff', 440, FALSE, FALSE, 1, "
                 "'Towboats, barges, ships and boats on the rivers and lakes, heard by members'' own AIS receivers. Colors show the kind (orange towboats and tugs, blue cargo, red tankers, purple passenger vessels, green pleasure craft). Click one for its speed, course, size and where it is going.') ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_ais_seeded', '1') ON CONFLICT DO NOTHING")


def ensure_drones_layer(conn):
    """The Drones layer: drones heard broadcasting Remote ID (once per install, so a deleted one does not come back; existing installs get it on the next start)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_drones_seeded'").fetchone():
        return
    conn.execute("INSERT INTO map_layers (slug, name, grp, kind, url, options, attribution, opacity, min_zoom, max_zoom, color, sort_order, default_on, is_base, refresh_minutes, description) "
                 "VALUES ('drones', 'Drones', 'Radio', 'geojson', '/api/drones.geojson', "
                 "'label=event,headline;colorby=KIND:air=#ffb454;ground=#9fb0c8;other=#ff6b6b', "
                 "'Remote ID: drones heard by members'' own receivers', 0.7, 8, 19, '#ffb454', 450, FALSE, FALSE, 1, "
                 "'Drones that broadcast Remote ID, heard by members'' own receivers: orange in the air, gray on the ground. Click one for its ID, altitude, speed and heading.') ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_drones_seeded', '1') ON CONFLICT DO NOTHING")


def ensure_rivers_layer(conn):
    """The River Gauges layer (once per install, so a deleted one does not come back; existing installs get it on the next start)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_rivers_seeded'").fetchone():
        return
    conn.execute("INSERT INTO map_layers (slug, name, grp, kind, url, options, attribution, opacity, min_zoom, max_zoom, color, sort_order, default_on, is_base, refresh_minutes, description) "
                 "VALUES ('rivers', 'River Gauges', 'US', 'geojson', '/api/rivers.geojson', "
                 "'label=event,headline;colorby=KIND:OK=#2fbf5b;ACT=#ffe14d;MIN=#ff9f1a;MOD=#ff2d2d;MAJ=#b04dff', "
                 "'River gauges: NOAA National Water Prediction Service (public domain)', 0.85, 5, 19, '#2fbf5b', 430, FALSE, FALSE, 10, "
                 "'River and lake gauges in and around Arkansas, colored by flood category: green no flooding, yellow action stage, orange minor, red moderate, purple major. Click one for the stage, flow and the NOAA page.') ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_rivers_seeded', '1') ON CONFLICT DO NOTHING")


def ensure_satellite_layer(conn):
    """USGS imagery for the Satellite choice (once per install, so a deleted one does not come back; existing installs get it on the next start)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_usgs_seeded'").fetchone():
        return
    conn.execute("INSERT INTO map_layers (slug, name, grp, kind, url, options, attribution, opacity, min_zoom, max_zoom, color, sort_order, default_on, is_base, refresh_minutes, description) "
                 "VALUES ('usgs-imagery', 'USGS Satellite Imagery', 'US', 'tile', 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}', '', 'Imagery: USGS The National Map (public domain)', 1, 0, 16, '', 5, FALSE, TRUE, 0, "
                 "'Aerial and satellite imagery of the United States from the USGS National Map, free to use. The Satellite choice in Display and View uses it.') ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_usgs_seeded', '1') ON CONFLICT DO NOTHING")


def ensure_hybrid_layers(conn):
    """The roads and place names drawn over satellite imagery for the Hybrid choice (once per install; existing installs get them on the next start)."""
    if conn.execute("SELECT 1 FROM settings WHERE key='layer_hybrid_seeded'").fetchone():
        return
    for slug, name, path, order, text in (("esri-roads", "Roads (for satellite)", "Reference/World_Transportation", 6, "Road lines and road names, drawn over satellite imagery by the Hybrid choice."),
                                          ("esri-places", "Place Names (for satellite)", "Reference/World_Boundaries_and_Places", 7, "Cities, towns and boundaries named over satellite imagery by the Hybrid choice.")):
        conn.execute("INSERT INTO map_layers (slug, name, grp, kind, url, options, attribution, opacity, min_zoom, max_zoom, color, sort_order, default_on, is_base, refresh_minutes, description) "
                     "VALUES (%s, %s, 'Worldwide', 'tile', %s, '', 'Powered by Esri.com', 1, 0, 19, '', %s, FALSE, FALSE, 0, %s) ON CONFLICT DO NOTHING",
                     (slug, name, "https://server.arcgisonline.com/ArcGIS/rest/services/" + path + "/MapServer/tile/{z}/{y}/{x}", order, text))
    conn.execute("INSERT INTO settings (key, value) VALUES ('layer_hybrid_seeded', '1') ON CONFLICT DO NOTHING")


def list_all():
    return get_db().execute("SELECT * FROM map_layers ORDER BY is_base DESC, sort_order, lower(name)").fetchall()


def for_map():
    """Enabled layers as plain dicts for the live map page."""
    rows = get_db().execute("SELECT * FROM map_layers WHERE enabled ORDER BY is_base DESC, sort_order, lower(name)").fetchall()
    keep = ("slug", "name", "grp", "kind", "url", "options", "attribution", "opacity", "min_zoom", "max_zoom", "color", "default_on", "is_base", "refresh_minutes", "wx_group", "description")
    from . import access, features
    return [{k: r[k] for k in keep} for r in rows if not (r["slug"] in features.LAYER_FEATURE and not access.allowed(features.LAYER_FEATURE[r["slug"]]))]


def validate(f):
    """Return (clean values, error) from a submitted form."""
    slug, name, grp = f.get("slug", "").strip().lower(), f.get("name", "").strip(), f.get("grp", "").strip()
    kind, url, options = f.get("kind", ""), f.get("url", "").strip(), f.get("options", "").strip()
    attribution, color = f.get("attribution", "").strip(), f.get("color", "").strip()
    if not SLUG_RE.match(slug):
        return None, "The short name must be 2 to 39 characters: lowercase letters, numbers and dashes."
    if not name or len(name) > 60:
        return None, "Enter a display name of up to 60 characters."
    if not grp or len(grp) > 40:
        return None, "Enter a group name of up to 40 characters, such as Weather."
    if kind not in dict(KINDS):
        return None, "Choose a valid layer type."
    u = urlparse(url)
    local = url.startswith("/api/") and " " not in url and len(url) <= 200   # a layer this site serves itself
    if not local and (u.scheme != "https" or not u.netloc or " " in url or len(url) > 500):
        return None, "The address must be a full https:// address with no spaces."
    if kind in ("tile",) and not all(t in url for t in ("{z}", "{x}", "{y}")):
        return None, "A map tile address must contain {z}, {x} and {y}."
    if kind == "wms" and not options:
        return None, "A WMS layer needs the layer name(s) from the service, for example conus_bref_qcd."
    wx_group, description = f.get("wx_group", "").strip(), f.get("description", "").strip()
    if len(wx_group) > 30 or len(description) > 200:
        return None, "The Weather panel heading must be 30 characters or fewer and the description 200 or fewer."
    if len(options) > 200 or len(attribution) > 300:
        return None, "The layer names must be 200 characters or fewer and the credit 300 or fewer."
    if kind == "geojson" and color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        return None, "The shape color must look like #ff4d4d."
    try:
        opacity, lo, hi, order = float(f.get("opacity", "1")), int(f.get("min_zoom", "0")), int(f.get("max_zoom", "19")), int(f.get("sort_order", "100"))
        refresh = int(f.get("refresh_minutes", "0") or 0)
    except ValueError:
        return None, "Opacity, zoom levels and order must be numbers."
    if not (0 <= refresh <= 1440):
        return None, "Refresh must be 0 (never) to 1440 minutes."
    if not (0.05 <= opacity <= 1) or not (0 <= lo <= 19) or not (1 <= hi <= 19) or lo > hi:
        return None, "Opacity must be 0.05 to 1, and zoom levels 0 to 19 with the lowest not above the highest."
    return {"slug": slug, "name": name, "grp": grp, "kind": kind, "url": url, "options": options, "attribution": attribution,
            "wx_group": wx_group, "description": description, "opacity": opacity, "min_zoom": lo, "max_zoom": hi, "color": color or "#ff4d4d", "sort_order": order, "refresh_minutes": refresh,
            "enabled": bool(f.get("enabled")), "default_on": bool(f.get("default_on")), "is_base": bool(f.get("is_base"))}, None
