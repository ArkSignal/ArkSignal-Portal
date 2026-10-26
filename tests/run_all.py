import re, subprocess, sys, requests, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])

# start from a fresh database so every suite sees the same state
subprocess.run(["ssh", "-q", "nastemp", "cd ~/adsb-portal && echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker compose down -v && echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker compose up -d"], check=True, capture_output=True)
for _ in range(60):
    try:
        if requests.get(B + "/healthz", timeout=3).text == "ok":
            break
    except Exception:
        pass
    time.sleep(2)
time.sleep(5)

# the test portal does not listen to SondeHub (the suites that need balloons seed them through the collector); a live feed in one web worker skews which worker answers, which the timing suites assume is even (done first of all: the feed starts about 40 seconds after the portal does, and a flight it stored before this would break the counts)
subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('sonde_live_enabled','0') ON CONFLICT (key) DO UPDATE SET value='0'\""], capture_output=True)

# helper scripts must be inside the container (it is recreated on each deploy)
for f in ("mkfeeders.py", "mkpublic.py", "alerts_tick.py"):
    subprocess.run(["scp", "-q", f, f"nastemp:adsb-portal/{f}"], check=True, capture_output=True)
    subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker cp ~/adsb-portal/{f} adsb-portal:/tmp/{f}"], check=True, capture_output=True)

subprocess.run([sys.executable, "testsite_on.py", "--reset"], capture_output=True, timeout=300)   # the suites assume the built-in defaults, not the all-features-on test site
# baseline users
s = requests.Session()
t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/register").text).group(1)
s.post(B + "/my/register", data={"csrf_token": t, "username": "plain1", "email": "plain1@example.com", "password": "plainpassword1", "confirm": "plainpassword1"})

# airports first (other tests rely on the data)
adm = sess("marty", PW)
act(adm, "/admin/airports/import", "/admin/airports", countries="US")
# the public data cache would hide rows the suites write straight into the database; cache_test turns it on to test it
subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('public_cache_seconds','0') ON CONFLICT (key) DO UPDATE SET value='0'\""], capture_output=True)
subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('signin_ip_failures','0') ON CONFLICT (key) DO UPDATE SET value='0'\""], capture_output=True)
subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('log_unknown_usernames','exact') ON CONFLICT (key) DO UPDATE SET value='exact'\""], capture_output=True)
subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('sondesites_update_days','0') ON CONFLICT (key) DO UPDATE SET value='0'\""], capture_output=True)
subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('traumasystem_update_days','0') ON CONFLICT (key) DO UPDATE SET value='0'\""], capture_output=True)
subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('placemedia_update_days','0') ON CONFLICT (key) DO UPDATE SET value='0'\""], capture_output=True)

# the browser suites were written for maps that open with their panels showing; the shipped default is closed (panels_test covers both)
subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"INSERT INTO settings (key, value) VALUES ('map_panels_open','1') ON CONFLICT (key) DO UPDATE SET value='1'\""], capture_output=True)

