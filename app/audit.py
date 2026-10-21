from flask import request
from flask_login import current_user

from .clientip import client_ip
from .db import get_db


def audit(action, target="", detail="", actor_name=None):
    """Record an admin-visible action. Uses the signed-in user unless `actor_name` is given."""
    signed_in = current_user and current_user.is_authenticated
    d = get_db()
    d.execute("INSERT INTO audit_log (actor_id, actor_name, action, target, detail, ip) VALUES (%s,%s,%s,%s,%s,%s)",
              (current_user.id if signed_in else None,
               actor_name or (current_user.username if signed_in else "system"),
               action, target, detail, client_ip() if request else None))
    d.commit()
