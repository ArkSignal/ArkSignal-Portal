"""The site as an OpenID Connect provider, so the Matrix server (Synapse) signs members in with their ArkSignal account.

Nothing is built on an identity library: the flow is the plain authorization code flow with PKCE. Matrix sends the member's browser to /oidc/authorize; if they are not signed in they sign in on the site's own login
page first (password, two-factor and passkeys all still apply, and a disabled or locked account cannot get through); the site then sends the browser back to Matrix with a one-time code, and Matrix swaps the code at
/oidc/token for an access token and an ID token signed with the site's own RSA key (published at /oidc/jwks). /oidc/userinfo answers with who the token belongs to.

Kept on purpose: only the one registered client (Settings > Matrix: client ID and its sealed secret) may ask; its redirect addresses are matched exactly; a code works once and for a short time and is stored only as a
hash; tokens are stored as hashes; the signing key and the client secret are sealed in the database like every other secret; the account number (`sub`) is the member's id, never the user name or email, so a
renamed member keeps their Matrix identity; the claims are the user name, display name and email only (never the private legal names on the Profile). Everything the member does here is audit-logged."""
import base64
import json
import secrets
import time
from hashlib import sha256

from flask import Blueprint, abort, jsonify, redirect, request, session, url_for
from flask_login import current_user, login_required

from . import csrf
from .admin import permission_required
from .audit import audit
from .db import get_db
from .models import User
from .settings import cfg, cfg_int, get_secret, has_secret, set_secret

bp = Blueprint("oidc", __name__)

SCOPES = ("openid", "profile", "email")


def on():
    return cfg("matrix_enabled") == "1"


