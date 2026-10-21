"""AIS (the radio beacon ships and boats send) decoded from NMEA 0183 sentences, with the standard library only.

An AIS sentence looks like `!AIVDM,1,1,,B,177KQJ5000G?tO`K>RA1wUbN0TKH,0*5C`: how many parts the message has and which one this is, a sequence id for joining parts, the radio channel, the payload (six bits to a
character) and a check sum. This module checks the sum, joins multi-part messages, turns the payload back into bits and reads the fields of the message types that matter on a river:

  1, 2, 3   position report of a big ship (Class A)         18, 19   position report of a small craft (Class B; 19 also names it)
  5         voyage and static data of a Class A ship          24       static data of a Class B craft (part A the name, part B the type, call sign and size)

`decode(sentence)` returns a dict of what that sentence adds (an empty or None result for anything else) and `Assembler` joins the parts. Positions are degrees (None when the ship says it has none), speeds are
knots, courses degrees; sizes are meters. Nothing here touches the network or a database."""

STATUS = {0: "Under way using engine", 1: "At anchor", 2: "Not under command", 3: "Restricted maneuverability", 4: "Constrained by draught", 5: "Moored", 6: "Aground", 7: "Engaged in fishing",
          8: "Under way sailing", 11: "Towing astern", 12: "Pushing ahead or towing alongside", 14: "Emergency beacon", 15: "Not defined"}


def ship_type_name(t):
    """What a ship type number means (the first digit is the group; the second, the cargo or detail)."""
    if t is None or t == 0:
        return "Not stated"
    names = {20: "Wing in ground", 30: "Fishing", 31: "Towing", 32: "Towing (large)", 33: "Dredging or underwater work", 34: "Diving", 35: "Military", 36: "Sailing", 37: "Pleasure craft",
             50: "Pilot vessel", 51: "Search and rescue", 52: "Tug", 53: "Port tender", 54: "Anti-pollution", 55: "Law enforcement", 58: "Medical", 59: "Special craft"}
    if t in names:
        return names[t]
    g = t // 10
    base = {6: "Passenger", 7: "Cargo", 8: "Tanker", 9: "Other"}.get(g)
    if base:
        return base
    return names.get(g * 10, "Type " + str(t))


def category(t):
    """A short group for coloring and filtering: tow, cargo, tanker, passenger, pleasure, fishing, service or other."""
    if t in (31, 32, 52):
        return "tow"
    if t is None or t == 0:
        return "unknown"
    g = t // 10
    if t in (36, 37):
        return "pleasure"
    if g == 7:
        return "cargo"
    if g == 8:
        return "tanker"
    if g == 6:
        return "passenger"
    if t == 30:
        return "fishing"
    if g == 5 or t in (33, 34, 35):
        return "service"
    return "other"


def checksum_ok(sentence):
    """True when the two hex digits after the * match the XOR of everything between ! and *. A sentence without a sum is not trusted."""
    s = sentence.strip()
    if not s.startswith(("!", "$")) or "*" not in s:
        return False
    body, _star, tail = s[1:].rpartition("*")
    try:
        want = int(tail[:2], 16)
    except ValueError:
        return False
    x = 0
    for ch in body:
        x ^= ord(ch)
    return x == want


def _bits(payload, fill):
    out = []
    for ch in payload:
        v = ord(ch) - 48
        if v > 40:
            v -= 8
        if not 0 <= v < 64:
            return None
        out.append(format(v, "06b"))
    s = "".join(out)
    return s[:len(s) - fill] if fill else s


def _u(b, a, n):
    seg = b[a:a + n]
    return int(seg, 2) if len(seg) == n else None


def _i(b, a, n):
    v = _u(b, a, n)
    if v is None:
        return None
    return v - (1 << n) if v >= 1 << (n - 1) else v


def _text(b, a, n):
    """Six-bit ASCII text of n bits, trailing @ and spaces removed."""
    chars = []
    for i in range(a, a + n, 6):
        v = _u(b, i, 6)
        if v is None:
            break
        chars.append(chr(v + 64) if v < 32 else chr(v))
    return "".join(chars).replace("@", " ").strip()


