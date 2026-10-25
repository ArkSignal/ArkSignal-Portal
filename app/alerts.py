"""User alert rules on live aircraft: build rules, evaluate them against the hub's aircraft.json, log and email matches.

aircraft.json fields used (readsb): hex, flight (callsign), r (registration), t (type), squawk, alt_baro (feet or
"ground"), lat, lon, dbFlags (bit 0 = military), emergency.
"""
import json
import math
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from .settings import cfg_int, get_setting

KINDS = {
    "any": "Any aircraft",
    "icao": "Specific aircraft (ICAO hex)",
    "registration": "Registration (tail number)",
    "callsign_prefix": "Callsign starts with",
    "squawk": "Squawk code",
    "type": "Aircraft type",
    "military": "Military aircraft",
    "emergency": "Emergency (7500, 7600, 7700)",
}
NEEDS_VALUE = {"icao", "registration", "callsign_prefix", "squawk", "type"}
EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}


def miles_nm(lat1, lon1, lat2, lon2):
    """Great-circle distance in nautical miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3440.065 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _alt(ac):
    v = ac.get("alt_baro")
    if v == "ground":
        return 0
    return v if isinstance(v, (int, float)) else None


def matches(rule, ac):
    """True when one aircraft satisfies a rule. Pure function, no database access."""
    kind, val = rule["kind"], (rule["value"] or "").strip().upper()
    hexid = str(ac.get("hex", "")).lower().lstrip("~")
    if kind == "icao" and hexid != val.lower():
        return False
    if kind == "registration" and str(ac.get("r", "")).upper() != val:
        return False
    if kind == "callsign_prefix" and not str(ac.get("flight", "")).strip().upper().startswith(val):
        return False
    if kind == "squawk" and str(ac.get("squawk", "")) != val:
        return False
    if kind == "type" and str(ac.get("t", "")).upper() != val:
        return False
    if kind == "military" and not (int(ac.get("dbFlags", 0) or 0) & 1):
        return False
    if kind == "emergency" and not (str(ac.get("squawk", "")) in EMERGENCY_SQUAWKS or str(ac.get("emergency", "none")) not in ("none", "")):
        return False
    if rule.get("area_lat") is not None:
        if ac.get("lat") is None or ac.get("lon") is None:
            return False
        if miles_nm(rule["area_lat"], rule["area_lon"], ac["lat"], ac["lon"]) > rule["area_radius_nm"]:
            return False
    lo, hi = rule.get("alt_min_ft"), rule.get("alt_max_ft")
    if lo is not None or hi is not None:
        alt = _alt(ac)
        if alt is None or (lo is not None and alt < lo) or (hi is not None and alt > hi):
            return False
    return True


def validate_rule(form, default_cooldown):
    """Return (values, error). Values map to alert_rules columns."""
    kind = form.get("kind", "")
    if kind not in KINDS:
        return None, "Choose what to watch for."
    name = form.get("name", "").strip()[:60]
    value = form.get("value", "").strip().upper()
    if kind in NEEDS_VALUE:
        patterns = {"icao": (r"^[0-9A-F]{6}$", "An ICAO hex code is six characters, 0-9 and A-F."),
                    "registration": (r"^[A-Z0-9-]{2,10}$", "Enter a registration such as N12345."),
                    "callsign_prefix": (r"^[A-Z0-9]{1,8}$", "Enter the start of a callsign, such as AAL or N123."),
                    "squawk": (r"^[0-7]{4}$", "A squawk code is four digits from 0 to 7."),
                    "type": (r"^[A-Z0-9]{2,4}$", "Enter an aircraft type code such as B738 or C172.")}
        pat, msg = patterns[kind]
        if not re.match(pat, value):
            return None, msg
    else:
        value = ""
    if not name:
        name = KINDS[kind] + (f": {value}" if value else "")

    def num(key, lo, hi, label):
        raw = form.get(key, "").strip()
        if not raw:
            return None
        try:
            n = float(raw)
        except ValueError:
            raise ValueError(f"{label} must be a number.")
        if not (lo <= n <= hi):
            raise ValueError(f"{label} must be between {lo:g} and {hi:g}.")
        return n

    try:
        lat, lon, rad = num("area_lat", -90, 90, "Latitude"), num("area_lon", -180, 180, "Longitude"), num("area_radius_nm", 1, 500, "Radius")
        if any(v is not None for v in (lat, lon, rad)) and not all(v is not None for v in (lat, lon, rad)):
            return None, "For an area, enter the latitude, longitude and radius together, or leave all three blank."
        lo, hi = num("alt_min_ft", -2000, 100000, "Minimum altitude"), num("alt_max_ft", -2000, 100000, "Maximum altitude")
        cooldown = int(num("cooldown_minutes", 1, 1440, "Repeat interval") or default_cooldown)
    except ValueError as e:
        return None, str(e)
    if lo is not None and hi is not None and lo > hi:
        return None, "The minimum altitude cannot be above the maximum."
    if kind == "any" and lat is None and lo is None and hi is None:
        return None, "Watching for any aircraft needs an area or an altitude range, otherwise every aircraft would alert."
    return {"name": name, "kind": kind, "value": value, "area_lat": lat, "area_lon": lon, "area_radius_nm": rad,
            "alt_min_ft": lo, "alt_max_ft": hi, "cooldown_minutes": cooldown,
            "notify_email": bool(form.get("notify_email")), "notify_webhook": bool(form.get("notify_webhook")), "notify_push": bool(form.get("notify_push"))}, None


# ---------------------------------------------------------------- evaluation loop

def snapshot(ac):
    alt = _alt(ac)
    return {"hex": str(ac.get("hex", "")).lower(), "callsign": str(ac.get("flight", "")).strip() or None,
            "registration": ac.get("r"), "type": ac.get("t"), "alt": alt, "lat": ac.get("lat"), "lon": ac.get("lon"),
            "squawk": ac.get("squawk"), "gs": ac.get("gs")}


def run_tick(app, now=None, aircraft=None):
    """One evaluation pass. Returns the number of new alert events. `aircraft` is for tests."""
    hub = app.config.get("HUB_URL", "")
    aircraft_given = aircraft is not None
    if aircraft is None:
        if not hub:
            return 0
        with urllib.request.urlopen(hub.rstrip("/") + "/data/aircraft.json", timeout=6) as r:
            aircraft = json.loads(r.read(20_000_000)).get("aircraft", [])
    now = now or datetime.now(timezone.utc)
    url = app.config["DATABASE_URL"]
    new_events = []
    with psycopg.connect(url, autocommit=True) as lock_conn:
        if aircraft_given:   # a caller that hands in the aircraft (the test suite) waits its turn instead of skipping when the background pass holds the lock
            lock_conn.execute("SET lock_timeout = '30s'")
            lock_conn.execute("SELECT pg_advisory_lock(727007)")
        elif not lock_conn.execute("SELECT pg_try_advisory_lock(727007)").fetchone()[0]:
            return 0
        try:
            with psycopg.connect(url, row_factory=dict_row) as conn:
                rules = conn.execute(
                    "SELECT r.*, u.email, u.username, u.disabled, u.webhook_url FROM alert_rules r JOIN users u ON u.id = r.user_id "
                    "WHERE r.enabled AND NOT u.disabled").fetchall()
                for rule in rules:
                    for ac in aircraft:
                        if not ac.get("hex") or not matches(rule, ac):
                            continue
                        snap = snapshot(ac)
                        last = conn.execute("SELECT last_alert FROM alert_state WHERE rule_id=%s AND hex=%s",
                                            (rule["id"], snap["hex"])).fetchone()
                        if last and (now - last["last_alert"]).total_seconds() < rule["cooldown_minutes"] * 60:
                            continue
                        conn.execute("INSERT INTO alert_state (rule_id, hex, last_alert) VALUES (%s,%s,%s) "
                                     "ON CONFLICT (rule_id, hex) DO UPDATE SET last_alert=EXCLUDED.last_alert",
                                     (rule["id"], snap["hex"], now))
                        conn.execute("INSERT INTO alert_events (rule_id, user_id, ts, hex, callsign, registration, type, alt_ft, lat, lon, squawk, email_state, webhook_state, push_state) "
                                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                     (rule["id"], rule["user_id"], now, snap["hex"], snap["callsign"], snap["registration"],
                                      snap["type"], snap["alt"], snap["lat"], snap["lon"], snap["squawk"],
                                      "pending" if rule["notify_email"] else "none",
                                      "pending" if rule["notify_webhook"] and rule["webhook_url"] else "none",
                                      "pending" if rule["notify_push"] else "none"))
                        new_events.append((rule, snap))
                conn.execute("DELETE FROM alert_state WHERE last_alert < now() - interval '2 days'")
                conn.commit()
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(727007)")
    return len(new_events)


def email_usage(conn, user_id):
    """(emails sent in the last hour, in the last 24 hours, matches waiting) for one user."""
    r = conn.execute(
        "SELECT COUNT(*) FILTER (WHERE ts > now() - interval '1 hour') AS hour, COUNT(*) AS day FROM alert_emails "
        "WHERE user_id=%s AND ts > now() - interval '24 hours'", (user_id,)).fetchone()
    waiting = conn.execute("SELECT COUNT(*) AS n FROM alert_events WHERE user_id=%s AND email_state='pending'", (user_id,)).fetchone()
    get = (lambda row, k, i: row[k] if isinstance(row, dict) else row[i])
    return get(r, "hour", 0), get(r, "day", 1), get(waiting, "n", 0)


def _email_body(site, rows, hidden):
    lines = [f"{len(rows) + hidden} aircraft matched your alerts on {site}:", ""]
    by_rule = {}
    for r in rows:
        by_rule.setdefault(r["rule_name"], []).append(r)
    for rule, items in by_rule.items():
        lines.append(f"{rule}")
        for r in items:
            who = r["callsign"] or r["registration"] or r["hex"].upper()
            alt = f"{r['alt_ft']:.0f} ft" if r["alt_ft"] is not None else "altitude unknown"
            when = r["ts"].astimezone(timezone.utc).strftime("%H:%M UTC")
            lines.append(f"  {when}  {who} ({r['hex'].upper()})  {r['type'] or 'type unknown'}  {alt}  squawk {r['squawk'] or 'none'}")
        lines.append("")
    if hidden:
        lines += [f"...and {hidden} more. All matches are listed under Aircraft Alerts on the site.", ""]
    lines.append("You can change or turn off these alerts under My Feeders > Aircraft Alerts.")
    return "\n".join(lines) + "\n"


def send_digests(app, now=None):
    """Email waiting matches, one combined message per person, within the per-hour and per-day limits. Returns emails sent."""
    from .mailer import send_mail, smtp_configured
    sent = 0
    with app.app_context():
        if not smtp_configured():
            return 0
        per_hour, per_day = cfg_int("alert_email_per_hour"), cfg_int("alert_email_per_day")
        combine, max_items = cfg_int("alert_email_combine_minutes"), cfg_int("alert_email_max_items")
        site = get_setting("site_name") or "ArkSignal"
        with psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row) as conn:
            conn.execute("UPDATE alert_events SET email_state='expired' WHERE email_state='pending' AND ts < now() - interval '24 hours'")
            users = conn.execute(
                "SELECT e.user_id, u.email, MIN(e.ts) AS first FROM alert_events e JOIN users u ON u.id = e.user_id "
                "WHERE e.email_state='pending' AND NOT u.disabled GROUP BY e.user_id, u.email").fetchall()
            for u in users:
                t = now or datetime.now(timezone.utc)
                if (t - u["first"]).total_seconds() < combine * 60:
                    continue                        # still collecting matches that arrive close together
                hour, day, _ = email_usage(conn, u["user_id"])
                if hour >= per_hour or day >= per_day:
                    continue                        # over the limit: the matches wait for a later email
                rows = conn.execute(
                    "SELECT e.*, r.name AS rule_name FROM alert_events e JOIN alert_rules r ON r.id = e.rule_id "
                    "WHERE e.user_id=%s AND e.email_state='pending' ORDER BY e.ts", (u["user_id"],)).fetchall()
                shown, hidden = rows[:max_items], max(0, len(rows) - max_items)
                subject = f"{site} alert: " + (f"{len(rows)} aircraft" if len(rows) > 1 else (shown[0]["callsign"] or shown[0]["registration"] or shown[0]["hex"].upper()))
                try:
                    ok, _msg = send_mail(u["email"], subject, _email_body(site, shown, hidden))
                except Exception:
                    ok = False
                conn.execute("INSERT INTO alert_emails (user_id, events, ok) VALUES (%s,%s,%s)", (u["user_id"], len(rows), ok))
                if ok:
                    conn.execute("UPDATE alert_events SET email_state='sent' WHERE user_id=%s AND email_state='pending' AND id = ANY(%s)",
                                 (u["user_id"], [r["id"] for r in rows]))
                    sent += 1
            conn.commit()
    return sent


def webhook_usage(conn, user_id):
    """(messages sent in the last hour, in the last 24 hours) for one user."""
    r = conn.execute("SELECT COUNT(*) FILTER (WHERE ts > now() - interval '1 hour') AS hour, COUNT(*) AS day FROM alert_webhooks WHERE user_id=%s AND ts > now() - interval '24 hours'", (user_id,)).fetchone()
    return (r["hour"], r["day"]) if isinstance(r, dict) else (r[0], r[1])


def send_webhooks(app, now=None):
    """Send waiting matches to each person's webhook, one combined message per person, within the limits. Returns messages sent."""
    from . import webhooks
    sent = 0
    with app.app_context():
        per_hour, per_day, combine = cfg_int("webhook_per_hour"), cfg_int("webhook_per_day"), cfg_int("webhook_combine_minutes")
        max_items = cfg_int("alert_email_max_items")
        site = get_setting("site_name") or "ArkSignal"
        with psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row) as conn:
            conn.execute("UPDATE alert_events SET webhook_state='expired' WHERE webhook_state='pending' AND ts < now() - interval '24 hours'")
            users = conn.execute(
                "SELECT e.user_id, u.webhook_url, u.webhook_kind, MIN(e.ts) AS first FROM alert_events e JOIN users u ON u.id = e.user_id "
                "WHERE e.webhook_state='pending' AND NOT u.disabled GROUP BY e.user_id, u.webhook_url, u.webhook_kind").fetchall()
            for u in users:
                t = now or datetime.now(timezone.utc)
                if (t - u["first"]).total_seconds() < combine * 60:
                    continue
                hour, day = webhook_usage(conn, u["user_id"])
                if hour >= per_hour or day >= per_day:
                    continue
                rows = conn.execute(
                    "SELECT e.*, r.name AS rule_name FROM alert_events e JOIN alert_rules r ON r.id = e.rule_id "
                    "WHERE e.user_id=%s AND e.webhook_state='pending' ORDER BY e.ts", (u["user_id"],)).fetchall()
                shown, hidden = rows[:max_items], max(0, len(rows) - max_items)
                matches = [{"rule": r["rule_name"], "callsign": r["callsign"], "registration": r["registration"], "hex": r["hex"], "type": r["type"],
                            "altitude_ft": r["alt_ft"], "lat": r["lat"], "lon": r["lon"], "squawk": r["squawk"], "time": r["ts"].astimezone(timezone.utc).isoformat()} for r in shown]
                from .vault import unseal
                ok, _msg = webhooks.send(unseal(u["webhook_url"]), u["webhook_kind"], _email_body(site, shown, hidden), matches, site)
                conn.execute("INSERT INTO alert_webhooks (user_id, events, ok) VALUES (%s,%s,%s)", (u["user_id"], len(rows), ok))
                conn.execute("UPDATE alert_events SET webhook_state=%s WHERE user_id=%s AND webhook_state='pending' AND id = ANY(%s)",
                             ("sent" if ok else "failed", u["user_id"], [r["id"] for r in rows]))
                sent += 1 if ok else 0
            conn.commit()
    return sent