def _need():
    if not on():
        abort(404)


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64ud(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issuer():
    """The site's name in sign-in answers: the address saved for it, else https:// and the Main Address, else the address this request came to."""
    fixed = cfg("matrix_issuer").strip().rstrip("/")
    if fixed:
        return fixed
    host = (cfg("main_address") or "").strip().lower()
    return ("https://" + host) if host else request.url_root.rstrip("/")


issuer_for_service = issuer


def _hash(value):
    return sha256(value.encode()).hexdigest()


# ------------------------------------------------------------------ keys and the client

def signing_key():
    """The site's RSA private key (made on first use, kept sealed)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    pem = get_secret("oidc_signing_key")
    if not pem:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        set_secret("oidc_signing_key", pem)
        get_db().commit()
        return key
    return serialization.load_pem_private_key(pem.encode(), password=None)


def key_id():
    pub = signing_key().public_key().public_numbers()
    return sha256(pub.n.to_bytes(256, "big")).hexdigest()[:16]


def client_secret(create=False):
    s = get_secret("oidc_client_secret")
    if not s and create:
        s = secrets.token_urlsafe(40)
        set_secret("oidc_client_secret", s)
        get_db().commit()
    return s


def automatic_return():
    """The address the Matrix server sends people back to after they sign in: always the Matrix Address plus the fixed path Synapse uses, so nobody has to type it."""
    base = cfg("matrix_public_url").strip().rstrip("/")
    return base + "/_synapse/client/oidc/callback" if base else ""


def redirect_uris():
    """The automatic return address plus any extra ones an administrator listed."""
    extra = [x.strip() for x in cfg("matrix_redirect_uris").replace("\n", ",").split(",") if x.strip()]
    auto = automatic_return()
    return ([auto] if auto and auto not in extra else []) + extra


def jwks():
    pub = signing_key().public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": key_id(), "n": b64u(pub.n.to_bytes(256, "big")), "e": b64u(pub.e.to_bytes(3, "big"))}]}


def sign_jwt(claims):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    head = b64u(json.dumps({"alg": "RS256", "typ": "JWT", "kid": key_id()}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    sig = signing_key().sign((head + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256())
    return head + "." + body + "." + b64u(sig)


def user_claims(user, scopes):
    c = {"sub": str(user.id)}
    if "profile" in scopes:
        c["preferred_username"] = user.username
        c["name"] = (user.row.get("display_name") or user.username)
    if "email" in scopes and user.row.get("email"):
        c["email"] = user.row["email"]
        c["email_verified"] = bool(user.row.get("email_verified"))
    return c


# ------------------------------------------------------------------ discovery and keys

@bp.route("/.well-known/openid-configuration")
def discovery():
    _need()
    base = issuer()
    return jsonify({
        "issuer": base, "authorization_endpoint": base + "/oidc/authorize", "token_endpoint": base + "/oidc/token", "userinfo_endpoint": base + "/oidc/userinfo", "jwks_uri": base + "/oidc/jwks",
        "response_types_supported": ["code"], "subject_types_supported": ["public"], "id_token_signing_alg_values_supported": ["RS256"], "scopes_supported": list(SCOPES),
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"], "code_challenge_methods_supported": ["S256"], "grant_types_supported": ["authorization_code"],
        "claims_supported": ["sub", "preferred_username", "name", "email", "email_verified", "iss", "aud", "exp", "iat", "nonce", "auth_time"]})


@bp.route("/oidc/jwks")
def keys():
    _need()
    r = jsonify(jwks())
    r.headers["Cache-Control"] = "public, max-age=300"
    return r


# ------------------------------------------------------------------ authorize

def _error_page(message):
    return message, 400, {"Content-Type": "text/plain; charset=utf-8"}


def _back(redirect_uri, **q):
    from urllib.parse import urlencode
    return redirect(redirect_uri + ("&" if "?" in redirect_uri else "?") + urlencode({k: v for k, v in q.items() if v}), 302)


@bp.route("/oidc/authorize")
def authorize():
    _need()
    a = request.args
    uri = a.get("redirect_uri", "")
    if a.get("client_id") != cfg("matrix_client_id") or uri not in redirect_uris():
        return _error_page("This sign-in request is not from a registered application.")
    state = a.get("state", "")
    if a.get("response_type") != "code":
        return _back(uri, error="unsupported_response_type", state=state)
    scopes = [s for s in a.get("scope", "").split() if s in SCOPES]
    if "openid" not in scopes:
        return _back(uri, error="invalid_scope", state=state)
    challenge = a.get("code_challenge", "")
    if challenge and a.get("code_challenge_method", "plain") != "S256":
        return _back(uri, error="invalid_request", state=state)
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
    user = User.get(current_user.id)
    if user is None or user.disabled:
        return _back(uri, error="access_denied", state=state)
    from . import access, matrix
    if not access.allowed("matrix") or matrix.is_locked(user.id):
        return _back(uri, error="access_denied", state=state)
    code = secrets.token_urlsafe(32)
    d = get_db()
    d.execute("DELETE FROM oidc_codes WHERE expires < now()")
    d.execute("INSERT INTO oidc_codes (code_hash, user_id, redirect_uri, nonce, challenge, scope, expires) VALUES (%s,%s,%s,%s,%s,%s, now() + make_interval(secs => %s))",
              (_hash(code), user.id, uri, a.get("nonce", "")[:200], challenge[:200], " ".join(scopes), cfg_int("oidc_code_seconds")))
    d.commit()
    audit("oidc.authorize", target=user.username, detail="Matrix sign-in")
    return _back(uri, code=code, state=state)


# ------------------------------------------------------------------ token and userinfo

def _token_error(code, status=400):
    r = jsonify({"error": code})
    r.status_code = status
    r.headers["Cache-Control"] = "no-store"
    return r


def _client_ok():
    cid, secret = request.form.get("client_id", ""), request.form.get("client_secret", "")
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            from urllib.parse import unquote
            cid, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
            cid, secret = unquote(cid), unquote(secret)
        except Exception:
            return False
    real = client_secret()
    return bool(real) and cid == cfg("matrix_client_id") and secrets.compare_digest(secret.encode(), real.encode())


@bp.route("/oidc/token", methods=["POST"])
@csrf.exempt
def token():
    _need()
    if not _client_ok():
        return _token_error("invalid_client", 401)
    if request.form.get("grant_type") != "authorization_code":
        return _token_error("unsupported_grant_type")
    d = get_db()
    row = d.execute("DELETE FROM oidc_codes WHERE code_hash=%s AND expires > now() RETURNING *", (_hash(request.form.get("code", "")),)).fetchone()   # one use only
    d.commit()
    if row is None or row["redirect_uri"] != request.form.get("redirect_uri", ""):
        return _token_error("invalid_grant")
    if row["challenge"]:
        verifier = request.form.get("code_verifier", "")
        if not verifier or b64u(sha256(verifier.encode()).digest()) != row["challenge"]:
            return _token_error("invalid_grant")
    user = User.get(row["user_id"])
    if user is None or user.disabled:
        return _token_error("invalid_grant")
    scopes = row["scope"].split()
    now = int(time.time())
    life = cfg_int("oidc_token_seconds")
    access_token = secrets.token_urlsafe(40)
    d.execute("DELETE FROM oidc_tokens WHERE expires < now()")
    d.execute("INSERT INTO oidc_tokens (token_hash, user_id, scope, expires) VALUES (%s,%s,%s, now() + make_interval(secs => %s))", (_hash(access_token), user.id, row["scope"], life))
    d.commit()
    claims = {"iss": issuer(), "aud": cfg("matrix_client_id"), "iat": now, "exp": now + life, "auth_time": now, **user_claims(user, scopes)}
    if row["nonce"]:
        claims["nonce"] = row["nonce"]
    r = jsonify({"access_token": access_token, "token_type": "Bearer", "expires_in": life, "scope": row["scope"], "id_token": sign_jwt(claims)})
    r.headers["Cache-Control"] = "no-store"
    return r


@bp.route("/oidc/userinfo", methods=["GET", "POST"])
@csrf.exempt
def userinfo():
    _need()
    auth = request.headers.get("Authorization", "")
    tok = auth[7:] if auth.startswith("Bearer ") else ""
    row = get_db().execute("SELECT * FROM oidc_tokens WHERE token_hash=%s AND expires > now()", (_hash(tok),)).fetchone() if tok else None
    user = User.get(row["user_id"]) if row else None
    if user is None or user.disabled:
        r = jsonify({"error": "invalid_token"})
        r.status_code = 401
        r.headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
        return r
    return jsonify(user_claims(user, row["scope"].split()))


# ------------------------------------------------------------------ Admin

@bp.route("/admin/matrix/secret", methods=["POST"])
@login_required
@permission_required("manage_settings")
def admin_new_secret():
    """Make a new client secret (the old one stops working at once). It is shown once; the Matrix server's configuration must be given the new one."""
    set_secret("oidc_client_secret", secrets.token_urlsafe(40))
    get_db().commit()
    audit("oidc.secret_made", target="Matrix client secret")
    return jsonify({"ok": True, "client_id": cfg("matrix_client_id"), "secret": client_secret(), "message": "A new client secret is made. It is shown only now."})
