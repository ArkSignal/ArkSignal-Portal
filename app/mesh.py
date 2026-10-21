"""The portal's side of the mesh service (deploy/mesh, its own container and database, with our own Mosquitto broker beside it): Meshtastic nodes that publish themselves (the map report a node
sends on purpose) are listed, mapped and given a page, and the gateways that heard them show who hears whom. When the service is away every call returns a plain note and the rest of the site works as
before. Terms: DATA_SOURCES_TERMS.md (only what owners deliberately publish; nothing encrypted is read; a node can be hidden on request)."""
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

from . import vault
from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int

CREDIT = "Mesh nodes: Meshtastic nodes that publish their own map reports and MeshCore nodes that advertise themselves"
RESOURCES = [("LR Mesh", "https://lrme.sh/", "Central Arkansas Meshtastic: configuration guide, community and the US Arkansas Discord thread"),
             ("NWA Mesh", "https://nwame.sh/", "Northwest Arkansas Meshtastic"),
             ("Hot Springs Mesh", "https://hotme.sh/", "Hot Springs area Meshtastic"),
             ("Memphis Mesh", "http://memphismesh.com/", "Memphis, Tennessee, across the river"),
             ("LZMesh", "https://lzmesh.com/", "Southwest Missouri and the surrounding area"),
             ("Meshtastic Public Mesh Map", "https://meshmap.net/", "A nearly live map of Meshtastic nodes seen by the official Meshtastic MQTT server (not our data)"),
             ("MeshCore Node Map", "https://meshcore.co.uk/map.html", "Nodes MeshCore users have put on the community map (not our data)"),
             ("Meshtastic documentation", "https://meshtastic.org/docs/getting-started/", "Getting started, MQTT and map reporting"),
             ("MeshCore", "https://github.com/meshcore-dev/MeshCore", "The MeshCore firmware and documentation")]   # links only: their data is not copied (DATA_SOURCES_TERMS.md)
ROLE_COLORS = {"Router": "#ff8c42", "Router and client": "#ff8c42", "Router (late)": "#ff8c42", "Repeater": "#ffd24f", "Tracker": "#c58bff", "Sensor": "#3ee6a8", "Client": "#4fb3ff", "Client (muted)": "#4fb3ff",
               "Client (base)": "#4fb3ff", "Chat node": "#4fb3ff", "Room server": "#c58bff", "Client (hidden)": "#8a9bb3", "TAK": "#ff6b6b", "TAK tracker": "#ff6b6b", "Lost and found": "#ff8cd9"}


def enabled():
    return cfg("mesh_enabled") == "1"


