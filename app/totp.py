"""Two-factor sign-in with an authenticator app (time-based one-time passwords, RFC 6238) and one-time recovery codes.

Standard parameters (SHA-1, 6 digits, 30 seconds) so Google Authenticator, Microsoft Authenticator, Authy, 1Password, Bitwarden and the
like all work. A code can be used only once (the last accepted time step is remembered), and one step either side is allowed for clock drift.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    n = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(n).zfill(DIGITS)


def code_now(secret, at=None):
    return _code(secret, int((at if at is not None else time.time()) // STEP))


def verify(secret, code, last_used=0, at=None):
    """The accepted time step (an int) when the code is right and fresh, otherwise None."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    now = int((at if at is not None else time.time()) // STEP)
    for step in (now, now - 1, now + 1):
        if step > last_used and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def uri(secret, account, issuer):
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def grouped(secret):
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def new_recovery_codes(n=10):
    """Codes like 4k7m-9q2x-bc5d (12 characters, about 59 bits); the portal keeps only a keyed hash of each."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)) for _ in range(n)]


def hash_code(code):
    from .vault import keyed_hash
    return keyed_hash("".join(ch for ch in code.lower() if ch.isalnum()), "recovery")
