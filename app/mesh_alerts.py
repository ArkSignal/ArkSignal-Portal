"""Owner alerts for mesh nodes: tell the member who owns a node when their router has gone quiet or its battery is low (and, if they want, when it is back).

Who owns a node: the member with a verified claim on it (My Mesh Nodes > My Nodes), or whose mesh login lists it. What counts: a router or repeater not heard for longer than Settings > Mesh Nodes > Health:
Router Quiet For, and any owned node reporting a battery at or below Health: Low Battery At or Below (the same limits as the Mesh Health page). Each member chooses which of the two to hear about, whether
to hear when it recovers, and whether it goes by email and by their webhook (the address on My Account > Aircraft Alerts); every alert is also kept in the list on My Mesh Nodes.
An alert is sent when the trouble starts, again after Repeat Alerts Every (Hours) while it lasts, at most Most Repeats times, and one "back" note when it clears (a battery has to rise a margin above the limit
first, so one hovering at the limit does not flip back and forth). Messages for a person are combined and limited per day. Nothing is guessed: a node the mesh service did not list is left alone."""
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

from . import mesh as ms
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int, get_setting

bp = Blueprint("mesh_alerts", __name__)

LOCK_ID = 727023
KINDS = {"quiet": "Router gone quiet", "battery": "Low battery"}
KEY_RE = re.compile(r"(?:mc[:-]([0-9a-fA-F]{64}))|(?:!?([0-9a-fA-F]{8}))")
PREFS = ("quiet", "battery", "recovered", "by_email", "by_webhook", "by_push")


def on():
    return ms.enabled() and cfg("mesh_owner_alerts") == "1"


def prefs_of(uid):
    r = get_db().execute("SELECT * FROM mesh_alert_prefs WHERE user_id=%s", (uid,)).fetchone()
    return dict(r) if r else {"quiet": True, "battery": True, "recovered": True, "by_email": True, "by_webhook": False, "by_push": False}


def owners():
    """{node key: set of user ids}: verified claims and the nodes named on members' mesh logins."""
    d = get_db()
    out = {}
    for r in d.execute("SELECT c.node_key, c.owner_id FROM mesh_claims c JOIN users u ON u.id = c.owner_id WHERE c.status='claimed' AND NOT u.disabled").fetchall():
        out.setdefault(r["node_key"], set()).add(r["owner_id"])
    for r in d.execute("SELECT a.nodes, a.owner_id FROM mesh_accounts a JOIN users u ON u.id = a.owner_id WHERE NOT a.disabled AND NOT u.disabled AND a.nodes <> ''").fetchall():
        for m in KEY_RE.finditer(r["nodes"]):
            key = ("mc:" + m.group(1).lower()) if m.group(1) else ("!" + m.group(2).lower())
            out.setdefault(key, set()).add(r["owner_id"])
    return out


def _age_h(n, now):
    try:
        return (now - datetime.fromisoformat(n["last_seen"].replace("Z", "+00:00"))).total_seconds() / 3600.0
    except (KeyError, ValueError, AttributeError):
        return 9e9


def _is_router(n):
    return n.get("role_name") in ("Router", "Router and client", "Router (late)", "Repeater")


def trouble(n, now):
    """{kind: detail} for what is wrong with this node right now (empty when nothing is)."""
    out = {}
    off_h, low = cfg_int("mesh_offline_hours"), cfg_int("mesh_low_battery_pct")
    age = _age_h(n, now)
    if _is_router(n) and age > off_h:
        out["quiet"] = f"not heard for {int(age)} hours" if age < 9e8 else "not heard recently"
    b = n.get("battery")
    if isinstance(b, int) and 0 < b <= low and age <= 48:
        out["battery"] = f"battery at {b}%"
    return out


def _label(n):
    return (n.get("long_name") or n.get("short_name") or n["id"]).strip() or n["id"]


