#!/usr/bin/env python3
"""The private tunnel's relay (request 205): the only thing a tunnel peer can talk to.

It listens on the tunnel's own address, port 1884, and carries a peer's MQTT connection to the broker's private listener. Before it carries anything it reads the MQTT CONNECT packet and checks two things:
  * the login named in it is one the portal has bound to the peer's tunnel address (so a peer can use its own login and no other, even one it has learned);
  * the password in it is replaced with one only the relay and the portal can make (an HMAC of the login and the password with a key kept in /wg/relay.key), so a login's password the member holds is
    useless on the broker's other doors (the public TLS listener, the internal one): the broker does not know it.
Everything after the CONNECT packet is carried unchanged. The portal writes /wg/peers.json (each peer's address and logins) and /wg/relay.key through the mesh service; this reads them again every few seconds."""
import base64
import hashlib
import hmac
import json
import os
import socket
import struct
import threading
import time

ADDR = os.environ.get("WG_ADDRESS", "10.77.0.1")
BROKER = (os.environ.get("BROKER_HOST", "mesh-mqtt"), 1884)
W = "/wg"
_state = {"at": 0.0, "by_ip": {}, "key": b""}
_lock = threading.Lock()


def reload():
    with _lock:
        if time.time() - _state["at"] < 3:
            return
        _state["at"] = time.time()
    by_ip, key = {}, b""
    try:
        for p in json.load(open(os.path.join(W, "peers.json"))).get("peers", []):
            by_ip[str(p.get("ip"))] = set(str(x) for x in p.get("logins", []))
    except (OSError, ValueError):
        pass
    try:
        key = open(os.path.join(W, "relay.key")).read().strip().encode()
    except OSError:
        pass
    with _lock:
        _state["by_ip"], _state["key"] = by_ip, key


def recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def varint_read(s):
    value, shift, raw = 0, 0, b""
    for _ in range(4):
        b = recv_exact(s, 1)
        raw += b
        value |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return value, raw
        shift += 7
    raise ValueError("bad length")


def varint_bytes(v):
    out = b""
    while True:
        b = v & 0x7F
        v >>= 7
        out += bytes([b | (0x80 if v else 0)])
        if not v:
            return out


def varint_at(buf, i):
    value, shift, n = 0, 0, 0
    while True:
        b = buf[i + n]
        n += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, n
        shift += 7
        if n > 4:
            raise ValueError("bad length")


def parse_connect(body):
    """(level, username, password, offset of the password field, its end) from the body of a CONNECT packet; username and password are bytes or None."""
    n = struct.unpack(">H", body[0:2])[0]
    i = 2 + n
    level, flags = body[i], body[i + 1]
    i += 4   # level, flags, keep alive
    if level == 5:
        plen, used = varint_at(body, i)
        i += used + plen
    cl = struct.unpack(">H", body[i:i + 2])[0]
    i += 2 + cl   # client id
    if flags & 0x04:   # a will
        if level == 5:
            plen, used = varint_at(body, i)
            i += used + plen
        tl = struct.unpack(">H", body[i:i + 2])[0]
        i += 2 + tl
        ml = struct.unpack(">H", body[i:i + 2])[0]
        i += 2 + ml
    user = pw = None
    pw_start = pw_end = None
    if flags & 0x80:
        ul = struct.unpack(">H", body[i:i + 2])[0]
        user = body[i + 2:i + 2 + ul]
        i += 2 + ul
    if flags & 0x40:
        pl = struct.unpack(">H", body[i:i + 2])[0]
        pw = body[i + 2:i + 2 + pl]
        pw_start, pw_end = i, i + 2 + pl
    return level, user, pw, pw_start, pw_end


def broker_password(key, user, pw):
    """The password the broker holds for a private login: the same function the portal uses (app/meshwg.py broker_password)."""
    mac = hmac.new(key, user + b"\0" + pw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=")[:43]


def refuse(c, level):
    try:
        c.sendall(b"\x20\x03\x00\x87\x00" if level == 5 else b"\x20\x02\x00\x05")
    except OSError:
        pass


def pipe(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    for s in (a, b):
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def handle(c, peer):
    level = 4
    try:
        c.settimeout(10)
        first = recv_exact(c, 1)
        if first[0] != 0x10:
            c.close(); return
        length, _raw = varint_read(c)
        if length > 4096:
            c.close(); return
        body = recv_exact(c, length)
        level, user, pw, ps, pe = parse_connect(body)
        reload()
        with _lock:
            allowed, key = _state["by_ip"].get(peer[0], set()), _state["key"]
        if user is None or pw is None or not key or user.decode("utf-8", "ignore") not in allowed:
            print("refused %s as %s" % (peer[0], (user or b"").decode("utf-8", "ignore")[:40]), flush=True)
            refuse(c, level)
            c.close(); return
        new = broker_password(key, user, pw)
        body = body[:ps] + struct.pack(">H", len(new)) + new + body[pe:]
        up = socket.create_connection(BROKER, timeout=10)
        up.settimeout(None)
        up.sendall(b"\x10" + varint_bytes(len(body)) + body)
        c.settimeout(None)
        threading.Thread(target=pipe, args=(up, c), daemon=True).start()
        pipe(c, up)
    except Exception:
        try:
            c.close()
        except OSError:
            pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((ADDR, 1884))
    srv.listen(64)
    print("relay listening on %s:1884" % ADDR, flush=True)
    while True:
        c, peer = srv.accept()
        threading.Thread(target=handle, args=(c, peer), daemon=True).start()


if __name__ == "__main__":
    main()
