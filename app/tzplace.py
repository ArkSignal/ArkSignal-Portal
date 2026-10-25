"""The time zone of a place: the zone listed for the airport's country whose reference point (zone.tab in the system's time zone files) is nearest to it, so a flight's times can be written in each airport's own
local time and stored in UTC. A country the files do not list, or a machine without the list, falls back to a whole-hour offset from the longitude (written as a fixed zone)."""
import math
import os
from functools import lru_cache

TAB_PATHS = ("/usr/share/zoneinfo/zone.tab", "/usr/lib/zoneinfo/zone.tab", "/usr/share/lib/zoneinfo/zone.tab", "/etc/zoneinfo/zone.tab")


def _coord(text):
    """+3254-11655 or +325400-1165500 -> (lat, lon) degrees."""
    sign_i = max(text.rfind("+"), text.rfind("-"), 1)
    la, lo = text[:sign_i], text[sign_i:]
    def part(s, deg):
        sign = -1 if s.startswith("-") else 1
        s = s.lstrip("+-")
        d, rest = int(s[:deg]), s[deg:]
        return sign * (d + (int(rest[:2]) / 60 if rest else 0) + (int(rest[2:4]) / 3600 if len(rest) >= 4 else 0))
    return part(la, 2), part(lo, 3)


@lru_cache(maxsize=1)
def _zones():
    for p in TAB_PATHS:
        if os.path.exists(p):
            out = []
            for line in open(p, encoding="utf-8", errors="replace"):
                if line.startswith("#") or not line.strip():
                    continue
                cols = line.rstrip("\n").split("\t")
                if len(cols) >= 3:
                    try:
                        out.append((cols[0], _coord(cols[1]), cols[2]))
                    except (ValueError, IndexError):
                        pass
            if out:
                return out
    return []


def _km(a, b):
    dy, dx = (b[0] - a[0]) * 111.195, (b[1] - a[1]) * 111.195 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def _rules(zone):
    """What a zone's clock does this year: its offset in January and in July (the same pair means the same clock for any flight in living memory)."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    y = datetime.now(timezone.utc).year
    try:
        z = ZoneInfo(zone)
        return (datetime(y, 1, 1, 12, tzinfo=z).utcoffset(), datetime(y, 7, 1, 12, tzinfo=z).utcoffset())
    except Exception:
        return (zone,)


def zone_for(country, lat, lon):
    """An IANA zone name for a place (country is the two-letter code), or an Etc/GMT zone from the longitude when nothing better is known."""
    zones = _zones()
    here = (lat, lon)
    pool = [z for z in zones if z[0] == (country or "").upper()] or zones
    if pool:
        near = min(pool, key=lambda z: _km(here, z[1]))
        # several zones of a country share today's clock (Chicago, Tell City and Knox are all US Central time): give the plain, well-known one (fewest slashes in its name, then the nearest)
        same = [z for z in pool if z[0] == near[0] and _rules(z[2]) == _rules(near[2])]
        return min(same, key=lambda z: (z[2].count("/"), _km(here, z[1])))[2]
    off = max(-12, min(14, round(lon / 15.0)))
    return "UTC" if off == 0 else f"Etc/GMT{'-' if off > 0 else '+'}{abs(off)}"   # Etc/GMT-5 means UTC+5
