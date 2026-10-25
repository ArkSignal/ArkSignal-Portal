"""Notifications for every kind of alert: ONE registry of the kinds, one switch for each in My Account > Community Settings > Notifications, and the watcher that raises the kinds that depend on a place.

Each kind has a key, a label, who may get it (everyone signed in, or administrators), a default, and a site switch (Settings > Matrix Chat: Notification Kinds, a list of the kinds the site does not offer). A member's choice
is kept in `push_prefs`, except three kinds that already had a home and keep it so the old pages stay true: friend notices (`users.push_friends`), mesh node alerts (`mesh_alert_prefs.by_push`) and, for aircraft alerts,
the per-alert "also notify my phone" tick stays and this switch is the master. Sending always goes through `notify()`: the site switch, the kind's own site switch, the member's switch, and a limit per kind per member
(`push_sent`) are checked there, then `webpush.notify_member` does the delivery. The kinds that depend on a place or on following something are raised by `watch_pass()` every few minutes:

* weather warnings (the NWS Weather Alerts layer) that cover one of the member's places, or any warning whose outline comes within the place's radius,
* earthquakes (USGS, the Earthquakes layer) of at least a magnitude the site sets, within the place's radius,
* river gauges (NWPS, the River Gauges layer) at a flood category the site sets or worse, within the place's radius,
* a satellite pass of a satellite the member ticked, over a member's place, a few minutes before it rises,
* a balloon the member follows that has landed,
* items needing attention in Admin > Overview, for administrators.

What was already told is remembered in `push_seen` so a warning is not repeated. Nothing here reads a new outside source: the layers' own cached answers are used (their credits and terms are already on About and in DATA_SOURCES_TERMS.md)."""
import hashlib
import math
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from .action_response import action_response
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_float, cfg_int

bp = Blueprint("pushkinds", __name__)

# (key, label, what it is, default, who: "member" or "admin", group)
KINDS = [
    ("chat", "Chat messages", "A new message or invitation in your chats (Matrix). You choose what counts in Element, room by room; this switch turns them all off here.", True, "member", "People"),
    ("friends", "Friend requests and messages", "When someone asks to be your friend, and notices from your friends list.", True, "member", "People"),
    ("community", "Community requests and answers", "A request to join a community you run, and the answer to your own request.", True, "member", "People"),
    ("community_news", "Community news and events", "News and events posted by a mesh community you follow.", False, "member", "People"),
    ("aircraft", "Aircraft alerts", "Matches of your aircraft alerts. Each alert still has its own tick, Also Notify My Phone or Computer.", True, "member", "Aircraft"),
    ("myflights_share", "My Flights links opened", "When someone opens one of your shared flight links (at most once a day for each link).", True, "member", "Aircraft"),
    ("satpass", "Satellite pass reminders", "A few minutes before a satellite you ticked rises over one of your places.", False, "member", "Space and Radio"),
    ("balloon", "Balloons I follow", "When a radiosonde or balloon you follow has landed.", True, "member", "Space and Radio"),
    ("mesh_node", "Mesh node alerts", "When one of your mesh nodes goes quiet, has a low battery, or is back.", False, "member", "Space and Radio"),
    ("weather_alert", "Weather warnings near my places", "National Weather Service warnings and watches that cover one of your places.", False, "member", "Weather and Hazards"),
    ("quake", "Earthquakes near my places", "An earthquake of the size the site sets, within the distance you choose of one of your places.", False, "member", "Weather and Hazards"),
    ("river", "River flooding near my places", "A river gauge near one of your places that reaches the flood level the site sets.", False, "member", "Weather and Hazards"),
    ("admin_attention", "Items needing attention (administrators)", "A new item on the Admin overview that needs a look: a service down, a failed check, a locked account.", True, "admin", "Administration"),
]
BY_KEY = {k[0]: k for k in KINDS}
KEYS = [k[0] for k in KINDS]
GROUPS = ["People", "Aircraft", "Space and Radio", "Weather and Hazards", "Administration"]
PLACE_KINDS = ("weather_alert", "quake", "river", "satpass")


# ------------------------------------------------------------------ the registry

def _site_off():
    return {x.strip() for x in cfg("push_kinds_off").split(",") if x.strip()}