def run_pass(now=None, rows=None):
    """One check of every owned node. Returns the number of alerts made. `rows` (a node list) is for tests."""
    if not on():
        return 0
    now = now or datetime.now(timezone.utc)
    if rows is None:
        rows = ms.nodes("", "", None, False)
        if not rows:
            return 0
    own = owners()
    if not own:
        return 0
    d = get_db()
    repeat_h, max_rep, margin = cfg_int("mesh_owner_alert_repeat_hours"), cfg_int("mesh_owner_alert_max_repeats"), cfg_int("mesh_owner_alert_battery_margin")
    email_ok = _smtp()
    made = 0
    seen_keys = {n["id"]: n for n in rows}
    for key, uids in own.items():
        n = seen_keys.get(key)
        if n is None:
            continue
        bad = trouble(n, now)
        for uid in uids:
            p = prefs_of(uid)
            u = d.execute("SELECT webhook_url FROM users WHERE id=%s", (uid,)).fetchone()
            for kind in KINDS:
                st = d.execute("SELECT * FROM mesh_alert_state WHERE user_id=%s AND node_key=%s AND kind=%s", (uid, key, kind)).fetchone()
                cond = kind in bad
                if cond and p[kind]:
                    if st is None:
                        _event(d, uid, key, n, kind, bad[kind], now, p, email_ok, u)
                        d.execute("INSERT INTO mesh_alert_state (user_id, node_key, kind, since, last_sent, repeats) VALUES (%s,%s,%s,%s,%s,0)", (uid, key, kind, now, now))
                        made += 1
                    elif st["repeats"] < max_rep and now - st["last_sent"] >= timedelta(hours=repeat_h):
                        _event(d, uid, key, n, kind, bad[kind] + " (still)", now, p, email_ok, u)
                        d.execute("UPDATE mesh_alert_state SET last_sent=%s, repeats=repeats+1 WHERE user_id=%s AND node_key=%s AND kind=%s", (now, uid, key, kind))
                        made += 1
                elif st is not None:
                    b = n.get("battery")
                    if kind == "battery" and p[kind] and isinstance(b, int) and 0 < b <= cfg_int("mesh_low_battery_pct") + margin:
                        continue   # just above the limit: not recovered yet
                    if p["recovered"] and p[kind] and not cond:
                        _event(d, uid, key, n, "back_" + kind, "heard again" if kind == "quiet" else f"battery back to {b}%", now, p, email_ok, u)
                        made += 1
                    d.execute("DELETE FROM mesh_alert_state WHERE user_id=%s AND node_key=%s AND kind=%s", (uid, key, kind))
    d.execute("DELETE FROM mesh_alert_events WHERE ts < now() - make_interval(days => %s)", (cfg_int("mesh_owner_alert_keep_days"),))
    d.commit()
    return made


def _smtp():
    from .mailer import smtp_configured
    return smtp_configured()


