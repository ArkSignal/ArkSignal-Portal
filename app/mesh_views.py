"""Mesh node pages: the overview list, the map, a node's page, the Join guide, and the administrators' page (service status, applying settings, the takedown list).
The logic is in mesh.py; the data comes from the mesh service container."""
import re

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import mesh as ms
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("mesh", __name__)
ID_RE = re.compile(r"^(?:!?[0-9a-fA-F]{8}|mc-[0-9a-fA-F]{64})$")
HIDE_RE = re.compile(r"^(?:!?[0-9a-fA-F]{8}|mc[:-][0-9a-fA-F]{64})$")


def _on():
    if cfg("mesh_enabled") != "1":
        abort(404)


@bp.route("/mesh/")
def overview():
    """The Mesh overview: live numbers from the mesh service, in the same form as the Weather overview. The list of nodes is on /mesh/nodes (the old search addresses still work and lead there)."""
    _on()
    if request.args:
        return redirect(url_for("mesh.nodes", **request.args), 301)
    return render_template("mesh.html", o=ms.overview(), credit=ms.CREDIT, resources=ms.RESOURCES, colors=ms.ROLE_COLORS, links_on=cfg("mesh_links_show") == "1", map_lat=cfg("map_lat"), map_lon=cfg("map_lon"))


@bp.route("/mesh/nodes")
def nodes():
    _on()
    a = request.args
    q = re.sub(r"[^A-Za-z0-9!_ .-]", "", a.get("q", ""))[:30].strip()
    role = a.get("role", "") if a.get("role", "").isdigit() else ""
    hours = min(max(int(a["hours"]), 1), 2160) if a.get("hours", "").isdigit() else 168
    net = a.get("network", "") if a.get("network", "") in ("meshtastic", "meshcore") else ""
    rows = ms.nodes_cached(q, role, hours, a.get("placed") == "1", net)
    return render_template("mesh_nodes.html", rows=rows[:400], total=len(rows), st=ms.stats(), args=a, q=q, role=role, hours=hours, network=net, credit=ms.CREDIT, roles=_roles(), colors=ms.ROLE_COLORS, resources=ms.RESOURCES)


def _roles():
    return [("0", "Client"), ("1", "Client (muted)"), ("2", "Router"), ("3", "Router and client"), ("4", "Repeater"), ("5", "Tracker"), ("6", "Sensor"), ("7", "TAK"), ("9", "Lost and found"), ("12", "Client (base)")]


@bp.route("/mesh/health")
def health_page():
    _on()
    return render_template("mesh_health.html", h=ms.health(), credit=ms.CREDIT)


