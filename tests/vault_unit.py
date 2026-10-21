import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
import psycopg
from psycopg.rows import dict_row
from app import create_app, vault
app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
with app.app_context():
    s = vault.seal("https://discord.com/api/webhooks/123/TOKEN-abc")
    check("sealed text starts with the marker and hides the content", s.startswith("enc1:") and "TOKEN-abc" not in s and "discord" not in s)
    check("unsealing returns the original", vault.unseal(s) == "https://discord.com/api/webhooks/123/TOKEN-abc")
    check("sealing twice does not double-encrypt", vault.seal(s) == s)
    check("plain legacy text is read as is", vault.unseal("legacy") == "legacy")
    check("empty stays empty", vault.seal("") == "" and vault.unseal("") == "")
    check("every seal is different (random IV)", vault.seal("same") != vault.seal("same"))
    h = vault.keyed_hash("abcd1234", "recovery")
    check("the keyed hash is stable, 64 hex characters, and not a plain sha256", h == vault.keyed_hash("abcd1234", "recovery") and len(h) == 64 and h != __import__("hashlib").sha256(b"abcd1234").hexdigest())
    check("a different purpose gives a different hash", h != vault.keyed_hash("abcd1234", "other"))
    good = app.config["SECRET_KEY"]
    app.config["SECRET_KEY"] = "a-completely-different-key"
    check("with another SECRET_KEY the sealed value cannot be read (empty, not an error)", vault.unseal(s) == "")
    app.config["SECRET_KEY"] = good
    with psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row) as c:
        c.execute("UPDATE users SET webhook_url='https://example.com/plain-secret-token', totp_secret='PLAINSECRETBASE32' WHERE username='plain1'")
        n = vault.encrypt_existing(c); c.commit()
        r = c.execute("SELECT webhook_url, totp_secret FROM users WHERE username='plain1'").fetchone()
        check("plain-text secrets left by an older version are sealed on start", n >= 2 and r["webhook_url"].startswith("enc1:") and r["totp_secret"].startswith("enc1:"), str(r))
        check("and still read back correctly", vault.unseal(r["webhook_url"]) == "https://example.com/plain-secret-token" and vault.unseal(r["totp_secret"]) == "PLAINSECRETBASE32")
        check("running it again changes nothing", vault.encrypt_existing(c) == 0)
        c.execute("UPDATE users SET webhook_url='', totp_secret='' WHERE username='plain1'"); c.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
