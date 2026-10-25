"""Runs inside the portal container (tests/sechard_test.py): the outgoing-address rule (safenet), the mail security rule and the log redaction, with nothing sent anywhere. Prints one JSON line."""
import json, os, socket, sys
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["DISABLE_MESH_SYNC"] = "1"
from app import create_app
app = create_app()
out = {}
with app.app_context():
    from app import safenet, mailer, logredact
    from app.settings import set_setting
    # ---- safenet: which addresses may be contacted
    def blocked(ip, allow=False):
        try:
            safenet.check_ip(ip, allow); return False
        except safenet.Blocked:
            return True
    out["private_loopback_linklocal_shared_blocked"] = all(blocked(i) for i in ("127.0.0.1", "10.1.2.3", "192.168.1.5", "172.16" + ".0.9", "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1"))
    out["public_allowed"] = not blocked("8.8.8.8") and not blocked("2606:4700:4700::1111")
    out["private_allowed_only_when_the_administrator_allows_it"] = not blocked("10.1.2.3", True) and not blocked("127.0.0.1", True) and blocked("0.0.0.0", True)
    # ---- a name is resolved once and the connection goes to the checked address (a name that answers differently the second time gets nothing)
    calls = []
    real = socket.getaddrinfo
    answers = [[(2, 1, 6, "", ("93.184.216.34", 80))], [(2, 1, 6, "", ("127.0.0.1", 80))]]
    def fake(host, port, *a, **k):
        if host == "rebind.test":
            calls.append(host)
            return answers[min(len(calls) - 1, 1)]
        return real(host, port, *a, **k)
    socket.getaddrinfo = fake
    created = []
    real_cc = socket.create_connection
    def fake_cc(addr, *a, **k):
        created.append(addr[0]); raise OSError("stop here: no real connection in the test")
    socket.create_connection = fake_cc
    try:
        safenet.opener().open("http://rebind.test/", timeout=2)
    except Exception:
        pass
    out["a_name_is_resolved_once_and_the_checked_address_is_used"] = len(calls) == 1 and created == ["93.184.216.34"]
    socket.create_connection = real_cc
    answers[0] = [(2, 1, 6, "", ("10.0.0.5", 80))]
    calls.clear(); created.clear()
    try:
        safenet.opener().open("http://rebind.test/", timeout=2)
        refused = False
    except Exception as e:
        refused = "private" in str(e) or isinstance(getattr(e, "reason", None), safenet.Blocked)
    out["a_name_that_points_inside_is_refused_before_any_connection"] = refused and not created
    socket.getaddrinfo = real
    try:
        safenet.opener().open("http://localhost:9/", timeout=2)
        loop = False
    except Exception as e:
        loop = "private" in str(e) or isinstance(getattr(e, "reason", None), safenet.Blocked)
    out["localhost_is_refused"] = loop
    from app import webhooks
    out["a_webhook_to_a_private_address_is_refused"] = webhooks.check_url("https://10.0.0.5/hook")[1] is not None and webhooks.check_url("https://127.0.0.1/x")[1] is not None
    ok, msg = webhooks.send("https://127.0.0.1/hook", "json", "t", [], "s")
    out["sending_to_loopback_is_refused_at_send_time"] = ok is False
    # ---- mail: nothing private crosses a network unencrypted
    for k, v in (("smtp_host", "mail.example.net"), ("smtp_from", "x@example.net"), ("smtp_security", "none"), ("smtp_username", "u")):
        set_setting(k, v)
    ok, msg = mailer.send_mail("a@example.com", "s", "b")
    out["plain_smtp_with_a_password_is_refused"] = (not ok) and "encrypted" in msg
    set_setting("smtp_username", "")
    ok, msg = mailer.send_mail("a@example.com", "s", "b")
    out["plain_smtp_to_a_public_server_is_refused"] = (not ok) and "your own network" in msg
    for k in ("smtp_host", "smtp_from", "smtp_security", "smtp_username"):
        from app.db import get_db
        get_db().execute("DELETE FROM settings WHERE key=%s", (k,))
    get_db().commit()
    # ---- the access log keeps secrets out
    r = logredact.redact
    out["log_hides_tokens_in_paths_and_queries"] = (r("POST /sat/push/AbCdEfGhIjKlMnOpQrStUvWx012345 HTTP/1.1") == "POST /sat/push/<hidden> HTTP/1.1" and "<hidden>" in r("GET /my/reset/" + "x" * 43 + " HTTP/1.1")
        and r("GET /login/google/callback?code=4%2Fabc&state=zz HTTP/1.1") == "GET /login/google/callback?code=<hidden>&state=<hidden> HTTP/1.1" and r("GET /share/flights/" + "Ab-_" * 11 + "/f/3 HTTP/1.1") == "GET /share/flights/<hidden>/f/3 HTTP/1.1")
    out["log_keeps_ordinary_addresses"] = r("GET /flight/a1b2c3?x=1 HTTP/1.1") == "GET /flight/a1b2c3?x=1 HTTP/1.1" and r("GET /my/flights/12 HTTP/1.1") == "GET /my/flights/12 HTTP/1.1"
    out["the_gunicorn_logger_class_exists"] = logredact.RedactingLogger is not None
print(json.dumps(out))
