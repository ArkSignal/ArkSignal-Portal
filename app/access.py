"""Who can see a feature (Settings > Site > Feature Access).

Every feature in app/features.py has a policy: everyone (the default), signed-in members only, or only members whose role holds the permission "See <feature>". An administrator who holds the feature's own
manage permission (or Manage Settings, or is a Super Admin) always gets through, so nobody can lock the people who run it out of it.

The policy is applied where every page already asks whether a feature is on: `settings.cfg()` answers "0" for a feature's Settings switch to a viewer who may not see it. So the menus, the Feed menu and every
feeding page, the home page cards and layers, the About page, the Radio and Weather overviews, the map layer lists, the pages themselves (a 404, as if the feature were off) and the feature's data routes all
hide it together, without each one being edited. Three kinds of request are exempt because no signed-in person is behind them: a receiver or station posting to its private upload address, a key-holder calling
/api/v1/, and background work with no request at all; for the API the key's owner is checked instead (see apikeys.py)."""
from flask import g, has_request_context
from flask_login import current_user

from . import features
from .settings import get_setting


def policy(key):
    own = key in features.MEMBERS_ONLY   # a feature that belongs to each signed-in member is never open to visitors
    p = get_setting("access_" + key, "members" if own else "everyone")
    if own:
        return p if p == "roles" else "members"
    return p if p in ("everyone", "members", "roles") else "everyone"


def allowed(key, user=None):
    """Whether `user` (default: the person making this request) may see the feature."""
    p = policy(key)
    if p == "everyone":
        return True
    u0 = user if user is not None else current_user
    if getattr(u0, "is_authenticated", False) and getattr(u0, "is_super_admin", False):
        return True   # the Super Admin role overrides every one of these settings
    cache = None
    if user is None and has_request_context():
        cache = g.setdefault("_access", {})
        if key in cache:
            return cache[key]
    u = user if user is not None else current_user
    ok = False
    if u is not None and getattr(u, "is_authenticated", False):
        if p == "members":
            ok = True
        else:
            ok = u.has_permission("use_" + key) or u.has_permission(features.MANAGE.get(key, "manage_settings")) or u.has_permission("manage_settings")
    if cache is not None:
        cache[key] = ok
    return ok


def visible(key):
    """The same, for templates and menus."""
    return allowed(key)
