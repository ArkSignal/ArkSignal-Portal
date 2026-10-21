"""arksignal-mesh: keeps the Meshtastic and MeshCore nodes that publish themselves to the community MQTT broker: who they are (the unencrypted map report a node sends on purpose for public maps: name, hardware,
role, firmware, region, position at the precision its owner chose), when each was last heard, and which gateway heard it with what signal (for the coverage view). Its own container and database.

Terms (DATA_SOURCES_TERMS.md): only what node owners deliberately publish is used: packets that arrive already decoded (the map report; position, node information and telemetry sent in the clear).
Anything encrypted, a private channel above all, is never decrypted and never stored. A node can be hidden by its ID on request. Nothing is read from other people's maps.
MeshCore: an observer (the meshcoretomqtt program on a repeater or companion) publishes the packets its radio hears as JSON under meshcore/<airport code>/<public key>/packets; only the ADVERT packets (a node announcing its name, kind and
position on purpose, signed with its own key, which is checked) are used; every other packet type is encrypted chat and is dropped unread.
The broker is our own (the mosquitto container in the same stack) unless the portal's settings point the service at another one the site is allowed to read.
"""
import base64
import hashlib
import json
import math
import os
import re
import struct
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
from psycopg.rows import dict_row

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:   # the signature check is skipped, and said so in the statistics, when the library is missing
    Ed25519PublicKey = None

DB_URL = os.environ.get("DATABASE_URL", "postgresql://mesh:mesh@mesh-db:5432/mesh")
PORT = int(os.environ.get("PORT", "8482"))
VERSION = "dev"
try:
    t = open("/BUILD_INFO").read()
    VERSION = t.split("version:")[1].split()[0] if "version:" in t else "dev"
except OSError:
    pass

DEFAULTS = {"enabled": "1", "host": "mesh-mqtt", "port": "1883", "user": "", "password": "", "topic": "msh/#,meshcore/#", "south": "32.0", "west": "-95.5", "north": "37.2", "east": "-88.8", "keep_days": "60",
            "heard_keep_days": "14", "links_keep_days": "30", "up_enabled": "1", "up_host": "mqtt.meshtastic.org", "up_port": "1883", "up_user": "meshdev", "up_password": "large4cats", "up_topic": "msh/US/AR/#",
            "public_rules": "1", "public_channels": "LongFast,LongSlow,LongModerate,MediumFast,MediumSlow,ShortFast,ShortSlow,ShortTurbo,VLongSlow", "public_portnums": "1,3,4,5,7,67,71,73",
            "public_max_bits": "16", "publish_root": "msh/US/AR"}
STATE = {"connected": False, "packets": 0, "decoded": 0, "encrypted": 0, "stored": 0, "outside": 0, "mc_packets": 0, "mc_adverts": 0, "mc_dropped": 0, "mc_badsig": 0, "links": 0, "public_dropped": 0, "public_coarsened": 0, "since": time.time(), "last": None, "error": "", "reconnects": 0, "up_connected": False, "up_error": "", "up_reconnects": 0}
_cfg = {}
_lock = threading.Lock()

PORT_MAP_REPORT, PORT_POSITION, PORT_NODEINFO, PORT_TELEMETRY, PORT_NEIGHBORINFO = 73, 3, 4, 67, 71
ROLES = {0: "Client", 1: "Client (muted)", 2: "Router", 3: "Router and client", 4: "Repeater", 5: "Tracker", 6: "Sensor", 7: "TAK", 8: "Client (hidden)", 9: "Lost and found", 10: "TAK tracker", 11: "Router (late)", 12: "Client (base)"}
HARDWARE = {1: "TLoRa V2", 2: "TLoRa V1", 3: "TLoRa V2.1 1.6", 4: "T-Beam", 5: "Heltec V2.0", 6: "T-Beam 0.7", 7: "T-Echo", 8: "TLoRa V1.1.3", 9: "RAK4631", 10: "Heltec V2.1", 11: "Heltec V1", 12: "T-Beam S3 Core", 13: "RAK11200", 14: "Nano G1", 15: "TLoRa V2.1 1.8", 16: "TLoRa T3 S3", 17: "Nano G1 Explorer", 18: "Nano G2 Ultra", 19: "LoRa Type", 20: "Wiphone", 22: "RAK2560", 23: "Heltec Hru 3601", 24: "Heltec Wireless Bridge", 25: "Station G1", 26: "Rak11310", 27: "Makerfabs Tracker", 28: "Makerfabs Reserved", 30: "RP2040 LoRa", 31: "Station G2", 32: "LoRa Relay V1", 34: "Ppr", 35: "Genieblocks", 36: "nRF52 Unknown", 37: "Portduino", 38: "Android Sim", 39: "Diy V1", 40: "Nrf52840 Pca10059", 41: "Dr Dev", 42: "M5Stack", 43: "Heltec V3", 44: "Heltec Wsl V3", 45: "Betafpv 2400 Tx", 46: "Betafpv 900 Nano Tx", 47: "Rpi Pico", 49: "Heltec Wireless Paper", 50: "T-Deck", 51: "T-Watch S3", 52: "Picomputer S3", 53: "Heltec HT62", 54: "EByte ESP32 S3", 55: "ESP32 S3 Pico", 56: "Chatter 2", 57: "Heltec Wireless Paper V1 0", 58: "Heltec Wireless Tracker V1 0", 59: "Unphone", 60: "Td Lorac", 61: "Cdebyte Eora S3", 62: "Twc Mesh V4", 63: "nRF52 Promicro Diy", 64: "Radiomaster 900 Bandit Nano", 65: "Heltec Capsule Sensor V3", 66: "Heltec Vision Master T190", 67: "Heltec Vision Master E213", 68: "Heltec Vision Master E290", 69: "Heltec Mesh Node T114", 70: "Sensecap Indicator", 72: "RAK3172", 73: "Wio E5", 74: "Radiomaster 900 Bandit", 75: "Me25ls01 4Y10TD", 76: "RP2040 Feather RFM95", 77: "M5Stack Corebasic", 78: "M5Stack CORE2", 79: "Rpi PICO2", 80: "M5Stack CORES3", 81: "Seeed XIAO S3", 82: "MS24SF1", 83: "TLoRa C6", 84: "Wismesh Tap", 85: "Routastic", 86: "Mesh Tab", 87: "Meshlink", 88: "XIAO nRF52 Kit", 90: "Thinknode M2", 91: "T-ETH Elite", 92: "Heltec Sensor Hub", 93: "Muzi Base", 94: "Heltec Mesh Pocket", 95: "Seeed Solar Node", 96: "Nomadstar Meteor Pro", 97: "Crowpanel", 98: "Link 32", 101: "Muzi R1 Neo", 104: "M5Stack Reserved", 106: "RAK3312", 107: "Thinknode M5", 108: "Heltec Mesh Solar", 109: "T-Echo Lite", 110: "Heltec V4", 111: "M5Stack C6L", 112: "M5Stack Cardputer Adv", 114: "T-Watch Ultra", 115: "Thinknode M3", 116: "Wismesh Tap V2", 117: "RAK3401", 118: "RAK6421", 119: "Thinknode M4", 120: "Thinknode M6", 121: "Meshstick 1262", 123: "T5 S3 E-Paper Pro", 125: "Mini E-Paper S3", 126: "Tdisplay S3 Pro", 127: "Heltec Mesh Node T096", 129: "Thinknode M7", 130: "Thinknode M8", 131: "Thinknode M9", 132: "Heltec V4 R8", 133: "Heltec Mesh Node T1", 134: "Station G3", 135: "T-Impulse Plus", 136: "T-Echo Card", 137: "Seeed Wio Tracker L2", 138: "Crowpanel P4", 139: "Heltec Mesh Tower V2", 140: "Meshnology W10", 141: "Heltec RC32", 142: "Heltec RC52", 143: "Heltec RCC6", 144: "Seeed Wio Tracker L1 Pro 1W", 145: "Meshnology W12", 146: "Meshpager X2", 147: "T-Connect Pro", 148: "Axiometa Genesis Mini", 149: "Makerfabs Nomad Terminal", 150: "Thinknode Mx", 255: "Private hardware"}
REGIONS = {1: "US", 2: "EU 433", 3: "EU 868", 4: "CN", 5: "JP", 6: "ANZ", 7: "KR", 8: "TW", 9: "RU", 10: "IN", 11: "NZ 865", 12: "TH", 13: "LoRa 24", 14: "UA 433", 15: "UA 868", 16: "MY 433", 17: "MY 919", 18: "SG 923"}
PRESETS = {0: "Long Fast", 1: "Long Slow", 2: "Very Long Slow", 3: "Medium Slow", 4: "Medium Fast", 5: "Short Slow", 6: "Short Fast", 7: "Long Moderate", 8: "Short Turbo"}