def _event(d, uid, key, n, kind, detail, now, p, email_ok, u):
    d.execute("INSERT INTO mesh_alert_events (user_id, node_key, node_name, kind, detail, ts, email_state, webhook_state, push_state) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (uid, key, _label(n)[:60], kind, detail[:120], now, "pending" if p["by_email"] and email_ok else "none", "pending" if p["by_webhook"] and u and u["webhook_url"] else "none", "pending" if p.get("by_push") else "none"))


def _lines(rows, site):
    out = []
    for r in rows:
        what = KINDS.get(r["kind"]) or ("Back: " + KINDS.get(r["kind"][5:], r["kind"][5:]))
        out.append(f"{r['ts'].astimezone(timezone.utc).strftime('%H:%M UTC')}  {r['node_name']} ({r['node_key']}): {what}, {r['detail']}")
    return out


def send_pending(now=None):
    """Email and webhook the waiting alerts, one combined message per person and way, within the daily limit. Returns messages sent."""
    from . import webhooks
    from .mailer import send_mail
    from .vault import unseal
    d = get_db()
    now = now or datetime.now(timezone.utc)
    combine, per_day = cfg_int("mesh_owner_alert_combine_minutes"), cfg_int("mesh_owner_alert_per_day")
    site = get_setting("site_name") or "ArkSignal"
    sent = 0
    d.execute("UPDATE mesh_alert_events SET email_state='expired' WHERE email_state='pending' AND ts < now() - interval '24 hours'")
    d.execute("UPDATE mesh_alert_events SET webhook_state='expired' WHERE webhook_state='pending' AND ts < now() - interval '24 hours'")
    d.execute("UPDATE mesh_alert_events SET push_state='expired' WHERE push_state='pending' AND ts < now() - interval '24 hours'")
    for way, col in (("email", "email_state"), ("webhook", "webhook_state"), ("push", "push_state")):
        users = d.execute(f"SELECT e.user_id, MIN(e.ts) AS first FROM mesh_alert_events e JOIN users u ON u.id = e.user_id WHERE e.{col}='pending' AND NOT u.disabled GROUP BY e.user_id").fetchall()
        for u in users:
            if now - u["first"] < timedelta(minutes=combine):
                continue
            if d.execute("SELECT COUNT(*) AS n FROM mesh_alert_sends WHERE user_id=%s AND way=%s AND ts > now() - interval '24 hours'", (u["user_id"], way)).fetchone()["n"] >= per_day:
                continue
            rows = d.execute(f"SELECT * FROM mesh_alert_events WHERE user_id=%s AND {col}='pending' ORDER BY ts", (u["user_id"],)).fetchall()
            lines = _lines(rows, site)
            text = f"Your mesh nodes on {site}:\n\n" + "\n".join(lines) + "\n\nChoose what you hear about under My Account > My Mesh Nodes > Node Alerts.\n"
            row = d.execute("SELECT email, webhook_url, webhook_kind FROM users WHERE id=%s", (u["user_id"],)).fetchone()
            try:
                if way == "push":
                    from . import webpush
                    if len(rows) == 1:
                        title, body = f"Mesh node: {KINDS.get(rows[0]['kind']) or 'Back: ' + KINDS.get(rows[0]['kind'][5:], rows[0]['kind'][5:])}", f"{rows[0]['node_name']}, {rows[0]['detail']}"
                    else:
                        title, body = f"{len(rows)} mesh node alerts", "; ".join(f"{r['node_name']}: {r['detail']}" for r in rows[:3]) + (f"; and {len(rows) - 3} more" if len(rows) > 3 else "")
                    from . import pushkinds
                    ok = pushkinds.wants(u["user_id"], "mesh_node") and webpush.notify_member(u["user_id"], title, body, "/my/mesh", "mesh-alert", urgency="high") > 0
                elif way == "email":
                    ok, _m = send_mail(row["email"], f"{site}: " + (lines[0].split(': ', 1)[1].split(',')[0] if len(rows) == 1 else f"{len(rows)} mesh node alerts"), text)
                else:
                    ok, _m = webhooks.send(unseal(row["webhook_url"]), row["webhook_kind"], text, [{"node": r["node_key"], "name": r["node_name"], "alert": r["kind"], "detail": r["detail"]} for r in rows], site)
            except Exception:
                ok = False
            d.execute("INSERT INTO mesh_alert_sends (user_id, way, events, ok) VALUES (%s,%s,%s,%s)", (u["user_id"], way, len(rows), bool(ok)))
            d.execute(f"UPDATE mesh_alert_events SET {col}=%s WHERE user_id=%s AND {col}='pending' AND id = ANY(%s)", ("sent" if ok else "failed", u["user_id"], [r["id"] for r in rows]))
            sent += 1 if ok else 0
    d.execute("DELETE FROM mesh_alert_sends WHERE ts < now() - interval '3 days'")
    d.commit()
    return sent


def start(app):
    import random

    def loop():
        time.sleep(40 + random.random() * 10)
        last = 0.0
        while True:
            try:
                with app.app_context():
                    wait = max(60, cfg_int("mesh_owner_alert_check_seconds"))
                    if time.time() - last >= wait:
                        last = time.time()
                        d = get_db()
                        if d.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (LOCK_ID,)).fetchone()["ok"]:   # two web workers run this loop: one pass at a time
                            run_pass()
                            send_pending()
                        d.commit()
            except Exception as e:
                print("mesh owner alerts:", type(e).__name__, str(e)[:160], flush=True)
            time.sleep(30)
    threading.Thread(target=loop, name="mesh-owner-alerts", daemon=True).start()


# ------------------------------------------------------------------ the member's choices

@bp.route("/my/mesh/alerts/save", methods=["POST"])
@login_required
def save():
    if not ms.enabled():
        abort(404)
    f = request.form
    vals = {k: bool(f.get(k)) for k in PREFS}
    d = get_db()
    d.execute("INSERT INTO mesh_alert_prefs (user_id, quiet, battery, recovered, by_email, by_webhook, by_push) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET quiet=EXCLUDED.quiet, battery=EXCLUDED.battery, "
              "recovered=EXCLUDED.recovered, by_email=EXCLUDED.by_email, by_webhook=EXCLUDED.by_webhook, by_push=EXCLUDED.by_push", (current_user.id, *[vals[k] for k in PREFS]))
    d.commit()
    audit("mesh.alert_prefs", target=current_user.username, detail=", ".join(k for k in PREFS if vals[k]) or "all off")
    from .action_response import action_response
    return action_response(True, "Saved.", redirect_endpoint="mesh_views.my_mesh")
