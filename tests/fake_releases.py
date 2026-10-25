"""A stand-in for GitHub's releases answer, run inside the portal container (tests/meshrelease_test.py): GET /<repo>/releases/latest answers {tag_name, published_at} from what GET /set/<firmware tag>/<protobufs tag> last said (a dash for none); a repository with none answers 404."""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
STATE = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if self.path == "/quit":
            self.send_response(200); self.end_headers(); self.wfile.write(b"bye")
            import threading; threading.Thread(target=self.server.shutdown).start()
            return
        parts = self.path.strip("/").split("/")
        if parts[0] == "set" and len(parts) == 3:   # /set/<firmware tag or ->/<protobufs tag or ->
            STATE.clear(); STATE.update({k: v for k, v in (("firmware", parts[1]), ("protobufs", parts[2])) if v != "-"})
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
            return
        tag = STATE.get(parts[0])
        if len(parts) == 3 and parts[1] == "releases" and parts[2] == "latest" and tag:
            body = json.dumps({"tag_name": tag, "published_at": "2026-10-01T12:00:00Z"}).encode()
            self.send_response(200)
        else:
            body = b"{}"
            self.send_response(404)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
