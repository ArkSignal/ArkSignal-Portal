"""Logical backups of the portal database.

A backup is a .tar.gz holding manifest.json plus one CSV per table (COPY format), taken inside a single
repeatable-read transaction so it is a consistent snapshot. Tables are discovered from the database, so
tables added in later versions are included automatically. No pg_dump or version matching is needed.
"""
import contextlib
import io
import json
import os
import re
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from .settings import cfg, cfg_int, get_setting, set_setting

FORMAT_VERSION = 1
MAGIC = b"ADSBPBK1\n"          # first bytes of an encrypted backup
CHUNK = 1 << 20
KINDS = ("manual", "scheduled", "pre-restore")
NAME_RE = re.compile(r"^portal-backup-(\d{8})-(\d{6})-(manual|scheduled|pre-restore)\.tar\.gz$")


def backup_dir():
    d = os.environ.get("BACKUP_DIR", "/backups")
    os.makedirs(d, exist_ok=True)
    return d


def is_encrypted(path):
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _encrypt_file(src, dst):
    """Encrypt a file in 1 MiB pieces (each its own Fernet token) so large backups need little memory."""
    from .vault import _fernet
    f = _fernet()
    with open(src, "rb") as r, open(dst, "wb") as w:
        w.write(MAGIC)
        while True:
            piece = r.read(CHUNK)
            if not piece:
                break
            tok = f.encrypt(piece)
            w.write(len(tok).to_bytes(4, "big") + tok)


@contextlib.contextmanager
def readable(path):
    """The path of a plain .tar.gz for this backup: itself, or a temporary decrypted copy of an encrypted one."""
    if not is_encrypted(path):
        yield path
        return
    from cryptography.fernet import InvalidToken
    from .vault import _fernet
    f = _fernet()
    fd, tmp = tempfile.mkstemp(suffix=".tar.gz", dir=backup_dir())
    try:
        with os.fdopen(fd, "wb") as w, open(path, "rb") as r:
            r.read(len(MAGIC))
            while True:
                head = r.read(4)
                if not head:
                    break
                try:
                    w.write(f.decrypt(r.read(int.from_bytes(head, "big"))))
                except InvalidToken:
                    raise ValueError("This backup is encrypted with a different SECRET_KEY than this portal uses, so it cannot be read here.")
        yield tmp
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def valid_name(name):
    return bool(NAME_RE.match(name or ""))


def backup_path(name):
    if not valid_name(name):
        raise ValueError("Invalid backup name.")
    return os.path.join(backup_dir(), name)


# ---------------------------------------------------------------- schema discovery

def _tables_in_order(conn):
    """Public tables, parents before children (foreign-key order)."""
    tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()]
    deps = {t: set() for t in tables}
    for child, parent in conn.execute(
            "SELECT conrelid::regclass::text, confrelid::regclass::text FROM pg_constraint "
            "WHERE contype='f' AND connamespace='public'::regnamespace").fetchall():
        if child != parent and child in deps and parent in deps:
            deps[child].add(parent)
    ordered = []
    while deps:
        ready = sorted(t for t, d in deps.items() if not d)
        if not ready:
            raise RuntimeError("Circular foreign keys; cannot order tables.")
        for t in ready:
            ordered.append(t)
            del deps[t]
        for d in deps.values():
            d.difference_update(ready)
    return ordered


def _columns(conn, table):
    return [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s "
        "ORDER BY ordinal_position", (table,)).fetchall()]


# ---------------------------------------------------------------- create

def create_backup(database_url, kind="manual"):
    """Write a backup and return (filename, manifest). Raises on failure."""
    if kind not in KINDS:
        raise ValueError("Unknown backup kind.")
    now = datetime.now(timezone.utc)
    name = f"portal-backup-{now:%Y%m%d-%H%M%S}-{kind}.tar.gz"
    final = os.path.join(backup_dir(), name)
    partial = final + ".partial"
    counts = {}
    with psycopg.connect(database_url) as conn, tempfile.TemporaryDirectory() as tmp:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        conn.read_only = True
        with conn.transaction():
            tables = _tables_in_order(conn)
            for t in tables:
                path = os.path.join(tmp, t + ".csv")
                with open(path, "wb") as f, conn.cursor() as cur:
                    with cur.copy(f'COPY (SELECT * FROM "{t}") TO STDOUT WITH (FORMAT csv, HEADER true)') as copy:
                        for chunk in copy:
                            f.write(bytes(chunk))
                counts[t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        manifest = {"format": FORMAT_VERSION, "created_at": now.isoformat(), "kind": kind,
                    "tables": tables, "row_counts": counts}
        try:
            with tarfile.open(partial, "w:gz") as tar:
                data = json.dumps(manifest, indent=1).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size = len(data)
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(data))
                for t in tables:
                    tar.add(os.path.join(tmp, t + ".csv"), arcname=f"tables/{t}.csv")
            if cfg("backup_encrypt") == "1":
                _encrypt_file(partial, final + ".enc")
                os.replace(final + ".enc", final)
                os.remove(partial)
            else:
                os.replace(partial, final)
        finally:
            if os.path.exists(partial):
                os.remove(partial)
    return name, manifest


