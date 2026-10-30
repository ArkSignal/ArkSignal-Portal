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

DEFAULTS = {"enabled": "1", "host": "mesh-mqtt", "port": "1883", "user": "", "password": "", "topic": "msh/#,meshcore/#", "south": "32.0", "west": "-95.5", "north": "37.2", "east": "-88.8", "keep_days": "60", "history_keep_days": "30", "history_min_seconds": "300",
            "heard_keep_days": "14", "links_keep_days": "30", "up_enabled": "1", "up_host": "mqtt.meshtastic.org", "up_port": "1883", "up_user": "meshdev", "up_password": "large4cats", "up_topic": "msh/US/AR/#",
            "public_rules": "1", "public_channels": "LongFast,LongSlow,LongModerate,MediumFast,MediumSlow,ShortFast,ShortSlow,ShortTurbo,VLongSlow", "public_portnums": "1,3,4,5,7,67,71,73",
            "public_max_bits": "16", "publish_root": "msh/US/AR", "fwd_enabled": "0", "fwd_root": "msh/US/AR", "fwd_communities": "", "links": "[]", "relay_gateway": "!a5c0ae11", "link_dedupe_seconds": "600",
            "hw_source": "https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto", "hw_refresh_hours": "24"}
STATE = {"connected": False, "packets": 0, "decoded": 0, "encrypted": 0, "stored": 0, "outside": 0, "mc_packets": 0, "mc_adverts": 0, "mc_dropped": 0, "mc_badsig": 0, "links": 0, "public_dropped": 0, "public_coarsened": 0, "since": time.time(), "last": None, "error": "", "reconnects": 0, "up_connected": False, "up_error": "", "up_reconnects": 0, "fwd_sent": 0, "fwd_dropped": 0, "fwd_error": ""}
_cfg = {}
_lock = threading.Lock()

PORT_MAP_REPORT, PORT_POSITION, PORT_NODEINFO, PORT_TELEMETRY, PORT_NEIGHBORINFO = 73, 3, 4, 67, 71
ROLES = {0: "Client", 1: "Client (muted)", 2: "Router", 3: "Router and client", 4: "Repeater", 5: "Tracker", 6: "Sensor", 7: "TAK", 8: "Client (hidden)", 9: "Lost and found", 10: "TAK tracker", 11: "Router (late)", 12: "Client (base)"}
HARDWARE = {71: "Tracker T1000-E", 89: "Thinknode M1", 99: "Seeed Wio Tracker L1", 100: "Seeed Wio Tracker L1 E-Ink", 102: "T-Deck Pro", 103: "T-LoRa Pager", 105: "Wismesh Tag", 113: "Heltec Wireless Tracker V2", 122: "T-Beam 1 Watt", 124: "T-Beam BPF", 1: "TLoRa V2", 2: "TLoRa V1", 3: "TLoRa V2.1 1.6", 4: "T-Beam", 5: "Heltec V2.0", 6: "T-Beam 0.7", 7: "T-Echo", 8: "TLoRa V1.1.3", 9: "RAK4631", 10: "Heltec V2.1", 11: "Heltec V1", 12: "T-Beam S3 Core", 13: "RAK11200", 14: "Nano G1", 15: "TLoRa V2.1 1.8", 16: "TLoRa T3 S3", 17: "Nano G1 Explorer", 18: "Nano G2 Ultra", 19: "LoRa Type", 20: "Wiphone", 22: "RAK2560", 23: "Heltec Hru 3601", 24: "Heltec Wireless Bridge", 25: "Station G1", 26: "Rak11310", 27: "Makerfabs Tracker", 28: "Makerfabs Reserved", 30: "RP2040 LoRa", 31: "Station G2", 32: "LoRa Relay V1", 34: "Ppr", 35: "Genieblocks", 36: "nRF52 Unknown", 37: "Portduino", 38: "Android Sim", 39: "Diy V1", 40: "Nrf52840 Pca10059", 41: "Dr Dev", 42: "M5Stack", 43: "Heltec V3", 44: "Heltec Wsl V3", 45: "Betafpv 2400 Tx", 46: "Betafpv 900 Nano Tx", 47: "Rpi Pico", 49: "Heltec Wireless Paper", 50: "T-Deck", 51: "T-Watch S3", 52: "Picomputer S3", 53: "Heltec HT62", 54: "EByte ESP32 S3", 55: "ESP32 S3 Pico", 56: "Chatter 2", 57: "Heltec Wireless Paper V1 0", 58: "Heltec Wireless Tracker V1 0", 59: "Unphone", 60: "Td Lorac", 61: "Cdebyte Eora S3", 62: "Twc Mesh V4", 63: "nRF52 Promicro Diy", 64: "Radiomaster 900 Bandit Nano", 65: "Heltec Capsule Sensor V3", 66: "Heltec Vision Master T190", 67: "Heltec Vision Master E213", 68: "Heltec Vision Master E290", 69: "Heltec Mesh Node T114", 70: "Sensecap Indicator", 72: "RAK3172", 73: "Wio E5", 74: "Radiomaster 900 Bandit", 75: "Me25ls01 4Y10TD", 76: "RP2040 Feather RFM95", 77: "M5Stack Corebasic", 78: "M5Stack CORE2", 79: "Rpi PICO2", 80: "M5Stack CORES3", 81: "Seeed XIAO S3", 82: "MS24SF1", 83: "TLoRa C6", 84: "Wismesh Tap", 85: "Routastic", 86: "Mesh Tab", 87: "Meshlink", 88: "XIAO nRF52 Kit", 90: "Thinknode M2", 91: "T-ETH Elite", 92: "Heltec Sensor Hub", 93: "Muzi Base", 94: "Heltec Mesh Pocket", 95: "Seeed Solar Node", 96: "Nomadstar Meteor Pro", 97: "Crowpanel", 98: "Link 32", 101: "Muzi R1 Neo", 104: "M5Stack Reserved", 106: "RAK3312", 107: "Thinknode M5", 108: "Heltec Mesh Solar", 109: "T-Echo Lite", 110: "Heltec V4", 111: "M5Stack C6L", 112: "M5Stack Cardputer Adv", 114: "T-Watch Ultra", 115: "Thinknode M3", 116: "Wismesh Tap V2", 117: "RAK3401", 118: "RAK6421", 119: "Thinknode M4", 120: "Thinknode M6", 121: "Meshstick 1262", 123: "T5 S3 E-Paper Pro", 125: "Mini E-Paper S3", 126: "Tdisplay S3 Pro", 127: "Heltec Mesh Node T096", 129: "Thinknode M7", 130: "Thinknode M8", 131: "Thinknode M9", 132: "Heltec V4 R8", 133: "Heltec Mesh Node T1", 134: "Station G3", 135: "T-Impulse Plus", 136: "T-Echo Card", 137: "Seeed Wio Tracker L2", 138: "Crowpanel P4", 139: "Heltec Mesh Tower V2", 140: "Meshnology W10", 141: "Heltec RC32", 142: "Heltec RC52", 143: "Heltec RCC6", 144: "Seeed Wio Tracker L1 Pro 1W", 145: "Meshnology W12", 146: "Meshpager X2", 147: "T-Connect Pro", 148: "Axiometa Genesis Mini", 149: "Makerfabs Nomad Terminal", 150: "Thinknode Mx", 255: "Private hardware"}
REGIONS = {1: "US", 2: "EU 433", 3: "EU 868", 4: "CN", 5: "JP", 6: "ANZ", 7: "KR", 8: "TW", 9: "RU", 10: "IN", 11: "NZ 865", 12: "TH", 13: "LoRa 24", 14: "UA 433", 15: "UA 868", 16: "MY 433", 17: "MY 919", 18: "SG 923"}
PRESETS = {0: "Long Fast", 1: "Long Slow", 2: "Very Long Slow", 3: "Medium Slow", 4: "Medium Fast", 5: "Short Slow", 6: "Short Fast", 7: "Long Moderate", 8: "Short Turbo"}


