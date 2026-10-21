"""Embedded tools: pages from the hub (map, statistics, Planefence ...) shown inside the portal's own
navigation instead of a new tab. The list is edited in Admin > Tools."""
import os
import re
from urllib.parse import urlparse

from .db import get_db

# Paths this portal serves itself. A tool pointing at one would show the portal inside itself.
PORTAL_PREFIXES = ("/view/", "/my/", "/admin/", "/feeders/", "/feed/", "/airports/", "/api/", "/healthz")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")

# {lat}, {lon} and {zoom_page} are filled in from Admin > Settings (Map Defaults) when the page is shown.
DEFAULT_TOOLS = [
    ("heatmap", "Heatmap", "/map/?heatmap&realheat&hideSidebar&hideButtons&lat={lat}&lon={lon}&zoom={zoom_page}", 20, "Where traffic flies, for any period"),
    ("replay", "Replay", "/map/?replay&lat={lat}&lon={lon}&zoom={zoom_page}", 30, "Play back past traffic"),
    ("statistics", "Aircraft Statistics", "/grafana/d/ultrafeeder/ultrafeeder-stats?orgId=1&theme=dark&kiosk", 40, "Message rates, aircraft counts, range and long-term history"),
]


# Tools added after the first release: each is offered once to existing installs too (its own settings flag), then left to the admin.
LATER_TOOLS = [
    ("tools_planefence_seeded", ("planefence", "Planefence", "/pf/?mode=pf", 50, "Aircraft that flew over the network's area, with photos and details")),
    ("tools_planealert_seeded", ("plane-alert", "Plane-Alert", "/pf/?mode=pa", 55, "Interesting aircraft: military, government, rare and notable")),
    ("tools_feeders_seeded", ("network-feeders", "Aircraft Feeder Statistics", "/grafana/d/feeders/network-feeders?orgId=1&theme=dark&kiosk", 45,
                              "Messages, positions and range for every station, from the portal's own statistics")),
    ("tools_wxdash_seeded", ("weather-stats", "Weather Dashboard", "/grafana/d/weather/weather-dashboard?orgId=1&theme=dark&kiosk", 60, "Temperature, humidity, pressure, wind and rain of every public weather station, over time")),
    ("tools_sondedash_seeded", ("balloon-stats", "Radiosonde Dashboard", "/grafana/d/sondes/radiosonde-dashboard?orgId=1&theme=dark&kiosk", 65, "Balloons in the air, their altitude and climb, and how many flights were recorded")),
]


