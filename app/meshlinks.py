"""Linking islanded mesh areas through the broker: two communities with no radio path between them (the Arkansas link channel and the Sheridan extension, for example) share one channel by having the mesh service carry
the packets on it from each community's topic to the other's.

How it is agreed. A Manager (or Owner) of one community proposes a link to another community for one of its channels; a Manager of the other accepts or declines; a site administrator then approves it (unless the site
does not ask for that: Settings > Mesh Nodes). Either community can pause, resume or remove it at any time, and an administrator can too. The mesh service (deploy/mesh/meshd.py) carries the packets exactly as they arrive:
they stay encrypted and the service never holds a channel key, so for a node on the far side to read them the two communities' channels must have the same name and the same key. Accepting a link therefore offers to
give the accepting community's channel the proposer's key (its members then scan the channel's code again); the portal checks that the two keys match and says when they do not. Packets are carried once (a
sender and packet id are remembered for a few minutes, whichever gateway heard them and whichever side they came from), a packet's hop limit is never raised and is at most the link's, each link has a rate limit, and
the service never reads again what it published itself. What the service cannot do without a channel key is to look at the kind of packet (text, position and so on): a link carries everything on its channel.
Every step is in the audit log; Service counters (carried each way, duplicates, limited, errors) are on the community's Linked Areas page."""
import threading
import time

from flask import Blueprint, abort, request
from flask_login import login_required

from . import mesh as ms
from .audit import audit
from .db import get_db
from .admin import permission_required
from .settings import cfg, cfg_int

bp = Blueprint("meshlinks", __name__)
LOCK_ID = 727123


def on():
    return cfg("mesh_enabled") == "1" and cfg("meshlink_enabled") == "1"


def _community(cid):
    return get_db().execute("SELECT id, slug, name FROM communities WHERE id=%s", (cid,)).fetchone()


def _channel(cid, name):
    return get_db().execute("SELECT * FROM community_channels WHERE community_id=%s AND lower(name)=lower(%s)", (cid, name)).fetchone()


def keys_match(link):
    from .communities import _psk
    a, b = _channel(link["a_id"], link["channel"]), _channel(link["b_id"], link["channel"])
    return bool(a and b and _psk(a) == _psk(b) and _psk(a) != b"")


def sync():
    """Tell the mesh service which links are active (none when the site switch is off). Returns the number of active links sent."""
    rows = []
    if on():
        rows = get_db().execute("SELECT l.*, a.slug AS a_slug, b.slug AS b_slug FROM mesh_area_links l JOIN communities a ON a.id = l.a_id JOIN communities b ON b.id = l.b_id WHERE l.state='active'").fetchall()
    ok, _r = ms.call("/links/set", "POST", {"links": [{"id": r["id"], "a": r["a_slug"], "b": r["b_slug"], "channel": r["channel"], "hop_limit": r["hop_limit"], "rate_per_min": r["rate_per_min"], "paused": r["paused"]} for r in rows]})
    return len(rows) if ok else -1


def stats():
    ok, d = ms.call("/links/stats")
    return (d.get("links") or {}) if ok and isinstance(d, dict) else {}


def _state_text(l):
    if l["state"] == "proposed":
        return "Waiting for the other community to answer"
    if l["state"] == "accepted":
        return "Accepted; waiting for a site administrator to approve"
    if l["state"] == "declined":
        return "Declined"
    if l["state"] == "rejected":
        return "Not approved by the site administrator"
    return "Paused" if l["paused"] else "Carrying packets"


