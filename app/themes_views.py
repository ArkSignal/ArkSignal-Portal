"""Admin > Themes: which seasonal and holiday themes are on and on what dates, the theme shown to everyone right now (if an administrator wants one for an event), and a try-it for each look."""
import json
import re
from datetime import date, timedelta

from flask import Blueprint, render_template, request
from flask_login import current_user

from . import themes as th
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .settings import cfg, set_setting

bp = Blueprint("themes", __name__)
MD = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")


def _this_year(tid, today):
    """The days a seasonal theme runs this year (and into the next year when it crosses New Year), as text."""
    rule = th.schedule_of(tid)
    if rule.get("type") == "holiday":
        h = th.holiday_date(rule["holiday"], today.year)
        a, b = h - timedelta(days=int(rule.get("before", 0))), h + timedelta(days=int(rule.get("after", 0)))
        return f"{a.strftime('%b %-d')} to {b.strftime('%b %-d')} (around {h.strftime('%b %-d')})"
    a, b = th._md(rule["from"]), th._md(rule["to"])
    return f"{date(2001, *a).strftime('%b %-d')} to {date(2001, *b).strftime('%b %-d')}"


@bp.route("/admin/themes")
@permission_required("manage_settings")
def admin_themes():
    today = date.today()
    rows = []
    for tid in th.PRIORITY:
        t = th.THEMES[tid]
        rule = th.schedule_of(tid)
        rows.append({"id": tid, "name": t["name"], "enabled": th.enabled(tid), "type": rule.get("type"), "from": rule.get("from", ""), "to": rule.get("to", ""), "when": _this_year(tid, today),
                     "deco": " ".join(t.get("deco", [])), "balloons": " ".join(t.get("balloons", [])), "planes": " ".join(t.get("planes", [])), "vars": t["vars"], "mode": t["mode"],
                     "holiday": rule.get("holiday", ""), "today": th.matches(rule, today) and th.enabled(tid)})
    schemes = [{"id": k, "name": v["name"], "vars": v["vars"], "mode": v["mode"]} for k, v in th.THEMES.items() if v["kind"] in ("base", "scheme")]
    return render_template("admin_themes.html", active="themes", rows=rows, schemes=schemes, today_theme=th.active_seasonal(today), site_theme=th.current_site_theme(today), forced=cfg("theme_force"),
                           names={k: v["name"] for k, v in th.THEMES.items()}, base=th.DARK, light=th.LIGHT)


@bp.route("/admin/themes/save", methods=["POST"])
@permission_required("manage_settings")
def save():
    f = request.form
    conf = {}
    for tid in th.PRIORITY:
        entry = {"enabled": bool(f.get("en_" + tid))}
        a, b = (f.get("from_" + tid) or "").strip(), (f.get("to_" + tid) or "").strip()
        if th.THEMES[tid]["schedule"]["type"] == "range":
            if not (MD.match(a) and MD.match(b)):
                return action_response(False, f"{th.THEMES[tid]['name']}: write the dates as month-day, for example 10-15 and 11-01.", redirect_endpoint="themes.admin_themes")
            entry["from"], entry["to"] = a, b
        conf[tid] = entry
    force = f.get("force", "")
    if force and force not in th.THEMES:
        return action_response(False, "Choose a theme from the list.", redirect_endpoint="themes.admin_themes")
    set_setting("themes_config", json.dumps(conf))
    set_setting("theme_force", force)
    from .cache import _store
    _store.clear()
    audit("themes.save", detail=("forced " + force) if force else "schedule")
    return action_response(True, "Themes saved." + (f" Everyone sees {th.THEMES[force]['name']} until you clear that." if force else ""), redirect_endpoint="themes.admin_themes")
