"""Protection of secrets kept in the database.

Passwords are never stored: only salted scrypt hashes (Werkzeug). Everything else that is secret but must be read back is encrypted with a key
derived from SECRET_KEY (Fernet: AES-128-CBC with HMAC-SHA-256), so a stolen database or backup file alone does not reveal it:
the authenticator-app secret (two-factor), webhook addresses (they contain a token), and the SMTP password. Recovery codes are stored as a keyed
hash (HMAC-SHA-256 with a key derived from SECRET_KEY), so even their short alphabet cannot be brute-forced from a stolen database.

Values written by this module start with "enc1:"; anything without that prefix is treated as legacy plain text, read as is, and encrypted
the next time the portal starts (`encrypt_existing`). If SECRET_KEY changes the sealed values can no longer be read (they come back empty);
administrators can then reset two-factor for people, and the command `python -m app.cli reset-2fa <user>` does it from the server.
"""
import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

PREFIX = "enc1:"


def _key(purpose):
    return hashlib.sha256((purpose + "\0" + current_app.config["SECRET_KEY"]).encode()).digest()


def _fernet():
    return Fernet(base64.urlsafe_b64encode(_key("vault")))


def seal(text):
    """Encrypt for storage. Empty stays empty."""
    if not text:
        return ""
    if text.startswith(PREFIX):
        return text
    return PREFIX + _fernet().encrypt(text.encode()).decode()


def unseal(value):
    """The plain text of a stored value; '' if it cannot be decrypted (the SECRET_KEY changed)."""
    if not value:
        return ""
    if not value.startswith(PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return ""


def keyed_hash(text, purpose="recovery"):
    """A hash nobody can compute without SECRET_KEY."""
    return hmac.new(_key(purpose), text.encode(), hashlib.sha256).hexdigest()


def encrypt_existing(conn):
    """Seal any secret that an older version stored as plain text. Safe to run on every start."""
    n = 0
    for table, col, key in (("users", "totp_secret", "id"), ("users", "webhook_url", "id")):
        for r in conn.execute(f"SELECT {key} AS k, {col} AS v FROM {table} WHERE {col} <> '' AND {col} NOT LIKE %s", (PREFIX + "%",)).fetchall():
            v = r["v"] if isinstance(r, dict) else r[1]
            k = r["k"] if isinstance(r, dict) else r[0]
            conn.execute(f"UPDATE {table} SET {col}=%s WHERE {key}=%s", (seal(v), k))
            n += 1
    # private message text: sealed at rest (the old email-style messages, the old direct and group chats and the old private threads)
    for table, cols in (("conv_messages", ("body",)), ("pm_messages", ("body",)), ("mail_messages", ("subject", "body")), ("chat_rooms", ("discord_webhook",)), ("my_flights", ("notes",)), ("my_flight_pax", ("name",))):
        for col in cols:
            while True:
                rows = conn.execute(f"SELECT id AS k, {col} AS v FROM {table} WHERE {col} <> '' AND {col} NOT LIKE %s LIMIT 500", (PREFIX + "%",)).fetchall()
                if not rows:
                    break
                for r in rows:
                    conn.execute(f"UPDATE {table} SET {col}=%s WHERE id=%s", (seal(r["v"] if isinstance(r, dict) else r[1]), r["k"] if isinstance(r, dict) else r[0]))
                    n += 1
    from .settings import SEALED_SETTINGS
    for key in sorted(SEALED_SETTINGS):   # settings that hold a secret (Settings pages): sealed in place
        r = conn.execute("SELECT value FROM settings WHERE key=%s AND value <> '' AND value NOT LIKE %s", (key, PREFIX + "%")).fetchone()
        if r is not None:
            conn.execute("UPDATE settings SET value=%s WHERE key=%s", (seal(r["value"] if isinstance(r, dict) else r[0]), key))
            n += 1
    return n