def page_context(c):
    d = get_db()
    st = stats()
    rows = []
    for l in d.execute("SELECT l.*, a.name AS a_name, a.slug AS a_slug, b.name AS b_name, b.slug AS b_slug FROM mesh_area_links l JOIN communities a ON a.id = l.a_id JOIN communities b ON b.id = l.b_id WHERE l.a_id=%s OR l.b_id=%s ORDER BY l.id DESC", (c["id"], c["id"])).fetchall():
        l = dict(l)
        mine_a = l["a_id"] == c["id"]
        s = st.get(str(l["id"])) or {}
        l.update(other_name=l["b_name"] if mine_a else l["a_name"], other_slug=l["b_slug"] if mine_a else l["a_slug"], i_proposed=mine_a, text=_state_text(l), keys=keys_match(l) if l["state"] in ("proposed", "accepted", "active") else None,
                 out=s.get("a_to_b" if mine_a else "b_to_a", 0), inn=s.get("b_to_a" if mine_a else "a_to_b", 0), dup=s.get("duplicates", 0), limited=s.get("limited", 0), errors=s.get("errors", 0), last_error=s.get("last_error", ""))
        rows.append(l)
    return {"site_on": on(), "rows": rows, "others": d.execute("SELECT slug, name FROM communities WHERE published AND id <> %s ORDER BY lower(name)", (c["id"],)).fetchall(),
            "channels": d.execute("SELECT name FROM community_channels WHERE community_id=%s ORDER BY lower(name)", (c["id"],)).fetchall(), "approval": cfg("meshlink_admin_approval") == "1",
            "max_hops": cfg_int("meshlink_max_hops"), "default_hops": cfg_int("meshlink_default_hops"), "max_rate": cfg_int("meshlink_max_rate"), "limit": cfg_int("meshlink_max_per_community")}


def pending_for_admin():
    return get_db().execute("SELECT l.*, a.name AS a_name, b.name AS b_name FROM mesh_area_links l JOIN communities a ON a.id = l.a_id JOIN communities b ON b.id = l.b_id WHERE l.state='accepted' ORDER BY l.id").fetchall()


def _back(slug, ok, msg):
    from .communities import _back as back
    return back(slug, ok, msg, "links")


@bp.route("/mesh/c/<slug>/links/propose", methods=["POST"])
@login_required
def propose(slug):
    from .communities import _ctx
    c, _r = _ctx(slug, "manage_members")
    f = request.form
    d = get_db()
    if not on():
        return _back(slug, False, "The site administrator has not turned linking areas on.")
    other = d.execute("SELECT id, slug, name FROM communities WHERE slug=%s AND published", (f.get("other", ""),)).fetchone()
    if other is None or other["id"] == c["id"]:
        return _back(slug, False, "Choose another community to link with.")
    ch = _channel(c["id"], f.get("channel", ""))
    if ch is None:
        return _back(slug, False, "Choose one of your channels to share.")
    if d.execute("SELECT COUNT(*) AS n FROM mesh_area_links WHERE (a_id=%s OR b_id=%s) AND state IN ('proposed','accepted','active')", (c["id"], c["id"])).fetchone()["n"] >= cfg_int("meshlink_max_per_community"):
        return _back(slug, False, f"A community can have {cfg_int('meshlink_max_per_community')} links. Remove one first.")
    if d.execute("SELECT 1 FROM mesh_area_links WHERE lower(channel)=lower(%s) AND ((a_id=%s AND b_id=%s) OR (a_id=%s AND b_id=%s)) AND state IN ('proposed','accepted','active')", (ch["name"], c["id"], other["id"], other["id"], c["id"])).fetchone():
        return _back(slug, False, "These two communities already have a link on that channel.")
    hops = min(max(f.get("hops", type=int) or cfg_int("meshlink_default_hops"), 0), cfg_int("meshlink_max_hops"))
    rate = min(max(f.get("rate", type=int) or cfg_int("meshlink_max_rate"), 1), cfg_int("meshlink_max_rate"))
    from flask_login import current_user
    d.execute("INSERT INTO mesh_area_links (a_id, b_id, channel, hop_limit, rate_per_min, proposed_by) VALUES (%s,%s,%s,%s,%s,%s)", (c["id"], other["id"], ch["name"], hops, rate, current_user.id))
    d.commit()
    audit("community.link", target=slug, detail=f"proposed a link with {other['slug']} on {ch['name']}")
    return _back(slug, True, f"The link is proposed to {other['name']}. A Manager there has to accept it.")


