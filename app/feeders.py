"""Feeder queries and chart helpers shared by the admin and public pages."""
import math
from datetime import datetime, timedelta, timezone

from markupsafe import Markup

from .cache import cached
from .db import get_db
from .settings import cfg_int

# period key -> (label, window, bucket seconds)
PERIODS = {
    "24h": ("24 hours", timedelta(hours=24), 300),
    "7d": ("7 days", timedelta(days=7), 3600),
    "30d": ("30 days", timedelta(days=30), 3600),
    "1y": ("1 year", timedelta(days=365), 86400),
}


def _uptime(bucket_count, first_seen, window, bucket_s, now):
    """Share of the buckets since the feeder first appeared (within the window) that had data."""
    start = max(first_seen, now - window)
    expected = max(1, math.ceil((now - start).total_seconds() / bucket_s))
    return min(100.0, 100.0 * bucket_count / expected)


def list_feeders():
    """Every feeder with owner name, latest rates and 24 hour uptime."""
    d = get_db()
    now = datetime.now(timezone.utc)
    rows = d.execute(
        "SELECT f.*, u.username AS owner_name, s.msg_per_s, s.pos_per_s, s.rtt_ms "
        "FROM feeders f LEFT JOIN users u ON u.id = f.owner_id "
        "LEFT JOIN LATERAL (SELECT msg_per_s, pos_per_s, rtt_ms FROM feeder_samples WHERE feeder_uuid = f.uuid "
        "ORDER BY ts DESC LIMIT 1) s ON TRUE ORDER BY f.online DESC, f.first_seen").fetchall()
    buckets = {r["feeder_uuid"]: r["n"] for r in d.execute(
        "SELECT feeder_uuid, COUNT(DISTINCT floor(extract(epoch from ts)/300)) AS n FROM feeder_samples "
        "WHERE ts > now() - interval '24 hours' GROUP BY feeder_uuid").fetchall()}
    out = []
    for r in rows:
        r = dict(r)
        r["uptime_24h"] = _uptime(buckets.get(r["uuid"], 0), r["first_seen"], timedelta(hours=24), 300, now)
        out.append(r)
    return out


def get_feeder(uuid):
    return get_db().execute(
        "SELECT f.*, u.username AS owner_name FROM feeders f LEFT JOIN users u ON u.id = f.owner_id WHERE f.uuid=%s",
        (uuid,)).fetchone()


def feeder_events(uuid, limit=100):
    return get_db().execute("SELECT * FROM feeder_events WHERE feeder_uuid=%s ORDER BY ts DESC, id DESC LIMIT %s",
                            (uuid, limit)).fetchall()


def series(uuid, period="24h"):
    """(points, uptime%) for a feeder over a period. Points: [(datetime, msg/s, pos/s)] in time order."""
    label, window, bucket = PERIODS.get(period, PERIODS["24h"])
    now = datetime.now(timezone.utc)
    start = now - window
    d = get_db()
    rows = d.execute(
        "WITH s AS (SELECT ts, msg_per_s, pos_per_s FROM feeder_samples WHERE feeder_uuid=%(u)s AND ts >= %(a)s "
        "UNION ALL SELECT hour, msg_per_s, pos_per_s FROM feeder_hourly WHERE feeder_uuid=%(u)s AND hour >= %(a)s) "
        "SELECT to_timestamp(floor(extract(epoch from ts)/%(b)s)*%(b)s) AS t, AVG(msg_per_s) AS m, AVG(pos_per_s) AS p "
        "FROM s GROUP BY 1 ORDER BY 1", {"u": uuid, "a": start, "b": bucket}).fetchall()
    f = get_feeder(uuid)
    up = _uptime(len(rows), f["first_seen"], window, bucket, now) if f else 0.0
    return [(r["t"], r["m"] or 0, r["p"] or 0) for r in rows], up


