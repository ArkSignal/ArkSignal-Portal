"""Drone Remote ID messages (ASTM F3411, the broadcast the FAA and other regulators require most drones to send) decoded, with the standard library only.

A Remote ID broadcast is a 25-byte message; several are often sent together as a *message pack* (a first byte of 0xF, the size of one message, the count, then the messages). The type is the high nibble of
the first byte. The layouts follow the ASTM standard as written out in the OpenDroneID reference code (opendroneid-core-c) that the common receivers (ESP32 and Android scanners) are built on:

  0 Basic ID      who it is: an ID type (1 serial number, 2 registration, 3 UUID, 4 session ID) and the 20-character ID, and what kind of aircraft
  1 Location      where it is: status, direction, speed, vertical speed, latitude, longitude, three altitudes
  3 Self ID       a free-text description the pilot chose
  4 System        where the operator is (when the receiver passes it on), the operating area
  5 Operator ID   the operator's registration, 20 characters
  2 Authentication is recognized and skipped.

`decode(raw_bytes)` returns a dict of everything the message or pack says; keys are `id`, `id_type`, `ua_type`, `lat`, `lon`, `alt_geo`, `alt_baro`, `height`, `speed` (m/s), `heading`, `vspeed`, `status`, `op_lat`, `op_lon`,
`op_id`, `desc`. Values the aircraft marks as unknown are left out. Nothing here touches the network or a database."""
import struct

UA_TYPES = {0: "Not stated", 1: "Airplane", 2: "Helicopter or multirotor", 3: "Gyroplane", 4: "Hybrid lift", 5: "Ornithopter", 6: "Glider", 7: "Kite", 8: "Free balloon", 9: "Captive balloon", 10: "Airship",
            11: "Parachute", 12: "Rocket", 13: "Tethered powered aircraft", 14: "Ground obstacle", 15: "Other"}
ID_TYPES = {0: "None", 1: "Serial number", 2: "Registration ID", 3: "UTM UUID", 4: "Session ID"}
STATUS = {0: "Undeclared", 1: "On the ground", 2: "In the air", 3: "Emergency", 4: "Remote ID system failure"}


def _alt(v):
    return None if v == 0xFFFF else v * 0.5 - 1000.0


def _text(b):
    return b.split(b"\x00")[0].decode("ascii", "replace").strip()


def decode_message(m):
    """One 25-byte message as a dict (empty for a type this site does not use)."""
    if len(m) != 25:
        return {}
    t = m[0] >> 4
    out = {}
    if t == 0:
        out["ua_type"], out["id_type"] = m[1] & 0x0F, m[1] >> 4
        ident = _text(m[2:22])
        if ident:
            out["id"] = ident
    elif t == 1:
        flags = m[1]
        status, ew, mult = flags >> 4, (flags >> 1) & 1, flags & 1
        out["status"] = status
        if m[2] <= 179:   # the direction is 0 to 179 with the east-west flag adding 180 (a byte above 179 means unknown)
            out["heading"] = float(m[2] + (180 if ew else 0))
        if m[3] != 255:
            out["speed"] = m[3] * 0.25 if not mult else m[3] * 0.75 + 255 * 0.25
        vs = struct.unpack("b", m[4:5])[0]
        if vs != 63:
            out["vspeed"] = vs * 0.5
        lat, lon = struct.unpack("<ii", m[5:13])
        if lat != 0 or lon != 0:
            la, lo = lat * 1e-7, lon * 1e-7
            if -90 <= la <= 90 and -180 <= lo <= 180:
                out["lat"], out["lon"] = la, lo
        for key, off in (("alt_baro", 13), ("alt_geo", 15), ("height", 17)):
            v = _alt(struct.unpack("<H", m[off:off + 2])[0])
            if v is not None:
                out[key] = v
    elif t == 3:
        d = _text(m[2:25])
        if d:
            out["desc"] = d
    elif t == 4:
        lat, lon = struct.unpack("<ii", m[2:10])
        if lat != 0 or lon != 0:
            la, lo = lat * 1e-7, lon * 1e-7
            if -90 <= la <= 90 and -180 <= lo <= 180:
                out["op_lat"], out["op_lon"] = la, lo
    elif t == 5:
        o = _text(m[2:22])
        if o:
            out["op_id"] = o
    return out


def decode(raw):
    """A message or a message pack decoded and merged into one dict ({} when there is nothing usable)."""
    if not raw:
        return {}
    if len(raw) == 25:
        return decode_message(raw)
    if raw[0] >> 4 == 0xF and len(raw) >= 3 and raw[1] == 25:
        n = raw[2]
        out = {}
        for i in range(n):
            part = raw[3 + 25 * i:3 + 25 * (i + 1)]
            if len(part) != 25:
                break
            out.update(decode_message(part))
        return out
    return {}


def decode_hex(text):
    """The same from a hex string (spaces, colons and a leading 0x are allowed); {} when it is not hex."""
    t = "".join(ch for ch in text.strip().lower().replace("0x", "") if ch in "0123456789abcdef")
    if not t or len(t) % 2:
        return {}
    try:
        return decode(bytes.fromhex(t))
    except ValueError:
        return {}
