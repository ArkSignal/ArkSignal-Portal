"""Restore part of a backup: by area (accounts, stations, flight history...) or by individual record (one user, one station, one alert rule...).

The ordinary restore replaces everything. This module restores only what is chosen, in one transaction after a safety backup, in one of two modes:
  merge    adds the backup's rows and updates rows that exist; nothing the backup does not mention is deleted. The safe default.
  replace  (areas only) first removes the current rows of the chosen tables, so they end up exactly as in the backup. Refused when that would also delete
           rows of tables that were not chosen (for example replacing Accounts would delete the stations' owners), and the page says which.
Record restore brings back the chosen records together with everything that belongs to them (a station with its statistics and events; a user with their
roles and recovery codes), merged, never deleting anything.
"""
import csv
import io
import tarfile

import psycopg

from . import backup as bk

# (key, label, what it contains, tables). Tables in no area are collected under "Other data" so a future table is never unreachable.
AREAS = [
    ("accounts", "Accounts and Roles", "People who can sign in, their roles and permissions, two-factor recovery codes, passkeys", ["users", "roles", "role_permissions", "user_roles", "recovery_codes", "passkeys"]),
    ("stations", "Stations", "Every station: name, owner, location, privacy, website, MLAT user name", ["feeders"]),
    ("station_history", "Station Statistics and Events", "Message rates, range history, connection events", ["feeder_samples", "feeder_hourly", "feeder_range", "feeder_events"]),
    ("weather", "Weather Stations and Readings", "Members' weather stations (their upload addresses and sealed keys included), their readings and the hourly history", ["wx_stations", "wx_push", "wx_readings", "wx_hourly", "wx_obs", "wx_station_dir"]),
    ("sondes", "Radiosondes and Balloons", "Recorded balloon flights with their tracks, which receiving stations heard them, the receiving stations (claims included) and the launch sites", ["sondes", "sonde_points", "sonde_heard", "sonde_stations", "sonde_sites", "sonde_recoveries", "sonde_chase_share", "sonde_history_months", "sonde_history_skipped"]),
    ("mapdata", "Map Data", "Hospitals, places, county outlines, air ambulances, interesting aircraft and the address lookup cache", ["hospitals", "hospital_rings", "place_media", "roads", "places", "counties", "sat_tle", "hems_aircraft", "interesting_aircraft", "geocode_cache"]),
    ("flights", "Flight History", "The portal's log of every flight and aircraft it has heard", ["flight_log", "aircraft_log"]),
    ("alerts", "Alert Rules and History", "People's alert rules, what matched, emails, webhook sends and phone notifications", ["alert_rules", "alert_state", "alert_events", "alert_emails", "alert_webhooks", "alert_pushes"]),
    ("settings", "Settings, Menus and Layers", "Site settings, the Tools menu, map layers, contacts, airline logos", ["settings", "embedded_tools", "map_layers", "site_contacts", "airline_logos"]),
    ("repeaters", "Repeaters", "The repeater directory with the reports, claims and the history of every change", ["repeaters", "radio_entries", "repeater_reports", "repeater_edits", "repeater_talkgroups", "repeater_groups", "repeater_group_members", "dmr_talkgroups", "dmr_contacts", "frequencies"]),
    ("mesh", "Mesh Logins and Takedown List", "Members' mesh broker logins (the passwords sealed), their node alert choices and the alerts sent, their private channels (the keys sealed) and who they are shared with, the mesh communities (pages, teams and their roles, invitations, chosen nodes, notices, links, followers, their admin bridges and the commands queued for them), and the nodes hidden from the mesh pages on request (the nodes and their direct links live in the mesh service's own database and refill by themselves as nodes are heard again)", ["mesh_hidden", "mesh_accounts", "mesh_claims", "mesh_channels", "mesh_channel_members", "mesh_alert_prefs", "mesh_alert_state", "mesh_alert_events", "mesh_alert_sends", "communities", "community_members", "community_invites", "community_join_requests", "community_nodes", "community_posts", "community_links", "community_follows", "mesh_chan_log", "mesh_area_links", "community_pins", "community_channels", "mesh_bridges", "mesh_commands"]),
    ("acars", "ACARS Stations and Takedown List", "Members' ACARS receiving stations (with their private upload addresses) and the aircraft hidden from the ACARS pages on request (the messages themselves live in the ACARS service's own database)", ["acars_stations", "acars_hidden"]),
    ("aprs", "APRS Takedown List", "The call signs hidden from the APRS pages on request (the station data itself lives in the APRS service's own database and refills by itself)", ["aprs_hidden"]),
    ("airports", "Airport Data", "Airports, their frequencies and runways", ["airports", "airport_frequencies", "runways"]),
    ("satimages", "Satellite Receivers and Pictures", "Members' satellite receivers (with their private upload addresses, sealed) and the weather satellite pictures they sent", ["sat_receivers", "sat_images"]),
    ("google", "Google Sign-In Links", "Which Google account each member linked for signing in (the client secret is a setting, kept with the settings)", ["google_links"]),
    ("ais", "River Traffic Stations, Vessels and Takedown List", "Members' AIS stations (with their private upload addresses, sealed), the vessels they heard, their trails and the takedown list", ["ais_stations", "ais_vessels", "ais_positions", "ais_hidden"]),
    ("drones", "Drone Remote ID Stations, Drones and Takedown List", "Members' Remote ID stations (with their private upload addresses, sealed), the drones they heard, their trails and the takedown list", ["rid_stations", "rid_drones", "rid_positions", "rid_hidden"]),
    ("chat", "Chat, Messages and Friends", "Members' chats (direct and group, with their messages and who has read them), email-style messages with their folders, friend lists, blocks, mutes and reports, the radio bridge links and their log, and the old chat rooms and private threads from before the redesign (the private ones are the members' own: restore them only to recover from a loss)", ["chat_rooms", "chat_messages", "chat_mutes", "chat_reports", "pm_threads", "pm_messages", "pm_blocks", "pm_reports", "convs", "conv_members", "conv_messages", "conv_reports", "friendships", "mail_messages", "mail_recipients", "mail_reports", "bridge_links", "bridge_log", "mesh_outbox", "matrix_locks", "matrix_report_state", "matrix_mirrors", "bridge_seen", "push_subscriptions"]),
    ("api", "API Keys", "Members' API keys (only a fingerprint of each key is kept), their limits and the daily counts", ["api_keys", "api_key_daily"]),
    ("myflights", "My Flights", "Members' private flight diaries, with the people and seats on each flight the tracks attached to them and their share links (only a fingerprint of each link is kept)", ["my_flights", "my_flight_pax", "my_flight_tracks", "my_flight_shares"]),
    ("notifications", "Notification Choices, Places and Followed Balloons", "Which kinds of notification each member turned on or off, their places for weather, earthquake, river and satellite notices, the satellites they ticked and the balloons they follow", ["push_prefs", "member_places", "push_sats", "sonde_follows"]),
    ("audit", "Audit Log and Sign-In History", "Who did what, every sign-in, and what the retention runs removed", ["audit_log", "login_events", "retention_runs", "export_log"]),
    ("offsite", "Off-Site Backup History", "The record of off-site copies", ["offsite_runs"]),
]
TRANSIENT = {"flight_schedule_cache", "flight_schedule_calls", "push_sent", "push_seen", "password_resets", "reset_attempts", "user_sessions", "callsign_cache", "api_usage", "api_usage_hourly", "feed_usage_hourly", "svc_usage", "usage_state", "api_key_minute", "matrix_seen_events", "goes_images", "goes_frames", "chat_presence", "oidc_codes", "oidc_tokens"}   # short-lived: never restored in parts