HW_EXTRA = {}   # hardware names read from the Meshtastic project's own list; the names above are kept when both have one
_HW_WORDS = {"RAK": "RAK", "TLORA": "TLoRa", "TBEAM": "T-Beam", "TECHO": "T-Echo", "TDECK": "T-Deck", "TWATCH": "T-Watch", "LORA": "LoRa", "NRF52": "nRF52", "EPAPER": "E-Paper", "ESP32": "ESP32", "XIAO": "XIAO", "DIY": "DIY", "TAK": "TAK", "WIO": "Wio"}


def pretty_hw(name):
    """HELTEC_MESH_NODE_T114 -> Heltec Mesh Node T114: words with a digit stay as they are, a few known names are spelled the way their makers do."""
    out = []
    for w in name.split("_"):
        out.append(_HW_WORDS.get(w, w if any(ch.isdigit() for ch in w) else w.capitalize()))
    return " ".join(out)


def parse_hw_enum(text):
    """{number: name} from the HardwareModel enum in Meshtastic's mesh.proto (nothing else is read; UNSET and the private range are left out)."""
    m = re.search(r"enum\s+HardwareModel\s*\{(.*?)\n\}", text, re.S)
    out = {}
    for line in (m.group(1) if m else "").splitlines():
        g = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(\d+)\s*;", line)
        if g and g.group(1) not in ("UNSET", "PRIVATE_HW") and 0 < int(g.group(2)) < 255:
            out[int(g.group(2))] = pretty_hw(g.group(1))
    return out


def hw_name(n):
    return HARDWARE.get(n) or HW_EXTRA.get(n)


def refresh_hardware():
    """Read the project's hardware list once; returns how many names it knows that this service did not."""
    import urllib.request
    try:
        req = urllib.request.Request(cfg("hw_source"), headers={"User-Agent": "ArkSignal-Mesh/" + VERSION})
        with urllib.request.urlopen(req, timeout=20) as r:
            names = parse_hw_enum(r.read(1_000_000).decode("utf-8", "replace"))
    except Exception:
        return -1
    if names:
        HW_EXTRA.clear()
        HW_EXTRA.update({k: v for k, v in names.items() if k not in HARDWARE})
        STATE["hw_refreshed"] = int(time.time())
    return len(HW_EXTRA)


def hw_loop():
    time.sleep(20)
    while True:
        try:
            refresh_hardware()
        except Exception:
            pass
        time.sleep(max(1, int(cfg("hw_refresh_hours"))) * 3600)


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
                c.execute("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS community TEXT NOT NULL DEFAULT ''")
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
                c.execute("""CREATE TABLE IF NOT EXISTS node_history (node_id BIGINT NOT NULL, ts TIMESTAMPTZ NOT NULL DEFAULT now(), battery REAL, voltage REAL, channel_util REAL, air_util REAL, temp_c REAL, humidity REAL,
                    pressure_hpa REAL, gas_ohm REAL, iaq REAL, lux REAL, wind_dir REAL, wind_ms REAL, gust_ms REAL, rain_1h_mm REAL)""")
                c.execute("CREATE INDEX IF NOT EXISTS node_history_node_ts ON node_history (node_id, ts DESC)")
            return
        except Exception as e:
            print("waiting for the database:", str(e)[:100], flush=True)
            time.sleep(2)
    raise SystemExit("the database never answered")


SECRET_KEYS = ("password", "up_password")   # settings that are secrets: kept sealed in this service's own database when the stack has a SERVICE_TOKEN to seal them with


def _sealer():
    tok = os.environ.get("SERVICE_TOKEN", "")
    if not tok:
        return None
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("meshd-config\0" + tok).encode()).digest()))


def _seal_value(key, v):
    f = _sealer() if key in SECRET_KEYS and v else None
    return "enc1:" + f.encrypt(v.encode()).decode() if f else v


def _open_value(key, v):
    if key in SECRET_KEYS and isinstance(v, str) and v.startswith("enc1:"):
        f = _sealer()
        try:
            return f.decrypt(v[5:].encode()).decode() if f else ""
        except Exception:
            return ""
    return v


