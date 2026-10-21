"""Sign in with Google (OpenID Connect, the authorization code flow with PKCE).

A member links their Google account on My Account > Google Sign-In; from then on the login page's Sign In with Google button signs them in without a password. Nothing is built on Google's own libraries: the
site sends the person to Google, Google sends them back with a one-time code, the site swaps the code for a signed ID token with its client secret, and checks that token itself (RS256 signature against Google's
published keys, issuer, audience, expiry, the nonce this sign-in started with, and that Google says the email is verified) before trusting anything in it. The stable Google account number (`sub`), not the email
address, is what an account is linked to, so a changed email at Google cannot hand an account to someone else.

Rules kept on purpose: a Google account is never linked to an existing account just because the email matches (the person signs in with their password first and links it on purpose); a member with two-factor
sign-in still gets the second step; a disabled or locked account cannot sign in this way either; creating an account from a Google sign-in is off unless an administrator turns it on and registration is open.
The endpoints and the client ID are settings; the client secret is kept sealed. Nothing is configured, so nothing is shown, until an administrator enters the client ID and secret from Google Cloud."""
import base64
import json
import re
import secrets
import time
import urllib.parse
import urllib.request
from hashlib import sha256

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user

from . import security
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .cache import cached
from .clientip import client_ip
from .db import get_db
from .models import User, log_login
from .settings import cfg, cfg_int, get_secret, has_secret, set_secret

bp = Blueprint("googleauth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")


def configured():
    return cfg("google_enabled") == "1" and bool(cfg("google_client_id").strip()) and has_secret("google_client_secret")


def _need():
    if not configured():
        abort(404)


def redirect_uri():
    return request.url_root.rstrip("/") + "/login/google/callback"


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64ud(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ------------------------------------------------------------------ checking Google's token

def _http(url, data=None, timeout=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data is not None else None,
                                 headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout or cfg_int("google_timeout_seconds")) as r:
        return json.loads(r.read(1024 * 1024))


def _keys(force=False):
    """{kid: RSA public key} from Google's published signing keys (kept an hour; fetched again when a token names a key we do not have)."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    def load():
        out = {}
        for k in _http(cfg("google_jwks_url")).get("keys", []):
            if k.get("kty") == "RSA" and k.get("kid"):
                out[k["kid"]] = rsa.RSAPublicNumbers(int.from_bytes(b64ud(k["e"]), "big"), int.from_bytes(b64ud(k["n"]), "big")).public_key()
        return out
    return cached(("google_jwks",), load, 0 if force else 3600)


def verify_id_token(token, nonce):
    """The claims of an ID token that checks out, or (None, why)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    try:
        head_b, body_b, sig_b = token.split(".")
        head, claims = json.loads(b64ud(head_b)), json.loads(b64ud(body_b))
    except (ValueError, TypeError):
        return None, "The sign-in answer from Google could not be read."
    if head.get("alg") != "RS256":
        return None, "Google's answer is signed in a way this site does not accept."
    keys = _keys()
    key = keys.get(head.get("kid"))
    if key is None:
        key = _keys(True).get(head.get("kid"))
    if key is None:
        return None, "Google's answer is signed with a key this site does not know."
    def good(k):
        try:
            k.verify(b64ud(sig_b), (head_b + "." + body_b).encode(), padding.PKCS1v15(), hashes.SHA256())
            return True
        except (InvalidSignature, ValueError):
            return False
    if not good(key):   # the keys we kept may be old: fetch them again once before refusing
        fresh = _keys(True).get(head.get("kid"))
        if fresh is None or not good(fresh):
            return None, "Google's answer did not check out (its signature is wrong)."
    if claims.get("iss") not in [x.strip() for x in cfg("google_issuers").split(",") if x.strip()]:
        return None, "Google's answer came from an issuer this site does not accept."
    aud = claims.get("aud")
    if cfg("google_client_id").strip() not in (aud if isinstance(aud, list) else [aud]):
        return None, "Google's answer was made for a different application."
    if float(claims.get("exp", 0)) < time.time() - 60:
        return None, "Google's answer has expired. Please try again."
    if not nonce or claims.get("nonce") != nonce:
        return None, "This sign-in did not start here (the check value did not match). Please try again."
    if not claims.get("sub"):
        return None, "Google's answer has no account number."
    return claims, None


# ------------------------------------------------------------------ starting and finishing a sign-in

def _start(mode, nxt=""):
    _need()
    st = {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24), "verifier": secrets.token_urlsafe(48), "mode": mode, "next": nxt, "at": time.time(),
          "uid": current_user.id if current_user.is_authenticated else None}
    session["goog"] = st
    q = {"client_id": cfg("google_client_id").strip(), "redirect_uri": redirect_uri(), "response_type": "code", "scope": "openid email profile", "state": st["state"], "nonce": st["nonce"],
         "code_challenge": b64u(sha256(st["verifier"].encode()).digest()), "code_challenge_method": "S256", "prompt": "select_account"}
    return redirect(cfg("google_auth_url") + ("&" if "?" in cfg("google_auth_url") else "?") + urllib.parse.urlencode(q), 302)