def push_usage(conn, user_id):
    """(notifications sent in the last hour, in the last 24 hours) for one user."""
    r = conn.execute("SELECT COUNT(*) FILTER (WHERE ts > now() - interval '1 hour') AS hour, COUNT(*) AS day FROM alert_pushes WHERE user_id=%s AND ts > now() - interval '24 hours'", (user_id,)).fetchone()
    return (r["hour"], r["day"]) if isinstance(r, dict) else (r[0], r[1])


def push_text(rows, total=None):
    """(title, body) for one alert notification: one match names it, several are counted."""
    total = total or len(rows)

    def what(r):
        return (r["callsign"] or r["registration"] or r["hex"].upper()) + (f" ({r['type']})" if r["type"] else "") + (f" at {int(r['alt_ft']):,} ft" if r["alt_ft"] is not None else "")
    if total == 1:
        return "Aircraft alert: " + rows[0]["rule_name"], what(rows[0])
    names = ", ".join(dict.fromkeys(r["rule_name"] for r in rows))[:80]
    return f"{total} aircraft alerts", "; ".join(what(r) for r in rows[:3]) + (f"; and {total - 3} more" if total > 3 else "") + f" ({names})"


def send_pushes(app, now=None):
    """Notify each person's phones and computers of waiting matches, one combined notification per person, within the limits. Returns notifications sent."""
    from . import webpush
    sent = 0
    with app.app_context():
        if not webpush.on():
            return 0
        per_hour, per_day, combine = cfg_int("alert_push_per_hour"), cfg_int("alert_push_per_day"), cfg_int("alert_push_combine_minutes")
        with psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row) as conn:
            conn.execute("UPDATE alert_events SET push_state='expired' WHERE push_state='pending' AND ts < now() - interval '24 hours'")
            users = conn.execute("SELECT e.user_id, MIN(e.ts) AS first FROM alert_events e JOIN users u ON u.id = e.user_id WHERE e.push_state='pending' AND NOT u.disabled GROUP BY e.user_id").fetchall()
            for u in users:
                t = now or datetime.now(timezone.utc)
                if (t - u["first"]).total_seconds() < combine * 60:
                    continue
                hour, day = push_usage(conn, u["user_id"])
                if hour >= per_hour or day >= per_day:
                    continue
                from . import pushkinds
                if not pushkinds.wants(u["user_id"], "aircraft"):   # the member (or the site) has aircraft notifications off: the matches stay on the Aircraft Alerts page
                    conn.execute("UPDATE alert_events SET push_state='none' WHERE user_id=%s AND push_state='pending'", (u["user_id"],))
                    continue
                subs = conn.execute("SELECT * FROM push_subscriptions WHERE user_id=%s", (u["user_id"],)).fetchall()
                rows = conn.execute("SELECT e.*, r.name AS rule_name FROM alert_events e JOIN alert_rules r ON r.id = e.rule_id WHERE e.user_id=%s AND e.push_state='pending' ORDER BY e.ts", (u["user_id"],)).fetchall()
                ids = [r["id"] for r in rows]
                if not subs:   # nowhere to send: the match stays on the Aircraft Alerts page and is not retried
                    conn.execute("UPDATE alert_events SET push_state='failed' WHERE user_id=%s AND push_state='pending' AND id = ANY(%s)", (u["user_id"], ids))
                    continue
                title, body = push_text(rows[:3], len(rows))
                oks = []
                for s in subs:
                    ok, status = webpush.send(s, {"title": title, "body": body, "url": "/my/alerts", "tag": "aircraft-alert"}, urgency="high")
                    oks.append(ok)
                    if ok:
                        conn.execute("UPDATE push_subscriptions SET last_ok=now() WHERE id=%s", (s["id"],))
                    elif status in (404, 410):   # the device turned notifications off or was removed
                        conn.execute("DELETE FROM push_subscriptions WHERE id=%s", (s["id"],))
                ok = any(oks)
                conn.execute("INSERT INTO alert_pushes (user_id, events, ok) VALUES (%s,%s,%s)", (u["user_id"], len(rows), ok))
                conn.execute("UPDATE alert_events SET push_state=%s WHERE user_id=%s AND push_state='pending' AND id = ANY(%s)", ("sent" if ok else "failed", u["user_id"], ids))
                sent += 1 if ok else 0
            conn.execute("DELETE FROM alert_pushes WHERE ts < now() - interval '3 days'")
            conn.commit()
    return sent