@bp.route("/mesh/map")
def mesh_map():
    _on()
    from . import maplayers
    return render_template("mesh_map.html", layers=maplayers.for_map(), label_scale=cfg("live_label_scale"), label_bg=cfg("live_label_bg"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"),
                           wx_refresh=cfg_int("weather_refresh_minutes"), credit=ms.CREDIT, roles=_roles(), colors=ms.ROLE_COLORS, links_on=cfg("mesh_links_show") == "1")


@bp.route("/api/mesh/nodes.json")
def api_nodes():
    _on()
    slug = request.args.get("community", "")
    area = None
    if slug:
        from . import communities as cm
        c = cm.get(slug) if cfg("mesh_communities_enabled") == "1" else None
        if c is None or (not c["published"] and cm.rank_of(c) == 0):
            abort(404)
        rows = [n for n in cm.community_nodes(c) if n.get("lat") is not None]
        area = cm.polygon_of(c)
    else:
        rows = ms.nodes_cached("", "", 168, True)
    keep = ("id", "slug", "network", "long_name", "short_name", "lat", "lon", "role", "role_name", "hardware", "firmware", "last_seen", "battery", "precision_bits")
    return jsonify({"nodes": [{k: r.get(k) for k in keep} for r in rows], "area": area, "credit": ms.CREDIT})


@bp.route("/api/mesh/links.json")
def api_links():
    """The direct links nodes reported with Neighbor Info, where both ends have a position: for the Direct Links layer of the Mesh Map."""
    _on()
    return jsonify({"links": ms.placed_links(), "shown": cfg("mesh_links_show") == "1", "days": cfg_int("mesh_links_show_days"), "credit": ms.CREDIT,
                    "note": "From the Neighbor Info reports nodes send (only those a gateway passed on in the clear). A line means the two hear each other directly, not that they are the only way."})


@bp.route("/api/mesh/coverage.json")
def api_coverage():
    """Measured coverage per gateway or observer: how far it was heard to hear in each direction (not a prediction). Hidden nodes are left out."""
    _on()
    hid = {h["node_key"] for h in ms.hidden()}
    rows = [g for g in ms.coverage() if ("mc:" + g["id"][3:] if g["network"] == "meshcore" else "!" + g["id"]) not in hid]
    return jsonify({"gateways": rows, "credit": ms.CREDIT, "note": "Measured from what was heard, not a prediction of coverage."})


@bp.route("/api/mesh/<nid>.json")
def api_node(nid):
    _on()
    if not ID_RE.match(nid):
        abort(404)
    d = ms.node(nid if nid.startswith("mc-") else "!" + nid.lstrip("!"))
    if d is None:
        abort(404)
    return jsonify(d)


def _node_charts(nid, period):
    """[(title, svg, caption, legend)] for the readings a node reported: only the charts that have data, in the site's own chart style and the Display menu's units."""
    from datetime import datetime
    from . import weather as wx
    rows = ms.history(nid, int(wx.PERIODS[period][1].total_seconds() // 3600))
    if not rows:
        return []
    pts = [dict(r, t=datetime.fromisoformat(r["t"].replace("Z", "+00:00"))) for r in rows]
    units = wx.units_for(request.args.get("units") if request.args.get("units") in ("imperial", "metric") else None)
    imp = units != "metric"
    ident = lambda v: v
    ts = sorted(p["t"].timestamp() for p in pts)
    gaps = sorted(b - a for a, b in zip(ts, ts[1:]) if b > a)
    spacing = gaps[len(gaps) // 2] if gaps else None   # how often this node reports
    have = lambda *ks: any(p.get(k) is not None for p in pts for k in ks)
    out = []

    def add(title, lines, convert, unit, caption, zero=False, bars=False):
        if have(*[k for k, _c, _l in lines]):
            out.append((title, wx.chart_svg(pts, lines, period, convert, unit=unit, zero=zero, bars=bars, spacing=spacing), caption, [(c, l) for _k, c, l in lines] if len(lines) > 1 else []))
    add("Battery", [("battery", "var(--acc)", "Battery")], ident, "%", "Battery level (%); a node on external power reports 101", zero=True)
    add("Voltage", [("voltage", "var(--acc2)", "Voltage")], ident, "V", "Battery or supply voltage (V)")
    add("Channel Use", [("channel_util", "#ff9f43", "Channel use"), ("air_util", "var(--acc)", "Airtime used by this node")], ident, "%", "How busy the radio channel is around the node, and how much of it this node used (%)", zero=True)
    add("Temperature", [("temp_c", "var(--acc2)", "Temperature")], ident if not imp else (lambda v: v * 9 / 5 + 32), "°F" if imp else "°C", "Temperature (" + ("°F" if imp else "°C") + ")")
    add("Humidity", [("humidity", "#6fb3ff", "Humidity")], ident, "%", "Relative humidity (%)", zero=True)
    add("Pressure", [("pressure_hpa", "#c79bff", "Pressure")], (lambda v: v * 0.02953) if imp else ident, "inHg" if imp else "hPa", "Barometric pressure (" + ("inHg" if imp else "hPa") + ")")
    add("Wind", [("gust_ms", "#ff9f43", "Gust"), ("wind_ms", "var(--acc)", "Wind")], (lambda v: v * 2.236936) if imp else (lambda v: v * 3.6), "mph" if imp else "km/h", "Wind and gust (" + ("mph" if imp else "km/h") + ")", zero=True)
    add("Rain", [("rain_1h_mm", "#4fb3ff", "Rain in the last hour")], (lambda v: v / 25.4) if imp else ident, "in" if imp else "mm", "Rain in the last hour (" + ("in" if imp else "mm") + ")", zero=True, bars=True)
    add("Light", [("lux", "#ffd24f", "Light")], ident, "lux", "Light level (lux)", zero=True)
    add("Air Quality", [("iaq", "#3ee6a8", "Air quality index")], ident, "", "Indoor air quality index (lower is cleaner)", zero=True)
    return out


@bp.route("/api/mesh/<nid>/history.json")
def api_history(nid):
    _on()
    if not ID_RE.match(nid) or nid.startswith("mc-"):
        abort(404)
    hours = max(1, min(request.args.get("hours", 24, type=int), 24 * 365))
    rows = ms.history("!" + nid.lstrip("!"), hours)
    if rows is None:
        abort(404)
    return jsonify({"hours": hours, "rows": rows, "credit": ms.CREDIT})


@bp.route("/mesh/planning")
def planning():
    _on()
    p = ms.plan()
    if p is not None:
        hid = {h["node_key"] for h in ms.hidden()}
        for s in p["suggestions"]:
            s["nodes"] = [n for n in s["nodes"] if n["id"] not in hid]
    return render_template("mesh_planning.html", p=p, credit=ms.CREDIT)


@bp.route("/mesh/weather")
def mesh_weather():
    _on()
    from . import weather as wx
    units = wx.units_for(request.args.get("units") if request.args.get("units") in ("imperial", "metric") else None)
    return render_template("mesh_weather.html", rows=ms.weather(), imp=units != "metric", units=units, credit=ms.CREDIT)


@bp.route("/mesh/<nid>")
def node_page(nid):
    _on()
    if not ID_RE.match(nid):
        abort(404)
    d = ms.node(nid if nid.startswith("mc-") else "!" + nid.lstrip("!"))
    if d is None:
        abort(404)
    hid = {h["node_key"] for h in ms.hidden()}
    nb = [x for x in d.get("neighbors", []) if x["id"] not in hid] if cfg("mesh_links_show") == "1" else []
    from . import weather as wx
    period = request.args.get("period", "24h")
    if period not in wx.PERIODS:
        period = "24h"
    charts = [] if nid.startswith("mc-") else _node_charts("!" + nid.lstrip("!"), period)
    return render_template("mesh_node.html", charts=charts, keep_days=cfg_int("mesh_history_keep_days"), period=period, periods=wx.PERIODS, n=d["node"], heard=d["heard_by"], gateway_for=d["gateway_for"], neighbors=nb, owners=ms.owners_of_node(nid), credit=ms.CREDIT, colors=ms.ROLE_COLORS, my_claim=_my_claim(nid))


def _my_claim(nid):
    """The signed-in member's claim on this node ('pending' or 'claimed'), or None."""
    if not current_user.is_authenticated:
        return None
    key = ms.claim_key(nid if nid.startswith("mc-") else "!" + nid.lstrip("!"))
    r = get_db().execute("SELECT status FROM mesh_claims WHERE owner_id=%s AND node_key=%s", (current_user.id, key)).fetchone() if key else None
    return r["status"] if r else None


@bp.route("/feed/mesh")
def feed_mesh():
    _on()
    from .public import _feed_context
    return render_template("feed_mesh.html", fsub="mesh", host=cfg("mesh_join_host") or cfg("feed_host") or cfg("main_address"), port=cfg("mesh_join_port"),
                           topic=cfg("mesh_join_topic"), links_keep_days=cfg_int("mesh_links_keep_days"), configured=_connect()["open"], signed_in=current_user.is_authenticated, resources=ms.RESOURCES, **_feed_context())


# ------------------------------------------------------------------ administrators

@bp.route("/admin/mesh")
@permission_required("manage_map_data")
def admin():
    from . import mesh_alerts as ma
    oa = get_db().execute("SELECT COUNT(*) FILTER (WHERE ts > now() - interval '24 hours') AS day, COUNT(*) FILTER (WHERE ts > now() - interval '7 days') AS week, COUNT(*) FILTER (WHERE email_state='pending' OR webhook_state='pending' OR push_state='pending') AS waiting, "
                          "COUNT(*) FILTER (WHERE email_state='failed' OR webhook_state='failed' OR push_state='failed') AS failed, COUNT(DISTINCT user_id) AS people FROM mesh_alert_events").fetchone()
    from .mesh_channels import admin_rows as channel_rows
    from .meshrelease import status as _rel
    return render_template("admin_mesh.html", rel=_rel(), channels=channel_rows(), oa=oa, oa_on=ma.on(), oa_open=get_db().execute("SELECT COUNT(*) AS n FROM mesh_alert_state").fetchone()["n"], claims=ms.all_claims(), slug=ms.claim_slug, active="mesh", st=ms.stats(), hidden=ms.hidden(), url=cfg("mesh_url"), enabled=ms.enabled(), accounts=ms.all_accounts(), conn=_connect())


@bp.route("/admin/mesh/apply", methods=["POST"])
@permission_required("manage_map_data")
def admin_apply():
    ok, msg = ms.push_config()
    audit("mesh.apply", detail=msg[:150])
    return action_response(ok, msg, redirect_endpoint="mesh.admin")


def _key(text):
    """The key a node is hidden by: !a1b2c3d4 (Meshtastic) or mc:<public key> (MeshCore), or None."""
    t = text.strip()
    if not HIDE_RE.match(t):
        return None
    return ("mc:" + t[3:].lower()) if t.lower().startswith("mc") else "!" + t.lstrip("!").lower()


@bp.route("/admin/mesh/hide", methods=["POST"])
@permission_required("manage_map_data")
def admin_hide():
    """Take a node off every mesh page (a takedown: its owner asked). Kept here and sent to the service."""
    key = _key(request.form.get("node", ""))
    if key is None:
        return action_response(False, "A Meshtastic node ID is eight hexadecimal characters, for example !a1b2c3d4; a MeshCore node is mc: and its 64 character public key.", redirect_endpoint="mesh.admin")
    reason = request.form.get("reason", "").strip()[:200]
    get_db().execute("INSERT INTO mesh_hidden (node_key, reason, added_by) VALUES (%s,%s,%s) ON CONFLICT (node_key) DO UPDATE SET reason=EXCLUDED.reason", (key, reason, current_user.id))
    get_db().commit()
    ok, _d = ms.call("/hide", "POST", {"id": key, "reason": reason})
    from .cache import _store
    _store.clear()
    audit("mesh.hide", target=key[:40], detail=reason)
    return action_response(True, f"{key[:14]} is hidden from every mesh page." + ("" if ok else " The service did not answer; it will be told when you press Apply."), redirect_endpoint="mesh.admin")


@bp.route("/admin/mesh/unhide", methods=["POST"])
@permission_required("manage_map_data")
def admin_unhide():
    key = _key(request.form.get("node", ""))
    if key:
        get_db().execute("DELETE FROM mesh_hidden WHERE node_key=%s", (key,))
        get_db().commit()
        ms.call("/unhide", "POST", {"id": key})
        audit("mesh.unhide", target=key[:40])
    return action_response(True, "The node may be shown again once it is heard.", redirect_endpoint="mesh.admin")


# ------------------------------------------------------------------ members' broker logins (My Mesh Nodes)

def _owned(aid):
    """The member's own login; an administrator who manages feeders opens anyone's with the same pages (to help someone); what they do is in the audit log under their name."""
    r = ms.get_account(aid)
    if r is None or not (r["owner_id"] == current_user.id or current_user.has_permission("manage_map_data")):
        abort(404)
    return r


def _connect():
    host = cfg("mesh_join_host") or cfg("feed_host")
    st = ms.stats()
    tls = bool(st.get("ok") and (st.get("data") or {}).get("feed", {}).get("tls"))   # the broker found its certificate and opened the TLS listener
    return dict(host=host, port=cfg("mesh_join_port"), topic=cfg("mesh_join_topic"), open=bool(cfg("mesh_join_host")) or tls)


@bp.route("/my/mesh")
@login_required
def my_mesh():
    _on()
    rows = ms.accounts_of(current_user.id)
    lim = cfg_int("mesh_max_per_user")
    ms.check_claims(current_user.id)   # a claim whose node now carries its code completes by itself
    from . import mesh_alerts as ma
    d = get_db()
    alerts = d.execute("SELECT * FROM mesh_alert_events WHERE user_id=%s ORDER BY ts DESC LIMIT 25", (current_user.id,)).fetchall()
    d.execute("UPDATE mesh_alert_events SET seen=TRUE WHERE user_id=%s AND NOT seen", (current_user.id,))
    d.commit()
    from .mailer import smtp_configured
    hook = d.execute("SELECT webhook_url FROM users WHERE id=%s", (current_user.id,)).fetchone()
    na = dict(on=ma.on(), prefs=ma.prefs_of(current_user.id), events=alerts, kinds=ma.KINDS, smtp=smtp_configured(), webhook=bool(hook and hook["webhook_url"]), off_h=cfg_int("mesh_offline_hours"), low=cfg_int("mesh_low_battery_pct"))
    return render_template("my_mesh.html", na=na, claims=ms.claims_of(current_user.id), slug=ms.claim_slug, rows=rows, full=len(rows) >= lim, limit=lim, add_open=request.args.get("add") == "1" or not rows, conn=_connect())


@bp.route("/my/mesh/add", methods=["POST"])
@login_required
def my_mesh_add():
    _on()
    f = request.form
    aid, err = ms.create_account(current_user.id, current_user.username, f.get("label", ""), f.get("nodes", ""), bool(f.get("public")))
    if err:
        return action_response(False, err, redirect_endpoint="mesh.my_mesh")
    audit("mesh.account_create", target=str(aid))
    flash("Mesh login made. Its username and password are below: copy them into your node.", "success")
    return redirect(url_for("mesh.my_mesh_account", aid=aid))


@bp.route("/my/mesh/<int:aid>")
@login_required
def my_mesh_account(aid):
    _on()
    r = _owned(aid)
    managing = "" if r["owner_id"] == current_user.id else (r["owner_display"] or r["owner_name"])
    return render_template("my_mesh_account.html", r=r, password=ms.password_of(r), managing=managing, conn=_connect())


@bp.route("/my/mesh/<int:aid>/save", methods=["POST"])
@login_required
def my_mesh_save(aid):
    _on()
    _owned(aid)
    f = request.form
    d = get_db()
    d.execute("UPDATE mesh_accounts SET label=%s, nodes=%s, public=%s WHERE id=%s", (f.get("label", "").strip()[:60], f.get("nodes", "").strip()[:300], bool(f.get("public")), aid))
    d.commit()
    from .cache import _store
    _store.clear()
    audit("mesh.account_update", target=str(aid))
    return action_response(True, "Saved.", redirect_endpoint="mesh.my_mesh_account", redirect_kwargs={"aid": aid})


@bp.route("/my/mesh/<int:aid>/new-password", methods=["POST"])
@login_required
def my_mesh_new_password(aid):
    _on()
    _owned(aid)
    ok, err = ms.new_password(aid)
    audit("mesh.account_new_password", target=str(aid))
    return action_response(ok, "A new password was made. The old one no longer works: update your node." if ok else err, redirect_endpoint="mesh.my_mesh_account", redirect_kwargs={"aid": aid})


@bp.route("/my/mesh/<int:aid>/delete", methods=["POST"])
@login_required
def my_mesh_delete(aid):
    _on()
    r = _owned(aid)
    ms.delete_account(aid)
    audit("mesh.account_delete", target=str(aid), detail=r["username"])
    flash("Mesh login deleted. Nodes using it are refused from now on.", "success")
    return redirect(url_for("mesh.my_mesh") if r["owner_id"] == current_user.id else url_for("mesh.admin"))


@bp.route("/admin/mesh/accounts/add", methods=["POST"])
@permission_required("manage_map_data")
def admin_account_add():
    """A login made for a member (to help them): it is under their account and opens on their own page, where the password is ready to copy."""
    f = request.form
    q = f.get("username", "").strip()
    u = get_db().execute("SELECT id, username FROM users WHERE lower(username) = lower(%s) OR lower(email) = lower(%s)", (q, q)).fetchone()
    if u is None:
        return action_response(False, "No member has that user name or email address. Pick one from the list that appears as you type.", redirect_endpoint="mesh.admin")
    aid, err = ms.create_account(u["id"], u["username"], f.get("label", ""), f.get("nodes", ""), bool(f.get("public")), by_admin=True)
    if err:
        return action_response(False, err, redirect_endpoint="mesh.admin")
    audit("mesh.account_create", target=str(aid), detail=f"for {u['username']}, added by an administrator")
    flash(f"Mesh login made for {u['username']}. This is their page: copy the password from it.", "success")
    return redirect(url_for("mesh.my_mesh_account", aid=aid))


@bp.route("/admin/mesh/accounts/<int:aid>/<action>", methods=["POST"])
@permission_required("manage_map_data")
def admin_account_action(aid, action):
    r = ms.get_account(aid)
    if r is None or action not in ("disable", "enable", "delete"):
        abort(404)
    if action == "delete":
        ms.delete_account(aid)
        ok, err = True, ""
    else:
        ok, err = ms.set_disabled(aid, action == "disable")
    audit("mesh.account_" + action, target=str(aid), detail=r["username"])
    return action_response(ok, {"disable": "Login switched off: its node is refused now.", "enable": "Login switched on.", "delete": "Login deleted."}[action] if ok else err, redirect_endpoint="mesh.admin")


@bp.route("/admin/mesh/accounts/sync", methods=["POST"])
@permission_required("manage_map_data")
def admin_account_sync():
    ok, err = ms.sync_accounts()
    audit("mesh.accounts_sync", detail=err[:150])
    return action_response(ok, "The broker's logins now match this list." if ok else "The broker could not be reached: " + str(err), redirect_endpoint="mesh.admin")


# ------------------------------------------------------------------ claiming a node

def _claim_of(cid):
    r = get_db().execute("SELECT * FROM mesh_claims WHERE id=%s", (cid,)).fetchone()
    if r is None or not (r["owner_id"] == current_user.id or current_user.has_permission("manage_map_data")):
        abort(404)
    return r


@bp.route("/my/mesh/claim", methods=["POST"])
@login_required
def my_mesh_claim():
    _on()
    cid, err = ms.create_claim(current_user.id, request.form.get("node", ""))
    if err:
        return action_response(False, err, redirect_endpoint="mesh.my_mesh")
    audit("mesh.claim", target=str(cid))
    flash("Claim started. Put the code in the node's name, let it send its information, then press Check Now.", "success")
    return redirect(url_for("mesh.my_mesh") + "#sec-claims")


@bp.route("/my/mesh/claims/<int:cid>/check", methods=["POST"])
@login_required
def my_mesh_claim_check(cid):
    _on()
    r = _claim_of(cid)
    ms.check_claims(r["owner_id"])
    ok = get_db().execute("SELECT status FROM mesh_claims WHERE id=%s", (cid,)).fetchone()["status"] == "claimed"
    audit("mesh.claim_check", target=str(cid))
    return action_response(True, "The claim is complete: the node is yours." if ok else "The code is not in the node's name yet. Change the name on the node, wait for it to send (a few minutes), and press Check Now again.", redirect_endpoint="mesh.my_mesh")


@bp.route("/my/mesh/claims/<int:cid>/save", methods=["POST"])
@login_required
def my_mesh_claim_save(cid):
    _on()
    r = _claim_of(cid)
    if r["status"] != "claimed":
        return action_response(False, "Only a claimed node can be changed.", redirect_endpoint="mesh.my_mesh")
    get_db().execute("UPDATE mesh_claims SET public=%s WHERE id=%s", (bool(request.form.get("public")), cid))
    get_db().commit()
    hide = bool(request.form.get("hide"))
    if hide != bool(r["owner_hidden"]):
        ms.set_owner_hidden(r, hide)
    from .cache import _store
    _store.clear()
    audit("mesh.claim_update", target=str(cid))
    return action_response(True, "Saved.", redirect_endpoint="mesh.my_mesh")


@bp.route("/my/mesh/claims/<int:cid>/delete", methods=["POST"])
@login_required
def my_mesh_claim_delete(cid):
    _on()
    r = _claim_of(cid)
    if r["owner_hidden"]:
        ms.set_owner_hidden(r, False)
    get_db().execute("DELETE FROM mesh_claims WHERE id=%s", (cid,))
    get_db().commit()
    audit("mesh.claim_delete", target=str(cid), detail=r["node_key"][:20])
    return action_response(True, "Claim removed.", redirect_endpoint="mesh.my_mesh" if r["owner_id"] == current_user.id else "mesh.admin")


@bp.route("/admin/mesh/claims/add", methods=["POST"])
@permission_required("manage_map_data")
def admin_claim_add():
    """A node claimed for a member (to help them), approved at once."""
    f = request.form
    q = f.get("username", "").strip()
    u = get_db().execute("SELECT id, username FROM users WHERE lower(username) = lower(%s) OR lower(email) = lower(%s)", (q, q)).fetchone()
    if u is None:
        return action_response(False, "No member has that user name or email address. Pick one from the list that appears as you type.", redirect_endpoint="mesh.admin")
    cid, err = ms.create_claim(u["id"], f.get("node", ""), by_admin=True, approve=True)
    if err:
        return action_response(False, err, redirect_endpoint="mesh.admin")
    audit("mesh.claim", target=str(cid), detail=f"for {u['username']}, approved by an administrator")
    return action_response(True, f"The node is claimed for {u['username']}.", redirect_endpoint="mesh.admin")


@bp.route("/admin/mesh/claims/<int:cid>/approve", methods=["POST"])
@permission_required("manage_map_data")
def admin_claim_approve(cid):
    r = get_db().execute("SELECT * FROM mesh_claims WHERE id=%s", (cid,)).fetchone()
    if r is None:
        abort(404)
    if get_db().execute("SELECT 1 FROM mesh_claims WHERE node_key=%s AND status='claimed' AND id<>%s", (r["node_key"], cid)).fetchone():
        return action_response(False, "That node is already claimed by someone else.", redirect_endpoint="mesh.admin")
    get_db().execute("UPDATE mesh_claims SET status='claimed', claimed_at=now() WHERE id=%s", (cid,))
    get_db().commit()
    audit("mesh.claim_approve", target=str(cid))
    return action_response(True, "Claim approved.", redirect_endpoint="mesh.admin")