@bp.route("/login/google")
def login_start():
    if current_user.is_authenticated:
        return redirect(url_for("auth.dashboard"))
    return _start("login", request.args.get("next") or "")


@bp.route("/my/google/link")
@login_required
def link_start():
    return _start("link")


def _bounce(message, mode):
    flash(message, "error")
    return redirect(url_for("googleauth.my_page") if mode == "link" and current_user.is_authenticated else url_for("auth.login"))


def _username_from(email):
    base = re.sub(r"[^A-Za-z0-9._-]", "", email.split("@")[0])[:28] or "member"
    if len(base) < 3:
        base = (base + "user")[:28]
    d, name, n = get_db(), base, 1
    while d.execute("SELECT 1 FROM users WHERE lower(username)=lower(%s)", (name,)).fetchone():
        n += 1
        name = f"{base[:28]}{n}"
    return name


def _finish_login(user, claims, how):
    """Sign the person in as the password form does: the second step first when they use one."""
    d = get_db()
    d.execute("UPDATE google_links SET last_login=now(), email=%s WHERE sub=%s", (claims.get("email", ""), claims["sub"]))
    d.commit()
    nxt = (session.get("goog") or {}).get("next") or ""
    if user.row.get("totp_enabled"):
        session.clear()
        session["pending_2fa"] = {"uid": user.id, "at": time.time(), "next": nxt}
        return redirect(url_for("auth.login_2fa"))
    session.clear()
    user.register_success()
    log_login(user.username, client_ip(), True, how)
    login_user(user, remember=False)
    session.permanent = True
    security.stamp_login()
    security.new_session(user.id)
    from .auth import _safe_next
    return redirect(url_for("auth.change_password") if user.must_change_password else _safe_next(nxt))


@bp.route("/login/google/callback")
def callback():
    _need()
    st = session.pop("goog", None)
    mode = (st or {}).get("mode", "login")
    if security.ip_blocked(client_ip()):
        return _bounce("Too many failed sign-in attempts from your address. Please wait a few minutes and try again.", mode)
    if not st or time.time() - st["at"] > 600 or request.args.get("state") != st["state"]:
        return _bounce("That sign-in took too long or did not start here. Please try again.", mode)
    if request.args.get("error"):
        log_login("", client_ip(), False, "google: " + request.args.get("error", "")[:60])
        return _bounce("Google did not sign you in" + (" (you chose not to allow it)." if request.args.get("error") == "access_denied" else ".") , mode)
    code = request.args.get("code", "")
    if not code:
        return _bounce("Google's answer had no code. Please try again.", mode)
    try:
        tok = _http(cfg("google_token_url"), {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(), "client_id": cfg("google_client_id").strip(),
                                              "client_secret": get_secret("google_client_secret"), "code_verifier": st["verifier"]})
    except Exception as e:
        audit("google.error", target="token", detail=str(e)[:200], actor_name=(current_user.username if current_user.is_authenticated else "visitor"))
        return _bounce("Could not reach Google to finish signing in. Please try again in a moment.", mode)
    claims, err = verify_id_token(str(tok.get("id_token") or ""), st["nonce"])
    if err:
        log_login("", client_ip(), False, "google: token refused")
        audit("google.refused", target="id_token", detail=err, actor_name=(current_user.username if current_user.is_authenticated else "visitor"))
        return _bounce(err, mode)
    verified = claims.get("email_verified") in (True, "true")
    email = (claims.get("email") or "").strip()
    d = get_db()
    link = d.execute("SELECT * FROM google_links WHERE sub=%s", (claims["sub"],)).fetchone()
    if mode == "link":
        if not current_user.is_authenticated or current_user.id != st.get("uid"):
            return _bounce("Sign in first, then link your Google account.", "login")
        if link and link["user_id"] != current_user.id:
            return _bounce("That Google account is already linked to another member.", "link")
        if d.execute("SELECT 1 FROM google_links WHERE user_id=%s AND sub<>%s", (current_user.id, claims["sub"])).fetchone():
            return _bounce("You already have a Google account linked. Unlink it first to link another.", "link")
        d.execute("INSERT INTO google_links (sub, user_id, email, name) VALUES (%s,%s,%s,%s) ON CONFLICT (sub) DO UPDATE SET email=EXCLUDED.email, name=EXCLUDED.name",
                  (claims["sub"], current_user.id, email, (claims.get("name") or "")[:120]))
        d.commit()
        audit("google.link", target=current_user.username, detail=email)
        flash("Your Google account is linked. You can now sign in with it.", "success")
        return redirect(url_for("googleauth.my_page"))
    # signing in
    if link:
        user = User.get(link["user_id"])
        if user is None or user.disabled:
            log_login(user.username if user else "", client_ip(), False, "google: disabled account")
            return _bounce("This account is disabled.", "login")
        if user.is_locked():
            log_login(user.username, client_ip(), False, "google: locked account")
            return _bounce("Too many failed attempts. Try again in a few minutes.", "login")
        return _finish_login(user, claims, "google")
    if cfg("google_link_by_email") == "1" and verified and email:
        # an administrator chose to trust Google's verified email: an account with that very address is linked and signed in (and never a second account made)
        match = d.execute("SELECT id FROM users WHERE lower(email)=lower(%s) AND NOT disabled", (email,)).fetchone()
        if match is not None and not d.execute("SELECT 1 FROM google_links WHERE user_id=%s", (match["id"],)).fetchone():
            user = User.get(match["id"])
            d.execute("INSERT INTO google_links (sub, user_id, email, name) VALUES (%s,%s,%s,%s)", (claims["sub"], user.id, email, (claims.get("name") or "")[:120]))
            d.commit()
            audit("google.autolink", target=user.username, detail=email, actor_name=user.username)
            return _finish_login(user, claims, "google")
    if cfg("google_allow_signup") == "1" and cfg("registration_open") == "1" and verified and email:
        if User.email_taken(email):
            return _bounce("An account with the email address of that Google account already exists. Sign in with your password, then link Google on My Account > Google Sign-In.", "login")
        name = _username_from(email)
        uid = User.create(name, email, secrets.token_urlsafe(32))
        d.execute("INSERT INTO google_links (sub, user_id, email, name, created_by_signup) VALUES (%s,%s,%s,%s,TRUE)", (claims["sub"], uid, email, (claims.get("name") or "")[:120]))
        d.commit()
        audit("google.signup", target=name, detail=email, actor_name=name)
        return _finish_login(User.get(uid), claims, "google signup")
    log_login(email[:60], client_ip(), False, "google: not linked")
    return _bounce("No account is linked to that Google account. Sign in with your password, then link Google on My Account > Google Sign-In." + ("" if verified else " (Google also says that email address is not verified.)"), "login")


