"""The mesh alert and weather channel: a community can have weather warnings and a regular weather summary carried out over its mesh.

How it works. A community's Manager (or Owner) picks one of its channels as the alert channel and turns the service on (Alert Channel page of the community). The site then writes short text messages (200 bytes at
most): the National Weather Service warnings that cover the community's area (the Weather Alerts layer's own data, nothing new is read) as they appear, and a summary of what the public weather stations inside the area
report, every few hours. The mesh service (deploy/mesh/meshd.py) builds a Meshtastic packet from each text, encrypts it with the channel's key (kept sealed here) and publishes it on the community's own topic at
<root>/<community>/2/e/<channel>/<the service's own gateway>. Only a node with downlink switched on for that channel and logged in with the community's downlink login sends it on the air; the service never writes
anywhere else (its broker login may publish under <root>/<community>/2/e/ only). Everything is switched off until the administrator turns the site switch on (Settings > Mesh Nodes) and a community turns its own on.
Limits: a daily cap and a minimum gap between messages for each community, a log of everything that went out (and why a message was held back), and an audit entry for each send. Nothing is sent twice: a warning is
remembered by its text and end time."""
import base64
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, abort
from flask_login import login_required

from . import mesh as ms
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("meshalertchan", __name__)
LOCK_ID = 727122
MAX_BYTES = 200


def site_on():
    return cfg("mesh_enabled") == "1" and cfg("meshalert_enabled") == "1"