def prune(app):
    """Old alert events follow the history retention setting; the lists of what was sent (kept only to count a day's limit) go after three days."""
    with app.app_context():
        with psycopg.connect(app.config["DATABASE_URL"]) as conn:
            for t in ("alert_emails", "alert_webhooks"):
                conn.execute(f"DELETE FROM {t} WHERE ts < now() - interval '3 days'")
            conn.commit()
        days = cfg_int("history_retention_days")
        if days <= 0:
            return
        with psycopg.connect(app.config["DATABASE_URL"]) as conn:
            conn.execute("DELETE FROM alert_events WHERE ts < now() - make_interval(days => %s)", (days,))
            conn.commit()


def start_alerts(app):
    def loop():
        time.sleep(15)
        n = 0
        while True:
            try:
                run_tick(app)
            except Exception:
                pass
            try:
                with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock_conn:   # one worker sends at a time
                    if lock_conn.execute("SELECT pg_try_advisory_lock(727008)").fetchone()[0]:
                        try:
                            send_digests(app)
                            send_webhooks(app)
                            send_pushes(app)
                        finally:
                            lock_conn.execute("SELECT pg_advisory_unlock(727008)")
            except Exception:
                pass
            n += 1
            if n % 240 == 0:  # about hourly
                try:
                    prune(app)
                except Exception:
                    pass
            with app.app_context():
                wait = cfg_int("alert_poll_seconds")
            time.sleep(wait)
    threading.Thread(target=loop, name="alerts", daemon=True).start()