def cfg(key):
    with _lock:
        hit = _cfg.get(key)
        if hit and time.time() - hit[1] < 15:
            return hit[0]
    try:
        with db() as c:
            r = c.execute("SELECT value FROM config WHERE key=%s", (key,)).fetchone()
        v = _open_value(key, r["value"]) if r else os.environ.get("MESH_" + key.upper(), DEFAULTS[key])
    except Exception:
        v = DEFAULTS[key]
    with _lock:
        _cfg[key] = (v, time.time())
    return v


def set_cfg(key, value):
    if key not in DEFAULTS:
        raise ValueError("unknown setting " + key)
    with db() as c:
        c.execute("INSERT INTO config (key, value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, _seal_value(key, str(value))))
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


HIST_DEVICE = ("battery", "voltage", "channel_util", "air_util")
HIST_ENV = ("temp_c", "humidity", "pressure_hpa", "gas_ohm", "iaq", "lux", "wind_dir", "wind_ms", "gust_ms", "rain_1h_mm")


def decode_env(payload):
    """The environment metrics of a Telemetry message (field 3): temperature=1, relative_humidity=2, barometric_pressure=3, gas_resistance=4, iaq=7, lux=9, wind_direction=13, wind_speed=14, wind_gust=16, rainfall_1h=19.
    Only values that look possible are kept (a sensor that is not fitted reports zeros or garbage)."""
    t = fields(payload)
    em = first(t, 3)
    if not isinstance(em, bytes):
        return {}
    d = fields(em)
    g = lambda n: (f32(first(d, n)) if isinstance(first(d, n), int) and first(d, n) is not None else None)
    raw = {"temp_c": g(1), "humidity": g(2), "pressure_hpa": g(3), "gas_ohm": g(4), "iaq": first(d, 7), "lux": g(9), "wind_dir": first(d, 13), "wind_ms": g(14), "gust_ms": g(16), "rain_1h_mm": g(19)}
    return clean_env(raw)


def clean_env(raw):
    ok = {"temp_c": (-80, 80), "humidity": (0, 100), "pressure_hpa": (300, 1100), "gas_ohm": (0, 1e9), "iaq": (0, 500), "lux": (0, 200000), "wind_dir": (0, 360), "wind_ms": (0, 120), "gust_ms": (0, 150), "rain_1h_mm": (0, 1000)}
    out = {}
    for k, (lo, hi) in ok.items():
        v = raw.get(k)
        if isinstance(v, (int, float)) and v == v and lo <= v <= hi:
            out[k] = float(v)
    return out


def record_history(c, node, hist):
    """One row of what a node reported, at most one per history_min_seconds per node. Zero battery and voltage (not reported) are not kept."""
    row = {k: hist[k] for k in HIST_DEVICE + HIST_ENV if isinstance(hist.get(k), (int, float))}
    if row.get("battery") == 0:
        row.pop("battery")
    if row.get("voltage") == 0:
        row.pop("voltage")
    if not row:
        return
    last = c.execute("SELECT 1 FROM node_history WHERE node_id=%s AND ts > now() - make_interval(secs => %s)", (node, int(cfg("history_min_seconds")))).fetchone()
    if last:
        return
    cols = list(row)
    c.execute("INSERT INTO node_history (node_id, " + ", ".join(cols) + ") VALUES (%s, " + ", ".join(["%s"] * len(cols)) + ")", [node] + [row[k] for k in cols])


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


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


def community_of(topic):
    """The community a topic belongs to: <publish root>/<community address>/2/... (a packet sent under a community's own root); empty for the ordinary topics."""
    root = (cfg("publish_root").strip("/") or "msh") + "/"
    if not topic.startswith(root):
        return ""
    parts = topic[len(root):].split("/")
    return parts[0] if len(parts) >= 3 and parts[1] == "2" and SLUG_RE.match(parts[0]) else ""


def _store(c, node, upd, gw, snr, rssi, telemetry, hist=None, topic=""):
    """Write what one packet said about a node and which gateway heard it. Returns "ignored" when it adds or places nothing, else None."""
    known = c.execute("SELECT 1 FROM nodes WHERE node_id=%s", (node,)).fetchone()
    if not known and (not upd or telemetry):
        return "ignored"   # a node is only added by something that says who or where it is
    sets = []
    vals = {"n": node}
    com = community_of(topic) if topic else ""
    if com:
        upd = dict(upd, community=com)
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
    if hist:
        record_history(c, node, hist)
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
    hist = None
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
        hist = {k: upd[k] for k in HIST_DEVICE if k in upd}
        hist.update(clean_env({"temp_c": p.get("temperature"), "humidity": p.get("relative_humidity"), "pressure_hpa": p.get("barometric_pressure"), "gas_ohm": p.get("gas_resistance"), "iaq": p.get("iaq"),
                               "lux": p.get("lux"), "wind_dir": p.get("wind_direction"), "wind_ms": p.get("wind_speed"), "gust_ms": p.get("wind_gust"), "rain_1h_mm": p.get("rainfall_1h")}))
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
        r = _store(c, node, upd, gw, snr, rssi, typ == "telemetry", hist, topic)
    if r:
        return r
    STATE["stored"] += 1
    return "stored"


def handle(raw, topic=""):
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
        hist = None
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
            hist = dict({k: upd[k] for k in HIST_DEVICE if k in upd}, **decode_env(e["payload"]))
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
        r = _store(c, node, upd, e["gateway"], e["snr"], e["rssi"], e["portnum"] == PORT_TELEMETRY, hist, topic)
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
SYSTEM_USERS = {ADMIN_USER, "mesh-reader", "mesh-bot"}
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


def bot_password():
    """The service's own sender's login, worked out from the control password like the reader's."""
    a = admin_password()
    return hashlib.sha256(("mesh-bot\0" + a).encode()).hexdigest()[:32] if a else ""


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
        # a community has its own root under the join root (<root>/<community>/2/...): an ordinary login may not write there
        *([{"command": "removeRoleACL", "rolename": "publisher", "acltype": "publishClientSend", "topic": _ROOT[0] + "/+/2/#"}] if _ROOT[0] and _ROOT[0] != (cfg("publish_root").strip("/") or "msh") else []),
        {"command": "addRoleACL", "rolename": "publisher", "acltype": "publishClientSend", "topic": (cfg("publish_root").strip("/") or "msh") + "/+/2/#", "allow": False, "priority": 10},
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
        # the service's own sender (alert and weather messages, relayed packets): may write a community's packets (<root>/<community>/2/e/#) and nothing else, and is never sent anything
        {"command": "createRole", "rolename": "bot"},
        {"command": "addRoleACL", "rolename": "bot", "acltype": "publishClientSend", "topic": (cfg("publish_root").strip("/") or "msh") + "/+/2/e/#", "allow": True},
        {"command": "addRoleACL", "rolename": "bot", "acltype": "publishClientReceive", "topic": "#", "allow": False, "priority": 10},
        {"command": "createClient", "username": "mesh-bot", "password": bot_password()},
        {"command": "setClientPassword", "username": "mesh-bot", "password": bot_password()},
        {"command": "addClientRole", "username": "mesh-bot", "rolename": "bot"},
        {"command": "createClient", "username": "mesh-reader", "password": rp},
        {"command": "setClientPassword", "username": "mesh-reader", "password": rp},
        {"command": "addClientRole", "username": "mesh-reader", "rolename": "reader"},
    ])
    bad = [x for x in r if not _ok(x)]
    if bad:
        raise RuntimeError("the broker refused: " + str(bad[0].get("error")))
    _ROOT[0] = cfg("publish_root").strip("/") or "msh"


