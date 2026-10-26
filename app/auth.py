import re
import time
from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from psycopg.errors import UniqueViolation

from .clientip import client_ip
from .audit import audit
from .db import get_db
from .models import User, log_login
from . import security
from .settings import cfg, cfg_int

# Everything user-facing lives under /my/ so the proxy only needs the five planned locations.
bp = Blueprint("auth", __name__, url_prefix="/my")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _safe_next(target):
    """Only allow same-site relative paths after login."""
    if target and target.startswith("/") and not target.startswith("//") and not urlparse(target).netloc:
        return target
    from .landing import target_for
    return target_for(current_user)


@bp.route("/")
@login_required
def dashboard():
    if current_user.must_change_password:
        return redirect(url_for("auth.change_password"))
    from . import stations
    ip = client_ip()
    return render_template("my.html", feeders=stations.my_feeders(current_user.id), candidates=stations.claim_candidates(ip), ip=ip, paste_days=cfg_int("claim_paste_days"))


_DUMMY = []


def _spend_hash_time(password):
    """Do the same work a real password check does, so an unknown name, a disabled account and a locked one take as long to answer as a wrong password (the answer must not tell which accounts exist)."""
    from werkzeug.security import check_password_hash, generate_password_hash
    if not _DUMMY:
        import secrets
        _DUMMY.append(generate_password_hash(secrets.token_urlsafe(24)))
    check_password_hash(_DUMMY[0], password[:1024])


GENERIC_FAIL = "Incorrect username or password. After several wrong tries an account is locked for a few minutes."


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_safe_next(request.args.get("next")))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        ip = client_ip()
        user = User.get_by_login(username)
        if security.ip_blocked(ip):
            time.sleep(0.5)
            flash("Too many failed sign-in attempts from your address. Please wait a few minutes and try again.", "error")
            return render_template("login.html"), 429
        if len(password) > 1024:   # no real password is this long; the hash is not made to chew on it
            password = ""
        if user is None:
            _spend_hash_time(password)
            time.sleep(0.5)
            # an unknown name may be a password typed into the wrong box: keep only a masked trace of it
            log_login(username if cfg("log_unknown_usernames") == "exact" else ((username[:2] + "\u2022" * min(max(len(username) - 2, 0), 6)) if username else ""), ip, False, "unknown username")
            flash(GENERIC_FAIL, "error")
        elif user.disabled:
            _spend_hash_time(password)
            time.sleep(0.5)
            log_login(username, ip, False, "disabled account")
            flash(GENERIC_FAIL, "error")
        elif user.is_locked():
            _spend_hash_time(password)
            time.sleep(0.5)
            log_login(username, ip, False, "locked account")
            flash(GENERIC_FAIL, "error")
        elif not user.check_password(password):
            time.sleep(0.5)
            user.register_failure()
            log_login(username, ip, False, "wrong password")
            flash(GENERIC_FAIL, "error")
        elif user.row.get("totp_enabled"):
            # the password is right; the second step proves it is really them
            session.clear()
            session["pending_2fa"] = {"uid": user.id, "at": time.time(), "next": request.args.get("next") or ""}
            return redirect(url_for("auth.login_2fa"))
        else:
            user.register_success()
            session.clear()   # a new session at sign-in: nothing a visitor had before it carries over
            new_address = user.has_admin_access and not get_db().execute(
                "SELECT 1 FROM login_events WHERE username=%s AND ip=%s AND success AND ts > now() - interval '90 days' LIMIT 1", (user.username, ip)).fetchone()
            log_login(username, ip, True)
            login_user(user, remember=False)
            session.permanent = True  # lifetime comes from Admin > Settings
            security.stamp_login()
            security.new_session(user.id)
            if new_address:
                from .audit import audit
                audit("login.new_address", target=user.username, detail=f"Administrator signed in from an address not seen in the last 90 days: {ip}")
            if user.must_change_password:
                return redirect(url_for("auth.change_password"))
            return redirect(_safe_next(request.args.get("next")))
    return render_template("login.html")