def _pos(lon_raw, lat_raw):
    if lon_raw is None or lat_raw is None or lon_raw == 0x6791AC0 or lat_raw == 0x3412140:
        return None, None
    lon, lat = lon_raw / 600000.0, lat_raw / 600000.0
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else (None, None)


def decode_payload(payload, fill=0):
    """The fields a payload carries, or None for a message type this site does not read."""
    b = _bits(payload, fill)
    if not b or len(b) < 38:
        return None
    t, mmsi = _u(b, 0, 6), _u(b, 8, 30)
    if not mmsi:
        return None
    out = {"type": t, "mmsi": mmsi}
    if t in (1, 2, 3) and len(b) >= 137:
        sog, cog, hdg = _u(b, 50, 10), _u(b, 116, 12), _u(b, 128, 9)
        out.update(status=_u(b, 38, 4), speed=None if sog in (None, 1023) else sog / 10.0, course=None if cog in (None, 3600) else cog / 10.0, heading=None if hdg in (None, 511) else hdg)
        out["lat"], out["lon"] = _pos(_i(b, 61, 28), _i(b, 89, 27))
        out["cls"] = "A"
    elif t == 5 and len(b) >= 422:
        out.update(imo=_u(b, 40, 30) or None, callsign=_text(b, 70, 42), name=_text(b, 112, 120), ship_type=_u(b, 232, 8), to_bow=_u(b, 240, 9), to_stern=_u(b, 249, 9), to_port=_u(b, 258, 6),
                   to_starboard=_u(b, 264, 6), draught=(_u(b, 294, 8) or 0) / 10.0 or None, destination=_text(b, 302, 120), cls="A")
    elif t in (18, 19) and len(b) >= 133:
        sog, cog, hdg = _u(b, 46, 10), _u(b, 112, 12), _u(b, 124, 9)
        out.update(speed=None if sog in (None, 1023) else sog / 10.0, course=None if cog in (None, 3600) else cog / 10.0, heading=None if hdg in (None, 511) else hdg, cls="B")
        out["lat"], out["lon"] = _pos(_i(b, 57, 28), _i(b, 85, 27))
        if t == 19 and len(b) >= 271:
            out.update(name=_text(b, 143, 120), ship_type=_u(b, 263, 8))
    elif t == 24 and len(b) >= 160:
        part = _u(b, 38, 2)
        out["cls"] = "B"
        if part == 0:
            out["name"] = _text(b, 40, 120)
        elif part == 1 and len(b) >= 162:
            out.update(ship_type=_u(b, 40, 8), callsign=_text(b, 90, 42), to_bow=_u(b, 132, 9), to_stern=_u(b, 141, 9), to_port=_u(b, 150, 6), to_starboard=_u(b, 156, 6))
    else:
        return None
    return out


class Assembler:
    """Joins the parts of multi-part sentences (type 5 needs two) and decodes whole messages. Parts wait up to `keep` seconds for their partner."""

    def __init__(self, keep=30):
        self.parts, self.keep = {}, keep

    def feed(self, sentence, now=0.0):
        s = sentence.strip()
        if not s or not checksum_ok(s):
            return None
        f = s[1:].rpartition("*")[0].split(",")
        if len(f) < 7 or not f[0].endswith(("VDM", "VDO")):
            return None
        try:
            total, num, fill = int(f[1]), int(f[2]), int(f[6] or 0)
        except ValueError:
            return None
        if total == 1:
            return decode_payload(f[5], fill)
        key = (f[3], f[4])
        for k in [k for k, v in self.parts.items() if now - v["at"] > self.keep]:
            del self.parts[k]
        slot = self.parts.setdefault(key, {"at": now, "n": total, "p": {}, "fill": 0})
        slot["p"][num] = f[5]
        if num == total:
            slot["fill"] = fill
        if len(slot["p"]) == total and all(i in slot["p"] for i in range(1, total + 1)):
            del self.parts[key]
            return decode_payload("".join(slot["p"][i] for i in range(1, total + 1)), slot["fill"])
        return None


def decode(sentence):
    """One single-part sentence decoded; None for anything else (use Assembler for multi-part messages)."""
    return Assembler().feed(sentence)
