"""Feeder achievements: friendly recognition worked out from numbers the site already keeps (30 day range, uptime and activity, and how long a feeder has been part of the network). Nothing is stored: the
badges are computed from the public feeder list each time (which is cached briefly), so they follow the real numbers and can never be out of date. The limits are all in Admin > Settings."""
from . import feeders as fd
from .settings import cfg, cfg_int

# code -> (label, one-line meaning built from the settings)
def definitions():
    return [
        ("reach", "Long Reach", f"Heard an aircraft {cfg_int('achv_range_nm')} nautical miles or more away in the last 30 days."),
        ("furthest", "Furthest Reach", "Holds the longest range of any feeder in the last 30 days."),
        ("alwayson", "Always On", f"Connected {cfg_int('achv_uptime_pct')}% of the time over the last 30 days (after a first week)."),
        ("busy", "Busy Antenna", f"One of the {cfg_int('achv_busy_top')} feeders that report the most positions per day."),
        ("veteran", "Veteran", f"Part of the network for {cfg_int('achv_veteran_days')} days or more."),
        ("established", "Established", f"Part of the network for {cfg_int('achv_established_days')} days or more."),
        ("new", "New Arrival", f"Joined in the last {cfg_int('achv_new_days')} days: welcome."),
        ("pillar", "Network Pillar", "One of the network's own official stations."),
    ]


def award(rows):
    """{feeder uuid: [(code, label), ...]} for the public feeder rows (use the 30 day list)."""
    from datetime import datetime, timezone
    if cfg("achv_enabled") != "1":
        return {}
    labels = {c: l for c, l, _t in definitions()}
    now = datetime.now(timezone.utc)
    out = {r["uuid"]: [] for r in rows}
    ranked = sorted((r for r in rows if not r.get("official") and (r.get("positions_per_day") or 0) > 0), key=lambda r: -r["positions_per_day"])
    busy = {r["uuid"] for r in ranked[:max(0, cfg_int("achv_busy_top"))]}
    far = max(rows, key=lambda r: r.get("max_range_30d") or 0, default=None)
    for r in rows:
        age_days = (now - r["first_seen"]).total_seconds() / 86400 if r.get("first_seen") else 0
        got = []
        if (r.get("max_range_30d") or 0) >= cfg_int("achv_range_nm"):
            got.append("reach")
        if far is not None and r["uuid"] == far["uuid"] and (far.get("max_range_30d") or 0) > 0:
            got.append("furthest")
        if age_days >= 7 and (r.get("uptime") or 0) >= cfg_int("achv_uptime_pct"):
            got.append("alwayson")
        if r["uuid"] in busy:
            got.append("busy")
        if age_days >= cfg_int("achv_veteran_days"):
            got.append("veteran")
        elif age_days >= cfg_int("achv_established_days"):
            got.append("established")
        elif age_days < cfg_int("achv_new_days"):
            got.append("new")
        if r.get("official"):
            got.append("pillar")
        out[r["uuid"]] = [(c, labels[c]) for c in got]
    return out


def for_all():
    """The badges of every public feeder, from the 30 day list."""
    return award(fd.public_feeders("30d", "positions"))