def community_roles(slug):
    return "community-" + slug, "community-" + slug + "-down"


def ensure_community(slug, down=False):
    """The broker role of one community's logins: may publish and subscribe under the community's own root and nowhere else, and (the downlink role only) is sent what is published there. The uplink role is sent nothing."""
    if not SLUG_RE.match(slug):
        raise ValueError("a community address is 3 to 40 lowercase letters, digits and dashes")
    root = (cfg("publish_root").strip("/") or "msh") + "/" + slug
    up, dn = community_roles(slug)
    role = dn if down else up
    cmds = [{"command": "createRole", "rolename": role},
            {"command": "addRoleACL", "rolename": role, "acltype": "publishClientSend", "topic": root + "/#", "allow": True},
            {"command": "addRoleACL", "rolename": role, "acltype": "subscribePattern", "topic": root + "/#", "allow": True},
            {"command": "addRoleACL", "rolename": role, "acltype": "publishClientReceive", "topic": "#", "allow": False, "priority": 10}]
    if down:
        cmds.append({"command": "addRoleACL", "rolename": role, "acltype": "publishClientReceive", "topic": root + "/#", "allow": True, "priority": 20})
    bad = [x for x in control(cmds) if not _ok(x)]
    if bad:
        raise RuntimeError("the broker refused: " + str(bad[0].get("error")))
    return role


def set_user(username, password, community="", down=False):
    """A member's login: may publish to the mesh topics, may not read anything. A community's login publishes only under its community's root (and, for a downlink login, is sent what is published there).
    Safe to repeat (it also changes the password and moves a login between kinds)."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,40}", username) or username in SYSTEM_USERS or not (8 <= len(password) <= 63):
        raise ValueError("a login name is 3 to 41 lowercase letters, digits, dots, dashes; a password 8 to 63 characters")
    cmds = [{"command": "createClient", "username": username, "password": password}, {"command": "setClientPassword", "username": username, "password": password}]
    if community:
        want = ensure_community(community, bool(down))
        up, dn = community_roles(community)
        cmds += [{"command": "removeClientRole", "username": username, "rolename": "publisher"}, {"command": "removeClientRole", "username": username, "rolename": dn if want == up else up},
                 {"command": "addClientRole", "username": username, "rolename": want}]
    else:
        cmds.append({"command": "addClientRole", "username": username, "rolename": "publisher"})
    r = control(cmds)
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


def sync_users(users, prune=True):
    """Make the broker's member logins this list [{username, password}]: new ones made, passwords set and (when prune is true) logins nobody asked for removed. The automatic sync passes prune false: it may add and
    repair logins, but it never deletes one."""
    if prune:
        ensure_base()   # the broker's own roles and the service's login are re-asserted (the broker disconnects clients whose rules are touched): the automatic sync skips this unless it is needed
    want = {u["username"]: u["password"] for u in users}
    kind = {u["username"]: (u.get("community") or "", bool(u.get("down"))) for u in users}
    have = set(list_users())
    try:
        for name, pw in want.items():
            set_user(name, pw, *kind[name])
    except RuntimeError:   # the roles are missing (a rebuilt broker): make them, then try again
        ensure_base()
        for name, pw in want.items():
            set_user(name, pw, *kind[name])
    removed = 0
    for name in (have - set(want)) if prune else ():
        delete_user(name); removed += 1
    return {"set": len(want), "removed": removed}


def base_loop():
    """Prepare the roles and the service's own login as soon as the broker answers (and again if it was rebuilt)."""
    ok_at = 0
    while True:
        try:
            if cfg("enabled") == "1" and admin_password() and time.time() - ok_at > 600:
                ensure_base(); ok_at = time.time(); STATE["broker_ready"] = True
                try:
                    _bot_client()   # the sender's connection is made ahead of the first message
                except Exception:
                    pass
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
            if not pfx:
                try:
                    forward(msg.topic, msg.payload)
                except Exception as ex:
                    STATE["fwd_error"] = str(ex)[:80]
                try:
                    relay(msg.topic, msg.payload)
                except Exception as ex:
                    STATE["relay_error"] = str(ex)[:80]
            if msg.topic.startswith("meshcore/"):
                handle_meshcore(msg.topic, msg.payload)
            elif "/2/json/" in msg.topic:
                handle_json(msg.topic, msg.payload)
            else:
                handle(msg.payload, msg.topic)
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


# ------------------------------------------------------------------ forwarding a community's uplink to the public Meshtastic server
# A node can send to one MQTT server only, so a community that wants its nodes on the public map as well gets what its nodes send forwarded here: the packet bytes unchanged, under the public root
# (<community root>/2/e/<channel>/<gateway> -> <fwd root>/2/e/<channel>/<gateway>). Off unless the site switch is on and the community asked; the public server's own rules are applied first.
_FWD = {"client": None, "sig": None}
_fwd_lock = threading.Lock()