def db():
    return psycopg.connect(DB_URL, row_factory=dict_row, autocommit=True)


def init_db():
    for _ in range(60):
        try:
            with db() as c:
                c.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                c.execute("""CREATE TABLE IF NOT EXISTS nodes (node_id BIGINT PRIMARY KEY, long_name TEXT NOT NULL DEFAULT '', short_name TEXT NOT NULL DEFAULT '', hw_model INT, role INT, firmware TEXT NOT NULL DEFAULT '',
                    region INT, modem_preset INT, lat DOUBLE PRECISION, lon DOUBLE PRECISION, alt INT, precision_bits INT, online_local INT, battery INT, voltage REAL, channel_util REAL, air_util REAL, uptime INT,
                    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen TIMESTAMPTZ NOT NULL DEFAULT now(), last_report TIMESTAMPTZ, packets INT NOT NULL DEFAULT 0)""")
                c.execute("CREATE INDEX IF NOT EXISTS nodes_seen ON nodes (last_seen DESC)")
                c.execute("""CREATE TABLE IF NOT EXISTS heard (node_id BIGINT NOT NULL, gateway TEXT NOT NULL, snr REAL, rssi INT, best_snr REAL, ts TIMESTAMPTZ NOT NULL DEFAULT now(), count INT NOT NULL DEFAULT 1,
                    PRIMARY KEY (node_id, gateway))""")
                c.execute("""CREATE TABLE IF NOT EXISTS mc_nodes (pubkey TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', kind INT, lat DOUBLE PRECISION, lon DOUBLE PRECISION, advert_time TIMESTAMPTZ,
                    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen TIMESTAMPTZ NOT NULL DEFAULT now(), packets INT NOT NULL DEFAULT 0)""")
                c.execute("CREATE INDEX IF NOT EXISTS mc_nodes_seen ON mc_nodes (last_seen DESC)")
                c.execute("""CREATE TABLE IF NOT EXISTS mc_heard (pubkey TEXT NOT NULL, observer TEXT NOT NULL, observer_name TEXT NOT NULL DEFAULT '', snr REAL, rssi INT, best_snr REAL, hops INT,
                    ts TIMESTAMPTZ NOT NULL DEFAULT now(), count INT NOT NULL DEFAULT 1, PRIMARY KEY (pubkey, observer))""")
                c.execute("""CREATE TABLE IF NOT EXISTS links (a BIGINT NOT NULL, b BIGINT NOT NULL, snr REAL, last_seen TIMESTAMPTZ NOT NULL DEFAULT now(), reported_by TEXT NOT NULL DEFAULT '', count INT NOT NULL DEFAULT 1,
                    PRIMARY KEY (a, b))""")
                c.execute("CREATE INDEX IF NOT EXISTS links_seen ON links (last_seen DESC)")
                c.execute("CREATE TABLE IF NOT EXISTS hidden (node_key TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', added TIMESTAMPTZ NOT NULL DEFAULT now())")
            return
        except Exception as e:
            print("waiting for the database:", str(e)[:100], flush=True)
            time.sleep(2)
    raise SystemExit("the database never answered")


def cfg(key):
    with _lock:
        hit = _cfg.get(key)
        if hit and time.time() - hit[1] < 15:
            return hit[0]
    try:
        with db() as c:
            r = c.execute("SELECT value FROM config WHERE key=%s", (key,)).fetchone()
        v = r["value"] if r else os.environ.get("MESH_" + key.upper(), DEFAULTS[key])
    except Exception:
        v = DEFAULTS[key]
    with _lock:
        _cfg[key] = (v, time.time())
    return v