# ---------------------------------------------------------------- list / read

def read_manifest(path):
    with readable(path) as plain, tarfile.open(plain, "r:gz") as tar:
        f = tar.extractfile("manifest.json")
        if f is None:
            raise ValueError("The file has no manifest.json.")
        m = json.loads(f.read(1_000_000))
    if not isinstance(m, dict) or m.get("format") != FORMAT_VERSION or not isinstance(m.get("tables"), list):
        raise ValueError("This is not a portal backup, or it is from an incompatible version.")
    return m


def list_backups():
    out = []
    for name in sorted(os.listdir(backup_dir()), reverse=True):
        m = NAME_RE.match(name)
        if not m:
            continue
        p = os.path.join(backup_dir(), name)
        try:
            when = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            manifest = read_manifest(p)
            rows = sum(manifest.get("row_counts", {}).values())
            ok = True
        except Exception:  # unreadable or corrupt file stays listed so it can be deleted
            when = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            rows, ok = 0, False
        out.append({"name": name, "when": when, "kind": m.group(3), "size": os.path.getsize(p), "rows": rows, "ok": ok, "encrypted": is_encrypted(p)})
    return out


def delete_backup(name):
    os.remove(backup_path(name))


def apply_retention():
    """Scheduled backups: keep the newest N. Pre-restore safety backups: keep the newest few."""
    removed = 0
    keep = max(1, cfg_int("backup_retention"))
    for kind, limit in (("scheduled", keep), ("pre-restore", max(1, cfg_int("pre_restore_keep")))):
        items = [b for b in list_backups() if b["kind"] == kind]
        for b in items[limit:]:
            delete_backup(b["name"])
            removed += 1
    return removed


# ---------------------------------------------------------------- restore

def validate_archive(path, database_url):
    """Return the manifest if this archive can be restored into the current schema, else raise ValueError."""
    try:
        manifest = read_manifest(path)
    except (tarfile.TarError, OSError, json.JSONDecodeError, EOFError) as e:
        raise ValueError(f"The file could not be read as a backup ({e}).")
    with psycopg.connect(database_url) as conn, readable(path) as plain:
        db_tables = set(_tables_in_order(conn))
        with tarfile.open(plain, "r:gz") as tar:
            names = set(tar.getnames())
            for t in manifest["tables"]:
                if t not in db_tables:
                    raise ValueError(f"The backup contains a table ({t}) this version does not have. It is from a newer version.")
                if f"tables/{t}.csv" not in names:
                    raise ValueError(f"The backup is incomplete (missing data for {t}).")
                header = tar.extractfile(f"tables/{t}.csv").readline().decode().strip().split(",")
                header = [h.strip('"') for h in header]
                missing = [h for h in header if h not in _columns(conn, t)]
                if missing:
                    raise ValueError(f"The backup has columns ({', '.join(missing)}) that table {t} does not have.")
    return manifest


def pause_background(conn):
    """Inside a restore: wait for the background writers that touch many tables in one transaction (the weather statistics collector, the data updates) to finish and keep them out until the restore is over, so a restore never deadlocks with one of them."""
    conn.execute("SELECT pg_advisory_xact_lock(727011)")
    conn.execute("SELECT pg_advisory_xact_lock(727013)")   # the flight log recorder (flightlog.RESTORE_GUARD)
    try:
        from .dataupdates import JOBS
        for i in range(len(JOBS)):
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (727100 + i,))
    except Exception:
        pass


