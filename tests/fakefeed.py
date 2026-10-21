
import socket, threading
LINES = [b"W5REM-9>APRS,TCPIP*::KD5TST   :hello there{12\r\n", b"W5REM-9>APRS,TCPIP*::KD5TST   :hello there{12\r\n", b"W5OTH>APRS,TCPIP*::KD5TST   :from a stranger{1\r\n",
         b"W5REM-9>APRS,TCPIP*::K1OTHER  :not for me{2\r\n", b"W5REM-9>APRS,TCPIP*::KD5TST   :ack12\r\n", b"W5REM-9>APRS,TCPIP*::KD5TST   :second one{13\r\n"]
def handle(c):
    f = c.makefile("rwb", buffering=0)
    f.write(b"# fake aprs feed\r\n")
    login = f.readline().decode().strip()
    open("/tmp/bridge_feed.log", "a").write(login + "\n")
    for l in LINES:
        f.write(l)
    c.close()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 14592)); s.listen(5)
while True:
    c, _ = s.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
