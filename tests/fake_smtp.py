"""Temporary test mail servers on 127.0.0.1 -- one personality per port:
  2525 STARTTLS offered   2526 implicit TLS (smtps)   2527 plain, no encryption at all
Accepts any AUTH PLAIN except password 'wrong'. Each accepted message is appended to
smtp_received.txt, tagged with the port and whether the session was encrypted. Test-only."""
import base64, socket, ssl, threading

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain("fake_smtp_cert.pem", "fake_smtp_key.pem")
KIND = {2525: "starttls", 2526: "ssl", 2527: "plain"}


def handle(conn, port):
    kind = KIND[port]
    tls = False
    try:
        if kind == "ssl":
            conn = ctx.wrap_socket(conn, server_side=True); tls = True
        f = conn.makefile("rwb", buffering=0)
        send = lambda s: (f.write((s + "\r\n").encode()), f.flush())
        send("220 fake ESMTP")
        while True:
            raw = f.readline()
            if not raw:
                return
            line = raw.decode().strip(); up = line.upper()
            if up.startswith("EHLO"):
                send("250-fake")
                if kind == "starttls" and not tls: send("250-STARTTLS")
                send("250 AUTH PLAIN")
            elif up == "STARTTLS" and kind == "starttls":
                send("220 go ahead")
                conn = ctx.wrap_socket(conn, server_side=True); f = conn.makefile("rwb", buffering=0); tls = True
            elif up.startswith("AUTH PLAIN"):
                tokens = line.split()
                if len(tokens) < 3:          # curl style: ask for the credentials, then read them
                    send("334 ")
                    cred = f.readline().decode().strip()
                else:                        # smtplib style: credentials inline
                    cred = tokens[2]
                parts = base64.b64decode(cred).split(b"\0")
                send("535 5.7.8 bad credentials" if parts[2] == b"wrong" else "235 ok")
            elif up == "DATA":
                send("354 go"); body = []
                while True:
                    l = f.readline().decode()
                    if l in (".\r\n", ""): break
                    body.append(l)
                open("smtp_received.txt", "a").write(f"[port {port} {kind} encrypted={tls}]\n" + "".join(body) + "\n-----\n")
                send("250 queued")
            elif up == "QUIT":
                send("221 bye"); return
            else:
                send("250 ok")
    except (ssl.SSLError, OSError):
        pass
    finally:
        try: conn.close()
        except OSError: pass


def listen(port):
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port)); srv.listen(5)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c, port), daemon=True).start()


for p in KIND:
    threading.Thread(target=listen, args=(p,), daemon=True).start()
threading.Event().wait()
