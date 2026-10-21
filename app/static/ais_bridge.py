#!/usr/bin/env python3
"""ArkSignal AIS bridge: listens for the AIS sentences (!AIVDM,...) that AIS-catcher, rtl_ais or any AIS receiver sends over UDP (one or more sentences per datagram) and forwards them in batches, over https, to
your private upload address on the ArkSignal site. Standard library only.

  python3 ais_bridge.py https://YOUR-SITE/ais/push/YOUR-KEY           (listens on UDP 10110, sends every 5 seconds)
  environment: ARK_LISTEN_PORT (10110), ARK_BATCH_SECONDS (5)

Point your receiver at it, for AIS-catcher:  AIS-catcher -d:0 -u 127.0.0.1 10110     (rtl_ais:  rtl_ais -h 127.0.0.1 -P 10110)
"""
import os
import socket
import sys
import threading
import time
import urllib.request

if len(sys.argv) != 2 or not sys.argv[1].startswith("http"):
    sys.exit(__doc__)
URL = sys.argv[1]
PORT = int(os.environ.get("ARK_LISTEN_PORT", "10110"))
EVERY = float(os.environ.get("ARK_BATCH_SECONDS", "5"))
buf, lock, sent = [], threading.Lock(), 0


def push():
    global sent
    while True:
        time.sleep(EVERY)
        with lock:
            batch, buf[:] = buf[:800], buf[800:]
        if not batch:
            continue
        req = urllib.request.Request(URL, data="\n".join(batch).encode(), headers={"Content-Type": "text/plain", "User-Agent": "arksignal-ais-bridge"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                sent += len(batch)
                print(time.strftime("%H:%M:%S"), "sent", len(batch), "sentences (total", sent, ") ->", r.status, flush=True)
        except Exception as e:
            print(time.strftime("%H:%M:%S"), "could not send, will try again:", e, flush=True)
            with lock:
                buf[:0] = batch[:2000]


threading.Thread(target=push, daemon=True).start()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", PORT))
print("listening on UDP", PORT, "and sending to", URL.split("/ais/")[0], flush=True)
while True:
    data, _ = s.recvfrom(65535)
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith(("!AIVDM", "!AIVDO", "$AIVDM")):
            with lock:
                if len(buf) < 20000:
                    buf.append(line)
