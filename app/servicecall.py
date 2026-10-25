"""The shared secret between the portal and the services in its own stack (mesh, ACARS, APRS, predictions, Matrix helper).

Those services listen only on the stack's internal Docker network, and each one now also refuses any request that does not carry the stack's SERVICE_TOKEN (set once in the stack's .env, passed to the portal and
to every service) in the X-Service-Token header; only /health is open. Another container on the same network, or a request that got in some other way, cannot read their data or change their logins. With no token set
the services stay open as before (and Admin > Overview says so)."""
import os


def token():
    return os.environ.get("SERVICE_TOKEN", "")


def headers():
    return {"X-Service-Token": token()} if token() else {}