def _fwd_client():
    sig = (cfg("up_host"), cfg("up_port"), cfg("up_user"), cfg("up_password"))
    with _fwd_lock:
        if _FWD["client"] is not None and _FWD["sig"] == sig:
            return _FWD["client"]
        if _FWD["client"] is not None:
            try:
                _FWD["client"].loop_stop(); _FWD["client"].disconnect()
            except Exception:
                pass
        import paho.mqtt.client as mqtt
        cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2) if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client()
        if sig[2]:
            cl.username_pw_set(sig[2], sig[3])
        cl.reconnect_delay_set(2, 60)
        cl.connect_async(sig[0], int(sig[1]), 60)
        cl.loop_start()
        _FWD["client"], _FWD["sig"] = cl, sig
        return cl


def forward_target(topic):
    """The public topic a community packet is forwarded to, or None when its community is not forwarding (or the topic is not a node's uplink)."""
    if cfg("fwd_enabled") != "1":
        return None
    com = community_of(topic)
    if not com or com not in {x.strip() for x in cfg("fwd_communities").split(",") if x.strip()}:
        return None
    root = (cfg("publish_root").strip("/") or "msh") + "/" + com + "/"
    rest = topic[len(root):]
    return (cfg("fwd_root").strip("/") or "msh") + "/" + rest if rest.startswith(("2/e/", "2/map/")) else None


def forward_allowed(topic, raw):
    """The public server's rules for one packet: an encrypted packet only on a default-key channel it lists, a decoded one only with an allowed port number and a position no more exact than allowed."""
    if "/2/map/" in topic:
        e = decode_envelope(raw)
        if e is None or e["portnum"] != PORT_MAP_REPORT:
            return False
        bits = decode_map_report(e["payload"]).get("precision_bits")
        return bits is not None and bits <= int(cfg("public_max_bits"))
    e = decode_envelope(raw)
    if e is None:
        return False
    if cfg("public_rules") != "1" or not is_public(e["channel"]):
        return False   # only the default-key channels the public server lists: a private channel is never forwarded
    if e["encrypted"] or e["portnum"] is None:
        return True
    if not port_allowed(e["channel"], e["portnum"]):
        return False
    if e["portnum"] == PORT_POSITION:
        bits = decode_position(e["payload"]).get("precision_bits")
        return bits is not None and bits <= int(cfg("public_max_bits"))
    return True


def forward(topic, raw):
    target = forward_target(topic)
    if target is None:
        return None
    if not forward_allowed(topic, raw):
        STATE["fwd_dropped"] += 1
        return "dropped"
    try:
        _fwd_client().publish(target, raw, qos=0, retain=False)
        STATE["fwd_sent"] += 1
        STATE["fwd_error"] = ""
        return "sent"
    except Exception as ex:
        STATE["fwd_error"] = str(ex)[:80]
        return "error"


# ------------------------------------------------------------------ sending packets ourselves: the alert and weather channel (and, later, relayed packets)
# A Meshtastic packet on the broker is a ServiceEnvelope { packet (MeshPacket), channel_id, gateway_id } published at <root>/<community>/2/e/<channel name>/<gateway id>. The payload of a private or default-key channel is
# encrypted with AES-CTR under the channel's key; the nonce is the packet id (8 bytes, little endian), the sender's node number (4 bytes, little endian) and four zero bytes. A key of one byte is an index into the
# well known default key. Only a node with downlink switched on for that channel, logged in with a community's downlink login, sends such a packet out over the air.
PORT_TEXT = 1
_BOT = {"client": None, "sig": None}
_bot_lock = threading.Lock()


def _bot_client():
    sig = (cfg("host"), cfg("port"), bot_password())
    with _bot_lock:
        if _BOT["client"] is not None and _BOT["sig"] == sig:
            for _ in range(80):   # the broker drops this login whenever it is given new rules; the client reconnects by itself: wait for it (up to eight seconds)
                if _BOT["client"].is_connected():
                    break
                time.sleep(0.1)
            return _BOT["client"]
        if _BOT["client"] is not None:
            try:
                _BOT["client"].loop_stop(); _BOT["client"].disconnect()
            except Exception:
                pass
        import paho.mqtt.client as mqtt
        cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2) if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client()
        cl.username_pw_set("mesh-bot", sig[2])
        cl.reconnect_delay_set(1, 5)
        cl.connect_async(sig[0], int(sig[1]), 60)
        cl.loop_start()
        for _ in range(50):   # a packet published before the connection is up is dropped: wait for it (up to five seconds)
            if cl.is_connected():
                break
            time.sleep(0.1)
        _BOT["client"], _BOT["sig"] = cl, sig
        return cl


def publish_packet(community, channel, raw, gateway):
    """Publish an envelope on a community's topic for a channel. Returns True when the broker took it."""
    root = (cfg("publish_root").strip("/") or "msh") + "/" + community
    topic = f"{root}/2/e/{channel}/{gateway}"
    for _ in range(4):   # the first packet after a connection is made or lost can find the client not yet connected: wait and try again
        try:
            info = _bot_client().publish(topic, raw, qos=0, retain=False)
            if info.rc == 0:
                info.wait_for_publish(5)
                if info.is_published():
                    return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def publish_envelope(community, channel, raw):
    """Publish a finished envelope (already encrypted by the portal; the service never sees a channel key) on a community's topic for a channel. Returns {ok, error?}."""
    if not SLUG_RE.match(community or ""):
        return {"ok": False, "error": "a community address is 3 to 40 lowercase letters, digits and dashes"}
    if not channel or len(channel) > 11 or "/" in channel or "+" in channel or "#" in channel:
        return {"ok": False, "error": "a channel name is 1 to 11 characters without / + #"}
    e = decode_envelope(raw)
    if e is None or len(raw) > 400:
        return {"ok": False, "error": "that is not a packet this service will send"}
    if not re.fullmatch(r"![0-9a-f]{8}", e["gateway"] or ""):
        return {"ok": False, "error": "the gateway ID is not of the form !a1b2c3d4"}
    ok = publish_packet(community, channel, raw, e["gateway"])
    STATE["bot_sent" if ok else "bot_failed"] = STATE.get("bot_sent" if ok else "bot_failed", 0) + 1
    return {"ok": ok, "error": "" if ok else "the broker did not take the packet"}


