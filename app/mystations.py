"""My Stations: one page that shows everything a member runs or has claimed, across the services (ADS-B feeders, weather stations, radiosonde stations, ACARS stations, repeaters, mesh logins and nodes),
with a count for each and a link to its own page. The pages themselves stay where they are."""
from flask import Blueprint, render_template
from flask_login import current_user, login_required

from .db import get_db
from .settings import cfg

bp = Blueprint("mystations", __name__)


def _count(sql, uid):
    try:
        return int(get_db().execute(sql, (uid,)).fetchone()["n"])
    except Exception:
        get_db().rollback()
        return 0


def cards(uid):
    """[{title, blurb, count, open, add, extra}]: one per service the site has switched on."""
    out = [
        {"key": "feeders", "title": "ADS-B Feeders", "blurb": "Receivers that feed aircraft to the network.", "count": _count("SELECT COUNT(*) AS n FROM feeders WHERE owner_id=%s", uid), "open": "/my/", "add": "/feed/", "add_label": "How to Feed"},
        {"key": "weather", "title": "Weather Stations", "blurb": "Home weather stations that report to the Weather Map.", "count": _count("SELECT COUNT(*) AS n FROM wx_stations WHERE owner_id=%s", uid), "open": "/my/weather", "add": "/feed/weather", "add_label": "How to Feed"},
        {"key": "sondes", "title": "Radiosonde Stations", "blurb": "Receivers that hear weather balloons (claimed by call sign).", "count": _count("SELECT COUNT(*) AS n FROM sonde_stations WHERE owner_id=%s", uid), "open": "/my/sondes", "add": "/feed/sondes", "add_label": "How to Feed"},
    ]
    if cfg("acars_enabled") == "1":
        out.append({"key": "acars", "title": "ACARS Stations", "blurb": "Aircraft datalink receivers.", "count": _count("SELECT COUNT(*) AS n FROM acars_stations WHERE owner_id=%s", uid), "open": "/my/acars", "add": "/feed/acars", "add_label": "How to Feed"})
    if cfg("repeater_enabled") == "1":
        out.append({"key": "repeaters", "title": "Repeaters", "blurb": "Repeaters you run or have claimed.", "count": _count("SELECT COUNT(*) AS n FROM repeaters WHERE owner_id=%s", uid), "open": "/my/repeaters", "add": "/repeaters/", "add_label": "Repeater List"})
    if cfg("mesh_enabled") == "1":
        n = _count("SELECT COUNT(*) AS n FROM mesh_accounts WHERE owner_id=%s", uid)
        c = _count("SELECT COUNT(*) AS n FROM mesh_claims WHERE owner_id=%s AND status='claimed'", uid)
        out.append({"key": "mesh", "title": "Mesh Nodes", "blurb": "Broker logins for your nodes and the nodes you have claimed.", "count": n + c, "extra": f"{n} login{'s' if n != 1 else ''}, {c} claimed node{'s' if c != 1 else ''}", "open": "/my/mesh", "add": "/feed/mesh", "add_label": "How to Feed"})
    return out


@bp.route("/my/stations")
@login_required
def my_stations():
    return render_template("my_stations.html", cards=cards(current_user.id))
