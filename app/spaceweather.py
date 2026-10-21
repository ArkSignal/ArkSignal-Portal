"""Space weather for amateur radio: the NOAA Space Weather Prediction Center's current storm scales, planetary K index, 10.7 cm solar flux and X-ray flux, and a rough HF band guide worked out from them.

Source: services.swpc.noaa.gov (NOAA/NWS, a US Government work in the public domain, see DATA_SOURCES_TERMS.md). Kept five minutes in memory only."""
import json
import urllib.request
from datetime import datetime, timezone

from .cache import cached
from .settings import cfg, cfg_int

BASE = "https://services.swpc.noaa.gov/"


def _get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def xray_class(flux):
    """The GOES long-band X-ray flux (W/m2) as the letter class solar flares are named by: 'M1.2'."""
    if not flux or flux <= 0:
        return ""
    for letter, base in (("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7), ("A", 1e-8)):
        if flux >= base:
            return f"{letter}{flux / base:.1f}"
    return "A0.0"


def _step(level, down):
    order = ["Poor", "Fair", "Good"]
    return order[max(0, order.index(level) - down)]


def band_guide(sfi, kp):
    """A rule-of-thumb daytime and nighttime guide for the HF bands from the solar flux and the Kp index. Not a forecast: [(bands, day, night)]."""
    if sfi is None:
        return []
    down = 2 if (kp or 0) >= 5 else 1 if (kp or 0) >= 4 else 0
    def hi(a, b):   # poor below a, fair below b, good from b
        return "Good" if sfi >= b else "Fair" if sfi >= a else "Poor"
    rows = [("80 m and 40 m", "Fair", "Good"),
            ("30 m and 20 m", hi(60, 80), "Fair" if sfi >= 90 else "Poor" if sfi < 70 else "Fair"),
            ("17 m and 15 m", hi(80, 110), "Fair" if sfi >= 150 else "Poor"),
            ("12 m and 10 m", hi(110, 150), "Poor")]
    return [(bands, _step(day, down), _step(night, down)) for bands, day, night in rows]


def _build():
    out = {"ok": False, "scales": {}, "kp": [], "sfi": None, "xray": None, "at": datetime.now(timezone.utc).isoformat()}
    try:
        sc = (_get("products/noaa-scales.json") or {}).get("0") or {}
        out["scales"] = {k: {"scale": (sc.get(k) or {}).get("Scale") or "0", "text": (sc.get(k) or {}).get("Text") or ""} for k in ("R", "S", "G")}
        out["ok"] = True
    except Exception:
        pass
    try:
        rows = _get("products/noaa-planetary-k-index.json") or []
        out["kp"] = [{"time": r["time_tag"], "kp": r["Kp"]} for r in rows[-24:] if isinstance(r, dict) and r.get("Kp") is not None]
        out["ok"] = True
    except Exception:
        pass
    try:
        fl = _get("products/summary/10cm-flux.json") or []
        out["sfi"] = float(fl[0]["flux"]) if fl else None
    except Exception:
        pass
    try:
        xr = [x for x in (_get("json/goes/primary/xrays-6-hour.json") or []) if x.get("energy") == "0.1-0.8nm" and x.get("flux")]
        if xr:
            out["xray"] = {"class": xray_class(xr[-1]["flux"]), "flux": xr[-1]["flux"], "time": xr[-1]["time_tag"]}
    except Exception:
        pass
    kp_now = out["kp"][-1]["kp"] if out["kp"] else None
    out["guide"] = band_guide(out["sfi"], kp_now)
    out["kp_now"] = kp_now
    return out


def current():
    """The current space weather ({ok, scales, kp, kp_now, sfi, xray, guide, at}); {'ok': False} when the layer is off or NOAA cannot be reached."""
    if cfg("spaceweather_enabled") != "1":
        return {"ok": False, "off": True}
    return cached("spaceweather", _build, max(60, cfg_int("spaceweather_cache_seconds")))
