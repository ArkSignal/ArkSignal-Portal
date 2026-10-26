import psycopg
from flask import current_app, g
from psycopg.rows import dict_row

from .permissions import ADMIN_ROLE, USER_ROLE

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username TEXT NOT NULL,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    callsign TEXT,
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    disabled BOOLEAN NOT NULL DEFAULT FALSE,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower ON users (lower(username));
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower ON users (lower(email));

CREATE TABLE IF NOT EXISTS password_resets (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS reset_attempts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip TEXT,
    subject TEXT,
    method TEXT,
    ok BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS reset_attempts_ts ON reset_attempts (ts);
CREATE TABLE IF NOT EXISTS site_contacts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 100,
    shown BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS roles (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_super_admin BOOLEAN NOT NULL DEFAULT FALSE,
    is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS roles_name_lower ON roles (lower(name));

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission TEXT NOT NULL,
    PRIMARY KEY (role_id, permission)
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_id BIGINT,
    actor_name TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_ts ON audit_log (ts DESC);

CREATE TABLE IF NOT EXISTS login_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    username TEXT,
    ip TEXT,
    success BOOLEAN NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS user_sessions (
    sid TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip TEXT,
    ua TEXT,
    ended BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS user_sessions_user ON user_sessions (user_id, last_seen DESC);
CREATE INDEX IF NOT EXISTS login_events_ip_ts ON login_events (ip, ts DESC);

CREATE TABLE IF NOT EXISTS feeders (
    uuid TEXT PRIMARY KEY,
    station_id TEXT,
    name TEXT,
    description TEXT,
    notes TEXT,
    website TEXT,
    owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    connected_since TIMESTAMPTZ,
    last_ip TEXT,
    online BOOLEAN NOT NULL DEFAULT FALSE,
    banned BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS feeders_station_id_lower ON feeders (lower(station_id)) WHERE station_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS feeders_owner ON feeders (owner_id);

CREATE TABLE IF NOT EXISTS feeder_range (
    feeder_uuid TEXT NOT NULL REFERENCES feeders(uuid) ON DELETE CASCADE,
    day DATE NOT NULL,
    sector SMALLINT NOT NULL,
    max_nm REAL NOT NULL,
    max_alt INTEGER,
    PRIMARY KEY (feeder_uuid, day, sector)
);

CREATE TABLE IF NOT EXISTS feeder_samples (
    feeder_uuid TEXT NOT NULL REFERENCES feeders(uuid) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,
    msg_per_s REAL,
    pos_per_s REAL,
    kbit_per_s REAL,
    rtt_ms REAL,
    conn_secs INTEGER,
    positions BIGINT,
    msg_total BIGINT,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS feeder_samples_feeder_ts ON feeder_samples (feeder_uuid, ts DESC);
CREATE INDEX IF NOT EXISTS feeder_samples_ts ON feeder_samples (ts);

CREATE TABLE IF NOT EXISTS feeder_hourly (
    feeder_uuid TEXT NOT NULL REFERENCES feeders(uuid) ON DELETE CASCADE,
    hour TIMESTAMPTZ NOT NULL,
    samples INTEGER NOT NULL,
    msg_per_s REAL,
    pos_per_s REAL,
    PRIMARY KEY (feeder_uuid, hour)
);

CREATE TABLE IF NOT EXISTS feeder_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feeder_uuid TEXT NOT NULL REFERENCES feeders(uuid) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    event TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS feeder_events_feeder_ts ON feeder_events (feeder_uuid, ts DESC);

CREATE TABLE IF NOT EXISTS airports (
    id INTEGER PRIMARY KEY,
    ident TEXT NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    elevation_ft INTEGER,
    country TEXT,
    region TEXT,
    municipality TEXT,
    scheduled_service BOOLEAN NOT NULL DEFAULT FALSE,
    icao_code TEXT,
    iata_code TEXT,
    gps_code TEXT,
    local_code TEXT,
    home_link TEXT,
    wikipedia_link TEXT
);
CREATE INDEX IF NOT EXISTS airports_ident ON airports (lower(ident));
CREATE INDEX IF NOT EXISTS airports_region ON airports (region);
CREATE TABLE IF NOT EXISTS places (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT '',
    geoid TEXT NOT NULL DEFAULT '',
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    land_sqmi DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS places_kind_name ON places (kind, lower(name) text_pattern_ops);
CREATE TABLE IF NOT EXISTS hospitals (
    facility_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    zip TEXT NOT NULL DEFAULT '',
    county TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    htype TEXT NOT NULL DEFAULT '',
    ownership TEXT NOT NULL DEFAULT '',
    emergency BOOLEAN NOT NULL DEFAULT FALSE,
    rating INTEGER,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    pos_source TEXT NOT NULL DEFAULT '',
    trauma TEXT NOT NULL DEFAULT '',
    services TEXT NOT NULL DEFAULT '',
    heliport_ident TEXT NOT NULL DEFAULT '',
    heliport_set BOOLEAN NOT NULL DEFAULT FALSE,
    notes TEXT NOT NULL DEFAULT '',
    updated TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hospitals_state ON hospitals (state);
CREATE TABLE IF NOT EXISTS hems_aircraft (
    ident TEXT PRIMARY KEY,
    hex TEXT,
    reg TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'registry',
    excluded BOOLEAN NOT NULL DEFAULT FALSE,
    note TEXT NOT NULL DEFAULT '',
    added TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS airport_frequencies (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    airport_id INTEGER NOT NULL REFERENCES airports(id) ON DELETE CASCADE,
    type TEXT,
    description TEXT,
    mhz DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS airport_frequencies_airport ON airport_frequencies (airport_id);
CREATE TABLE IF NOT EXISTS runways (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    airport_id INTEGER NOT NULL REFERENCES airports(id) ON DELETE CASCADE,
    length_ft INTEGER,
    width_ft INTEGER,
    surface TEXT,
    lighted BOOLEAN NOT NULL DEFAULT FALSE,
    closed BOOLEAN NOT NULL DEFAULT FALSE,
    le_ident TEXT,
    he_ident TEXT,
    le_heading DOUBLE PRECISION,
    he_heading DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS runways_airport ON runways (airport_id);

CREATE TABLE IF NOT EXISTS embedded_tools (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS map_layers (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    grp TEXT NOT NULL DEFAULT 'Layers',
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '',
    attribution TEXT NOT NULL DEFAULT '',
    opacity DOUBLE PRECISION NOT NULL DEFAULT 1,
    min_zoom INTEGER NOT NULL DEFAULT 0,
    max_zoom INTEGER NOT NULL DEFAULT 19,
    color TEXT NOT NULL DEFAULT '#ff4d4d',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    default_on BOOLEAN NOT NULL DEFAULT FALSE,
    is_base BOOLEAN NOT NULL DEFAULT FALSE,
    refresh_minutes INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS alert_rules (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    area_lat DOUBLE PRECISION,
    area_lon DOUBLE PRECISION,
    area_radius_nm DOUBLE PRECISION,
    alt_min_ft DOUBLE PRECISION,
    alt_max_ft DOUBLE PRECISION,
    cooldown_minutes INTEGER NOT NULL DEFAULT 30,
    notify_email BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alert_rules_user ON alert_rules (user_id);
CREATE TABLE IF NOT EXISTS alert_state (
    rule_id BIGINT NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    hex TEXT NOT NULL,
    last_alert TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (rule_id, hex)
);
CREATE TABLE IF NOT EXISTS alert_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    hex TEXT NOT NULL,
    callsign TEXT,
    registration TEXT,
    type TEXT,
    alt_ft DOUBLE PRECISION,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    squawk TEXT
);
CREATE INDEX IF NOT EXISTS alert_events_user_ts ON alert_events (user_id, ts DESC);

CREATE TABLE IF NOT EXISTS alert_emails (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    events INTEGER NOT NULL,
    ok BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS alert_emails_user_ts ON alert_emails (user_id, ts DESC);

CREATE TABLE IF NOT EXISTS airline_logos (
    code TEXT PRIMARY KEY,                 -- ICAO airline code (the first three letters of a callsign)
    content BYTEA,                         -- the image as fetched (NULL when no logo was found)
    content_type TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS flight_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hex TEXT NOT NULL,
    callsign TEXT,
    reg TEXT,
    type TEXT,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    start_lat DOUBLE PRECISION, start_lon DOUBLE PRECISION,
    end_lat DOUBLE PRECISION, end_lon DOUBLE PRECISION,
    max_alt INTEGER, max_gs INTEGER,
    positions INTEGER NOT NULL DEFAULT 0,
    mil BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS flight_log_hex ON flight_log (hex, last_seen DESC);
CREATE INDEX IF NOT EXISTS flight_log_seen ON flight_log (last_seen DESC);
CREATE INDEX IF NOT EXISTS flight_log_callsign ON flight_log (callsign);
CREATE INDEX IF NOT EXISTS flight_log_reg ON flight_log (reg);

CREATE TABLE IF NOT EXISTS aircraft_log (
    hex TEXT PRIMARY KEY,
    reg TEXT,
    type TEXT,
    description TEXT,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    flights INTEGER NOT NULL DEFAULT 0,
    mil BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


MIGRATIONS = [
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS official BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS official_order INTEGER NOT NULL DEFAULT 100",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS website_public BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS mlat_user TEXT",
    "CREATE TABLE IF NOT EXISTS offsite_runs (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, kind TEXT NOT NULL, trigger TEXT NOT NULL DEFAULT 'schedule', started TIMESTAMPTZ NOT NULL DEFAULT now(), finished TIMESTAMPTZ, ok BOOLEAN, bytes BIGINT NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '', remote_name TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS offsite_runs_kind_started ON offsite_runs (kind, started DESC)",
    "ALTER TABLE offsite_runs ADD COLUMN IF NOT EXISTS db_backup TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE offsite_runs ADD COLUMN IF NOT EXISTS source_backup TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE embedded_tools ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE map_layers ADD COLUMN IF NOT EXISTS refresh_minutes INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS prefs TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE airline_logos ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS webhook_url TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_last BIGINT NOT NULL DEFAULT 0",
    "CREATE TABLE IF NOT EXISTS recovery_codes (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, code_hash TEXT NOT NULL, used_at TIMESTAMPTZ)",
    "CREATE INDEX IF NOT EXISTS recovery_codes_user ON recovery_codes (user_id)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS webhook_kind TEXT NOT NULL DEFAULT 'json'",
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS notify_webhook BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS webhook_state TEXT NOT NULL DEFAULT 'none'",
    "CREATE TABLE IF NOT EXISTS alert_webhooks (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, ts TIMESTAMPTZ NOT NULL DEFAULT now(), events INTEGER NOT NULL, ok BOOLEAN NOT NULL DEFAULT TRUE)",
    "CREATE INDEX IF NOT EXISTS alert_webhooks_user_ts ON alert_webhooks (user_id, ts DESC)",
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS notify_push BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS push_state TEXT NOT NULL DEFAULT 'none'",
    "CREATE TABLE IF NOT EXISTS alert_pushes (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, ts TIMESTAMPTZ NOT NULL DEFAULT now(), events INTEGER NOT NULL, ok BOOLEAN NOT NULL DEFAULT TRUE)",
    "CREATE INDEX IF NOT EXISTS alert_pushes_user_ts ON alert_pushes (user_id, ts DESC)",
    "CREATE TABLE IF NOT EXISTS retention_runs (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(), who TEXT NOT NULL DEFAULT '', what TEXT NOT NULL DEFAULT '', rows_removed BIGINT NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS communities (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, about TEXT NOT NULL DEFAULT '', logo_url TEXT NOT NULL DEFAULT '', website TEXT NOT NULL DEFAULT '', join_channel TEXT NOT NULL DEFAULT '', join_preset TEXT NOT NULL DEFAULT '', join_region TEXT NOT NULL DEFAULT '', join_steps TEXT NOT NULL DEFAULT '', south DOUBLE PRECISION, west DOUBLE PRECISION, north DOUBLE PRECISION, east DOUBLE PRECISION, published BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), created_by BIGINT REFERENCES users(id) ON DELETE SET NULL)",
    "CREATE TABLE IF NOT EXISTS community_members (community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, role TEXT NOT NULL, added_by BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (community_id, user_id))",
    "CREATE TABLE IF NOT EXISTS community_invites (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, role TEXT NOT NULL, token_hash TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', created_by BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL, state TEXT NOT NULL DEFAULT 'pending', accepted_by BIGINT)",
    "CREATE TABLE IF NOT EXISTS community_nodes (community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, node_key TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'include', PRIMARY KEY (community_id, node_key))",
    "CREATE TABLE IF NOT EXISTS community_posts (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, author_id BIGINT REFERENCES users(id) ON DELETE SET NULL, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'notice', event_at TIMESTAMPTZ, pinned BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS community_links (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, label TEXT NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS community_follows (community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (community_id, user_id))",
    "ALTER TABLE communities ADD COLUMN IF NOT EXISTS area_poly TEXT NOT NULL DEFAULT ''",
    "UPDATE settings SET value='members' WHERE key IN ('access_myflights','access_chat','access_messages','access_friends','access_matrix','access_bridges') AND value='everyone' AND NOT EXISTS (SELECT 1 FROM settings WHERE key='access_member_defaults_applied')",   # features that need a sign-in were saved as 'everyone' while that was the default for all of them: once, they become members only
    "INSERT INTO settings (key, value) VALUES ('access_member_defaults_applied', '1') ON CONFLICT (key) DO NOTHING",
    "CREATE TABLE IF NOT EXISTS community_join_requests (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, message TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), decided_by BIGINT, decided_at TIMESTAMPTZ)",
    "CREATE UNIQUE INDEX IF NOT EXISTS community_join_requests_one ON community_join_requests (community_id, user_id) WHERE state = 'pending'",
    "ALTER TABLE communities ADD COLUMN IF NOT EXISTS feed_site_map BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE communities ADD COLUMN IF NOT EXISTS feed_public BOOLEAN NOT NULL DEFAULT FALSE",
    "CREATE TABLE IF NOT EXISTS community_channels (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, name TEXT NOT NULL, visibility TEXT NOT NULL DEFAULT 'team', min_role TEXT NOT NULL DEFAULT 'viewer', key_kind TEXT NOT NULL DEFAULT 'random', psk_sealed TEXT NOT NULL DEFAULT '', uplink BOOLEAN NOT NULL DEFAULT false, downlink BOOLEAN NOT NULL DEFAULT false, precision INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', created_by BIGINT REFERENCES users(id) ON DELETE SET NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), rotated TIMESTAMPTZ)",
    "CREATE UNIQUE INDEX IF NOT EXISTS community_channels_name ON community_channels (community_id, lower(name))",
    "CREATE TABLE IF NOT EXISTS community_pins (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active', lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL, node_key TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', created_by BIGINT REFERENCES users(id) ON DELETE SET NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS mesh_bridges (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, name TEXT NOT NULL, token_hash TEXT NOT NULL, created_by BIGINT REFERENCES users(id) ON DELETE SET NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen TIMESTAMPTZ, version TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS mesh_bridges_token ON mesh_bridges (token_hash)",
    "CREATE TABLE IF NOT EXISTS mesh_commands (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, bridge_id BIGINT NOT NULL REFERENCES mesh_bridges(id) ON DELETE CASCADE, node_key TEXT NOT NULL, kind TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}', risky BOOLEAN NOT NULL DEFAULT false, state TEXT NOT NULL DEFAULT 'pending', requested_by BIGINT REFERENCES users(id) ON DELETE SET NULL, requested_at TIMESTAMPTZ NOT NULL DEFAULT now(), decided_by BIGINT REFERENCES users(id) ON DELETE SET NULL, decided_at TIMESTAMPTZ, sent_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, result TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS mesh_commands_bridge ON mesh_commands (bridge_id, state)",
    "CREATE TABLE IF NOT EXISTS my_flights (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, mode TEXT NOT NULL DEFAULT 'detailed', flight_no TEXT NOT NULL DEFAULT '', airline TEXT NOT NULL DEFAULT '', from_ident TEXT NOT NULL DEFAULT '', from_code TEXT NOT NULL DEFAULT '', from_name TEXT NOT NULL DEFAULT '', from_city TEXT NOT NULL DEFAULT '', from_lat DOUBLE PRECISION, from_lon DOUBLE PRECISION, from_tz TEXT NOT NULL DEFAULT 'UTC', to_ident TEXT NOT NULL DEFAULT '', to_code TEXT NOT NULL DEFAULT '', to_name TEXT NOT NULL DEFAULT '', to_city TEXT NOT NULL DEFAULT '', to_lat DOUBLE PRECISION, to_lon DOUBLE PRECISION, to_tz TEXT NOT NULL DEFAULT 'UTC', aircraft_type TEXT NOT NULL DEFAULT '', registration TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', distance_km REAL, sched_out TIMESTAMPTZ, act_out TIMESTAMPTZ, sched_off TIMESTAMPTZ, act_off TIMESTAMPTZ, sched_on TIMESTAMPTZ, act_on TIMESTAMPTZ, sched_in TIMESTAMPTZ, act_in TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE INDEX IF NOT EXISTS my_flights_user ON my_flights (user_id)",
    "ALTER TABLE my_flights ADD COLUMN IF NOT EXISTS from_country TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE my_flights ADD COLUMN IF NOT EXISTS to_country TEXT NOT NULL DEFAULT ''",
    "CREATE TABLE IF NOT EXISTS my_flight_pax (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, flight_id BIGINT NOT NULL REFERENCES my_flights(id) ON DELETE CASCADE, name TEXT NOT NULL, klass TEXT NOT NULL DEFAULT 'Economy', reason TEXT NOT NULL DEFAULT 'Leisure', seat TEXT NOT NULL DEFAULT '', seat_kind TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS my_flight_pax_flight ON my_flight_pax (flight_id)",
    "CREATE TABLE IF NOT EXISTS my_flight_tracks (flight_id BIGINT PRIMARY KEY REFERENCES my_flights(id) ON DELETE CASCADE, source TEXT NOT NULL DEFAULT '', points TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS my_flight_shares (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, flight_id BIGINT REFERENCES my_flights(id) ON DELETE CASCADE, token_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL DEFAULT '', show_track BOOLEAN NOT NULL DEFAULT TRUE, show_reg BOOLEAN NOT NULL DEFAULT FALSE, show_people BOOLEAN NOT NULL DEFAULT FALSE, show_notes BOOLEAN NOT NULL DEFAULT FALSE, expires_at TIMESTAMPTZ, views INTEGER NOT NULL DEFAULT 0, last_viewed TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE INDEX IF NOT EXISTS my_flight_shares_user ON my_flight_shares (user_id)",
    "CREATE TABLE IF NOT EXISTS push_prefs (user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, kind TEXT NOT NULL, enabled BOOLEAN NOT NULL, PRIMARY KEY (user_id, kind))",
    "CREATE TABLE IF NOT EXISTS push_sent (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, kind TEXT NOT NULL, ts TIMESTAMPTZ NOT NULL DEFAULT now(), ok BOOLEAN NOT NULL DEFAULT TRUE)",
    "CREATE INDEX IF NOT EXISTS push_sent_user_kind ON push_sent (user_id, kind, ts)",
    "CREATE TABLE IF NOT EXISTS push_seen (user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, kind TEXT NOT NULL, key TEXT NOT NULL, ts TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (user_id, kind, key))",
    "CREATE TABLE IF NOT EXISTS member_places (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL, radius_km DOUBLE PRECISION NOT NULL DEFAULT 25, created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE INDEX IF NOT EXISTS member_places_user ON member_places (user_id)",
    "CREATE TABLE IF NOT EXISTS push_sats (user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, norad INTEGER NOT NULL, PRIMARY KEY (user_id, norad))",
    "CREATE TABLE IF NOT EXISTS sonde_follows (user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, serial TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (user_id, serial))",
    "CREATE TABLE IF NOT EXISTS flight_schedule_cache (key TEXT PRIMARY KEY, body TEXT NOT NULL, fetched TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS flight_schedule_calls (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE SET NULL, ts TIMESTAMPTZ NOT NULL DEFAULT now(), ok BOOLEAN NOT NULL DEFAULT TRUE)",
    "CREATE INDEX IF NOT EXISTS flight_schedule_calls_ts ON flight_schedule_calls (ts)",
    "ALTER TABLE communities ADD COLUMN IF NOT EXISTS alert_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE communities ADD COLUMN IF NOT EXISTS alert_channel_id BIGINT",
    "ALTER TABLE communities DROP CONSTRAINT IF EXISTS communities_alert_channel_id_fkey",
    "ALTER TABLE communities ADD COLUMN IF NOT EXISTS alert_nws BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE communities ADD COLUMN IF NOT EXISTS alert_weather BOOLEAN NOT NULL DEFAULT TRUE",
    "CREATE TABLE IF NOT EXISTS mesh_chan_log (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, ts TIMESTAMPTZ NOT NULL DEFAULT now(), kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', ok BOOLEAN NOT NULL DEFAULT TRUE, key TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS mesh_chan_log_community ON mesh_chan_log (community_id, ts DESC)",
    "CREATE TABLE IF NOT EXISTS mesh_area_links (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, a_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, b_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE, channel TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'proposed', paused BOOLEAN NOT NULL DEFAULT FALSE, hop_limit INTEGER NOT NULL DEFAULT 3, rate_per_min INTEGER NOT NULL DEFAULT 20, proposed_by BIGINT REFERENCES users(id) ON DELETE SET NULL, accepted_by BIGINT REFERENCES users(id) ON DELETE SET NULL, approved_by BIGINT REFERENCES users(id) ON DELETE SET NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE INDEX IF NOT EXISTS mesh_area_links_a ON mesh_area_links (a_id)",
    "CREATE INDEX IF NOT EXISTS mesh_area_links_b ON mesh_area_links (b_id)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS push_quiet_start SMALLINT NOT NULL DEFAULT -1",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS push_quiet_end SMALLINT NOT NULL DEFAULT -1",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS push_quiet_urgent BOOLEAN NOT NULL DEFAULT TRUE",
    "CREATE TABLE IF NOT EXISTS export_log (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, ts TIMESTAMPTZ NOT NULL DEFAULT now(), kind TEXT NOT NULL, rows INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS export_log_user_ts ON export_log (user_id, ts DESC)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS push_friends BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE map_layers ADD COLUMN IF NOT EXISTS wx_group TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE map_layers ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS alerts_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    "CREATE TABLE IF NOT EXISTS interesting_aircraft (hex TEXT PRIMARY KEY, reg TEXT NOT NULL DEFAULT '', type TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual', excluded BOOLEAN NOT NULL DEFAULT FALSE, added TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS geocode_cache (finder TEXT NOT NULL, query TEXT NOT NULL, answer TEXT NOT NULL, fetched TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (finder, query))",
    "ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS pos_check TEXT NOT NULL DEFAULT ''",
    # the trauma system: which hospitals the Trauma Map shows ('state' in Arkansas, 'system' designated by the state, 'border' within the drive time of the state line) and the website and picture of a hospital or a county
    "ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS trauma_scope TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS border_min REAL",
    "CREATE TABLE IF NOT EXISTS place_media (kind TEXT NOT NULL, key TEXT NOT NULL, website TEXT NOT NULL DEFAULT '', photo_url TEXT NOT NULL DEFAULT '', photo_credit TEXT NOT NULL DEFAULT '', photo_page TEXT NOT NULL DEFAULT '', "
    "source TEXT NOT NULL DEFAULT '', edited BOOLEAN NOT NULL DEFAULT FALSE, checked TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (kind, key))",
    "ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS pos_checked TIMESTAMPTZ",
    "ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS trauma_date TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE hems_aircraft ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'heli'",
    "ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS trauma_source TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS email_state TEXT NOT NULL DEFAULT 'none'",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS lon DOUBLE PRECISION",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS antenna_height_ft REAL",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS height_ref TEXT",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS ground_elev_ft REAL",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS privacy TEXT",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS station_id_kind TEXT",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS claim_method TEXT",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS setup_receiver TEXT",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS setup_antenna TEXT",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS setup_filter_lna TEXT",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS setup_feedline TEXT",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS setup_computer TEXT",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS setup_software TEXT",
    "ALTER TABLE feeders ADD COLUMN IF NOT EXISTS setup_other TEXT",
    "CREATE TABLE IF NOT EXISTS acars_stations (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, station_id TEXT NOT NULL, owner_id BIGINT REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL DEFAULT '', "
    "description TEXT NOT NULL DEFAULT '', hardware TEXT NOT NULL DEFAULT '', bands TEXT NOT NULL DEFAULT '', lat DOUBLE PRECISION, lon DOUBLE PRECISION, privacy TEXT NOT NULL DEFAULT 'general', "
    "public BOOLEAN NOT NULL DEFAULT false, disabled BOOLEAN NOT NULL DEFAULT false, token_hash TEXT NOT NULL, token_sealed TEXT NOT NULL, created TIMESTAMPTZ NOT NULL DEFAULT now(), "
    "last_report TIMESTAMPTZ, messages BIGINT NOT NULL DEFAULT 0)",
    "CREATE UNIQUE INDEX IF NOT EXISTS acars_stations_sid ON acars_stations (lower(station_id))",
    "CREATE INDEX IF NOT EXISTS acars_stations_token ON acars_stations (token_hash)",
    "CREATE INDEX IF NOT EXISTS acars_stations_owner ON acars_stations (owner_id)",
    "DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='mesh_hidden' AND column_name='node_id') THEN DROP TABLE mesh_hidden; END IF; END $$",   # the first, unreleased form of the takedown list (Meshtastic only) keyed by number
    "CREATE TABLE IF NOT EXISTS mesh_accounts (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, label TEXT NOT NULL DEFAULT '', "
    "nodes TEXT NOT NULL DEFAULT '', public BOOLEAN NOT NULL DEFAULT false, disabled BOOLEAN NOT NULL DEFAULT false, username TEXT NOT NULL DEFAULT '', password_sealed TEXT NOT NULL DEFAULT '', "
    "created TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE UNIQUE INDEX IF NOT EXISTS mesh_accounts_username ON mesh_accounts (username) WHERE username <> ''",
    "CREATE INDEX IF NOT EXISTS mesh_accounts_owner ON mesh_accounts (owner_id)",
    "CREATE TABLE IF NOT EXISTS api_usage (day DATE NOT NULL, host TEXT NOT NULL, calls BIGINT NOT NULL DEFAULT 0, errors BIGINT NOT NULL DEFAULT 0, bytes BIGINT NOT NULL DEFAULT 0, last_call TIMESTAMPTZ, PRIMARY KEY (day, host))",
    "CREATE TABLE IF NOT EXISTS api_usage_hourly (hour TIMESTAMPTZ NOT NULL, host TEXT NOT NULL, calls BIGINT NOT NULL DEFAULT 0, errors BIGINT NOT NULL DEFAULT 0, bytes BIGINT NOT NULL DEFAULT 0, last_call TIMESTAMPTZ, PRIMARY KEY (hour, host))",
    "CREATE TABLE IF NOT EXISTS feed_usage_hourly (hour TIMESTAMPTZ NOT NULL, feed TEXT NOT NULL, msgs BIGINT NOT NULL DEFAULT 0, PRIMARY KEY (hour, feed))",
    "CREATE TABLE IF NOT EXISTS svc_usage (day DATE NOT NULL, via TEXT NOT NULL, host TEXT NOT NULL, calls BIGINT NOT NULL DEFAULT 0, bytes BIGINT NOT NULL DEFAULT 0, PRIMARY KEY (day, via, host))",
    "CREATE TABLE IF NOT EXISTS usage_state (k TEXT PRIMARY KEY, v1 BIGINT NOT NULL DEFAULT 0, v2 BIGINT NOT NULL DEFAULT 0)",
    "INSERT INTO api_usage_hourly (hour, host, calls, errors, bytes, last_call) SELECT day::timestamp AT TIME ZONE 'UTC', host, calls, errors, bytes, last_call FROM api_usage "
    "WHERE day < COALESCE((SELECT min(hour)::date FROM api_usage_hourly), current_date + 1) ON CONFLICT DO NOTHING",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS passkey_handle TEXT NOT NULL DEFAULT ''",
    "CREATE TABLE IF NOT EXISTS passkeys (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, credential_id TEXT NOT NULL UNIQUE, "
    "public_key BYTEA NOT NULL, sign_count BIGINT NOT NULL DEFAULT 0, name TEXT NOT NULL DEFAULT '', transports TEXT NOT NULL DEFAULT '', aaguid TEXT NOT NULL DEFAULT '', backed_up BOOLEAN NOT NULL DEFAULT FALSE, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_used TIMESTAMPTZ)",
    "CREATE INDEX IF NOT EXISTS passkeys_user ON passkeys (user_id)",
    "CREATE TABLE IF NOT EXISTS sat_tle (norad INTEGER PRIMARY KEY, name TEXT NOT NULL, line1 TEXT NOT NULL, line2 TEXT NOT NULL, epoch TIMESTAMPTZ NOT NULL, fetched TIMESTAMPTZ NOT NULL DEFAULT now(), group_name TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS radio_entries (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, freq_mhz DOUBLE PRECISION, freq_note TEXT NOT NULL DEFAULT '', "
    "mode TEXT NOT NULL DEFAULT '', area TEXT NOT NULL DEFAULT '', lat DOUBLE PRECISION, lon DOUBLE PRECISION, recurrence TEXT NOT NULL DEFAULT 'weekly', weekday INTEGER, nth INTEGER, at_time TEXT NOT NULL DEFAULT '', "
    "tz TEXT NOT NULL DEFAULT '', contact TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'approved', owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), reviewed_by TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS radio_entries_kind ON radio_entries (kind, status)",
    "CREATE TABLE IF NOT EXISTS sat_receivers (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'meteor', "
    "place TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', public BOOLEAN NOT NULL DEFAULT TRUE, enabled BOOLEAN NOT NULL DEFAULT TRUE, token_hash TEXT NOT NULL UNIQUE, token_sealed TEXT NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_image_at TIMESTAMPTZ)",
    "CREATE TABLE IF NOT EXISTS sat_images (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, receiver_id BIGINT NOT NULL REFERENCES sat_receivers(id) ON DELETE CASCADE, satellite TEXT NOT NULL DEFAULT '', "
    "product TEXT NOT NULL DEFAULT '', captured_at TIMESTAMPTZ NOT NULL DEFAULT now(), received_at TIMESTAMPTZ NOT NULL DEFAULT now(), content_type TEXT NOT NULL, size INTEGER NOT NULL, data BYTEA NOT NULL, "
    "note TEXT NOT NULL DEFAULT '', hidden BOOLEAN NOT NULL DEFAULT FALSE)",
    "CREATE INDEX IF NOT EXISTS sat_images_rx ON sat_images (receiver_id, captured_at DESC)",
    "CREATE INDEX IF NOT EXISTS sat_images_time ON sat_images (captured_at DESC)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS pm_open BOOLEAN NOT NULL DEFAULT TRUE",
    "CREATE TABLE IF NOT EXISTS chat_rooms (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', readonly BOOLEAN NOT NULL DEFAULT FALSE, sort_order INTEGER NOT NULL DEFAULT 0, "
    "discord_webhook TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS chat_messages (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, room_id BIGINT NOT NULL REFERENCES chat_rooms(id) ON DELETE CASCADE, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, body TEXT NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), deleted_at TIMESTAMPTZ, deleted_by TEXT)",
    "CREATE INDEX IF NOT EXISTS chat_messages_room ON chat_messages (room_id, id DESC)",
    "CREATE TABLE IF NOT EXISTS chat_mutes (user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, scope TEXT NOT NULL, until TIMESTAMPTZ, reason TEXT NOT NULL DEFAULT '', by_name TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (user_id, scope))",
    "CREATE TABLE IF NOT EXISTS chat_reports (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, message_id BIGINT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE, reporter_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, note TEXT NOT NULL DEFAULT '', "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), handled_by TEXT, handled_at TIMESTAMPTZ)",
    "CREATE TABLE IF NOT EXISTS pm_threads (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_a BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, user_b BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (user_a, user_b))",
    "CREATE TABLE IF NOT EXISTS pm_messages (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, thread_id BIGINT NOT NULL REFERENCES pm_threads(id) ON DELETE CASCADE, sender_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, body TEXT NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), read_at TIMESTAMPTZ, delivered BOOLEAN NOT NULL DEFAULT TRUE, hidden_a BOOLEAN NOT NULL DEFAULT FALSE, hidden_b BOOLEAN NOT NULL DEFAULT FALSE)",
    "CREATE INDEX IF NOT EXISTS pm_messages_thread ON pm_messages (thread_id, id)",
    "CREATE TABLE IF NOT EXISTS pm_blocks (blocker_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, blocked_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (blocker_id, blocked_id))",
    "CREATE TABLE IF NOT EXISTS pm_reports (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, message_id BIGINT NOT NULL REFERENCES pm_messages(id) ON DELETE CASCADE, reporter_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, note TEXT NOT NULL DEFAULT '', "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), handled_by TEXT, handled_at TIMESTAMPTZ)",
    "CREATE TABLE IF NOT EXISTS convs (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'direct', name TEXT NOT NULL DEFAULT '', created_by BIGINT REFERENCES users(id) ON DELETE SET NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_at TIMESTAMPTZ NOT NULL DEFAULT now(), pair_key TEXT UNIQUE)",
    "CREATE TABLE IF NOT EXISTS conv_members (conv_id BIGINT NOT NULL REFERENCES convs(id) ON DELETE CASCADE, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, joined_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
    "last_read_id BIGINT NOT NULL DEFAULT 0, typing_at TIMESTAMPTZ, hidden BOOLEAN NOT NULL DEFAULT FALSE, PRIMARY KEY (conv_id, user_id))",
    "CREATE INDEX IF NOT EXISTS conv_members_user ON conv_members (user_id)",
    "CREATE TABLE IF NOT EXISTS conv_messages (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, conv_id BIGINT NOT NULL REFERENCES convs(id) ON DELETE CASCADE, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "body TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), deleted_at TIMESTAMPTZ, deleted_by TEXT)",
    "CREATE INDEX IF NOT EXISTS conv_messages_conv ON conv_messages (conv_id, id DESC)",
    "CREATE TABLE IF NOT EXISTS conv_reports (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, message_id BIGINT NOT NULL REFERENCES conv_messages(id) ON DELETE CASCADE, reporter_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "note TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), handled_by TEXT, handled_at TIMESTAMPTZ)",
    "CREATE TABLE IF NOT EXISTS friendships (user_a BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, user_b BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, status TEXT NOT NULL DEFAULT 'pending', "
    "requested_by BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), responded_at TIMESTAMPTZ, PRIMARY KEY (user_a, user_b), CHECK (user_a < user_b))",
    "CREATE TABLE IF NOT EXISTS chat_presence (user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, seen_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS contact_policy TEXT NOT NULL DEFAULT 'anyone'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS show_online BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS find_by_email BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS find_by_callsign BOOLEAN NOT NULL DEFAULT TRUE",
    "CREATE TABLE IF NOT EXISTS mail_messages (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, thread_id BIGINT NOT NULL DEFAULT 0, parent_id BIGINT, sender_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', draft_to TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), sent_at TIMESTAMPTZ, sender_folder TEXT NOT NULL DEFAULT 'drafts', trashed_at TIMESTAMPTZ)",
    "CREATE INDEX IF NOT EXISTS mail_messages_sender ON mail_messages (sender_id, sender_folder)",
    "CREATE INDEX IF NOT EXISTS mail_messages_thread ON mail_messages (thread_id)",
    "CREATE TABLE IF NOT EXISTS mail_recipients (message_id BIGINT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, folder TEXT NOT NULL DEFAULT 'inbox', "
    "read_at TIMESTAMPTZ, delivered BOOLEAN NOT NULL DEFAULT TRUE, trashed_at TIMESTAMPTZ, PRIMARY KEY (message_id, user_id))",
    "CREATE INDEX IF NOT EXISTS mail_recipients_user ON mail_recipients (user_id, folder)",
    "CREATE TABLE IF NOT EXISTS mail_reports (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, message_id BIGINT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE, reporter_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "note TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), handled_by TEXT, handled_at TIMESTAMPTZ)",
    "ALTER TABLE conv_messages ADD COLUMN IF NOT EXISTS remote_from TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE conv_messages ADD COLUMN IF NOT EXISTS remote_id TEXT NOT NULL DEFAULT ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS conv_messages_remote ON conv_messages (conv_id, remote_id) WHERE remote_id <> ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS bridge_aprs_ok BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ham_status TEXT NOT NULL DEFAULT 'unverified'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ham_call TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ham_name TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ham_checked TIMESTAMPTZ",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ham_expires DATE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ham_reason TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ham_licensee TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ham_override TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ham_by TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ham_warn TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS bridge_mesh_ok BOOLEAN NOT NULL DEFAULT FALSE",
    "CREATE TABLE IF NOT EXISTS bridge_links (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, conv_id BIGINT NOT NULL REFERENCES convs(id) ON DELETE CASCADE, kind TEXT NOT NULL, remote TEXT NOT NULL, "
    "created_by BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, enabled BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_in TIMESTAMPTZ, last_out TIMESTAMPTZ, "
    "last_result TEXT NOT NULL DEFAULT '', UNIQUE (conv_id, kind, remote))",
    "CREATE TABLE IF NOT EXISTS bridge_log (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, link_id BIGINT NOT NULL REFERENCES bridge_links(id) ON DELETE CASCADE, direction TEXT NOT NULL, ok BOOLEAN NOT NULL, "
    "detail TEXT NOT NULL DEFAULT '', at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE INDEX IF NOT EXISTS bridge_log_link ON bridge_log (link_id, at DESC)",
    "CREATE TABLE IF NOT EXISTS mesh_outbox (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, link_id BIGINT NOT NULL REFERENCES bridge_links(id) ON DELETE CASCADE, node_id TEXT NOT NULL, body TEXT NOT NULL, "
    "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), sent_at TIMESTAMPTZ)",
    "CREATE TABLE IF NOT EXISTS rid_stations (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL, name TEXT NOT NULL, place TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', "
    "public BOOLEAN NOT NULL DEFAULT TRUE, enabled BOOLEAN NOT NULL DEFAULT TRUE, token_hash TEXT NOT NULL UNIQUE, token_sealed TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_report TIMESTAMPTZ, msgs BIGINT NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS rid_drones (drone_id TEXT PRIMARY KEY, id_type INTEGER, ua_type INTEGER, status INTEGER, lat DOUBLE PRECISION, lon DOUBLE PRECISION, alt_geo DOUBLE PRECISION, alt_baro DOUBLE PRECISION, height DOUBLE PRECISION, "
    "speed DOUBLE PRECISION, heading DOUBLE PRECISION, vspeed DOUBLE PRECISION, op_id TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', op_lat DOUBLE PRECISION, op_lon DOUBLE PRECISION, "
    "first_seen TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen TIMESTAMPTZ NOT NULL DEFAULT now(), msgs BIGINT NOT NULL DEFAULT 0, station_id BIGINT REFERENCES rid_stations(id) ON DELETE SET NULL)",
    "CREATE INDEX IF NOT EXISTS rid_drones_seen ON rid_drones (last_seen DESC)",
    "CREATE TABLE IF NOT EXISTS rid_positions (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, drone_id TEXT NOT NULL, ts TIMESTAMPTZ NOT NULL DEFAULT now(), lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL, alt DOUBLE PRECISION, speed DOUBLE PRECISION)",
    "CREATE INDEX IF NOT EXISTS rid_positions_drone ON rid_positions (drone_id, ts DESC)",
    "CREATE TABLE IF NOT EXISTS rid_hidden (drone_id TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', hidden_by TEXT NOT NULL DEFAULT '', hidden_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS ais_stations (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL, name TEXT NOT NULL, place TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', "
    "public BOOLEAN NOT NULL DEFAULT TRUE, enabled BOOLEAN NOT NULL DEFAULT TRUE, token_hash TEXT NOT NULL UNIQUE, token_sealed TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_report TIMESTAMPTZ, msgs BIGINT NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS ais_vessels (mmsi BIGINT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', callsign TEXT NOT NULL DEFAULT '', imo BIGINT, ship_type INTEGER, status INTEGER, lat DOUBLE PRECISION, lon DOUBLE PRECISION, "
    "speed DOUBLE PRECISION, course DOUBLE PRECISION, heading INTEGER, to_bow INTEGER, to_stern INTEGER, to_port INTEGER, to_starboard INTEGER, draught DOUBLE PRECISION, destination TEXT NOT NULL DEFAULT '', "
    "first_seen TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen TIMESTAMPTZ NOT NULL DEFAULT now(), msgs BIGINT NOT NULL DEFAULT 0, station_id BIGINT REFERENCES ais_stations(id) ON DELETE SET NULL)",
    "CREATE INDEX IF NOT EXISTS ais_vessels_seen ON ais_vessels (last_seen DESC)",
    "CREATE TABLE IF NOT EXISTS ais_positions (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, mmsi BIGINT NOT NULL, ts TIMESTAMPTZ NOT NULL DEFAULT now(), lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL, speed DOUBLE PRECISION, course DOUBLE PRECISION)",
    "CREATE INDEX IF NOT EXISTS ais_positions_mmsi ON ais_positions (mmsi, ts DESC)",
    "CREATE TABLE IF NOT EXISTS ais_hidden (mmsi BIGINT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', hidden_by TEXT NOT NULL DEFAULT '', hidden_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS google_links (sub TEXT PRIMARY KEY, user_id BIGINT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE, email TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', "
    "linked_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_login TIMESTAMPTZ, created_by_signup BOOLEAN NOT NULL DEFAULT FALSE)",
    "CREATE TABLE IF NOT EXISTS oidc_codes (code_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, redirect_uri TEXT NOT NULL, nonce TEXT NOT NULL DEFAULT '', challenge TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '', expires TIMESTAMPTZ NOT NULL)",
    "CREATE TABLE IF NOT EXISTS oidc_tokens (token_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, scope TEXT NOT NULL DEFAULT '', expires TIMESTAMPTZ NOT NULL)",
    "CREATE TABLE IF NOT EXISTS matrix_locks (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, reason TEXT NOT NULL DEFAULT '', "
    "by_name TEXT NOT NULL DEFAULT '', created TIMESTAMPTZ NOT NULL DEFAULT now(), until TIMESTAMPTZ, lifted BOOLEAN NOT NULL DEFAULT FALSE, lifted_by TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS matrix_report_state (report_id BIGINT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'dismissed', by_name TEXT NOT NULL DEFAULT '', at TIMESTAMPTZ NOT NULL DEFAULT now(), note TEXT NOT NULL DEFAULT '')",
    "ALTER TABLE bridge_links ALTER COLUMN conv_id DROP NOT NULL",
    "ALTER TABLE bridge_links ADD COLUMN IF NOT EXISTS room_id TEXT",
    "ALTER TABLE bridge_links ADD COLUMN IF NOT EXISTS room_name TEXT NOT NULL DEFAULT ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS bridge_links_room ON bridge_links (room_id, kind, remote) WHERE room_id IS NOT NULL",
    "CREATE TABLE IF NOT EXISTS bridge_seen (link_id BIGINT NOT NULL REFERENCES bridge_links(id) ON DELETE CASCADE, remote_id TEXT NOT NULL, at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (link_id, remote_id))",
    "CREATE TABLE IF NOT EXISTS matrix_seen_events (event_id TEXT PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS matrix_mirrors (alias TEXT PRIMARY KEY, room_id TEXT NOT NULL, webhook TEXT NOT NULL, by_name TEXT NOT NULL DEFAULT '', created TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS push_subscriptions (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, endpoint TEXT NOT NULL UNIQUE, "
    "p256dh_sealed TEXT NOT NULL, auth_sealed TEXT NOT NULL, pushkey_hash TEXT NOT NULL, device TEXT NOT NULL DEFAULT '', created TIMESTAMPTZ NOT NULL DEFAULT now(), last_ok TIMESTAMPTZ, failures INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS push_subscriptions_user ON push_subscriptions (user_id)",
    "CREATE INDEX IF NOT EXISTS push_subscriptions_key ON push_subscriptions (pushkey_hash)",
    "CREATE TABLE IF NOT EXISTS goes_images (sector TEXT NOT NULL, product TEXT NOT NULL, data BYTEA NOT NULL, content_type TEXT NOT NULL DEFAULT 'image/jpeg', source_time TIMESTAMPTZ, fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (sector, product))",
    "CREATE TABLE IF NOT EXISTS goes_frames (sector TEXT NOT NULL, product TEXT NOT NULL, frame_time TIMESTAMPTZ NOT NULL, data BYTEA NOT NULL, PRIMARY KEY (sector, product, frame_time))",
    "CREATE TABLE IF NOT EXISTS api_keys (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', "
    "prefix TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), created_by TEXT NOT NULL DEFAULT '', last_used TIMESTAMPTZ, revoked BOOLEAN NOT NULL DEFAULT FALSE, "
    "revoked_at TIMESTAMPTZ, revoked_by TEXT NOT NULL DEFAULT '', rate_minute INTEGER, rate_day INTEGER)",
    "CREATE INDEX IF NOT EXISTS api_keys_user ON api_keys (user_id)",
    "CREATE TABLE IF NOT EXISTS api_key_minute (key_id BIGINT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE, bucket TIMESTAMPTZ NOT NULL, calls INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (key_id, bucket))",
    "CREATE TABLE IF NOT EXISTS api_key_daily (key_id BIGINT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE, day DATE NOT NULL, calls INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (key_id, day))",
    "CREATE TABLE IF NOT EXISTS mesh_claims (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, node_key TEXT NOT NULL, code TEXT NOT NULL, "
    "status TEXT NOT NULL DEFAULT 'pending', public BOOLEAN NOT NULL DEFAULT true, owner_hidden BOOLEAN NOT NULL DEFAULT false, created TIMESTAMPTZ NOT NULL DEFAULT now(), claimed_at TIMESTAMPTZ)",
    "CREATE UNIQUE INDEX IF NOT EXISTS mesh_claims_node ON mesh_claims (node_key) WHERE status = 'claimed'",
    "CREATE INDEX IF NOT EXISTS mesh_claims_owner ON mesh_claims (owner_id)",
    "CREATE TABLE IF NOT EXISTS mesh_alert_prefs (user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, quiet BOOLEAN NOT NULL DEFAULT true, battery BOOLEAN NOT NULL DEFAULT true, recovered BOOLEAN NOT NULL DEFAULT true, by_email BOOLEAN NOT NULL DEFAULT true, by_webhook BOOLEAN NOT NULL DEFAULT false)",
    "CREATE TABLE IF NOT EXISTS mesh_alert_state (user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, node_key TEXT NOT NULL, kind TEXT NOT NULL, since TIMESTAMPTZ NOT NULL DEFAULT now(), last_sent TIMESTAMPTZ NOT NULL DEFAULT now(), repeats INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, node_key, kind))",
    "CREATE TABLE IF NOT EXISTS mesh_alert_events (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, node_key TEXT NOT NULL, node_name TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', ts TIMESTAMPTZ NOT NULL DEFAULT now(), email_state TEXT NOT NULL DEFAULT 'none', webhook_state TEXT NOT NULL DEFAULT 'none', seen BOOLEAN NOT NULL DEFAULT false)",
    "CREATE INDEX IF NOT EXISTS mesh_alert_events_user ON mesh_alert_events (user_id, ts DESC)",
    "CREATE TABLE IF NOT EXISTS mesh_alert_sends (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, way TEXT NOT NULL, events INTEGER NOT NULL DEFAULT 0, ok BOOLEAN NOT NULL DEFAULT false, ts TIMESTAMPTZ NOT NULL DEFAULT now())",
    "ALTER TABLE mesh_accounts ADD COLUMN IF NOT EXISTS community_id BIGINT REFERENCES communities(id) ON DELETE SET NULL",
    "ALTER TABLE mesh_accounts ADD COLUMN IF NOT EXISTS link TEXT NOT NULL DEFAULT 'up'",
    "ALTER TABLE mesh_alert_prefs ADD COLUMN IF NOT EXISTS by_push BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE mesh_alert_events ADD COLUMN IF NOT EXISTS push_state TEXT NOT NULL DEFAULT 'none'",
    "CREATE TABLE IF NOT EXISTS mesh_channels (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, psk_sealed TEXT NOT NULL, uplink BOOLEAN NOT NULL DEFAULT false, downlink BOOLEAN NOT NULL DEFAULT false, precision INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', created TIMESTAMPTZ NOT NULL DEFAULT now(), rotated TIMESTAMPTZ)",
    "CREATE UNIQUE INDEX IF NOT EXISTS mesh_channels_owner_name ON mesh_channels (owner_id, lower(name))",
    "CREATE TABLE IF NOT EXISTS mesh_channel_members (channel_id BIGINT NOT NULL REFERENCES mesh_channels(id) ON DELETE CASCADE, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, added_by BIGINT REFERENCES users(id) ON DELETE SET NULL, added TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (channel_id, user_id))",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS legal_first_sealed TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS legal_last_sealed TEXT NOT NULL DEFAULT ''",
    "CREATE TABLE IF NOT EXISTS mesh_hidden (node_key TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', added TIMESTAMPTZ NOT NULL DEFAULT now(), added_by BIGINT REFERENCES users(id) ON DELETE SET NULL)",
    "CREATE TABLE IF NOT EXISTS acars_hidden (tail TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', added TIMESTAMPTZ NOT NULL DEFAULT now(), added_by BIGINT REFERENCES users(id) ON DELETE SET NULL)",
    "CREATE TABLE IF NOT EXISTS wx_stations (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, station_id TEXT, owner_id BIGINT REFERENCES users(id) ON DELETE CASCADE, "
    "feeder_uuid TEXT REFERENCES feeders(uuid) ON DELETE SET NULL, name TEXT NOT NULL DEFAULT '', lat DOUBLE PRECISION, lon DOUBLE PRECISION, privacy TEXT NOT NULL DEFAULT 'general', "
    "kind TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, token_sealed TEXT NOT NULL DEFAULT '', secrets TEXT NOT NULL DEFAULT '', public BOOLEAN NOT NULL DEFAULT FALSE, "
    "disabled BOOLEAN NOT NULL DEFAULT FALSE, hardware TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', latest TEXT NOT NULL DEFAULT '', last_report TIMESTAMPTZ, "
    "last_stored TIMESTAMPTZ, last_poll TIMESTAMPTZ, last_error TEXT NOT NULL DEFAULT '', fail_count INTEGER NOT NULL DEFAULT 0, created TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE UNIQUE INDEX IF NOT EXISTS wx_stations_sid_lower ON wx_stations (lower(station_id)) WHERE station_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS wx_stations_feeder ON wx_stations (feeder_uuid) WHERE feeder_uuid IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS wx_stations_owner ON wx_stations (owner_id)",
    "CREATE TABLE IF NOT EXISTS wx_readings (station_pk BIGINT NOT NULL REFERENCES wx_stations(id) ON DELETE CASCADE, ts TIMESTAMPTZ NOT NULL, temp_f REAL, dew_f REAL, hum REAL, press_in REAL, "
    "wind_mph REAL, gust_mph REAL, wind_dir REAL, rain_rate_in REAL, rain_day_in REAL, solar REAL, uv REAL, lightning_n REAL, lightning_mi REAL, PRIMARY KEY (station_pk, ts))",
    "CREATE TABLE IF NOT EXISTS wx_push (station_pk BIGINT PRIMARY KEY REFERENCES wx_stations(id) ON DELETE CASCADE, enabled BOOLEAN NOT NULL DEFAULT FALSE, mode TEXT NOT NULL DEFAULT 'cwop', callsign TEXT NOT NULL DEFAULT '', "
    "passcode_sealed TEXT NOT NULL DEFAULT '', position TEXT NOT NULL DEFAULT 'public', last_push TIMESTAMPTZ, last_reading TIMESTAMPTZ, last_ok BOOLEAN NOT NULL DEFAULT FALSE, last_result TEXT NOT NULL DEFAULT '', "
    "fail_count INTEGER NOT NULL DEFAULT 0, sent BIGINT NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS wx_hourly (station_pk BIGINT NOT NULL REFERENCES wx_stations(id) ON DELETE CASCADE, hour TIMESTAMPTZ NOT NULL, n INTEGER NOT NULL DEFAULT 0, temp_avg REAL, "
    "temp_min REAL, temp_max REAL, hum_avg REAL, press_avg REAL, wind_avg REAL, gust_max REAL, rain_day_max REAL, solar_max REAL, uv_max REAL, dew_avg REAL, PRIMARY KEY (station_pk, hour))",
    "CREATE TABLE IF NOT EXISTS sondes (serial TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'radiosonde', type TEXT NOT NULL DEFAULT '', subtype TEXT NOT NULL DEFAULT '', manufacturer TEXT NOT NULL DEFAULT '', "
    "frequency DOUBLE PRECISION, first_seen TIMESTAMPTZ NOT NULL, last_seen TIMESTAMPTZ NOT NULL, last_lat DOUBLE PRECISION, last_lon DOUBLE PRECISION, last_alt DOUBLE PRECISION, last_vel_v DOUBLE PRECISION, "
    "last_temp DOUBLE PRECISION, max_alt DOUBLE PRECISION, max_alt_ts TIMESTAMPTZ, frames INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'aloft', launch_checked BOOLEAN NOT NULL DEFAULT FALSE, "
    "launch_lat DOUBLE PRECISION, launch_lon DOUBLE PRECISION, launch_alt DOUBLE PRECISION, launch_time TIMESTAMPTZ, site_id TEXT NOT NULL DEFAULT '', site_name TEXT NOT NULL DEFAULT '', agency TEXT NOT NULL DEFAULT '', "
    "landing_lat DOUBLE PRECISION, landing_lon DOUBLE PRECISION, landing_alt DOUBLE PRECISION, pred_lat DOUBLE PRECISION, pred_lon DOUBLE PRECISION, pred_time TIMESTAMPTZ, pred_burst DOUBLE PRECISION, pred_at TIMESTAMPTZ)",
    "CREATE INDEX IF NOT EXISTS sondes_last ON sondes (last_seen DESC)",
    "CREATE INDEX IF NOT EXISTS sondes_status ON sondes (status)",
    "CREATE INDEX IF NOT EXISTS sondes_site ON sondes (site_id)",
    "CREATE TABLE IF NOT EXISTS sonde_points (serial TEXT NOT NULL REFERENCES sondes(serial) ON DELETE CASCADE, ts TIMESTAMPTZ NOT NULL, lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL, alt DOUBLE PRECISION, "
    "vel_v REAL, vel_h REAL, heading REAL, temp REAL, hum REAL, press REAL, sats SMALLINT, batt REAL, freq DOUBLE PRECISION, snr REAL, rssi REAL, uploader TEXT NOT NULL DEFAULT '', PRIMARY KEY (serial, ts))",
    "CREATE INDEX IF NOT EXISTS sonde_points_ts ON sonde_points (ts)",
    "CREATE TABLE IF NOT EXISTS sonde_heard (serial TEXT NOT NULL REFERENCES sondes(serial) ON DELETE CASCADE, callsign TEXT NOT NULL, packets INTEGER NOT NULL DEFAULT 0, first_ts TIMESTAMPTZ, last_ts TIMESTAMPTZ, "
    "best_snr REAL, PRIMARY KEY (serial, callsign))",
    "CREATE INDEX IF NOT EXISTS sonde_heard_call ON sonde_heard (lower(callsign))",
    "CREATE TABLE IF NOT EXISTS sonde_stations (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, callsign TEXT NOT NULL, lat DOUBLE PRECISION, lon DOUBLE PRECISION, alt DOUBLE PRECISION, antenna TEXT NOT NULL DEFAULT '', "
    "radio TEXT NOT NULL DEFAULT '', software TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '', mobile BOOLEAN NOT NULL DEFAULT FALSE, privacy TEXT NOT NULL DEFAULT 'general', public BOOLEAN NOT NULL DEFAULT TRUE, "
    "disabled BOOLEAN NOT NULL DEFAULT FALSE, source TEXT NOT NULL DEFAULT 'sondehub', owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL, claim_status TEXT NOT NULL DEFAULT 'unclaimed', claim_code TEXT NOT NULL DEFAULT '', "
    "claimed_at TIMESTAMPTZ, display_name TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', first_seen TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen TIMESTAMPTZ)",
    "CREATE UNIQUE INDEX IF NOT EXISTS sonde_stations_call ON sonde_stations (lower(callsign))",
    "CREATE INDEX IF NOT EXISTS sonde_stations_owner ON sonde_stations (owner_id)",
    "CREATE TABLE IF NOT EXISTS sonde_sites (site_id TEXT PRIMARY KEY, name TEXT NOT NULL, lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL, alt DOUBLE PRECISION, rs_types TEXT NOT NULL DEFAULT '', "
    "times TEXT NOT NULL DEFAULT '', nws BOOLEAN NOT NULL DEFAULT FALSE)",
    "ALTER TABLE sondes ADD COLUMN IF NOT EXISTS max_lat DOUBLE PRECISION",
    "ALTER TABLE sondes ADD COLUMN IF NOT EXISTS max_lon DOUBLE PRECISION",
    "ALTER TABLE sonde_points ADD COLUMN IF NOT EXISTS frame INTEGER",
    "ALTER TABLE sonde_stations ADD COLUMN IF NOT EXISTS push_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE sonde_stations ADD COLUMN IF NOT EXISTS push_sealed TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE sonde_stations ADD COLUMN IF NOT EXISTS push_last TIMESTAMPTZ",
    "ALTER TABLE sonde_stations ADD COLUMN IF NOT EXISTS push_frames BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE sonde_stations ADD COLUMN IF NOT EXISTS reach_km REAL",
    "ALTER TABLE sonde_stations ADD COLUMN IF NOT EXISTS reach_sectors TEXT",   # JSON: the farthest balloon heard in each of 36 ten degree sectors (kilometers), drawn like the ADS-B coverage
    "ALTER TABLE sonde_heard ADD COLUMN IF NOT EXISTS last_snr REAL",
    "ALTER TABLE sonde_heard ADD COLUMN IF NOT EXISTS last_freq DOUBLE PRECISION",
    "CREATE TABLE IF NOT EXISTS sonde_recoveries (serial TEXT NOT NULL, ts TIMESTAMPTZ NOT NULL, recovered BOOLEAN NOT NULL DEFAULT FALSE, planned BOOLEAN NOT NULL DEFAULT FALSE, recovered_by TEXT NOT NULL DEFAULT '', "
    "description TEXT NOT NULL DEFAULT '', lat DOUBLE PRECISION, lon DOUBLE PRECISION, alt DOUBLE PRECISION, software TEXT NOT NULL DEFAULT '', PRIMARY KEY (serial, ts))",
    "ALTER TABLE sondes ADD COLUMN IF NOT EXISTS state TEXT",
    "ALTER TABLE sondes ADD COLUMN IF NOT EXISTS pred_path TEXT",
    "UPDATE sondes SET site_id = '', site_name = '', agency = '' WHERE kind = 'amateur' AND (site_id <> '' OR site_name <> '' OR agency <> '')",   # an amateur balloon that began near a weather-service site was wrongly given the site and NWS
    "ALTER TABLE sondes ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT ''",   # 'archive' = loaded from SondeHub's archive, never thinned   # the whole predicted path as JSON [[time, lat, lon, alt], ...]
    "ALTER TABLE sonde_stations ADD COLUMN IF NOT EXISTS state TEXT",
    "CREATE INDEX IF NOT EXISTS sondes_state ON sondes (state)",
    "ALTER TABLE sonde_recoveries ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'sondehub'",
    "ALTER TABLE sonde_recoveries ADD COLUMN IF NOT EXISTS user_id BIGINT",
    "ALTER TABLE sonde_recoveries ADD COLUMN IF NOT EXISTS sent TEXT NOT NULL DEFAULT ''",
    "CREATE TABLE IF NOT EXISTS sonde_chase_share (user_id BIGINT PRIMARY KEY, started TIMESTAMPTZ NOT NULL, last_ts TIMESTAMPTZ NOT NULL, sent INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS counties (geoid TEXT PRIMARY KEY, name TEXT NOT NULL, base TEXT NOT NULL, state_fips TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, lat DOUBLE PRECISION, lon DOUBLE PRECISION, "
    "area_sqmi DOUBLE PRECISION, geometry TEXT NOT NULL)",
    # the repeater directory: one record per repeater, node or gateway; `source` says where it came from (hearham, local, member) and a verified or hand-edited record is never overwritten by an import
    "CREATE TABLE IF NOT EXISTS repeaters (id BIGSERIAL PRIMARY KEY, callsign TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT '', lat DOUBLE PRECISION, lon DOUBLE PRECISION, "
    "freq_hz BIGINT NOT NULL, offset_hz BIGINT NOT NULL DEFAULT 0, tone TEXT NOT NULL DEFAULT '', modes TEXT NOT NULL DEFAULT 'FM', color_code TEXT NOT NULL DEFAULT '', network TEXT NOT NULL DEFAULT '', "
    "kind TEXT NOT NULL DEFAULT 'repeater', description TEXT NOT NULL DEFAULT '', power TEXT NOT NULL DEFAULT '', restriction TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'unverified', "
    "source TEXT NOT NULL DEFAULT 'local', source_id TEXT NOT NULL DEFAULT '', approved BOOLEAN NOT NULL DEFAULT TRUE, edited BOOLEAN NOT NULL DEFAULT FALSE, verified_at TIMESTAMPTZ, verified_by TEXT NOT NULL DEFAULT '', "
    "owner_id BIGINT, claim_status TEXT NOT NULL DEFAULT '', submitted_by BIGINT, created TIMESTAMPTZ NOT NULL DEFAULT now(), updated TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE UNIQUE INDEX IF NOT EXISTS repeaters_source ON repeaters (source, source_id) WHERE source_id <> ''",
    "CREATE INDEX IF NOT EXISTS repeaters_freq ON repeaters (freq_hz)",
    "CREATE TABLE IF NOT EXISTS repeater_groups (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', created_by BIGINT, created TIMESTAMPTZ NOT NULL DEFAULT now())",
    "ALTER TABLE repeater_groups DROP CONSTRAINT IF EXISTS repeater_groups_created_by_fkey",   # who made it is only a record: no link to users, so replacing accounts never reaches the repeaters
    "CREATE UNIQUE INDEX IF NOT EXISTS repeater_groups_name ON repeater_groups (lower(name))",
    "CREATE TABLE IF NOT EXISTS repeater_group_members (group_id BIGINT NOT NULL REFERENCES repeater_groups(id) ON DELETE CASCADE, repeater_id BIGINT NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE, added_by BIGINT, added TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (group_id, repeater_id))",
    "ALTER TABLE repeater_group_members DROP CONSTRAINT IF EXISTS repeater_group_members_added_by_fkey",
    "CREATE INDEX IF NOT EXISTS repeater_group_members_rep ON repeater_group_members (repeater_id)",
    "CREATE TABLE IF NOT EXISTS repeater_reports (id BIGSERIAL PRIMARY KEY, repeater_id BIGINT NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE, user_id BIGINT, kind TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', "
    "suggested TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open', created TIMESTAMPTZ NOT NULL DEFAULT now(), handled_by TEXT NOT NULL DEFAULT '', handled_at TIMESTAMPTZ)",
    "CREATE INDEX IF NOT EXISTS repeater_reports_open ON repeater_reports (status, created)",
    "CREATE TABLE IF NOT EXISTS aprs_hidden (call TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', added TIMESTAMPTZ NOT NULL DEFAULT now(), added_by BIGINT)",   # call signs taken off the APRS pages on request
    "CREATE TABLE IF NOT EXISTS hospital_rings (facility_id TEXT NOT NULL REFERENCES hospitals(facility_id) ON DELETE CASCADE, minutes INT NOT NULL, poly TEXT NOT NULL, computed TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (facility_id, minutes))",
    "CREATE TABLE IF NOT EXISTS roads (route TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', geometry TEXT NOT NULL, PRIMARY KEY (kind, route, state))",
    "CREATE TABLE IF NOT EXISTS sonde_history_months (site TEXT NOT NULL, ym TEXT NOT NULL, done TIMESTAMPTZ NOT NULL DEFAULT now(), flights INT NOT NULL DEFAULT 0, PRIMARY KEY (site, ym))",
    "CREATE TABLE IF NOT EXISTS sonde_history_skipped (serial TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', ts TIMESTAMPTZ NOT NULL DEFAULT now())",
    "ALTER TABLE repeaters ADD COLUMN IF NOT EXISTS dmr_id BIGINT",   # the repeater's own DMR ID
    "ALTER TABLE repeaters ADD COLUMN IF NOT EXISTS attrs TEXT NOT NULL DEFAULT '{}'",   # the settings of the digital modes (app/repeater_digital.py ATTR_KEYS)
    "CREATE TABLE IF NOT EXISTS repeater_talkgroups (id BIGSERIAL PRIMARY KEY, repeater_id BIGINT NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE, slot SMALLINT NOT NULL DEFAULT 1, tg_id BIGINT, name TEXT NOT NULL DEFAULT '', call_type TEXT NOT NULL DEFAULT 'group', mode TEXT NOT NULL DEFAULT 'static', network TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS repeater_talkgroups_rep ON repeater_talkgroups (repeater_id)",
    "CREATE TABLE IF NOT EXISTS wx_obs (id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(), src TEXT NOT NULL, sid TEXT NOT NULL, temp_f REAL, dew_f REAL, hum REAL, press_in REAL, wind_mph REAL, gust_mph REAL, wind_dir REAL, rain_day_in REAL, uv REAL, cloud_pct REAL)",
    "CREATE INDEX IF NOT EXISTS wx_obs_ts ON wx_obs (ts)",
    "CREATE INDEX IF NOT EXISTS wx_obs_sid ON wx_obs (src, sid, ts)",
    "CREATE TABLE IF NOT EXISTS wx_station_dir (src TEXT NOT NULL, sid TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', lat DOUBLE PRECISION, lon DOUBLE PRECISION, state TEXT NOT NULL DEFAULT '', county TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '', zip TEXT NOT NULL DEFAULT '', PRIMARY KEY (src, sid))",
    "CREATE TABLE IF NOT EXISTS frequencies (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, service TEXT NOT NULL, name TEXT NOT NULL, freq_hz BIGINT NOT NULL, mode TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', builtin BOOLEAN NOT NULL DEFAULT FALSE, created TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS dmr_talkgroups (id BIGINT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'Group Call', source TEXT NOT NULL DEFAULT 'file', updated TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE INDEX IF NOT EXISTS dmr_talkgroups_name ON dmr_talkgroups (lower(name))",
    "CREATE TABLE IF NOT EXISTS dmr_contacts (id BIGINT PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'Private Call', source TEXT NOT NULL DEFAULT 'file', updated TIMESTAMPTZ NOT NULL DEFAULT now())",
    "ALTER TABLE dmr_contacts ADD COLUMN IF NOT EXISTS call TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE dmr_contacts ADD COLUMN IF NOT EXISTS remark TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS dmr_contacts_call ON dmr_contacts (upper(call))",
    "CREATE TABLE IF NOT EXISTS callsign_cache (call TEXT PRIMARY KEY, data TEXT NOT NULL, fetched TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS repeater_edits (id BIGSERIAL PRIMARY KEY, repeater_id BIGINT NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE, actor TEXT NOT NULL DEFAULT '', action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', ts TIMESTAMPTZ NOT NULL DEFAULT now())",
]


def get_db():
    if "db" not in g:
        g.db = psycopg.connect(current_app.config["DATABASE_URL"], row_factory=dict_row)
    return g.db


def close_db(_exc=None):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def init_db():
    d = get_db()
    d.execute(SCHEMA)
    for stmt in MIGRATIONS:
        d.execute(stmt)
    from . import frequencies
    frequencies.seed()   # the known frequencies (APRS, calling, FRS, GMRS, MURS, weather radio...), never overwriting an edit
    # Rebrand to ArkSignal (2026-10), once: settings that still hold the old built-in wording move to the new default, with an audit entry.
    if not d.execute("SELECT 1 FROM settings WHERE key='rebrand_arksignal_done'").fetchone():
        for key, old in (("site_name", "Arkansas ADS-B Network"), ("site_tagline", "Statewide 1090 MHz receiver network"),
                         ("feed_host", "feed.arksignal.net"), ("lookup_user_agent", "ArkSignalPortal/1.0 (+https://arksignal.net)")):
            if d.execute("DELETE FROM settings WHERE key=%s AND value=%s RETURNING key", (key, old)).fetchone():
                d.execute("INSERT INTO audit_log (actor_name, action, target, detail) VALUES ('system', 'settings.rebrand', %s, %s)", (key, f"{old} -> default (ArkSignal)"))
        for key in ("about_intro", "support_intro"):
            row = d.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
            if row and row["value"].startswith("The Arkansas ADS-B Network is "):
                d.execute("DELETE FROM settings WHERE key=%s", (key,))
                d.execute("INSERT INTO audit_log (actor_name, action, target, detail) VALUES ('system', 'settings.rebrand', %s, 'old built-in text replaced by the new default')", (key,))
        d.execute("INSERT INTO settings (key, value) VALUES ('rebrand_arksignal_done', '1')")
    # Built-in roles: User (auto-assigned at registration, no permissions) and
    # Admin (Super Admin). Neither can be deleted.
    d.execute("INSERT INTO roles (name, description, is_builtin) "
              "SELECT %s, %s, TRUE WHERE NOT EXISTS (SELECT 1 FROM roles WHERE lower(name)=lower(%s))",
              (USER_ROLE, "Default role for every registered account. Grants no administrative access.", USER_ROLE))
    d.execute("INSERT INTO roles (name, description, is_builtin, is_super_admin) "
              "SELECT %s, %s, TRUE, TRUE WHERE NOT EXISTS (SELECT 1 FROM roles WHERE lower(name)=lower(%s))",
              (ADMIN_ROLE, "Full, unrestricted access to everything.", ADMIN_ROLE))
    from .tools import seed_defaults
    seed_defaults(d)
    from .maplayers import seed_defaults as seed_layers
    seed_layers(d)
    from .maplayers import apply_weather_defaults
    apply_weather_defaults(d)
    from .maplayers import ensure_local_layers
    ensure_local_layers(d)
    from .maplayers import ensure_satellite_layer
    ensure_satellite_layer(d)
    from .maplayers import ensure_alerts_layer
    ensure_alerts_layer(d)
    from .maplayers import ensure_satellite_extra_layers
    ensure_satellite_extra_layers(d)
    from .maplayers import fix_alerts_label
    fix_alerts_label(d)
    from .maplayers import ensure_quakes_layer
    ensure_quakes_layer(d)
    from .maplayers import ensure_satellites_layer
    ensure_satellites_layer(d)
    from .maplayers import ensure_ais_layer
    ensure_ais_layer(d)
    from .maplayers import ensure_drones_layer
    ensure_drones_layer(d)
    from .maplayers import ensure_rivers_layer
    ensure_rivers_layer(d)
    from .maplayers import place_hazard_layers
    place_hazard_layers(d)
    from .maplayers import ensure_hybrid_layers
    ensure_hybrid_layers(d)
    d.commit()


def init_app(app):
    app.teardown_appcontext(close_db)