def call(path, method="GET", body=None, timeout=8):
    """(ok, data or message). Never raises."""
    if not enabled():
        return False, "Mesh nodes are switched off in Settings."
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(cfg("mesh_url").rstrip("/") + path, data=data, method=method, headers={"User-Agent": cfg("lookup_user_agent"), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read(20_000_000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"The mesh service answered with an error ({e.code})."
    except Exception as e:
        return False, "The mesh service did not answer (" + type(e).__name__ + ")."


def service_config():
    return {"host": cfg("mesh_host"), "port": cfg("mesh_port"), "user": cfg("mesh_user"), "password": cfg("mesh_password"), "topic": cfg("mesh_topic"), "south": cfg("mesh_south"), "west": cfg("mesh_west"),
            "north": cfg("mesh_north"), "east": cfg("mesh_east"), "keep_days": cfg("mesh_keep_days"), "links_keep_days": cfg("mesh_links_keep_days"), "enabled": "1",
            "up_enabled": cfg("mesh_up_enabled"), "up_host": cfg("mesh_up_host"), "up_port": cfg("mesh_up_port"), "up_user": cfg("mesh_up_user"), "up_password": cfg("mesh_up_password"), "up_topic": cfg("mesh_up_topic"),
            "public_rules": cfg("mesh_public_rules"), "public_channels": cfg("mesh_public_channels"), "public_portnums": cfg("mesh_public_portnums"), "public_max_bits": cfg("mesh_public_max_bits"),
            "publish_root": cfg("mesh_join_topic")}


def push_config():
    """Send the settings, the takedown list and every member's broker login (kept here so a rebuilt service or broker forgets nobody) to the service."""
    ok, d = call("/config", "POST", service_config())
    if not ok:
        return False, d
    for r in get_db().execute("SELECT node_key, reason FROM mesh_hidden").fetchall():
        call("/hide", "POST", {"id": r["node_key"], "reason": r["reason"]})
    ok, d = sync_accounts()
    return (True, "Settings, takedown list and logins sent.") if ok else (True, "Settings and takedown list sent; the logins could not be sent: " + d)


# ------------------------------------------------------------------ members' broker logins (each member, each login: its own username and password, made in the broker by the service)

SELECT = "SELECT a.*, u.username AS owner_name, u.display_name AS owner_display FROM mesh_accounts a JOIN users u ON u.id = a.owner_id "


def accounts_of(uid):
    return get_db().execute(SELECT + "WHERE a.owner_id=%s ORDER BY a.created", (uid,)).fetchall()


def all_accounts():
    return get_db().execute(SELECT + "ORDER BY a.created DESC").fetchall()


def get_account(aid):
    return get_db().execute(SELECT + "WHERE a.id=%s", (aid,)).fetchone()


def password_of(row):
    return vault.unseal(row["password_sealed"])


def _slug(name):
    return (re.sub(r"[^a-z0-9]", "", (name or "").lower())[:12] or "member")


def create_account(owner_id, owner_name, label, nodes="", public=False, by_admin=False):
    """A new login for a member. Returns (id, error). The row is only kept when the broker has the login."""
    d = get_db()
    if not by_admin and d.execute("SELECT COUNT(*) AS n FROM mesh_accounts WHERE owner_id=%s", (owner_id,)).fetchone()["n"] >= cfg_int("mesh_max_per_user"):
        return None, f"You can have {cfg_int('mesh_max_per_user')} mesh logins."
    pw = secrets.token_urlsafe(18)
    aid = d.execute("INSERT INTO mesh_accounts (owner_id, label, nodes, public, password_sealed) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (owner_id, (label or "").strip()[:60], (nodes or "").strip()[:300], bool(public), vault.seal(pw))).fetchone()["id"]
    name = f"{_slug(owner_name)}-{aid}"
    ok, r = call("/credentials/set", "POST", {"username": name, "password": pw})
    if not ok or not r.get("ok"):
        d.rollback()
        return None, "The login could not be made on the broker right now: " + (r if isinstance(r, str) else str(r.get("error", "")))
    d.execute("UPDATE mesh_accounts SET username=%s WHERE id=%s", (name, aid))
    d.commit()
    return aid, None


def new_password(aid):
    row = get_account(aid)
    pw = secrets.token_urlsafe(18)
    ok, r = call("/credentials/set", "POST", {"username": row["username"], "password": pw})
    if not ok or not r.get("ok"):
        return False, "The broker could not change the password right now: " + (r if isinstance(r, str) else str(r.get("error", "")))
    d = get_db()
    d.execute("UPDATE mesh_accounts SET password_sealed=%s WHERE id=%s", (vault.seal(pw), aid))
    d.commit()
    return True, ""


def set_disabled(aid, off):
    """Switching a login off removes it from the broker (the node is refused at once); switching it on makes it again with the same password."""
    row = get_account(aid)
    if off:
        ok, r = call("/credentials/delete", "POST", {"username": row["username"]})
    else:
        ok, r = call("/credentials/set", "POST", {"username": row["username"], "password": password_of(row)})
    if not ok or not r.get("ok"):
        return False, "The broker did not answer: " + (r if isinstance(r, str) else str(r.get("error", "")))
    d = get_db()
    d.execute("UPDATE mesh_accounts SET disabled=%s WHERE id=%s", (bool(off), aid))
    d.commit()
    return True, ""


def delete_account(aid):
    row = get_account(aid)
    if row and row["username"]:
        call("/credentials/delete", "POST", {"username": row["username"]})
    d = get_db()
    d.execute("DELETE FROM mesh_accounts WHERE id=%s", (aid,))
    d.commit()


def sync_accounts():
    """Make the broker's member logins exactly the enabled ones here. (ok, note)."""
    rows = get_db().execute("SELECT username, password_sealed FROM mesh_accounts WHERE NOT disabled AND username <> ''").fetchall()
    ok, r = call("/credentials/sync", "POST", {"users": [{"username": x["username"], "password": vault.unseal(x["password_sealed"])} for x in rows]}, timeout=30)
    if not ok:
        return False, r
    return bool(r.get("ok")), ("" if r.get("ok") else str(r.get("error", "")))


def owners_of_node(node_id):
    """The members who said a node is theirs and chose to show their name: [display name]. A verified claim counts; so does a login listing the node."""
    like = "%" + node_id.lstrip("!").lower() + "%"
    rows = get_db().execute(SELECT + "WHERE a.public AND NOT a.disabled AND lower(a.nodes) LIKE %s", (like,)).fetchall()
    names = {(r["owner_display"] or r["owner_name"]) for r in rows}
    key = claim_key(node_id)
    for r in get_db().execute("SELECT u.username AS owner_name, u.display_name AS owner_display FROM mesh_claims c JOIN users u ON u.id = c.owner_id WHERE c.node_key = %s AND c.status = 'claimed' AND c.public", (key,)).fetchall():
        names.add(r["owner_display"] or r["owner_name"])
    return sorted(names)


# ------------------------------------------------------------------ claiming a node: proof that it is yours (its name carries a code), or an administrator's approval

NODE_RE = re.compile(r"^(?:!?([0-9a-fA-F]{8})|(?:mc[:-])?([0-9a-fA-F]{64}))$")


def claim_key(text):
    """The key a node is known by: !a1b2c3d4 (Meshtastic) or mc:<public key> (MeshCore); None when it is neither."""
    t = (text or "").strip()
    if t.startswith("mc-"):
        t = "mc:" + t[3:]
    m = NODE_RE.match(t)
    if not m:
        return None
    return ("!" + m.group(1).lower()) if m.group(1) else ("mc:" + m.group(2).lower())


def claim_slug(key):
    return ("mc-" + key[3:]) if key.startswith("mc:") else key


def claims_of(uid):
    return get_db().execute("SELECT * FROM mesh_claims WHERE owner_id=%s ORDER BY created", (uid,)).fetchall()


def all_claims():
    return get_db().execute("SELECT c.*, u.username AS owner_name, u.display_name AS owner_display FROM mesh_claims c JOIN users u ON u.id = c.owner_id ORDER BY c.created DESC").fetchall()


def create_claim(owner_id, node_text, by_admin=False, approve=False):
    """(id, error). The member gets a code to put in the node's name; an administrator may approve at once."""
    key = claim_key(node_text)
    if key is None:
        return None, "A Meshtastic node ID is eight hexadecimal characters, for example !a1b2c3d4; a MeshCore node is its 64 character public key."
    d = get_db()
    if not by_admin and d.execute("SELECT COUNT(*) AS n FROM mesh_claims WHERE owner_id=%s", (owner_id,)).fetchone()["n"] >= cfg_int("mesh_max_claims"):
        return None, f"You can claim {cfg_int('mesh_max_claims')} nodes."
    if d.execute("SELECT 1 FROM mesh_claims WHERE node_key=%s AND status='claimed'", (key,)).fetchone():
        return None, "That node is already claimed."
    if d.execute("SELECT 1 FROM mesh_claims WHERE node_key=%s AND owner_id=%s", (key, owner_id)).fetchone():
        return None, "You have already started a claim on that node."
    code = "ARK-" + secrets.token_hex(3).upper()
    cid = d.execute("INSERT INTO mesh_claims (owner_id, node_key, code, status, claimed_at) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (owner_id, key, code, "claimed" if approve else "pending", __import__("datetime").datetime.now(__import__("datetime").timezone.utc) if approve else None)).fetchone()["id"]
    d.commit()
    return cid, None


def check_claims(owner_id=None):
    """Complete the pending claims whose node now carries the code in its name. Returns how many were completed."""
    rows = get_db().execute("SELECT * FROM mesh_claims WHERE status='pending'" + (" AND owner_id=%s" if owner_id else ""), (owner_id,) if owner_id else ()).fetchall()
    n = 0
    for r in rows:
        d = node(claim_slug(r["node_key"]))
        nd = (d or {}).get("node") or {}
        if r["code"].lower() in ((nd.get("long_name") or "") + " " + (nd.get("short_name") or "")).lower():
            c = get_db()
            c.execute("UPDATE mesh_claims SET status='claimed', claimed_at=now() WHERE id=%s AND NOT EXISTS (SELECT 1 FROM mesh_claims x WHERE x.node_key=%s AND x.status='claimed')", (r["id"], r["node_key"]))
            c.commit()
            n += 1
    return n


def set_owner_hidden(claim, hide):
    """The owner of a claimed node takes it off (or puts it back on) every mesh page. Kept in the takedown list with the owner as the reason."""
    d = get_db()
    if hide:
        d.execute("INSERT INTO mesh_hidden (node_key, reason, added_by) VALUES (%s,%s,%s) ON CONFLICT (node_key) DO NOTHING", (claim["node_key"], "hidden by its owner", claim["owner_id"]))
        call("/hide", "POST", {"id": claim["node_key"], "reason": "hidden by its owner"})
    else:
        d.execute("DELETE FROM mesh_hidden WHERE node_key=%s AND reason=%s", (claim["node_key"], "hidden by its owner"))
        call("/unhide", "POST", {"id": claim["node_key"]})
    d.execute("UPDATE mesh_claims SET owner_hidden=%s WHERE id=%s", (bool(hide), claim["id"]))
    d.commit()
    from .cache import _store
    _store.clear()


def coverage():
    """[{id, network, name, lat, lon, sectors, heard, reach_km}]: what each gateway or observer with a known place was measured to hear, farthest per ten degrees (empty when the service is away)."""
    def load():
        ok, d = call("/coverage", timeout=10)
        return d.get("gateways", []) if ok and isinstance(d, dict) else []
    return cached("mesh_coverage", load, 300)


def health(rows=None):
    """What needs attention, from the nodes heard lately (see Settings > Mesh Nodes for the limits). rows: the node list (all networks, a week or more)."""
    import math
    import re as _re
    from datetime import datetime, timedelta, timezone
    rows = rows if rows is not None else nodes("", "", 168, False)
    now = datetime.now(timezone.utc)

    def age_h(n):
        try:
            return (now - datetime.fromisoformat(n["last_seen"].replace("Z", "+00:00"))).total_seconds() / 3600.0
        except (KeyError, ValueError, AttributeError):
            return 9e9
    isrouter = lambda n: (n.get("role_name") in ("Router", "Router and client", "Router (late)", "Repeater")) or (n.get("network") == "meshcore" and n.get("role_name") == "Repeater")
    low, busy_pct, off_h, gap = cfg_int("mesh_low_battery_pct"), cfg_int("mesh_busy_channel_pct"), cfg_int("mesh_offline_hours"), cfg_int("mesh_firmware_gap")
    routers = [n for n in rows if isrouter(n)]
    quiet = sorted([n for n in routers if age_h(n) > off_h], key=lambda n: n["last_seen"], reverse=True)
    lowb = sorted([n for n in rows if isinstance(n.get("battery"), int) and 0 < n["battery"] <= low and age_h(n) <= 48], key=lambda n: n["battery"])
    busy = sorted([n for n in rows if isinstance(n.get("channel_util"), (int, float)) and n["channel_util"] >= busy_pct and age_h(n) <= 48], key=lambda n: -n["channel_util"])
    ver = lambda n: tuple(int(x) for x in m.groups()[:3]) if (m := _re.match(r"^(\d+)\.(\d+)\.(\d+)", n.get("firmware") or "")) else None
    fws = [(ver(n), n) for n in rows if n.get("network") != "meshcore" and ver(n)]
    newest = max((v for v, _n in fws), default=None)
    old = sorted([n for v, n in fws if newest and (v[0] < newest[0] or (v[0] == newest[0] and newest[1] - v[1] >= gap))], key=lambda n: n.get("firmware") or "")
    # groups of nodes (a 0.1 degree cell, about 10 km) with no router or repeater near them
    rr = [(n["lat"], n["lon"]) for n in routers if n.get("lat") is not None]
    km = lambda a, b: math.hypot((a[0] - b[0]) * 111.2, (a[1] - b[1]) * 111.2 * math.cos(math.radians(a[0])))
    cells = {}
    for n in rows:
        if n.get("lat") is not None and age_h(n) <= 168 and not isrouter(n):
            cells.setdefault((round(n["lat"] * 10), round(n["lon"] * 10)), []).append((n["lat"], n["lon"]))
    gaps = []
    for pts in cells.values():
        la, lo = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        near = min((km((la, lo), r) for r in rr), default=None)
        if len(pts) >= 3 and (near is None or near / 1.609344 > cfg_int("mesh_router_gap_miles")):
            gaps.append({"lat": la, "lon": lo, "count": len(pts), "router_mi": (near / 1.609344) if near is not None else None})
    gaps.sort(key=lambda g: -g["count"])
    return {"total": len(rows), "days": 7, "active": sum(1 for n in rows if age_h(n) <= 24), "routers": len(routers), "routers_quiet": quiet, "low_battery": lowb, "low_pct": low, "busy": busy, "busy_pct": busy_pct,
            "old_fw": old, "fw_gap": gap, "newest_fw": ".".join(str(x) for x in newest) if newest else "", "offline_hours": off_h, "gaps": gaps[:20], "gap_mi": cfg_int("mesh_router_gap_miles")}


def stats():
    def load():
        ok, d = call("/stats", timeout=3)
        return {"ok": ok, "data": d if ok else None, "note": "" if ok else d}
    return cached("mesh_stats", load, 15)


def nodes(q="", role="", hours=None, placed=False, limit=3000, network=""):
    args = {"limit": limit}
    if network in ("meshtastic", "meshcore"):
        args["network"] = network
    if q:
        args["q"] = q
    if role != "":
        args["role"] = role
    if hours:
        args["hours"] = hours
    if placed:
        args["placed"] = "1"
    ok, d = call("/nodes?" + urllib.parse.urlencode(args))
    return d.get("nodes", []) if ok else []


def nodes_cached(q="", role="", hours=None, placed=False, network=""):
    return cached(("mesh_nodes", q, role, hours, placed, network), lambda: nodes(q, role, hours, placed, network=network), 20)


def node(node_id):
    ok, d = call("/nodes/" + urllib.parse.quote(node_id, safe=""))
    return d if ok else None


def hidden():
    return get_db().execute("SELECT h.node_key, h.reason, h.added, u.username FROM mesh_hidden h LEFT JOIN users u ON u.id = h.added_by ORDER BY h.node_key").fetchall()


def links(hours=None):
    """The direct links nodes reported with Neighbor Info within the window (default Settings > Mesh Nodes > Show Links Heard Within), as the service keeps them: {a, b, snr, last_seen, reported_by, count}; hidden nodes are left out."""
    if cfg("mesh_links_show") != "1":
        return []
    hours = hours or max(1, cfg_int("mesh_links_show_days")) * 24

    def load():
        ok, d = call(f"/links?hours={int(hours)}", timeout=10)
        return d.get("links", []) if ok and isinstance(d, dict) else []
    hid = {h["node_key"] for h in hidden()}
    return [x for x in cached(("mesh_links", hours), load, 60) if x["a"] not in hid and x["b"] not in hid]


def placed_links(hours=None):
    """The links whose two ends both have a position (so they can be drawn): [{a, b, a_lat, a_lon, b_lat, b_lon, a_name, b_name, snr, last_seen, both}] with each pair once (the stronger report when both ends reported)."""
    by = {n["id"]: n for n in nodes_cached("", "", 24 * max(1, cfg_int("mesh_links_show_days")), True, "meshtastic") if n.get("lat") is not None}
    best = {}
    for x in links(hours):
        a, b = by.get(x["a"]), by.get(x["b"])
        if not a or not b:
            continue
        key = tuple(sorted((x["a"], x["b"])))
        cur = best.get(key)
        if cur is None:
            best[key] = dict(a=x["a"], b=x["b"], a_lat=a["lat"], a_lon=a["lon"], b_lat=b["lat"], b_lon=b["lon"], a_name=a.get("short_name") or a.get("long_name") or x["a"], b_name=b.get("short_name") or b.get("long_name") or x["b"],
                             snr=x["snr"], last_seen=x["last_seen"], both=False)
        else:
            cur["both"] = True
            if x["snr"] is not None and (cur["snr"] is None or x["snr"] > cur["snr"]):
                cur["snr"] = x["snr"]
            cur["last_seen"] = max(cur["last_seen"], x["last_seen"])
    return list(best.values())


def overview():
    """Everything the Mesh overview shows, worked out from the nodes and the statistics the mesh service keeps (nothing here is typed in): {ok, note, tiles, roles, firmware, hardware, gateways, busiest, new, problems, links}."""
    from collections import Counter
    from datetime import datetime, timedelta, timezone
    st = stats()
    if not st["ok"]:
        return {"ok": False, "note": st["note"]}
    sd = st["data"]
    rows = nodes_cached("", "", 168, False)
    now = datetime.now(timezone.utc)

    def age_min(n):
        try:
            return (now - datetime.fromisoformat(n["last_seen"].replace("Z", "+00:00"))).total_seconds() / 60.0
        except (KeyError, ValueError, AttributeError):
            return 9e9

    def first_age_h(n):
        try:
            return (now - datetime.fromisoformat(n["first_seen"].replace("Z", "+00:00"))).total_seconds() / 3600.0
        except (KeyError, ValueError, AttributeError):
            return 9e9
    online = [n for n in rows if age_min(n) <= max(1, cfg_int("mesh_online_minutes"))]
    new = sorted([n for n in rows if first_age_h(n) <= 24], key=lambda n: n["first_seen"], reverse=True)
    fw = Counter()
    for n in rows:
        if n.get("network") != "meshcore" and n.get("firmware"):
            fw[".".join((n["firmware"].split(".") + ["", "", ""])[:3]).rstrip(".")] += 1
    gws = sd.get("gateways", [])
    day_ago = (now - timedelta(hours=24)).isoformat()
    h = health(rows)
    return {"ok": True, "note": "", "sd": sd, "online": len(online), "online_minutes": max(1, cfg_int("mesh_online_minutes")), "rows": len(rows),
            "gateways": {"meshtastic": sum(1 for g in gws if g["network"] == "meshtastic"), "meshcore": sum(1 for g in gws if g["network"] == "meshcore"), "active": sum(1 for g in gws if (g.get("last") or "") >= day_ago)},
            "busiest": sorted(gws, key=lambda g: -g["nodes"])[:5], "new": new[:8], "new_count": len(new),
            "groups": [(t, [(a, b, round(100 * b / max(x[1] for x in items))) for a, b in items]) for t, items in (("Roles and kinds (heard this week)", [(r["role"], r["n"]) for r in sd.get("roles", [])[:10]]), ("Firmware (Meshtastic)", fw.most_common(8)),
                                                                                                                  ("Hardware (Meshtastic, this week)", [(r["hardware"], r["n"]) for r in sd.get("hardware", [])[:8]])) if items] or [],
            "problems": {"quiet": len(h["routers_quiet"]), "low": len(h["low_battery"]), "busy": len(h["busy"]), "old": len(h["old_fw"]), "gaps": len(h["gaps"]), "low_pct": h["low_pct"], "off_h": h["offline_hours"], "busy_pct": h["busy_pct"], "gap_mi": h["gap_mi"]},
            "links": {"total": sd.get("links", 0), "day": sd.get("links_day", 0), "reporters": sd.get("link_reporters", 0), "shown": cfg("mesh_links_show") == "1"}}
