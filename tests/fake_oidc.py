"""A stand-in for Google's sign-in servers, for tests only: run inside the portal container (python /tmp/fake_oidc.py). It serves the signing keys (/certs) and swaps a made-up one-time code for a signed ID token
(/token). The code is a base64url JSON the test builds itself: {"claims": {...}, "challenge": "...", "bad_sig": false}; the server checks the client secret and the PKCE verifier like the real one, then signs
the claims. /quit stops it."""
import base64
import json
import sys
import threading
import time
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18990
SECRET = "test-secret-123"
KEY, OTHER = rsa.generate_private_key(65537, 2048), rsa.generate_private_key(65537, 2048)


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64ud(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def jwk(key, kid):
    n = key.public_key().public_numbers()
    return {"kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig", "n": b64u(n.n.to_bytes(256, "big")), "e": b64u(n.e.to_bytes(3, "big"))}


def sign(claims, key, kid):
    head = b64u(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
    body = b64u(json.dumps(claims).encode())
    sig = key.sign((head + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256())
    return head + "." + body + "." + b64u(sig)


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
        if self.path.startswith("/certs"):
            return self._send(200, {"keys": [jwk(KEY, "kid-1")]})
        if self.path.startswith("/quit"):
            self._send(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self._send(200, {"ok": True})

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        f = {k: v[0] for k, v in form.items()}
        if f.get("client_secret") != SECRET:
            return self._send(401, {"error": "invalid_client"})
        try:
            code = json.loads(b64ud(f.get("code", "")))
        except ValueError:
            return self._send(400, {"error": "invalid_grant"})
        if b64u(sha256(f.get("code_verifier", "").encode()).digest()) != code.get("challenge"):
            return self._send(400, {"error": "invalid_grant", "error_description": "PKCE check failed"})
        now = int(time.time())
        claims = {"iss": "https://accounts.google.com", "aud": f.get("client_id"), "iat": now, "exp": now + 300}
        claims.update(code.get("claims", {}))
        tok = sign(claims, OTHER if code.get("bad_sig") else KEY, "kid-1")
        self._send(200, {"id_token": tok, "access_token": "x", "token_type": "Bearer"})


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
