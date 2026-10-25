"""Keeping secrets out of the web server's access log.

Several addresses carry a secret in the path or the query: a receiver's upload address (/sat/push/<token>), a password reset link, a community invitation, a shared-flight link, the sign-in return from Google
or from another service (code and state). The access log is kept by Docker and read by whoever can read the host's logs, so what it writes for a request has any such part replaced before it is written.
`RedactingLogger` is gunicorn's logger with that applied (the Dockerfile starts gunicorn with --logger-class app.logredact.RedactingLogger)."""
import re

# a long run of URL-safe characters as a whole path part: a token (secrets.token_urlsafe), a station's UUID, a hash
_PATH_SECRET = re.compile(r"/[A-Za-z0-9_\-]{22,}(?=[/?\s\"]|$)")
_QUERY_SECRET = re.compile(r"([?&](?:token|code|key|api_key|apikey|secret|password|state|id_token|access_token|client_secret|nonce)=)[^&\s\"]*", re.I)


def redact(text):
    if not text or not isinstance(text, str):
        return text
    return _QUERY_SECRET.sub(r"\1<hidden>", _PATH_SECRET.sub("/<hidden>", text))


try:
    from gunicorn.glogging import Logger

    class RedactingLogger(Logger):
        def atoms(self, resp, req, environ, request_time):
            a = super().atoms(resp, req, environ, request_time)
            for k in ("r", "U", "q", "f", "{referer}i", "{Referer}i", "{referrer}i"):
                if k in a:
                    a[k] = redact(a[k])
            return a
except ImportError:   # not running under gunicorn (the tests, the command line)
    RedactingLogger = None