def site_offers(kind):
    """Whether the site offers this kind at all (the master push switch, and the kind not on the list of kinds turned off)."""
    return cfg("push_enabled") == "1" and kind in BY_KEY and kind not in _site_off()


def may_have(user, kind):
    """Whether this member can be offered the kind: administrators' items only for people who can open the admin area."""
    k = BY_KEY.get(kind)
    return bool(k) and (k[4] == "member" or bool(user and user.has_admin_access))


def enabled(user_id, kind):
    """The member's own switch for a kind (their saved choice, else the kind's default)."""
    d = get_db()
    if kind == "friends":
        r = d.execute("SELECT push_friends FROM users WHERE id=%s", (user_id,)).fetchone()
        return bool(r and r["push_friends"])
    if kind == "mesh_node":
        r = d.execute("SELECT by_push FROM mesh_alert_prefs WHERE user_id=%s", (user_id,)).fetchone()
        return bool(r and r["by_push"])
    r = d.execute("SELECT enabled FROM push_prefs WHERE user_id=%s AND kind=%s", (user_id, kind)).fetchone()
    return bool(r["enabled"]) if r else BY_KEY[kind][3]


def set_enabled(user_id, kind, on):
    d = get_db()
    if kind == "friends":
        d.execute("UPDATE users SET push_friends=%s WHERE id=%s", (bool(on), user_id))
    elif kind == "mesh_node":
        d.execute("INSERT INTO mesh_alert_prefs (user_id, by_push) VALUES (%s,%s) ON CONFLICT (user_id) DO UPDATE SET by_push=EXCLUDED.by_push", (user_id, bool(on)))
    else:
        d.execute("INSERT INTO push_prefs (user_id, kind, enabled) VALUES (%s,%s,%s) ON CONFLICT (user_id, kind) DO UPDATE SET enabled=EXCLUDED.enabled", (user_id, kind, bool(on)))


def in_quiet_hours(user_id):
    """(in the member's quiet hours now, whether urgent warnings still come through). Hours are the site's own time zone; a window may run past midnight; start = end means no quiet hours."""
    if cfg("push_quiet_enabled") != "1":
        return False, True
    r = get_db().execute("SELECT push_quiet_start AS a, push_quiet_end AS b, push_quiet_urgent AS u FROM users WHERE id=%s", (user_id,)).fetchone()
    if not r or r["a"] < 0 or r["b"] < 0 or r["a"] == r["b"]:
        return False, True
    try:
        from zoneinfo import ZoneInfo
        h = datetime.now(timezone.utc).astimezone(ZoneInfo(cfg("timezone"))).hour
    except Exception:
        h = datetime.now(timezone.utc).hour
    inside = (r["a"] <= h < r["b"]) if r["a"] < r["b"] else (h >= r["a"] or h < r["b"])
    return inside, bool(r["u"])


def wants(user_id, kind, urgent=False):
    """Whether a notification of this kind may be sent to this member right now: the site offers it, the member has it on, and it is not their quiet time (an urgent one still comes through when they allowed that)."""
    if not (site_offers(kind) and enabled(user_id, kind)):
        return False
    quiet, urgent_ok = in_quiet_hours(user_id)
    return not quiet or (urgent and urgent_ok)


def _within_limits(user_id, kind):
    d = get_db()
    r = d.execute("SELECT COUNT(*) FILTER (WHERE ts > now() - interval '1 hour') AS h, COUNT(*) AS n FROM push_sent WHERE user_id=%s AND kind=%s AND ts > now() - interval '24 hours'", (user_id, kind)).fetchone()
    return r["h"] < cfg_int("push_kind_per_hour") and r["n"] < cfg_int("push_kind_per_day")


def notify(user_id, kind, title, body, url, tag=None, urgency="normal"):
    """One notification of a kind to a member: only if the site offers the kind, the member has it on, and the limits allow it. Returns the number of devices reached (0 when nothing was sent). Never raises."""
    try:
        from . import webpush
        if not wants(user_id, kind, urgent=(urgency == "high")) or not _within_limits(user_id, kind):
            return 0
        n = webpush.notify_member(user_id, title[:120], body[:240], url, tag or kind, urgency=urgency)
        get_db().execute("INSERT INTO push_sent (user_id, kind, ok) VALUES (%s,%s,%s)", (user_id, kind, n > 0))
        get_db().commit()
        return n
    except Exception:
        return 0


