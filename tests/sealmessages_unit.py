"""Runs inside the portal container (tests/sealmessages_test.py): private message text that an older version stored as plain text is sealed by the start-up pass, can still be read through the site before and after, and nothing
else is changed. Prints one JSON line."""
import json, os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["DISABLE_MESH_SYNC"] = "1"
from app import create_app
app = create_app()
out = {}
with app.app_context():
    from app import vault
    from app.db import get_db
    d = get_db()
    uid = d.execute("SELECT id FROM users WHERE username='plain1'").fetchone()["id"]
    mid = d.execute("INSERT INTO mail_messages (sender_id, subject, body, draft_to, sent_at, sender_folder) VALUES (%s,'Legacy subject SM1','Legacy body SM1','x',now(),'sent') RETURNING id", (uid,)).fetchone()["id"]
    cid = d.execute("INSERT INTO convs (kind, created_by) VALUES ('group', %s) RETURNING id", (uid,)).fetchone()["id"]
    d.execute("INSERT INTO conv_members (conv_id, user_id) VALUES (%s,%s)", (cid, uid))
    xid = d.execute("INSERT INTO conv_messages (conv_id, user_id, body) VALUES (%s,%s,'Legacy chat SM1') RETURNING id", (cid, uid)).fetchone()["id"]
    d.commit()
    out["plain_to_start"] = d.execute("SELECT body FROM conv_messages WHERE id=%s", (xid,)).fetchone()["body"] == "Legacy chat SM1"
    n = vault.encrypt_existing(d)
    d.commit()
    m = d.execute("SELECT subject, body FROM mail_messages WHERE id=%s", (mid,)).fetchone()
    c = d.execute("SELECT body FROM conv_messages WHERE id=%s", (xid,)).fetchone()
    out["sealed_by_the_pass"] = n >= 3 and m["subject"].startswith("enc1:") and m["body"].startswith("enc1:") and c["body"].startswith("enc1:") and "SM1" not in m["subject"] + m["body"] + c["body"]
    out["opens_to_the_same_text"] = vault.unseal(m["subject"]) == "Legacy subject SM1" and vault.unseal(m["body"]) == "Legacy body SM1" and vault.unseal(c["body"]) == "Legacy chat SM1"
    out["a_second_pass_changes_nothing"] = vault.encrypt_existing(d) == 0
    # ---- the private flight diary: notes and passengers' names
    fid = d.execute("INSERT INTO my_flights (user_id, from_code, to_code, notes) VALUES (%s,'AAA','BBB','Legacy diary note SM1') RETURNING id", (uid,)).fetchone()["id"]
    pid = d.execute("INSERT INTO my_flight_pax (flight_id, name) VALUES (%s,'Legacy Passenger SM1') RETURNING id", (fid,)).fetchone()["id"]
    d.commit()
    vault.encrypt_existing(d); d.commit()
    fr = d.execute("SELECT notes FROM my_flights WHERE id=%s", (fid,)).fetchone()["notes"]
    pr = d.execute("SELECT name FROM my_flight_pax WHERE id=%s", (pid,)).fetchone()["name"]
    out["diary_notes_and_names_sealed"] = fr.startswith("enc1:") and pr.startswith("enc1:") and vault.unseal(fr) == "Legacy diary note SM1" and vault.unseal(pr) == "Legacy Passenger SM1"
    d.execute("DELETE FROM my_flights WHERE id=%s", (fid,))
    d.execute("DELETE FROM mail_messages WHERE id=%s", (mid,))
    d.execute("DELETE FROM convs WHERE id=%s", (cid,))
    d.commit()
print(json.dumps(out))
