"""Outgoing email through the SMTP server configured on the admin SMTP page."""
import smtplib
import socket
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage

from .settings import get_secret, get_setting

TIMEOUT_SECONDS = 10

# security -> (default port, label)
MODES = {
    "starttls": (587, "STARTTLS"),
    "ssl": (465, "SSL/TLS"),
    "none": (25, "no encryption"),
}


def smtp_config():
    return {
        "host": get_setting("smtp_host").strip(),
        "port": get_setting("smtp_port").strip(),
        "security": get_setting("smtp_security", "starttls"),
        "skip_cert": get_setting("smtp_skip_cert") == "1",
        "username": get_setting("smtp_username").strip(),
        "password": get_secret("smtp_password"),
        "from": get_setting("smtp_from").strip(),
    }


def smtp_configured():
    c = smtp_config()
    return bool(c["host"] and c["from"])


def send_mail(recipient, subject, body):
    """Send one message. Returns (ok, message); never raises."""
    c = smtp_config()
    host, sender, recipient = c["host"], c["from"], (recipient or "").strip()
    security = c["security"] if c["security"] in MODES else "starttls"
    default_port, mode_label = MODES[security]
    skip_verify = security != "none" and c["skip_cert"]
    if skip_verify:
        mode_label += ", certificate not checked"
    try:
        port = int(c["port"] or default_port)
    except ValueError:
        return False, "SMTP Port is not a number. Fix it, save, and try again."
    missing = [n for n, v in (("SMTP Host", host), ("From Address", sender)) if not v]
    if missing:
        return False, f"Set {' and '.join(missing)} and save before sending."
    if not recipient:
        return False, "Enter an address to send to."

    message = EmailMessage()
    try:
        message["From"] = sender
        message["To"] = recipient
    except ValueError:
        return False, "The From or To address is not a valid email address."
    message["Subject"] = subject
    message.set_content(body)

    hint = _mode_hint(security, port)
    try:
        if security == "ssl":
            conn = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS, context=_tls_context(skip_verify))
        else:
            conn = smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS)
        with conn as smtp:
            smtp.ehlo()
            if security == "starttls":
                smtp.starttls(context=_tls_context(skip_verify))
                smtp.ehlo()
            if c["username"]:
                smtp.login(c["username"], c["password"])
            smtp.send_message(message)
    except smtplib.SMTPNotSupportedError as e:
        if "AUTH" in str(e).upper():
            return False, (f"{host}:{port} does not accept a login"
                           + (" over an unencrypted connection" if security == "none" else "")
                           + ". Clear the SMTP Username if this server does not need one.")
        return False, (f"{host}:{port} does not offer an encrypted (STARTTLS) connection. If this server does not "
                       "encrypt, set Connection Security to None; otherwise check the port.")
    except smtplib.SMTPAuthenticationError as e:
        return False, (f"{host} rejected the username and password ({_reply(e)}). "
                       "For Gmail, use an App Password rather than the account password.")
    except smtplib.SMTPRecipientsRefused:
        return False, f"{host} refused the recipient address ({recipient}). Check it for typos."
    except smtplib.SMTPSenderRefused as e:
        return False, (f"{host} refused the From Address ({sender}): {_reply(e)}. "
                       "Many providers only allow their own addresses.")
    except ssl.SSLCertVerificationError as e:
        return False, (f"{host}'s security certificate could not be verified ({e.verify_message or e.reason}). "
                       "If this is an internal server you trust, turn on Skip Certificate Check.")
    except ssl.SSLError as e:
        if "WRONG_VERSION" in str(e.reason or e).upper():
            return False, (f"{host}:{port} is not using SSL/TLS on that port. Set Connection Security to STARTTLS "
                           "(usually port 587) or None (unencrypted, usually port 25) to match the server.")
        return False, f"The encrypted connection to {host}:{port} failed ({e.reason or e}). {hint}".strip()
    except socket.gaierror:
        return False, f"Could not find a server named {host}. Check the SMTP Host for typos."
    except (TimeoutError, socket.timeout):
        return False, f"{host}:{port} did not answer within {TIMEOUT_SECONDS} seconds. {hint or _silence_hint(security)}".strip()
    except ConnectionRefusedError:
        return False, f"{host}:{port} refused the connection. Check the host and port. {hint}".strip()
    except smtplib.SMTPServerDisconnected as e:
        if "timed out" in str(e):
            return False, f"{host}:{port} did not answer within {TIMEOUT_SECONDS} seconds. {hint or _silence_hint(security)}".strip()
        return False, f"{host}:{port} closed the connection unexpectedly. {hint or _silence_hint(security)}".strip()
    except smtplib.SMTPException as e:
        return False, f"{host} reported an error: {_reply(e)}."
    except OSError as e:
        return False, f"Could not reach {host}:{port} ({e.strerror or e})."
    return True, f"Sent to {recipient} via {host}:{port} ({mode_label}). The server accepted it."


def send_test_email(recipient):
    ok, msg = send_mail(
        recipient, "ArkSignal test email",
        "This is a test message from the ArkSignal portal.\n\n"
        "If you are reading it, the SMTP settings work.\n\n"
        f"Sent {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.\n")
    if ok:
        msg += " Check that inbox (and spam) to confirm it arrived."
    return ok, msg


def _tls_context(skip_verify):
    ctx = ssl.create_default_context()
    if skip_verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _mode_hint(security, port):
    if security == "starttls" and port == 465:
        return "Port 465 normally needs Connection Security set to SSL/TLS."
    if security == "ssl" and port in (587, 25):
        return f"Port {port} normally needs Connection Security set to {'STARTTLS' if port == 587 else 'None'}."
    if security == "none" and port in (465, 587):
        return f"Port {port} normally needs encryption. Set Connection Security to {'SSL/TLS' if port == 465 else 'STARTTLS'}."
    if security != "none" and port == 25:
        return "Port 25 is usually unencrypted. Try Connection Security set to None."
    return ""


def _silence_hint(security):
    if security == "starttls":
        return "If this server uses SSL/TLS from the start (usually port 465), set Connection Security to SSL/TLS."
    if security == "ssl":
        return "If this server uses STARTTLS or no encryption, change Connection Security to match."
    return "If this server requires encryption, set Connection Security to STARTTLS or SSL/TLS."


def _reply(e):
    code = getattr(e, "smtp_code", None)
    text = getattr(e, "smtp_error", None) or str(e)
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = " ".join(str(text).split())[:160]
    return f"{code} {text}" if code else text
