"""The private tunnel for the mesh private feed (request 205, stage 4).

A gateway reaches the private broker through a WireGuard tunnel, and uses the tunnel's own address as its MQTT address: the Meshtastic firmware treats a private address (10/8 and the other private ranges) as a
broker it need not hold to the OK to MQTT setting, and nothing but that one broker port is reachable from inside the tunnel (see deploy/mesh/wg-start.sh). The portal owns the keys: the server key is kept
sealed here (so a rebuilt hub gets the same tunnel back), a peer's private key is shown once in its configuration and never kept, only its public key and address are. The mesh service writes what the
tunnel container reads."""
import base64
import hashlib
import hmac
import ipaddress
import io
import re
import secrets
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from . import mesh as ms
from . import vault
from .db import get_db
from .settings import cfg, cfg_int

ENDPOINT_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}(:\d{1,5})?$")


def _b64(raw):
    return base64.b64encode(raw).decode()


def new_keypair():
    """(private, public) as WireGuard writes them: base64 of the 32 raw bytes."""
    k = X25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64(priv), _b64(pub)


def public_of(priv_b64):
    k = X25519PrivateKey.from_private_bytes(base64.b64decode(priv_b64))
    return _b64(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def address():
    """The tunnel address gateways use as the MQTT address (the same value the tunnel container is given)."""
    try:
        return str(ipaddress.ip_address(cfg("mesh_wg_address").strip()))
    except ValueError:
        return "10.77.0.1"


def endpoint():
    """host:port of the tunnel as peers reach it from outside; empty until the site sets it."""
    e = cfg("mesh_wg_endpoint").strip()
    if not e or not ENDPOINT_RE.match(e):
        return ""
    return e if ":" in e else e + ":51820"


def server_key():
    """(private, public): made the first time it is needed and kept sealed."""
    d = get_db()
    row = d.execute("SELECT private_sealed FROM mesh_wg_server WHERE id = 1").fetchone()
    priv = vault.unseal(row["private_sealed"]) if row else ""
    if not priv:
        priv, _pub = new_keypair()
        d.execute("INSERT INTO mesh_wg_server (id, private_sealed) VALUES (1, %s) ON CONFLICT (id) DO UPDATE SET private_sealed = EXCLUDED.private_sealed", (vault.seal(priv),))
        d.commit()
    return priv, public_of(priv)


def relay_key():
    """The key only the portal and the tunnel's relay hold (kept sealed): a private login's password on the broker is made from the member's password and this key, so the member's own password opens
    nothing but the tunnel's relay. Made the first time it is needed."""
    d = get_db()
    server_key()
    row = d.execute("SELECT relay_key_sealed FROM mesh_wg_server WHERE id = 1").fetchone()
    k = vault.unseal(row["relay_key_sealed"]) if row and row["relay_key_sealed"] else ""
    if not k:
        k = secrets.token_urlsafe(32)
        d.execute("UPDATE mesh_wg_server SET relay_key_sealed=%s WHERE id = 1", (vault.seal(k),))
        d.commit()
    return k


def broker_password(username, password):
    """The password the broker holds for a private login (the relay, deploy/mesh/wg-relay.py, makes the same one from what a peer presents)."""
    mac = hmac.new(relay_key().encode(), username.encode() + b"\0" + password.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=")[:43].decode()


def _used_ips(d):
    return {r["ip"] for r in d.execute("SELECT ip FROM mesh_wg_peers").fetchall()}


def next_ip(d):
    net = ipaddress.ip_network(address() + "/24", strict=False)
    used = _used_ips(d)
    for h in list(net.hosts())[1:]:   # .1 is the tunnel's own address
        if str(h) not in used:
            return str(h)
    return ""


def config_text(peer_priv, ip):
    """The WireGuard configuration for one gateway or phone. AllowedIPs is the tunnel address alone, so nothing else a phone does goes through the tunnel."""
    _p, spub = server_key()
    return ("[Interface]\nPrivateKey = " + peer_priv + "\nAddress = " + ip + "/32\n\n[Peer]\nPublicKey = " + spub + "\nEndpoint = " + endpoint() + "\nAllowedIPs = " + address() + "/32\nPersistentKeepalive = 25\n")


def qr_svg(text):
    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2, dark="#000000", light="#ffffff", xmldecl=False, svgns=True, nl=False)
    svg = buf.getvalue().decode()
    m = re.search(r' width="(\d+)" height="(\d+)"', svg)
    if m:   # the code scales to the box it is in (it was drawn at a fixed size and ran over the text beside it)
        svg = svg.replace(m.group(0), f' viewBox="0 0 {m.group(1)} {m.group(2)}" style="width:100%;height:auto;display:block"', 1)
    return svg


def sync():
    """Send the server key and the enabled peers to the mesh service. (ok, note)."""
    d = get_db()
    priv, _pub = server_key()
    peers = [{"key": r["public_key"], "ip": r["ip"], "logins": [r["username"]] if r["username"] and not r["adisabled"] else []}
             for r in d.execute("SELECT p.public_key, p.ip, a.username, COALESCE(a.disabled, false) AS adisabled FROM mesh_wg_peers p LEFT JOIN mesh_accounts a ON a.id = p.login_id WHERE NOT p.disabled ORDER BY p.id").fetchall()]
    ok, r = ms.call("/wg/set", "POST", {"server_key": priv, "peers": peers, "relay_key": relay_key()})
    if not ok:
        return False, r
    return bool(r.get("ok")), ("" if r.get("ok") else str(r.get("error", "")))


def status():
    """{ready, peers: {public key: {handshake, rx, tx}}} from the tunnel container (via the mesh service); ready is false when it has not answered lately."""
    ok, r = ms.call("/wg/status")
    return r if ok and isinstance(r, dict) else {"ready": False, "peers": {}}


def free_logins(community_id):
    """The community's private feed logins that no tunnel peer is bound to yet."""
    return get_db().execute("SELECT a.id, a.username, a.label FROM mesh_accounts a WHERE a.community_id=%s AND a.link='private' AND NOT a.disabled AND a.username <> '' "
                            "AND a.id NOT IN (SELECT login_id FROM mesh_wg_peers WHERE login_id IS NOT NULL) ORDER BY a.id", (community_id,)).fetchall()


def create_peer(community_id, label, user_id, login_id=None):
    """Make a peer for a community's gateway, bound to one of its private feed logins (the peer may use that login and no other). Returns (peer id, configuration text, error); the private key is in the text and nowhere else."""
    d = get_db()
    if not endpoint():
        return None, "", "The tunnel address is not set yet (Settings > Mesh Nodes > Tunnel Address)."
    if not login_id or login_id not in {r["id"] for r in free_logins(community_id)}:
        return None, "", "Choose one of the community's private feed logins that has no tunnel peer yet (make the login first)."
    if d.execute("SELECT COUNT(*) AS n FROM mesh_wg_peers").fetchone()["n"] >= cfg_int("mesh_wg_max_peers"):
        return None, "", f"The site allows {cfg_int('mesh_wg_max_peers')} tunnel peers in all."
    ip = next_ip(d)
    if not ip:
        return None, "", "The tunnel has no free address left."
    priv, pub = new_keypair()
    pid = d.execute("INSERT INTO mesh_wg_peers (community_id, label, public_key, ip, created_by, login_id, private_sealed) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (community_id, (label or "").strip()[:60] or "Gateway", pub, ip, user_id, login_id, vault.seal(priv))).fetchone()["id"]
    d.commit()
    ok, note = sync()
    if not ok:
        d.execute("DELETE FROM mesh_wg_peers WHERE id=%s", (pid,))
        d.commit()
        return None, "", "The tunnel could not be updated right now: " + str(note)
    return pid, config_text(priv, ip), ""


def peer_config(community_id, pid):
    """(peer row, configuration text) for a peer of the community, so the team can open it again later like a member opens their own login. The text is empty for a peer made before the key was kept
    (rekey_peer makes it a new key); None for the row when there is no such peer."""
    row = get_db().execute("SELECT p.id, p.label, p.ip, p.disabled, p.private_sealed, a.username AS login FROM mesh_wg_peers p LEFT JOIN mesh_accounts a ON a.id = p.login_id WHERE p.id=%s AND p.community_id=%s", (pid, community_id)).fetchone()
    if row is None:
        return None, ""
    priv = vault.unseal(row["private_sealed"]) if row["private_sealed"] else ""
    return row, (config_text(priv, row["ip"]) if priv else "")


def rekey_peer(community_id, pid):
    """A new key for a peer (same address and login). The old configuration stops working at once. (ok, note)."""
    d = get_db()
    priv, pub = new_keypair()
    if not d.execute("UPDATE mesh_wg_peers SET public_key=%s, private_sealed=%s WHERE id=%s AND community_id=%s RETURNING id", (pub, vault.seal(priv), pid, community_id)).fetchone():
        return False, "No such peer."
    d.commit()
    return sync()


def peers_of(community_id):
    st = status()
    rows = get_db().execute("SELECT p.id, p.label, p.public_key, p.ip, p.disabled, p.created, a.username AS login FROM mesh_wg_peers p LEFT JOIN mesh_accounts a ON a.id = p.login_id WHERE p.community_id=%s ORDER BY p.id", (community_id,)).fetchall()
    out = []
    for r in rows:
        s = st["peers"].get(r["public_key"]) or {}
        hs = s.get("handshake") or 0
        age = int(time.time()) - hs if hs else None
        state = "never connected" if not hs else ("connected just now" if age < 180 else "last connected " + (f"{age // 60} minutes" if age < 7200 else f"{age // 3600} hours" if age < 172800 else f"{age // 86400} days") + " ago")
        out.append(dict(r, handshake=hs, state=state, rx=s.get("rx") or 0, tx=s.get("tx") or 0))
    return out, bool(st.get("ready"))


def set_disabled(community_id, pid, disabled):
    d = get_db()
    if not d.execute("UPDATE mesh_wg_peers SET disabled=%s WHERE id=%s AND community_id=%s RETURNING id", (bool(disabled), pid, community_id)).fetchone():
        return False, "No such peer."
    d.commit()
    return sync()


def delete_peer(community_id, pid):
    d = get_db()
    if not d.execute("DELETE FROM mesh_wg_peers WHERE id=%s AND community_id=%s RETURNING id", (pid, community_id)).fetchone():
        return False, "No such peer."
    d.commit()
    return sync()
