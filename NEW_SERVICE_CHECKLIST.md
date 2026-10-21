# Checklist: adding a service, a kind of station, or a map layer

Follow this every time something new is added (repeaters, mesh nodes, APRS or CWOP weather, ACARS, roads, anything a member can add or connect). It exists because new work kept
missing the shared pages. Marty's rule (2026-10-09): **a new item updates the existing pages, or is added to them dynamically from one registry. Never leave a page behind.**

Tick each line in the commit message or the tracker entry. `tests/services_test.py` fails when a registered service is missing from the pages it covers.

## 1. The registry (the one place; most shared pages read from it)
Add one entry to `SERVICES` in `app/services.py`. It gives, with no further page edits:
- [ ] `enabled` (a Settings switch), `label`, `permission`, `status_key`
- [ ] `about`: the line on the About page (What You Can Do Here Today)
- [ ] `feed`: the Feed menu entry (and build the `/feed/<service>` How to Feed page)
- [ ] `home_layers`: the layer(s) on the **home page map** (one legend switch each)
- [ ] `home_now`: the block in the **card beside the home map** (Weather Right Now, Radiosondes Right Now ...); it also refreshes by itself
- [ ] `admin_tiles`, `admin_attention`, `admin_data`: the **admin Overview** (numbers, things to look at, data kept)

## 2. Pages that are not (yet) read from the registry: check each by hand
- [ ] Main menu group in `app/navmenu.py`: **Overview first, the map second**, the group's own link goes to the overview; the My ... page for members last. Update `tests/navmenu_test.py`.
- [ ] Admin menu in `app/adminnav.py` (a group and a permission) and the admin page, with its permission on every route.
- [ ] Roles: the permission in `app/permissions.py` (and `IMPLIED_BY` if a broader permission should include it); it shows on the Roles page by itself. Update `tests/rolesplit_test.py`.
- [ ] Service Status: a check in `app/status.py` (`status_key` above).
- [ ] About page: the paragraph under "Where It Is Going", and credits for any outside source in the open-source table.
- [ ] Roadmap: the Request log in `ROADMAP.md`, `app/roadmap.json` and the `next` entry of `app/release_notes.json`; `PROJECT_TRACKER.md`.
- [ ] Search (`/search`), the sign-in landing choices (`app/landing.py`) and the My ... sub-menu (`partials/_my_subnav.html`) when members use it.
- [ ] **Backups and restore** (STANDING RULE, Marty 2026-10-09: keep them up to date whenever a feature is added or changed): every new table is in a restore area in `app/restorepart.py` `AREAS` (or in `TRANSIENT` with a reason), a new thing members can pick one of (a station, a rule) is in `RECORD_TABLES`, and files kept outside the database (uploads, keys, certificates, other containers' data) are in the backup too. `tests/backupcover_test.py` fails for a table in no area; run the backup and restore tests (`backup_test`, `restorepart_test`) after any change to the data. Data Updates jobs when the service keeps data.

## 3. Everything a member can add or connect (STANDING RULE)
- [ ] A **Manage** button on the admin list that opens the member's own page with a banner.
- [ ] An **add or assign for a member** form with every option the member has, and the live member search (`static/memberpicker.js`).
- [ ] **Audit logging** for create, change, delete and admin actions.
- [ ] Location choices come from `app/privacy.py`.

## 4. Maps (every map page uses `partials/_mapshell.html`, `static/mapshell.css`, `static/maplayers.js`)
- [ ] The **aircraft map** layer (the Layers list) and its legend entry.
- [ ] The **Coverage Map** layer.
- [ ] The **county map** overlay: `counties.OVERLAYS`, `METRICS`, the county index kinds and the county page, with the **same icons** (hospital squares, airport dots, `static/sondeicons.js`) and the **same click popups** as the other maps.
- [ ] The **home page map** (registry `home_layers`).
- [ ] Stations that can share a place use `Colocate.track(map, kind, marker, lat, lon)` (`static/colocate.js`).
- [ ] Panels start closed (Settings > Map Tiles and Weather > Panels When a Map Opens); "Icon Size" not "Dot Size".

## 5. Settings, terms, style
- [ ] **Units:** every measurement (altitude, speed, distance, temperature) goes through `static/arkunits.js` or the `u_*` filters in `app/units.py`, never a fixed unit; imperial is the default and each person's choice (Display > Units) wins. Admin distance settings use the `dist` kind.
- [ ] Every tunable is in Admin > Settings (`settings.DEFAULTS` + `setting_specs.SPECS` + a config test). Nothing hard-coded.
- [ ] Outside source: read its terms and record them in `DATA_SOURCES_TERMS.md` **before** building; credit it on the About page.
- [ ] US spelling, Title Case headings and buttons, "Feeders" for the feeding stations, the current look (the UI style guide in the photo-automation project, and the memory notes).
- [ ] Targeted tests of what changed (`sh tests/targeted.sh ...`); add the new suite to `tests/run_all.py`.

## 6. Scale (STANDING RULE, Marty 2026-10-09)
- [ ] Heavy or long-running work (imports, models, predictions, big queries) is a **separate service container** with an HTTP API and its own database URL, so it can move to another host. The portal reaches it through a Settings address and keeps working (with a note) if it is away.
- [ ] A new image is added to the release workflows, the bundled compose files, the stack updater and Watchtower labels, and the checks on Docker Hub (see HANDOFF7 and HANDOFF9).

## 4. How to Feed pages (STANDING RULE, Marty 2026-10-17)
Anything members can feed from their own equipment (a receiver, a station, a script) gets a **How to Feed page** in the same change: `/feed/<service>` built like `feed_acars.html` (the short version, how it works, what you need, hardware, setup with a copy-paste script or container, how to test, after you are feeding, ports, troubleshooting, privacy, more help), its entry in the registry's `feed` hook (that adds it to the Feed menu and, because `other_feeds()` reads the Feed menu, to every other feeding page), a section on Am I Feeding? (`feed_check.html`), a link from the service's own pages and My page, a Service Status check, and tests that fetch the page and each of those links. A service that members cannot feed (nets and Winlink gateways, API keys) needs the registry entry and About line but no feeding page.