# ------------------------------------------------------------------ linking two communities' areas through the broker
# Two areas with no radio path between them (the Arkansas link channel and the Sheridan extension, say) are joined by carrying packets between their community topics. The packets are carried exactly as they arrive:
# still encrypted, never opened (the service holds no channel key), so the two communities' channel must have the same name and the same key for a node on the far side to read them. Only the packet's own
# hop limit is rewritten (it is not encrypted): never raised, at most the link's limit. Loop and duplicate protection: a packet (sender and packet id) is carried once, whichever gateway heard it first and
# whichever side it came from, for the minutes set; the service's own relayed packets are never read again; a rate limit per link; a paused link carries nothing. A link is {id, a, b, channel, hop_limit, rate_per_min, paused}.
_RELAY = {"seen": {}, "hits": {}, "stats": {}}
_relay_lock = threading.Lock()


def links():
    try:
        return [l for l in json.loads(cfg("links")) if isinstance(l, dict) and SLUG_RE.match(str(l.get("a", ""))) and SLUG_RE.match(str(l.get("b", ""))) and l.get("channel")]
    except ValueError:
        return []


def _lstat(lid):
    return _RELAY["stats"].setdefault(str(lid), {"a_to_b": 0, "b_to_a": 0, "duplicates": 0, "limited": 0, "bad": 0, "errors": 0, "last_error": "", "last_at": None})


def encode_fields(fl):
    out = b""
    for num, wt, v in fl:
        if wt == 0:
            out += _venc(num << 3) + _venc(v)
        elif wt == 5:
            out += _venc((num << 3) | 5) + struct.pack("<I", v & 0xFFFFFFFF)
        elif wt == 1:
            out += _venc((num << 3) | 1) + struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)
        elif wt == 2:
            out += _venc((num << 3) | 2) + _venc(len(v)) + v
    return out


def _venc(v):
    out = b""
    v &= (1 << 64) - 1
    while True:
        b = v & 0x7F
        v >>= 7
        out += bytes([b | (0x80 if v else 0)])
        if not v:
            return out


def rewrite_hop(raw, hop):
    """The envelope with its packet's hop limit (field 9) set to `hop`, everything else byte for byte as it was."""
    env = fields(raw)
    out = []
    for num, wt, v in env:
        if num == 1 and wt == 2:
            mp = [f for f in fields(v) if f[0] != 9]
            mp.append((9, 0, hop))
            v = encode_fields(mp)
        out.append((num, wt, v))
    return encode_fields(out)


def relay(topic, raw):
    """Carry a packet heard on one community's topic to the community it is linked with. Returns the number of links it was carried on."""
    root = (cfg("publish_root").strip("/") or "msh") + "/"
    if not topic.startswith(root):
        return 0
    parts = topic[len(root):].split("/")
    if len(parts) != 5 or parts[1] != "2" or parts[2] != "e":
        return 0
    slug, channel, gw = parts[0], parts[3], parts[4]
    if gw == cfg("relay_gateway"):
        return 0   # what this service published itself
    mine = [l for l in links() if not l.get("paused") and l["channel"] == channel and slug in (l["a"], l["b"])]
    if not mine:
        return 0
    env = fields(raw)
    pk = first(env, 1)
    mp = fields(pk) if isinstance(pk, bytes) else []
    sender, pid = first(mp, 1), first(mp, 6)
    if sender is None or pid is None:
        for l in mine:
            _lstat(l["id"])["bad"] += 1
        return 0
    now = time.time()
    keep = int(cfg("link_dedupe_seconds"))
    with _relay_lock:
        for k in [k for k, t in _RELAY["seen"].items() if now - t > keep]:
            del _RELAY["seen"][k]
        if (sender, pid) in _RELAY["seen"]:
            for l in mine:
                _lstat(l["id"])["duplicates"] += 1
            return 0
        _RELAY["seen"][(sender, pid)] = now
    done = 0
    hop = first(mp, 9, 0) or 0
    for l in mine:
        st = _lstat(l["id"])
        with _relay_lock:
            hits = [t for t in _RELAY["hits"].get(l["id"], []) if now - t < 60]
            if len(hits) >= int(l.get("rate_per_min") or 20):
                st["limited"] += 1
                _RELAY["hits"][l["id"]] = hits
                continue
            hits.append(now)
            _RELAY["hits"][l["id"]] = hits
        dst = l["b"] if slug == l["a"] else l["a"]
        try:
            ok = publish_packet(dst, channel, rewrite_hop(raw, min(hop, int(l.get("hop_limit") or 3))), cfg("relay_gateway"))
        except Exception as ex:
            ok, st["last_error"] = False, str(ex)[:80]
        if ok:
            st["a_to_b" if slug == l["a"] else "b_to_a"] += 1
            st["last_at"] = now
            done += 1
        else:
            st["errors"] += 1
            st["last_error"] = st["last_error"] or "the broker did not take the packet"
    if done == 0:   # nothing went out: the same packet heard again (by another gateway) may try again
        with _relay_lock:
            _RELAY["seen"].pop((sender, pid), None)
    return done


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
                c.execute("DELETE FROM node_history WHERE ts < now() - make_interval(days => %s) OR node_id NOT IN (SELECT node_id FROM nodes)", (int(cfg("history_keep_days")),))
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
    out["hardware"] = hw_name(r.get("hw_model")) or (("Unlisted hardware (#%s)" % r["hw_model"]) if r.get("hw_model") else "")
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