def set_cfg(key, value):
    if key not in DEFAULTS:
        raise ValueError("unknown setting " + key)
    with db() as c:
        c.execute("INSERT INTO config (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, str(value)))
    with _lock:
        _cfg.pop(key, None)


# ------------------------------------------------------------------ protobuf (just the few messages we read)

def fields(buf):
    """A protobuf message as a list of (field number, wire type, value): varints as ints, fixed32 as ints, length-delimited as bytes. Stops quietly at anything malformed."""
    out, i, n = [], 0, len(buf)
    try:
        while i < n:
            key = 0
            shift = 0
            while True:
                b = buf[i]; i += 1
                key |= (b & 0x7F) << shift
                if not b & 0x80:
                    break
                shift += 7
            num, wt = key >> 3, key & 7
            if wt == 0:
                v = 0; shift = 0
                while True:
                    b = buf[i]; i += 1
                    v |= (b & 0x7F) << shift
                    if not b & 0x80:
                        break
                    shift += 7
            elif wt == 5:
                v = struct.unpack("<I", buf[i:i + 4])[0]; i += 4
            elif wt == 1:
                v = struct.unpack("<Q", buf[i:i + 8])[0]; i += 8
            elif wt == 2:
                ln = 0; shift = 0
                while True:
                    b = buf[i]; i += 1
                    ln |= (b & 0x7F) << shift
                    if not b & 0x80:
                        break
                    shift += 7
                v = bytes(buf[i:i + ln]); i += ln
                if len(v) != ln:
                    break
            else:
                break
            out.append((num, wt, v))
    except (IndexError, struct.error):
        pass
    return out


def first(fl, num, default=None):
    for n, _w, v in fl:
        if n == num:
            return v
    return default


def s32(v):   # a signed 32 bit number from a varint (a negative int32 is sent as a 64 bit value) or a fixed32
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= 1 << 31 else v


def zigzag(v):
    return (v >> 1) ^ -(v & 1)


def f32(v):
    return struct.unpack("<f", struct.pack("<I", v))[0]


def decode_envelope(raw):
    """ServiceEnvelope bytes -> {node (the sender's number), gateway, channel, portnum, payload, snr, rssi, encrypted} or None."""
    env = fields(raw)
    pk = first(env, 1)
    if not isinstance(pk, bytes):
        return None
    mp = fields(pk)
    sender = first(mp, 1)
    if sender is None:
        return None
    gw = first(env, 3, b"")
    out = {"node": sender, "gateway": gw.decode("utf-8", "ignore") if isinstance(gw, bytes) else "", "channel": (first(env, 2, b"") or b"").decode("utf-8", "ignore"),
           "snr": f32(first(mp, 8)) if first(mp, 8) is not None else None, "rssi": s32(first(mp, 12)) if first(mp, 12) is not None else None, "encrypted": first(mp, 5) is not None, "portnum": None, "payload": b""}
    data = first(mp, 4)
    if isinstance(data, bytes):
        d = fields(data)
        out["portnum"], out["payload"] = first(d, 1, 0), first(d, 2, b"") or b""
    return out


def decode_map_report(payload):
    m = fields(payload)
    lat, lon = first(m, 9), first(m, 10)
    return {"long_name": (first(m, 1, b"") or b"").decode("utf-8", "ignore")[:40], "short_name": (first(m, 2, b"") or b"").decode("utf-8", "ignore")[:8], "role": first(m, 3, 0), "hw_model": first(m, 4),
            "firmware": (first(m, 5, b"") or b"").decode("utf-8", "ignore")[:30], "region": first(m, 6), "modem_preset": first(m, 7),
            "lat": s32(lat) / 1e7 if lat is not None else None, "lon": s32(lon) / 1e7 if lon is not None else None, "alt": s32(first(m, 11)) if first(m, 11) is not None else None,
            "precision_bits": first(m, 12), "online_local": first(m, 13)}


def decode_position(payload):
    p = fields(payload)
    lat, lon = first(p, 1), first(p, 2)
    return {"lat": s32(lat) / 1e7 if lat is not None else None, "lon": s32(lon) / 1e7 if lon is not None else None, "alt": s32(first(p, 3) or 0) if first(p, 3) is not None else None, "precision_bits": first(p, 23)}


def decode_nodeinfo(payload):
    u = fields(payload)   # User: id=1, long_name=2, short_name=3, macaddr=4, hw_model=5, is_licensed=6, role=7
    return {"long_name": (first(u, 2, b"") or b"").decode("utf-8", "ignore")[:40], "short_name": (first(u, 3, b"") or b"").decode("utf-8", "ignore")[:8], "hw_model": first(u, 5), "role": first(u, 7, 0)}


def decode_telemetry(payload):
    t = fields(payload)   # Telemetry: time=1, device_metrics=2 {battery_level=1, voltage=2 (float), channel_utilization=3 (float), air_util_tx=4 (float), uptime_seconds=5}
    dm = first(t, 2)
    if not isinstance(dm, bytes):
        return {}
    d = fields(dm)
    g = lambda n: (f32(first(d, n)) if first(d, n) is not None else None)
    return {"battery": first(d, 1), "voltage": g(2), "channel_util": g(3), "air_util": g(4), "uptime": first(d, 5)}


def decode_neighborinfo(payload):
    """NeighborInfo: node_id=1, last_sent_by_id=2, node_broadcast_interval_secs=3, repeated neighbors=4 {node_id=1, snr=2 (float), last_rx_time=3, node_broadcast_interval_secs=4}.
    Returns {node, neighbors: [(node, snr)]}: the reporting node and the nodes it says it hears directly (zero hops)."""
    n = fields(payload)
    out = []
    for num, _wt, item in n:
        if num != 4 or not isinstance(item, bytes):
            continue
        f = fields(item)
        nb = first(f, 1)
        snr = f32(first(f, 2)) if first(f, 2) is not None else None
        if isinstance(nb, int):
            out.append((nb, snr))
    return {"node": first(n, 1), "neighbors": out}


def store_links(c, reporter, neighbors, gw):
    """Keep the direct links a node reported, only when the reporter is a node we already have (so inside the area, not hidden) and the neighbor is not hidden, not itself and not the broadcast address; a
    signal outside what a LoRa radio can report is dropped. Returns how many links were stored."""
    if not c.execute("SELECT 1 FROM nodes WHERE node_id=%s", (reporter,)).fetchone():
        return 0
    hid = {r["node_key"] for r in c.execute("SELECT node_key FROM hidden WHERE node_key LIKE '!%%'").fetchall()}
    n = 0
    for nb, snr in neighbors[:200]:
        if not (0 < nb < 0xFFFFFFFF) or nb == reporter or ("!%08x" % nb) in hid:
            continue
        if snr is not None and not (-40 <= snr <= 40):
            snr = None
        c.execute("INSERT INTO links (a, b, snr, last_seen, reported_by) VALUES (%s,%s,%s,now(),%s) ON CONFLICT (a, b) DO UPDATE SET snr=EXCLUDED.snr, last_seen=now(), reported_by=EXCLUDED.reported_by, count=links.count+1",
                  (reporter, nb, snr, gw or ""))
        n += 1
    return n


# The public Meshtastic server's rules (https://meshtastic.org/docs/software/integrations/mqtt/#public-mqtt-server), applied to what this service keeps from the channels that use the default key: only the
# port numbers it allows, and positions only as imprecise as it allows (10 to 16 bits). A position that arrives more exact than that is made coarser before it is stored, and a port number that is not on the
# list is dropped. The list is the public server's plus Neighbor Info (71), which the site reads on purpose.
def is_public(channel):
    return cfg("public_rules") == "1" and (channel or "").strip().lower() in {x.strip().lower() for x in cfg("public_channels").split(",") if x.strip()}


def port_allowed(channel, port):
    if not is_public(channel):
        return True
    return str(port) in {x.strip() for x in cfg("public_portnums").split(",") if x.strip()}


def coarsen(lat, lon, bits):
    """The position cut to `bits` bits of precision the way the Meshtastic firmware does it (the cell's middle), as (lat, lon)."""
    bits = max(0, min(32, int(bits)))
    if bits >= 32:
        return lat, lon
    shift = 32 - bits
    out = []
    for v in (lat, lon):
        i = int(round(v * 1e7))
        i = ((i >> shift) << shift) + ((1 << (shift - 1)) if shift else 0)
        out.append(i / 1e7)
    return out[0], out[1]


def public_position(channel, lat, lon, bits):
    """(lat, lon) as the public rules allow on this channel: unchanged on a private channel or when the node already sent 16 bits or fewer; else cut to the most exact the rules allow."""
    if not is_public(channel):
        return lat, lon
    top = max(10, min(16, int(cfg("public_max_bits") or 16)))
    if bits is not None and bits <= top:
        return lat, lon
    STATE["public_coarsened"] += 1
    return coarsen(lat, lon, top)


def inside(lat, lon):
    try:
        return float(cfg("south")) <= lat <= float(cfg("north")) and float(cfg("west")) <= lon <= float(cfg("east"))
    except (TypeError, ValueError):
        return True


def _store(c, node, upd, gw, snr, rssi, telemetry):
    """Write what one packet said about a node and which gateway heard it. Returns "ignored" when it adds or places nothing, else None."""
    known = c.execute("SELECT 1 FROM nodes WHERE node_id=%s", (node,)).fetchone()
    if not known and (not upd or telemetry):
        return "ignored"   # a node is only added by something that says who or where it is
    sets = []
    vals = {"n": node}
    for k, v in upd.items():
        if k == "last_report":
            sets.append("last_report=now()")
        else:
            sets.append(f"{k}=%({k})s")
            vals[k] = v
    if known:
        c.execute("UPDATE nodes SET " + ", ".join(sets + ["last_seen=now()", "packets=packets+1"]) + " WHERE node_id=%(n)s", vals)
    else:
        cols = ["node_id"] + [k for k in upd if k != "last_report"]
        c.execute("INSERT INTO nodes (" + ", ".join(cols + (["last_report"] if "last_report" in upd else [])) + ") VALUES (" + ", ".join([f"%(n)s"] + [f"%({k})s" for k in cols[1:]] + (["now()"] if "last_report" in upd else [])) + ")", vals)
    if gw and gw.lstrip("!").lower() != ("%08x" % node):
        c.execute("INSERT INTO heard (node_id, gateway, snr, rssi, best_snr, ts) VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT (node_id, gateway) DO UPDATE SET snr=EXCLUDED.snr, rssi=EXCLUDED.rssi, "
                  "best_snr=GREATEST(COALESCE(heard.best_snr, -99), COALESCE(EXCLUDED.snr, -99)), ts=now(), count=heard.count+1", (node, gw, snr, rssi, snr))
    return None


def handle_json(topic, payload):
    """A gateway's decoded JSON message (topic .../2/json/<channel>/<gateway id>): nodeinfo, position and telemetry, which carry no secret. Same rules and the same tables as the protobuf door."""
    STATE["packets"] += 1
    STATE["last"] = time.time()
    try:
        m = json.loads(payload)
        node = int(m.get("from"))
    except (ValueError, TypeError):
        return "ignored"
    if not isinstance(m, dict) or not (0 < node < 2 ** 32):
        return "ignored"
    p = m.get("payload") if isinstance(m.get("payload"), dict) else {}
    typ = str(m.get("type") or "")
    chan = topic.rsplit("/", 2)[-2] if topic.count("/") >= 2 else ""
    if not port_allowed(chan, {"position": 3, "nodeinfo": 4, "telemetry": 67, "neighborinfo": 71}.get(typ, 0)):
        STATE["public_dropped"] += 1
        return "ignored"
    upd = {}
    if typ == "position":
        la, lo = p.get("latitude_i"), p.get("longitude_i")
        if not isinstance(la, int) or not isinstance(lo, int) or (la == 0 and lo == 0) or abs(la) > 900000000 or abs(lo) > 1800000000:
            return "ignored"
        lat, lon = la / 1e7, lo / 1e7
        lat, lon = public_position(chan, lat, lon, p.get("precision_bits") if isinstance(p.get("precision_bits"), int) else None)
        if not inside(lat, lon):
            STATE["outside"] += 1
            return "outside"
        upd = {"lat": lat, "lon": lon}
        if isinstance(p.get("altitude"), int):
            upd["alt"] = p["altitude"]
        if isinstance(p.get("precision_bits"), int):
            upd["precision_bits"] = p["precision_bits"]
    elif typ == "nodeinfo":
        if p.get("longname"):
            upd["long_name"] = str(p["longname"])[:60]
        if p.get("shortname"):
            upd["short_name"] = str(p["shortname"])[:12]
        if isinstance(p.get("hardware"), int):
            upd["hw_model"] = p["hardware"]
        if isinstance(p.get("role"), int):
            upd["role"] = p["role"]
    elif typ == "telemetry":
        for k_in, k_out in (("battery_level", "battery"), ("voltage", "voltage"), ("channel_utilization", "channel_util"), ("air_util_tx", "air_util"), ("uptime_seconds", "uptime")):
            if isinstance(p.get(k_in), (int, float)):
                upd[k_out] = p[k_in]
    elif typ == "neighborinfo":
        nbs = []
        for x in (p.get("neighbors") or []):
            if isinstance(x, dict) and isinstance(x.get("node_id"), int):
                nbs.append((x["node_id"], float(x["snr"]) if isinstance(x.get("snr"), (int, float)) else None))
        STATE["decoded"] += 1
        with db() as c:
            if c.execute("SELECT 1 FROM hidden WHERE node_key=%s", ("!%08x" % node,)).fetchone():
                return "hidden"
            n = store_links(c, node, nbs, str(m.get("sender") or topic.rsplit("/", 1)[-1] or ""))
        if n:
            STATE["stored"] += 1
            STATE["links"] += n
        return "stored" if n else "ignored"
    else:
        return "ignored"
    STATE["decoded"] += 1
    gw = str(m.get("sender") or topic.rsplit("/", 1)[-1] or "")
    snr = float(m["snr"]) if isinstance(m.get("snr"), (int, float)) else None
    rssi = int(m["rssi"]) if isinstance(m.get("rssi"), (int, float)) else None
    with db() as c:
        if c.execute("SELECT 1 FROM hidden WHERE node_key=%s", ("!%08x" % node,)).fetchone():
            return "hidden"
        r = _store(c, node, upd, gw, snr, rssi, typ == "telemetry")
    if r:
        return r
    STATE["stored"] += 1
    return "stored"


def handle(raw):
    """One envelope from the broker (or the test door): what is kept is only what was sent decoded; returns what was done."""
    STATE["packets"] += 1
    STATE["last"] = time.time()
    e = decode_envelope(raw)
    if e is None:
        return "ignored"
    if e["encrypted"] or e["portnum"] is None:
        STATE["encrypted"] += 1
        return "encrypted"
    if not port_allowed(e["channel"], e["portnum"]):
        STATE["public_dropped"] += 1
        return "ignored"
    STATE["decoded"] += 1
    node = e["node"]
    with db() as c:
        if c.execute("SELECT 1 FROM hidden WHERE node_key=%s", ("!%08x" % node,)).fetchone():
            return "hidden"
        upd = {}
        if e["portnum"] == PORT_MAP_REPORT:
            r = decode_map_report(e["payload"])
            if r["lat"] is not None and (abs(r["lat"]) > 90 or abs(r["lon"]) > 180):
                r["lat"] = r["lon"] = None
            if r["lat"] is not None and not inside(r["lat"], r["lon"]):
                STATE["outside"] += 1
                return "outside"
            upd = {k: v for k, v in r.items() if v is not None}
            upd["last_report"] = "now"
        elif e["portnum"] == PORT_POSITION:
            p = decode_position(e["payload"])
            if p["lat"] is None or p["lon"] is None or (p["lat"] == 0 and p["lon"] == 0):
                return "ignored"
            p["lat"], p["lon"] = public_position(e["channel"], p["lat"], p["lon"], p.get("precision_bits"))
            if not inside(p["lat"], p["lon"]):
                STATE["outside"] += 1
                return "outside"
            upd = {k: v for k, v in p.items() if v is not None and k != "precision_bits"}
        elif e["portnum"] == PORT_NODEINFO:
            upd = {k: v for k, v in decode_nodeinfo(e["payload"]).items() if v not in (None, "")}
        elif e["portnum"] == PORT_TELEMETRY:
            upd = {k: v for k, v in decode_telemetry(e["payload"]).items() if v is not None}
        elif e["portnum"] == PORT_NEIGHBORINFO:
            ni = decode_neighborinfo(e["payload"])
            reporter = ni["node"] or node
            if reporter != node:   # a report is only believed from the node itself
                return "ignored"
            n = store_links(c, reporter, ni["neighbors"], e["gateway"])
            if n:
                STATE["stored"] += 1
                STATE["links"] += n
            return "stored" if n else "ignored"
        else:
            upd = {}
        r = _store(c, node, upd, e["gateway"], e["snr"], e["rssi"], e["portnum"] == PORT_TELEMETRY)
        if r:
            return r
    STATE["stored"] += 1
    return "stored"



# ------------------------------------------------------------------ MeshCore (adverts heard by observers)

MC_KINDS = {1: "Chat node", 2: "Repeater", 3: "Room server", 4: "Sensor"}
PT_ADVERT = 0x04


def parse_mc_packet(raw):
    """A MeshCore packet's bytes -> (payload type, hop count, payload bytes) or None. [header][4 transport bytes if route type 0 or 3][path length][path][payload]; header 0bVVPPPPRR."""
    if len(raw) < 2:
        return None
    h = raw[0]
    route, ptype = h & 3, (h >> 2) & 0x0F
    i = 1
    if route in (0, 3):
        i += 4
    if i >= len(raw):
        return None
    pl = raw[i]; i += 1
    hops, size = pl & 0x3F, ((pl >> 6) & 3) + 1
    if size > 3:
        return None
    i += hops * size
    if i > len(raw):
        return None
    return ptype, hops, raw[i:]


def parse_mc_advert(p):
    """An ADVERT payload: public key (32), time (4, little endian), signature (64), then appdata: flags (kind in the low four bits, 0x10 position, 0x20 and 0x40 two bytes each, 0x80 name), latitude and longitude
    (signed 32 bit, little endian, millionths of a degree) and the name in the bytes that remain. Returns a dict or None; the signature is checked when the library is there."""
    if len(p) < 101:
        return None
    pub, ts, sig, app = p[:32], p[32:36], p[36:100], p[100:]
    flags = app[0]
    i, lat, lon, name = 1, None, None, ""
    if flags & 0x10:
        if len(app) < i + 8:
            return None
        lat, lon = struct.unpack("<ii", app[i:i + 8]); i += 8
        lat, lon = lat / 1e6, lon / 1e6
        if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
            lat = lon = None
    if flags & 0x20:
        i += 2
    if flags & 0x40:
        i += 2
    if flags & 0x80:
        name = app[i:].decode("utf-8", "ignore").replace("\x00", "").strip()[:40]
    ok = None
    if Ed25519PublicKey is not None:
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(sig, pub + ts + app)
            ok = True
        except (InvalidSignature, ValueError):
            ok = False
    return {"pubkey": pub.hex(), "time": struct.unpack("<I", ts)[0], "kind": flags & 0x0F, "lat": lat, "lon": lon, "name": name, "signature_ok": ok}


def handle_meshcore(topic, payload):
    """One observer message (meshcoretomqtt's JSON): only ADVERT packets are used, and only with a good signature."""
    STATE["packets"] += 1; STATE["mc_packets"] += 1; STATE["last"] = time.time()
    try:
        d = json.loads(payload)
        raw = bytes.fromhex(str(d.get("raw") or ""))
    except (ValueError, TypeError):
        return "ignored"
    pk = parse_mc_packet(raw)
    if pk is None or pk[0] != PT_ADVERT:
        STATE["mc_dropped"] += 1
        return "dropped"   # everything but an advert is encrypted chat, never read
    a = parse_mc_advert(pk[2])
    if a is None:
        STATE["mc_dropped"] += 1
        return "ignored"
    if a["signature_ok"] is False:
        STATE["mc_badsig"] += 1
        return "badsig"
    now = time.time()
    if a["time"] and (a["time"] < now - 14 * 86400 or a["time"] > now + 86400):
        return "stale"   # a replayed old advert, or one from a clock that is wrong
    if a["lat"] is not None and not inside(a["lat"], a["lon"]):
        STATE["outside"] += 1
        return "outside"
    STATE["mc_adverts"] += 1
    key = "mc:" + a["pubkey"]
    obs = re.sub(r"[^0-9A-Fa-f]", "", str(d.get("origin_id") or "")).lower()[:64]
    obs_name = str(d.get("origin") or "")[:40]
    with db() as c:
        if c.execute("SELECT 1 FROM hidden WHERE node_key=%s", (key,)).fetchone():
            return "hidden"
        c.execute("INSERT INTO mc_nodes (pubkey, name, kind, lat, lon, advert_time, last_seen, packets) VALUES (%s,%s,%s,%s,%s,to_timestamp(%s),now(),1) "
                  "ON CONFLICT (pubkey) DO UPDATE SET name=CASE WHEN EXCLUDED.name <> '' THEN EXCLUDED.name ELSE mc_nodes.name END, kind=EXCLUDED.kind, lat=COALESCE(EXCLUDED.lat, mc_nodes.lat), "
                  "lon=COALESCE(EXCLUDED.lon, mc_nodes.lon), advert_time=EXCLUDED.advert_time, last_seen=now(), packets=mc_nodes.packets+1",
                  (a["pubkey"], a["name"], a["kind"], a["lat"], a["lon"], a["time"] or now))
        if obs and obs != a["pubkey"]:
            try:
                snr, rssi = float(d.get("SNR")), int(float(d.get("RSSI")))
            except (TypeError, ValueError):
                snr = rssi = None
            c.execute("INSERT INTO mc_heard (pubkey, observer, observer_name, snr, rssi, best_snr, hops, ts) VALUES (%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT (pubkey, observer) DO UPDATE SET "
                      "snr=EXCLUDED.snr, rssi=EXCLUDED.rssi, best_snr=GREATEST(COALESCE(mc_heard.best_snr, -99), COALESCE(EXCLUDED.snr, -99)), hops=EXCLUDED.hops, observer_name=EXCLUDED.observer_name, ts=now(), count=mc_heard.count+1",
                      (a["pubkey"], obs, obs_name, snr, rssi, snr, pk[1]))
    STATE["stored"] += 1
    return "stored"


# ------------------------------------------------------------------ the broker's logins (Mosquitto dynamic security)

ADMIN_USER, ADMIN_PASS_FILE = "mesh-admin", os.environ.get("BROKER_ADMIN_PASS_FILE", "/broker/admin.pass")
SYSTEM_USERS = {ADMIN_USER, "mesh-reader"}
CONTROL, CONTROL_REPLY = "$CONTROL/dynamic-security/v1", "$CONTROL/dynamic-security/v1/response"


def admin_password():
    try:
        return open(ADMIN_PASS_FILE).read().strip()
    except OSError:
        return ""


def reader_password():
    """The service's own read-only login on the broker, worked out from the control password so there is nothing more to keep."""
    a = admin_password()
    return hashlib.sha256(("mesh-reader\0" + a).encode()).hexdigest()[:32] if a else ""


def control(commands, timeout=10):
    """Send commands to the broker's dynamic security plugin as the control user; returns the list of responses (each {command, error?, data?}) or raises RuntimeError."""
    import paho.mqtt.client as mqtt
    pw = admin_password()
    if not pw:
        raise RuntimeError("the broker's control password is not readable (the broker volume is not mounted)")
    done, got = threading.Event(), {}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2) if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client()
    client.username_pw_set(ADMIN_USER, pw)

    def on_connect(c, u, flags, rc, *a):
        if getattr(rc, "value", rc) != 0:
            got["error"] = f"the broker refused the control login ({rc})"; done.set(); return
        c.subscribe(CONTROL_REPLY)
        c.publish(CONTROL, json.dumps({"commands": commands}))

    def on_message(c, u, msg):
        try:
            got["responses"] = json.loads(msg.payload).get("responses", [])
        except ValueError:
            got["error"] = "an unreadable answer from the broker"
        done.set()
    client.on_connect, client.on_message = on_connect, on_message
    try:
        client.connect(cfg("host"), int(cfg("port")), 20)
    except Exception as e:
        raise RuntimeError("cannot reach the broker (" + type(e).__name__ + ")")
    client.loop_start()
    try:
        if not done.wait(timeout):
            raise RuntimeError("the broker did not answer")
    finally:
        client.loop_stop(); client.disconnect()
    if got.get("error"):
        raise RuntimeError(got["error"])
    return got.get("responses", [])


