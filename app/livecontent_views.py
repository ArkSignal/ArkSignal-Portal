"""Admin > Updates > Live Content: where the Release Notes and Roadmap pages read their data from (see livecontent.py)."""
import re
import time

from flask import Blueprint, request

from . import livecontent
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .settings import get_setting, set_secret, set_setting

bp = Blueprint("livecontent_admin", __name__)
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,38}/[A-Za-z0-9._-]{1,100}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,80}$")


def context():
    c = livecontent.conf()
    st = {k: dict(v, iso=livecontent.iso(v["at"])) for k, v in livecontent.status().items()}
    return {"lc": dict(c, status=st)}


@bp.route("/admin/updates/content", methods=["POST"])
@permission_required("manage_settings")
def save():
    f = request.form
    repo, branch = f.get("repo", "").strip(), f.get("branch", "").strip() or "main"
    base = f.get("base", "").strip().rstrip("/")
    if not REPO_RE.match(repo):
        return action_response(False, "Repository must look like owner/name, for example mallison01/adsb-portal.", redirect_endpoint="admin.updates")
    if not BRANCH_RE.match(branch):
        return action_response(False, "Branch may only contain letters, digits and . _ - /", redirect_endpoint="admin.updates")
    if base and not re.match(r"^https?://[A-Za-z0-9.:/_-]{3,200}$", base):
        return action_response(False, "The custom address must start with http:// or https://.", redirect_endpoint="admin.updates")
    try:
        minutes = int(f.get("minutes", "10"))
        if not 1 <= minutes <= 1440:
            raise ValueError
    except ValueError:
        return action_response(False, "Refresh interval must be from 1 to 1440 minutes.", redirect_endpoint="admin.updates")
    set_setting("content_enabled", "1" if f.get("enabled") else "0")
    set_setting("content_repo", repo)
    set_setting("content_branch", branch)
    set_setting("content_minutes", str(minutes))
    set_setting("content_base_url", base)
    token = f.get("token", "").strip()
    if token:
        set_secret("content_token", token)
    elif f.get("clear_token"):
        set_secret("content_token", "")
    livecontent.forget()
    audit("content.save", detail=f"{repo}@{branch}" + (f" via {base}" if base else ""))
    return action_response(True, "Live content settings saved.", redirect_endpoint="admin.updates")


@bp.route("/admin/updates/content/test", methods=["POST"])
@permission_required("manage_settings")
def test():
    from . import releasenotes, roadmap
    livecontent.forget()
    out = []
    for label, fn in (("Release Notes", releasenotes.load), ("Roadmap", roadmap.load)):
        _data, meta = fn()
        out.append(f"{label}: " + ("read from GitHub" if meta["source"] == "github" else "using the built-in copy" + (f" ({meta['error']})" if meta["error"] else "")))
    ok = all("read from GitHub" in x for x in out)
    return action_response(ok, "; ".join(out) + ".", redirect_endpoint="admin.updates")
