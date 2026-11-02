"""Outbound MQTT export of mesh traffic (request 208).

The administrators define connectors, each one an outside MQTT broker with its own filters (which feed, which communities, channels and port numbers, how exact a position may be, the public rules).
The portal keeps them (the password sealed, the table in the backup) and sends them to the mesh service, which reads our broker and publishes what each connector's filters let through. Private feed data
leaves the site only through a connector an administrator has explicitly allowed to carry it."""
import json
import re

from . import mesh as ms
from . import vault
from .db import get_db
from .settings import cfg, cfg_int

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9_./-]{1,100}$")
CHAN_RE = re.compile(r"^[A-Za-z0-9_ -]{1,11}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
PORTS = [(1, "Text messages"), (3, "Positions"), (4, "Node information"), (67, "Telemetry"), (71, "Neighbor information"), (73, "Map reports"), (5, "Routing"), (7, "Text (compressed)")]
SOURCES = [("public", "Public feed (what the public Meshtastic maps would carry)"), ("private", "Private feed (every packet our private gateways send)"), ("both", "Both")]
POSITIONS = [("asis", "As received"), ("bits", "Cut to the number of bits below"), ("none", "Removed (position and map report packets are not sent)")]


def _csv(text):
    return [x.strip() for x in (text or "").replace("\n", ",").split(",") if x.strip()]


def rows():
    return get_db().execute("SELECT * FROM mesh_exports ORDER BY id").fetchall()


def get(cid):
    return get_db().execute("SELECT * FROM mesh_exports WHERE id=%s", (cid,)).fetchone()


def _connector(r):
    """What the mesh service gets for one connector."""
    return {"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]), "host": r["host"], "port": r["port"], "tls": bool(r["tls"]), "username": r["username"], "password": vault.unseal(r["password_sealed"]),
            "qos": r["qos"], "prefix": r["prefix"], "source": r["source"], "communities": _csv(r["communities"]), "channels": _csv(r["channels"]), "portnums": [int(x) for x in _csv(r["portnums"]) if x.isdigit()],
            "position": r["position"], "max_bits": r["max_bits"], "mimic_public": bool(r["mimic_public"]), "encrypted": bool(r["encrypted"]), "rate_per_min": r["rate_per_min"]}


def push():
    """Send the site switch and every connector to the mesh service. (ok, note)."""
    payload = {"enabled": cfg("mesh_export_enabled"), "exports": [_connector(r) for r in rows()]}
    ok, d = ms.call("/exports/set", "POST", payload)
    return (bool(ok and isinstance(d, dict) and d.get("ok")), "" if ok else str(d))


def status():
    """{enabled, connectors: {id: {connected, sent, filtered, rate_dropped, errors, last_error, last_sent}}}, or an empty answer when the service does not respond."""
    ok, d = ms.call("/exports/status")
    return d if ok and isinstance(d, dict) else {"enabled": False, "connectors": {}}


def parse(form, existing=None):
    """(values dict, error) from the admin form. A blank password keeps the stored one."""
    name = " ".join((form.get("name") or "").split())[:60]
    host = (form.get("host") or "").strip()
    if not name:
        return None, "Give the connector a name."
    if not HOST_RE.match(host):
        return None, "The broker's host name or address looks wrong."
    try:
        port = int(form.get("port") or 1883)
        bits = int(form.get("max_bits") or 16)
        rate = int(form.get("rate_per_min") or 600)
    except ValueError:
        return None, "The port, the bits and the rate must be numbers."
    if not 1 <= port <= 65535 or not 1 <= bits <= 32 or not 1 <= rate <= 100000:
        return None, "Port 1 to 65535, bits 1 to 32, packets per minute 1 to 100000."
    prefix = (form.get("prefix") or "").strip().strip("/")
    if not PREFIX_RE.match(prefix):
        return None, "The topic prefix may hold letters, digits and the characters _ . / - only (no wildcards)."
    source = form.get("source") if form.get("source") in ("public", "private", "both") else "public"
    position = form.get("position") if form.get("position") in ("asis", "bits", "none") else "asis"
    chans = _csv(form.get("channels"))
    if any(not CHAN_RE.match(c) for c in chans):
        return None, "A channel name is up to 11 letters, digits, spaces, dashes or underscores."
    ports = {int(p) for p in form.getlist("portnums") if p.isdigit()} | {int(p) for p in _csv(form.get("portnums_extra")) if p.isdigit() and int(p) < 512}
    coms = [c for c in form.getlist("communities") if c == "-" or SLUG_RE.match(c)]
    pw = form.get("password") or ""
    sealed = vault.seal(pw) if pw else (existing["password_sealed"] if existing else "")
    return {"name": name, "enabled": bool(form.get("enabled")), "host": host, "port": port, "tls": bool(form.get("tls")), "username": (form.get("username") or "").strip()[:100], "password_sealed": sealed,
            "qos": 1 if form.get("qos") == "1" else 0, "prefix": prefix, "source": source, "communities": ",".join(coms), "channels": ",".join(chans), "portnums": ",".join(str(p) for p in sorted(ports)),
            "position": position, "max_bits": bits, "mimic_public": bool(form.get("mimic_public")), "encrypted": bool(form.get("encrypted")), "rate_per_min": rate}, ""


def save(values, cid=None):
    d = get_db()
    cols = ["name", "enabled", "host", "port", "tls", "username", "password_sealed", "qos", "prefix", "source", "communities", "channels", "portnums", "position", "max_bits", "mimic_public", "encrypted", "rate_per_min"]
    if cid:
        d.execute("UPDATE mesh_exports SET " + ", ".join(f"{c}=%({c})s" for c in cols) + ", updated=now() WHERE id=%(id)s", dict(values, id=cid))
    else:
        cid = d.execute("INSERT INTO mesh_exports (" + ", ".join(cols) + ") VALUES (" + ", ".join(f"%({c})s" for c in cols) + ") RETURNING id", values).fetchone()["id"]
    d.commit()
    return cid


def delete(cid):
    get_db().execute("DELETE FROM mesh_exports WHERE id=%s", (cid,))
    get_db().commit()


def count():
    return get_db().execute("SELECT COUNT(*) AS n FROM mesh_exports").fetchone()["n"]


def limit():
    return cfg_int("mesh_export_max")