def _ok(resp):
    """A response counts as done unless it is an error other than 'already exists' / 'not found' (an idempotent command)."""
    e = (resp.get("error") or "").lower()
    if resp.get("command") in ("addClientRole", "addRoleACL") and "internal error" in e:
        return True   # the plugin's answer for a role or rule the client or role already has
    return not e or "already exists" in e or "not found" in e


_ROOT = [None]   # the topic root the publisher role was last given, so a changed root removes the old rule


def ensure_base():
    """The two roles (publisher: may write the mesh topics, may subscribe so the phone app is content, and is never sent anything; reader: may read them) and the service's own read-only client."""
    rp = reader_password()
    r = control([
        {"command": "createRole", "rolename": "publisher"},
        {"command": "removeRoleACL", "rolename": "publisher", "acltype": "publishClientSend", "topic": "msh/#"},   # an older broker allowed all of msh/#
        *([{"command": "removeRoleACL", "rolename": "publisher", "acltype": "publishClientSend", "topic": _ROOT[0] + "/#"}] if _ROOT[0] and _ROOT[0] != (cfg("publish_root").strip("/") or "msh") else []),
        {"command": "addRoleACL", "rolename": "publisher", "acltype": "publishClientSend", "topic": (cfg("publish_root").strip("/") or "msh") + "/#", "allow": True},
        {"command": "addRoleACL", "rolename": "publisher", "acltype": "publishClientSend", "topic": "meshcore/#", "allow": True},
        # the Meshtastic app and firmware also subscribe (to the channel and PKI topics) and show a red error when the broker refuses it: accept the subscription, but a member's login is never allowed to RECEIVE
        # anything (the broker lets a client receive unless a rule says no, so the role denies every topic), so it still reads nothing of what other members' nodes send
        {"command": "addRoleACL", "rolename": "publisher", "acltype": "subscribePattern", "topic": "msh/#", "allow": True},
        {"command": "addRoleACL", "rolename": "publisher", "acltype": "subscribePattern", "topic": "meshcore/#", "allow": True},
        {"command": "addRoleACL", "rolename": "publisher", "acltype": "publishClientReceive", "topic": "#", "allow": False, "priority": 10},
        {"command": "createRole", "rolename": "reader"},
        {"command": "addRoleACL", "rolename": "reader", "acltype": "subscribePattern", "topic": "msh/#", "allow": True},
        {"command": "addRoleACL", "rolename": "reader", "acltype": "subscribePattern", "topic": "meshcore/#", "allow": True},
        {"command": "addRoleACL", "rolename": "reader", "acltype": "publishClientReceive", "topic": "msh/#", "allow": True},
        {"command": "addRoleACL", "rolename": "reader", "acltype": "publishClientReceive", "topic": "meshcore/#", "allow": True},
        {"command": "createClient", "username": "mesh-reader", "password": rp},
        {"command": "setClientPassword", "username": "mesh-reader", "password": rp},
        {"command": "addClientRole", "username": "mesh-reader", "rolename": "reader"},
    ])
    bad = [x for x in r if not _ok(x)]
    if bad:
        raise RuntimeError("the broker refused: " + str(bad[0].get("error")))
    _ROOT[0] = cfg("publish_root").strip("/") or "msh"


