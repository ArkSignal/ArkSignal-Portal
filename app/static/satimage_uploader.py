#!/usr/bin/env python3
"""ArkSignal satellite picture uploader: watches a folder for new pictures (PNG, JPEG or WebP) written by SatDump, goestools or anything else, and sends each one once to your receiver's private
upload address on the ArkSignal site. Standard library only.

  python3 satimage_uploader.py UPLOAD_ADDRESS FOLDER [--satellite "METEOR-M2 3"] [--product "MSA false color"] [--every 30] [--once]

  UPLOAD_ADDRESS  the address on your receiver's card (My Account > My Satellite Receivers), like https://YOUR-SITE/sat/push/YOUR-KEY
  FOLDER          where your decoder saves pictures (looked through, including sub-folders)
  --satellite     the satellite name shown under the picture; when left out, the name of the sub-folder or file is used
  --product       the kind of picture (for example "MSA false color"); optional
  --every         seconds between looks at the folder (default 30)
  --once          look once, send what is new and stop (for a scheduled job)

A picture is sent when it has stopped changing for ten seconds. What was sent is remembered in a file called .arksignal_sent in the folder, so a picture is never sent twice.
Pictures bigger than the site allows, or sent faster than its hourly limit, are skipped and tried again later.
"""
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

EXT = (".png", ".jpg", ".jpeg", ".webp")


def args():
    a, opts, pos = sys.argv[1:], {}, []
    i = 0
    while i < len(a):
        if a[i] in ("--satellite", "--product", "--every"):
            if i + 1 >= len(a):
                sys.exit(__doc__)
            opts[a[i][2:]] = a[i + 1]
            i += 2
        elif a[i] == "--once":
            opts["once"] = True
            i += 1
        else:
            pos.append(a[i])
            i += 1
    if len(pos) != 2 or not pos[0].startswith("http") or not os.path.isdir(pos[1]):
        sys.exit(__doc__)
    return pos[0], pos[1], opts


def post(url, path, fields):
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        if v:
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    body += f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{os.path.basename(path)}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
    with open(path, "rb") as f:
        body += f.read()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "multipart/form-data; boundary=" + boundary, "User-Agent": "arksignal-satimage-uploader"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read() or b"{}")


def main():
    url, folder, opts = args()
    memo = os.path.join(folder, ".arksignal_sent")
    sent = set(open(memo).read().split("\n")) if os.path.exists(memo) else set()
    every = float(opts.get("every", 30))
    while True:
        for root, _dirs, files in os.walk(folder):
            for name in sorted(files):
                path = os.path.join(root, name)
                if not name.lower().endswith(EXT) or path in sent:
                    continue
                try:
                    st = os.stat(path)
                    if time.time() - st.st_mtime < 10:
                        continue   # still being written
                    sub = os.path.relpath(root, folder)
                    sat = opts.get("satellite") or (os.path.splitext(name)[0] if sub == "." else os.path.basename(root))
                    code, answer = post(url, path, {"satellite": sat, "product": opts.get("product", ""), "captured": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))})
                    print(time.strftime("%H:%M:%S"), "sent", name, "->", code, answer, flush=True)
                except urllib.error.HTTPError as e:
                    detail = e.read().decode("utf-8", "replace")[:200]
                    print(time.strftime("%H:%M:%S"), name, "refused:", e.code, detail, flush=True)
                    if e.code in (400, 413):   # this picture will never be accepted: do not try it again
                        sent.add(path)
                        open(memo, "a").write(path + "\n")
                    continue
                except Exception as e:
                    print(time.strftime("%H:%M:%S"), "could not send", name, "will try again:", e, flush=True)
                    continue
                sent.add(path)
                open(memo, "a").write(path + "\n")
        if opts.get("once"):
            return
        time.sleep(every)


main()
