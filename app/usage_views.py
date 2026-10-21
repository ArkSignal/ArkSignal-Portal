"""Admin > Usage: what the site asks of outside services and what it stores for each service. The counting is in usage.py."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template, request
from flask_login import current_user

from . import usage
from .admin import permission_required
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("usage", __name__)


def _service(label, mod, enabled, rows_of, feed_of=None):
    """One service container: is it answering, how big is its database, how much does it hold, how busy is its feed."""
    out = {"service": label, "size": None, "holds": "", "feed": "", "note": "", "enabled": enabled}
    if not enabled:
        out["note"] = "switched off"
        return out
    try:
        st = mod.stats()
    except Exception as e:
        out["note"] = "could not ask: " + type(e).__name__
        return out
    if not st.get("ok"):
        out["note"] = st.get("note") or "not answering"
        return out
    d = st["data"] or {}
    out["size"] = d.get("db_bytes")
    out["holds"] = rows_of(d)
    out["feed"] = feed_of(d) if feed_of else ""
    out["data"] = d
    return out


def _services():
    from . import acars, aprs, mesh, sonde_predict
    feed = lambda d: d.get("feed") or {}
    return [
        _service("Prediction and archive (radiosondes)", sonde_predict, sonde_predict.enabled(),
                 lambda d: f"{d.get('flights', 0):,} archived flights, {d.get('predictions', 0):,} predictions"),
        _service("APRS", aprs, cfg("aprs_enabled") == "1", lambda d: f"{d.get('stations', 0):,} stations",
                 lambda d: f"{feed(d).get('packets', 0):,} packets since start, {feed(d).get('per_min', 0)}/min, {'connected' if feed(d).get('connected') else 'not connected'}"),
        _service("ACARS", acars, cfg("acars_enabled") == "1", lambda d: f"{d.get('messages', 0):,} messages"),
        _service("Mesh nodes", mesh, cfg("mesh_enabled") == "1", lambda d: f"{d.get('nodes', 0):,} nodes ({d.get('meshtastic', 0):,} Meshtastic, {d.get('meshcore', 0):,} MeshCore)",
                 lambda d: f"{feed(d).get('packets', 0):,} packets since start, {feed(d).get('per_min', 0)}/min, our broker {'connected' if feed(d).get('connected') else 'not connected'}"
                           + (f", public server {'connected' if feed(d).get('up_connected') else 'not connected'}" if (d.get('config') or {}).get('up_enabled') == '1' else '')),
    ]


def _sonde_feed():
    from . import sondes
    f = sondes.feed_stats()
    if not f:
        return ""
    return f"{f.get('msgs', 0):,} messages since start ({f.get('per_min', 0)}/min), {'connected' if f.get('connected') else 'not connected'}"


def counters():
    """The cumulative counters of the live feeds and the outside requests the service containers made, for the sampler (usage.sample): ({feed: total}, {(service, host, day): (calls, bytes)})."""
    from . import sondes
    feeds, outs = {}, {}
    f = sondes.feed_stats()
    if f:
        feeds["Radiosonde live feed (SondeHub)"] = int(f.get("msgs") or 0)
    for sv in _services():
        d = sv.get("data") or {}
        fd = d.get("feed") or {}
        if sv["service"] in ("APRS", "Mesh nodes") and fd.get("packets") is not None:
            feeds[sv["service"]] = int(fd.get("packets") or 0)
        o = d.get("outside") or []
        for out in ([o] if isinstance(o, dict) else o):
            for day, v in (out.get("days") or {}).items():
                outs[(sv["service"], out["host"], day)] = (v[0], v[1])
    return feeds, outs


@bp.route("/admin/usage")
@permission_required("manage_settings")
def usage_page():
    db = get_db()
    usage.flush()
    usage.sample()
    usage.prune(db, cfg_int("usage_keep_days"))
    now = datetime.now(timezone.utc)
    all_time = request.args.get("all") == "1"
    start = datetime(2000, 1, 1, tzinfo=timezone.utc) if all_time else usage.parse_when(request.args.get("from"), now - timedelta(days=30))
    end = usage.parse_when(request.args.get("to"), now + timedelta(hours=1))
    if end <= start:
        start, end = end - timedelta(hours=1), start + timedelta(hours=1)
    services = _services()
    tables, total = usage.portal_storage(db)
    feed_rng, feed_all = usage.feed_totals(db, start, end)
    extra = [{"host": r["host"], "name": usage.NAMES.get(r["host"], ""), "via": r["via"], "calls": int(r["calls"] or 0), "bytes": int(r["bytes"] or 0)} for r in usage.service_requests(db, start, end)]
    first = db.execute("SELECT min(hour) AS t FROM api_usage_hourly").fetchone()["t"]
    return render_template("admin_usage.html", active="usage", rows=usage.outbound(db, start, end), services=services, tables=tables, total=total, sonde_feed=_sonde_feed(), extra=extra, human=usage.human,
                           keep=cfg_int("usage_keep_days"), start=start.isoformat(), end=end.isoformat(), all_time=all_time, feed_rng=feed_rng, feed_all=feed_all, first=first)
