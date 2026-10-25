"""Keeping up with the Meshtastic project: once a day the site asks GitHub for the newest stable firmware and the newest protobuf definitions (the packet formats the mesh service decodes by hand).

The firmware version is shown on Mesh Health and Admin > Mesh Nodes, so people can see how far behind a node is. A new protobuf release is never applied blindly: the administrators are told (an audit entry and an
item on the Admin Overview) that the formats may have changed, they review the decoders against the new definitions and run `tests/mesh_test.py`, and press Mark Reviewed. Firmware updates themselves stay with the
official flasher. Settings > Mesh Nodes: Check for Meshtastic Releases (on or off), how often, and the address asked."""
import json
import threading
import time
import urllib.request
from datetime import datetime, timezone

from flask import Blueprint

from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int, get_setting, set_setting

bp = Blueprint("meshrelease", __name__)


def _fetch(repo):
    """(tag, published) of the newest stable release of a Meshtastic repository, or (None, '') when it could not be read."""
    req = urllib.request.Request(cfg("mesh_release_api").rstrip("/") + "/" + repo + "/releases/latest", headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read(2_000_000).decode("utf-8"))
        tag = str(j.get("tag_name") or "").strip()[:40]
        return (tag or None), str(j.get("published_at") or "")[:10]
    except Exception:
        return None, ""


def check(who="schedule"):
    """Ask GitHub now and keep the answer. Returns a plain sentence. The first answer becomes the reviewed baseline, so nobody is asked to review what was never different."""
    fw, fw_date = _fetch("firmware")
    pb, pb_date = _fetch("protobufs")
    if fw is None and pb is None:
        return False, "GitHub did not answer (or the address in Settings is wrong). The last answer is kept."
    old_pb = get_setting("mesh_pb_latest")
    if fw:
        set_setting("mesh_fw_latest", fw)
        set_setting("mesh_fw_date", fw_date)
    if pb:
        set_setting("mesh_pb_latest", pb)
        if not get_setting("mesh_pb_reviewed"):
            set_setting("mesh_pb_reviewed", pb)
        elif old_pb and pb != old_pb:
            audit("mesh.release", target=pb, detail=f"new Meshtastic protobuf release {pb} (was {old_pb}): review the decoders", actor_name="system" if who == "schedule" else None)
    set_setting("mesh_release_checked", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    get_db().commit()
    return True, f"Newest firmware {fw or 'unknown'}, newest protobufs {pb or 'unknown'}."


def status():
    pb, rev = get_setting("mesh_pb_latest"), get_setting("mesh_pb_reviewed")
    return {"fw": get_setting("mesh_fw_latest"), "fw_date": get_setting("mesh_fw_date"), "pb": pb, "reviewed": rev, "checked": get_setting("mesh_release_checked"), "pending_review": bool(pb and rev and pb != rev), "on": cfg("mesh_release_check") == "1"}


@bp.route("/admin/mesh/releases/check", methods=["POST"])
@permission_required("manage_map_data")
def check_now():
    ok, msg = check("Check Now")
    audit("mesh.release", detail="checked: " + msg[:150])
    return action_response(ok, msg, redirect_endpoint="mesh.admin")


@bp.route("/admin/mesh/releases/review", methods=["POST"])
@permission_required("manage_map_data")
def reviewed():
    s = status()
    if not s["pb"]:
        return action_response(False, "No protobuf release is known yet. Check now first.", redirect_endpoint="mesh.admin")
    set_setting("mesh_pb_reviewed", s["pb"])
    get_db().commit()
    audit("mesh.release", target=s["pb"], detail="decoders reviewed against " + s["pb"])
    return action_response(True, f"Marked as reviewed against {s['pb']}.", redirect_endpoint="mesh.admin")


def start(app):
    def loop():
        time.sleep(180)
        while True:
            try:
                with app.app_context():
                    due = cfg("mesh_release_check") == "1" and cfg("mesh_enabled") == "1"
                    if due:
                        last = get_setting("mesh_release_checked")
                        try:
                            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 3600 if last else 9e9
                        except ValueError:
                            age_h = 9e9
                        due = age_h >= max(1, cfg_int("mesh_release_check_hours"))
                    if due:
                        check("schedule")
            except Exception:
                pass
            time.sleep(1800)
    threading.Thread(target=loop, name="mesh-release", daemon=True).start()
