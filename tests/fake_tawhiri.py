"""A stand-in Tawhiri server for predictmethods_test (runs on the test box): answers the standard profile with a fixed trajectory and writes each request path to /tmp/tawhiri_seen.log."""
import json, re, sys
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        open("/tmp/tawhiri_seen.log", "a").write(self.path + "\n")
        launch = re.search(r"launch_datetime=([^&]+)", self.path).group(1).replace("%3A", ":")
        t0 = datetime.fromisoformat(launch.replace("Z", "+00:00"))
        stage = lambda name, pts: {"stage": name, "trajectory": [{"latitude": la, "longitude": lo, "altitude": al, "datetime": (t0 + timedelta(minutes=m)).isoformat()} for m, la, lo, al in pts]}
        body = json.dumps({"prediction": [stage("ascent", [(0, 34.75, 267.7, 170), (60, 34.9, 268.3, 20000), (95, 35.0, 268.6, 30000)]), stage("descent", [(110, 35.05, 268.9, 8000), (125, 35.1, 269.1, 250)])]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
HTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 18999), H).serve_forever()