def set_user(username, password):
    """A member's login: may publish to the mesh topics, may not read anything. Safe to repeat (it also changes the password)."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,40}", username) or username in SYSTEM_USERS or not (8 <= len(password) <= 63):
        raise ValueError("a login name is 3 to 41 lowercase letters, digits, dots, dashes; a password 8 to 63 characters")
    r = control([{"command": "createClient", "username": username, "password": password}, {"command": "setClientPassword", "username": username, "password": password},
                 {"command": "addClientRole", "username": username, "rolename": "publisher"}])
    bad = [x for x in r if not _ok(x)]
    if bad:
        raise RuntimeError("the broker refused: " + str(bad[0].get("error")))


def delete_user(username):
    if username in SYSTEM_USERS:
        raise ValueError("that login belongs to the service")
    control([{"command": "deleteClient", "username": username}])


def list_users():
    r = control([{"command": "listClients"}])
    return [c for c in (r[0].get("data", {}).get("clients", []) if r else []) if c not in SYSTEM_USERS]


def sync_users(users):
    """Make the broker's member logins exactly this list [{username, password}]: new ones made, passwords set, logins nobody asked for removed."""
    ensure_base()
    want = {u["username"]: u["password"] for u in users}
    have = set(list_users())
    for name, pw in want.items():
        set_user(name, pw)
    removed = 0
    for name in have - set(want):
        delete_user(name); removed += 1
    return {"set": len(want), "removed": removed}


