"""How precisely a member's station location is shown to others. One list of choices for every kind of station (ADS-B feeders, weather stations, radiosonde receiving stations and whatever comes next),
for every form and every administrator setting: `PRIVACY` (key -> label), `choices()` (for settings and forms), `help_text()` (the sentence the help texts use). Nothing else lists the steps.

A position is shown rounded to a grid: a number of decimal places of a degree (one place is about 7 miles from side to side) or a grid of a fixed width in meters; the point shown is the nearest grid
point, so the true place is within about half of the width. Exact shows the place itself, Hidden shows nothing. General uses the site's own setting (Settings > General Location Precision, one
decimal place, "about 6 miles"). Labels are written in the viewer's own units (Display menu > Units): the same step reads "0.7 mile" or "1.1 kilometers". Only the rounded position ever leaves the server."""
import math

from .settings import cfg_int

# key: (name, how it rounds, (nominal width in imperial words, in metric words)). How it rounds: ("dec", places) | ("grid", meters) | "site" | None (exact and hidden). The order is the order everywhere.
LEVELS = {
    "exact": ("Exact", None, None),
    "fine": ("Fine", ("dec", 4), ("40 feet", "12 meters")),
    "near": ("Near", ("dec", 3), ("400 feet", "120 meters")),
    "block": ("Block", ("grid", 400), ("0.25 mile", "400 meters")),
    "close": ("Close", ("dec", 2), ("0.7 mile", "1.1 kilometers")),
    "area": ("Area", ("grid", 2414), ("1.5 miles", "2.4 kilometers")),
    "district": ("District", ("grid", 4828), ("3 miles", "4.8 kilometers")),
    "general": ("General", "site", None),
    "region": ("Region", ("grid", 24140), ("15 miles", "24 kilometers")),
    "broad": ("Broad", ("grid", 48280), ("30 miles", "48 kilometers")),
    "wide": ("Wide", ("dec", 0), ("60 miles", "97 kilometers")),
    "hidden": ("Hidden", None, None),
}
DEFAULT = "general"
M_PER_DEG = 111320.0


def _metric():
    from . import units
    return units.system() == "metric"


def _width_words(level):
    """The nominal width of the step in the viewer's units ('0.7 mile'); General follows the site's setting."""
    name, how, words = LEVELS[level]
    if how == "site":
        km = 9.656 * 10 ** (1 - cfg_int("general_precision_decimals"))   # one place is "about 6 miles"
        if _metric():
            return f"{km:.0f} kilometers" if km >= 9.5 else f"{km:.1f} kilometers"
        mi = km / 1.609344
        return f"{mi:.0f} miles" if mi >= 2 else f"{mi:.1f} miles"
    return words[1 if _metric() else 0]


def label(level):
    name, how, _w = LEVELS[level]
    return name if how is None else f"{name} (rounded to about {_width_words(level)})"


class _Labels(dict):
    """key -> label, with the labels written in the viewer's units at the moment they are read."""
    def __getitem__(self, k):
        label_ = label(k) if k in LEVELS else None
        if label_ is None:
            raise KeyError(k)
        return label_

    def get(self, k, default=None):
        return label(k) if k in LEVELS else default

    def items(self):
        return [(k, label(k)) for k in LEVELS]

    def values(self):
        return [label(k) for k in LEVELS]

    def __iter__(self):
        return iter(LEVELS)

    def __len__(self):
        return len(LEVELS)

    def __contains__(self, k):
        return k in LEVELS

    def keys(self):
        return list(LEVELS)


PRIVACY = _Labels()


class _Choices(list):
    """[(key, label)] for a settings select, read fresh every time so the labels follow the viewer's units."""
    def __init__(self, skip=()):
        super().__init__()
        self._skip = tuple(skip)

    def _rows(self):
        return [(k, label(k)) for k in LEVELS if k not in self._skip]

    def __iter__(self):
        return iter(self._rows())

    def __len__(self):
        return len(self._rows())

    def __getitem__(self, i):
        return self._rows()[i]

    def __contains__(self, x):
        return x in self._rows() or x in [k for k, _l in self._rows()]


def choices(skip=()):
    """The one list of choices (a list of (key, label) that stays current) for any form or setting; `skip` leaves some keys out (for example "exact" where it makes no sense)."""
    return _Choices(skip)


def help_text():
    """The sentence every help text uses to say what the choices are."""
    steps = [f"{LEVELS[k][0]} about {_width_words(k)}" for k in LEVELS if LEVELS[k][1] is not None]
    return "Exact shows the precise coordinates; the others round the position to a grid (" + ", ".join(steps) + "); Hidden keeps the station off every public map. A rounded position is drawn as a circle around the point shown."


def _how(level):
    level = level if level in LEVELS else DEFAULT
    how = LEVELS[level][1]
    if how == "site":
        return ("dec", cfg_int("general_precision_decimals"))
    return how


def decimals(level):
    """Decimal places the level rounds to; None for exact, hidden and the grids of a fixed width."""
    how = _how(level)
    return how[1] if how and how[0] == "dec" else None


def position(lat, lon, level):
    """The position to show for a station, or None when it is hidden or has none. Exact keeps five places (about a meter)."""
    level = level if level in LEVELS else DEFAULT
    if lat is None or lon is None or level == "hidden":
        return None
    if level == "exact":
        return round(lat, 5), round(lon, 5)
    how = _how(level)
    if how[0] == "dec":
        return round(lat, how[1]), round(lon, how[1])
    g = how[1]
    dlat = g / M_PER_DEG
    la = round(round(lat / dlat) * dlat, 6)
    dlon = g / (M_PER_DEG * max(0.05, math.cos(math.radians(la))))
    return la, round(round(lon / dlon) * dlon, 6)


def radius_m(level):
    """Radius in meters of the circle drawn around an approximate position: half of one step of the rounding (0 for exact and hidden)."""
    level = level if level in LEVELS else DEFAULT
    how = _how(level)
    if how is None:
        return 0
    return int(0.5 * (g_width(how)))


def g_width(how):
    return how[1] if how[0] == "grid" else 10 ** -how[1] * M_PER_DEG
