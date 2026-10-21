#!/usr/bin/env python3
"""ArkSignal ACARS bridge: listens for the JSON messages that acars_router, acarsdec, dumpvdl2 or dumphfdl send over UDP (one JSON object per datagram) and forwards them in small batches,
over https, to your private upload address on the ArkSignal site. Standard library only.

  python3 acars_bridge.py https://YOUR-SITE/acars/push/YOUR-TOKEN            (listens on UDP 5555, sends every 5 seconds)
  environment: ARK_LISTEN_PORT (5555), ARK_BATCH_SECONDS (5)

Point acars_router at it with:  AR_SEND_UDP_ACARS=127.0.0.1:5555  AR_SEND_UDP_VDLM2=127.0.0.1:5555  (and AR_SEND_UDP_HFDL=127.0.0.1:5555 for HFDL)
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.request

if len(sys.argv) != 2 or not sys.argv[1].startswith("http"):
    sys.exit(__doc__)
URL = sys.argv[1]
PORT = int(os.environ.get("ARK_LISTEN_PORT", "5555"))
EVERY = float(os.environ.get("ARK_BATCH_SECONDS", "5"))
buf, lock, sent = [], threading.Lock(), 0


def push():
    global sent
    while True:
        time.sleep(EVERY)
        with lock:
            batch, buf[:] = buf[:200], buf[200:]
        if not batch:
            continue
        req = urllib.request.Request(URL, data=json.dumps(batch).encode(), headers={"Content-Type": "application/json", "User-Agent": "arksignal-acars-bridge"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                sent += len(batch)
                print(time.strftime("%H:%M:%S"), "sent", len(batch), "messages (total", sent, ") ->", r.status, flush=True)
        except Exception as e:
            print(time.strftime("%H:%M:%S"), "could not send, will try again:", e, flush=True)
            with lock:
                buf[:0] = batch[:1000]


threading.Thread(target=push, daemon=True).start()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", PORT))
print("listening on UDP", PORT, "and sending to", URL.split("/acars/")[0], flush=True)
while True:
    data, _ = s.recvfrom(65535)
    try:
        for line in data.decode("utf-8", "replace").splitlines():
            if line.strip():
                m = json.loads(line)
                with lock:
                    if len(buf) < 5000:
                        buf.append(m)
    except ValueError:
        pass