def _seen(user_id, kind, key):
    """True the first time it is called for this member, kind and key (and remembers it); False after."""
    r = get_db().execute("INSERT INTO push_seen (user_id, kind, key) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING 1", (user_id, kind, key[:200])).fetchone()
    return r is not None


# ------------------------------------------------------------------ the member's page

def _places(user_id):
    return [dict(r) for r in get_db().execute("SELECT * FROM member_places WHERE user_id=%s ORDER BY id", (user_id,)).fetchall()]


@bp.route("/my/notifications")
@login_required
def page():
    from . import satpass
    d = get_db()
    rows = []
    off = _site_off()
    for key, label, text, default, who, group in KINDS:
        if not may_have(current_user, key):
            continue
        rows.append({"key": key, "label": label, "text": text, "group": group, "on": enabled(current_user.id, key), "offered": key not in off})
    sats = []
    if cfg("satpass_enabled") == "1":
        picked = {r["norad"] for r in d.execute("SELECT norad FROM push_sats WHERE user_id=%s", (current_user.id,)).fetchall()}
        sats = [{"norad": e["norad"], "name": e["name"], "on": e["norad"] in picked} for e in satpass.elements()]
    follows = [dict(r) for r in d.execute("SELECT f.serial, s.type, s.status FROM sonde_follows f LEFT JOIN sondes s ON s.serial = f.serial WHERE f.user_id=%s ORDER BY f.created_at DESC", (current_user.id,)).fetchall()]
    q = d.execute("SELECT push_quiet_start AS a, push_quiet_end AS b, push_quiet_urgent AS u FROM users WHERE id=%s", (current_user.id,)).fetchone()
    return render_template("my_notifications.html", quiet={"on": cfg("push_quiet_enabled") == "1", "start": q["a"], "end": q["b"], "urgent": q["u"]}, groups=GROUPS, rows=rows, places=_places(current_user.id), sats=sats, follows=follows, push_on=cfg("push_enabled") == "1",
                           max_places=cfg_int("push_places_per_member"), max_radius=cfg_int("push_place_max_radius_km"), min_mag=cfg("push_quake_min_mag"), lead=cfg_int("push_satpass_lead_minutes"),
                           min_elev=cfg_int("push_satpass_min_elev"), active="notifications")


@bp.route("/my/notifications/save", methods=["POST"])
@login_required
def save():
    changed = []
    for key in KEYS:
        if not may_have(current_user, key):
            continue
        on = request.form.get("kind_" + key) == "1"
        if on != enabled(current_user.id, key):
            set_enabled(current_user.id, key, on)
            changed.append(("+" if on else "-") + key)
    if cfg("satpass_enabled") == "1" and request.form.get("sats_sent") == "1":
        from . import satpass
        want = {int(x) for x in request.form.getlist("sat") if x.isdigit()} & {e["norad"] for e in satpass.elements()}
        d = get_db()
        d.execute("DELETE FROM push_sats WHERE user_id=%s", (current_user.id,))
        for n in sorted(want):
            d.execute("INSERT INTO push_sats (user_id, norad) VALUES (%s,%s)", (current_user.id, n))
        changed.append(f"satellites:{len(want)}")
    get_db().commit()
    if changed:
        audit("push.prefs", target=current_user.username, detail=" ".join(changed)[:200])
    return action_response(True, "Your notification choices are saved.", redirect_endpoint="pushkinds.page")


@bp.route("/my/notifications/quiet", methods=["POST"])
@login_required
def quiet():
    f = request.form
    a, b = f.get("start", type=int), f.get("end", type=int)
    if a is None or b is None or not (-1 <= a <= 23 and -1 <= b <= 23):
        return action_response(False, "Choose the hours from the lists.", redirect_endpoint="pushkinds.page")
    if a < 0 or b < 0:
        a = b = -1
    urgent = f.get("urgent") == "1"
    d = get_db()
    d.execute("UPDATE users SET push_quiet_start=%s, push_quiet_end=%s, push_quiet_urgent=%s WHERE id=%s", (a, b, urgent, current_user.id))
    d.commit()
    audit("push.quiet", target=current_user.username, detail="off" if a < 0 or a == b else f"{a}:00 to {b}:00, urgent {'yes' if urgent else 'no'}")
    return action_response(True, "Your quiet hours are saved." if a >= 0 and a != b else "Quiet hours are off.", redirect_endpoint="pushkinds.page")


