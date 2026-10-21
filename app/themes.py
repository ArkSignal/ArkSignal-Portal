"""Site themes: color schemes anyone can pick, and seasonal and holiday themes that switch on by date.

A theme is a set of the site's color variables (the same names base.html defines), a light or dark base, and for the seasonal ones decorations (emoji that drift down the page, a banner) and the symbols the maps
use (Santa for some of the aircraft at Christmas, pumpkins and skeletons for the balloons at Halloween...). The registry is plain data so it is sent to the browser once and applied there without a flash.

Schedules repeat every year: a range of month-day dates ("10-15" to "11-01", across New Year when the end is earlier than the start), or a day relative to a holiday that moves (Thanksgiving, Easter).
Administrators switch themes on and off and change their dates in Admin > Themes (kept as JSON in the setting themes_config); when several themes match a day the first in PRIORITY wins, so a holiday beats its season.
Which theme a person sees: their own choice in the Display menu, or the site's choice (the default theme, with the seasonal one on its days when automatic seasons are on)."""
import json
from datetime import date, timedelta

from .settings import cfg

# the colors of the two base looks (base.html's own); every other theme changes some of these
DARK = {"--bg": "#070d17", "--panel": "rgba(18,28,44,.72)", "--line": "rgba(120,160,210,.14)", "--text": "#e8eef7", "--dim": "#8a9bb3", "--acc": "#3ee6a8", "--acc2": "#4fb3ff", "--err": "#ff6b6b",
        "--input-bg": "rgba(7,13,23,.7)", "--input-bg-soft": "rgba(7,13,23,.45)", "--pop-bg": "#17253d", "--card-bg": "rgba(10,18,32,.88)", "--map-bg": "#0a1220",
        "--map-filter": "brightness(.62) contrast(1.05) saturate(.8)", "--g1": "rgba(79,179,255,.14)", "--g2": "rgba(62,230,168,.10)"}
LIGHT = {"--bg": "#eef3f9", "--panel": "rgba(255,255,255,.8)", "--line": "rgba(40,70,110,.18)", "--text": "#0f1c2e", "--dim": "#566880", "--acc": "#0b9a6e", "--acc2": "#1769c2", "--err": "#d23a3a",
         "--input-bg": "rgba(255,255,255,.95)", "--input-bg-soft": "rgba(240,245,252,.8)", "--pop-bg": "#ffffff", "--card-bg": "rgba(255,255,255,.92)", "--map-bg": "#dfe7f1",
         "--map-filter": "none", "--g1": "rgba(23,105,194,.10)", "--g2": "rgba(11,154,110,.08)"}


def _dark(**kw):
    return dict(DARK, **kw)


def _light(**kw):
    return dict(LIGHT, **kw)