def base_loop():
    """Prepare the roles and the service's own login as soon as the broker answers (and again if it was rebuilt)."""
    ok_at = 0
    while True:
        try:
            if cfg("enabled") == "1" and admin_password() and time.time() - ok_at > 600:
                ensure_base(); ok_at = time.time(); STATE["broker_ready"] = True
        except Exception as e:
            STATE["broker_ready"] = False
            STATE["broker_error"] = str(e)[:100]
            ok_at = 0
        time.sleep(15 if not STATE.get("broker_ready") else 60)


# ------------------------------------------------------------------ MQTT

def reader(pfx=""):
    """Reads one broker: ours (pfx empty) or, when switched on, the public Meshtastic one (pfx up_), the same feed the community maps are made from."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        STATE[pfx + "error"] = "the MQTT library is missing"
        return
    cur = [None]

    def on_connect(client, userdata, flags, rc, *a):
        ok = (getattr(rc, "value", rc) == 0)
        STATE[pfx + "connected"] = ok
        STATE[pfx + "error"] = "" if ok else f"the broker refused the connection ({rc})"
        if ok:
            for t in [x.strip() for x in cfg(pfx + "topic").split(",") if x.strip()]:
                client.subscribe(t)

    def on_disconnect(client, userdata, *a):
        STATE[pfx + "connected"] = False
        STATE[pfx + "reconnects"] += 1

    def on_message(client, userdata, msg):
        try:
            if msg.topic.startswith("meshcore/"):
                handle_meshcore(msg.topic, msg.payload)
            elif "/2/json/" in msg.topic:
                handle_json(msg.topic, msg.payload)
            else:
                handle(msg.payload)
        except Exception as ex:
            STATE[pfx + "error"] = "handling a packet failed: " + str(ex)[:80]

    while True:
        sig = (cfg(pfx + "host"), cfg(pfx + "port"), cfg(pfx + "user"), cfg(pfx + "password"), cfg(pfx + "topic"), cfg("enabled"), cfg(pfx + "enabled") if pfx else "1")
        if cfg("enabled") != "1" or (pfx and cfg("up_enabled") != "1"):
            if cur[0]:
                try:
                    cur[0][0].loop_stop(); cur[0][0].disconnect()
                except Exception:
                    pass
                cur[0] = None
                STATE[pfx + "connected"] = False
            time.sleep(15)
            continue
        if cur[0] is None or cur[0][1] != sig:
            if cur[0]:
                try:
                    cur[0][0].loop_stop(); cur[0][0].disconnect()
                except Exception:
                    pass
            try:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2) if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client()
                client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
                if cfg(pfx + "user"):
                    client.username_pw_set(cfg(pfx + "user"), cfg(pfx + "password"))
                elif not pfx and admin_password():   # our own broker: the service reads with its own read-only login
                    client.username_pw_set("mesh-reader", reader_password())
                client.reconnect_delay_set(2, 30)
                client.connect_async(cfg(pfx + "host"), int(cfg(pfx + "port")), 60)
                client.loop_start()
                cur[0] = (client, sig)
            except Exception as ex:
                STATE[pfx + "error"] = "cannot start the MQTT client: " + str(ex)[:80]
        time.sleep(10)


def prune_links():
    with db() as c:
        c.execute("DELETE FROM links WHERE last_seen < now() - make_interval(days => %s) OR a NOT IN (SELECT node_id FROM nodes)", (int(cfg("links_keep_days")),))


def pruner():
    while True:
        time.sleep(3600)
        try:
            prune_links()
            with db() as c:
                c.execute("DELETE FROM mc_nodes WHERE last_seen < now() - make_interval(days => %s)", (int(cfg("keep_days")),))
                c.execute("DELETE FROM mc_heard WHERE ts < now() - make_interval(days => %s) OR pubkey NOT IN (SELECT pubkey FROM mc_nodes)", (int(cfg("heard_keep_days")),))
                c.execute("DELETE FROM nodes WHERE last_seen < now() - make_interval(days => %s)", (int(cfg("keep_days")),))
                c.execute("DELETE FROM heard WHERE ts < now() - make_interval(days => %s) OR node_id NOT IN (SELECT node_id FROM nodes)", (int(cfg("heard_keep_days")),))
        except Exception as e:
            print("prune failed:", str(e)[:100], flush=True)


def iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def mc_row(r):
    out = {k: iso(v) for k, v in r.items()}
    out.update({"network": "meshcore", "id": "mc:" + r["pubkey"], "slug": "mc-" + r["pubkey"], "long_name": r["name"], "short_name": r["pubkey"][:8].upper(), "role": r["kind"], "role_name": MC_KINDS.get(r["kind"], ""),
                "hardware": "", "firmware": "", "region_name": "", "preset_name": "", "battery": None, "precision_bits": None})
    return out


def node_row(r):
    out = {k: iso(v) for k, v in r.items()}
    out["network"] = "meshtastic"
    out["slug"] = "%08x" % r["node_id"]
    out["id"] = "!%08x" % r["node_id"]
    out["role_name"] = ROLES.get(r.get("role"), "")
    out["hardware"] = HARDWARE.get(r.get("hw_model"), ("hardware #%s" % r["hw_model"]) if r.get("hw_model") else "")
    out["region_name"] = REGIONS.get(r.get("region"), "")
    out["preset_name"] = PRESETS.get(r.get("modem_preset"), "")
    return out


def node_key(text):
    """The key a node is hidden by: !a1b2c3d4 for a Meshtastic node, mc:<64 hex characters> for a MeshCore node; None when the text is neither."""
    t = text.strip()
    m = re.fullmatch(r"(?:mc[:-])([0-9a-fA-F]{64})", t)
    if m:
        return "mc:" + m.group(1).lower()
    m = re.fullmatch(r"!?([0-9a-fA-F]{8})", t)
    return "!" + m.group(1).lower() if m else None


def node_key(text):
    """The key a node is hidden by: !a1b2c3d4 for a Meshtastic node, mc:<64 hex characters> for a MeshCore node; None when the text is neither."""
    t = text.strip()
    m = re.fullmatch(r"(?:mc[:-])([0-9a-fA-F]{64})", t)
    if m:
        return "mc:" + m.group(1).lower()
    m = re.fullmatch(r"!?([0-9a-fA-F]{8})", t)
    return "!" + m.group(1).lower() if m else None


def parse_id(text):
    t = text.strip().lstrip("!")
    if re.fullmatch(r"[0-9a-fA-F]{8}", t):
        return int(t, 16)
    return int(t) if t.isdigit() else None


def bearing_km(la0, lo0, la, lo):
    """(kilometers, bearing in degrees 0-360) from the first place to the second."""
    r = math.radians
    dy, dx = (la - la0) * 111.195, (lo - lo0) * 111.195 * math.cos(r((la + la0) / 2))
    return math.hypot(dx, dy), (math.degrees(math.atan2(dx, dy)) + 360) % 360


def coverage(days=14):
    """Measured coverage of every gateway or observer that has a known place: the farthest node it was heard to hear in each ten degree sector (what was heard, not a prediction)."""
    out = {}
    with db() as c:
        rows = c.execute("SELECT g.node_id AS gid, g.long_name AS gname, g.short_name AS gshort, g.lat AS glat, g.lon AS glon, n.lat AS nlat, n.lon AS nlon, h.snr, 'meshtastic' AS net "
                         "FROM heard h JOIN nodes g ON h.gateway = '!' || lpad(to_hex(g.node_id), 8, '0') JOIN nodes n ON n.node_id = h.node_id "
                         "WHERE g.lat IS NOT NULL AND n.lat IS NOT NULL AND h.node_id <> g.node_id AND h.ts > now() - make_interval(days => %s)", (days,)).fetchall()
        rows += c.execute("SELECT o.pubkey AS gid, o.name AS gname, '' AS gshort, o.lat AS glat, o.lon AS glon, n.lat AS nlat, n.lon AS nlon, h.snr, 'meshcore' AS net "
                          "FROM mc_heard h JOIN mc_nodes o ON h.observer = 'mc:' || o.pubkey JOIN mc_nodes n ON n.pubkey = h.pubkey "
                          "WHERE o.lat IS NOT NULL AND n.lat IS NOT NULL AND h.pubkey <> o.pubkey AND h.ts > now() - make_interval(days => %s)", (days,)).fetchall()
    for r in rows:
        km, brg = bearing_km(r["glat"], r["glon"], r["nlat"], r["nlon"])
        if km < 0.05:
            continue
        key = (r["net"], str(r["gid"]))
        g = out.setdefault(key, {"id": ("mc-" + str(r["gid"])) if r["net"] == "meshcore" else "%08x" % r["gid"], "network": r["net"], "name": r["gname"] or r["gshort"] or "", "lat": r["glat"], "lon": r["glon"],
                                 "sectors": [0.0] * 36, "heard": 0, "reach_km": 0.0})
        g["heard"] += 1
        s = int(brg // 10) % 36
        g["sectors"][s] = round(max(g["sectors"][s], km), 1)
        g["reach_km"] = round(max(g["reach_km"], km), 1)
    return sorted(out.values(), key=lambda x: -x["reach_km"])


def dbsize():
    """The size of this service's whole database in bytes (shown on the portal's Admin > Usage)."""
    try:
        with db() as c:
            return int(c.execute("SELECT pg_database_size(current_database()) AS s").fetchone()["s"])
    except Exception:
        return None


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        p = u.path.rstrip("/")
        try:
            if p in ("", "/health"):
                return self.send({"ok": True, "version": VERSION, "connected": STATE["connected"]})
            if p == "/stats":
                with db() as c:
                    n = c.execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE last_seen > now() - interval '1 hour') AS hour, COUNT(*) FILTER (WHERE last_seen > now() - interval '24 hours') AS day, "
                                  "COUNT(*) FILTER (WHERE lat IS NOT NULL) AS placed, COUNT(*) FILTER (WHERE role IN (2,3,4,11)) AS routers FROM nodes").fetchone()
                    m = c.execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE last_seen > now() - interval '1 hour') AS hour, COUNT(*) FILTER (WHERE last_seen > now() - interval '24 hours') AS day, "
                                  "COUNT(*) FILTER (WHERE lat IS NOT NULL) AS placed, COUNT(*) FILTER (WHERE kind IN (2,3)) AS routers FROM mc_nodes").fetchone()
                    gws = c.execute("SELECT gateway, COUNT(*) AS nodes, MAX(ts) AS last FROM heard GROUP BY gateway ORDER BY nodes DESC LIMIT 50").fetchall()
                    obs = c.execute("SELECT observer, max(observer_name) AS name, COUNT(*) AS nodes, MAX(ts) AS last FROM mc_heard GROUP BY observer ORDER BY nodes DESC LIMIT 50").fetchall()
                    roles = c.execute("SELECT role, COUNT(*) AS n FROM nodes WHERE last_seen > now() - interval '7 days' GROUP BY role ORDER BY n DESC").fetchall()
                    mroles = c.execute("SELECT kind, COUNT(*) AS n FROM mc_nodes WHERE last_seen > now() - interval '7 days' GROUP BY kind ORDER BY n DESC").fetchall()
                    hw = c.execute("SELECT hw_model, COUNT(*) AS n FROM nodes WHERE last_seen > now() - interval '7 days' GROUP BY hw_model ORDER BY n DESC LIMIT 12").fetchall()
                    hid = c.execute("SELECT COUNT(*) AS n FROM hidden").fetchone()["n"]
                    lk = c.execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE last_seen > now() - interval '24 hours') AS day, COUNT(DISTINCT a) AS reporters FROM links").fetchone()
                up = max(1, time.time() - STATE["since"])
                return self.send({"db_bytes": dbsize(), "version": VERSION, "nodes": n["n"] + m["n"], "meshtastic": n["n"], "meshcore": m["n"], "heard_hour": n["hour"] + m["hour"], "heard_day": n["day"] + m["day"], "placed": n["placed"] + m["placed"],
                                  "routers": n["routers"] + m["routers"], "hidden": hid, "links": lk["n"], "links_day": lk["day"], "link_reporters": lk["reporters"],
                                  "gateways": [{"gateway": r["gateway"], "network": "meshtastic", "nodes": r["nodes"], "last": iso(r["last"])} for r in gws] + [{"gateway": "mc:" + r["observer"], "network": "meshcore", "name": r["name"], "nodes": r["nodes"], "last": iso(r["last"])} for r in obs],
                                  "roles": [{"role": ROLES.get(r["role"], "unknown"), "n": r["n"]} for r in roles] + [{"role": MC_KINDS.get(r["kind"], "unknown") + " (MeshCore)", "n": r["n"]} for r in mroles],
                                  "hardware": [{"hardware": HARDWARE.get(r["hw_model"], "other" if r["hw_model"] else "unknown"), "n": r["n"]} for r in hw],
                                  "feed": dict(STATE, per_min=round(STATE["packets"] / (up / 60), 1), last=STATE["last"] and time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(STATE["last"])), signatures_checked=Ed25519PublicKey is not None, tls=os.path.exists("/broker/certs/fullchain.pem") and os.path.exists("/broker/certs/privkey.pem"), broker_ready=bool(STATE.get("broker_ready")), broker_error=STATE.get("broker_error", "")),
                                  "config": {k: (cfg(k) if k != "password" else ("set" if cfg(k) else "")) for k in DEFAULTS}})
            if p == "/nodes":
                where, args = ["true"], {"l": min(int(q.get("limit") or 3000), 10000), "h": min(int(q.get("hours") or 168), 24 * 90)}
                where.append("last_seen > now() - make_interval(hours => %(h)s)")
                mwhere = list(where)
                if q.get("q"):
                    where.append("(long_name ILIKE %(q)s OR short_name ILIKE %(q)s OR to_char(node_id, 'FM0000000000') ILIKE %(q)s OR lower(lpad(to_hex(node_id), 8, '0')) LIKE lower(%(qh)s))")
                    mwhere.append("(name ILIKE %(q)s OR pubkey ILIKE %(qh)s)")
                    args["q"] = "%" + q["q"].strip() + "%"
                    args["qh"] = "%" + q["q"].strip().lstrip("!") + "%"
                if q.get("role") not in (None, ""):
                    where.append("role = %(role)s")
                    mwhere.append("kind = %(role)s")
                    args["role"] = int(q["role"])
                if q.get("placed") == "1":
                    where.append("lat IS NOT NULL")
                    mwhere.append("lat IS NOT NULL")
                net = q.get("network", "")
                rows = []
                with db() as c:
                    if net in ("", "meshtastic"):
                        rows += [node_row(r) for r in c.execute("SELECT * FROM nodes WHERE " + " AND ".join(where) + " ORDER BY last_seen DESC LIMIT %(l)s", args).fetchall()]
                    if net in ("", "meshcore"):
                        rows += [mc_row(r) for r in c.execute("SELECT * FROM mc_nodes WHERE " + " AND ".join(mwhere) + " ORDER BY last_seen DESC LIMIT %(l)s", args).fetchall()]
                rows.sort(key=lambda r: r["last_seen"], reverse=True)
                return self.send({"nodes": rows[: args["l"]]})
            m = re.match(r"^/nodes/([^/]+)$", p)
            if m:
                key = urllib.parse.unquote(m.group(1)).strip()
                mc = re.fullmatch(r"(?:mc[:-])?([0-9a-fA-F]{64})", key)
                with db() as c:
                    if mc:
                        pub = mc.group(1).lower()
                        r = c.execute("SELECT * FROM mc_nodes WHERE pubkey=%s", (pub,)).fetchone()
                        if not r:
                            return self.send({"error": "not found"}, 404)
                        hb = c.execute("SELECT observer, observer_name, snr, rssi, best_snr, hops, ts, count FROM mc_heard WHERE pubkey=%s ORDER BY ts DESC LIMIT 30", (pub,)).fetchall()
                        known = {x["pubkey"] for x in c.execute("SELECT pubkey FROM mc_nodes WHERE pubkey = ANY(%s)", ([h["observer"] for h in hb],)).fetchall()}
                        out = [{"gateway": "mc:" + h["observer"], "slug": ("mc-" + h["observer"]) if h["observer"] in known else "", "name": h["observer_name"], "snr": h["snr"], "rssi": h["rssi"], "best_snr": h["best_snr"],
                                "hops": h["hops"], "ts": iso(h["ts"]), "count": h["count"]} for h in hb]
                        gw_of = c.execute("SELECT count(*) AS n FROM mc_heard WHERE observer = %s", (pub,)).fetchone()["n"]
                        return self.send({"node": mc_row(r), "heard_by": out, "gateway_for": gw_of})
                    nid = parse_id(key)
                    if nid is None:
                        return self.send({"error": "not a node"}, 400)
                    r = c.execute("SELECT * FROM nodes WHERE node_id=%s", (nid,)).fetchone()
                    if not r:
                        return self.send({"error": "not found"}, 404)
                    hb = c.execute("SELECT gateway, snr, rssi, best_snr, ts, count FROM heard WHERE node_id=%s ORDER BY ts DESC LIMIT 30", (nid,)).fetchall()
                    gw_of = c.execute("SELECT count(*) AS n FROM heard WHERE lower(gateway) = %s", ("!%08x" % nid,)).fetchone()["n"]
                    nb = c.execute("SELECT l.a, l.b, l.snr, l.last_seen, o.long_name, o.short_name FROM links l JOIN nodes o ON o.node_id = CASE WHEN l.a = %s THEN l.b ELSE l.a END WHERE l.a = %s OR l.b = %s ORDER BY l.last_seen DESC LIMIT 100", (nid, nid, nid)).fetchall()
                    nb += [dict(r2, long_name="", short_name="") for r2 in c.execute("SELECT l.a, l.b, l.snr, l.last_seen FROM links l WHERE l.a = %s AND NOT EXISTS (SELECT 1 FROM nodes o WHERE o.node_id = l.b)", (nid,)).fetchall()]
                neighbors = [{"id": "!%08x" % (x["b"] if x["a"] == nid else x["a"]), "slug": "%08x" % (x["b"] if x["a"] == nid else x["a"]), "name": x["long_name"] or x["short_name"] or "", "snr": x["snr"],
                              "direction": "hears" if x["a"] == nid else "heard_by", "last_seen": iso(x["last_seen"])} for x in nb]
                return self.send({"node": node_row(r), "heard_by": [dict({k: iso(v) for k, v in h.items()}, slug=h["gateway"].lstrip("!")) for h in hb], "gateway_for": gw_of, "neighbors": neighbors})
            if p == "/links":
                days_h = min(int(q.get("hours") or 336), 24 * 365)
                with db() as c:
                    rows = c.execute("SELECT a, b, snr, last_seen, reported_by, count FROM links WHERE last_seen > now() - make_interval(hours => %s) ORDER BY last_seen DESC LIMIT 20000", (days_h,)).fetchall()
                return self.send({"links": [{"a": "!%08x" % r["a"], "b": "!%08x" % r["b"], "snr": r["snr"], "last_seen": iso(r["last_seen"]), "reported_by": r["reported_by"], "count": r["count"]} for r in rows]})
            if p == "/coverage":
                return self.send({"gateways": coverage(int(q.get("days") or 14))})
            if p == "/credentials":
                try:
                    return self.send({"ok": True, "users": list_users()})
                except RuntimeError as e:
                    return self.send({"ok": False, "error": str(e)}, 502)
            if p == "/hidden":
                with db() as c:
                    rows = c.execute("SELECT node_key, reason, added FROM hidden ORDER BY node_key").fetchall()
                return self.send({"hidden": [{"id": r["node_key"], "reason": r["reason"], "added": iso(r["added"])} for r in rows]})
            return self.send({"error": "not found"}, 404)
        except Exception as e:
            return self.send({"error": str(e)[:200]}, 500)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path.rstrip("/")
        b = self.body_json()
        try:
            if p == "/config":
                for k, v in (b or {}).items():
                    set_cfg(k, v)
                if "publish_root" in (b or {}) and _ROOT[0] and (cfg("publish_root").strip("/") or "msh") != _ROOT[0]:
                    try:
                        ensure_base()
                    except Exception as ex:
                        print("could not apply the new publish root:", str(ex)[:120], flush=True)
                return self.send({"ok": True})
            if p == "/ingest_json":   # a gateway's decoded JSON message, for tests: {topic, payload}
                return self.send({"ok": True, "result": handle_json(str(b.get("topic") or ""), str(b.get("payload") or ""))})
            if p == "/prune":
                prune_links()
                return self.send({"ok": True})
            if p == "/ingest":   # the same door the broker uses, for tests and for a one-off import: a base64 ServiceEnvelope
                return self.send({"ok": True, "result": handle(base64.b64decode(b.get("envelope_b64") or ""))})
            if p == "/credentials/sync":
                users = [{"username": str(u.get("username") or ""), "password": str(u.get("password") or "")} for u in (b.get("users") or []) if isinstance(u, dict)]
                try:
                    return self.send(dict(ok=True, **sync_users(users)))
                except (RuntimeError, ValueError) as e:
                    return self.send({"ok": False, "error": str(e)}, 502)
            if p == "/credentials/set":
                try:
                    ensure_base()
                    set_user(str(b.get("username") or ""), str(b.get("password") or ""))
                    return self.send({"ok": True})
                except ValueError as e:
                    return self.send({"ok": False, "error": str(e)}, 400)
                except RuntimeError as e:
                    return self.send({"ok": False, "error": str(e)}, 502)
            if p == "/credentials/delete":
                try:
                    delete_user(str(b.get("username") or ""))
                    return self.send({"ok": True})
                except ValueError as e:
                    return self.send({"ok": False, "error": str(e)}, 400)
                except RuntimeError as e:
                    return self.send({"ok": False, "error": str(e)}, 502)
            if p == "/hide":
                key = node_key(str(b.get("id") or ""))
                if key is None:
                    return self.send({"error": "a node ID looks like !a1b2c3d4, or mc: and the 64 character public key"}, 400)
                with db() as c:
                    c.execute("INSERT INTO hidden (node_key, reason) VALUES (%s,%s) ON CONFLICT (node_key) DO UPDATE SET reason=EXCLUDED.reason", (key, str(b.get("reason") or "")[:200]))
                    if key.startswith("mc:"):
                        c.execute("DELETE FROM mc_nodes WHERE pubkey=%s", (key[3:],)); c.execute("DELETE FROM mc_heard WHERE pubkey=%s", (key[3:],))
                    else:
                        nid = int(key[1:], 16)
                        c.execute("DELETE FROM nodes WHERE node_id=%s", (nid,)); c.execute("DELETE FROM heard WHERE node_id=%s", (nid,)); c.execute("DELETE FROM links WHERE a=%s OR b=%s", (nid, nid))
                return self.send({"ok": True})
            if p == "/unhide":
                key = node_key(str(b.get("id") or ""))
                with db() as c:
                    c.execute("DELETE FROM hidden WHERE node_key=%s", (key,))
                return self.send({"ok": True})
            return self.send({"error": "not found"}, 404)
        except Exception as e:
            return self.send({"error": str(e)[:200]}, 500)


def main():
    init_db()
    for fn in (reader, lambda: reader("up_"), pruner, base_loop):
        threading.Thread(target=fn, daemon=True).start()
    print(f"arksignal-mesh {VERSION} listening on {PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
