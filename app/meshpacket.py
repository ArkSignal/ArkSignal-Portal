"""Building Meshtastic packets for the site's own messages (the alert and weather channel). The channel key is used HERE, in the portal, and never leaves it: the mesh service is only handed the finished (encrypted)
packet to publish. A ServiceEnvelope { packet (MeshPacket), channel_id, gateway_id } goes to <root>/<community>/2/e/<channel>/<gateway>; the text is encrypted with AES-CTR under the channel's key (nonce: packet id
8 bytes little endian, sender node 4 bytes little endian, four zero bytes); a one byte key indexes the well known default key."""
import os
import struct
import time

DEFAULT_PSK = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")
PORT_TEXT = 1
_ids = {"n": int.from_bytes(os.urandom(4), "little")}


def expand_psk(psk):
    """The key a channel really uses: a one byte key indexes the default key, a short key is padded with zeros to 16 or 32 bytes, an empty key means no encryption."""
    if not psk or psk == b"\x00":
        return b""
    if len(psk) == 1:
        k = bytearray(DEFAULT_PSK)
        k[15] = (k[15] + psk[0] - 1) & 0xFF
        return bytes(k)
    if len(psk) < 16:
        return psk.ljust(16, b"\x00")
    if len(psk) < 32 and len(psk) != 16:
        return psk.ljust(32, b"\x00")
    return psk[:32]


def channel_hash(name, key):
    h = 0
    for b in name.encode("utf-8"):
        h ^= b
    for b in key:
        h ^= b
    return h


def _crypt(key, packet_id, from_node, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    nonce = struct.pack("<Q", packet_id) + struct.pack("<I", from_node) + b"\x00\x00\x00\x00"
    c = Cipher(algorithms.AES(key), modes.CTR(nonce)).encryptor()
    return c.update(data) + c.finalize()


def _venc(v):
    out = b""
    v &= (1 << 64) - 1
    while True:
        b = v & 0x7F
        v >>= 7
        out += bytes([b | (0x80 if v else 0)])
        if not v:
            return out


def _f_var(num, v):
    return _venc(num << 3) + _venc(v)


def _f_fix32(num, v):
    return _venc((num << 3) | 5) + struct.pack("<I", v & 0xFFFFFFFF)


def _f_len(num, data):
    return _venc((num << 3) | 2) + _venc(len(data)) + data


def next_packet_id():
    _ids["n"] = (_ids["n"] + 1) & 0xFFFFFFFF or 1
    return _ids["n"]


def build_envelope(portnum, payload, psk, channel, from_node, gateway, packet_id=None, to=0xFFFFFFFF, hop_limit=3, rx_time=None):
    """The bytes of a ServiceEnvelope carrying one packet from `from_node` to `to` (everyone by default) on a channel, encrypted when the channel has a key. want_ack is never set."""
    key = expand_psk(psk)
    pid = packet_id or next_packet_id()
    data = _f_var(1, portnum) + _f_len(2, payload)
    mp = _f_fix32(1, from_node) + _f_fix32(2, to) + _f_var(3, channel_hash(channel, key))
    mp += _f_len(5, _crypt(key, pid, from_node, data)) if key else _f_len(4, data)
    mp += _f_fix32(6, pid) + _f_fix32(7, int(rx_time or time.time())) + _f_var(9, hop_limit) + _f_var(14, 1) + _f_var(15, hop_limit)
    return _f_len(1, mp) + _f_len(2, channel.encode("utf-8")) + _f_len(3, gateway.encode("utf-8"))


def _fields(buf):
    out, i, n = [], 0, len(buf)
    try:
        while i < n:
            key, sh = 0, 0
            while True:
                b = buf[i]; i += 1
                key |= (b & 0x7F) << sh; sh += 7
                if not b & 0x80:
                    break
            num, wt = key >> 3, key & 7
            if wt == 0:
                v, sh = 0, 0
                while True:
                    b = buf[i]; i += 1
                    v |= (b & 0x7F) << sh; sh += 7
                    if not b & 0x80:
                        break
            elif wt == 5:
                v = struct.unpack("<I", buf[i:i + 4])[0]; i += 4
            elif wt == 2:
                ln, sh = 0, 0
                while True:
                    b = buf[i]; i += 1
                    ln |= (b & 0x7F) << sh; sh += 7
                    if not b & 0x80:
                        break
                v = bytes(buf[i:i + ln]); i += ln
            else:
                break
            out.append((num, wt, v))
    except IndexError:
        pass
    return out


def _first(fl, num, default=None):
    for n, _w, v in fl:
        if n == num:
            return v
    return default


def open_envelope(raw, psk):
    """The packet in an envelope with its payload decrypted when it is encrypted: {from, to, id, channel, portnum, payload, hop_limit} or None (the key is the channel's, as given to build_envelope)."""
    env = _fields(raw)
    pk = _first(env, 1)
    if not isinstance(pk, bytes):
        return None
    mp = _fields(pk)
    sender, pid = _first(mp, 1), _first(mp, 6)
    if sender is None or pid is None:
        return None
    enc, dec = _first(mp, 5), _first(mp, 4)
    if isinstance(enc, bytes):
        key = expand_psk(psk)
        if not key:
            return None
        dec = _crypt(key, pid, sender, enc)
    if not isinstance(dec, bytes):
        return None
    d = _fields(dec)
    return {"from": sender, "to": _first(mp, 2), "id": pid, "channel": (_first(env, 2, b"") or b"").decode("utf-8", "ignore"), "portnum": _first(d, 1, 0), "payload": _first(d, 2, b"") or b"", "hop_limit": _first(mp, 9),
            "gateway": (_first(env, 3, b"") or b"").decode("utf-8", "ignore")}