# id: name, kind (base, scheme, seasonal), mode (dark or light base), vars, decorations, map symbols, schedule
THEMES = {
    "dark": {"name": "Dark", "kind": "base", "mode": "dark", "vars": {}},
    "light": {"name": "Light", "kind": "base", "mode": "light", "vars": {}},
    # ---- color schemes: always available to pick
    "midnight": {"name": "Midnight", "kind": "scheme", "mode": "dark", "vars": _dark(**{"--bg": "#0b0a1f", "--panel": "rgba(28,24,60,.72)", "--line": "rgba(160,140,255,.16)", "--acc": "#b79cff", "--acc2": "#6fa8ff", "--pop-bg": "#1c1840", "--card-bg": "rgba(16,13,40,.9)", "--map-bg": "#0d0b26", "--g1": "rgba(130,100,255,.16)", "--g2": "rgba(80,160,255,.10)"})},
    "ocean": {"name": "Ocean", "kind": "scheme", "mode": "dark", "vars": _dark(**{"--bg": "#04141c", "--panel": "rgba(10,40,52,.72)", "--line": "rgba(90,200,220,.18)", "--acc": "#2fd6d0", "--acc2": "#4fa8ff", "--pop-bg": "#0f3140", "--card-bg": "rgba(5,26,36,.9)", "--map-bg": "#06202c", "--g1": "rgba(47,214,208,.14)", "--g2": "rgba(79,168,255,.10)"})},
    "forest": {"name": "Forest", "kind": "scheme", "mode": "dark", "vars": _dark(**{"--bg": "#08130c", "--panel": "rgba(18,42,26,.72)", "--line": "rgba(120,210,140,.16)", "--acc": "#7ee08a", "--acc2": "#d3e86a", "--pop-bg": "#173323", "--card-bg": "rgba(8,26,15,.9)", "--map-bg": "#0b1d12", "--g1": "rgba(126,224,138,.13)", "--g2": "rgba(211,232,106,.09)"})},
    "sunset": {"name": "Sunset", "kind": "scheme", "mode": "dark", "vars": _dark(**{"--bg": "#160c0c", "--panel": "rgba(52,26,24,.72)", "--line": "rgba(255,170,120,.17)", "--acc": "#ffa24d", "--acc2": "#ff6f91", "--pop-bg": "#3a1d1a", "--card-bg": "rgba(32,14,12,.9)", "--map-bg": "#1f0f0e", "--g1": "rgba(255,111,145,.14)", "--g2": "rgba(255,162,77,.10)"})},
    "slate": {"name": "Slate", "kind": "scheme", "mode": "dark", "vars": _dark(**{"--bg": "#14161a", "--panel": "rgba(38,42,50,.72)", "--line": "rgba(170,180,200,.16)", "--acc": "#9fd0c0", "--acc2": "#9db4d4", "--pop-bg": "#262b33", "--card-bg": "rgba(24,27,32,.9)", "--map-bg": "#191c21", "--g1": "rgba(157,180,212,.10)", "--g2": "rgba(159,208,192,.07)"})},
    "contrast": {"name": "High Contrast", "kind": "scheme", "mode": "dark", "vars": _dark(**{"--bg": "#000000", "--panel": "rgba(0,0,0,.9)", "--line": "rgba(255,255,255,.55)", "--text": "#ffffff", "--dim": "#cfd6df", "--acc": "#ffe14d", "--acc2": "#5fd0ff", "--err": "#ff7a7a", "--pop-bg": "#000000", "--card-bg": "rgba(0,0,0,.96)", "--map-bg": "#000000", "--map-filter": "brightness(.75) contrast(1.2)", "--g1": "transparent", "--g2": "transparent"})},
    "paper": {"name": "Paper", "kind": "scheme", "mode": "light", "vars": _light(**{"--bg": "#f6efe2", "--panel": "rgba(255,252,244,.85)", "--line": "rgba(110,80,40,.2)", "--text": "#2b2113", "--dim": "#6e5d45", "--acc": "#8a5a1f", "--acc2": "#3d6f8a", "--pop-bg": "#fffaf0", "--card-bg": "rgba(255,251,242,.94)", "--map-bg": "#eadfc9", "--g1": "rgba(138,90,31,.10)", "--g2": "rgba(61,111,138,.08)"})},
    "daylight": {"name": "Daylight", "kind": "scheme", "mode": "light", "vars": _light(**{"--bg": "#f3f8ff", "--acc": "#006fd6", "--acc2": "#00867a", "--map-bg": "#e3eefc"})},
    # ---- the seasons and the holidays: on their days when the site follows the seasons, and always there to pick
    "spring": {"name": "Spring", "kind": "seasonal", "mode": "dark", "vars": _dark(**{"--bg": "#0a1510", "--panel": "rgba(22,48,34,.7)", "--acc": "#8be8a0", "--acc2": "#ff9ecb", "--pop-bg": "#193a28", "--map-bg": "#0d1f16", "--g1": "rgba(255,158,203,.12)", "--g2": "rgba(139,232,160,.10)"}),
               "deco": ["&#127800;", "&#127807;", "&#129419;", "&#127804;"], "banner": "Spring", "schedule": {"type": "range", "from": "03-20", "to": "06-20"}},
    "summer": {"name": "Summer", "kind": "seasonal", "mode": "dark", "vars": _dark(**{"--bg": "#0a1520", "--acc": "#ffd447", "--acc2": "#45c6ff", "--map-bg": "#0b1b2a", "--g1": "rgba(255,212,71,.12)", "--g2": "rgba(69,198,255,.12)"}),
               "deco": ["&#9728;&#65039;", "&#127796;", "&#127754;", "&#127803;"], "banner": "Summer", "schedule": {"type": "range", "from": "06-21", "to": "09-21"}},
    "fall": {"name": "Fall", "kind": "seasonal", "mode": "dark", "vars": _dark(**{"--bg": "#150e08", "--panel": "rgba(48,30,16,.72)", "--line": "rgba(230,160,90,.18)", "--acc": "#ff9a3c", "--acc2": "#e6c35c", "--pop-bg": "#33200f", "--card-bg": "rgba(28,16,8,.9)", "--map-bg": "#1c1209", "--g1": "rgba(255,154,60,.14)", "--g2": "rgba(230,195,92,.10)"}),
             "deco": ["&#127810;", "&#127809;", "&#127812;", "&#127815;"], "banner": "Fall", "schedule": {"type": "range", "from": "09-22", "to": "11-30"}},
    "winter": {"name": "Winter", "kind": "seasonal", "mode": "dark", "vars": _dark(**{"--bg": "#0a1220", "--panel": "rgba(26,40,66,.72)", "--line": "rgba(190,220,255,.2)", "--acc": "#bfe3ff", "--acc2": "#8fb8ff", "--pop-bg": "#1a2a48", "--map-bg": "#0d1626", "--g1": "rgba(191,227,255,.14)", "--g2": "rgba(143,184,255,.10)"}),
               "deco": ["&#10052;&#65039;", "&#10053;&#65039;", "&#9731;&#65039;", "&#10052;&#65039;"], "banner": "Winter", "schedule": {"type": "range", "from": "12-21", "to": "03-19"}},
    "halloween": {"name": "Halloween", "kind": "seasonal", "mode": "dark", "vars": _dark(**{"--bg": "#0d0712", "--panel": "rgba(40,20,48,.74)", "--line": "rgba(255,140,40,.22)", "--text": "#f4e9ff", "--dim": "#b29ac4", "--acc": "#ff8a1f", "--acc2": "#a06bff", "--err": "#ff4d4d", "--pop-bg": "#2a1534", "--card-bg": "rgba(20,8,26,.92)", "--map-bg": "#12081a", "--g1": "rgba(255,138,31,.16)", "--g2": "rgba(160,107,255,.14)"}),
                  "deco": ["&#127875;", "&#128128;", "&#129415;", "&#128123;", "&#128375;&#65039;"], "banner": "Happy Halloween", "schedule": {"type": "range", "from": "10-15", "to": "11-01"},
                  "balloons": ["&#127875;", "&#128128;", "&#128123;", "&#129439;"], "planes": ["&#129415;", "&#128123;"], "plane_share": 6},
    "thanksgiving": {"name": "Thanksgiving", "kind": "seasonal", "mode": "dark", "vars": _dark(**{"--bg": "#150d07", "--panel": "rgba(52,32,18,.74)", "--line": "rgba(222,160,80,.22)", "--acc": "#e8a23a", "--acc2": "#c9663a", "--pop-bg": "#38220f", "--card-bg": "rgba(30,17,8,.92)", "--map-bg": "#1d1209", "--g1": "rgba(232,162,58,.15)", "--g2": "rgba(201,102,58,.12)"}),
                     "deco": ["&#129411;", "&#127810;", "&#129367;", "&#127806;"], "banner": "Happy Thanksgiving", "schedule": {"type": "holiday", "holiday": "thanksgiving", "before": 7, "after": 1},
                     "balloons": ["&#129411;", "&#127811;", "&#129367;"], "planes": ["&#129411;"], "plane_share": 10},
    "christmas": {"name": "Christmas", "kind": "seasonal", "mode": "dark", "vars": _dark(**{"--bg": "#0a1410", "--panel": "rgba(22,44,34,.74)", "--line": "rgba(230,70,70,.24)", "--text": "#f4f1e8", "--acc": "#ff5252", "--acc2": "#4cd37a", "--err": "#ffb347", "--pop-bg": "#1a3a2a", "--card-bg": "rgba(8,24,16,.92)", "--map-bg": "#0c1c14", "--g1": "rgba(255,82,82,.16)", "--g2": "rgba(76,211,122,.14)"}),
                  "deco": ["&#10052;&#65039;", "&#127876;", "&#127873;", "&#10052;&#65039;", "&#11088;"], "banner": "Merry Christmas", "schedule": {"type": "range", "from": "12-01", "to": "12-26"},
                  "balloons": ["&#127876;", "&#127877;", "&#127873;", "&#9924;"], "planes": ["&#127877;", "&#129420;"], "plane_share": 5},
    "newyear": {"name": "New Year", "kind": "seasonal", "mode": "dark", "vars": _dark(**{"--bg": "#05060a", "--panel": "rgba(20,20,30,.78)", "--line": "rgba(240,200,90,.24)", "--acc": "#f4cf5c", "--acc2": "#cfd8e8", "--pop-bg": "#161620", "--card-bg": "rgba(8,8,14,.94)", "--map-bg": "#08090e", "--g1": "rgba(244,207,92,.16)", "--g2": "rgba(207,216,232,.10)"}),
                "deco": ["&#127878;", "&#129346;", "&#127881;", "&#10024;"], "banner": "Happy New Year", "schedule": {"type": "range", "from": "12-27", "to": "01-03"},
                "balloons": ["&#127878;", "&#129346;", "&#127881;"], "planes": [], "plane_share": 0},
    "july4": {"name": "Independence Day", "kind": "seasonal", "mode": "dark", "vars": _dark(**{"--bg": "#060b1c", "--panel": "rgba(16,26,60,.74)", "--line": "rgba(255,255,255,.2)", "--acc": "#ff4d5e", "--acc2": "#5aa2ff", "--pop-bg": "#142050", "--card-bg": "rgba(6,12,38,.92)", "--map-bg": "#0a1230", "--g1": "rgba(255,77,94,.16)", "--g2": "rgba(90,162,255,.16)"}),
              "deco": ["&#127878;", "&#127879;", "&#11088;", "&#127482;&#127480;"], "banner": "Happy Fourth of July", "schedule": {"type": "range", "from": "06-28", "to": "07-05"},
              "balloons": ["&#127878;", "&#127879;", "&#127482;&#127480;"], "planes": [], "plane_share": 0},
    "easter": {"name": "Easter", "kind": "seasonal", "mode": "light", "vars": _light(**{"--bg": "#f4f0fb", "--acc": "#7a4fd0", "--acc2": "#e0568f", "--map-bg": "#e6e0f3", "--g1": "rgba(122,79,208,.10)", "--g2": "rgba(224,86,143,.08)"}),
               "deco": ["&#129370;", "&#128035;", "&#127799;", "&#128048;"], "banner": "Happy Easter", "schedule": {"type": "holiday", "holiday": "easter", "before": 7, "after": 1},
               "balloons": ["&#129370;", "&#128035;"], "planes": [], "plane_share": 0},
    "stpatrick": {"name": "St. Patrick's Day", "kind": "seasonal", "mode": "dark", "vars": _dark(**{"--bg": "#06140a", "--acc": "#46dc78", "--acc2": "#f0d44c", "--map-bg": "#09200f", "--g1": "rgba(70,220,120,.16)", "--g2": "rgba(240,212,76,.10)"}),
                  "deco": ["&#9752;&#65039;", "&#127808;", "&#127752;"], "banner": "Happy St. Patrick's Day", "schedule": {"type": "range", "from": "03-10", "to": "03-17"},
                  "balloons": ["&#9752;&#65039;", "&#127808;"], "planes": [], "plane_share": 0},
    "valentines": {"name": "Valentine's Day", "kind": "seasonal", "mode": "dark", "vars": _dark(**{"--bg": "#150812", "--panel": "rgba(54,20,44,.74)", "--line": "rgba(255,130,180,.22)", "--acc": "#ff6fa5", "--acc2": "#ffb3cf", "--pop-bg": "#3a1530", "--map-bg": "#1d0b19", "--g1": "rgba(255,111,165,.16)", "--g2": "rgba(255,179,207,.10)"}),
                   "deco": ["&#128150;", "&#127801;", "&#128140;", "&#10084;&#65039;"], "banner": "Happy Valentine's Day", "schedule": {"type": "range", "from": "02-07", "to": "02-14"},
                   "balloons": ["&#128149;", "&#128152;"], "planes": [], "plane_share": 0},
}
STRIPE = {"spring": ["#8be8a0", "#ff9ecb", "#ffe58f"], "summer": ["#ffd447", "#45c6ff", "#ff9a3c"], "fall": ["#ff9a3c", "#e6c35c", "#a8501f"], "winter": ["#bfe3ff", "#ffffff", "#8fb8ff"],
          "halloween": ["#ff8a1f", "#a06bff", "#ff8a1f", "#1b0f24"], "thanksgiving": ["#e8a23a", "#c9663a", "#8a4b1f"], "christmas": ["#ff5252", "#ffffff", "#4cd37a", "#ffd447"],
          "newyear": ["#f4cf5c", "#cfd8e8", "#f4cf5c"], "july4": ["#ff4d5e", "#ffffff", "#5aa2ff"], "easter": ["#ffb3d9", "#b6e7a8", "#ffe58f", "#b9a8f0"], "stpatrick": ["#46dc78", "#f0d44c", "#46dc78"],
          "valentines": ["#ff6fa5", "#ffffff", "#ff6fa5"]}