@bp.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    """Second step of sign-in for people with an authenticator app: the 6-digit code, or one of their recovery codes."""
    from . import totp
    pend = session.get("pending_2fa")
    if not pend or time.time() - pend["at"] > 300:
        session.pop("pending_2fa", None)
        flash("Start again: enter your username and password first.", "info")
        return redirect(url_for("auth.login"))
    user = User.get(pend["uid"])
    if user is None or user.disabled:
        session.pop("pending_2fa", None)
        return redirect(url_for("auth.login"))
    ip = client_ip()
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if user.is_locked() or security.ip_blocked(ip):
            flash("Too many failed attempts. Try again in a few minutes.", "error")
            return render_template("login_2fa.html"), 429
        d = get_db()
        from .vault import unseal
        step = totp.verify(unseal(user.row["totp_secret"]), code, user.row["totp_last"])
        used_recovery = False
        if step is None and len(code.replace("-", "").replace(" ", "")) >= 8:
            h = totp.hash_code(code)
            rc = d.execute("SELECT id FROM recovery_codes WHERE user_id=%s AND code_hash=%s AND used_at IS NULL", (user.id, h)).fetchone()
            if rc:
                d.execute("UPDATE recovery_codes SET used_at=now() WHERE id=%s", (rc["id"],))
                used_recovery = True
        if step is None and not used_recovery:
            time.sleep(0.5)
            locked = user.register_failure()
            log_login(user.username, ip, False, "wrong two-factor code")
            flash(f"Too many failed attempts. Account locked for {cfg_int('lockout_minutes')} minutes." if locked else "That code is not right. Check the time on your phone and try again.", "error")
            return render_template("login_2fa.html"), 200
        if step is not None:
            d.execute("UPDATE users SET totp_last=%s WHERE id=%s", (step, user.id))
        d.commit()
        nxt = pend.get("next", "")
        session.clear()
        user.register_success()
        log_login(user.username, ip, True, "two-factor recovery code" if used_recovery else "")
        login_user(user, remember=False)
        session.permanent = True
        security.stamp_login()
        security.new_session(user.id)
        if used_recovery:
            audit("login.recovery_code", target=user.username, detail="a recovery code was used to sign in")
            flash("You signed in with a recovery code. Each code works once; make new ones under Two-Factor.", "info")
        if user.must_change_password:
            return redirect(url_for("auth.change_password"))
        return redirect(_safe_next(nxt))
    return render_template("login_2fa.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("auth.dashboard"))
    if cfg("registration_open") != "1":
        return render_template("placeholder.html", title="Registration Closed",
                               text="New account registration is currently closed. Please see the Contact page to ask for an account."), 403
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not USERNAME_RE.fullmatch(username):
            flash("Username must be 3 to 32 characters: letters, numbers, dot, dash, underscore.", "error")
        elif len(email) > 254 or not EMAIL_RE.match(email):
            flash("Enter a valid email address.", "error")
        elif security.password_error(password):
            flash(security.password_error(password), "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif User.email_taken(email):
            flash("An account with that email address already exists.", "error")
        else:
            try:
                User.create(username, email, password)
            except UniqueViolation:
                get_db().rollback()
                flash("That username or email address is already in use.", "error")
            else:
                audit("user.register", target=username, actor_name=username)
                flash("Account created. Please sign in.", "success")
                return redirect(url_for("auth.login"))
    return render_template("register.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    try:
        security.end_session()
    except Exception:
        pass
    logout_user()
    flash("Signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        if not current_user.check_password(current):
            flash("Current password is incorrect.", "error")
        elif security.password_error(new):
            flash(security.password_error(new), "error")
        elif new != confirm:
            flash("Passwords do not match.", "error")
        else:
            current_user.set_password(new, must_change=False)   # every other sign-in of this account ends
            security.new_session(current_user.id)               # this one carries on, as a new tracked sign-in
            audit("user.password_change", target=current_user.username)
            flash("Password updated.", "success")
            return redirect(url_for("auth.dashboard"))
    return render_template("password.html", forced=current_user.must_change_password)


# ---------------------------------------------------------------- forgotten password

def _reset_page(**kw):
    from .mailer import smtp_configured
    return render_template("forgot.html", email_ready=smtp_configured(), by_station=cfg("reset_by_station") == "1", **kw)


@bp.route("/forgot")
def forgot():
    if current_user.is_authenticated:
        return redirect(url_for("auth.change_password"))
    return _reset_page()


@bp.route("/forgot/email", methods=["POST"])
def forgot_email():
    """Send a reset link. The answer is the same whether or not the account exists, so the form cannot be used to find accounts."""
    from . import accounts
    from .mailer import send_mail, smtp_configured
    login = request.form.get("login", "").strip()[:254]
    ip = client_ip()
    generic = "If an account matches, an email with a reset link is on its way. The link works once and expires soon."
    if not smtp_configured():
        flash("Email is not set up on this site yet. Use a station you own, or ask for help on the Contact page.", "error")
        return redirect(url_for("auth.forgot"))
    if not login or not accounts.allowed(ip, login):
        accounts.note_attempt(ip, login, "email", False)
        flash("Too many recovery attempts. Please wait a while and try again.", "error")
        return redirect(url_for("auth.forgot"))
    user = User.get_by_login(login)
    accounts.note_attempt(ip, login, "email", bool(user))
    if user is not None and not user.disabled:
        token = accounts.make_token(user.id)
        from .seo import base_url
        link = base_url() + url_for("auth.reset", token=token)   # the site's own address (Settings), not whatever Host header the request carried
        site = cfg("site_name")
        send_mail(user.email, f"{site}: reset your password",
                  f"Someone asked to reset the password for the account {user.username} on {site}.\n\n"
                  f"To choose a new password, open this link (it works once and expires in {cfg_int('reset_token_minutes')} minutes):\n{link}\n\n"
                  f"If you did not ask for this, ignore this email; your password has not changed.\n")
        audit("password.reset_requested", target=user.username, actor_name=user.username)
    time.sleep(0.3)
    flash(generic, "info")
    return redirect(url_for("auth.forgot"))


@bp.route("/forgot/station", methods=["POST"])
def forgot_station():
    """Reset by proving ownership of a claimed station: the UUID is shown only to the station's owner."""
    from . import accounts
    if cfg("reset_by_station") != "1":
        flash("Resetting with a station is turned off on this site.", "error")
        return redirect(url_for("auth.forgot"))
    login = request.form.get("login", "").strip()[:254]
    uuid = request.form.get("uuid", "").strip().lower()
    new, confirm = request.form.get("new", ""), request.form.get("confirm", "")
    ip = client_ip()
    if not accounts.allowed(ip, login):
        accounts.note_attempt(ip, login, "station", False)
        flash("Too many recovery attempts. Please wait a while and try again.", "error")
        return redirect(url_for("auth.forgot"))
    if security.password_error(new):
        flash(security.password_error(new), "error")
        return redirect(url_for("auth.forgot"))
    if new != confirm:
        flash("The new passwords do not match.", "error")
        return redirect(url_for("auth.forgot"))
    user = User.get_by_login(login)
    ok = bool(user and not user.disabled and accounts.owns_station(user.id, uuid))
    accounts.note_attempt(ip, login, "station", ok)
    if not ok:
        time.sleep(0.8)
        flash("Those details do not match an account and a station it owns.", "error")
        return redirect(url_for("auth.forgot"))
    user.set_password(new)
    audit("password.reset_station", target=user.username, actor_name=user.username, detail=uuid[:8])
    flash("Your password has been changed. You can sign in now.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    from . import accounts
    uid = accounts.find_token(token)
    if uid is None:
        return render_template("placeholder.html", title="Reset Link Not Valid",
                               text="This password reset link has expired or was already used. Ask for a new one from the Forgot Password page."), 400
    if request.method == "POST":
        new, confirm = request.form.get("new", ""), request.form.get("confirm", "")
        if security.password_error(new):
            flash(security.password_error(new), "error")
        elif new != confirm:
            flash("The new passwords do not match.", "error")
        else:
            user = User.get(uid)
            user.set_password(new)
            accounts.use_token(token)
            audit("password.reset_email", target=user.username, actor_name=user.username)
            flash("Your password has been changed. You can sign in now.", "success")
            return redirect(url_for("auth.login"))
    return render_template("reset.html", token=token)


@bp.route("/keepalive", methods=["POST"])
@login_required
def keepalive():
    """The "Stay Signed In" button and the page's quiet pings while someone is working: the request itself counts as activity."""
    return ("", 204)