def line_chart(points, period="24h", width=720, height=200):
    """Inline SVG with messages/s and positions/s lines. Server-rendered; no JavaScript."""
    if not points:
        return Markup('<p class="dim">No data for this period yet.</p>')
    _label, window, _b = PERIODS.get(period, PERIODS["24h"])
    now = datetime.now(timezone.utc)
    t0 = (now - window).timestamp()
    t1 = now.timestamp()
    pad_l, pad_r, pad_t, pad_b = 46, 12, 10, 24
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    top = max(1.0, max(max(m, p) for _, m, p in points))
    top = 10 ** math.ceil(math.log10(top)) if top > 10 else math.ceil(top / 5) * 5 or 5

    def xy(t, v):
        return pad_l + w * (t.timestamp() - t0) / (t1 - t0), pad_t + h * (1 - v / top)

    def path(idx):
        out, last = [], None
        gap = _b * 2.5
        for p in points:
            x, y = xy(p[0], p[idx])
            cmd = "M" if last is None or p[0].timestamp() - last > gap else "L"
            out.append(f"{cmd}{x:.1f},{y:.1f}")
            last = p[0].timestamp()
        return " ".join(out)

    grid = "".join(
        f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{pad_t + h * (1 - i / 4):.1f}" y2="{pad_t + h * (1 - i / 4):.1f}" stroke="var(--line)"/>'
        f'<text x="{pad_l - 6}" y="{pad_t + h * (1 - i / 4) + 4:.1f}" text-anchor="end" fill="var(--dim)" font-size="10">{top * i / 4:g}</text>'
        for i in range(5))
    svg = (f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Messages and positions per second">'
           f'{grid}<path d="{path(1)}" fill="none" stroke="var(--acc2)" stroke-width="1.6"/>'
           f'<path d="{path(2)}" fill="none" stroke="var(--acc)" stroke-width="1.6"/></svg>')
    return Markup(svg)


# ---------------------------------------------------------------- public views

PUBLIC_PERIODS = {"24h": ("24 hours", timedelta(hours=24)), "7d": ("7 days", timedelta(days=7)), "30d": ("30 days", timedelta(days=30))}
SORTS = {"positions": ("Positions per Day", "positions_per_day"), "messages": ("Messages per Day", "messages_per_day"),
         "uptime": ("Uptime", "uptime"), "range": ("Maximum Range (30 Days)", "max_range_30d")}
PUBLIC_WHERE = "f.owner_id IS NOT NULL AND f.station_id IS NOT NULL AND NOT f.banned"


def public_location(f):
    """The only place coordinates are prepared for public use. Hidden gives nothing; General rounds to 0.1
    degree (about 6 miles); Exact is the owner's explicit opt-in."""
    from . import privacy
    return privacy.position(f["lat"], f["lon"], f["privacy"] or "general")


def owner_label(row):
    return row.get("owner_display") or row.get("owner_name") or ""


def public_feeders(period="24h", sort="positions"):
    """Cached briefly (Settings > Public Data Cache); callers get their own copies."""
    return [dict(r) for r in cached(("public_feeders", period, sort), lambda: _public_feeders(period, sort))]


