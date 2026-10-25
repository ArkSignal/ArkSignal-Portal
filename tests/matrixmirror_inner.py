"""Runs inside the portal container (driven by matrixmirror_test.py): the Discord mirror of a standing room. Messages of a mirrored room go to the saved webhook (sealed in the database) with the writer's name; the bridge's
own messages, other kinds of event, repeats and people who are not members are ignored."""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from app import create_app
app = create_app()
from app import matrix, vault, webhooks
from app.db import get_db
from app.models import User

sent = []
webhooks.send = lambda url, kind, text, matches, site: (sent.append((url, kind, text, site)) or (True, "ok"))
URL = "https://discord.com/api/webhooks/123/abcdef"
fails = 0
def check(n, c, x=""):
    global fails
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    fails += 0 if c else 1
with app.app_context():
    d = get_db()
    d.execute("DELETE FROM users WHERE username = 'mirror1'")
    User.create("mirror1", "mirror1@example.com", "mirrorpassword1")
    d.execute("UPDATE users SET display_name = 'Mirror One' WHERE username = 'mirror1'")
    d.execute("DELETE FROM matrix_mirrors")
    d.execute("INSERT INTO matrix_mirrors (alias, room_id, webhook, by_name) VALUES ('general', '!room:matrix-test.local', %s, 'test')", (vault.seal(URL),))
    d.commit()
    check("the webhook address is sealed in the database", d.execute("SELECT webhook FROM matrix_mirrors WHERE alias='general'").fetchone()["webhook"].startswith("enc1:"))
    def ev(i, **kw):
        e = {"event_id": "$e" + i, "type": "m.room.message", "room_id": "!room:matrix-test.local", "sender": "@mirror1:matrix-test.local", "content": {"msgtype": "m.text", "body": "hello discord"}}
        e.update(kw)
        return e
    d.execute("DELETE FROM matrix_seen_events")
    d.commit()
    matrix.handle_event(ev("1"))
    check("a message in a mirrored room goes to the saved Discord address, under the writer's name", len(sent) == 1 and sent[0][0] == URL and sent[0][1] == "discord" and sent[0][2] == "Mirror One: hello discord", sent)
    matrix.handle_event(ev("1"))
    check("the same event twice is sent once", len(sent) == 1)
    matrix.handle_event(ev("2", sender="@arksignal-bridge:matrix-test.local"))
    check("the bridge's own messages are never mirrored (no loop)", len(sent) == 1)
    matrix.handle_event(ev("3", room_id="!other:matrix-test.local"))
    check("a room without a mirror sends nothing", len(sent) == 1)
    matrix.handle_event(ev("4", sender="@stranger:matrix-test.local"))
    check("a writer who is not a member of the site is ignored", len(sent) == 1)
    matrix.handle_event(ev("5", content={"msgtype": "m.image", "body": "photo.jpg"}))
    matrix.handle_event(ev("6", type="m.room.topic", content={"topic": "x"}))
    check("pictures and other kinds of event are not mirrored", len(sent) == 1)
    d.execute("UPDATE users SET disabled = TRUE WHERE username = 'mirror1'")
    d.commit()
    matrix.handle_event(ev("7"))
    check("a disabled member's messages are not mirrored", len(sent) == 1)
    check("the mirror is in the audit log", d.execute("SELECT 1 FROM audit_log WHERE action = 'matrix.mirror' LIMIT 1").fetchone() is not None)
    d.execute("DELETE FROM matrix_mirrors")
    d.execute("DELETE FROM users WHERE username = 'mirror1'")
    d.commit()
print("ALL PASS" if not fails else "SOME FAILED")