# tables whose single records can be picked, with the columns that name a record
RECORD_TABLES = {
    "users": ("username", "email"), "feeders": ("station_id", "name", "uuid"), "roles": ("name",), "alert_rules": ("name", "kind", "value"),
    "embedded_tools": ("name", "slug"), "api_keys": ("name", "prefix"), "map_layers": ("name", "slug"), "site_contacts": ("name", "role"), "repeaters": ("callsign", "city"),
}
RECORD_LABELS = {"users": "People", "feeders": "Stations", "roles": "Roles", "alert_rules": "Alert rules", "embedded_tools": "Tools menu entries",
                 "map_layers": "Map layers", "site_contacts": "Contacts", "repeaters": "Repeaters", "api_keys": "API keys"}


def areas_for(manifest_tables):
    """The areas with only the tables this backup has, plus 'Other data' for anything unassigned."""
    have = set(manifest_tables)
    out, used = [], set()
    for key, label, text, tables in AREAS:
        t = [x for x in tables if x in have]
        used |= set(tables)
        if t:
            out.append({"key": key, "label": label, "text": text, "tables": t})
    rest = sorted(have - used - TRANSIENT)
    if rest:
        out.append({"key": "other", "label": "Other Data", "text": "Anything not listed above", "tables": rest})
    return out


