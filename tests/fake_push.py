"""A stand-in for a browser maker's push service, run inside the portal container (tests/webpush_test.py): it keeps every notification it is given in /tmp/push.log (one JSON line each) and answers 201,
or 410 Gone for a path that ends in /gone."""
import base64, json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        with open("/tmp/push.log", "a") as f:
            f.write(json.dumps({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": base64.b64encode(body).decode()}) + "\n")
        code = 410 if self.path.endswith("/gone") else 201
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()
    def do_GET(self):
        if self.path == "/quit":
            self.send_response(200); self.end_headers(); self.wfile.write(b"bye")
            import threading; threading.Thread(target=self.server.shutdown).start()
            return
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
