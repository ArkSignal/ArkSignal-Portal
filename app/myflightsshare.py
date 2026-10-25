"""Sharing My Flights: a member can give out an unlisted, read-only link to one flight or to the whole diary.

The link holds a long random token; only a keyed hash of it is stored (like the community invitations), so it can be shown once and never read back. A link can have an expiry and is revoked by removing it. The owner
chooses what shows: the route, the times, the aircraft and the airline always; the recorded track, the registration, the people on board and the notes only when switched on (people and notes start off). What is
switched off is taken out of the page and the data before it is built, not hidden by the page. Shared pages are never indexed, never cached, and send no referrer."""
import json
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, flash, jsonify, render_template, request
from flask_login import current_user, login_required

from . import vault
from .action_response import action_response
from .audit import audit
from .db import get_db
from .myflights import open_pax, open_row, _load, _on, _own, _pax_of, _summary, derive, dist_txt, hm, map_data, on as _myflights_on, stats_ctx
from .myflightstracks import STAGES, track_of, track_stats
from .settings import DEFAULTS, cfg, cfg_int, get_setting

bp = Blueprint("myflightsshare", __name__)
PURPOSE = "mfshare"
EXPIRY_CHOICES = [(7, "7 days"), (30, "30 days"), (90, "90 days"), (365, "1 year"), (0, "Never")]


def sharing_on():
    return _myflights_on() and cfg("myflights_sharing") == "1"


def public_sharing_on():
    """The same question for a visitor with no account: the saved switches, not the Feature Access answer (a guest is not allowed My Flights, but the link's owner made it)."""
    return get_setting("myflights_enabled", DEFAULTS["myflights_enabled"]) == "1" and get_setting("myflights_sharing", DEFAULTS["myflights_sharing"]) == "1"


@bp.after_request
def _private_headers(resp):
    if request.path.startswith("/share/flights/"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _allowed_days():
    """The expiry choices this site offers: with a longest life set, nothing longer (and no Never)."""
    cap = cfg_int("myflights_share_max_days")
    return [(d, t) for d, t in EXPIRY_CHOICES if (d != 0 and (cap == 0 or d <= cap)) or (d == 0 and cap == 0)]


# ------------------------------------------------------------------ the member's own page

@bp.route("/my/flights/shares")
@login_required
def page():
    _on()
    if not sharing_on():
        abort(404)
    d = get_db()
    rows = [dict(r) for r in d.execute(
        "SELECT s.*, f.from_code, f.to_code FROM my_flight_shares s LEFT JOIN my_flights f ON f.id = s.flight_id WHERE s.user_id=%s ORDER BY s.created_at DESC", (current_user.id,)).fetchall()]
    now = datetime.now(timezone.utc)
    for r in rows:
        r["expired"] = bool(r["expires_at"] and r["expires_at"] <= now)
    pick = None
    fid = request.args.get("flight", type=int)
    if fid:
        pick, _p = _load(fid)
    return render_template("my_flight_shares.html", rows=rows, pick=pick, expiry=_allowed_days(), limit=cfg_int("myflights_max_shares"), active="flightshares", default_days=next((d for d, _t in _allowed_days() if d == 30), _allowed_days()[0][0]))


@bp.route("/my/flights/shares/create", methods=["POST"])
@login_required
def create():
    _on()
    if not sharing_on():
        abort(404)
    f = request.form
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM my_flight_shares WHERE user_id=%s", (current_user.id,)).fetchone()["n"] >= cfg_int("myflights_max_shares"):
        return action_response(False, f"You can keep {cfg_int('myflights_max_shares')} share links. Remove one to make another.", redirect_endpoint="myflightsshare.page")
    fid = f.get("flight_id", type=int)
    if fid:
        row, _p = _load(fid)
        what = f"{row['from_code']} to {row['to_code']}"
    else:
        fid, what = None, "the diary"
    days = f.get("days", type=int)
    if days not in {x for x, _t in _allowed_days()}:
        return action_response(False, "Choose how long the link lasts.", redirect_endpoint="myflightsshare.page", redirect_kwargs={"flight": fid} if fid else None)
    token = secrets.token_urlsafe(32)
    flags = {k: f.get(k) == "1" for k in ("show_track", "show_reg", "show_people", "show_notes")}
    d.execute("INSERT INTO my_flight_shares (user_id, flight_id, token_hash, label, show_track, show_reg, show_people, show_notes, expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (current_user.id, fid, vault.keyed_hash(token, PURPOSE), " ".join((f.get("label") or "").split())[:80], flags["show_track"], flags["show_reg"], flags["show_people"], flags["show_notes"],
               (datetime.now(timezone.utc) + timedelta(days=days)) if days else None))
    d.commit()
    audit("myflights.share_create", target=current_user.username, detail=f"{what}; " + ", ".join(k[5:] for k, v in flags.items() if v) + (f"; {days} days" if days else "; no expiry"))
    from .seo import base_url
    link = base_url() + "/share/flights/" + token
    flash("Share link (shown once, copy it now): " + link, "success")
    return action_response(True, "The share link is made.", redirect_endpoint="myflightsshare.page")


