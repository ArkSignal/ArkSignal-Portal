"""Repeater directory pages: the list, a repeater's page, the map, a member's own page (submit, edit what they look after), and the administrators' queue.
The logic is in repeaters.py; terms for the sources are in DATA_SOURCES_TERMS.md."""
import re

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from . import repeaters as rp
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("repeaters", __name__)


def _on():
    if cfg("repeater_enabled") != "1":
        abort(404)


def _moderator():
    return current_user.is_authenticated and (current_user.has_permission("moderate_repeaters") or current_user.has_permission("manage_map_data"))


@bp.app_context_processor
def _helpers():
    return {"rp_mhz": rp.mhz, "rp_band": rp.band_of, "rp_kinds": dict(rp.KINDS), "rp_statuses": dict(rp.STATUSES)}


@bp.route("/repeaters/")
def overview():
    _on()
    a = request.args
    try:
        page = max(1, int(a.get("page", 1)))
    except ValueError:
        page = 1
    per = 50
    total, rows = rp.search(q=a.get("q", "").strip()[:60], mode=a.get("mode", ""), band=a.get("band", ""), kind=a.get("kind", ""), status=a.get("status", ""), state=a.get("state", ""),
                            city=a.get("city", "").strip()[:40], sort=a.get("sort", "freq"), direction="desc" if a.get("dir") == "desc" else "asc", limit=per, offset=(page - 1) * per,
                            group=int(a["group"]) if a.get("group", "").isdigit() else 0)
    states = [r["state"] for r in get_db().execute("SELECT DISTINCT state FROM repeaters WHERE approved AND state <> '' ORDER BY state").fetchall()]
    return render_template("repeaters.html", groups=rp.all_groups(), rgroups=rp.group_names([r["id"] for r in rows]), rows=rows, total=total, page=page, pages=max(1, -(-total // per)), args=a, modes=rp.MODES, kinds=rp.KINDS, statuses=rp.STATUSES, states=states,
                           counts=cached("rp_counts", rp.counts, 30), sort=a.get("sort", "freq"), direction="desc" if a.get("dir") == "desc" else "asc", credit=rp.CREDIT, bands=["10 m", "6 m", "2 m", "1.25 m", "70 cm", "33 cm", "23 cm"], mod=_moderator())


@bp.route("/repeaters/<int:rid>")
def detail(rid):
    _on()
    r = rp.get(rid)
    if r is None or not (r["approved"] or _moderator() or (current_user.is_authenticated and r["submitted_by"] == current_user.id)):
        abort(404)
    out, inp = rp.output_input(r)
    mine = current_user.is_authenticated and r["owner_id"] == current_user.id
    from . import repeater_digital as rd
    from . import dmrlists
    return render_template("repeater.html", linked=rp.groups_of(rid), all_groups=rp.all_groups(), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), can_edit=_can_edit(r) if current_user.is_authenticated else False, tg_catalog=dmrlists.catalog(), r=r, out=out, inp=inp, band=rp.band_of(out), attrs=rd.attrs_of(r), tgs=rd.talkgroups(rid), attr_keys=rd.ATTR_KEYS, call_lookup=cfg("callsign_lookup_enabled") == "1" and bool(r["callsign"]), report_kinds=rp.REPORT_KINDS, mod=_moderator(), mine=mine, history=rp.history(rid) if _moderator() or mine else [], credit=rp.CREDIT)


@bp.route("/repeaters/<int:rid>/position", methods=["POST"])
@login_required
def set_position(rid):
    """The person who looks after a repeater (or an editor) sets or corrects its position: picked on the map or from an address answer."""
    _on()
    r = rp.get(rid)
    if not _can_edit(r):
        abort(403)
    try:
        lat, lon = float(request.form.get("lat", "")), float(request.form.get("lon", ""))
    except ValueError:
        return action_response(False, "Pick a place on the map or from the address answers first.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return action_response(False, "That is not a position on Earth.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})
    d = get_db()
    d.execute("UPDATE repeaters SET lat=%s, lon=%s, updated=now() WHERE id=%s", (round(lat, 6), round(lon, 6), rid))
    rp.log_edit(rid, current_user.username, "position", f"{lat:.5f}, {lon:.5f}" + (f" (was {r['lat']:.5f}, {r['lon']:.5f})" if r["lat"] is not None else " (none before)"))
    d.commit()
    audit("repeater.position", target=str(rid), detail=f"{lat:.5f}, {lon:.5f}")
    return action_response(True, "Position saved.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})


@bp.route("/admin/repeaters/<int:rid>/position", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_position(rid):
    """From the queue of repeaters waiting for a position: one click puts a repeater on the place its city names, or on the numbers typed."""
    r = rp.get(rid)
    if r is None:
        abort(404)
    try:
        lat, lon = float(request.form.get("lat", "")), float(request.form.get("lon", ""))
    except ValueError:
        return action_response(False, "Enter the latitude and longitude, or use the suggested place.", redirect_endpoint="repeaters.admin")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return action_response(False, "That is not a position on Earth.", redirect_endpoint="repeaters.admin")
    d = get_db()
    d.execute("UPDATE repeaters SET lat=%s, lon=%s, updated=now() WHERE id=%s", (round(lat, 6), round(lon, 6), rid))
    rp.log_edit(rid, current_user.username, "position", f"{lat:.5f}, {lon:.5f} (from the queue" + (", the city's place)" if request.form.get("from") == "city" else ")"))
    d.commit()
    audit("repeater.position", target=str(rid), detail=f"{lat:.5f}, {lon:.5f}")
    return action_response(True, "Position saved.", redirect_endpoint="repeaters.admin")


@bp.route("/repeaters/<int:rid>/talkgroups/add", methods=["POST"])
@login_required
def talkgroup_add(rid):
    """The person who looks after a repeater (or an editor) adds a talkgroup on a time slot, from the standard and imported lists or by number and name."""
    _on()
    r = rp.get(rid)
    if not _can_edit(r):
        abort(403)
    from . import repeater_digital as rd, dmrlists
    f = request.form
    raw = (f.get("tg") or "").strip()
    tg = int(raw) if raw.isdigit() and len(raw) <= 9 else None
    try:
        slot = int(f.get("slot", "1"))
    except ValueError:
        slot = 0
    err = dmrlists.add_talkgroup(rid, slot, tg, f.get("name", ""), f.get("mode", "static"), current_user.username)
    return action_response(not err, err or "Talkgroup added.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})


@bp.route("/repeaters/<int:rid>/talkgroups/<int:row>/remove", methods=["POST"])
@login_required
def talkgroup_remove(rid, row):
    _on()
    r = rp.get(rid)
    if not _can_edit(r):
        abort(403)
    from . import dmrlists
    ok = dmrlists.remove_talkgroup(rid, row, current_user.username)
    return action_response(ok, "Talkgroup removed." if ok else "That talkgroup is not on this repeater.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})


@bp.route("/repeaters/<int:rid>/report", methods=["POST"])
@login_required
def report(rid):
    _on()
    r = rp.get(rid)
    if r is None or not r["approved"]:
        abort(404)
    f = request.form
    sug = {}
    for key, parse in (("freq", rp.parse_mhz), ("offset", rp.parse_offset)):
        if f.get("s_" + key, "").strip():
            v = parse(f.get("s_" + key))
            if v is None:
                return action_response(False, "That suggested " + ("frequency" if key == "freq" else "offset") + " is not a number in MHz.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})
            sug["freq_hz" if key == "freq" else "offset_hz"] = v
    if f.get("s_tone", "").strip():
        sug["tone"] = f.get("s_tone").strip().upper()[:8]
    ok, msg = rp.report(current_user, rid, f.get("kind", ""), f.get("note", ""), sug)
    if ok:
        audit("repeater.report", target=str(rid), detail=f.get("kind", ""))
    return action_response(ok, msg, redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})


@bp.route("/repeaters/<int:rid>/claim", methods=["POST"])
@login_required
def claim(rid):
    """A sysop asks to be the one who keeps this repeater current; an administrator approves."""
    _on()
    r = rp.get(rid)
    if r is None or not r["approved"]:
        abort(404)
    if r["owner_id"]:
        return action_response(False, "This repeater already has someone looking after it.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})
    if r["claim_status"] == "pending":
        return action_response(False, "A claim is already waiting for an administrator.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})
    get_db().execute("UPDATE repeaters SET owner_id=%s, claim_status='pending' WHERE id=%s", (current_user.id, rid))
    rp.log_edit(rid, current_user.username, "claim requested")
    get_db().commit()
    audit("repeater.claim", target=str(rid))
    return action_response(True, "Your claim was sent. An administrator will check it before you can edit this repeater.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})


def _plug_filters():
    a = request.args
    f = {"bands": [b for b in a.getlist("band") if b in rp.BANDS_ALL], "modes": [m for m in a.getlist("mode") if m in rp.MODES], "state": a.get("state", "AR").strip()[:2], "city": a.get("city", "").strip()[:40],
         "active": a.get("active") == "1", "mapped": a.get("mapped") == "1"}
    try:
        lat, lon, mi = float(a.get("lat", "")), float(a.get("lon", "")), float(a.get("miles", "0") or 0)
        if mi > 0 and abs(lat) <= 90 and abs(lon) <= 180:
            f["near"] = (lat, lon, min(mi, 2000.0))
    except ValueError:
        pass
    return f


def _plug_opts():
    a = request.args
    return {"power": "low" if a.get("power") == "low" else "high", "bw": "12.5" if a.get("bw") == "12.5" else "25", "rxgroup": a.get("rxgroup", "").strip()[:16], "radio_id": a.get("radio_id", "").strip()[:16],
            "no_dmr": a.get("dmr") == "0"}


@bp.route("/repeaters/export")
@login_required
def plug_export():
    """Pick repeaters by band, mode, place and status and download them as a radio's channel list (DM32), a CHIRP file or an RT Systems style file."""
    _on()
    from . import plugexport as pe
    f, o = _plug_filters(), _plug_opts()
    reps = pe.select(f)
    chans, tgs = pe.dm32_rows(reps, o)
    states = [r["state"] for r in get_db().execute("SELECT DISTINCT state FROM repeaters WHERE approved AND state <> '' ORDER BY state").fetchall()]
    return render_template("plug_export.html", f=f, o=o, n=len(reps), n_chan=len(chans), n_tg=len(tgs), n_fm=sum(1 for r in reps if "FM" in (r["modes"] or "FM").split(",")), bands=rp.BANDS_ALL, modes=rp.MODES, states=states,
                           comparison=pe.COMPARISON, formats=pe.FORMATS, args=request.args, qs=request.query_string.decode(), preview=chans[:12], mapped=sum(1 for r in reps if r["lat"] is not None))


@bp.route("/repeaters/export/<name>")
@login_required
def plug_download(name):
    _on()
    from flask import Response
    from . import plugexport as pe
    reps = pe.select(_plug_filters())
    if name == "dm32-channels.csv":
        body = pe.dm32_csv(reps, _plug_opts())[0]
    elif name == "dm32-talkgroups.csv":
        body = pe.dm32_csv(reps, _plug_opts())[1]
    elif name == "chirp.csv":
        body = pe.chirp_csv(reps)[0]
    elif name == "rtsystems.csv":
        body = pe.rt_csv(reps)[0]
    else:
        abort(404)
    resp = Response(body, mimetype="text/csv")
    resp.headers["Content-Disposition"] = f"attachment; filename=arksignal-{name}"
    return resp


@bp.route("/api/repeaters/links.json")
def api_links():
    """The groups of linked repeaters (a named system and the repeaters in it) with the lines that join them, for the map."""
    _on()
    return jsonify({"groups": cached("rp_links", rp.links_for_map, 30), "credit": rp.CREDIT})


def _clear_links_cache():
    from .cache import _store
    for k in [k for k in list(_store) if isinstance(k, str) and k.startswith("rp_")]:
        _store.pop(k, None)


@bp.route("/repeaters/<int:rid>/link", methods=["POST"])
@login_required
def link_repeater(rid):
    """Put this repeater in a linked system: an existing one, or a new one by name. The repeater's owner or a moderator."""
    _on()
    r = rp.get(rid)
    if r is None or not _can_edit(r):
        abort(404)
    f = request.form
    gid = int(f["group"]) if f.get("group", "").isdigit() else 0
    if not gid:
        gid, err = rp.make_group(f.get("name", ""), f.get("notes", ""), current_user.id)
        if err:
            return action_response(False, err, redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})
    rp.link(gid, rid, current_user.id)
    rp.log_edit(rid, current_user.username, "linked", f"added to linked system {gid}")
    get_db().commit()
    _clear_links_cache()
    return action_response(True, "The repeater is now part of that linked system.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})


@bp.route("/repeaters/<int:rid>/link/<int:gid>/remove", methods=["POST"])
@login_required
def unlink_repeater(rid, gid):
    _on()
    r = rp.get(rid)
    if r is None or not _can_edit(r):
        abort(404)
    rp.unlink(gid, rid)
    rp.log_edit(rid, current_user.username, "unlinked", f"removed from linked system {gid}")
    get_db().commit()
    _clear_links_cache()
    return action_response(True, "The repeater is no longer in that linked system.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})


@bp.route("/admin/repeaters/groups/add", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_group_add():
    """Make a linked system and put repeaters in it by their numbers (separated by commas or spaces)."""
    f = request.form
    gid, err = rp.make_group(f.get("name", ""), f.get("notes", ""), current_user.id)
    if err:
        return action_response(False, err, redirect_endpoint="repeaters.admin")
    ids = [int(x) for x in re.findall(r"\d+", f.get("members", ""))][:200]
    n = 0
    for rid in ids:
        if rp.get(rid):
            rp.link(gid, rid, current_user.id)
            n += 1
    _clear_links_cache()
    audit("repeaters.group_add", target=str(gid), detail=f"{n} repeaters")
    return action_response(True, f"Linked system saved with {n} repeater{'s' if n != 1 else ''} added.", redirect_endpoint="repeaters.admin")


@bp.route("/admin/repeaters/groups/<int:gid>/delete", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_group_delete(gid):
    rp.delete_group(gid)
    _clear_links_cache()
    audit("repeaters.group_delete", target=str(gid))
    return action_response(True, "Linked system deleted (the repeaters stay).", redirect_endpoint="repeaters.admin")


@bp.route("/repeaters/map")
def repeater_map():
    _on()
    from . import maplayers
    return render_template("repeaters_map.html", layers=maplayers.for_map(), label_scale=cfg("live_label_scale"), label_bg=cfg("live_label_bg"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"),
                           wx_refresh=cfg_int("weather_refresh_minutes"), credit=rp.CREDIT, modes=rp.MODES, bands=["10 m", "6 m", "2 m", "1.25 m", "70 cm", "33 cm", "23 cm"])


@bp.route("/api/repeaters/map.json")
def api_map():
    _on()
    return jsonify({"repeaters": cached("rp_map", rp.map_points, 30), "credit": rp.CREDIT})


# ------------------------------------------------------------------ members

def _my_rows():
    return get_db().execute("SELECT * FROM repeaters WHERE owner_id=%s OR submitted_by=%s ORDER BY freq_hz", (current_user.id, current_user.id)).fetchall()


@bp.route("/my/repeaters")
@login_required
def mine():
    _on()
    return render_template("my_repeaters.html", active="repeaters", rows=_my_rows(), kinds=rp.KINDS, statuses=rp.STATUSES, modes=rp.MODES, f={}, banner="", target=None, action="/my/repeaters/new", is_new=True)


def _can_edit(r):
    return r is not None and (r["owner_id"] == current_user.id and r["claim_status"] != "pending" or _moderator())


@bp.route("/my/repeaters/new", methods=["POST"])
@login_required
def submit():
    """A member submits a repeater; it waits for an editor unless the member is one."""
    _on()
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM repeaters WHERE submitted_by=%s AND created > now() - interval '1 day'", (current_user.id,)).fetchone()["n"] >= cfg_int("repeater_submissions_per_day"):
        return action_response(False, "You have submitted several repeaters today. Try again tomorrow.", redirect_endpoint="repeaters.mine")
    f, err = rp.clean(request.form)
    if err:
        return action_response(False, err, redirect_endpoint="repeaters.mine")
    dup = d.execute("SELECT id FROM repeaters WHERE freq_hz=%s AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s", (f["freq_hz"], f["lat"] - .05, f["lat"] + .05, f["lon"] - .06, f["lon"] + .06)).fetchone()
    if dup:
        return action_response(False, f"A repeater on {rp.mhz(f['freq_hz'])} MHz is already listed near there. Open it and use Report a Problem to correct it.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": dup["id"]})
    mod = _moderator()
    f["status"] = f["status"] if mod else "unverified"
    rid = rp.create(f, current_user.username, source="member", approved=mod, submitted_by=current_user.id, owner_id=None)
    if request.form.get("talkgroups") is not None:
        from . import repeater_digital as rd
        rd.set_talkgroups(rid, request.form.get("talkgroups"), current_user.username)
    audit("repeater.submit", target=str(rid), detail=rp.mhz(f["freq_hz"]) + " " + f["callsign"])
    return action_response(True, "Added to the directory." if mod else "Thank you. An editor will check it before it appears.", redirect_endpoint="repeaters.mine")


@bp.route("/my/repeaters/<int:rid>", methods=["GET", "POST"])
@login_required
def edit(rid):
    """The repeater's keeper (a claimed sysop) or an editor changes what is current about it, with the history kept."""
    _on()
    r = rp.get(rid)
    if r is None or not _can_edit(r):
        abort(404)
    if request.method == "POST":
        f, err = rp.clean(request.form)
        if err:
            return action_response(False, err, redirect_endpoint="repeaters.edit", redirect_kwargs={"rid": rid})
        allowed = None if _moderator() else rp.EDITABLE_BY_OWNER
        rp.update(rid, f, current_user.username, allowed)
        if request.form.get("talkgroups") is not None:
            from . import repeater_digital as rd
            count, tg_problems = rd.set_talkgroups(rid, request.form.get("talkgroups"), current_user.username)
            if tg_problems:
                return action_response(False, "Saved, but " + " ".join(tg_problems[:3]), redirect_endpoint="repeaters.edit", redirect_kwargs={"rid": rid})
        audit("repeater.edit", target=str(rid), detail=("for " + (r["owner_name"] or "") if r["owner_id"] != current_user.id else ""))
        return action_response(True, "Saved.", redirect_endpoint="repeaters.edit", redirect_kwargs={"rid": rid})
    banner = f"You are editing this repeater as an administrator; it is looked after by {r['owner_name']}." if r["owner_id"] and r["owner_id"] != current_user.id else ""
    return render_template("my_repeaters.html", active="repeaters", rows=[], kinds=rp.KINDS, statuses=rp.STATUSES, modes=rp.MODES, f=dict(r, tgtext=__import__("app.repeater_digital", fromlist=["x"]).talkgroups_text(rid)), banner=banner, target=r, action=f"/my/repeaters/{rid}", is_new=False, history=rp.history(rid))


# ------------------------------------------------------------------ administrators

@bp.route("/admin/repeaters")
@permission_required("moderate_repeaters")
def admin():
    d = get_db()
    reports = d.execute("SELECT t.*, r.callsign, r.freq_hz, r.city, u.username FROM repeater_reports t JOIN repeaters r ON r.id=t.repeater_id LEFT JOIN users u ON u.id=t.user_id WHERE t.status='open' ORDER BY t.created").fetchall()
    pending = d.execute("SELECT r.*, u.username AS who FROM repeaters r LEFT JOIN users u ON u.id=r.submitted_by WHERE NOT r.approved ORDER BY r.created").fetchall()
    claims = d.execute("SELECT r.*, u.username AS who, u.callsign AS who_call FROM repeaters r JOIN users u ON u.id=r.owner_id WHERE r.claim_status='pending' ORDER BY r.updated").fetchall()
    from .settings import get_setting
    from . import dmrlists
    waiting = d.execute("SELECT id, callsign, city, state, freq_hz, modes, source, created FROM repeaters WHERE approved AND lat IS NULL AND kind = 'repeater' ORDER BY created DESC, freq_hz LIMIT 300").fetchall()
    waiting_total = d.execute("SELECT COUNT(*) AS n FROM repeaters WHERE approved AND lat IS NULL AND kind = 'repeater'").fetchone()["n"]
    guess = {}
    from . import places
    for r in waiting:   # the place the city names, from the portal's own places list (no outside lookup), offered as a one-click answer
        key = ((r["city"] or "").strip(), (r["state"] or "").strip())
        if key[0] and key not in guess:
            hit = places.search(key[0] + (", " + key[1] if len(key[1]) == 2 else ""), limit=1)
            guess[key] = hit[0] if hit and hit[0]["kind"] == "place" else None
    return render_template("admin_repeaters.html", link_groups=[dict(g, members=rp.group_members(g["id"])) for g in rp.all_groups()], active="repeaters", waiting=waiting, waiting_total=waiting_total, guess=guess, dmr=dmrlists.counts(), counts=rp.counts(), reports=reports, pending=pending, claims=claims, kinds=rp.KINDS, statuses=rp.STATUSES, modes=rp.MODES, report_kinds=dict(rp.REPORT_KINDS),
                           last=get_setting("repeaters_last_result") or "", last_at=get_setting("repeaters_last_import") or "", enabled=cfg("repeater_enabled") == "1")


@bp.route("/admin/repeaters/add", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_add():
    """Add a repeater (to the directory, or to a member's list when a member is named, so they look after it)."""
    f, err = rp.clean(request.form)
    if err:
        return action_response(False, err, redirect_endpoint="repeaters.admin")
    owner = None
    uname = request.form.get("username", "").strip()
    if uname:
        u = get_db().execute("SELECT id FROM users WHERE lower(username)=lower(%s)", (uname,)).fetchone()
        if not u:
            return action_response(False, "No member with that user name.", redirect_endpoint="repeaters.admin")
        owner = u["id"]
    rid = rp.create(f, current_user.username, source="local", approved=True, submitted_by=owner, owner_id=owner)
    if request.form.get("talkgroups"):
        from . import repeater_digital as rd
        rd.set_talkgroups(rid, request.form.get("talkgroups"), current_user.username)
    if owner:
        get_db().execute("UPDATE repeaters SET claim_status='approved' WHERE id=%s", (rid,))
        get_db().commit()
    audit("repeater.admin_add", target=str(rid), detail=(("for " + uname + ": ") if uname else "") + rp.mhz(f["freq_hz"]) + " " + f["callsign"])
    return action_response(True, "Added.", redirect_endpoint="repeaters.detail", redirect_kwargs={"rid": rid})


@bp.route("/admin/repeaters/template.csv")
@permission_required("moderate_repeaters")
def admin_template():
    from flask import Response
    body = ",".join(rp.CSV_FIELDS) + "\n146.94,-0.6,100.0,W5ABC,Little Rock,AR,34.7465,-92.2896,FM,,,repeater,50 W,Open,Linked to the club net,active\n442.1,+5,,KG5XYZ,Conway,AR,,,DMR,1,Brandmeister,repeater,,,,unverified\n"
    return Response(body, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=repeaters-template.csv"})


@bp.route("/admin/repeaters/import-csv", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_import_csv():
    """Load a list a club or the repeater council gave us permission to use (a CSV file). Nothing is overwritten; repeaters already listed on the same frequency nearby are skipped."""
    f = request.files.get("file")
    label = request.form.get("label", "").strip()
    if not f or not label:
        return action_response(False, "Choose the file and say whose list it is (it is shown as the source of each repeater).", redirect_endpoint="repeaters.admin")
    try:
        text = f.read(3_000_000).decode("utf-8-sig")
    except UnicodeDecodeError:
        return action_response(False, "The file is not text (save it as CSV, UTF-8).", redirect_endpoint="repeaters.admin")
    c, problems = rp.import_csv(text, label, current_user.username)
    audit("repeater.import_csv", target=label[:60], detail=f"{c['added']} added, {c['known']} already listed, {len(problems)} problems")
    msg = f"{c['added']} added, {c['known']} already listed" + (f", {len(problems)} rows not taken: " + " ".join(problems[:5]) if problems else "") + "."
    return action_response(c["added"] > 0 or not problems, msg, redirect_endpoint="repeaters.admin")


@bp.route("/admin/repeaters/import-dmr", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_import_dmr():
    """The DMR talkgroup list and the contact list from files (a radio's exports, or RadioID-style and BrandMeister-style lists): the numbers behind names, kept for code plug imports and repeater talkgroup lists."""
    from . import dmrlists
    done = []
    f = request.files.get("talkgroups")
    if f and f.filename:
        done.append(f"{dmrlists.import_talkgroups(f.read(40_000_000).decode('utf-8-sig', 'replace'), 'file')} talkgroups")
    files = [x for x in request.files.getlist("contacts") if x and x.filename]
    if files:
        replace = request.form.get("replace") == "1"   # the first file replaces the whole list; the others add to it
        total = 0
        for k, x in enumerate(files):
            total += dmrlists.import_contacts(x.read(60_000_000).decode("utf-8-sig", "replace"), "file", replace=replace and k == 0)
        done.append(f"{total} contacts from {len(files)} file{'' if len(files) == 1 else 's'}" + (" (the old list replaced)" if replace else ""))
    if not done:
        return action_response(False, "Choose a talkgroup file, a contact file or both.", redirect_endpoint="repeaters.admin")
    audit("repeater.import_dmr", detail=", ".join(done))
    return action_response(True, "Kept: " + ", ".join(done) + ".", redirect_endpoint="repeaters.admin")


@bp.route("/admin/repeaters/import-plug", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_import_plug():
    """A radio's channel list (and optionally its contact list): the color codes, time slots, talkgroups and tones of the repeaters it holds, added to the repeaters already listed or as new ones waiting for a position."""
    from . import repeater_digital as rd
    f, cf = request.files.get("file"), request.files.get("contacts")
    label = request.form.get("label", "").strip()
    if not f or not label:
        return action_response(False, "Choose the channel list (CSV) and name it, for example the radio.", redirect_endpoint="repeaters.admin")
    try:
        text = f.read(5_000_000).decode("utf-8-sig", "replace")
        ctext = cf.read(2_000_000).decode("utf-8-sig", "replace") if cf and cf.filename else ""
    except Exception:
        return action_response(False, "The files are not text (save them as CSV).", redirect_endpoint="repeaters.admin")
    c, problems = rd.import_plug(text, ctext, label, current_user.username)
    audit("repeater.import_plug", target=label[:60], detail=f"{c['added']} added, {c['updated']} updated, {c['talkgroups']} talkgroups, {c['skipped']} channels skipped")
    return action_response(True, f"{c['added']} repeaters added (waiting for a position), {c['updated']} updated, {c['talkgroups']} talkgroups added, {c['skipped']} channels skipped (not named after a repeater's call sign).", redirect_endpoint="repeaters.admin")


@bp.route("/radio/")
def radio_overview():
    """The Radio overview: what the radio services hold and where to go, one card each (repeaters, APRS, frequencies, call signs and talkgroups), with the ones still to come."""
    from . import frequencies as fq, dmrlists, aprs as ap
    from .roadmap import coming_services
    on = cfg("repeater_enabled") == "1"
    rep = cached("rp_counts", rp.counts, 30) if on else None
    aprs = None
    if cfg("aprs_enabled") == "1":
        try:
            st = ap.stats()
            aprs = (st.get("data") or {}) if st.get("ok") else {}
        except Exception:
            aprs = {}
    nf = get_db().execute("SELECT count(*) AS n FROM frequencies").fetchone()["n"]
    extra = {"sw_on": cfg("spaceweather_enabled") == "1", "satpass_on": cfg("satpass_enabled") == "1", "satimg_on": cfg("satimg_enabled") == "1", "lists_on": cfg("radiolists_enabled") == "1", "sat": None, "nets": None}
    if extra["satimg_on"]:
        from . import satimages
        extra["sat"] = satimages.counts()
    if extra["lists_on"]:
        r_ = get_db().execute("SELECT COUNT(*) FILTER (WHERE kind='net') AS nets, COUNT(*) FILTER (WHERE kind='winlink') AS gateways FROM radio_entries WHERE status='approved'").fetchone()
        extra["nets"] = r_
    return render_template("radio_overview.html", rep=rep, aprs=aprs, aprs_on=cfg("aprs_enabled") == "1", nfreq=nf, dmr=dmrlists.counts(), lookup=cfg("callsign_lookup_enabled") == "1", rep_on=on, coming=[c for c in coming_services() if "mesh" in c.lower() or "acars" in c.lower()], **extra)


@bp.route("/api/space-weather.json")
def space_weather_json():
    """The current space weather as JSON (the Weather overview's tile reads it)."""
    from . import spaceweather as sw
    if cfg("spaceweather_enabled") != "1":
        abort(404)
    return jsonify(sw.current())


@bp.route("/radio/space-weather")
def space_weather():
    """Space weather for amateur radio: NOAA's storm scales, K index, solar flux and X-ray flares, with a rough HF band guide (app/spaceweather.py)."""
    from . import spaceweather as sw
    if cfg("spaceweather_enabled") != "1":
        abort(404)
    return render_template("space_weather.html", sw=sw.current())


@bp.route("/frequencies")
def frequencies_page():
    """Known frequencies in one list: APRS, amateur calling frequencies, FRS, GMRS, MURS, weather radio, marine and aviation, mesh."""
    from . import frequencies as fq
    service = request.args.get("service", "")
    q = " ".join(request.args.get("q", "").split())[:60]
    return render_template("frequencies.html", rows=fq.search(service, q), services=fq.SERVICES, service=service, q=q, mhz=fq.mhz, can_edit=current_user.is_authenticated and _moderator())


@bp.route("/frequencies/add", methods=["POST"])
@login_required
def frequency_add():
    if not _moderator():
        abort(403)
    from . import frequencies as fq
    f = request.form
    try:
        hz = round(float(f.get("mhz", "").replace(",", ".")) * 1e6)
    except ValueError:
        return action_response(False, "Give the frequency in MHz, for example 146.52.", redirect_endpoint="repeaters.frequencies_page")
    name = f.get("name", "").strip()[:80]
    if not name or not (1_000_000 <= hz <= 6_000_000_000) or f.get("service") not in dict(fq.SERVICES):
        return action_response(False, "Choose a service, give it a name and a frequency between 1 and 6000 MHz.", redirect_endpoint="repeaters.frequencies_page")
    get_db().execute("INSERT INTO frequencies (service, name, freq_hz, mode, notes) VALUES (%s,%s,%s,%s,%s)", (f["service"], name, hz, f.get("mode", "").strip()[:40], f.get("notes", "").strip()[:300]))
    get_db().commit()
    audit("frequency.add", target=name[:60], detail=f"{hz / 1e6:.4f} MHz")
    return action_response(True, "Frequency added.", redirect_endpoint="repeaters.frequencies_page")


@bp.route("/frequencies/<int:fid>/delete", methods=["POST"])
@login_required
def frequency_delete(fid):
    if not _moderator():
        abort(403)
    d = get_db()
    r = d.execute("DELETE FROM frequencies WHERE id=%s RETURNING name", (fid,)).fetchone()
    d.commit()
    if r:
        audit("frequency.delete", target=r["name"][:60])
    return action_response(bool(r), "Frequency removed." if r else "Not found.", redirect_endpoint="repeaters.frequencies_page")


@bp.route("/callsign")
def callsign_page():
    """Call Signs, Contacts and Talkgroups on one page: a US call sign's public license record (Callook.info, the FCC's license data) beside what this site knows about it; the DMR contact list (for signed-in
    members); and the talkgroups, the standard ones and the imported list, with the repeaters that carry each."""
    from . import repeater_digital as rd, dmrlists
    tab = request.args.get("tab", "call")
    if tab not in ("call", "contacts", "talkgroups"):
        tab = "call"
    ctx = {"tab": tab, "call": "", "info": None, "here": [], "lookup_on": cfg("callsign_lookup_enabled") == "1"}
    if tab == "call":
        if not ctx["lookup_on"]:
            abort(404)
        call = re.sub(r"[^A-Za-z0-9]", "", request.args.get("call", "")).upper()[:10]
        ctx.update(call=call, info=rd.callsign_info(call) if call else None, here=rd.portal_matches(call, current_user.is_authenticated) if call else [], prefix=rd.portal_prefix(call, current_user.is_authenticated) if len(call) >= 2 else [])
    elif tab == "contacts":
        q = " ".join(request.args.get("q", "").split())[:60]
        rows, total = [], dmrlists.counts()["contacts"]
        if current_user.is_authenticated and q:
            like = "%" + q.replace("%", "").replace("_", "") + "%"
            rows = get_db().execute("SELECT id, call, name, city, state, country, kind, remark FROM dmr_contacts WHERE call ILIKE %s OR name ILIKE %s OR remark ILIKE %s OR id::text LIKE %s OR city ILIKE %s OR state ILIKE %s ORDER BY (upper(call) = upper(%s)) DESC, call, name LIMIT 300", (like, like, like, q.replace("%", "") + "%", like, like, q)).fetchall()
        ctx.update(q=q, rows=rows, total_contacts=total)
    else:
        cat, carried = dmrlists.catalog(), dmrlists.carried_by()
        q = " ".join(request.args.get("q", "").split())[:60].lower()
        if q:
            cat = [c for c in cat if q in c["name"].lower() or q in str(c["id"])]
        ctx.update(q=q, cat=cat[:400], carried=carried, total_tg=len(dmrlists.catalog()))
    return render_template("callsign.html", **ctx)


@bp.route("/api/callsign/<call>.json")
def api_callsign(call):
    """What the FCC's license search says about a US call sign (kept for a while); {} when there is nothing."""
    if cfg("callsign_lookup_enabled") != "1":
        abort(404)
    from . import repeater_digital as rd
    info = rd.callsign_info(call)
    resp = jsonify({"callsign": info} if info else {"callsign": None})
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.route("/admin/repeaters/report/<int:rid>/<action>", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_report(rid, action):
    if action not in ("apply", "reject"):
        abort(404)
    ok = rp.handle_report(rid, current_user.username, action == "apply")
    audit("repeater.report_" + action, target=str(rid))
    return action_response(ok, "Report applied." if action == "apply" else "Report closed.", redirect_endpoint="repeaters.admin")


@bp.route("/admin/repeaters/<int:rid>/<action>", methods=["POST"])
@permission_required("moderate_repeaters")
def admin_action(rid, action):
    d = get_db()
    r = rp.get(rid)
    if r is None:
        abort(404)
    who = current_user.username
    if action == "approve":
        d.execute("UPDATE repeaters SET approved=TRUE, updated=now() WHERE id=%s", (rid,)); rp.log_edit(rid, who, "approved"); msg = "Approved; it is in the directory now."
    elif action == "reject":
        d.execute("DELETE FROM repeaters WHERE id=%s AND NOT approved", (rid,)); msg = "Submission removed."
    elif action == "verify":
        rp.verify(rid, who); msg = "Marked as verified today."
    elif action == "claim-approve":
        d.execute("UPDATE repeaters SET claim_status='approved' WHERE id=%s", (rid,)); rp.log_edit(rid, who, "claim approved", r["owner_name"] or ""); msg = "Claim approved: they can now keep it current."
    elif action == "claim-deny":
        d.execute("UPDATE repeaters SET owner_id=NULL, claim_status='' WHERE id=%s", (rid,)); rp.log_edit(rid, who, "claim denied", r["owner_name"] or ""); msg = "Claim denied."
    elif action == "delete":
        d.execute("DELETE FROM repeaters WHERE id=%s", (rid,)); msg = "Deleted."
    else:
        abort(404)
    d.commit()
    audit("repeater." + action.replace("-", "_"), target=str(rid), detail=f"{rp.mhz(r['freq_hz'])} {r['callsign']}")
    return action_response(True, msg, redirect_endpoint="repeaters.admin")