def _tz():
    try:
        return ZoneInfo(cfg("timezone"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _clip(text, n=MAX_BYTES):
    """Text cut at a word end so its UTF-8 bytes are at most n, with an ellipsis when something was left out."""
    t = " ".join((text or "").split())
    if len(t.encode("utf-8")) <= n:
        return t
    while len((t + "...").encode("utf-8")) > n:
        t = t[:-1]
    cut = t.rsplit(" ", 1)
    if len(cut) == 2 and len(cut[0]) > len(t) * 0.6:
        t = cut[0]
    return t.rstrip(",;:.") + "..."


def _local(iso):
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00")).astimezone(_tz())
    except ValueError:
        return ""
    return d.strftime("%a %-I:%M %p")


# ------------------------------------------------------------------ what to say

def overlaps(geom, poly):
    """Whether an alert's outline and a community's area (a list of [lat, lon]) touch: a corner of one inside the other, or the area's middle inside the alert."""
    from .communities import in_polygon
    from .pushkinds import _in_ring, _polys
    if not poly or len(poly) < 3:
        return False
    mid = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    for rings in _polys(geom):
        outer = rings[0] if rings else []
        if len(outer) < 3:
            continue
        if _in_ring(mid[1], mid[0], outer) or any(_in_ring(p[1], p[0], outer) for p in poly) or any(in_polygon(y, x, poly) for x, y in outer):
            return True
    return False


def alerts_for(c):
    """The warnings (of the kinds the site sends) that cover a community's area: [{key, kind, text}]."""
    from . import wxalerts
    from .communities import polygon_of
    poly = polygon_of(c)
    codes = {x.strip().upper() for x in cfg("meshalert_codes").split(",") if x.strip()}
    out = []
    for f in wxalerts.geojson().get("features", []):
        p = f.get("properties") or {}
        if p.get("KIND") not in codes or not overlaps(f.get("geometry"), poly):
            continue
        until = _local(p.get("ends"))
        text = f"{p.get('event') or 'Weather alert'}" + (f" until {until}" if until else "") + (f" for {p.get('area')}" if p.get("area") else "") + ". Source: NWS"
        out.append({"key": hashlib.sha1(f"{p.get('event')}|{p.get('headline')}|{p.get('ends')}".encode()).hexdigest(), "kind": "nws", "text": _clip(text)})
    return out


def weather_summary(c):
    """One line of what the public weather stations inside the area report (their position is used only to find them, never sent), or '' when none has reported lately."""
    from .communities import in_polygon, polygon_of
    poly = polygon_of(c)
    if not poly:
        return ""
    rows = get_db().execute(
        "SELECT w.lat, w.lon, r.temp_f, r.hum, r.wind_mph, r.gust_mph, r.rain_day_in FROM wx_stations w JOIN LATERAL (SELECT * FROM wx_readings r WHERE r.station_pk = w.id ORDER BY ts DESC LIMIT 1) r ON TRUE "
        "WHERE NOT w.disabled AND w.public AND w.privacy <> 'hidden' AND w.lat IS NOT NULL AND r.ts > now() - make_interval(mins => %s)", (cfg_int("meshalert_station_minutes"),)).fetchall()
    rows = [r for r in rows if in_polygon(r["lat"], r["lon"], poly)]
    temps = [r["temp_f"] for r in rows if r["temp_f"] is not None]
    if not temps:
        return ""
    wind = [r["wind_mph"] for r in rows if r["wind_mph"] is not None]
    gust = [r["gust_mph"] for r in rows if r["gust_mph"] is not None]
    hum = [r["hum"] for r in rows if r["hum"] is not None]
    rain = [r["rain_day_in"] for r in rows if r["rain_day_in"] is not None]
    parts = [f"{round(sum(temps) / len(temps))}F" + (f" ({round(min(temps))} to {round(max(temps))})" if len(temps) > 1 else "")]
    if wind:
        parts.append(f"wind {round(sum(wind) / len(wind))} mph" + (f", gusts {round(max(gust))}" if gust and max(gust) >= 1 else ""))
    if hum:
        parts.append(f"humidity {round(sum(hum) / len(hum))}%")
    if rain and max(rain) > 0:
        parts.append(f"rain today {max(rain):.2f} in")
    now = datetime.now(timezone.utc).astimezone(_tz()).strftime("%-I:%M %p")
    return _clip(f"Weather {now}, {c['name']}: " + ", ".join(parts) + f" ({len(temps)} station{'s' if len(temps) != 1 else ''})")


# ------------------------------------------------------------------ sending, within the limits

def _channel(c):
    if not c["alert_channel_id"]:
        return None
    return get_db().execute("SELECT * FROM community_channels WHERE id=%s AND community_id=%s", (c["alert_channel_id"], c["id"])).fetchone()


def _log(c, kind, text, ok, key="", detail=""):
    get_db().execute("INSERT INTO mesh_chan_log (community_id, kind, text, ok, key, detail) VALUES (%s,%s,%s,%s,%s,%s)", (c["id"], kind, text, ok, key, detail[:200]))
    get_db().commit()


def send(c, kind, text, key="", force=False):
    """Send one message on a community's alert channel: (ok, message). Held back (and logged) when the community's daily cap is used up or the minimum gap has not passed; a test ignores the gap."""
    d = get_db()
    ch = _channel(c)
    if ch is None:
        return False, "Choose the channel the messages go on first."
    sent_day = d.execute("SELECT COUNT(*) AS n FROM mesh_chan_log WHERE community_id=%s AND ok AND ts > now() - interval '24 hours'", (c["id"],)).fetchone()["n"]
    if sent_day >= cfg_int("meshalert_daily_cap"):
        _log(c, kind, text, False, key, "held back: the daily limit is used up")
        return False, f"The daily limit of {cfg_int('meshalert_daily_cap')} messages is used up."
    last = d.execute("SELECT max(ts) AS t FROM mesh_chan_log WHERE community_id=%s AND ok", (c["id"],)).fetchone()["t"]
    if not force and last and (datetime.now(timezone.utc) - last).total_seconds() < cfg_int("meshalert_min_gap_seconds"):
        return False, "Too soon after the last message."   # not logged: it is tried again on the next pass
    from . import meshpacket
    from .communities import _psk
    raw = meshpacket.build_envelope(meshpacket.PORT_TEXT, " ".join(text.split()).encode("utf-8")[:MAX_BYTES], _psk(ch), ch["name"], int(cfg("meshalert_node_id"), 16), cfg("meshalert_gateway"))   # the channel key is used here and goes no further
    ok, resp = ms.call("/publish", "POST", {"community": c["slug"], "channel": ch["name"], "envelope_b64": base64.b64encode(raw).decode()}, timeout=15)
    good = bool(ok and isinstance(resp, dict) and resp.get("ok"))
    _log(c, kind, text, good, key, "" if good else (resp if isinstance(resp, str) else str((resp or {}).get("error") or "the service did not take it")))
    audit("mesh.alert_send", target=c["slug"], detail=f"{kind} on {ch['name']}: {'sent' if good else 'failed'}", actor_name="system" if kind != "test" else None)
    return good, "Sent." if good else "The mesh service could not send it."


def watch_pass():
    """One pass over every community that switched the channel on. Returns the number of messages sent."""
    if not site_on():
        return 0
    d = get_db()
    sent = 0
    for c in d.execute("SELECT * FROM communities WHERE alert_enabled AND alert_channel_id IS NOT NULL").fetchall():
        if c["alert_nws"]:
            for a in alerts_for(c):
                if d.execute("SELECT 1 FROM mesh_chan_log WHERE community_id=%s AND key=%s", (c["id"], a["key"])).fetchone():
                    continue
                ok, _m = send(c, "nws", a["text"], a["key"])
                if not ok and _m.startswith("Too soon"):
                    break
                sent += 1 if ok else 0
        hours = cfg_int("meshalert_weather_hours")
        if c["alert_weather"] and hours > 0:
            last = d.execute("SELECT max(ts) AS t FROM mesh_chan_log WHERE community_id=%s AND kind='weather'", (c["id"],)).fetchone()["t"]
            if last is None or datetime.now(timezone.utc) - last >= timedelta(hours=hours):
                text = weather_summary(c)
                if text:
                    ok, _m = send(c, "weather", text)
                    sent += 1 if ok else 0
        d.commit()
    d.execute("DELETE FROM mesh_chan_log WHERE ts < now() - make_interval(days => %s)", (cfg_int("meshalert_log_days"),))
    d.commit()
    return sent


def start(app):
    import random

    def loop():
        time.sleep(90 + random.random() * 10)
        last = 0.0
        while True:
            try:
                with app.app_context():
                    if time.time() - last >= max(60, cfg_int("meshalert_check_seconds")):
                        last = time.time()
                        d = get_db()
                        if d.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (LOCK_ID,)).fetchone()["ok"]:
                            watch_pass()
                        d.commit()
            except Exception as e:
                print("mesh alert channel:", type(e).__name__, str(e)[:160], flush=True)
            time.sleep(30)
    threading.Thread(target=loop, name="mesh-alert-channel", daemon=True).start()


# ------------------------------------------------------------------ the community's page for it

def page_context(c):
    d = get_db()
    chans = [dict(r) for r in d.execute("SELECT id, name, uplink, downlink FROM community_channels WHERE community_id=%s ORDER BY lower(name)", (c["id"],)).fetchall()]
    log = [dict(r) for r in d.execute("SELECT * FROM mesh_chan_log WHERE community_id=%s ORDER BY ts DESC LIMIT 30", (c["id"],)).fetchall()]
    sent_day = d.execute("SELECT COUNT(*) AS n FROM mesh_chan_log WHERE community_id=%s AND ok AND ts > now() - interval '24 hours'", (c["id"],)).fetchone()["n"]
    return {"site_on": site_on(), "channels": chans, "log": log, "cap": cfg_int("meshalert_daily_cap"), "sent_day": sent_day, "hours": cfg_int("meshalert_weather_hours"), "gap": cfg_int("meshalert_min_gap_seconds"),
            "codes": cfg("meshalert_codes"), "root": community_topic(c)}


def community_topic(c):
    return (cfg("mesh_join_topic").strip("/") or "msh") + "/" + c["slug"] + "/2/e/"


@bp.route("/mesh/c/<slug>/alerts", methods=["POST"])
@login_required
def save(slug):
    from flask import request
    from .communities import _back, _ctx
    c, _r = _ctx(slug, "manage_members")
    f = request.form
    d = get_db()
    cid = f.get("channel", type=int)
    if cid and d.execute("SELECT 1 FROM community_channels WHERE id=%s AND community_id=%s", (cid, c["id"])).fetchone() is None:
        return _back(slug, False, "Choose one of this community's channels.", "alerts")
    on = f.get("alert_enabled") == "1"
    if on and not cid:
        return _back(slug, False, "Choose the channel the messages go on before turning this on.", "alerts")
    d.execute("UPDATE communities SET alert_enabled=%s, alert_channel_id=%s, alert_nws=%s, alert_weather=%s WHERE id=%s", (on, cid, f.get("alert_nws") == "1", f.get("alert_weather") == "1", c["id"]))
    d.commit()
    audit("community.edit", target=slug, detail=f"alert channel: {'on' if on else 'off'}, nws {f.get('alert_nws') == '1'}, weather {f.get('alert_weather') == '1'}")
    return _back(slug, True, "The alert channel settings are saved.", "alerts")


@bp.route("/mesh/c/<slug>/alerts/test", methods=["POST"])
@login_required
def test(slug):
    from .communities import _back, _ctx
    c, _r = _ctx(slug, "manage_members")
    if not site_on():
        return _back(slug, False, "The site administrator has not turned the alert channel on.", "alerts")
    ok, msg = send(c, "test", _clip(f"Test message from {cfg('brand_name')}{cfg('brand_accent')} for {c['name']}. If you read this on your node, the alert channel works."), force=True)
    return _back(slug, ok, "The test message was sent on the channel." if ok else msg, "alerts")
