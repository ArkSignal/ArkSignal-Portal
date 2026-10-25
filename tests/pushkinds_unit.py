"""Runs inside the container (tests/pushkinds_test.py): every kind of notification through the registry, with the push sender replaced by a recorder and the layers' data replaced by fixed answers, so nothing depends on timing or on the
outside world. Prints one JSON line."""
import os, json
from datetime import datetime, timedelta, timezone
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["DISABLE_MESH_SYNC"] = "1"
from app import create_app
app = create_app()
from app import pushkinds as pk, webpush, vault, wxalerts, quakes, rivers, satpass, adminoverview
from app.db import get_db
from app.settings import set_setting
out = {}
calls = []
webpush.send = lambda sub, payload, ttl=None, urgency="normal": (calls.append((sub["user_id"], payload, urgency)), (True, 201))[1]
NOW = datetime.now(timezone.utc)
with app.app_context():
    d = get_db()
    for k, v in (("push_enabled", "1"), ("push_kinds_off", ""), ("push_kind_per_hour", "10"), ("push_kind_per_day", "40"), ("wxalerts_enabled", "1"), ("quakes_enabled", "1"), ("rivers_enabled", "1"), ("satpass_enabled", "1"),
                 ("push_quake_min_mag", "3.0"), ("push_river_min_category", "MIN"), ("push_weather_codes", "TOR,SVR,FLD"), ("push_satpass_lead_minutes", "15"), ("push_satpass_min_elev", "30")):
        set_setting(k, v)
    uid = lambda n: d.execute("SELECT id FROM users WHERE username=%s", (n,)).fetchone()["id"]
    a, b, adm = uid("plain1"), uid("claimer1"), uid("marty")
    for t in ("push_prefs", "push_sent", "push_seen", "member_places", "push_sats", "sonde_follows"):
        d.execute(f"DELETE FROM {t} WHERE user_id = ANY(%s)", ([a, b, adm],))
    d.execute("DELETE FROM push_subscriptions WHERE user_id = ANY(%s)", ([a, b, adm],))
    for n, u in enumerate((a, b, adm)):
        d.execute("INSERT INTO push_subscriptions (user_id, endpoint, p256dh_sealed, auth_sealed, pushkey_hash, device) VALUES (%s,%s,%s,%s,%s,'t')", (u, f"https://fcm.googleapis.com/u{n}", vault.seal("x"), vault.seal("y"), f"hk{n}"))
    d.execute("UPDATE users SET push_friends = true WHERE id = ANY(%s)", ([a, b],))
    d.execute("DELETE FROM mesh_alert_prefs WHERE user_id = ANY(%s)", ([a, b],))
    d.commit()
    mine = lambda u, kind=None: [c for c in calls if c[0] == u and (kind is None or c[1]["tag"].startswith(kind))]

    # ---- the registry and the switches
    out["keys_unique_and_known"] = len(pk.KEYS) == len(set(pk.KEYS)) == 13 and all(k[4] in ("member", "admin") for k in pk.KINDS)
    out["defaults"] = pk.enabled(a, "chat") is True and pk.enabled(a, "weather_alert") is False and pk.enabled(a, "friends") is True and pk.enabled(a, "mesh_node") is False
    pk.set_enabled(a, "weather_alert", True); pk.set_enabled(a, "friends", False); pk.set_enabled(a, "mesh_node", True); d.commit()
    out["switches_write_to_their_homes"] = pk.enabled(a, "weather_alert") and d.execute("SELECT push_friends FROM users WHERE id=%s", (a,)).fetchone()["push_friends"] is False and d.execute("SELECT by_push FROM mesh_alert_prefs WHERE user_id=%s", (a,)).fetchone()["by_push"] is True
    pk.set_enabled(a, "friends", True); pk.set_enabled(a, "mesh_node", False); d.commit()
    out["admin_kind_only_for_admins"] = pk.may_have(__import__("app.models", fromlist=["User"]).User(d.execute("SELECT * FROM users WHERE id=%s", (adm,)).fetchone()), "admin_attention") and not pk.may_have(__import__("app.models", fromlist=["User"]).User(d.execute("SELECT * FROM users WHERE id=%s", (a,)).fetchone()), "admin_attention")

    # ---- notify(): member switch, site switch, limits
    calls.clear()
    n1 = pk.notify(a, "chat", "t", "b", "/chat/")
    pk.set_enabled(a, "chat", False); d.commit()
    n2 = pk.notify(a, "chat", "t", "b", "/chat/")
    pk.set_enabled(a, "chat", True); set_setting("push_kinds_off", "chat,quake"); d.commit()
    n3 = pk.notify(a, "chat", "t", "b", "/chat/")
    set_setting("push_kinds_off", ""); set_setting("push_enabled", "0"); d.commit()
    n4 = pk.notify(a, "chat", "t", "b", "/chat/")
    set_setting("push_enabled", "1"); d.commit()
    out["notify_checks_switches"] = n1 == 1 and n2 == 0 and n3 == 0 and n4 == 0 and len(mine(a)) == 1
    d.execute("DELETE FROM push_sent WHERE user_id=%s", (a,)); d.commit(); calls.clear()
    set_setting("push_kind_per_hour", "2"); d.commit()
    r = [pk.notify(a, "chat", "t", "b", "/chat/") for _ in range(4)]
    out["limit_per_kind_per_hour"] = r == [1, 1, 0, 0] and len(mine(a)) == 2 and pk.notify(a, "friends", "t", "b", "/x") == 1
    set_setting("push_kind_per_hour", "10"); d.execute("DELETE FROM push_sent WHERE user_id=%s", (a,)); d.commit(); calls.clear()

    # ---- quiet hours (site time zone): held back, urgent ones allowed through when the member says so
    from zoneinfo import ZoneInfo
    from app.settings import cfg
    hh = datetime.now(timezone.utc).astimezone(ZoneInfo(cfg("timezone"))).hour
    pk.set_enabled(a, "weather_alert", True); d.execute("UPDATE users SET push_quiet_start=%s, push_quiet_end=%s, push_quiet_urgent=true WHERE id=%s", (hh, (hh + 2) % 24, a)); d.execute("DELETE FROM push_sent WHERE user_id=%s", (a,)); d.commit(); calls.clear()
    out["quiet_holds_a_normal_notice"] = pk.notify(a, "chat", "t", "b", "/chat/") == 0 and pk.in_quiet_hours(a) == (True, True)
    out["quiet_lets_an_urgent_one_through"] = pk.notify(a, "weather_alert", "t", "b", "/x", urgency="high") == 1
    d.execute("UPDATE users SET push_quiet_urgent=false WHERE id=%s", (a,)); d.commit()
    out["quiet_without_the_urgent_allowance_holds_it"] = pk.notify(a, "weather_alert", "t2", "b", "/x", urgency="high") == 0
    d.execute("UPDATE users SET push_quiet_start=%s, push_quiet_end=%s WHERE id=%s", ((hh + 3) % 24, (hh + 5) % 24, a)); d.commit()
    out["outside_the_window_sends"] = pk.in_quiet_hours(a)[0] is False and pk.notify(a, "chat", "t", "b", "/chat/") == 1
    d.execute("UPDATE users SET push_quiet_start=%s, push_quiet_end=%s WHERE id=%s", ((hh + 23) % 24, (hh + 2) % 24, a)); d.commit()
    out["a_window_past_midnight_works"] = pk.in_quiet_hours(a)[0] is True
    set_setting("push_quiet_enabled", "0"); d.commit()
    out["the_site_can_switch_quiet_hours_off"] = pk.in_quiet_hours(a)[0] is False
    set_setting("push_quiet_enabled", "1")
    d.execute("UPDATE users SET push_quiet_start=-1, push_quiet_end=-1, push_quiet_urgent=true WHERE id=%s", (a,)); d.execute("DELETE FROM push_sent WHERE user_id=%s", (a,)); d.commit(); calls.clear()

    # ---- weather warnings: inside the outline, near it, far away, a kind the site does not send
    d.execute("INSERT INTO member_places (user_id, name, lat, lon, radius_km) VALUES (%s,'Home',34.75,-92.29,10), (%s,'Far',40.0,-100.0,10)", (a, a)); d.commit()
    sq = lambda la, lo, h: [[lo - h, la - h], [lo + h, la - h], [lo + h, la + h], [lo - h, la + h], [lo - h, la - h]]
    feat = lambda kind, ev, ring: {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"event": ev, "KIND": kind, "headline": ev + " issued", "area": "Pulaski", "ends": "2099-01-01T00:00:00Z"}}
    wxalerts.geojson = lambda: {"type": "FeatureCollection", "features": [feat("TOR", "Tornado Warning", sq(34.75, -92.29, 0.3)), feat("SVR", "Severe Thunderstorm Warning", sq(36.0, -92.29, 0.05)), feat("HOT", "Heat Advisory", sq(34.75, -92.29, 0.3)),
                                                                              feat("FLD", "Flood Warning", sq(34.80, -92.29, 0.01))]}
    s1 = pk._watch_weather(); d.commit()
    ev = [c[1]["title"] for c in mine(a, "wx-")]
    out["weather_inside_and_near_only"] = s1 == 2 and sorted(ev) == ["Flood Warning near Home", "Tornado Warning near Home"]
    out["weather_not_repeated"] = pk._watch_weather() == 0 and len(mine(a, "wx-")) == 2
    out["weather_off_for_others"] = len(mine(b)) == 0

    # ---- earthquakes
    qf = lambda mag, la, lo, h: {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lo, la, 5]}, "properties": {"mag": mag, "headline": "5 km N of Somewhere", "time": (NOW - timedelta(hours=h)).isoformat(), "KIND": "M3"}}
    pk.set_enabled(a, "quake", True); d.commit(); calls.clear()
    quakes.geojson = lambda: {"type": "FeatureCollection", "features": [qf(4.1, 34.78, -92.30, 1), qf(2.0, 34.78, -92.30, 1), qf(4.5, 39.0, -92.0, 1), qf(4.0, 34.78, -92.30, 20)]}
    s1 = pk._watch_quakes(); d.commit()
    ev = [c[1]["title"] for c in mine(a, "quake")]
    out["quake_size_distance_and_age"] = s1 == 1 and ev == ["M 4.1 earthquake near Home"]
    out["quake_not_repeated"] = pk._watch_quakes() == 0

    # ---- rivers
    pk.set_enabled(a, "river", True); d.commit(); calls.clear()
    gf = lambda kind, name, la, lo: {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lo, la]}, "properties": {"event": name, "KIND": kind, "headline": "Stage 30 ft"}}
    rivers.geojson = lambda: {"type": "FeatureCollection", "features": [gf("MOD", "Arkansas River at Little Rock", 34.76, -92.30), gf("ACT", "Fourche Creek", 34.76, -92.30), gf("MAJ", "White River at Newport", 35.6, -91.3)]}
    s1 = pk._watch_rivers(); d.commit()
    ev = [c[1]["title"] for c in mine(a, "river")]
    out["river_level_and_distance"] = s1 == 1 and ev == ["Arkansas River at Little Rock: moderate flooding"]
    rivers.geojson = lambda: {"type": "FeatureCollection", "features": [gf("MAJ", "Arkansas River at Little Rock", 34.76, -92.30)]}
    out["river_worse_level_is_new"] = pk._watch_rivers() == 1

    # ---- satellite pass reminders
    pk.set_enabled(a, "satpass", True)
    d.execute("INSERT INTO push_sats (user_id, norad) VALUES (%s, 25544)", (a,)); d.commit(); calls.clear()
    satpass.elements = lambda: [{"norad": 25544, "name": "ISS (ZARYA)", "line1": "1", "line2": "2"}]
    satpass.satrec = lambda l1, l2: object()
    rise = lambda mins, el: [{"aos": NOW + timedelta(minutes=mins), "los": NOW + timedelta(minutes=mins + 6), "max_el": el, "az_aos": 270.0}]
    satpass.passes = lambda sat, obs, start, hours, min_elev: rise(10, 62)
    s1 = pk._watch_satellites(); d.commit()
    ev = [c[1] for c in mine(a, "sat-")]
    out["satpass_reminder"] = s1 == 2 and len(ev) == 2 and ev[0]["title"].startswith("ISS (ZARYA) rises in") and "Home" in ev[0]["body"] + ev[1]["body"]
    out["satpass_not_repeated"] = pk._watch_satellites() == 0
    satpass.passes = lambda sat, obs, start, hours, min_elev: rise(40, 62)
    d.execute("DELETE FROM push_seen WHERE user_id=%s AND kind='satpass'", (a,)); d.commit(); calls.clear()
    out["satpass_not_before_the_lead_time"] = pk._watch_satellites() == 0 and not mine(a, "sat-")
    pk.set_enabled(a, "satpass", False); d.commit()
    satpass.passes = lambda sat, obs, start, hours, min_elev: rise(5, 62)
    out["satpass_off_sends_nothing"] = pk._watch_satellites() == 0

    # ---- followed balloons
    d.execute("DELETE FROM sondes WHERE serial='PKTEST1'")
    d.execute("INSERT INTO sondes (serial, type, status, first_seen, last_seen, landing_lat, landing_lon) VALUES ('PKTEST1','RS41','landed', now(), now(), 35.1, -92.4)")
    d.execute("INSERT INTO sonde_follows (user_id, serial) VALUES (%s,'PKTEST1'), (%s,'PKTEST1')", (a, b)); d.commit(); calls.clear()
    pk.set_enabled(b, "balloon", False); d.commit()
    s1 = pk._watch_balloons(); d.commit()
    out["balloon_landed_for_the_follower_with_it_on"] = s1 == 1 and len(mine(a, "balloon")) == 1 and not mine(b) and "has landed" in mine(a)[0][1]["title"]
    out["balloon_not_repeated"] = pk._watch_balloons() == 0
    d.execute("DELETE FROM sondes WHERE serial='PKTEST1'"); d.commit()

    # ---- items needing attention (administrators only)
    calls.clear()
    adminoverview.build = lambda user: {"attention": [{"level": "warn", "text": "ADS-B hub: no data for 10 minutes", "link": "/admin/status", "label": "Status"}, {"level": "info", "text": "A release is available", "link": "/admin/updates", "label": "x"}]}
    pk.set_enabled(a, "admin_attention", True); d.commit()
    s1 = pk._watch_admin(); d.commit()
    out["admin_items"] = s1 == 1 and len(mine(adm)) == 1 and mine(adm)[0][1]["body"].startswith("ADS-B hub") and not mine(a) and mine(adm)[0][1]["url"] == "/admin/status"
    out["admin_items_not_repeated"] = pk._watch_admin() == 0

    # ---- the whole pass keeps going when one kind fails
    quakes.geojson = lambda: 1 / 0
    d.execute("DELETE FROM push_seen WHERE user_id=%s", (a,)); d.commit(); calls.clear()
    out["one_failing_kind_does_not_stop_the_pass"] = pk.watch_pass() >= 1

    # ---- the choice gates the old senders
    out["aircraft_master_switch"] = pk.wants(a, "aircraft") is True
    pk.set_enabled(a, "aircraft", False); d.commit()
    out["aircraft_master_switch_off"] = pk.wants(a, "aircraft") is False
    for t in ("push_prefs", "push_sent", "push_seen", "member_places", "push_sats", "sonde_follows"):
        d.execute(f"DELETE FROM {t} WHERE user_id = ANY(%s)", ([a, b, adm],))
    d.execute("DELETE FROM push_subscriptions WHERE user_id = ANY(%s)", ([a, b, adm],))
    d.execute("DELETE FROM mesh_alert_prefs WHERE user_id = ANY(%s)", ([a, b],))
    d.execute("UPDATE users SET push_friends = true WHERE id = ANY(%s)", ([a, b],))
    d.commit()
print(json.dumps(out))
