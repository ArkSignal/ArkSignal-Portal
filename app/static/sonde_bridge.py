#!/usr/bin/env python3
"""ArkSignal radiosonde bridge: listens for the "payload summary" that radiosonde_auto_rx broadcasts over UDP (one JSON object per datagram) and forwards what it hears, in small batches,
over https, to your private upload address on the ArkSignal site. Standard library only.

  python3 sonde_bridge.py https://YOUR-SITE/sondes/push/YOUR-TOKEN            (listens on UDP 55673, sends every 5 seconds)
  environment: ARK_LISTEN_PORT (55673), ARK_BATCH_SECONDS (5)

Switch the output on in auto_rx's station.cfg:
  [oziplotter]
  payload_summary_enabled = True
  payload_summary_port = 55673
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
PORT = int(os.environ.get("ARK_LISTEN_PORT", "55673"))
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
        req = urllib.request.Request(URL, data=json.dumps(batch).encode(), headers={"Content-Type": "application/json", "User-Agent": "arksignal-sonde-bridge"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                sent += len(batch)
                print(time.strftime("%H:%M:%S"), "sent", len(batch), "frames (total", sent, ") ->", r.status, flush=True)
        except Exception as e:
            print(time.strftime("%H:%M:%S"), "could not send, will try again:", e, flush=True)
            with lock:
                buf[:0] = batch[:1000]


threading.Thread(target=push, daemon=True).start()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", PORT))
print("listening on UDP", PORT, "and sending to", URL.split("/sondes/")[0], flush=True)
while True:
    data, _ = s.recvfrom(65535)
    try:
        for line in data.decode("utf-8", "replace").splitlines():
            if line.strip():
                m = json.loads(line)
                if isinstance(m, dict):
                    m.setdefault("time_received", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))   # this computer's clock, in UTC
                    with lock:
                        if len(buf) < 5000:
                            buf.append(m)
    except ValueError:
        pass