ART = {"halloween": "cobweb", "christmas": "lights", "newyear": "sparkle", "july4": "stars", "thanksgiving": "leaves", "winter": "snowcap", "valentines": "hearts"}
for _k, _v in STRIPE.items():
    THEMES[_k]["stripe"] = _v
    if _k in ART:
        THEMES[_k]["art"] = ART[_k]

PRIORITY = ["halloween", "thanksgiving", "christmas", "newyear", "july4", "easter", "stpatrick", "valentines", "fall", "winter", "spring", "summer"]
SEASONAL = [k for k in PRIORITY]


def _easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _thanksgiving(year):
    d = date(year, 11, 1)
    first = d + timedelta(days=(3 - d.weekday()) % 7)   # the first Thursday
    return first + timedelta(days=21)


def holiday_date(name, year):
    return {"thanksgiving": _thanksgiving, "easter": _easter}[name](year)


def _md(text):
    m, d = text.split("-")
    return int(m), int(d)


def matches(rule, today):
    """Is `today` inside a schedule rule? A range runs across New Year when its end is earlier than its start."""
    if not rule:
        return False
    if rule["type"] == "holiday":
        day = holiday_date(rule["holiday"], today.year)
        return day - timedelta(days=int(rule.get("before", 0))) <= today <= day + timedelta(days=int(rule.get("after", 0)))
    a, b = _md(rule["from"]), _md(rule["to"])
    t = (today.month, today.day)
    return (a <= t <= b) if a <= b else (t >= a or t <= b)


