"""In-container: the Planefence proxy streams the data (first rows at once, compressed, nothing collected first) and caches the pages. A tiny slow upstream is started here."""
import gzip, os, threading, time, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
HITS = []


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        HITS.append(self.path)
        if self.path.startswith("/cgi/stream.sh"):
            self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
            for i in range(12):
                line = ('{"n": %d, "pad": "%s"}\n' % (i, "x" * 200)).encode()
                self.wfile.write(b"%x\r\n" % len(line) + line + b"\r\n"); self.wfile.flush(); time.sleep(0.25)
            self.wfile.write(b"0\r\n\r\n")
        else:
            body = b"<html>" + b"<a href=\"/cgi/x\">row</a> " * 500 + b"</html>"
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["PLANEFENCE_URL"] = f"http://127.0.0.1:{srv.server_port}"
from app import create_app
app = create_app()
ok_all = True


def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)


c = app.test_client()
t0 = time.time()
r = c.get("/pf/cgi/stream.sh?mode=planefence&date=today", headers={"Accept-Encoding": "gzip"}, buffered=False)
it = iter(r.response)
first = next(it); t_first = time.time() - t0
rest = b"".join(it); t_all = time.time() - t0
check("the first rows reach the page long before the stream ends (first piece %.2f s, whole stream %.2f s)" % (t_first, t_all), t_first < 1.0 and t_all > 2.5, (t_first, t_all))
check("the stream is sent compressed and unbuffered", r.headers.get("Content-Encoding") == "gzip" and r.headers.get("X-Accel-Buffering") == "no" and "no-store" in r.headers.get("Cache-Control", ""), dict(r.headers))
text = zlib.decompressobj(31).decompress(first + rest).decode()
check("every line arrives intact", text.count("\n") == 12 and '"n": 11' in text, text[:80])
r2 = c.get("/pf/cgi/stream.sh?mode=planefence&date=today", buffered=True)
check("a visitor whose browser does not accept gzip gets the plain stream", r2.headers.get("Content-Encoding") is None and r2.data.count(b"\n") == 12)
before = len(HITS)
a = c.get("/pf/index.html", headers={"Accept-Encoding": "gzip"}); b = c.get("/pf/index.html", headers={"Accept-Encoding": "gzip"})
check("a page is fetched from Planefence once and then served from the cache", len(HITS) - before == 1 and a.status_code == 200 and b.status_code == 200, HITS[before:])
check("it is compressed for browsers that accept it, with the script paths pointed at the portal", a.headers.get("Content-Encoding") == "gzip" and b"/pf/cgi/x" in gzip.decompress(a.data) and len(a.data) < 1000, (a.headers.get("Content-Encoding"), len(a.data)))
plain = c.get("/pf/index.html")
check("and plain for the rest", plain.headers.get("Content-Encoding") is None and b"/pf/cgi/x" in plain.data)
print("ALL PASS" if ok_all else "SOME FAILED")