@bp.route("/my/notifications/places/add", methods=["POST"])
@login_required
def place_add():
    f = request.form
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM member_places WHERE user_id=%s", (current_user.id,)).fetchone()["n"] >= cfg_int("push_places_per_member"):
        return action_response(False, f"You can keep {cfg_int('push_places_per_member')} places. Remove one to add another.", redirect_endpoint="pushkinds.page")
    try:
        lat, lon, radius = float(f.get("lat", "")), float(f.get("lon", "")), float(f.get("radius", "") or 25)
    except ValueError:
        return action_response(False, "Give the place as numbers: latitude and longitude, for example 34.75 and -92.29.", redirect_endpoint="pushkinds.page")
    name = " ".join((f.get("name") or "").split())[:40]
    if not name or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return action_response(False, "Give the place a name and a position on the earth.", redirect_endpoint="pushkinds.page")
    radius = min(max(radius, 1), cfg_int("push_place_max_radius_km"))
    d.execute("INSERT INTO member_places (user_id, name, lat, lon, radius_km) VALUES (%s,%s,%s,%s,%s)", (current_user.id, name, round(lat, 4), round(lon, 4), radius))
    d.commit()
    audit("push.place_add", target=current_user.username, detail=name)
    return action_response(True, f"{name} is added to your places.", redirect_endpoint="pushkinds.page")


@bp.route("/my/notifications/places/<int:pid>/remove", methods=["POST"])
@login_required
def place_remove(pid):
    d = get_db()
    r = d.execute("DELETE FROM member_places WHERE id=%s AND user_id=%s RETURNING name", (pid, current_user.id)).fetchone()
    d.commit()
    if r is None:
        abort(404)
    audit("push.place_remove", target=current_user.username, detail=r["name"])
    return action_response(True, "The place is removed.", redirect_endpoint="pushkinds.page")


@bp.route("/my/notifications/balloons/<serial>/follow", methods=["POST"])
@login_required
def balloon_follow(serial):
    d = get_db()
    if d.execute("SELECT 1 FROM sondes WHERE serial=%s", (serial,)).fetchone() is None:
        abort(404)
    if d.execute("SELECT COUNT(*) AS n FROM sonde_follows WHERE user_id=%s", (current_user.id,)).fetchone()["n"] >= cfg_int("push_follows_per_member"):
        return action_response(False, f"You can follow {cfg_int('push_follows_per_member')} balloons. Stop following one first.", redirect_endpoint="sondes.flight_page", redirect_kwargs={"serial": serial})
    d.execute("INSERT INTO sonde_follows (user_id, serial) VALUES (%s,%s) ON CONFLICT DO NOTHING", (current_user.id, serial))
    d.commit()
    audit("push.balloon_follow", target=current_user.username, detail=serial)
    return action_response(True, "You follow this balloon. " + ("You are told when it lands." if enabled(current_user.id, "balloon") else "Turn on Balloons I Follow under My Account > Notifications to be told when it lands."), redirect_endpoint="sondes.flight_page", redirect_kwargs={"serial": serial})


@bp.route("/my/notifications/balloons/<serial>/unfollow", methods=["POST"])
@login_required
def balloon_unfollow(serial):
    d = get_db()
    d.execute("DELETE FROM sonde_follows WHERE user_id=%s AND serial=%s", (current_user.id, serial))
    d.commit()
    audit("push.balloon_unfollow", target=current_user.username, detail=serial)
    return action_response(True, "You no longer follow this balloon.", redirect_endpoint="pushkinds.page")


# ------------------------------------------------------------------ the watcher for the kinds that depend on a place

