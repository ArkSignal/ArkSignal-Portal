import os

from flask import Flask, render_template
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import db
from .settings import cfg, cfg_int
from .models import User

csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in."


@login_manager.user_loader
def load_user(user_id):
    return User.get(int(user_id))


def create_app():
    app = Flask(__name__)
    secret = os.environ.get("SECRET_KEY", "")
    if len(secret) < 16:
        raise RuntimeError("SECRET_KEY must be set (16+ characters)")
    app.config.update(
        SECRET_KEY=secret,
        DATABASE_URL=os.environ["DATABASE_URL"],
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=not os.environ.get("FORCE_INSECURE_COOKIES"),
        MAX_CONTENT_LENGTH=512 * 1024 * 1024,   # the ceiling; each request gets its own, smaller limit (see _body_limit)
        HUB_URL=os.environ.get("HUB_URL", "").strip(),
    )
    # one proxy hop in front (NPM)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    from . import usage as _usage
    _usage.install(app.config["DATABASE_URL"], app)   # counts every outside request this process makes (Admin > Usage)
    # A form's security token is tied to the sign-in session; the default one-hour expiry made any admin page left open for an hour
    # fail with a bare "400" on the next action. The token still dies with the session (Admin > Settings > Sign-In Session Length).
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    from . import security as _security
    csrf.init_app(app)
    login_manager.init_app(app)

    from .auth import bp as auth_bp
    from .admin import bp as admin_bp
    from .public import bp as public_bp
    from .live import bp as live_bp
    from .lookups import bp as lookups_bp
    from .my import bp as my_bp
    app.register_blueprint(public_bp)
    from .background import bp as background_bp
    app.register_blueprint(background_bp)
    app.register_blueprint(live_bp)
    from .histmap_views import bp as histmap_bp
    app.register_blueprint(histmap_bp)
    from .acars_views import bp as acars_bp
    app.register_blueprint(acars_bp)
    from .usage_views import bp as usage_bp
    app.register_blueprint(usage_bp)
    from .retention import bp as retention_bp
    app.register_blueprint(retention_bp)
    from .communities import bp as communities_bp
    app.register_blueprint(communities_bp)
    from .flightexport import bp as flightexport_bp
    app.register_blueprint(flightexport_bp)
    from .myflights import bp as myflights_bp
    app.register_blueprint(myflights_bp)
    from .myflightsio import bp as myflightsio_bp
    app.register_blueprint(myflightsio_bp)
    from .myflightstracks import bp as myflightstracks_bp
    app.register_blueprint(myflightstracks_bp)
    from .myflightsshare import bp as myflightsshare_bp
    app.register_blueprint(myflightsshare_bp)
    from .pushkinds import bp as pushkinds_bp
    app.register_blueprint(pushkinds_bp)
    from .schedules import bp as schedules_bp
    app.register_blueprint(schedules_bp)
    from .meshalertchan import bp as meshalertchan_bp
    app.register_blueprint(meshalertchan_bp)
    from .meshlinks import bp as meshlinks_bp
    app.register_blueprint(meshlinks_bp)
    from .meshbridge import bp as meshbridge_bp
    app.register_blueprint(meshbridge_bp)
    from .meshrelease import bp as meshrelease_bp
    app.register_blueprint(meshrelease_bp)
    from .mystations import bp as mystations_bp
    app.register_blueprint(mystations_bp)
    from .themes_views import bp as themes_bp
    app.register_blueprint(themes_bp)
    from .webpush import bp as webpush_bp
    app.register_blueprint(webpush_bp)
    from .matrix import bp as matrix_bp
    app.register_blueprint(matrix_bp)
    from .oidcprovider import bp as oidc_bp
    app.register_blueprint(oidc_bp)
    from .mesh_views import bp as mesh_bp
    app.register_blueprint(mesh_bp)
    from .mesh_alerts import bp as mesh_alerts_bp
    app.register_blueprint(mesh_alerts_bp)
    from .mesh_channels import bp as mesh_channels_bp
    app.register_blueprint(mesh_channels_bp)
    from .seo import bp as seo_bp
    app.register_blueprint(seo_bp)
    app.register_blueprint(lookups_bp)
    from .metrics import bp as metrics_bp
    app.register_blueprint(metrics_bp)
    from .status import bp as status_bp
    app.register_blueprint(status_bp)
    from .releasenotes import bp as releasenotes_bp
    app.register_blueprint(releasenotes_bp)
    from .planefence import bp as planefence_bp
    app.register_blueprint(planefence_bp)
    from .offsite import bp as offsite_bp
    app.register_blueprint(offsite_bp)
    from .restorepart_views import bp as restorepart_bp
    app.register_blueprint(restorepart_bp)
    from .roadmap import bp as roadmap_bp
    app.register_blueprint(roadmap_bp)
    from .livecontent_views import bp as livecontent_bp
    app.register_blueprint(livecontent_bp)
    from .hems import bp as hems_bp
    app.register_blueprint(hems_bp)
    from .apikeys import bp as apikeys_bp
    app.register_blueprint(apikeys_bp)
    from .passkeys import bp as passkeys_bp
    app.register_blueprint(passkeys_bp)
    from .aprspush import bp as aprspush_bp
    app.register_blueprint(aprspush_bp)
    from .satpass import bp as satpass_bp
    app.register_blueprint(satpass_bp)
    from .radiolists import bp as radiolists_bp
    app.register_blueprint(radiolists_bp)
    from .satimages import bp as satimages_bp
    app.register_blueprint(satimages_bp)
    from .goes import bp as goes_bp
    app.register_blueprint(goes_bp)
    from .googleauth import bp as googleauth_bp
    app.register_blueprint(googleauth_bp)
    from .ais import bp as ais_bp
    app.register_blueprint(ais_bp)
    from .drones import bp as drones_bp
    app.register_blueprint(drones_bp)
    from .chat import bp as chat_bp
    app.register_blueprint(chat_bp)
    from .messages import bp as messages_bp
    app.register_blueprint(messages_bp)
    from .friends import bp as friends_bp
    app.register_blueprint(friends_bp)
    from .bridge import bp as bridge_bp
    app.register_blueprint(bridge_bp)
    from .bridge_verify import bp as bridge_verify_bp
    app.register_blueprint(bridge_verify_bp)
    from .interesting import bp as interesting_bp
    app.register_blueprint(interesting_bp)
    from .drivetimes import bp as drivetimes_bp
    app.register_blueprint(drivetimes_bp)
    from .hospitals import bp as hospitals_bp
    app.register_blueprint(hospitals_bp)
    from .dataupdates_views import bp as dataupdates_bp
    app.register_blueprint(dataupdates_bp)
    from .places import bp as places_bp
    app.register_blueprint(places_bp)
    from .support import bp as support_bp
    app.register_blueprint(support_bp)
    from .weather_views import bp as weather_bp
    app.register_blueprint(weather_bp)
    from .counties_views import bp as counties_bp
    app.register_blueprint(counties_bp)
    from .trauma import bp as trauma_bp
    app.register_blueprint(trauma_bp)
    from .repeaters_views import bp as repeaters_bp
    app.register_blueprint(repeaters_bp)
    from .aprs_views import bp as aprs_bp
    app.register_blueprint(aprs_bp)
    from .sondes_views import bp as sondes_bp
    app.register_blueprint(sondes_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(my_bp)
    app.register_blueprint(admin_bp)
    from .useredit import bp as useredit_bp
    app.register_blueprint(useredit_bp)

    # Both gunicorn workers start together; a session-level advisory lock makes schema
    # creation, role seeding and first-admin creation run one worker at a time.
    with app.app_context():
        conn = db.get_db()
        conn.execute("SELECT pg_advisory_lock(727001)")
        try:
            db.init_db()
            User.bootstrap_admin(os.environ.get("ADMIN_USERNAME", "admin"),
                                 os.environ.get("ADMIN_EMAIL", ""),
                                 os.environ.get("ADMIN_PASSWORD", ""))
            from .vault import encrypt_existing
            encrypt_existing(conn)   # secrets an older version stored as plain text get sealed
            conn.commit()   # (the connection is rolled back below: without this the sealing never reached the database)
        finally:
            conn.rollback()
            conn.execute("SELECT pg_advisory_unlock(727001)")
            conn.commit()

    def _idle_seconds():
        """The inactivity limit that applies to the signed-in person, in seconds (0 for none), so the page can warn before it happens."""
        from flask_login import current_user
        if not current_user or not current_user.is_authenticated:
            return 0
        return _security.limits(current_user)[1] * 60

    def _alerts_unseen():
        """Alert matches the signed-in user has not looked at yet (0 for visitors)."""
        from flask import g
        from flask_login import current_user
        if not current_user or not current_user.is_authenticated:
            return 0
        if not hasattr(g, "_unseen"):
            g._unseen = db.get_db().execute(
                "SELECT COUNT(*) AS n FROM alert_events e JOIN users u ON u.id = e.user_id WHERE e.user_id=%s AND e.ts > u.alerts_seen_at",
                (current_user.id,)).fetchone()["n"]
        return g._unseen

    def _server_prefs():
        """The signed-in user's saved display preferences (an empty dict for visitors)."""
        from flask import g
        from flask_login import current_user
        if not current_user or not current_user.is_authenticated:
            return {}
        if not hasattr(g, "_prefs"):
            import json
            row = db.get_db().execute("SELECT prefs FROM users WHERE id=%s", (current_user.id,)).fetchone()
            try:
                g._prefs = json.loads(row["prefs"]) if row and row["prefs"] else {}
            except ValueError:
                g._prefs = {}
        return g._prefs

    from . import build_info as _bi
    _APP_VERSION = _bi.read()["version"]

    def _mark_cfg():
        """What the airline/military marks (partials/_airline_mark.html) need from the settings."""
        prefixes = {}
        for pair in cfg("military_callsign_services").split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                if k.strip() and v.strip():
                    prefixes[k.strip().upper()] = v.strip().lower()
        return {"logos": bool(cfg("airline_logo_url").strip()) and cfg("lookups_enabled") == "1",
                "mil": {"prefixes": prefixes, "default": cfg("military_default_service").strip().lower(),
                        "services": ["usaf", "army", "navy", "usmc", "uscg"]}}

    def _pm_unread():
        try:
            from flask_login import current_user as _cu
            if _cu.is_authenticated and cfg("pm_enabled") == "1" and cfg("matrix_enabled") != "1":
                from .messages import unread_count
                return unread_count(_cu.id)
        except Exception:
            pass
        return 0

    def _social_counts():
        """The numbers beside Chat and Friends in the menu (messages have their own, above)."""
        try:
            from flask_login import current_user as _cu
            if _cu.is_authenticated:
                from . import social
                return {"chat_unread": social.chat_unread(_cu.id) if cfg("chat_enabled") == "1" and cfg("matrix_enabled") != "1" else 0, "friend_requests": social.friend_requests(_cu.id) if cfg("friends_enabled") == "1" else 0}
        except Exception:
            pass
        return {"chat_unread": 0, "friend_requests": 0}

    def _google_on():
        try:
            from .googleauth import configured
            return configured()
        except Exception:
            return False

    @app.context_processor
    def inject_globals():
        from . import tools
        from flask_login import current_user
        from . import themes as _themes
        return {"ark_themes": _themes.public_config(), "unit_area": _units.area_now(), "unit_areas": _units.AREAS, "unit_site_by": {a: _units.site_default(a) for a, _l in _units.AREAS if _units.site_default(a)}, "repeater_on": cfg("repeater_enabled") == "1", "acars_on": cfg("acars_enabled") == "1", "mesh_on": cfg("mesh_enabled") == "1", "seo": __import__("app.seo", fromlist=["page_info"]).page_info() if __import__("flask").has_request_context() else {}, "mesh_channels_on": cfg("mesh_enabled") == "1" and cfg("mesh_channels_enabled") == "1", "myflights_on": cfg("myflights_enabled") == "1", "myflights_share_on": cfg("myflights_sharing") == "1", "push_on_site": cfg("push_enabled") == "1", "schedules_on": cfg("schedules_enabled") == "1" and bool(cfg("schedules_key")), "site_name": cfg("site_name"), "brand_name": cfg("brand_name"), "brand_accent": cfg("brand_accent"), "page_width_mode": cfg("page_width_mode"), "page_max_width": int(cfg("page_max_width")), "registration_open": cfg("registration_open") == "1",
                "nav_tools": [t for t in tools.list_enabled() if current_user.is_authenticated or t["slug"] not in ("planefence", "plane-alert")], "nav_groups": _nav_groups(), "nav_sub": next((g for g in _nav_groups() if g["active"]), None), "feeder_stats": any(t["slug"] == "network-feeders" for t in tools.list_enabled()), "min_password": cfg_int("min_password_length"),
                "kiosk_on": cfg("kiosk_enabled") == "1", "api_on": cfg("api_enabled") == "1", "passkeys_on": cfg("passkeys_enabled") == "1", "listings_on": cfg("radiolists_enabled") == "1", "satimg_on": cfg("satimg_enabled") == "1", "google_on": _google_on(), "ais_on": cfg("ais_enabled") == "1", "rid_on": cfg("rid_enabled") == "1", "goes_on": cfg("goes_enabled") == "1", "chat_on": cfg("chat_enabled") == "1" or cfg("matrix_enabled") == "1", "pm_on": cfg("pm_enabled") == "1" and cfg("matrix_enabled") != "1", "pm_unread": _pm_unread(), "friends_on": cfg("friends_enabled") == "1", "matrix_on": cfg("matrix_enabled") == "1", "bridges_on": (cfg("chat_enabled") == "1" or cfg("matrix_enabled") == "1") and (cfg("bridge_aprs_enabled") == "1" or cfg("bridge_mesh_enabled") == "1"), **_social_counts(), "map_panels_open": cfg("map_panels_open") == "1", "site_units": cfg("live_units"), "map_area": __import__("app.settings", fromlist=["map_area"]).map_area, "tile_url": cfg("map_tile_url"), "tile_attr": cfg("map_attribution"), "leaflet_base": cfg("leaflet_base_url"),
                # radius of the circle drawn for a General-privacy location: half the rounding step, in metres
                "site_tz": cfg("timezone"), "idle_seconds": _idle_seconds(), "idle_warning": cfg_int("idle_warning_seconds"), "password_rules": _security.password_rules_text(), "support_on": __import__("app.support", fromlist=["on"]).on(), "password_mix": cfg("password_require_mix") == "1", "alerts_unseen": _alerts_unseen(), "alert_poll_seconds": max(15, cfg_int("live_refresh_seconds") * 10), "app_version": _APP_VERSION, "server_prefs": _server_prefs(),
                "mark_cfg": _mark_cfg(), "approx_radius_m": int(0.5 * 10 ** -cfg_int("general_precision_decimals") * 111320)}

    from . import hostredirect as _hostredirect
    app.before_request(_hostredirect.enforce)
    app.before_request(__import__("app.adminallow", fromlist=["enforce"]).enforce)
    from .siteaddress import bp as siteaddress_bp
    app.register_blueprint(siteaddress_bp)

    def _nav_groups():
        from flask import g as _g
        if not hasattr(_g, "_nav_groups"):
            _g._nav_groups = __import__("app.navmenu", fromlist=["groups"]).groups(tools.list_enabled())
        return _g._nav_groups

    @app.before_request
    def apply_session_lifetime():
        # the cookie lives as long as the longest limit that can apply to anyone; security.enforce applies the real limits per person
        longest = max(cfg_int("session_hours"), cfg_int("admin_session_hours")) if cfg_int("session_hours") else 0
        app.permanent_session_lifetime = timedelta(hours=longest or 24 * 30)

    @app.before_request
    def _unfiltered_for_machines():
        # a receiver posting to its private upload address, or a key-holder calling the API, is not a signed-in person: the feature switches answer with what is saved (see settings.cfg)
        import re as _re
        from flask import request as _rq
        if _re.match(r"^/(acars/push|ais/push|drones/push|sat/push|sondes/push|wx/push|wx/wu|wx/relay)/|^/api/v1/|^/bridge/mesh/|^/\.well-known/(matrix|openid-configuration)|^/matrix/element-config\.json|^/_matrix/|^/oidc/", _rq.path):   # and the machine addresses of the radio bridge gateway, Matrix and the sign-in provider (token guarded)
            from flask import g as _gg
            _gg._cfg_raw = True

    def _body_limit():
        # how much a request may send: small for everything a visitor or a receiver can reach (an anonymous request must not be able to make the server hold a huge body in memory), larger where a person has signed in
        # to bring a file (an administrator restoring a backup, a member importing flights) and for a satellite receiver's picture
        from flask import request as _rq
        p = _rq.path
        mb = cfg_int("request_limit_mb")
        if p.startswith("/admin/"):
            mb = max(mb, cfg_int("request_limit_admin_mb"))
        elif p.startswith("/my/flights/"):
            mb = max(mb, cfg_int("request_limit_member_mb"))
        elif p.startswith("/sat/push/"):
            mb = max(mb, cfg_int("satimg_max_mb") + 2)
        _rq.max_content_length = mb * 1024 * 1024

    app.before_request_funcs.setdefault(None, []).insert(0, _body_limit)   # first of all, before the form token check reads the body

    app.before_request(_security.enforce)
    app.after_request(_security.apply_headers)
    app.jinja_env.globals["csp_nonce"] = _security.csp_nonce   # {{ csp_nonce }} in a script tag's nonce attribute (the function is called by the template)

    def _site_time(value, fmt):
        if isinstance(value, str):  # timestamps kept in settings are ISO text
            value = datetime.fromisoformat(value)
        try:
            zone = ZoneInfo(cfg("timezone"))
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        return value, value.astimezone(zone).strftime(fmt)

    from . import units as _units
    from . import adminnav as _adminnav
    from . import shop as _shop
    app.jinja_env.globals.update(shop_link=_shop.link, shop_note=_shop.disclosure)
    app.jinja_env.globals.update(admin_nav=_adminnav.visible)
    app.jinja_env.globals.update(dist_unit=_units.unit_for, dist_show=_units.show)
    app.jinja_env.filters["dist"] = lambda value, base="m": _units.fmt(value, base)
    for _k in ("alt", "spd", "vspd", "dist", "temp", "nm", "press", "rain"):   # a value in meters, m/s, kilometers or degrees C, shown in the viewer's own units (Display menu > Units)
        app.jinja_env.filters["u_" + _k] = (lambda k: lambda value, digits=None, area=None: _units.text(k, value, digits, area))(_k)
    app.jinja_env.globals["units_system"] = _units.system
    from . import privacy as _privacy
    app.jinja_env.globals["privacy_help"] = _privacy.help_text
    from . import maptypes as _maptypes
    app.jinja_env.globals["map_types"] = _maptypes.visible_types
    from . import repeater_digital as _rd
    app.jinja_env.globals["rp_attr_keys"] = _rd.ATTR_KEYS
    import json as _json
    app.jinja_env.filters["fromjson"] = lambda s: (lambda v: v if isinstance(v, dict) else {})(_json.loads(s)) if s else {}
    app.jinja_env.globals["map_sections_off"] = _maptypes.sections_off
    app.jinja_env.globals["map_hybrid"] = lambda: __import__("app.settings", fromlist=["cfg"]).cfg("map_hybrid_layers")
    app.jinja_env.globals["map_satellite"] = lambda: __import__("app.settings", fromlist=["cfg"]).cfg("map_satellite_layer")
    app.jinja_env.filters["u_ft"] = _units.text_ft
    app.jinja_env.filters["u_kt"] = _units.text_kt
    app.jinja_env.globals["unit_name"] = _units.unit_name

    @app.template_filter("callurl")
    def callurl_filter(call):
        """A station name for the end of a web address: a slash (APRS repeaters such as 147.090/R) becomes a tilde, so the address is one plain segment that no proxy or browser can split or mangle."""
        from urllib.parse import quote
        return quote(str(call or "").replace("/", "~"), safe="~.-_")

    @app.template_filter("cs")
    def cs_filter(call):
        """A call sign as a link to the Call Sign Lookup, with its license as hover text (static/callsign.js); anything that is not a call sign is returned as plain text."""
        from markupsafe import Markup, escape
        import re as _re
        base = _re.sub(r"[^A-Z0-9]", "", str(call or "").upper().split("-")[0].split("/")[0])
        if not call or not _re.fullmatch(r"[A-Z]{1,2}[0-9][A-Z]{1,3}", base) or cfg("callsign_lookup_enabled") != "1":
            return escape(call or "")
        return Markup('<a class="cs" data-call="%s" href="/callsign?call=%s">%s</a>') % (escape(call), base, escape(call))

    @app.template_filter("localtime_text")
    def localtime_text(value, fmt="%Y-%m-%d %H:%M"):
        """Plain text in the site time zone, for places that cannot hold markup (attributes, confirmations)."""
        return "never" if not value else _site_time(value, fmt)[1]

    @app.template_filter("localtime")
    def localtime(value, fmt="%Y-%m-%d %H:%M"):
        """A stored UTC timestamp as a <time> element. The text is the site time zone (Admin Settings); the visitor's browser
        re-writes it in their own time zone and clock style (Display menu) as soon as the page loads."""
        if not value:
            return "never"
        from markupsafe import Markup
        dt, text = _site_time(value, fmt)
        flag = "d" if "%H" not in fmt else ("dts" if "%S" in fmt else "dt")
        return Markup('<time class="ts" datetime="{}" data-fmt="{}">{}</time>').format(dt.astimezone(ZoneInfo("UTC")).isoformat(), flag, text)

    @app.template_filter("filesize")
    def filesize(n):
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024

    @app.errorhandler(403)
    @app.errorhandler(404)
    def err(e):
        from flask import request
        if request.headers.get("Sec-Fetch-Dest") == "iframe":
            # inside a tool frame: a short message, not a second copy of the whole portal
            return (f"<!doctype html><meta charset=utf-8><body style=\"font:15px system-ui;background:#070d17;color:#8a9bb3;"
                    f"display:grid;place-items:center;height:100vh;margin:0\"><div>{e.code}: this page is not available here.</div>"), e.code
        return render_template("error.html", code=e.code, msg=e.name), e.code

    if not os.environ.get("DISABLE_BACKUP_SCHEDULER"):
        from .backup import start_scheduler
        from .collector import start_collector
        from .alerts import start_alerts
        start_scheduler(app)
        start_collector(app)
        start_alerts(app)
        from .flightlog import start_flightlog
        start_flightlog(app)
        from .receivers import start_receivers
        start_receivers(app)
        from .dataupdates import start_scheduler as start_data_updates
        start_data_updates(app)   # airports, places, hospitals, trauma levels and air ambulances
        from .weather import start_poller as start_weather
        start_weather(app)   # reads members' own Ecowitt.net accounts, rolls readings up to hourly rows
        from .aprspush import start_push as start_aprs_push
        start_aprs_push(app)   # sends members' weather stations to CWOP or APRS-IS when they ask for it
        from .bridge import start_listener as start_bridge
        start_bridge(app)   # hears APRS messages for members who linked a chat to a call sign (one web worker at a time)
        from .sondes import start_feed as start_sondes
        start_sondes(app)   # the live radiosonde and balloon feed (one web worker at a time holds it)
        from .wxstats import start_collector as start_wxstats
        start_wxstats(app)   # keeps a reading of every weather station that reports (members, APRS, airports) for the statistics
        from .meshrelease import start as start_meshrelease
        start_meshrelease(app)   # reads the newest Meshtastic firmware and protobuf releases once a day
        from .retention import start as start_retention
        start_retention(app)   # thins old detail once a night when an administrator has set it up (Admin > Data Retention)
        from .status import start_collector as start_status
        start_status(app)   # the service checks run here in the background, on a schedule: the status pages only read the result
        from .update_check import start_update_checks
        start_update_checks(app)

    if not os.environ.get("DISABLE_MESH_SYNC"):   # not tied to the background-jobs switch the test portal turns off: a restarted broker must get its logins back by itself
        from .mesh import start_login_sync
        start_login_sync(app)   # the mesh broker's member logins are kept in step with this database by themselves
        from .matrix import start_sync as start_matrix_sync
        start_matrix_sync(app)   # the Matrix server is kept in step with the site (accounts, blocks, moderators, standing rooms)
        from .mesh_alerts import start as start_mesh_alerts
        start_mesh_alerts(app)   # owners are told when their router goes quiet or a battery is low
        from .pushkinds import start as start_pushwatch
        from .meshalertchan import start as start_meshalerts
        from .meshlinks import start as start_meshlinks
        start_meshlinks(app)   # the mesh service is told which community links are active
        start_meshalerts(app)   # warnings and weather summaries for the communities that chose the alert channel
        start_pushwatch(app)   # weather warnings, earthquakes, floods, satellite passes, landed balloons and admin items for the members who asked

    return app
