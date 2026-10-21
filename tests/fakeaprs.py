
import socket, threading, sys
LOG = "/tmp/aprs.log"
def handle(c):
    f = c.makefile("rwb", buffering=0)
    f.write(b"# fake aprs server\r\n")
    login = f.readline().decode().strip()
    call = login.split()[1] if login.startswith("user ") else "?"
    f.write(("# logresp %s %s, server FAKE\r\n" % (call, "unverified" if call.startswith("K0BAD") else "verified")).encode())
    line = f.readline().decode().strip()
    open(LOG, "a").write(login + "\n" + line + "\n")
    c.close()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 14589)); s.listen(5)
while True:
    c, _ = s.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