def _km(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _polys(geom):
    t, c = (geom or {}).get("type"), (geom or {}).get("coordinates") or []
    return [c] if t == "Polygon" else c if t == "MultiPolygon" else []


def covers(geom, lat, lon, radius_km):
    """Whether an alert's outline contains the place, or comes within its radius (measured to the outline's points)."""
    for poly in _polys(geom):
        if poly and _in_ring(lon, lat, poly[0]):
            return True
        if any(_km(lat, lon, y, x) <= radius_km for ring in poly[:1] for x, y in ring):
            return True
    return False


def _members(kind):
    return get_db().execute("SELECT u.id, u.username FROM users u WHERE NOT u.disabled AND EXISTS (SELECT 1 FROM push_subscriptions s WHERE s.user_id = u.id)").fetchall() if site_offers(kind) else []


def _watch_weather():
    from . import wxalerts
    if cfg("wxalerts_enabled") != "1":
        return 0
    codes = {c.strip().upper() for c in cfg("push_weather_codes").split(",") if c.strip()}
    feats = [f for f in wxalerts.geojson().get("features", []) if (f.get("properties") or {}).get("KIND") in codes]
    sent = 0
    for u in _members("weather_alert"):
        if not enabled(u["id"], "weather_alert"):
            continue
        for p in _places(u["id"]):
            for f in feats:
                pr = f["properties"]
                if not covers(f.get("geometry"), p["lat"], p["lon"], p["radius_km"]):
                    continue
                key = hashlib.sha1(f"{pr.get('event')}|{pr.get('headline')}|{pr.get('ends')}|{p['id']}".encode()).hexdigest()
                if _seen(u["id"], "weather_alert", key):
                    sent += 1 if notify(u["id"], "weather_alert", f"{pr.get('event')} near {p['name']}", pr.get("headline") or pr.get("area") or "", "/live?layers=wx-alerts", "wx-" + key[:8], urgency="high") else 0
    return sent


def _watch_quakes():
    from . import quakes
    if cfg("quakes_enabled") != "1":
        return 0
    floor = cfg_float("push_quake_min_mag")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
    feats = []
    for f in quakes.geojson().get("features", []):
        pr = f.get("properties") or {}
        try:
            when = datetime.fromisoformat(pr.get("time") or "")
        except ValueError:
            continue
        if (pr.get("mag") or 0) >= floor and when >= cutoff:
            feats.append((f, pr))
    sent = 0
    for u in _members("quake"):
        if not enabled(u["id"], "quake"):
            continue
        for p in _places(u["id"]):
            for f, pr in feats:
                lon, lat = f["geometry"]["coordinates"][:2]
                dist = _km(p["lat"], p["lon"], lat, lon)
                if dist <= p["radius_km"] and _seen(u["id"], "quake", f"{pr.get('time')}|{lat}|{lon}|{p['id']}"):
                    sent += 1 if notify(u["id"], "quake", f"M {pr['mag']:.1f} earthquake near {p['name']}", f"{pr.get('headline') or ''}, {dist:.0f} km away.", "/live?layers=quakes", "quake-" + str(pr.get("time")), urgency="high") else 0
    return sent


def _watch_rivers():
    from . import rivers
    if cfg("rivers_enabled") != "1":
        return 0
    order = ["OK", "ACT", "MIN", "MOD", "MAJ"]
    floor = order.index(cfg("push_river_min_category")) if cfg("push_river_min_category") in order else 2
    feats = [f for f in rivers.geojson().get("features", []) if order.index((f.get("properties") or {}).get("KIND", "OK")) >= floor]
    sent = 0
    for u in _members("river"):
        if not enabled(u["id"], "river"):
            continue
        for p in _places(u["id"]):
            for f in feats:
                pr = f["properties"]
                lon, lat = f["geometry"]["coordinates"][:2]
                if _km(p["lat"], p["lon"], lat, lon) <= p["radius_km"] and _seen(u["id"], "river", f"{pr.get('event')}|{pr.get('KIND')}|{p['id']}"):
                    sent += 1 if notify(u["id"], "river", f"{pr.get('event')}: {rivers.LABELS.get(pr.get('KIND'), '')}", pr.get("headline") or "", "/live?layers=rivers", "river-" + str(pr.get("event"))[:20]) else 0
    return sent


def _watch_satellites():
    from . import satpass
    if cfg("satpass_enabled") != "1":
        return 0
    lead = max(1, cfg_int("push_satpass_lead_minutes"))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    els = {e["norad"]: e for e in satpass.elements()}
    sent = 0
    d = get_db()
    for u in _members("satpass"):
        if not enabled(u["id"], "satpass"):
            continue
        norads = [r["norad"] for r in d.execute("SELECT norad FROM push_sats WHERE user_id=%s", (u["id"],)).fetchall() if r["norad"] in els]
        for p in _places(u["id"]):
            obs = satpass.observer(p["lat"], p["lon"])
            for n in norads:
                e = els[n]
                try:
                    ps = satpass.passes(satpass.satrec(e["line1"], e["line2"]), obs, now, (lead + 5) / 60.0, cfg_int("push_satpass_min_elev"))
                except Exception:
                    continue
                for ps1 in ps:
                    mins = (ps1["aos"] - now).total_seconds() / 60
                    if -1 <= mins <= lead and _seen(u["id"], "satpass", f"{n}|{ps1['aos'].strftime('%Y%m%d%H%M')}|{p['id']}"):
                        sent += 1 if notify(u["id"], "satpass", f"{e['name']} rises in {max(1, round(mins))} min", f"Over {p['name']}: up to {ps1['max_el']:.0f} degrees, rises in the {satpass.compass(ps1['az_aos'])}.", "/radio/satellites", "sat-" + str(n)) else 0
    return sent


def _watch_balloons():
    d = get_db()
    sent = 0
    for r in d.execute("SELECT f.user_id, f.serial, s.type, s.landing_lat, s.landing_lon FROM sonde_follows f JOIN sondes s ON s.serial = f.serial JOIN users u ON u.id = f.user_id "
                       "WHERE s.status='landed' AND s.last_seen > now() - interval '12 hours' AND NOT u.disabled").fetchall():
        if wants(r["user_id"], "balloon") and _seen(r["user_id"], "balloon", "landed|" + r["serial"]):
            where = f" near {r['landing_lat']:.3f}, {r['landing_lon']:.3f}" if r["landing_lat"] is not None else ""
            sent += 1 if notify(r["user_id"], "balloon", f"{r['serial']} has landed", f"The balloon you follow ({r['type'] or 'radiosonde'}) has landed{where}.", "/sondes/" + r["serial"], "balloon-" + r["serial"]) else 0
    return sent


def _watch_admin():
    from . import adminoverview
    from .models import User
    d = get_db()
    admins = [User(r) for r in d.execute("SELECT * FROM users WHERE NOT disabled AND EXISTS (SELECT 1 FROM push_subscriptions s WHERE s.user_id = users.id)").fetchall()]
    admins = [a for a in admins if a.has_admin_access and wants(a.id, "admin_attention")]
    if not admins:
        return 0
    items = [i for i in adminoverview.build(admins[0])["attention"] if i["level"] in ("warn", "down")]
    sent = 0
    for a in admins:
        for it in items:
            if _seen(a.id, "admin_attention", hashlib.sha1(it["text"].encode()).hexdigest()):
                sent += 1 if notify(a.id, "admin_attention", "Needs attention", it["text"], it["link"], "admin-" + hashlib.sha1(it["text"].encode()).hexdigest()[:8], urgency="high") else 0
    return sent


def watch_pass():
    """One pass over every place-based kind. Returns the number of notifications sent. A kind that fails does not stop the others."""
    sent = 0
    for fn in (_watch_weather, _watch_quakes, _watch_rivers, _watch_satellites, _watch_balloons, _watch_admin):
        try:
            sent += fn()
            get_db().commit()
        except Exception as e:
            get_db().rollback()
            print("notification watch:", fn.__name__, type(e).__name__, str(e)[:160], flush=True)
    get_db().execute("DELETE FROM push_seen WHERE ts < now() - interval '7 days'")
    get_db().execute("DELETE FROM push_sent WHERE ts < now() - interval '3 days'")
    get_db().commit()
    return sent


def start(app):
    import random

    def loop():
        time.sleep(60 + random.random() * 10)
        last = 0.0
        while True:
            try:
                with app.app_context():
                    wait = max(60, cfg_int("push_watch_seconds"))
                    if time.time() - last >= wait:
                        last = time.time()
                        d = get_db()
                        if d.execute("SELECT pg_try_advisory_xact_lock(727121) AS ok").fetchone()["ok"]:   # two web workers run this loop: one pass at a time
                            if cfg("push_enabled") == "1":
                                watch_pass()
                        d.commit()
            except Exception as e:
                print("notification watch:", type(e).__name__, str(e)[:160], flush=True)
            time.sleep(30)
    threading.Thread(target=loop, name="push-watch", daemon=True).start()
