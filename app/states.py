"""Which state a position is in, for Arkansas and its neighbours (the outlines the site already carries: ar_outline.json and neighbor_states.json). A position in none of
them is "-" ("anywhere else"). Used to give every balloon flight and receiving station a state, so the lists can be limited to the states chosen in Settings."""
import json
import os

from . import arkansas

NAMES = {"AR": "Arkansas", "MO": "Missouri", "TN": "Tennessee", "MS": "Mississippi", "LA": "Louisiana", "TX": "Texas", "OK": "Oklahoma", "-": "Anywhere else"}
OTHER = "-"
_rings = None


def _load():
    global _rings
    if _rings is None:
        with open(os.path.join(os.path.dirname(__file__), "neighbor_states.json"), encoding="utf-8") as f:
            raw = json.load(f)
        _rings = [("AR", [tuple(p) for p in arkansas.polygon()])]
        for code, rings in raw.items():
            for r in rings:
                _rings.append((code, [(float(x), float(y)) for x, y in r]))
    return _rings


def state_of(lat, lon):
    """AR, MO, TN, MS, LA, TX or OK for a position inside that state's outline, else '-'."""
    if lat is None or lon is None:
        return OTHER
    for code, ring in _load():
        if arkansas._inside(lat, lon, ring):
            return code
    return OTHER


def chosen(value):
    """The list of state codes in a setting value such as 'AR,MO'."""
    out = [c for c in (value or "").replace(" ", "").upper().split(",") if c in NAMES]
    return out or ["AR"]
