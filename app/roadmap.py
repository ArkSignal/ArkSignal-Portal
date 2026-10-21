"""The public Roadmap page. Data: `roadmap.json` (public-safe, plain language), read live from GitHub through livecontent with the built-in copy as the
fallback. Items have a title, a summary, a status (done, next, progress, planned, idea), an area and an audience: "public" (the default) or "admin" (only on Admin > Full Roadmap, never on the
public page or in /api/roadmap.json: hub access, internal tooling and the like)."""
import json
import os

from flask import Blueprint, jsonify, render_template

from . import livecontent
from .admin import permission_required

bp = Blueprint("roadmap", __name__)
STATUSES = (("next", "Coming in the next release", "Built and tested, waiting for the next release."),
            ("progress", "In progress", "Being built now."),
            ("planned", "Planned", "Designed and queued, in roughly this order."),
            ("idea", "Ideas we like", "Under consideration; no promise yet."),
            ("done", "Recently released", "Already live. The Release Notes page has the details."))
AREAS = ("Hub", "Map", "Stations", "Data", "Accounts", "Community")


def _builtin():
    try:
        with open(os.path.join(os.path.dirname(__file__), "roadmap.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"updated": "", "items": []}


def _validate(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        return None
    items = []
    for it in doc["items"][:400]:
        if not isinstance(it, dict) or not isinstance(it.get("title"), str):
            return None
        status = it.get("status") if it.get("status") in {s[0] for s in STATUSES} else "planned"
        items.append({"title": it["title"][:120], "summary": str(it.get("summary", ""))[:600], "status": status, "area": str(it.get("area", "Hub"))[:30], "audience": "admin" if it.get("audience") == "admin" else "public"})
    return {"updated": str(doc.get("updated", ""))[:30], "items": items}


def load():
    return livecontent.get("roadmap.json", lambda: _validate(_builtin()) or {"updated": "", "items": []}, _validate)


def coming_services(limit=4):
    """Titles of the new kinds of station or data still planned, from the public roadmap itself, so the note on the home page can never go stale."""
    try:
        doc, _meta = load()
    except Exception:
        return []
    return [i["title"] for i in doc["items"] if i["audience"] != "admin" and i["status"] in ("planned", "next", "progress") and i["area"] in ("Stations", "Data")][:limit]


@bp.route("/roadmap")
def page():
    return _render(False)


def _render(admin):
    doc, meta = load()
    items = [i for i in doc["items"] if admin or i["audience"] != "admin"]
    groups = [(key, label, blurb, [i for i in items if i["status"] == key]) for key, label, blurb in STATUSES]
    areas = sorted({i["area"] for i in items})
    return render_template("roadmap.html", groups=[g for g in groups if g[3]], areas=areas, updated=doc.get("updated"), meta=meta, fetched=livecontent.iso(meta["at"]), admin_view=admin, active="roadmap" if admin else None)


@bp.route("/admin/roadmap")
@permission_required("manage_settings")
def admin_page():
    """The whole list, including the items that are not for the public page."""
    return _render(True)


@bp.route("/api/roadmap.json")
def api():
    doc, meta = load()
    return jsonify({"updated": doc.get("updated"), "source": meta["source"], "items": [{k: v for k, v in i.items() if k != "audience"} for i in doc["items"] if i["audience"] != "admin"]})