# ------------------------------------------------------------------ My Account

def _my_ctx():
    link = get_db().execute("SELECT * FROM google_links WHERE user_id=%s", (current_user.id,)).fetchone()
    return {"link": link, "signup": cfg("google_allow_signup") == "1" and cfg("registration_open") == "1"}


@bp.route("/my/google")
@login_required
def my_page():
    _need()
    return render_template("my_google.html", **_my_ctx())


@bp.route("/my/google/unlink", methods=["POST"])
@login_required
def unlink():
    _need()
    d = get_db()
    row = d.execute("DELETE FROM google_links WHERE user_id=%s RETURNING email", (current_user.id,)).fetchone()
    d.commit()
    if row:
        audit("google.unlink", target=current_user.username, detail=row["email"])
    return action_response(bool(row), "Your Google account is unlinked. Sign in with your password from now on." if row else "No Google account was linked.",
                           redirect_endpoint="googleauth.my_page", fragment_template="partials/_my_google.html", fragment_context=_my_ctx())


# ------------------------------------------------------------------ Admin

def _admin_ctx():
    n = get_db().execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE created_by_signup) AS s FROM google_links").fetchone()
    return {"configured": configured(), "enabled": cfg("google_enabled") == "1", "client_id": cfg("google_client_id"), "secret_saved": has_secret("google_client_secret"), "links": n["n"], "signups": n["s"],
            "redirect": redirect_uri(), "signup_on": cfg("google_allow_signup") == "1", "active": "google"}


@bp.route("/admin/google")
@permission_required("manage_settings")
def admin_page():
    return render_template("admin_google.html", **_admin_ctx())


@bp.route("/admin/google/secret", methods=["POST"])
@permission_required("manage_settings")
def admin_secret():
    value = (request.form.get("secret") or "").strip()
    if request.form.get("clear") == "1":
        set_secret("google_client_secret", "")
        audit("google.secret_cleared", target="client secret")
        return action_response(True, "The client secret is removed. Sign in with Google is off until a new one is saved.", redirect_endpoint="googleauth.admin_page", fragment_template="partials/_admin_google.html", fragment_context=_admin_ctx())
    if len(value) < 8 or len(value) > 200:
        return action_response(False, "Paste the client secret from Google Cloud (it is at least eight characters).", redirect_endpoint="googleauth.admin_page", fragment_template="partials/_admin_google.html", fragment_context=_admin_ctx())
    set_secret("google_client_secret", value)
    audit("google.secret_saved", target="client secret")
    return action_response(True, "The client secret is saved (sealed). It is never shown again.", redirect_endpoint="googleauth.admin_page", fragment_template="partials/_admin_google.html", fragment_context=_admin_ctx())
