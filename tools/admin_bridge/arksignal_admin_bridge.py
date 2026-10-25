#!/usr/bin/env python3
"""ArkSignal Admin Bridge: remote administration of mesh nodes for a mesh community, with the admin key kept on your own computer.

Run this next to a gateway node (Wi-Fi or USB) that holds the admin key for the nodes you look after. It reaches OUT to the site over https with the bridge's token, takes the commands a Manager or the
Owner has queued for it (and approved, where a command changes something), carries them out with the official `meshtastic` command line program, and sends back what happened. The site never sees the key,
and nothing connects in to your computer.

Only five commands exist (info, set_owner, set_role, set_channel_name, reboot) and every value is checked here again before anything is run, so a command the site sends that is not one of them, or has
a value outside the rules, is refused and reported. Nothing runs through a shell.

Setup:   pip install meshtastic      (the official command line: https://meshtastic.org/docs/software/python/cli/)
Run:     python arksignal_admin_bridge.py --server https://arksignal.net --token-file bridge.token --host 192.168.1.50
         (or --port /dev/ttyUSB0 for a gateway on USB; leave both out to let meshtastic find a USB node)
Try:     add --dry-run to see what would be run without running it.
Keeping up: the meshtastic program is the official one, so updating it (pip install --upgrade meshtastic) keeps this in step with the project; this file only builds its command lines."""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

VERSION = "1.0"
ROLES = {"CLIENT", "CLIENT_MUTE", "ROUTER", "ROUTER_CLIENT", "REPEATER", "TRACKER", "SENSOR", "TAK", "CLIENT_HIDDEN", "LOST_AND_FOUND", "TAK_TRACKER", "ROUTER_LATE", "CLIENT_BASE"}
NAME_RE = re.compile(r"^[A-Za-z0-9 ._:+()#@/-]{1,40}$")
NODE_RE = re.compile(r"^![0-9a-f]{8}$")


class Refused(Exception):
    pass


def _name(v, n):
    if not isinstance(v, str) or not NAME_RE.match(v) or len(v) > n:
        raise Refused("a name is not allowed")
    return v


def _int(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise Refused("a number is out of range")
    return str(v)


def build_args(cmd):
    """The meshtastic command line (a list, never a shell string) for one command from the site, or Refused."""
    node, kind, p = cmd.get("node"), cmd.get("kind"), cmd.get("params") or {}
    if not isinstance(node, str) or not NODE_RE.match(node):
        raise Refused("the node is not a Meshtastic node ID")
    dest = ["--dest", node]
    if kind == "info":
        return dest + ["--info"]
    if kind == "set_owner":
        return dest + ["--set-owner", _name(p.get("long_name"), 39), "--set-owner-short", _name(p.get("short_name"), 4)]
    if kind == "set_role":
        if p.get("role") not in ROLES:
            raise Refused("the role is not one of the known roles")
        return dest + ["--set", "device.role", p["role"]]
    if kind == "set_channel_name":
        return dest + ["--ch-index", _int(p.get("index"), 0, 7), "--ch-set", "name", _name(p.get("name"), 11)]
    if kind == "reboot":
        _int(p.get("seconds"), 5, 600)   # the site asks for a delay; the command line's own reboot is used and the value is only checked
        return dest + ["--reboot"]
    raise Refused("that command is not one this bridge does")


def connection(args):
    if args.host:
        return ["--host", args.host]
    if args.port:
        return ["--port", args.port]
    return []


def run_command(cmd, args, runner=None):
    """(ok, text) for one command. `runner(argv)` returns (returncode, output); tests pass their own."""
    try:
        argv = ["meshtastic"] + connection(args) + build_args(cmd)
    except Refused as e:
        return False, "Refused by the bridge: " + str(e)
    if args.dry_run:
        return True, "Dry run, not run: " + " ".join(argv)

    def real(a):
        try:
            r = subprocess.run(a, capture_output=True, text=True, timeout=args.timeout)
            return r.returncode, (r.stdout + r.stderr)
        except FileNotFoundError:
            return 127, "The meshtastic program is not installed here (pip install meshtastic)."
        except subprocess.TimeoutExpired:
            return 124, f"The node did not answer within {args.timeout} seconds."
    code, out = (runner or real)(argv)
    return code == 0, (out or "").strip()[:3800]


def call(server, token, path, body):
    req = urllib.request.Request(server.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "X-Bridge-Version": VERSION, "User-Agent": "ArkSignal-Admin-Bridge/" + VERSION})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def poll_once(args, token, runner=None):
    """Ask the site for commands, run them, report each. Returns (number done, seconds to wait)."""
    d = call(args.server, token, "/api/mesh/bridge/poll", {})
    n = 0
    for cmd in d.get("commands", []):
        ok, text = run_command(cmd, args, runner)
        print(f"#{cmd.get('id')} {cmd.get('kind')} {cmd.get('node')}: {'done' if ok else 'failed'}", flush=True)
        call(args.server, token, "/api/mesh/bridge/result", {"id": cmd.get("id"), "ok": ok, "output": text})
        n += 1
    return n, max(5, int(d.get("poll_seconds") or 30))


def main():
    ap = argparse.ArgumentParser(description="ArkSignal Admin Bridge")
    ap.add_argument("--server", required=True, help="the site, for example https://arksignal.net")
    ap.add_argument("--token-file", required=True, help="a file holding the bridge's token (from the community page)")
    ap.add_argument("--host", help="the gateway node's address on your network")
    ap.add_argument("--port", help="the gateway node's USB serial port")
    ap.add_argument("--timeout", type=int, default=120, help="seconds to wait for one command to finish")
    ap.add_argument("--dry-run", action="store_true", help="show the commands without running them")
    ap.add_argument("--once", action="store_true", help="ask once and stop")
    args = ap.parse_args()
    token = open(args.token_file, encoding="utf-8").read().strip()
    while True:
        wait = 30
        try:
            _n, wait = poll_once(args, token)
        except urllib.error.HTTPError as e:
            print(f"The site answered {e.code}" + (" (is the token right?)" if e.code == 401 else ""), file=sys.stderr, flush=True)
            wait = 60
        except Exception as e:
            print("Could not reach the site:", type(e).__name__, file=sys.stderr, flush=True)
            wait = 60
        if args.once:
            return
        time.sleep(wait)


if __name__ == "__main__":
    main()
