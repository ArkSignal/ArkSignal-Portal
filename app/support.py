"""The Support page: help with the running costs and an equipment fund. Everything is set in Admin > Support. The portal only links out to the donation
services (Ko-fi, PayPal or another); it never handles card or payment details. The page stays hidden until it is switched on and at least one link is set."""
import re
from urllib.parse import urlparse

from flask import Blueprint, abort, render_template, request

from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .settings import cfg, set_setting

bp = Blueprint("support", __name__)
STATUS = (("needed", "Needed"), ("funded", "Funded"), ("bought", "Bought"), ("installed", "Installed"))
HOSTS = {"support_kofi": ("ko-fi.com",), "support_paypal": ("paypal.com", "paypal.me")}
KEYS = ("support_enabled", "support_intro", "support_kofi", "support_paypal", "support_other_label", "support_other", "support_costs", "support_equipment",
        "support_raised", "support_supporters", "support_thanks")


def on():
    return cfg("support_enabled") == "1" and bool(cfg("support_kofi") or cfg("support_paypal") or cfg("support_other"))


def _money(v):
    try:
        x = float(str(v).replace("$", "").replace(",", "").strip())
    except ValueError:
        raise ValueError(f"'{v}' is not an amount.")
    if not 0 <= x <= 1_000_000:
        raise ValueError("Amounts must be from 0 to 1,000,000.")
    return x


def parse_costs(text):
    out = []
    for line in (text or "").splitlines():
        if line.strip():
            name, _, amount = line.partition("|")
            if not name.strip():
                raise ValueError("Each cost line needs a name before the | sign.")
            out.append({"name": name.strip()[:80], "monthly": _money(amount or "0")})
    if len(out) > 40:
        raise ValueError("At most 40 cost lines.")
    return out


def parse_equipment(text):
    out = []
    keys = {k for k, _ in STATUS}
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        p = [x.strip() for x in line.split("|")]
        if len(p) < 2 or not p[0]:
            raise ValueError("Each equipment line is: Item | price | funded so far | status | note.")
        price = _money(p[1])
        funded = _money(p[2]) if len(p) > 2 and p[2] else 0.0
        status = (p[3].lower() if len(p) > 3 and p[3] else ("funded" if funded >= price > 0 else "needed"))
        if status not in keys:
            raise ValueError(f"Status must be one of: {', '.join(keys)}.")
        out.append({"item": p[0][:100], "price": price, "funded": min(funded, price) if price else funded, "status": status, "note": (p[4] if len(p) > 4 else "")[:200]})
    if len(out) > 60:
        raise ValueError("At most 60 equipment lines.")
    return out


def _link(key, value):
    value = value.strip()
    if not value:
        return ""
    u = urlparse(value)
    if u.scheme != "https" or not u.netloc:
        raise ValueError("Links must start with https://")
    hosts = HOSTS.get(key)
    if hosts and not any(u.netloc.lower() == h or u.netloc.lower().endswith("." + h) for h in hosts):
        raise ValueError(f"That does not look like a {hosts[0]} link.")
    return value[:300]


@bp.route("/support")
def page():
    if not on():
        abort(404)
    costs = parse_costs(cfg("support_costs")) if True else []
    total = sum(c["monthly"] for c in costs)
    try:
        raised = float(cfg("support_raised") or 0)
    except ValueError:
        raised = 0.0
    equipment = parse_equipment(cfg("support_equipment"))
    return render_template("support.html", intro=cfg("support_intro"), kofi=cfg("support_kofi"), paypal=cfg("support_paypal"), other=cfg("support_other"),
                           other_label=cfg("support_other_label") or "Donate", costs=costs, total=total, raised=raised,
                           pct=min(100, round(100 * raised / total)) if total else 0, equipment=equipment, status_labels=dict(STATUS),
                           supporters=[s.strip() for s in cfg("support_supporters").splitlines() if s.strip()][:200], thanks=cfg("support_thanks"))


@bp.route("/admin/support")
@permission_required("manage_settings")
def admin_page():
    return render_template("admin_support.html", active="support", v={k: cfg(k) for k in KEYS}, status=STATUS)


@bp.route("/admin/support/save", methods=["POST"])
@permission_required("manage_settings")
def admin_save():
    f = request.form
    try:
        new = {"support_enabled": "1" if f.get("enabled") else "0", "support_intro": f.get("intro", "").strip()[:2000], "support_thanks": f.get("thanks", "").strip()[:1000],
               "support_kofi": _link("support_kofi", f.get("kofi", "")), "support_paypal": _link("support_paypal", f.get("paypal", "")),
               "support_other": _link("support_other", f.get("other", "")), "support_other_label": f.get("other_label", "").strip()[:40]}
        parse_costs(f.get("costs", ""))
        parse_equipment(f.get("equipment", ""))
        new["support_costs"] = f.get("costs", "").strip()
        new["support_equipment"] = f.get("equipment", "").strip()
        new["support_raised"] = str(_money(f.get("raised", "0") or "0"))
        new["support_supporters"] = "\n".join(s.strip()[:60] for s in f.get("supporters", "").splitlines() if s.strip())[:4000]
        if new["support_enabled"] == "1" and not (new["support_kofi"] or new["support_paypal"] or new["support_other"]):
            raise ValueError("Add at least one donation link before switching the page on.")
    except ValueError as e:
        return action_response(False, str(e), redirect_endpoint="support.admin_page")
    for k, v in new.items():
        set_setting(k, v)
    audit("support.save", detail="on" if new["support_enabled"] == "1" else "off")
    return action_response(True, "Support page saved." + (" It is live at /support." if on() else ""), redirect_endpoint="support.admin_page")