def restore_backup(path, database_url):
    """Replace all data with the archive's. One transaction: any failure leaves the data untouched."""
    manifest = validate_archive(path, database_url)
    with psycopg.connect(database_url) as conn, readable(path) as plain:
        with conn.transaction():
            conn.execute("SET LOCAL lock_timeout = '20s'")  # fail cleanly instead of waiting forever
            conn.execute("SELECT pg_advisory_xact_lock(727003)")
            pause_background(conn)
            tables = _tables_in_order(conn)
            if "settings" in tables:   # every request reads the settings first and other tables after; taking that one first here keeps the order the same, so a request in flight and the restore never wait on each other (a deadlock otherwise)
                conn.execute('LOCK TABLE "settings" IN ACCESS EXCLUSIVE MODE')
            conn.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE")
            with tarfile.open(plain, "r:gz") as tar:
                for t in tables:
                    if t not in manifest["tables"]:
                        continue
                    f = tar.extractfile(f"tables/{t}.csv")
                    header = f.readline()
                    cols = ", ".join(f'"{c.strip(chr(34))}"' for c in header.decode().strip().split(","))
                    with conn.cursor() as cur:
                        with cur.copy(f'COPY "{t}" ({cols}) FROM STDIN WITH (FORMAT csv, HEADER false)') as copy:
                            while True:
                                chunk = f.read(1 << 20)
                                if not chunk:
                                    break
                                copy.write(chunk)
            # restart identity counters after the highest restored value
            for t, col in conn.execute(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND (is_identity='YES' OR column_default LIKE 'nextval(%')").fetchall():
                seq = conn.execute("SELECT pg_get_serial_sequence(%s, %s)", (f'public."{t}"', col)).fetchone()[0]
                if seq:
                    conn.execute(f'SELECT setval(%s, COALESCE((SELECT MAX("{col}") FROM "{t}"), 0) + 1, false)', (seq,))
    return manifest


# ---------------------------------------------------------------- schedule

def _zone():
    try:
        return ZoneInfo(cfg("timezone"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def last_run():
    raw = get_setting("backup_last_run_at")
    return datetime.fromisoformat(raw) if raw else None


def is_due(now=None):
    mode = cfg("backup_schedule")
    if mode == "off":
        return False
    now = now or datetime.now(timezone.utc)
    last = last_run()
    if mode == "hourly":
        return last is None or now - last >= timedelta(hours=1)
    try:
        hh, mm = (int(x) for x in cfg("backup_time").split(":"))
    except ValueError:
        hh, mm = 2, 0
    local = now.astimezone(_zone())
    slot = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if slot > local:
        slot -= timedelta(days=1)
    if mode == "weekly":
        while slot.weekday() != 6:  # Sunday
            slot -= timedelta(days=1)
    return last is None or last < slot.astimezone(timezone.utc)


def run_scheduled(app):
    """One scheduler tick. A database lock makes sure only one worker runs it."""
    with app.app_context():
        url = app.config["DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as lock_conn:
            if not lock_conn.execute("SELECT pg_try_advisory_lock(727002)").fetchone()[0]:
                return
            try:
                if not is_due():
                    return
                try:
                    name, _ = create_backup(url, "scheduled")
                    removed = apply_retention()
                    set_setting("backup_last_status", f"ok: {name}" + (f" (pruned {removed})" if removed else ""))
                except Exception as e:  # recorded for the Backups page; never crash the loop
                    set_setting("backup_last_status", f"failed: {e}"[:300])
                set_setting("backup_last_run_at", datetime.now(timezone.utc).isoformat())
            finally:
                lock_conn.execute("SELECT pg_advisory_unlock(727002)")


def prune_history_daily(app):
    """Once a day, delete audit and sign-in history older than the retention setting."""
    from .history import prune
    with app.app_context():
        today = datetime.now(timezone.utc).date().isoformat()
        if get_setting("history_last_prune") == today:
            return
        with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock_conn:
            if not lock_conn.execute("SELECT pg_try_advisory_lock(727004)").fetchone()[0]:
                return
            try:
                if get_setting("history_last_prune") != today:
                    removed = prune()
                    set_setting("history_last_prune", today)
                    if removed:
                        from .audit import audit
                        audit("history.prune", detail=f"{removed} old rows removed", actor_name="system")
            finally:
                lock_conn.execute("SELECT pg_advisory_unlock(727004)")


def start_scheduler(app):
    def loop():
        time.sleep(20)
        while True:
            for job in (run_scheduled, prune_history_daily):
                try:
                    job(app)
                except Exception:
                    pass
            time.sleep(60)
    threading.Thread(target=loop, name="backup-scheduler", daemon=True).start()