# ---------------------------------------------------------------- schema facts

def _pks(conn):
    rows = conn.execute("SELECT c.relname, a.attname FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum = ANY(i.indkey) "
                        "WHERE i.indisprimary AND c.relnamespace='public'::regnamespace ORDER BY c.relname, array_position(i.indkey, a.attnum)").fetchall()
    out = {}
    for t, col in rows:
        out.setdefault(t, []).append(col)
    return out


def _fks(conn):
    """[(child table, child column, parent table, parent column, delete action)] for single-column foreign keys."""
    rows = conn.execute(
        "SELECT cl.relname, ca.attname, pl.relname, pa.attname, c.confdeltype FROM pg_constraint c "
        "JOIN pg_class cl ON cl.oid=c.conrelid JOIN pg_class pl ON pl.oid=c.confrelid "
        "JOIN pg_attribute ca ON ca.attrelid=c.conrelid AND ca.attnum=c.conkey[1] JOIN pg_attribute pa ON pa.attrelid=c.confrelid AND pa.attnum=c.confkey[1] "
        "WHERE c.contype='f' AND c.connamespace='public'::regnamespace AND array_length(c.conkey,1)=1").fetchall()
    return [tuple(r) for r in rows]


def _load_temp(conn, plain, manifest, table, cols_cache):
    """Load the backup's rows of one table into a temporary table `tmp_<table>`; returns the column list."""
    with tarfile.open(plain, "r:gz") as tar:
        f = tar.extractfile(f"tables/{table}.csv")
        header = f.readline()
        cols = [c.strip('"') for c in header.decode().strip().split(",")]
        conn.execute(f'CREATE TEMP TABLE "tmp_{table}" (LIKE "{table}" INCLUDING DEFAULTS) ON COMMIT DROP')
        collist = ", ".join(f'"{c}"' for c in cols)
        with conn.cursor() as cur:
            with cur.copy(f'COPY "tmp_{table}" ({collist}) FROM STDIN WITH (FORMAT csv, HEADER false)') as copy:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    copy.write(chunk)
    cols_cache[table] = cols
    return cols


def _upsert(conn, table, cols, pks, replaced=False):
    """Write the temp table's rows into the real one. With a primary key: insert, updating rows that exist. Without one (statistics tables): a plain insert
    when the table was just emptied, otherwise only the rows that are not already there, compared column by column. Returns the rows written."""
    collist = ", ".join(f'"{c}"' for c in cols)
    if not pks.get(table):
        if replaced:
            return conn.execute(f'INSERT INTO "{table}" ({collist}) OVERRIDING SYSTEM VALUE SELECT {collist} FROM "tmp_{table}"').rowcount
        same = ", ".join(f'r."{c}"' for c in cols), ", ".join(f'x."{c}"' for c in cols)
        return conn.execute(f'INSERT INTO "{table}" ({collist}) OVERRIDING SYSTEM VALUE SELECT {collist} FROM "tmp_{table}" x '
                            f'WHERE NOT EXISTS (SELECT 1 FROM "{table}" r WHERE ROW({same[0]}) IS NOT DISTINCT FROM ROW({same[1]}))').rowcount
    pk = ", ".join(f'"{c}"' for c in pks[table])
    sets = [c for c in cols if c not in pks[table]]
    conflict = ("DO UPDATE SET " + ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in sets)) if sets else "DO NOTHING"
    cur = conn.execute(f'INSERT INTO "{table}" ({collist}) OVERRIDING SYSTEM VALUE SELECT {collist} FROM "tmp_{table}" ON CONFLICT ({pk}) {conflict}')
    return cur.rowcount


