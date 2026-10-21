"""Licensed-amateur check for the radio bridge: APRS may only be sent by a verified, licensed operator.

A member's profile call sign is looked up in the FCC's public amateur license data (Callook.info, the same source as the Call Sign Lookup; see DATA_SOURCES_TERMS.md). The license must be ACTIVE (not expired,
not cancelled, an individual and not a club) and the licensee's name must match the name on the member's profile (middle initials, suffixes like Jr. and the order of the names do not matter; how strict the
match is, is a setting). A good match is verified. A name that does not match, a profile name that is too short to compare, or a club license is "needs review": an administrator decides (Admin > Radio Bridges),
nothing is ever verified by guessing. A license that is not found, has expired or is no longer active is "lapsed". The status belongs to one call sign and one profile name: changing either puts the member
back to "unverified" until they check again. A Data Updates job re-checks everyone weekly (a setting) so a lapsed license stops the sending; if the lookup service is down, the last status is kept and a warning
is shown. Administrators can approve a needs-review member and revoke any member, with a reason, and everything is in the audit log. US call signs only: the FCC data covers US licenses.
Statuses: unverified, verified, review, lapsed, revoked."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from .admin import permission_required
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int, get_setting, set_setting

bp = Blueprint("bridge_verify", __name__)

STATUS_WORDS = {"unverified": "Not checked yet", "verified": "Verified", "review": "Needs an administrator's review", "lapsed": "License not active", "revoked": "Revoked by an administrator"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "md", "phd", "dds", "esq"}


def required():
    return cfg("bridge_verify_required") == "1"


def base_call(text):
    """The call sign without a -SSID, letters and digits only, upper case."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper().split("-")[0])


def _tokens(name):
    t = re.sub(r"[^a-z\s]", " ", (name or "").lower().replace("-", " ").replace("'", ""))
    return [x for x in t.split() if x not in SUFFIXES]


def names_match(profile, licensee, mode="normal"):
    """(matches, problem): does the profile name fit the FCC licensee name? Initials are ignored; the profile needs a first and a last name."""
    p = [t for t in _tokens(profile) if len(t) > 1]
    lic = [t for t in _tokens(licensee) if len(t) > 1]
    if len(p) < 2:
        return False, "Your profile name needs a first and a last name to be compared with the license."
    if not lic:
        return False, "The license record has no personal name."
    first, last = p[0], p[-1]
    if mode == "strict":
        ok = set(p) == set(lic)
    elif mode == "loose":
        ok = last in lic and (first in lic or any(t.startswith(first[0]) for t in lic))
    else:
        ok = first in lic and last in lic
    return ok, "" if ok else "The name on your profile does not match the name on the license."


def _parse_date(text):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((text or "").strip(), fmt).date()
        except ValueError:
            pass
    return None