@bp.route("/mesh/c/<slug>/links/<int:lid>/<action>", methods=["POST"])
@login_required
def act(slug, lid, action):
    from flask_login import current_user
    from .communities import _ctx, _psk
    c, _r = _ctx(slug, "manage_members")
    d = get_db()
    l = d.execute("SELECT * FROM mesh_area_links WHERE id=%s AND (a_id=%s OR b_id=%s)", (lid, c["id"], c["id"])).fetchone()
    if l is None or action not in ("accept", "decline", "pause", "resume", "remove", "cancel"):
        abort(404)
    mine_b = l["b_id"] == c["id"]
    other = _community(l["a_id"] if mine_b else l["b_id"])
    if action in ("accept", "decline"):
        if not (mine_b and l["state"] == "proposed"):
            return _back(slug, False, "There is nothing to answer.")
        if action == "decline":
            d.execute("UPDATE mesh_area_links SET state='declined', updated_at=now() WHERE id=%s", (lid,))
            d.commit()
            audit("community.link", target=slug, detail=f"declined the link from {other['slug']} on {l['channel']}")
            return _back(slug, True, "The link is declined.")
        mine, theirs = _channel(c["id"], l["channel"]), _channel(l["a_id"], l["channel"])
        if mine is None or theirs is None:
            return _back(slug, False, f"Your community needs a channel named {l['channel']} (with the same name as theirs) before you can accept.")
        if _psk(mine) != _psk(theirs):
            if request.form.get("adopt") != "1":
                return _back(slug, False, f"Your channel {mine['name']} has a different key from theirs, so the far side could not read the packets. Tick the box to give your channel their key (your members scan its code again), then accept.")
            d.execute("UPDATE community_channels SET key_kind=%s, psk_sealed=%s, rotated=now() WHERE id=%s", (theirs["key_kind"], theirs["psk_sealed"], mine["id"]))
            audit("community.channel", target=slug, detail=f"channel {mine['name']} now uses the key of {other['slug']} for the link")
        state = "accepted" if cfg("meshlink_admin_approval") == "1" else "active"
        d.execute("UPDATE mesh_area_links SET state=%s, accepted_by=%s, updated_at=now() WHERE id=%s", (state, current_user.id, lid))
        d.commit()
        audit("community.link", target=slug, detail=f"accepted the link from {other['slug']} on {l['channel']}")
        sync()
        return _back(slug, True, "The link is accepted." + (" A site administrator now has to approve it." if state == "accepted" else " It is carrying packets."))
    if action == "cancel":
        if l["state"] != "proposed" or mine_b:
            return _back(slug, False, "Only the community that proposed a link can take it back while it waits.")
        action = "remove"
    if action == "remove":
        d.execute("DELETE FROM mesh_area_links WHERE id=%s", (lid,))
    else:
        if l["state"] != "active":
            return _back(slug, False, "Only a link that is running can be paused or resumed.")
        d.execute("UPDATE mesh_area_links SET paused=%s, updated_at=now() WHERE id=%s", (action == "pause", lid))
    d.commit()
    audit("community.link", target=slug, detail=f"{action} the link with {other['slug']} on {l['channel']}")
    sync()
    return _back(slug, True, {"remove": "The link is removed.", "pause": "The link is paused: nothing is carried until it is resumed.", "resume": "The link is carrying packets again."}[action])


@bp.route("/admin/communities/links/<int:lid>/<action>", methods=["POST"])
@permission_required("manage_map_data")
def admin_act(lid, action):
    from flask import redirect, url_for, flash
    from flask_login import current_user
    d = get_db()
    l = d.execute("SELECT * FROM mesh_area_links WHERE id=%s AND state='accepted'", (lid,)).fetchone()
    if l is None or action not in ("approve", "reject"):
        abort(404)
    d.execute("UPDATE mesh_area_links SET state=%s, approved_by=%s, updated_at=now() WHERE id=%s", ("active" if action == "approve" else "rejected", current_user.id, lid))
    d.commit()
    audit("community.link", target=str(lid), detail=f"{action}d by the site administrator")
    sync()
    flash("The link is approved and carrying packets." if action == "approve" else "The link is rejected.", "success")
    return redirect(url_for("communities.admin"))


def start(app):
    def loop():
        time.sleep(100)
        while True:
            try:
                with app.app_context():
                    d = get_db()
                    if d.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (LOCK_ID,)).fetchone()["ok"]:
                        sync()   # the service keeps the list itself; this puts it right again after the service was rebuilt
                    d.commit()
            except Exception as e:
                print("mesh links:", type(e).__name__, str(e)[:160], flush=True)
            time.sleep(600)
    threading.Thread(target=loop, name="mesh-links", daemon=True).start()
