"""Where a person lands after signing in: their own choice (profile), else the site default (Admin > Settings), else My Feeders."""
import json

from .db import get_db
from .settings import get_setting

# path, label, needs admin access
CHOICES = [
    ("/my/", "My Feeders", False),
    ("/", "Home", False),
    ("/aircraft", "Aircraft Tracking", False),
    ("/live", "Live Map", False),
    ("/my/alerts", "My Alerts", False),
    ("/flight/", "Flights", False),
    ("/history/", "Flight History", False),
    ("/feeders/", "Feeders", False),
    ("/airports/", "Airports", False),
    ("/status", "Service Status", False),
    ("/release-notes", "Release Notes", False),
    ("/roadmap", "Roadmap", False),
    ("/admin/", "Admin", True),
]
FALLBACK = "/my/"


def allowed(user):
    return [(p, label) for p, label, admin in CHOICES if not admin or getattr(user, "has_admin_access", False)]


def target_for(user):
    """The page to open after sign in for this user."""
    ok = {p for p, _ in allowed(user)}
    try:
        row = get_db().execute("SELECT prefs FROM users WHERE id=%s", (user.id,)).fetchone()
        mine = (json.loads(row["prefs"]) if row and row["prefs"] else {}).get("landing", "")
    except Exception:
        mine = ""
    if mine in ok:
        return mine
    site = get_setting("default_landing") or FALLBACK
    return site if site in ok else FALLBACK
