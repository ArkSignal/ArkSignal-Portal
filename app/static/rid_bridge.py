#!/usr/bin/env python3
"""ArkSignal Remote ID bridge: reads the drone Remote ID messages your receiver produces and forwards them in batches, over https, to your private upload address on the ArkSignal site. Standard library only.

  cat /dev/ttyUSB0 | python3 rid_bridge.py https://YOUR-SITE/drones/push/YOUR-KEY      (lines from a scanner's serial port, or any program, on standard input)
  python3 rid_bridge.py https://YOUR-SITE/drones/push/YOUR-KEY --udp 5556                (messages sent to UDP port 5556 on this computer)

Each line is either the raw hex of a Remote ID message or message pack (like 0f190301...) or a JSON object of what your receiver decoded ({"id": "...", "lat": ..., "lon": ...}); a JSON line may also carry
"src" (the transmitter's address) so messages that arrive one at a time can be joined to the right drone. Lines that are neither are skipped. Sends every 2 seconds (ARK_BATCH_SECONDS).
"""
import os
import socket
import sys
import threading
import time
import urllib.request

args = [a for a in sys.argv[1:]]
if not args or not args[0].startswith("http"):
    sys.exit(__doc__)
URL = args[0]
UDP = int(args[args.index("--udp") + 1]) if "--udp" in args else None
EVERY = float(os.environ.get("ARK_BATCH_SECONDS", "2"))
buf, lock, sent = [], threading.Lock(), 0


def keep(line):
    line = line.strip()
    if line and (line[0] == "{" or all(c in "0123456789abcdefABCDEF :xX" for c in line)):
        with lock:
            if len(buf) < 20000:
                buf.append(line)


def push():
    global sent
    while True:
        time.sleep(EVERY)
        with lock:
            batch, buf[:] = buf[:500], buf[500:]
        if not batch:
            continue
        req = urllib.request.Request(URL, data="\n".join(batch).encode(), headers={"Content-Type": "text/plain", "User-Agent": "arksignal-rid-bridge"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                sent += len(batch)
                print(time.strftime("%H:%M:%S"), "sent", len(batch), "messages (total", sent, ") ->", r.status, flush=True)
        except Exception as e:
            print(time.strftime("%H:%M:%S"), "could not send, will try again:", e, flush=True)
            with lock:
                buf[:0] = batch[:2000]


threading.Thread(target=push, daemon=True).start()
if UDP:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", UDP))
    print("listening on UDP", UDP, "and sending to", URL.split("/drones/")[0], flush=True)
    while True:
        data, _ = s.recvfrom(65535)
        for ln in data.decode("utf-8", "replace").splitlines():
            keep(ln)
else:
    print("reading standard input and sending to", URL.split("/drones/")[0], flush=True)
    for ln in sys.stdin:
        keep(ln)
    time.sleep(EVERY + 1)   # let the last batch go