@bp.route("/my/flights/shares/<int:sid>/revoke", methods=["POST"])
@login_required
def revoke(sid):
    _on()
    d = get_db()
    row = d.execute("DELETE FROM my_flight_shares WHERE id=%s AND user_id=%s RETURNING flight_id", (sid, current_user.id)).fetchone()
    d.commit()
    if row is None:
        abort(404)
    audit("myflights.share_revoke", target=current_user.username, detail="a flight" if row["flight_id"] else "the diary")
    return action_response(True, "The link is removed. Anyone who has it now gets a page not found.", redirect_endpoint="myflightsshare.page")


# ------------------------------------------------------------------ what a visitor sees

def _share(token):
    """The share a token opens, with its owner's id; a missing, expired or switched-off share is a plain 404 whatever the reason."""
    if not public_sharing_on() or not token or len(token) > 100:
        abort(404)
    d = get_db()
    s = d.execute("SELECT * FROM my_flight_shares WHERE token_hash=%s", (vault.keyed_hash(token, PURPOSE),)).fetchone()
    if s is None or (s["expires_at"] and s["expires_at"] <= datetime.now(timezone.utc)):
        abort(404)
    if d.execute("SELECT 1 FROM users WHERE id=%s", (s["user_id"],)).fetchone() is None:
        abort(404)
    d.execute("UPDATE my_flight_shares SET views = views + 1, last_viewed = now() WHERE id=%s", (s["id"],))
    d.commit()
    return dict(s)


def _redact(row, s):
    row = dict(row)
    if not s["show_reg"]:
        row["registration"] = ""
    if not s["show_notes"]:
        row["notes"] = ""
    return row


def _flight_view(s, fid, base):
    d = get_db()
    row = d.execute("SELECT * FROM my_flights WHERE id=%s AND user_id=%s", (fid, s["user_id"])).fetchone()
    if row is None or (s["flight_id"] and s["flight_id"] != fid):
        abort(404)
    row = _redact(open_row(row), s)
    pax = [open_pax(p) for p in d.execute("SELECT name, klass, reason, seat, seat_kind FROM my_flight_pax WHERE flight_id=%s ORDER BY id", (fid,)).fetchall()] if s["show_people"] else []
    tr = track_of(fid) if s["show_track"] else None
    if tr:
        tr["source"] = ""
    row["when"] = row.get("act_out") or row.get("sched_out") or row.get("act_off") or row.get("sched_off") or row.get("act_in") or row.get("sched_in")
    return render_template("my_flight.html", f=row, pax=pax, der=derive(row), track=tr, stats=track_stats(tr["points"]) if tr else None, hm=hm, dist=dist_txt, stages=STAGES, shared=True,
                           track_url=f"{base}/track/{fid}.json", diary_url=base if not s["flight_id"] else "", map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), active="myflights")


def _tell_owner(s):
    """The owner may hear that a link was opened: once a day for each link."""
    try:
        from . import pushkinds
        if pushkinds.wants(s["user_id"], "myflights_share") and pushkinds._seen(s["user_id"], "myflights_share", f"{s['id']}|{datetime.now(timezone.utc).strftime('%Y%m%d')}"):
            pushkinds.notify(s["user_id"], "myflights_share", "Your shared flights were opened", (s["label"] + ": " if s["label"] else "") + "Someone opened the link you shared.", "/my/flights/shares", "flightshare-" + str(s["id"]))
        get_db().commit()
    except Exception:
        pass


@bp.route("/share/flights/<token>")
def view(token):
    s = _share(token)
    base = "/share/flights/" + token
    _tell_owner(s)
    if s["flight_id"]:
        return _flight_view(s, s["flight_id"], base)
    rows, years = _own(request.args.get("year", type=int), s["user_id"])
    year = request.args.get("year", type=int)
    data = map_data(rows)
    if not s["show_track"]:
        data["tracks"] = []
    rows = [_redact(r, s) for r in rows]
    d = get_db()
    pax = _pax_of(d, s["user_id"]) if s["show_people"] else {}
    ctx = stats_ctx(rows, pax)
    return render_template("my_share_diary.html", rows=rows, pax=pax, data=data, years=years, year=year, summary=_summary(rows), dist=dist_txt, hm=hm, base=base, share=s, label=s["label"],
                           map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), **ctx)


@bp.route("/share/flights/<token>/f/<int:fid>")
def view_flight(token, fid):
    s = _share(token)
    if s["flight_id"]:
        abort(404)
    return _flight_view(s, fid, "/share/flights/" + token)


@bp.route("/share/flights/<token>/track/<int:fid>.json")
def track(token, fid):
    s = _share(token)
    if not s["show_track"] or (s["flight_id"] and s["flight_id"] != fid):
        abort(404)
    if get_db().execute("SELECT 1 FROM my_flights WHERE id=%s AND user_id=%s", (fid, s["user_id"])).fetchone() is None:
        abort(404)
    tr = track_of(fid)
    if tr is None:
        abort(404)
    return jsonify({"points": tr["points"], "source": ""})