def lookup(call):
    """(kind, data): kind is 'ok' (data is Callook's answer), 'none' (no such license) or 'down' (the service did not answer; data is the reason). Always asks the service: a check is never answered from the cache."""
    url = cfg("callsign_lookup_url").rstrip("/") + "/" + urllib.parse.quote(call) + "/json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=cfg_int("bridge_verify_timeout_seconds")) as r:
            j = json.loads(r.read(200_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return "down", f"the license service answered {e.code}"
    except Exception as e:
        return "down", f"the license service did not answer ({type(e).__name__})"
    if not isinstance(j, dict):
        return "down", "the license service sent something unexpected"
    if j.get("status") in ("VALID", "UPDATING") and j.get("name"):
        return "ok", j
    if j.get("status") == "INVALID":
        return "none", j
    return "down", "the license service sent something unexpected"


def evaluate(call, profile_name, existing_approval=False):
    """The result of one check without touching the database: {status, reason, licensee, expires, down}."""
    kind, j = lookup(call)
    if kind == "down":
        return {"down": True, "reason": j}
    if kind == "none":
        return {"status": "lapsed", "reason": f"The FCC data has no active amateur license for {call}.", "licensee": "", "expires": None}
    licensee = (j.get("name") or "").title() if (j.get("name") or "").isupper() else (j.get("name") or "")
    exp = _parse_date((j.get("otherInfo") or {}).get("expiryDate"))
    if exp and exp < date.today():
        return {"status": "lapsed", "reason": f"The license for {call} expired on {exp.isoformat()}.", "licensee": licensee, "expires": exp}
    if (j.get("type") or "").upper() not in ("PERSON", ""):
        return {"status": "review", "reason": f"{call} is a {(j.get('type') or 'club').title()} license, not an individual's, so an administrator must decide.", "licensee": licensee, "expires": exp}
    ok, why = names_match(profile_name, licensee, cfg("bridge_verify_name_match"))
    if ok or existing_approval:
        return {"status": "verified", "reason": "Active license" + ("" if ok else " (the name was approved by an administrator)") + f", valid to {exp.isoformat()}." if exp else "Active license.", "licensee": licensee, "expires": exp}
    return {"status": "review", "reason": why + " An administrator will look at it.", "licensee": licensee, "expires": exp}


def _row(uid):
    return get_db().execute("SELECT id, username, display_name, callsign, ham_status, ham_call, ham_name, ham_checked, ham_expires, ham_reason, ham_licensee, ham_override, ham_by, ham_warn FROM users WHERE id=%s", (uid,)).fetchone()


def state(uid):
    """The member's effective status now: a status counts only for the call sign and profile name it was checked for."""
    r = _row(uid)
    if r is None:
        return "unverified"
    call = base_call(r["callsign"])
    if r["ham_override"] == "revoked":
        return "revoked"
    if not call or r["ham_call"] != call:
        return "unverified"
    if r["ham_override"] != "approved" and (r["ham_name"] or "") != (r["display_name"] or ""):
        return "unverified"
    return r["ham_status"]


def verified(uid):
    """May this member transmit through the bridge? (Always yes when the setting does not require a check.)"""
    return (not required()) or state(uid) == "verified"


def check(uid, by=None):
    """Look the member up, store the answer and return the new status. By an administrator when `by` is a name. When the service is down the last status is kept and a warning is stored."""
    d = get_db()
    r = _row(uid)
    call = base_call(r["callsign"])
    if not call:
        return {"status": "unverified", "reason": "There is no call sign on the profile."}
    approved = r["ham_override"] == "approved" and r["ham_call"] == call
    if r["ham_override"] == "revoked":
        return {"status": "revoked", "reason": "An administrator revoked this member."}
    res = evaluate(call, r["display_name"] or "", approved)
    now = datetime.now(timezone.utc)
    if res.get("down"):
        d.execute("UPDATE users SET ham_warn=%s WHERE id=%s", (f"The last check on {now.date().isoformat()} could not reach the license service: {res['reason']}. The previous status is kept.", uid))
        d.commit()
        set_setting("bridge_verify_last_error", f"{now.isoformat()}|{res['reason']}")
        return {"status": state(uid), "down": True, "reason": res["reason"]}
    set_setting("bridge_verify_last_ok", now.isoformat())
    d.execute("UPDATE users SET ham_status=%s, ham_call=%s, ham_name=%s, ham_checked=now(), ham_expires=%s, ham_reason=%s, ham_licensee=%s, ham_warn='', ham_override=CASE WHEN %s THEN ham_override ELSE '' END WHERE id=%s",
              (res["status"], call, r["display_name"] or "", res["expires"], res["reason"], res["licensee"], approved and res["status"] == "verified", uid))
    d.commit()
    audit("bridge.verify", target=r["username"], detail=f"{call}: {res['status']}", actor_name=by or (current_user.username if current_user and current_user.is_authenticated else "system"))
    return res


def recheck_all(limit=None):
    """The scheduled job: check everyone who has been checked before (and not revoked). Returns a short summary."""
    d = get_db()
    ids = [x["id"] for x in d.execute("SELECT id FROM users WHERE ham_call <> '' AND ham_status IN ('verified','review','lapsed') AND ham_override <> 'revoked' AND NOT disabled ORDER BY ham_checked NULLS FIRST LIMIT %s",
                                      (limit or cfg_int("bridge_verify_batch"),)).fetchall()]
    n = {"verified": 0, "review": 0, "lapsed": 0, "down": 0}
    for uid in ids:
        res = check(uid, by="schedule")
        n["down" if res.get("down") else res["status"]] = n["down" if res.get("down") else res["status"]] + 1
        time.sleep(0.4)
    return f"{len(ids)} checked: {n['verified']} verified, {n['review']} need review, {n['lapsed']} lapsed, {n['down']} not reachable"


def counts():
    return get_db().execute("SELECT COUNT(*) FILTER (WHERE ham_status='verified' AND ham_override <> 'revoked') AS verified, COUNT(*) FILTER (WHERE ham_status='review' AND ham_override <> 'revoked') AS review, "
                            "COUNT(*) FILTER (WHERE ham_status='lapsed') AS lapsed, COUNT(*) FILTER (WHERE ham_override='revoked') AS revoked FROM users WHERE ham_call <> '' OR ham_override <> ''").fetchone()


def member_context(uid):
    r = _row(uid)
    st = state(uid)
    why = r["ham_reason"] if st == r["ham_status"] else {"unverified": "Your call sign or your profile name changed since the last check, so it has to be checked again." if r["ham_call"] else "Not checked yet."}.get(st, r["ham_reason"])
    if st == "revoked":
        why = r["ham_reason"] or "An administrator revoked this member's APRS sending."
    return {"status": st, "word": STATUS_WORDS[st], "reason": why, "checked": r["ham_checked"], "expires": r["ham_expires"], "licensee": r["ham_licensee"] if st in ("verified", "review") else "", "by": r["ham_by"], "warn": r["ham_warn"]}


# ------------------------------------------------------------------ the member's Check button

@bp.route("/my/bridges/verify", methods=["POST"])
@login_required
def verify_me():
    if cfg("chat_enabled") != "1" or cfg("bridge_aprs_enabled") != "1":
        abort(404)
    r = _row(current_user.id)
    if not base_call(r["callsign"]):
        return jsonify({"ok": False, "message": "Put your amateur radio call sign on your Profile first."}), 400
    if r["ham_checked"] and (datetime.now(timezone.utc) - r["ham_checked"]).total_seconds() < cfg_int("bridge_verify_cooldown_minutes") * 60 and r["ham_call"] == base_call(r["callsign"]) and (r["ham_name"] or "") == (r["display_name"] or ""):
        return jsonify({"ok": False, "message": f"It was checked a moment ago. You can check again after {cfg_int('bridge_verify_cooldown_minutes')} minutes."}), 400
    res = check(current_user.id)
    if res.get("down"):
        return jsonify({"ok": False, "message": "The license service did not answer just now. Try again later."}), 503
    return jsonify({"ok": True, "message": STATUS_WORDS.get(res["status"], res["status"]) + ". " + res["reason"], "status": res["status"]})


# ------------------------------------------------------------------ the administrators' page

def _admin_ctx():
    rows = get_db().execute("SELECT id, username, display_name, callsign, ham_status, ham_call, ham_checked, ham_expires, ham_reason, ham_licensee, ham_override, ham_by, ham_warn, bridge_aprs_ok FROM users "
                            "WHERE ham_call <> '' OR ham_override <> '' OR bridge_aprs_ok ORDER BY (ham_status = 'review') DESC, lower(username)").fetchall()
    return {"rows": [dict(r, eff=state(r["id"])) for r in rows], "c": counts(), "words": STATUS_WORDS, "active": "bridges", "required": required(), "last_error": get_setting("bridge_verify_last_error", "")}


@bp.route("/admin/bridges")
@permission_required("moderate_chat")
def admin_page():
    return render_template("admin_bridges.html", **_admin_ctx())


@bp.route("/admin/bridges/<int:uid>/<act>", methods=["POST"])
@permission_required("moderate_chat")
def admin_act(uid, act):
    """check (look up again now), approve (a needs-review member), revoke (any member) or clear (take a revoke or approval away); approve, revoke and clear need a reason."""
    d = get_db()
    r = _row(uid)
    if r is None or act not in ("check", "approve", "revoke", "clear"):
        abort(404)
    reason = " ".join((request.form.get("reason") or "").split())[:200]
    if act == "check":
        res = check(uid, by=current_user.username)
        return jsonify({"ok": not res.get("down"), "message": ("The service did not answer. " if res.get("down") else "") + (res.get("reason") or "")})
    if not reason:
        return jsonify({"ok": False, "message": "Give a reason (it is kept in the audit log)."}), 400
    call = base_call(r["callsign"])
    if act == "approve":
        if not call or r["ham_call"] != call or r["ham_status"] != "review":
            return jsonify({"ok": False, "message": "Only a member whose check ended in needs-review, for their current call sign, can be approved. Ask them to check first."}), 400
        d.execute("UPDATE users SET ham_status='verified', ham_override='approved', ham_by=%s, ham_reason=%s, ham_name=%s WHERE id=%s", (current_user.username, f"Approved by {current_user.username}: {reason}", r["display_name"] or "", uid))
    elif act == "revoke":
        d.execute("UPDATE users SET ham_override='revoked', ham_by=%s, ham_reason=%s WHERE id=%s", (current_user.username, f"Revoked by {current_user.username}: {reason}", uid))
        d.execute("UPDATE bridge_links SET enabled=FALSE WHERE created_by=%s AND kind='aprs'", (uid,))
    else:
        d.execute("UPDATE users SET ham_override='', ham_status='unverified', ham_by='', ham_reason=%s WHERE id=%s", (f"Cleared by {current_user.username}: {reason}", uid))
    d.commit()
    audit("bridge.verify_" + act, target=r["username"], detail=f"{call}: {reason}")
    return jsonify({"ok": True, "message": {"approve": "Approved.", "revoke": "Revoked: their APRS links are switched off.", "clear": "Cleared: they must check again."}[act]})