def _reset_identities(conn):
    for t, col in conn.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public' AND (is_identity='YES' OR column_default LIKE 'nextval(%')").fetchall():
        seq = conn.execute("SELECT pg_get_serial_sequence(%s, %s)", (f'public."{t}"', col)).fetchone()[0]
        if seq:
            conn.execute(f'SELECT setval(%s, COALESCE((SELECT MAX("{col}") FROM "{t}"), 0) + 1, false)', (seq,))


def _explain(e):
    msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    if "foreign key" in msg.lower():
        return msg + " (something in the backup refers to a record that does not exist now: also restore what it belongs to, for example Accounts for stations, then try again)"
    if "unique" in msg.lower() or "duplicate" in msg.lower():
        return msg + " (a record in the backup clashes with a different current record that has the same unique value, such as a user name that has since been used by someone else)"
    return msg


# ---------------------------------------------------------------- preview

def preview(path, database_url):
    """Areas with, for each table, the backup's row count and the current row count."""
    manifest = bk.validate_archive(path, database_url)
    with psycopg.connect(database_url) as conn:
        out = []
        for a in areas_for(manifest["tables"]):
            rows = []
            for t in a["tables"]:
                now = conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                rows.append({"table": t, "backup": manifest["row_counts"].get(t, 0), "now": now})
            out.append(dict(a, rows=rows, backup_total=sum(r["backup"] for r in rows), now_total=sum(r["now"] for r in rows)))
        records = {t: (RECORD_LABELS[t], manifest["row_counts"].get(t, 0)) for t in RECORD_TABLES if t in manifest["tables"]}
    return {"manifest": manifest, "areas": out, "records": records}


def unselected_dependents(conn, tables):
    """Tables not chosen whose rows would be deleted or changed if the chosen tables were emptied (cascade or set-null foreign keys)."""
    chosen = set(tables)
    return sorted({c for c, _cc, p, _pc, act in _fks(conn) if p in chosen and c not in chosen and c not in TRANSIENT and act in ("c", "n", "d")})


# ---------------------------------------------------------------- restore by area

def restore_areas(path, database_url, area_keys, mode):
    """Returns {table: rows written}. One transaction; raises ValueError with a plain sentence when it cannot be done."""
    if mode not in ("merge", "replace"):
        raise ValueError("Choose merge or replace.")
    manifest = bk.validate_archive(path, database_url)
    chosen_areas = [a for a in areas_for(manifest["tables"]) if a["key"] in set(area_keys)]
    if not chosen_areas:
        raise ValueError("Choose at least one area.")
    tables = [t for a in chosen_areas for t in a["tables"]]
    written = {}
    with psycopg.connect(database_url) as conn, bk.readable(path) as plain:
        try:
            with conn.transaction():
                conn.execute("SET LOCAL lock_timeout = '20s'")
                conn.execute("SELECT pg_advisory_xact_lock(727003)")
                from .backup import pause_background
                pause_background(conn)
                order = [t for t in bk._tables_in_order(conn) if t in tables]
                pks, cache = _pks(conn), {}
                if mode == "replace":
                    dep = unselected_dependents(conn, order)
                    if dep:
                        raise ValueError("Replace would also delete or change data in " + ", ".join(dep) + ", which you did not choose. "
                                         "Choose Merge, or tick the areas that contain them as well.")
                    for t in reversed(order):
                        conn.execute(f'DELETE FROM "{t}"')
                for t in order:
                    cols = _load_temp(conn, plain, manifest, t, cache)
                    written[t] = _upsert(conn, t, cols, pks, replaced=(mode == "replace"))
                _reset_identities(conn)
        except psycopg.Error as e:
            raise ValueError(_explain(e))
    return written


# ---------------------------------------------------------------- restore by record

def _closure(fks, root, have):
    """Tables that belong to `root`: those that point at it, and those that point at those. [(child, child column, parent, parent column)] in order."""
    out, seen, frontier = [], {root}, [root]
    while frontier:
        nxt = []
        for p in frontier:
            for c, cc, pt, pc, _act in fks:
                if pt == p and c in have and c not in seen and c not in TRANSIENT:
                    out.append((c, cc, pt, pc)); seen.add(c); nxt.append(c)
        frontier = nxt
    return out


def list_records(path, database_url, table, limit=2000):
    """The backup's records of one table, each with a readable label and whether it exists now."""
    if table not in RECORD_TABLES:
        raise ValueError("That kind of record cannot be picked one by one.")
    manifest = bk.validate_archive(path, database_url)
    if table not in manifest["tables"]:
        raise ValueError("The backup has none of those.")
    with psycopg.connect(database_url) as conn, bk.readable(path) as plain:
        pks = _pks(conn)
        pk = pks.get(table, [])
        if len(pk) != 1:
            raise ValueError("That kind of record cannot be picked one by one.")
        cols = bk._columns(conn, table)
        labels = [c for c in RECORD_TABLES[table] if c in cols]
        cache = {}
        _load_temp(conn, plain, manifest, table, cache)
        sel = ", ".join(f't."{c}"::text' for c in labels) or f't."{pk[0]}"::text'
        rows = conn.execute(f'SELECT t."{pk[0]}"::text, {sel}, EXISTS (SELECT 1 FROM "{table}" r WHERE r."{pk[0]}" = t."{pk[0]}") FROM "tmp_{table}" t ORDER BY 2 LIMIT %s', (limit,)).fetchall()
        conn.rollback()
    return [{"id": r[0], "label": " · ".join(x for x in r[1:-1] if x) or r[0], "exists": r[-1]} for r in rows]


def restore_records(path, database_url, table, ids):
    """Merge the chosen records of `table` and everything that belongs to them. Returns {table: rows written}."""
    if table not in RECORD_TABLES or not ids:
        raise ValueError("Choose at least one record.")
    manifest = bk.validate_archive(path, database_url)
    written = {}
    with psycopg.connect(database_url) as conn, bk.readable(path) as plain:
        try:
            with conn.transaction():
                conn.execute("SET LOCAL lock_timeout = '20s'")
                conn.execute("SELECT pg_advisory_xact_lock(727003)")
                from .backup import pause_background
                pause_background(conn)
                pks, fks, cache = _pks(conn), _fks(conn), {}
                pk = pks.get(table, [])
                if len(pk) != 1:
                    raise ValueError("That kind of record cannot be picked one by one.")
                have = set(manifest["tables"])
                family = _closure(fks, table, have)
                _load_temp(conn, plain, manifest, table, cache)
                conn.execute(f'DELETE FROM "tmp_{table}" WHERE "{pk[0]}"::text <> ALL(%s)', (list(ids),))
                for c, cc, p, pc in family:
                    _load_temp(conn, plain, manifest, c, cache)
                    conn.execute(f'DELETE FROM "tmp_{c}" WHERE "{cc}" IS NULL OR NOT EXISTS (SELECT 1 FROM "tmp_{p}" x WHERE x."{pc}" = "tmp_{c}"."{cc}")')
                order = [t for t in bk._tables_in_order(conn) if t == table or t in {f[0] for f in family}]
                for t in order:
                    n = conn.execute(f'SELECT count(*) FROM "tmp_{t}"').fetchone()[0]
                    if n:
                        written[t] = _upsert(conn, t, cache[t], pks)
                _reset_identities(conn)
        except psycopg.Error as e:
            raise ValueError(_explain(e))
    return written