def plan(radius_km=15.0, poor_snr=-5.0, results=8, days=14, min_sep_km=3.0):
    """Where the next router would help most, from what was heard (not a prediction: no terrain, no antenna heights). A node is poorly served when no more than one gateway heard it recently or the best signal
    anyone had of it was weaker than poor_snr. Each candidate place is a poorly served node's own position; the best candidate is the one with the most poorly served nodes within radius_km, then the next
    best for the nodes that are left, and so on. A place within min_sep_km of an existing router or gateway is skipped (it would add nothing). Returns {suggestions: [...], poor, active, routers}."""
    with db() as c:
        nodes = c.execute("SELECT node_id, long_name, short_name, role, lat, lon FROM nodes WHERE lat IS NOT NULL AND last_seen > now() - make_interval(days => %s) "
                          "AND NOT EXISTS (SELECT 1 FROM hidden x WHERE x.node_key = '!' || lpad(to_hex(node_id), 8, '0'))", (days,)).fetchall()
        heard = {r["node_id"]: r for r in c.execute("SELECT node_id, COUNT(DISTINCT gateway) AS gws, MAX(best_snr) AS best FROM heard WHERE ts > now() - make_interval(days => %s) GROUP BY node_id", (days,)).fetchall()}
        gws = {r["gateway"].lstrip("!").lower() for r in c.execute("SELECT DISTINCT gateway FROM heard WHERE ts > now() - make_interval(days => %s)", (days,)).fetchall()}
    serving, poor = [], []
    for n in nodes:
        if n["role"] in (2, 3, 4, 11) or ("%08x" % n["node_id"]) in gws:
            serving.append(n)
            continue
        h = heard.get(n["node_id"])
        if h is None or h["gws"] <= 1 or (h["best"] is not None and h["best"] < poor_snr):
            poor.append(n)
    poor = poor[:3000]
    left = list(poor)
    out = []
    for _ in range(max(1, int(results))):
        best = None
        for cand in left:
            if any(bearing_km(cand["lat"], cand["lon"], s["lat"], s["lon"])[0] < min_sep_km for s in serving):
                continue
            covered = [x for x in left if bearing_km(cand["lat"], cand["lon"], x["lat"], x["lon"])[0] <= radius_km]
            if best is None or len(covered) > len(best[1]):
                best = (cand, covered)
        if best is None or not best[1]:
            break
        cand, covered = best
        near = min((bearing_km(cand["lat"], cand["lon"], s["lat"], s["lon"])[0] for s in serving), default=None)
        out.append({"lat": round(cand["lat"], 4), "lon": round(cand["lon"], 4), "helps": len(covered), "nearest_serving_km": None if near is None else round(near, 1),
                    "nodes": [{"id": "!%08x" % x["node_id"], "slug": "%08x" % x["node_id"], "name": x["long_name"] or x["short_name"] or ""} for x in covered[:6]]})
        ids = {x["node_id"] for x in covered}
        left = [x for x in left if x["node_id"] not in ids]
    return {"suggestions": out, "poor": len(poor), "active": len(nodes), "serving": len(serving), "radius_km": radius_km, "poor_snr": poor_snr, "days": days}


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

    def _allowed(self):
        """With a SERVICE_TOKEN set, every request but /health must carry it (the portal and this stack's other services do)."""
        want = os.environ.get("SERVICE_TOKEN", "")
        if not want or urllib.parse.urlparse(self.path).path.rstrip("/") in ("", "/health"):
            return True
        import hmac
        return hmac.compare_digest(self.headers.get("X-Service-Token", ""), want)

    def do_GET(self):
        if not self._allowed():
            return self.send({"error": "unauthorized"}, 401)
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        p = u.path.rstrip("/")
        try:
            if p in ("", "/health"):
                return self.send({"ok": True, "version": VERSION, "connected": STATE["connected"], "since": int(STATE["since"]), "broker_ready": bool(STATE.get("broker_ready"))})
            if p == "/links/stats":
                return self.send({"ok": True, "links": dict(_RELAY["stats"]), "active": len([l for l in links() if not l.get("paused")]), "error": STATE.get("relay_error", "")})
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
                                  "hardware": [{"hardware": hw_name(r["hw_model"]) or ("Unlisted hardware (#%s)" % r["hw_model"] if r["hw_model"] else "unknown"), "n": r["n"]} for r in hw],
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
            m = re.match(r"^/nodes/([^/]+)/history$", p)
            if m:
                nid = parse_id(urllib.parse.unquote(m.group(1)).strip())
                if nid is None:
                    return self.send({"error": "not a node"}, 400)
                hours = max(1, min(int(q.get("hours") or 24), 24 * 365))
                bucket = 300 if hours <= 24 else 3600 if hours <= 168 else 10800 if hours <= 720 else 86400
                avg = ", ".join(f"avg({k}) AS {k}" for k in HIST_DEVICE + HIST_ENV)
                with db() as c:
                    if not c.execute("SELECT 1 FROM nodes WHERE node_id=%s", (nid,)).fetchone() or c.execute("SELECT 1 FROM hidden WHERE node_key=%s", ("!%08x" % nid,)).fetchone():
                        return self.send({"error": "not found"}, 404)
                    rows = c.execute(f"SELECT to_timestamp(floor(extract(epoch FROM ts) / %(b)s) * %(b)s) AS t, {avg} FROM node_history WHERE node_id=%(n)s AND ts > now() - make_interval(hours => %(h)s) GROUP BY 1 ORDER BY 1",
                                     {"b": bucket, "n": nid, "h": hours}).fetchall()
                return self.send({"bucket": bucket, "hours": hours, "rows": [dict({k: (None if v is None else round(v, 3)) for k, v in r.items() if k != "t"}, t=iso(r["t"])) for r in rows]})
            if p == "/weather":
                with db() as c:
                    rows = c.execute("SELECT DISTINCT ON (h.node_id) h.node_id, h.ts, h.temp_c, h.humidity, h.pressure_hpa, h.gas_ohm, h.iaq, h.lux, h.wind_dir, h.wind_ms, h.gust_ms, h.rain_1h_mm, n.long_name, n.short_name, n.lat, n.lon "
                                     "FROM node_history h JOIN nodes n ON n.node_id = h.node_id WHERE h.ts > now() - make_interval(hours => %s) AND (h.temp_c IS NOT NULL OR h.humidity IS NOT NULL OR h.pressure_hpa IS NOT NULL OR h.wind_ms IS NOT NULL OR h.rain_1h_mm IS NOT NULL OR h.lux IS NOT NULL) "
                                     "AND NOT EXISTS (SELECT 1 FROM hidden x WHERE x.node_key = '!' || lpad(to_hex(h.node_id), 8, '0')) ORDER BY h.node_id, h.ts DESC", (min(int(q.get("hours") or 24), 24 * 30),)).fetchall()
                out = [dict({k: (None if v is None else round(v, 2)) for k, v in r.items() if k in HIST_ENV}, id="!%08x" % r["node_id"], slug="%08x" % r["node_id"], name=r["long_name"] or r["short_name"] or "", ts=iso(r["ts"]), lat=r["lat"], lon=r["lon"]) for r in rows]
                out.sort(key=lambda r: r["ts"], reverse=True)
                return self.send({"nodes": out})
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
                    gw_ids = {g: parse_id(g) for g in {h["gateway"] for h in hb}}   # the gateways' friendly names (a node taken down is not named)
                    gw_names = {r2["node_id"]: r2["long_name"] or r2["short_name"] or "" for r2 in c.execute("SELECT n.node_id, n.long_name, n.short_name FROM nodes n WHERE n.node_id = ANY(%s) AND NOT EXISTS (SELECT 1 FROM hidden x WHERE x.node_key = '!' || lpad(to_hex(n.node_id), 8, '0'))", ([i for i in gw_ids.values() if i is not None],)).fetchall()}
                    nb = c.execute("SELECT l.a, l.b, l.snr, l.last_seen, o.long_name, o.short_name FROM links l JOIN nodes o ON o.node_id = CASE WHEN l.a = %s THEN l.b ELSE l.a END WHERE l.a = %s OR l.b = %s ORDER BY l.last_seen DESC LIMIT 100", (nid, nid, nid)).fetchall()
                    nb += [dict(r2, long_name="", short_name="") for r2 in c.execute("SELECT l.a, l.b, l.snr, l.last_seen FROM links l WHERE l.a = %s AND NOT EXISTS (SELECT 1 FROM nodes o WHERE o.node_id = l.b)", (nid,)).fetchall()]
                neighbors = [{"id": "!%08x" % (x["b"] if x["a"] == nid else x["a"]), "slug": "%08x" % (x["b"] if x["a"] == nid else x["a"]), "name": x["long_name"] or x["short_name"] or "", "snr": x["snr"],
                              "direction": "hears" if x["a"] == nid else "heard_by", "last_seen": iso(x["last_seen"])} for x in nb]
                return self.send({"node": node_row(r), "heard_by": [dict({k: iso(v) for k, v in h.items()}, slug=h["gateway"].lstrip("!"), name=gw_names.get(gw_ids.get(h["gateway"]), "")) for h in hb], "gateway_for": gw_of, "neighbors": neighbors})
            if p == "/links":
                days_h = min(int(q.get("hours") or 336), 24 * 365)
                with db() as c:
                    rows = c.execute("SELECT a, b, snr, last_seen, reported_by, count FROM links WHERE last_seen > now() - make_interval(hours => %s) ORDER BY last_seen DESC LIMIT 20000", (days_h,)).fetchall()
                return self.send({"links": [{"a": "!%08x" % r["a"], "b": "!%08x" % r["b"], "snr": r["snr"], "last_seen": iso(r["last_seen"]), "reported_by": r["reported_by"], "count": r["count"]} for r in rows]})
            if p == "/plan":
                return self.send(plan(float(q.get("radius") or 15), float(q.get("snr") or -5), int(q.get("results") or 8), int(q.get("days") or 14), float(q.get("sep") or 3)))
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
        if not self._allowed():
            return self.send({"error": "unauthorized"}, 401)
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
            if p == "/forward_check":   # what forwarding would do with one packet, without sending it (for tests): {topic, envelope_b64}
                raw = base64.b64decode(b.get("envelope_b64") or "")
                t = forward_target(str(b.get("topic") or ""))
                return self.send({"ok": True, "target": t, "allowed": bool(t) and forward_allowed(str(b.get("topic") or ""), raw)})
            if p == "/publish":   # a finished, encrypted packet for a community's channel: {community, channel, envelope_b64}
                try:
                    raw = base64.b64decode(b.get("envelope_b64") or "")
                except Exception:
                    return self.send({"ok": False, "error": "the packet is not valid base64"}, 400)
                r = publish_envelope(str(b.get("community") or ""), str(b.get("channel") or ""), raw)
                return self.send(r, 200 if r.get("ok") else 400 if "broker" not in r["error"] else 502)
            if p == "/links/set":   # the linked areas: {links: [{id, a, b, channel, hop_limit, rate_per_min, paused}]}
                good = [{"id": int(l["id"]), "a": str(l["a"]), "b": str(l["b"]), "channel": str(l["channel"])[:11], "hop_limit": max(0, min(7, int(l.get("hop_limit", 3)))), "rate_per_min": max(1, min(600, int(l.get("rate_per_min", 20)))), "paused": bool(l.get("paused"))}
                        for l in (b.get("links") or []) if SLUG_RE.match(str(l.get("a", ""))) and SLUG_RE.match(str(l.get("b", ""))) and str(l.get("a")) != str(l.get("b")) and l.get("channel")]
                set_cfg("links", json.dumps(good))
                return self.send({"ok": True, "links": len(good)})
            if p == "/prune":
                prune_links()
                return self.send({"ok": True})
            if p == "/ingest":   # the same door the broker uses, for tests and for a one-off import: a base64 ServiceEnvelope
                return self.send({"ok": True, "result": handle(base64.b64decode(b.get("envelope_b64") or ""), str(b.get("topic") or ""))})
            if p == "/credentials/sync":
                users = [{"username": str(u.get("username") or ""), "password": str(u.get("password") or ""), "community": str(u.get("community") or ""), "down": bool(u.get("down"))} for u in (b.get("users") or []) if isinstance(u, dict)]
                try:
                    return self.send(dict(ok=True, **sync_users(users, prune=b.get("prune", True) is not False)))
                except (RuntimeError, ValueError) as e:
                    return self.send({"ok": False, "error": str(e)}, 502)
            if p == "/credentials/set":
                try:
                    ensure_base()
                    set_user(str(b.get("username") or ""), str(b.get("password") or ""), str(b.get("community") or ""), bool(b.get("down")))
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
    for fn in (reader, lambda: reader("up_"), pruner, base_loop, hw_loop):
        threading.Thread(target=fn, daemon=True).start()
    print(f"arksignal-mesh {VERSION} listening on {PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