results = {}
for name in ("smoke_test", "freshdb_test", "jscheck_test", "roles_test", "settings_test", "sections_test", "lookups_test", "layers_test", "prefs_test", "security_test", "account_test", "profile_test", "release_test", "flight_test", "range_test", "feederfilter_test", "history_test", "backup_test", "airports_test", "claim_test", "public_test", "home_test", "feed_test", "tools_test", "alerts_test", "config_test", "mobile_test", "roleui_test", "liveui_test", "skyview_test", "extras_test", "flightlog_test", "flightextra_test", "guestaccess_test", "reset_defaults", "webhook_test", "sessions_test", "twofactor_test", "privacy_test", "mlat_test", "status_test", "feedpage_test", "releasenotes_test", "planefence_test", "offsite_test", "regpage_test", "restorepart_test", "livecontent_test", "support_test", "uinav_test", "uilists_test", "overview_test", "redirect_test", "navmenu_test", "chips_test", "subbar_test", "places_test", "hospitals_test", "legend_test", "trauma_test", "hems_test", "useredit_test", "hospfilter_test", "hospsvc_test", "hosploc_test", "addrlookup_test", "hospimport_ui_test", "interesting_test", "foldflyout_test", "hospcheck_test", "hospissues_test", "adminwidth_test", "adminnav_test", "mapbutton_test", "sitewidth_test", "pagewidth_test", "radarsize_test", "authpages_test", "homepage_test", "hosptrauma_test", "units_test", "adminoverview_test", "dataupdates_test", "icon_test", "weather_test", "homelimit_test", "feedcheck_wx_test", "adminadd_test", "rolesplit_test", "panels_test", "services_test", "backupcover_test", "sonderecovery_test", "sondechase_ui_test", "mapopen_test", "prodro_test", "unitsite_test", "stackd_unit", "backupmatrix_unit", "sondes_test", "archive_test", "predict_test", "repeaters_test", "aprs_test", "acars_test", "mesh_test", "colocate_test", "meshlinks_test", "meshareas_test", "meshalertchan_test", "meshalerts_test", "meshchannels_test", "meshpersist_test", "meshhistory_test", "meshplan_test", "meshhw_test", "shop_test", "communities_test", "communitiesmap_test", "meshbridge_test", "communitybroker_test", "communityshare_test", "communityjoin_test", "meshrelease_test", "flightexport_test", "myflights_test", "myflightsio_test", "myflightstracks_test", "myflightsstats_test", "myflightsshare_test", "schedules_test", "myflightsbrowser_test", "replay_test", "sealed_test", "reset_defaults", "myaccount_test", "seo_test", "addresscheck_test", "publicmirror_test", "mapfit_test", "unitareas_test", "themes_test", "replinks_test", "reset_defaults", "mystations_test", "tar1090left_test", "sondecredit_test", "predictmethods_test", "usage_test", "adminsections_test", "wxalerts_test", "quakes_test", "rivers_test", "spaceweather_test", "cardfold_test", "satlayers_test", "achievements_test", "kiosk_test", "apikeys_test", "passkeys_test", "passkeysbrowser_test", "aprspush_test", "satpass_test", "satmap_test", "phonenew_test", "phonenew20_test", "sealmessages_test", "codeql_test", "sechard_test", "csp_test", "homecards_test", "mysidenavscripts_test", "radiolists_test", "satimages_test", "ais_test", "drones_test", "access_test", "reset_defaults", "chat_test", "friends_test", "peoplesearch_test", "mail_test", "bridge_test", "bridgeverify_test", "googleauth_test", "templates_test", "oidc_test", "matrix_test", "matrixchat_test", "matrixsync_test", "matrixbridge_test", "matrixmirror_test", "webpush_test", "alertspush_test", "pushmore_test", "pushkinds_test", "retention_test", "goes_test", "loops_test", "gfs_test", "traumaair_test", "satzoom_test", "mqttcert_test", "plugexport_test", "repqueue_test", "labelfit_test", "histmap_test", "maphost_test", "multiselect_test", "driveranges_test", "roads_test", "mapsearch_test", "mapfold_test", "mapfull_test", "balloonhistory_test", "totop_test", "logos_test", "mlatdots_test", "roadmap_test", "drivetimes_test", "aprslists_test", "adminservices_test", "admintabs_test", "balloonrange_test", "showonly_test", "mapextras_test", "wxsources_test", "repdigital_test", "weathersurface_test", "coveragearea_test", "siteflyout_test", "statspages_test", "cardfocus_test", "wxcombine_test", "countyoutline_test", "callsign_test", "dmrlists_test", "wxplace_test", "sondecoverage_test", "dmrpanel_test", "callsignuse_test", "grafanastats_test", "callsignall_test", "drivefold_test", "placecharts_test", "admincollapsed_test", "keepplace_test", "frequencies_test", "docs_test", "statsrender_test", "mylocation_test", "showmaptype_test", "stationlinks_test", "repposition_test", "traumamap_test", "tar1090more_test", "adminallow_test", "countylimit_test", "mydisplay_test", "phoneui_test", "callprefix_test", "wxstable_test", "sitesearch_test"):
    t0 = time.time()
    p = subprocess.run([sys.executable, name + ".py"], capture_output=True, text=True, timeout=600)
    subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"delete from feeders where uuid like 'test%' or uuid like 'pub%'\""], capture_output=True)
    out = p.stdout
    passed = len(re.findall(r"^PASS", out, re.M))
    failed = [l for l in out.splitlines() if l.startswith("FAIL")]
    ok = (p.returncode == 0) and not failed and ("ALL PASS" in out or "SMOKE PASS" in out)
    results[name] = (ok, passed, failed, p.stderr[-300:] if not ok else "")
    print(f"{'OK  ' if ok else 'FAIL'} {name}: {passed} checks passed in {time.time()-t0:.0f}s", ("" if ok else failed[:3] or results[name][3]))
print("ALL SUITES PASS" if all(v[0] for v in results.values()) else "SOME SUITES FAILED")
subprocess.run([sys.executable, "testsite_on.py"], capture_output=True, timeout=600)   # the suites left the test sites half switched off: switch every feature back on