def seed_defaults(conn):
    """First start only (a settings flag stops it coming back after an admin deletes tools); newer default tools once each."""
    _seed_first(conn)
    _rename_dashboards(conn)
    # the embedded tar1090 map is gone: the live map has every one of its features. Removed once (an admin who adds a tool of their own with that name later keeps it).
    if not conn.execute("SELECT 1 FROM settings WHERE key='tools_classicmap_removed'").fetchone():
        conn.execute("DELETE FROM embedded_tools WHERE slug='map'")
        conn.execute("INSERT INTO settings (key, value) VALUES ('tools_classicmap_removed', '1') ON CONFLICT DO NOTHING")
    for flag, (slug, name, url, order, description) in LATER_TOOLS:
        if conn.execute("SELECT 1 FROM settings WHERE key=%s", (flag,)).fetchone():
            continue
        if slug == "network-feeders" and os.environ.get("TOOLS_GRAFANA_URL"):   # test setups run Grafana on its own port
            url = os.environ["TOOLS_GRAFANA_URL"].replace("/d/ultrafeeder/ultrafeeder-stats", "/d/feeders/network-feeders")
        elif slug == "weather-stats" and os.environ.get("TOOLS_GRAFANA_URL"):
            url = os.environ["TOOLS_GRAFANA_URL"].replace("/d/ultrafeeder/ultrafeeder-stats", "/d/weather/weather-dashboard")
        elif slug == "balloon-stats" and os.environ.get("TOOLS_GRAFANA_URL"):
            url = os.environ["TOOLS_GRAFANA_URL"].replace("/d/ultrafeeder/ultrafeeder-stats", "/d/sondes/radiosonde-dashboard")
        conn.execute("INSERT INTO embedded_tools (slug, name, url, sort_order, description) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (slug, name, url, order, description))
        conn.execute("INSERT INTO settings (key, value) VALUES (%s, '1') ON CONFLICT DO NOTHING", (flag,))
    # the statistics pages describe aircraft only, and now say so: renamed once, only where they still carry the old default name
    if not conn.execute("SELECT 1 FROM settings WHERE key='tools_aircraftstats_renamed'").fetchone():
        conn.execute("UPDATE embedded_tools SET name='Aircraft Statistics' WHERE slug='statistics' AND name='Statistics Dashboard'")
        conn.execute("UPDATE embedded_tools SET name='Aircraft Feeder Statistics' WHERE slug='network-feeders' AND name IN ('Feeder Statistics', 'Network Feeders')")
        conn.execute("INSERT INTO settings (key, value) VALUES ('tools_aircraftstats_renamed', '1') ON CONFLICT DO NOTHING")
    # the dashboard was called Network Feeders, which read like a second Feeders page: renamed once, only if still called that
    if not conn.execute("SELECT 1 FROM settings WHERE key='tools_feederstats_renamed'").fetchone():
        conn.execute("UPDATE embedded_tools SET name='Feeder Statistics' WHERE slug='network-feeders' AND name='Network Feeders'")
        conn.execute("INSERT INTO settings (key, value) VALUES ('tools_feederstats_renamed', '1') ON CONFLICT DO NOTHING")


def _rename_dashboards(conn):
    """The two Grafana dashboards are the Weather Dashboard and the Radiosonde Dashboard (not a second 'Weather Statistics' beside the portal's own page, and no longer 'Balloon Statistics'): renamed once, only where they still carry the old default names."""
    if conn.execute("SELECT 1 FROM settings WHERE key='tools_dashboards_renamed'").fetchone():
        return
    conn.execute("UPDATE embedded_tools SET name='Weather Dashboard' WHERE slug='weather-stats' AND name='Weather Statistics'")
    conn.execute("UPDATE embedded_tools SET name='Radiosonde Dashboard' WHERE slug='balloon-stats' AND name='Balloon Statistics'")
    conn.execute("INSERT INTO settings (key, value) VALUES ('tools_dashboards_renamed', '1') ON CONFLICT DO NOTHING")


def _seed_first(conn):
    if conn.execute("SELECT 1 FROM settings WHERE key='tools_seeded'").fetchone():
        return
    base = os.environ.get("TOOLS_BASE_URL", "").strip().rstrip("/")
    for slug, name, url, order, description in DEFAULT_TOOLS:
        if base and slug != "statistics":   # test setups where the hub's pages are not behind /map/ here: point the defaults at the hub itself
            url = base + ("/" + url[len("/map/"):] if url.startswith("/map/") else url)
        if slug == "statistics" and os.environ.get("TOOLS_GRAFANA_URL"):   # test setups run Grafana on its own port
            url = os.environ["TOOLS_GRAFANA_URL"]
        conn.execute("INSERT INTO embedded_tools (slug, name, url, sort_order, description) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                     (slug, name, url, order, description))
    conn.execute("INSERT INTO settings (key, value) VALUES ('tools_seeded', '1') ON CONFLICT DO NOTHING")


def list_all():
    return get_db().execute("SELECT * FROM embedded_tools ORDER BY sort_order, lower(name)").fetchall()


def list_enabled():
    return get_db().execute("SELECT slug, name, description FROM embedded_tools WHERE enabled ORDER BY sort_order, lower(name)").fetchall()


def get(slug, enabled_only=True):
    r = get_db().execute("SELECT * FROM embedded_tools WHERE slug=%s", (slug,)).fetchone()
    return r if r and (r["enabled"] or not enabled_only) else None


def validate(slug, name, url, order, description=""):
    """Return (clean values dict, error). URLs are same-site paths (/map/) or full https addresses."""
    slug, name, url = slug.strip().lower(), name.strip(), url.strip()
    if not SLUG_RE.match(slug):
        return None, "The short name must be 2 to 39 characters: lowercase letters, numbers and dashes."
    if not name or len(name) > 40:
        return None, "Enter a display name of up to 40 characters."
    if len(url) > 300 or " " in url:
        return None, "The address must be 300 characters or fewer and contain no spaces."
    if url.startswith("/"):
        if url.startswith("//"):
            return None, "Use a path such as /map/ or a full https:// address."
        if any(url == p.rstrip("/") or url.startswith(p) for p in PORTAL_PREFIXES):
            return None, ("That address is a page of this portal itself, which would show the portal inside itself. "
                          "Use the address of the tool, such as /map/ or /grafana/.")
    else:
        u = urlparse(url)
        private = False
        if u.scheme == "http" and u.hostname:
            import ipaddress
            try:
                ip = ipaddress.ip_address(u.hostname)
                private = ip.is_private or ip.is_loopback
            except ValueError:
                private = u.hostname == "localhost"
        if not u.netloc or (u.scheme != "https" and not private):
            return None, "Use a path such as /map/ or a full https:// address. Plain http is allowed only for addresses on a private network."
    try:
        order = int(order)
    except (TypeError, ValueError):
        return None, "Order must be a whole number."
    if not (0 <= order <= 9999):
        return None, "Order must be between 0 and 9999."
    description = description.strip()
    if len(description) > 120:
        return None, "The description must be 120 characters or fewer."
    return {"slug": slug, "name": name, "url": url, "sort_order": order, "description": description}, None
