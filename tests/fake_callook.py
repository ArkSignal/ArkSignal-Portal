"""A stand-in for Callook.info (the FCC amateur license data), for tests only: run inside the portal container (python /tmp/fake_callook.py [port]). It answers /<CALL>/json like Callook does and keeps a mode
per call sign that a test can change with /_mode/<CALL>/<active|invalid|expired|down|club>. Made-up call signs: K5ACT (JOHN Q PUBLIC, active), K5MIS (JANE SMITH, active), K5OLD (JOHN Q PUBLIC, expired
2020), K5EXP (no such license), K5CLB (a club), K5DWN (the service fails). /quit stops it."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18991
NAMES = {"K5ACT": "JOHN Q PUBLIC", "K5MIS": "JANE SMITH", "K5OLD": "JOHN Q PUBLIC", "K5CLB": "ARKANSAS RADIO CLUB"}
MODES = {"K5ACT": "active", "K5MIS": "active", "K5OLD": "expired", "K5EXP": "invalid", "K5CLB": "club", "K5DWN": "down"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if parts[:1] == ["quit"]:
            self._send(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if parts[:1] == ["_mode"] and len(parts) == 3:
            MODES[parts[1].upper()] = parts[2]
            return self._send(200, {"ok": True})
        if len(parts) == 2 and parts[1] == "json":
            call = parts[0].upper()
            mode = MODES.get(call, "invalid")
            if mode == "down":
                return self._send(500, {"error": "down"})
            if mode == "invalid":
                return self._send(200, {"status": "INVALID"})
            exp = "01/01/2020" if mode == "expired" else "01/01/2036"
            return self._send(200, {"status": "VALID", "type": "CLUB" if mode == "club" else "PERSON", "current": {"callsign": call, "operClass": "GENERAL"}, "name": NAMES.get(call, "JOHN Q PUBLIC"),
                                    "address": {"line1": "1 MAIN ST", "line2": "LITTLE ROCK, AR 72201"}, "otherInfo": {"grantDate": "01/01/2026", "expiryDate": exp, "ulsUrl": "http://example.invalid/uls"}})
        self._send(404, {})


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