def overrides():
    try:
        d = json.loads(cfg("themes_config") or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def schedule_of(theme_id):
    """The rule a theme runs on now: the default one with the dates an administrator changed."""
    t = THEMES[theme_id]
    rule = dict(t.get("schedule") or {})
    o = overrides().get(theme_id) or {}
    if rule.get("type") == "range" and o.get("from") and o.get("to"):
        try:
            _md(o["from"]), _md(o["to"])
            rule["from"], rule["to"] = o["from"], o["to"]
        except (ValueError, KeyError):
            pass
    return rule


def enabled(theme_id):
    return (overrides().get(theme_id) or {}).get("enabled", True) is not False


def active_seasonal(today=None):
    """The seasonal theme for today, or '' (none matches, or the site does not follow the seasons)."""
    today = today or date.today()
    for tid in PRIORITY:
        if enabled(tid) and matches(schedule_of(tid), today):
            return tid
    return ""


def current_site_theme(today=None):
    """The theme the site shows people who have not chosen one: a theme forced for everyone, else the seasonal one on its days (when seasons are on), else the site's default."""
    forced = cfg("theme_force")
    if forced in THEMES:
        return forced
    if cfg("themes_seasonal") == "1":
        s = active_seasonal(today)
        if s:
            return s
    d = cfg("theme_site_default")
    return d if d in THEMES else "dark"


def valid_choice(value):
    """A theme value a person may keep in their preferences."""
    return value in THEMES or value in ("auto", "site", "normal")


def public_config(today=None):
    """What the browser needs: every theme's variables, mode and decorations, and which one the site shows today."""
    themes = {}
    for tid, t in THEMES.items():
        themes[tid] = {"name": t["name"], "kind": t["kind"], "mode": t["mode"], "vars": {k: v for k, v in t["vars"].items() if DARK.get(k) != v or t["mode"] == "light" and LIGHT.get(k) != v}}
        for k in ("deco", "banner", "balloons", "planes", "plane_share", "stripe", "art"):
            if k in t:
                themes[tid][k] = t[k]
    base = cfg("theme_site_default")
    return {"themes": themes, "site": current_site_theme(today), "normal": base if base in THEMES else "dark", "seasonal_on": active_seasonal(today) if cfg("themes_seasonal") == "1" else "", "enabled": cfg("themes_enabled") == "1", "decor": cfg("themes_decor") == "1",
            "order": [t for t in THEMES if THEMES[t]["kind"] in ("base", "scheme")] + [t for t in PRIORITY if enabled(t) and (cfg("themes_offseason") == "1" or matches(schedule_of(t), today or date.today()) or t == cfg("theme_force"))]}
