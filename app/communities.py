"""Mesh Communities: a named mesh (Little Rock, Northwest Arkansas, Hot Springs, ...) with its own page, map, node list, news board and a small team that runs it.

Roles are scoped to one community. Each member of a community holds one role there (Viewer, Node Operator, Editor, Manager or Owner) and each role is a set of permissions that apply to that community only
(ROLE_PERMS). A site administrator (Manage Map Data) creates a community and names its Owner, and can always step in. The Owner promotes the other roles from the
site's members (a user name, call sign or whole email address), or sends an invitation link that can be accepted or declined and expires; nobody can give a role higher than their own, and the Owner can
hand ownership to another member. Which nodes count as the community's: those inside its area (a box), those claimed by its members, and those added by hand, less those taken off by hand. A community's
Node Operators and Managers can make broker logins for the community's gateways (uplink only; the site's rules and loop guards stay with the administrators), and its team sees its health and statistics.
Everything is audit-logged and the tables are in the mesh restore area."""
import base64
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import mesh as ms
from . import mesh_channels as mc
from . import social, vault
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("communities", __name__)

ROLES = [("viewer", "Viewer", 1), ("operator", "Node Operator", 2), ("editor", "Editor", 3), ("manager", "Manager", 4), ("owner", "Owner", 5)]
RANK = {k: r for k, _l, r in ROLES}
LABEL = {k: l for k, l, _r in ROLES}
SITE_ADMIN = 9
# what each role can do inside one community (the roles are not a ladder: an Editor runs the page, a Node Operator runs the nodes; the ranks only decide who may give which role)
_ALL = {"view_stats", "choose_nodes", "manage_logins", "post", "edit_page", "manage_members", "queue_commands", "approve_commands", "manage_bridges", "manage_channels"}
ROLE_PERMS = {"viewer": {"view_stats"}, "operator": {"view_stats", "choose_nodes", "manage_logins", "queue_commands", "manage_channels"}, "editor": {"view_stats", "post", "edit_page"}, "manager": set(_ALL), "owner": set(_ALL)}
BY_RANK = {r: k for k, _l, r in ROLES}
PERM_TEXT = {"view_stats": "See the community's health and statistics", "choose_nodes": "Choose which nodes count as the community's", "manage_logins": "Make and remove broker logins for its gateways",
             "post": "Post notices and events", "edit_page": "Edit the page, its links and its area", "manage_members": "Add, promote and remove members, and send invitations",
             "queue_commands": "Queue remote administration commands for the community's nodes", "approve_commands": "Approve the commands that change a node", "manage_bridges": "Make and remove Admin Bridges", "manage_channels": "List, change and remove the community's channels (with their QR codes)"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
ROUTERS = ("Router", "Router and client", "Router (late)", "Repeater")


def _on():
    if not ms.enabled() or cfg("mesh_communities_enabled") != "1":
        abort(404)


def is_site_admin():
    return current_user.is_authenticated and current_user.has_permission("manage_map_data")


def get(slug):
    return get_db().execute("SELECT * FROM communities WHERE slug=%s", (slug,)).fetchone()


def rank_of(c):
    """What the person making this request may do in the community: SITE_ADMIN for an administrator, else the rank of their role there, else 0."""
    if not current_user.is_authenticated:
        return 0
    if is_site_admin():
        return SITE_ADMIN
    r = get_db().execute("SELECT role FROM community_members WHERE community_id=%s AND user_id=%s", (c["id"], current_user.id)).fetchone()
    return RANK.get(r["role"], 0) if r else 0


def can(rank, perm):
    return rank >= SITE_ADMIN or perm in ROLE_PERMS.get(BY_RANK.get(rank, ""), ())


def _ctx(slug, perm=None):
    _on()
    c = get(slug)
    if c is None:
        abort(404)
    rank = rank_of(c)
    if not c["published"] and rank == 0:
        abort(404)
    if perm and not can(rank, perm):
        abort(403)
    return c, rank


# the pages of a community: key, name, the permission that opens it (None: everyone who may see the community; "team": any team member), group
SECTIONS = [("overview", "Overview", None, "Community"), ("join", "How to Join", None, "Community"), ("map", "Map", None, "Community"), ("nodes", "Nodes", None, "Community"),
            ("news", "News and Events", None, "Community"), ("channels", "Channels", None, "Community"), ("health", "Health and Statistics", "view_stats", "Community"),
            ("edit", "Edit Page", "edit_page", "Manage"), ("area", "Area", "edit_page", "Manage"), ("pick", "Choose the Nodes", "choose_nodes", "Manage"), ("team", "Team", "team", "Manage"),
            ("logins", "Broker Logins", "manage_logins", "Manage"), ("share", "Sharing", "manage_members", "Manage"), ("alerts", "Alert Channel", "manage_members", "Manage"), ("links", "Linked Areas", "manage_members", "Manage"), ("remote", "Remote Administration", "queue_commands", "Manage")]
SECTION_KEYS = {k for k, _l, _p, _g in SECTIONS}


def menu_for(rank):
    """[(group, [(key, label)])] of the pages this person may open."""
    groups = {}
    for key, label, perm, group in SECTIONS:
        if perm is None or (perm == "team" and rank >= 1) or (perm not in (None, "team") and (can(rank, perm) or (key == "remote" and can(rank, "manage_bridges")))):
            if key == "remote" and cfg("mesh_bridge_enabled") != "1":
                continue
            groups.setdefault(group, []).append((key, label))
    return list(groups.items())


def _back(slug, ok, message, section=""):
    s = section or request.form.get("s", "")
    if s in SECTION_KEYS and s != "overview":
        return action_response(ok, message, redirect_endpoint="communities.section", redirect_kwargs={"slug": slug, "sec": s})
    return action_response(ok, message, redirect_endpoint="communities.page", redirect_kwargs={"slug": slug})


def _clean(text, n):
    return " ".join((text or "").split())[:n]


def _url(text, https_only=False):
    t = (text or "").strip()
    if not t:
        return ""
    ok = t.startswith("https://") or (not https_only and t.startswith("http://"))
    return t[:300] if ok and " " not in t else None


# ------------------------------------------------------------------ the nodes of a community

def polygon_of(c):
    """The community's area as [[lat, lon], ...] (at least three points), or None. A rectangle saved before shapes existed counts as its four corners."""
    try:
        pts = json.loads(c["area_poly"] or "[]")
    except ValueError:
        pts = []
    if len(pts) >= 3:
        return pts
    if all(c[k] is not None for k in ("south", "west", "north", "east")):
        s, w, n, e = c["south"], c["west"], c["north"], c["east"]
        return [[s, w], [s, e], [n, e], [n, w]]
    return None


def in_polygon(lat, lon, poly):
    """Whether a place is inside a shape (the usual ray count; the shape may be any simple outline, not only convex)."""
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        yi, xi, yj, xj = poly[i][0], poly[i][1], poly[j][0], poly[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def parse_poly(text, limit):
    """([[lat, lon], ...], error) from the JSON the drawing page sends: an empty list means no area."""
    try:
        pts = json.loads(text or "[]")
    except ValueError:
        return None, "The shape was not understood. Draw it again."
    if not isinstance(pts, list):
        return None, "The shape was not understood. Draw it again."
    if not pts:
        return [], ""
    if len(pts) < 3:
        return None, "An area needs at least three points."
    if len(pts) > limit:
        return None, f"An area can have {limit} points at most."
    out = []
    for p in pts:
        try:
            la, lo = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            return None, "A point of the shape is not a place."
        if not (-90 <= la <= 90 and -180 <= lo <= 180):
            return None, "A point of the shape is outside the map."
        out.append([round(la, 5), round(lo, 5)])
    return out, ""


def community_root(c):
    """The topic root a community's gateways use: the site's join root and the community's address (for example msh/US/AR/lr-mesh)."""
    return (cfg("mesh_join_topic").strip("/") or "msh") + "/" + c["slug"]


def connect_info(c):
    """What a gateway needs to connect for this community, in the Meshtastic app's own words."""
    host = cfg("mesh_join_host") or cfg("feed_host") or (cfg("main_address") or "")
    return {"host": host, "port": cfg("mesh_join_port"), "root": community_root(c), "tls": cfg("mesh_join_port") == "8883"}


def _psk(ch):
    if ch["key_kind"] == "default":
        return b"\x01"   # the default key: the same on every radio, which is what makes a channel open to everyone
    try:
        return base64.b64decode(vault.unseal(ch["psk_sealed"]))
    except ValueError:
        return b""


def _visible(ch, rank):
    """A public channel is for everyone; any other is for the team members whose role is at least the one chosen (an administrator always)."""
    return ch["visibility"] == "public" or rank >= SITE_ADMIN or (rank >= 1 and rank >= RANK.get(ch["min_role"], 5))


def _addr(ch, add=True):
    return mc.address(_psk(ch), ch["name"], ch["uplink"], ch["downlink"], ch["precision"], add)


def channels_for(c, rank):
    d = get_db()
    out = []
    for ch in d.execute("SELECT * FROM community_channels WHERE community_id=%s ORDER BY lower(name)", (c["id"],)).fetchall():
        if _visible(ch, rank):
            psk = _psk(ch)
            out.append(dict(ch, addr=_addr(ch), key_b64=base64.b64encode(psk).decode(), key_bits=len(psk) * 8))
    return out


def _node_key(n):
    return ms.claim_key(n.get("id") or "")


def community_nodes(c, rows=None):
    """The mesh nodes that count as this community's, from the service's list (hidden nodes left out), newest first. `rows` is the list when the caller already has it."""
    d = get_db()
    if rows is None:
        rows = ms.nodes("", "", None, False)
    if not rows:
        return []
    hid = {h["node_key"] for h in ms.hidden()}
    inc = {r["node_key"] for r in d.execute("SELECT node_key FROM community_nodes WHERE community_id=%s AND mode='include'", (c["id"],)).fetchall()}
    exc = {r["node_key"] for r in d.execute("SELECT node_key FROM community_nodes WHERE community_id=%s AND mode='exclude'", (c["id"],)).fetchall()}
    claimed = {r["node_key"] for r in d.execute("SELECT k.node_key FROM mesh_claims k JOIN community_members m ON m.user_id = k.owner_id WHERE m.community_id=%s AND k.status='claimed'", (c["id"],)).fetchall()}
    poly = polygon_of(c)
    # a node the team placed by hand on the map takes that place when it reports none of its own
    placed = {r["node_key"]: r for r in d.execute("SELECT node_key, lat, lon FROM community_pins WHERE community_id=%s AND node_key <> ''", (c["id"],)).fetchall()}
    out = []
    for n in rows:
        key = _node_key(n)
        if key is None or key in hid or key in exc:
            continue
        if n.get("lat") is None and key in placed:
            n = dict(n, lat=placed[key]["lat"], lon=placed[key]["lon"], placed=True)
        inside = poly is not None and n.get("lat") is not None and in_polygon(n["lat"], n["lon"], poly)
        if inside or key in claimed or key in inc or n.get("community") == c["slug"]:
            out.append(n)
    return out


def _age_h(n, now):
    try:
        return (now - datetime.fromisoformat(n["last_seen"].replace("Z", "+00:00"))).total_seconds() / 3600.0
    except (KeyError, ValueError, AttributeError):
        return 9e9


def stats_of(nodes):
    """The numbers and the lists that need attention for a set of nodes, as the Mesh Health page counts them."""
    now = datetime.now(timezone.utc)
    off_h, low = cfg_int("mesh_offline_hours"), cfg_int("mesh_low_battery_pct")
    routers = [n for n in nodes if n.get("role_name") in ROUTERS]
    util = [n["channel_util"] for n in nodes if isinstance(n.get("channel_util"), (int, float))]
    return {"total": len(nodes), "hour": sum(1 for n in nodes if _age_h(n, now) <= 1), "day": sum(1 for n in nodes if _age_h(n, now) <= 24), "routers": len(routers),
            "quiet": [n for n in routers if _age_h(n, now) > off_h], "off_h": off_h,
            "low": [n for n in nodes if isinstance(n.get("battery"), int) and 0 < n["battery"] <= low and _age_h(n, now) <= 48], "low_pct": low,
            "util": round(sum(util) / len(util), 1) if util else None, "packets": sum(int(n.get("packets") or 0) for n in nodes)}


# ------------------------------------------------------------------ public pages

@bp.route("/mesh/communities")
def listing():
    _on()
    d = get_db()
    rows = d.execute("SELECT c.*, (SELECT COUNT(*) FROM community_follows f WHERE f.community_id = c.id) AS followers, (SELECT COUNT(*) FROM community_members m WHERE m.community_id = c.id) AS team "
                     "FROM communities c WHERE c.published OR %s ORDER BY lower(c.name)", (is_site_admin(),)).fetchall()
    out = []
    allnodes = ms.nodes_cached("", "", None, False, everything=True)
    for c in rows:
        out.append(dict(c, nodes=len(community_nodes(c, allnodes)) if c["published"] else 0))
    return render_template("community_list.html", rows=out, admin=is_site_admin())


@bp.route("/api/mesh/communities.json")
def api_list():
    _on()
    rows = get_db().execute("SELECT slug, name, about, website FROM communities WHERE published ORDER BY lower(name)").fetchall()
    return {"communities": [dict(r) for r in rows], "credit": ms.CREDIT}


@bp.route("/mesh/c/<slug>")
def page(slug):
    return _render(slug, "overview")


@bp.route("/mesh/c/<slug>/<sec>")
def section(slug, sec):
    if sec not in SECTION_KEYS:
        abort(404)
    return _render(slug, sec)


def _render(slug, sec):
    c, rank = _ctx(slug)
    if sec not in {k for g, items in menu_for(rank) for k, _l in items}:
        abort(403 if rank else 404)
    d = get_db()
    nodes = community_nodes(c)
    st = stats_of(nodes)
    posts = d.execute("SELECT p.*, u.username, u.display_name FROM community_posts p LEFT JOIN users u ON u.id = p.author_id WHERE p.community_id=%s ORDER BY p.pinned DESC, COALESCE(p.event_at, p.created_at) DESC LIMIT 40", (c["id"],)).fetchall()
    links = d.execute("SELECT * FROM community_links WHERE community_id=%s ORDER BY position, id", (c["id"],)).fetchall()
    following = bool(current_user.is_authenticated and d.execute("SELECT 1 FROM community_follows WHERE community_id=%s AND user_id=%s", (c["id"], current_user.id)).fetchone())
    poly = polygon_of(c)
    pins = d.execute("SELECT * FROM community_pins WHERE community_id=%s ORDER BY lower(name)", (c["id"],)).fetchall()
    ctx = dict(root=community_root(c), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), cur=sec, menu=menu_for(rank), poly=poly, pins=pins, max_points=cfg_int("mesh_community_max_points"), c=c, rank=rank, nodes=nodes, st=st, posts=posts, links=links, following=following, followers=d.execute("SELECT COUNT(*) AS n FROM community_follows WHERE community_id=%s", (c["id"],)).fetchone()["n"],
               can=lambda p: can(rank, p), credit=ms.CREDIT, roles=ROLES, rank_map=RANK, perm_text=PERM_TEXT, role_perms=ROLE_PERMS, role_label=LABEL, now=datetime.now(timezone.utc))
    if rank >= 1:
        ctx["team"] = d.execute("SELECT m.*, u.username, u.display_name, u.callsign FROM community_members m JOIN users u ON u.id = m.user_id WHERE m.community_id=%s ORDER BY m.role = 'owner' DESC, lower(u.username)", (c["id"],)).fetchall()
        ctx["mine"] = next((m for m in ctx["team"] if current_user.is_authenticated and m["user_id"] == current_user.id), None)
    ctx["asking"] = cfg("mesh_community_join_requests") == "1" and current_user.is_authenticated and rank < 1
    ctx["my_request"] = d.execute("SELECT * FROM community_join_requests WHERE community_id=%s AND user_id=%s AND state='pending'", (c["id"], current_user.id)).fetchone() if current_user.is_authenticated and rank < 1 else None
    if can(rank, "manage_members"):
        ctx["requests"] = d.execute("SELECT r.*, u.username, u.display_name, u.callsign FROM community_join_requests r JOIN users u ON u.id = r.user_id WHERE r.community_id=%s AND r.state='pending' ORDER BY r.created_at", (c["id"],)).fetchall()
        ctx["invites"] = d.execute("SELECT * FROM community_invites WHERE community_id=%s AND state='pending' AND expires_at > now() ORDER BY created_at DESC", (c["id"],)).fetchall()
    ctx["channels"] = channels_for(c, rank) if sec == "channels" else []
    if sec == "channels":
        ctx.update(precisions=mc.PRECISIONS, precision_km=mc.PRECISION_KM, max_channels=cfg_int("mesh_community_max_channels"), channel_total=d.execute("SELECT COUNT(*) AS n FROM community_channels WHERE community_id=%s", (c["id"],)).fetchone()["n"])
    if can(rank, "choose_nodes"):
        ctx["picks"] = d.execute("SELECT * FROM community_nodes WHERE community_id=%s ORDER BY mode, node_key", (c["id"],)).fetchall()
    if can(rank, "manage_logins"):
        ctx["logins"] = d.execute("SELECT a.*, u.username AS owner_name FROM mesh_accounts a JOIN users u ON u.id = a.owner_id WHERE a.community_id=%s ORDER BY a.created", (c["id"],)).fetchall()
        ctx["login_limit"] = cfg_int("mesh_community_max_logins")
        ctx["connect"] = connect_info(c)
    if (can(rank, "queue_commands") or can(rank, "manage_bridges")) and cfg("mesh_bridge_enabled") == "1":
        from . import meshbridge
        ctx["bridge"] = meshbridge.page_context(c)
    if sec == "links":
        from . import meshlinks
        ctx["ml"] = meshlinks.page_context(c)
    if sec == "alerts":
        from . import meshalertchan
        ctx["ac"] = meshalertchan.page_context(c)
    ctx["fwd"] = {"on": cfg("mesh_forward_enabled") == "1", "root": cfg("mesh_forward_root"), "bits": cfg("mesh_public_max_bits")}
    ctx["mapdata"] = {"poly": poly, "nodes": [{"slug": n["slug"], "name": n.get("long_name") or n.get("short_name") or n["id"], "lat": n["lat"], "lon": n["lon"], "role": n.get("role_name") or "", "last": n.get("last_seen"), "placed": bool(n.get("placed")),
                                              "color": ms.ROLE_COLORS.get(n.get("role_name") or "", "#4fb3ff")} for n in nodes if n.get("lat") is not None],
                      "pins": [{"id": p["id"], "name": p["name"], "lat": p["lat"], "lon": p["lon"], "role": p["role"], "status": p["status"], "notes": p["notes"]} for p in pins]}
    return render_template("community.html", **ctx)


# ------------------------------------------------------------------ following

@bp.route("/mesh/c/<slug>/follow", methods=["POST"])
@login_required
def follow(slug):
    c, _r = _ctx(slug)
    d = get_db()
    d.execute("INSERT INTO community_follows (community_id, user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (c["id"], current_user.id))
    d.commit()
    return _back(slug, True, f"You follow {c['name']}.")


@bp.route("/mesh/c/<slug>/unfollow", methods=["POST"])
@login_required
def unfollow(slug):
    c, _r = _ctx(slug)
    d = get_db()
    d.execute("DELETE FROM community_follows WHERE community_id=%s AND user_id=%s", (c["id"], current_user.id))
    d.commit()
    return _back(slug, True, f"You no longer follow {c['name']}.")


# ------------------------------------------------------------------ the page itself

@bp.route("/mesh/c/<slug>/page", methods=["POST"])
@login_required
def save_page(slug):
    c, _r = _ctx(slug, "edit_page")
    f = request.form
    name = _clean(f.get("name"), 60)
    if not name:
        return _back(slug, False, "The community needs a name.")
    logo, site = _url(f.get("logo_url"), True), _url(f.get("website"))
    if logo is None or site is None:
        return _back(slug, False, "A logo or a website address must start with https:// (the website may also be http://) and have no spaces.")
    d = get_db()
    d.execute("UPDATE communities SET name=%s, about=%s, logo_url=%s, website=%s, join_channel=%s, join_preset=%s, join_region=%s, join_steps=%s WHERE id=%s",
              (name, (f.get("about") or "").strip()[:4000], logo, site, _clean(f.get("join_channel"), 60), _clean(f.get("join_preset"), 40), _clean(f.get("join_region"), 40), (f.get("join_steps") or "").strip()[:3000], c["id"]))
    d.commit()
    audit("community.edit", target=slug, detail="page")
    return _back(slug, True, "The page is saved.", "edit")


@bp.route("/mesh/c/<slug>/share", methods=["POST"])
@login_required
def save_share(slug):
    c, _r = _ctx(slug, "manage_members")
    f = request.form
    site, public = f.get("feed_site_map") == "1", f.get("feed_public") == "1" and cfg("mesh_forward_enabled") == "1"
    d = get_db()
    d.execute("UPDATE communities SET feed_site_map=%s, feed_public=%s WHERE id=%s", (site, public, c["id"]))
    d.commit()
    audit("community.edit", target=slug, detail=f"sharing: site map {'on' if site else 'off'}, public Meshtastic server {'on' if public else 'off'}")
    ms.call("/config", "POST", ms.service_config())   # the mesh service learns which communities forward
    return _back(slug, True, "Sharing is saved.", "share")


@bp.route("/mesh/c/<slug>/area", methods=["POST"])
@login_required
def save_area(slug):
    c, _r = _ctx(slug, "edit_page")
    pts, err = parse_poly(request.form.get("poly"), cfg_int("mesh_community_max_points"))
    if err:
        return _back(slug, False, err, "area")
    d = get_db()
    d.execute("UPDATE communities SET area_poly=%s, south=NULL, west=NULL, north=NULL, east=NULL WHERE id=%s", (json.dumps(pts) if pts else "", c["id"]))   # a shape replaces any old rectangle
    d.commit()
    audit("community.edit", target=slug, detail=f"area: {len(pts)} points" if pts else "area cleared")
    return _back(slug, True, f"The area is saved ({len(pts)} points)." if pts else "The area is cleared: only nodes your team claimed or added count now.", "area")


# ------------------------------------------------------------------ the community's channels (the same keys, addresses and QR codes as Mesh Channels)

def _ch(c, cid):
    ch = get_db().execute("SELECT * FROM community_channels WHERE id=%s AND community_id=%s", (cid, c["id"])).fetchone()
    if ch is None:
        abort(404)
    return ch


def _ch_options(f):
    try:
        precision = int(f.get("precision", "0") or 0)
    except ValueError:
        precision = -1
    if precision not in mc.PRECISIONS:
        return None, "Choose one of the position choices."
    vis = "public" if f.get("visibility") == "public" else "team"
    role = f.get("min_role") if f.get("min_role") in RANK else "viewer"
    return {"uplink": bool(f.get("uplink")), "downlink": bool(f.get("downlink")), "precision": precision, "note": (f.get("note") or "").strip()[:300], "visibility": vis, "min_role": role}, None


@bp.route("/mesh/c/<slug>/channels/add", methods=["POST"])
@login_required
def channel_add(slug):
    c, _r = _ctx(slug, "manage_channels")
    f = request.form
    name = (f.get("name") or "").strip()
    if not mc.NAME_RE.fullmatch(name):
        return _back(slug, False, "A channel name is 1 to 11 letters, digits, dashes or underscores (the Meshtastic limit).", "channels")
    opts, err = _ch_options(f)
    if err:
        return _back(slug, False, err, "channels")
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM community_channels WHERE community_id=%s", (c["id"],)).fetchone()["n"] >= cfg_int("mesh_community_max_channels"):
        return _back(slug, False, f"A community can list {cfg_int('mesh_community_max_channels')} channels. Remove one first.", "channels")
    if d.execute("SELECT 1 FROM community_channels WHERE community_id=%s AND lower(name)=lower(%s)", (c["id"], name)).fetchone():
        return _back(slug, False, "The community already has a channel with that name.", "channels")
    kind = f.get("key_kind") if f.get("key_kind") in ("128", "256", "default") else "256"
    if kind == "default":
        key_kind, sealed = "default", ""
        opts["visibility"] = "public"   # the default key is the same on every radio: such a channel is open to everyone, so it is listed as public
    else:
        key_kind, sealed = "random", vault.seal(base64.b64encode(secrets.token_bytes(16 if kind == "128" else 32)).decode())
    d.execute("INSERT INTO community_channels (community_id, name, visibility, min_role, key_kind, psk_sealed, uplink, downlink, precision, note, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (c["id"], name, opts["visibility"], opts["min_role"], key_kind, sealed, opts["uplink"], opts["downlink"], opts["precision"], opts["note"], current_user.id))
    d.commit()
    audit("community.channel", target=slug, detail=f"made {name} ({opts['visibility']}, {key_kind} key)")
    return _back(slug, True, f"Channel {name} is listed.", "channels")


@bp.route("/mesh/c/<slug>/channels/<int:cid>/save", methods=["POST"])
@login_required
def channel_save(slug, cid):
    c, _r = _ctx(slug, "manage_channels")
    ch = _ch(c, cid)
    opts, err = _ch_options(request.form)
    if err:
        return _back(slug, False, err, "channels")
    if ch["key_kind"] == "default":
        opts["visibility"] = "public"
    d = get_db()
    d.execute("UPDATE community_channels SET visibility=%s, min_role=%s, uplink=%s, downlink=%s, precision=%s, note=%s WHERE id=%s", (opts["visibility"], opts["min_role"], opts["uplink"], opts["downlink"], opts["precision"], opts["note"], cid))
    d.commit()
    audit("community.channel", target=slug, detail=f"changed {ch['name']}")
    return _back(slug, True, "Saved. The QR code and address changed with the options: people who already added the channel should scan again.", "channels")


@bp.route("/mesh/c/<slug>/channels/<int:cid>/rotate", methods=["POST"])
@login_required
def channel_rotate(slug, cid):
    c, _r = _ctx(slug, "manage_channels")
    ch = _ch(c, cid)
    if ch["key_kind"] == "default":
        return _back(slug, False, "The default key is the same on every radio and cannot be replaced.", "channels")
    d = get_db()
    d.execute("UPDATE community_channels SET psk_sealed=%s, rotated=now() WHERE id=%s", (vault.seal(base64.b64encode(secrets.token_bytes(len(_psk(ch)) or 32)).decode()), cid))
    d.commit()
    audit("community.channel", target=slug, detail=f"new key for {ch['name']}")
    return _back(slug, True, "The key was replaced. Everyone has to scan the new QR code.", "channels")


@bp.route("/mesh/c/<slug>/channels/<int:cid>/delete", methods=["POST"])
@login_required
def channel_delete(slug, cid):
    c, _r = _ctx(slug, "manage_channels")
    ch = _ch(c, cid)
    d = get_db()
    d.execute("DELETE FROM community_channels WHERE id=%s", (cid,))
    d.commit()
    audit("community.channel", target=slug, detail=f"removed {ch['name']}")
    return _back(slug, True, "Channel removed.", "channels")


@bp.route("/mesh/c/<slug>/channels/<int:cid>/qr.svg")
def channel_qr(slug, cid):
    c, rank = _ctx(slug)
    ch = _ch(c, cid)
    if not _visible(ch, rank):
        abort(404)
    r = Response(mc.qr_svg(_addr(ch, request.args.get("replace") != "1")), mimetype="image/svg+xml")
    r.headers["Cache-Control"] = "private, no-store"
    return r


# ------------------------------------------------------------------ nodes placed on the map by hand

PIN_STATUS = ("active", "planned", "offline")


@bp.route("/mesh/c/<slug>/pins/save", methods=["POST"])
@login_required
def pin_save(slug):
    c, _r = _ctx(slug, "choose_nodes")
    f = request.form
    name = _clean(f.get("name"), 60)
    try:
        lat, lon = float(f.get("lat", "")), float(f.get("lon", ""))
    except ValueError:
        return _back(slug, False, "Click the map to choose the place, or type its latitude and longitude.", "pick")
    if not name or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return _back(slug, False, "A node on the map needs a name and a place.", "pick")
    status = f.get("status") if f.get("status") in PIN_STATUS else "active"
    key = ms.claim_key(f.get("node_key", "")) if (f.get("node_key") or "").strip() else ""
    if key is None:
        return _back(slug, False, "A node ID is eight hexadecimal characters, for example !a1b2c3d4 (or leave it empty).", "pick")
    d = get_db()
    pid = f.get("id", type=int)
    if pid:
        if not d.execute("SELECT 1 FROM community_pins WHERE id=%s AND community_id=%s", (pid, c["id"])).fetchone():
            abort(404)
        d.execute("UPDATE community_pins SET name=%s, role=%s, status=%s, lat=%s, lon=%s, node_key=%s, notes=%s WHERE id=%s", (name, _clean(f.get("role"), 30), status, lat, lon, key, (f.get("notes") or "").strip()[:300], pid))
    else:
        if d.execute("SELECT COUNT(*) AS n FROM community_pins WHERE community_id=%s", (c["id"],)).fetchone()["n"] >= cfg_int("mesh_community_max_pins"):
            return _back(slug, False, f"A community can have {cfg_int('mesh_community_max_pins')} nodes placed by hand. Remove one first.", "pick")
        d.execute("INSERT INTO community_pins (community_id, name, role, status, lat, lon, node_key, notes, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (c["id"], name, _clean(f.get("role"), 30), status, lat, lon, key, (f.get("notes") or "").strip()[:300], current_user.id))
    d.commit()
    audit("community.pin", target=slug, detail=f"{'changed' if pid else 'placed'} {name} ({status})")
    return _back(slug, True, f"{name} is {'changed' if pid else 'placed on the map'}.", "pick")


@bp.route("/mesh/c/<slug>/pins/<int:pid>/delete", methods=["POST"])
@login_required
def pin_delete(slug, pid):
    c, _r = _ctx(slug, "choose_nodes")
    d = get_db()
    d.execute("DELETE FROM community_pins WHERE id=%s AND community_id=%s", (pid, c["id"]))
    d.commit()
    audit("community.pin", target=slug, detail="removed")
    return _back(slug, True, "It is taken off the map.", "pick")


@bp.route("/mesh/c/<slug>/links/add", methods=["POST"])
@login_required
def link_add(slug):
    c, _r = _ctx(slug, "edit_page")
    label, url = _clean(request.form.get("label"), 60), _url(request.form.get("url"))
    if not label or not url:
        return _back(slug, False, "A link needs a name and an address that starts with https:// or http://.")
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM community_links WHERE community_id=%s", (c["id"],)).fetchone()["n"] >= cfg_int("mesh_community_max_links"):
        return _back(slug, False, f"A community can have {cfg_int('mesh_community_max_links')} links. Remove one first.")
    d.execute("INSERT INTO community_links (community_id, label, url, position) VALUES (%s,%s,%s,(SELECT COALESCE(MAX(position), 0) + 1 FROM community_links WHERE community_id=%s))", (c["id"], label, url, c["id"]))
    d.commit()
    audit("community.edit", target=slug, detail="link added: " + label)
    return _back(slug, True, "The link is added.")


@bp.route("/mesh/c/<slug>/links/<int:lid>/delete", methods=["POST"])
@login_required
def link_delete(slug, lid):
    c, _r = _ctx(slug, "edit_page")
    d = get_db()
    d.execute("DELETE FROM community_links WHERE id=%s AND community_id=%s", (lid, c["id"]))
    d.commit()
    audit("community.edit", target=slug, detail="link removed")
    return _back(slug, True, "The link is removed.")


# ------------------------------------------------------------------ news and events

@bp.route("/mesh/c/<slug>/posts/add", methods=["POST"])
@login_required
def post_add(slug):
    c, _r = _ctx(slug, "post")
    f = request.form
    title, body = _clean(f.get("title"), 120), (f.get("body") or "").strip()[:4000]
    kind = "event" if f.get("kind") == "event" else "notice"
    if not title:
        return _back(slug, False, "A notice needs a title.")
    when = None
    if kind == "event":
        try:
            when = datetime.fromisoformat((f.get("event_at") or "").strip()).replace(tzinfo=timezone.utc) if f.get("event_at") else None
        except ValueError:
            return _back(slug, False, "The date and time of the event are not understood.")
        if when is None:
            return _back(slug, False, "An event needs a date and time.")
    d = get_db()
    d.execute("INSERT INTO community_posts (community_id, author_id, title, body, kind, event_at) VALUES (%s,%s,%s,%s,%s,%s)", (c["id"], current_user.id, title, body, kind, when))
    d.execute("DELETE FROM community_posts WHERE community_id=%s AND id NOT IN (SELECT id FROM community_posts WHERE community_id=%s ORDER BY created_at DESC LIMIT 200)", (c["id"], c["id"]))
    d.commit()
    audit("community.post", target=slug, detail=kind + ": " + title[:80])
    try:   # the people who follow the community are told (a phone notice when they chose those)
        from .pushkinds import notify
        for m in d.execute("SELECT user_id FROM community_follows WHERE community_id=%s AND user_id<>%s", (c["id"], current_user.id)).fetchall():
            notify(m["user_id"], "community_news", c["name"], ("Event: " if kind == "event" else "News: ") + title, f"/mesh/c/{slug}", "community-news-" + str(c["id"]))
    except Exception:
        pass
    return _back(slug, True, "Posted.")


@bp.route("/mesh/c/<slug>/posts/<int:pid>/delete", methods=["POST"])
@login_required
def post_delete(slug, pid):
    c, _r = _ctx(slug, "post")
    d = get_db()
    d.execute("DELETE FROM community_posts WHERE id=%s AND community_id=%s", (pid, c["id"]))
    d.commit()
    audit("community.post", target=slug, detail="removed")
    return _back(slug, True, "Removed.")


@bp.route("/mesh/c/<slug>/posts/<int:pid>/pin", methods=["POST"])
@login_required
def post_pin(slug, pid):
    c, _r = _ctx(slug, "post")
    d = get_db()
    d.execute("UPDATE community_posts SET pinned = NOT pinned WHERE id=%s AND community_id=%s", (pid, c["id"]))
    d.commit()
    return _back(slug, True, "Done.")


# ------------------------------------------------------------------ which nodes count

@bp.route("/mesh/c/<slug>/nodes/add", methods=["POST"])
@login_required
def nodes_add(slug):
    c, _r = _ctx(slug, "choose_nodes")
    key = ms.claim_key(request.form.get("node", ""))
    mode = "exclude" if request.form.get("mode") == "exclude" else "include"
    if key is None:
        return _back(slug, False, "A Meshtastic node ID is eight hexadecimal characters, for example !a1b2c3d4; a MeshCore node is mc: and its 64 character public key.")
    d = get_db()
    d.execute("INSERT INTO community_nodes (community_id, node_key, mode) VALUES (%s,%s,%s) ON CONFLICT (community_id, node_key) DO UPDATE SET mode=EXCLUDED.mode", (c["id"], key, mode))
    d.commit()
    audit("community.nodes", target=slug, detail=f"{mode} {key[:20]}")
    return _back(slug, True, f"{key[:14]} is {'taken off' if mode == 'exclude' else 'added to'} the community's nodes.")


@bp.route("/mesh/c/<slug>/nodes/remove", methods=["POST"])
@login_required
def nodes_remove(slug):
    c, _r = _ctx(slug, "choose_nodes")
    key = ms.claim_key(request.form.get("node", ""))
    d = get_db()
    d.execute("DELETE FROM community_nodes WHERE community_id=%s AND node_key=%s", (c["id"], key))
    d.commit()
    audit("community.nodes", target=slug, detail="cleared " + str(key)[:20])
    return _back(slug, True, "That choice is cleared; the node counts by the usual rules again.")


# ------------------------------------------------------------------ members, roles and invitations

def _may_grant(rank, role):
    """Nobody can give a role higher than their own; only an Owner (or a site administrator) can name an Owner."""
    return RANK.get(role, 99) <= rank and (role != "owner" or rank >= RANK["owner"])


@bp.route("/mesh/c/<slug>/members/add", methods=["POST"])
@login_required
def member_add(slug):
    c, rank = _ctx(slug, "manage_members")
    role = request.form.get("role", "")
    if role not in RANK or not _may_grant(rank, role):
        return _back(slug, False, "You cannot give that role.")
    u = social.find_user(request.form.get("who", ""))
    if u is None:
        return _back(slug, False, "No member matches that. Type a user name, a call sign or a whole email address.")
    d = get_db()
    cur = d.execute("SELECT role FROM community_members WHERE community_id=%s AND user_id=%s", (c["id"], u["id"])).fetchone()
    if cur and RANK[cur["role"]] > rank:
        return _back(slug, False, "That member already has a higher role than you can change.")
    d.execute("INSERT INTO community_members (community_id, user_id, role, added_by) VALUES (%s,%s,%s,%s) ON CONFLICT (community_id, user_id) DO UPDATE SET role=EXCLUDED.role", (c["id"], u["id"], role, current_user.id))
    d.commit()
    audit("community.member", target=slug, detail=f"{u['username']} is {LABEL[role]}")
    return _back(slug, True, f"{u['display_name'] or u['username']} is now {LABEL[role]} of {c['name']}.")


@bp.route("/mesh/c/<slug>/members/<int:uid>/role", methods=["POST"])
@login_required
def member_role(slug, uid):
    c, rank = _ctx(slug, "manage_members")
    role = request.form.get("role", "")
    d = get_db()
    cur = d.execute("SELECT m.role, u.username FROM community_members m JOIN users u ON u.id = m.user_id WHERE m.community_id=%s AND m.user_id=%s", (c["id"], uid)).fetchone()
    if cur is None:
        abort(404)
    if role not in RANK or not _may_grant(rank, role) or RANK[cur["role"]] > rank:
        return _back(slug, False, "You cannot change that role.")
    if cur["role"] == "owner" and role != "owner" and d.execute("SELECT COUNT(*) AS n FROM community_members WHERE community_id=%s AND role='owner'", (c["id"],)).fetchone()["n"] <= 1:
        return _back(slug, False, "A community needs an Owner. Give the Owner role to someone else first.")
    d.execute("UPDATE community_members SET role=%s WHERE community_id=%s AND user_id=%s", (role, c["id"], uid))
    if role == "owner" and uid != current_user.id and not is_site_admin():
        d.execute("UPDATE community_members SET role='manager' WHERE community_id=%s AND user_id=%s AND role='owner'", (c["id"], current_user.id))   # ownership is handed over, not shared
    d.commit()
    audit("community.member", target=slug, detail=f"{cur['username']} is {LABEL[role]}")
    return _back(slug, True, f"{cur['username']} is now {LABEL[role]}.")


@bp.route("/mesh/c/<slug>/members/<int:uid>/remove", methods=["POST"])
@login_required
def member_remove(slug, uid):
    c, rank = _ctx(slug)
    d = get_db()
    cur = d.execute("SELECT m.role, u.username FROM community_members m JOIN users u ON u.id = m.user_id WHERE m.community_id=%s AND m.user_id=%s", (c["id"], uid)).fetchone()
    if cur is None:
        abort(404)
    own = uid == current_user.id
    if not own and not (can(rank, "manage_members") and RANK[cur["role"]] <= rank):
        abort(403)
    if cur["role"] == "owner" and d.execute("SELECT COUNT(*) AS n FROM community_members WHERE community_id=%s AND role='owner'", (c["id"],)).fetchone()["n"] <= 1:
        return _back(slug, False, "A community needs an Owner. Give the Owner role to someone else first.")
    d.execute("DELETE FROM community_members WHERE community_id=%s AND user_id=%s", (c["id"], uid))
    d.commit()
    audit("community.member", target=slug, detail=f"{cur['username']} {'left' if own else 'removed'}")
    return _back(slug, True, "You left the team." if own else f"{cur['username']} is removed from the team.")


# ------------------------------------------------------------------ asking to join

@bp.route("/mesh/c/<slug>/join-request", methods=["POST"])
@login_required
def join_request(slug):
    c, rank = _ctx(slug)
    if cfg("mesh_community_join_requests") != "1":
        abort(404)
    if rank >= 1:
        return _back(slug, False, "You are on the team already.")
    d = get_db()
    if d.execute("SELECT 1 FROM community_join_requests WHERE community_id=%s AND user_id=%s AND state='pending'", (c["id"], current_user.id)).fetchone():
        return _back(slug, False, "Your request is waiting for the team already.")
    if d.execute("SELECT COUNT(*) AS n FROM community_join_requests WHERE community_id=%s AND state='pending'", (c["id"],)).fetchone()["n"] >= cfg_int("mesh_community_max_requests"):
        return _back(slug, False, "This community has as many requests waiting as it can take. Try again later.")
    msg = " ".join((request.form.get("message") or "").split())[:300]
    d.execute("INSERT INTO community_join_requests (community_id, user_id, message) VALUES (%s,%s,%s)", (c["id"], current_user.id, msg))
    d.commit()
    audit("community.join_request", target=slug, detail=current_user.username)
    try:   # the people who can answer are told (a phone notice when they chose those)
        from .pushkinds import notify as notify_member
        for m in d.execute("SELECT user_id FROM community_members WHERE community_id=%s AND role IN ('manager','owner')", (c["id"],)).fetchall():
            notify_member(m["user_id"], "community", "Request to join " + c["name"], f"{current_user.display_name or current_user.username} asked to join the team.", f"/mesh/c/{slug}/team", "community-join")
    except Exception:
        pass
    return _back(slug, True, f"Your request to join {c['name']} is sent. The team will answer it.")


@bp.route("/mesh/c/<slug>/join-request/cancel", methods=["POST"])
@login_required
def join_request_cancel(slug):
    c, _r = _ctx(slug)
    d = get_db()
    d.execute("UPDATE community_join_requests SET state='cancelled', decided_at=now() WHERE community_id=%s AND user_id=%s AND state='pending'", (c["id"], current_user.id))
    d.commit()
    return _back(slug, True, "Your request is withdrawn.")


@bp.route("/mesh/c/<slug>/join-requests/<int:rid>/<action>", methods=["POST"])
@login_required
def join_request_answer(slug, rid, action):
    c, rank = _ctx(slug, "manage_members")
    if action not in ("approve", "decline"):
        abort(404)
    d = get_db()
    row = d.execute("SELECT r.*, u.username, u.display_name FROM community_join_requests r JOIN users u ON u.id = r.user_id WHERE r.id=%s AND r.community_id=%s AND r.state='pending'", (rid, c["id"])).fetchone()
    if row is None:
        return _back(slug, False, "That request was answered already.", "team")
    if action == "approve":
        role = request.form.get("role", "viewer")
        if role not in RANK or not _may_grant(rank, role):
            return _back(slug, False, "You cannot give that role.", "team")
        d.execute("INSERT INTO community_members (community_id, user_id, role, added_by) VALUES (%s,%s,%s,%s) ON CONFLICT (community_id, user_id) DO NOTHING", (c["id"], row["user_id"], role, current_user.id))
    d.execute("UPDATE community_join_requests SET state=%s, decided_by=%s, decided_at=now() WHERE id=%s", ("approved" if action == "approve" else "declined", current_user.id, rid))
    d.commit()
    audit("community.join_request", target=slug, detail=f"{row['username']}: {action}d")
    try:
        from .pushkinds import notify as notify_member
        notify_member(row["user_id"], "community", c["name"], "You are on the team now." if action == "approve" else "Your request to join was not accepted.", f"/mesh/c/{slug}", "community-join")
    except Exception:
        pass
    return _back(slug, True, f"{row['display_name'] or row['username']} " + ("is on the team." if action == "approve" else "was declined."), "team")


@bp.route("/mesh/c/<slug>/invites/add", methods=["POST"])
@login_required
def invite_add(slug):
    c, rank = _ctx(slug, "manage_members")
    role = request.form.get("role", "")
    email = _clean(request.form.get("email"), 120)
    if role not in RANK or not _may_grant(rank, role):
        return _back(slug, False, "You cannot give that role.")
    token = secrets.token_urlsafe(24)
    d = get_db()
    d.execute("INSERT INTO community_invites (community_id, role, token_hash, email, created_by, expires_at) VALUES (%s,%s,%s,%s,%s,%s)",
              (c["id"], role, vault.keyed_hash(token, "cinvite"), email, current_user.id, datetime.now(timezone.utc) + timedelta(days=cfg_int("mesh_community_invite_days"))))
    d.commit()
    from .seo import base_url
    link = base_url() + url_for("communities.join", slug=slug, token=token)
    sent = ""
    if email and "@" in email:
        from .mailer import send_mail, smtp_configured
        if smtp_configured():
            ok, _m = send_mail(email, f"You are invited to {c['name']}", f"{current_user.username} invited you to be {LABEL[role]} of {c['name']} on {cfg('brand_name')}{cfg('brand_accent')}.\n\nOpen this link while signed in to accept or decline:\n{link}\n\nThe link works for {cfg_int('mesh_community_invite_days')} days.\n")
            sent = " It was emailed." if ok else " The email could not be sent; give the link to them yourself."
        else:
            sent = " Email is not set up on this site yet, so give them the link yourself."
    audit("community.invite", target=slug, detail=f"{LABEL[role]}" + (f" for {email}" if email else ""))
    flash("Invitation link (shown once, copy it now): " + link, "success")
    return _back(slug, True, f"The invitation is made.{sent}")


@bp.route("/mesh/c/<slug>/invites/<int:iid>/cancel", methods=["POST"])
@login_required
def invite_cancel(slug, iid):
    c, _r = _ctx(slug, "manage_members")
    d = get_db()
    d.execute("UPDATE community_invites SET state='cancelled' WHERE id=%s AND community_id=%s AND state='pending'", (iid, c["id"]))
    d.commit()
    audit("community.invite", target=slug, detail="cancelled")
    return _back(slug, True, "The invitation is cancelled.")


def _invite(c, token):
    row = get_db().execute("SELECT * FROM community_invites WHERE community_id=%s AND token_hash=%s", (c["id"], vault.keyed_hash(token, "cinvite"))).fetchone()
    if row is None:
        return None, "That invitation link is not valid."
    if row["state"] != "pending":
        return None, "That invitation has already been " + ("accepted." if row["state"] == "accepted" else "answered or cancelled.")
    if row["expires_at"] < datetime.now(timezone.utc):
        return None, "That invitation has expired. Ask for a new one."
    return row, ""


@bp.route("/mesh/c/<slug>/join/<token>", methods=["GET", "POST"])
@login_required
def join(slug, token):
    c, _r = _ctx(slug)
    row, why = _invite(c, token)
    if request.method == "GET":
        return render_template("community_join.html", c=c, inv=row, why=why, role_label=LABEL, token=token)
    if row is None:
        flash(why, "error")
        return redirect(url_for("communities.page", slug=slug))
    d = get_db()
    if request.form.get("answer") == "accept":
        d.execute("INSERT INTO community_members (community_id, user_id, role, added_by) VALUES (%s,%s,%s,%s) ON CONFLICT (community_id, user_id) DO UPDATE SET role = CASE WHEN "
                  "(CASE community_members.role WHEN 'owner' THEN 5 WHEN 'manager' THEN 4 WHEN 'editor' THEN 3 WHEN 'operator' THEN 2 ELSE 1 END) >= (CASE EXCLUDED.role WHEN 'owner' THEN 5 WHEN 'manager' THEN 4 WHEN 'editor' THEN 3 WHEN 'operator' THEN 2 ELSE 1 END) "
                  "THEN community_members.role ELSE EXCLUDED.role END", (c["id"], current_user.id, row["role"], row["created_by"]))
        d.execute("UPDATE community_invites SET state='accepted', accepted_by=%s WHERE id=%s", (current_user.id, row["id"]))
        audit("community.invite", target=slug, detail=f"accepted as {LABEL[row['role']]}")
        msg = f"You are now {LABEL[row['role']]} of {c['name']}."
    else:
        d.execute("UPDATE community_invites SET state='declined' WHERE id=%s", (row["id"],))
        audit("community.invite", target=slug, detail="declined")
        msg = "The invitation is declined."
    d.commit()
    flash(msg, "success")
    return redirect(url_for("communities.page", slug=slug))


# ------------------------------------------------------------------ broker logins for the community's gateways

@bp.route("/mesh/c/<slug>/logins/add", methods=["POST"])
@login_required
def login_add(slug):
    c, _r = _ctx(slug, "manage_logins")
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM mesh_accounts WHERE community_id=%s", (c["id"],)).fetchone()["n"] >= cfg_int("mesh_community_max_logins"):
        return _back(slug, False, f"A community can have {cfg_int('mesh_community_max_logins')} broker logins. Remove one first.")
    label = _clean(request.form.get("label"), 60) or "Gateway"
    down = request.form.get("link") == "down"
    aid, err = ms.create_account(current_user.id, slug, label, "", public=False, by_admin=True, community_id=c["id"], down=down)
    if err:
        return _back(slug, False, err)
    row = ms.get_account(aid)
    audit("community.login", target=slug, detail=f"made {row['username']} ({'uplink and downlink' if down else 'uplink'})")
    flash(f"Broker login {row['username']} (password shown once, copy it now): {ms.password_of(row)}", "success")
    return _back(slug, True, f"The login {row['username']} is made. Put it in the gateway node's MQTT settings; the Join the Mesh page shows where.")


def _login(slug, aid, perm="manage_logins"):
    c, _r = _ctx(slug, perm)
    row = ms.get_account(aid)
    if row is None or row["community_id"] != c["id"]:
        abort(404)
    return c, row


@bp.route("/mesh/c/<slug>/logins/<int:aid>/<action>", methods=["POST"])
@login_required
def login_action(slug, aid, action):
    c, row = _login(slug, aid)
    d = get_db()
    if action == "password":
        ok, why = ms.new_password(aid)
        if not ok:
            return _back(slug, False, why)
        flash(f"New password for {row['username']} (shown once, copy it now): {ms.password_of(ms.get_account(aid))}", "success")
        msg = "A new password is set."
    elif action in ("disable", "enable"):
        ok, why = ms.set_disabled(aid, action == "disable")
        if not ok:
            return _back(slug, False, why)
        msg = "The login is switched " + ("off." if action == "disable" else "on.")
    elif action == "delete":
        ms.call("/credentials/delete", "POST", {"username": row["username"]})
        d.execute("DELETE FROM mesh_accounts WHERE id=%s", (aid,))
        d.commit()
        msg = "The login is deleted."
    else:
        abort(404)
    audit("community.login", target=slug, detail=f"{action} {row['username']}")
    return _back(slug, True, msg)


# ------------------------------------------------------------------ administrators

@bp.route("/admin/communities")
@permission_required("manage_map_data")
def admin():
    d = get_db()
    rows = d.execute("SELECT c.*, (SELECT COUNT(*) FROM community_members m WHERE m.community_id = c.id) AS team, (SELECT u.username FROM community_members m JOIN users u ON u.id = m.user_id WHERE m.community_id = c.id AND m.role = 'owner' LIMIT 1) AS owner "
                     "FROM communities c ORDER BY lower(c.name)").fetchall()
    from . import meshlinks
    return render_template("admin_communities.html", active="communities", rows=rows, seeds=SEEDS, pending=meshlinks.pending_for_admin())


SEEDS = [("LR Mesh", "lr-mesh", "https://lrme.sh/", "Central Arkansas Meshtastic."), ("NWA Mesh", "nwa-mesh", "https://nwame.sh/", "Northwest Arkansas Meshtastic."),
         ("Hot Springs Mesh", "hot-springs-mesh", "https://hotme.sh/", "Hot Springs area Meshtastic.")]


def _slugify(name):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (name or "").lower())).strip("-")[:40]


@bp.route("/admin/communities/create", methods=["POST"])
@permission_required("manage_map_data")
def admin_create():
    name = _clean(request.form.get("name"), 60)
    slug = _slugify(request.form.get("slug") or name)
    if not name or not SLUG_RE.match(slug):
        return action_response(False, "Give the community a name; its address is made from it (letters, numbers and dashes, at least three characters).", redirect_endpoint="communities.admin")
    owner = None
    if request.form.get("owner", "").strip():
        owner = social.find_user(request.form.get("owner"))
        if owner is None:
            return action_response(False, "No member matches the Owner you typed. Type a user name, a call sign or a whole email address, or leave it empty to name one later.", redirect_endpoint="communities.admin")
    d = get_db()
    if get(slug):
        return action_response(False, "A community with that address exists already.", redirect_endpoint="communities.admin")
    cid = d.execute("INSERT INTO communities (slug, name, created_by) VALUES (%s,%s,%s) RETURNING id", (slug, name, current_user.id)).fetchone()["id"]
    if owner:
        d.execute("INSERT INTO community_members (community_id, user_id, role, added_by) VALUES (%s,%s,'owner',%s)", (cid, owner["id"], current_user.id))
    d.commit()
    audit("community.create", target=slug, detail=name + (f", owner {owner['username']}" if owner else ""))
    return action_response(True, f"{name} is made." + (f" {owner['username']} is its Owner." if owner else " It has no Owner yet; open it and name one."), redirect_endpoint="communities.admin")


@bp.route("/admin/communities/seed", methods=["POST"])
@permission_required("manage_map_data")
def admin_seed():
    d = get_db()
    made = 0
    for name, slug, site, about in SEEDS:
        if get(slug):
            continue
        d.execute("INSERT INTO communities (slug, name, about, website, created_by) VALUES (%s,%s,%s,%s,%s)", (slug, name, about + " This page has not been taken over yet: an organizer can ask an administrator to be named its Owner.", site, current_user.id))
        made += 1
    d.commit()
    audit("community.create", detail=f"seeded {made}")
    return action_response(True, f"{made} community page{'s' if made != 1 else ''} made." if made else "They exist already.", redirect_endpoint="communities.admin")


@bp.route("/admin/communities/<slug>/<action>", methods=["POST"])
@permission_required("manage_map_data")
def admin_action(slug, action):
    c = get(slug)
    if c is None:
        abort(404)
    d = get_db()
    if action == "publish":
        d.execute("UPDATE communities SET published = NOT published WHERE id=%s", (c["id"],))
        msg = f"{c['name']} is now {'hidden' if c['published'] else 'public'}."
    elif action == "delete":
        d.execute("DELETE FROM communities WHERE id=%s", (c["id"],))
        msg = f"{c['name']} is deleted (its broker logins stay with the members who made them)."
    else:
        abort(404)
    d.commit()
    audit("community." + action, target=slug)
    return action_response(True, msg, redirect_endpoint="communities.admin")