def _public_feeders(period, sort):
    """Claimed, named, not-banned feeders with period statistics, ranked."""
    label, window = PUBLIC_PERIODS.get(period, PUBLIC_PERIODS["24h"])
    now = datetime.now(timezone.utc)
    start = now - window
    d = get_db()
    rows = d.execute(
        "SELECT f.uuid, f.station_id, f.name, f.description, f.website, f.website_public, f.official, f.official_order, f.online, f.first_seen, f.last_seen, f.lat, f.lon, f.privacy, "
        "u.username AS owner_name, u.display_name AS owner_display, u.callsign AS owner_callsign, "
        "COALESCE(st.pos, 0) AS pos_total, COALESCE(st.msg, 0) AS msg_total, COALESCE(st.buckets, 0) AS buckets, "
        "cur.msg_per_s, cur.pos_per_s "
        f"FROM feeders f JOIN users u ON u.id = f.owner_id "
        "LEFT JOIN LATERAL (SELECT msg_per_s, pos_per_s FROM feeder_samples WHERE feeder_uuid = f.uuid ORDER BY ts DESC LIMIT 1) cur ON TRUE "
        "LEFT JOIN LATERAL (SELECT SUM(pos) AS pos, SUM(msg) AS msg, SUM(b) AS buckets FROM ("
        "  SELECT pos_per_s * 30 AS pos, msg_per_s * 30 AS msg, 1.0 AS b FROM feeder_samples WHERE feeder_uuid = f.uuid AND ts >= %s "
        "  UNION ALL SELECT pos_per_s * samples * 30, msg_per_s * samples * 30, samples::float FROM feeder_hourly WHERE feeder_uuid = f.uuid AND hour >= %s"
        ") x) st ON TRUE "
        f"WHERE {PUBLIC_WHERE}", (start, start)).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        seen_for = min(window, now - r["first_seen"])
        days = max(seen_for.total_seconds() / 86400, 1 / 24)
        r["positions_per_day"] = r["pos_total"] / days
        r["messages_per_day"] = r["msg_total"] / days
        # each sample covers about 30 seconds of connected time
        covered = r["buckets"] * 30.0
        r["uptime"] = min(100.0, 100.0 * covered / max(seen_for.total_seconds(), 30.0)) if r["buckets"] else 0.0
        out.append(r)
    from . import rangestats
    best = rangestats.summary(30)
    for r in out:
        r["max_range_30d"] = best.get(r["uuid"], 0.0)
    out.sort(key=lambda r: r[SORTS.get(sort, SORTS["positions"])[1]], reverse=True)
    for i, r in enumerate(out, 1):
        r["rank"] = i
    # official network stations are pinned to the top (in their own order); everyone keeps their real rank
    out.sort(key=lambda r: (not r["official"], r["official_order"] if r["official"] else 0, r["rank"]))
    return out


def network_totals():
    return dict(cached(("network_totals",), _network_totals))


def _network_totals():
    d = get_db()
    t = d.execute(
        "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE online AND NOT banned) AS online, "
        "COUNT(*) FILTER (WHERE owner_id IS NOT NULL AND station_id IS NOT NULL AND NOT banned) AS claimed FROM feeders").fetchone()
    cur = d.execute(
        "SELECT COALESCE(SUM(s.msg_per_s),0) AS msgs, COALESCE(SUM(s.pos_per_s),0) AS pos FROM feeders f "
        "JOIN LATERAL (SELECT msg_per_s, pos_per_s FROM feeder_samples WHERE feeder_uuid = f.uuid ORDER BY ts DESC LIMIT 1) s ON TRUE "
        "WHERE f.online AND NOT f.banned").fetchone()
    return {"total": t["total"], "online": t["online"], "claimed": t["claimed"], "msgs": cur["msgs"], "pos": cur["pos"]}


def online_in_state():
    """Online, not-banned stations that are in Arkansas (or whose position is not known, which cannot be judged) when the home page is limited to the state."""
    from . import arkansas
    def load():
        rows = get_db().execute("SELECT lat, lon FROM feeders WHERE online AND NOT banned").fetchall()
        return sum(1 for r in rows if arkansas.inside(r["lat"], r["lon"]))
    return cached(("online_ar", arkansas.limited()), load)


def public_feeder(station_id):
    return get_db().execute(
        "SELECT f.*, u.username AS owner_name, u.display_name AS owner_display, u.callsign AS owner_callsign "
        f"FROM feeders f JOIN users u ON u.id = f.owner_id WHERE {PUBLIC_WHERE} AND lower(f.station_id) = lower(%s)",
        (station_id,)).fetchone()


def feeders_for_ip(ip, hours):
    """Feeders seen from this public IP recently, for the 'Am I feeding?' check. A feeder's UUID is only
    returned while it is unclaimed (it is needed to claim it); claimed feeders show their public station ID."""
    return get_db().execute(
        "SELECT CASE WHEN owner_id IS NULL THEN uuid END AS uuid, uuid AS full_uuid, station_id, name, online, last_seen, "
        "(owner_id IS NOT NULL) AS claimed, (owner_id IS NOT NULL AND station_id IS NOT NULL AND NOT banned) AS public "
        "FROM feeders WHERE last_ip = %s AND last_seen > now() - make_interval(hours => %s) AND NOT banned "
        "ORDER BY online DESC, last_seen DESC", (ip, hours)).fetchall()
