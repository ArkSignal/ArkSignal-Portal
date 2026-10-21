# tar1090 parity checklist (audit 2026-10-06)

Marty: "all features of the tar1090 map need to be added". This list was built from the running tar1090 on the test hub (version 3.14.1823: its index.html,
script, layers and defaults files, at http://test-box.invalid:18080) compared with our live map (`app/templates/live.html`). Same rule as always: our own look
and code, tar1090's behaviour and data formats. **Done** = we have it. **Open** = to build, in the order at the bottom.

## Filters and search
| tar1090 | Us |
|---|---|
| Filter by altitude (min/max) | Done |
| Filter by source (ADS-B, MLAT, TIS-B, UAT/ADS-R, Mode S, other) | **Done 2026-10-06** with live counts (readsb reports UAT as ADS-R, so they are one entry) |
| Military only (key U) | Done |
| Hide on ground / with position only / only in view (key V: table in view) | Done |
| Filter by DB flags (military, interesting, PIA, LADD) | **Done 2026-10-06** (Aircraft Flags in the filter card) |
| TIS-B filter toggle (key Shift-T), no-GPS-only toggle (key G: aircraft with no position) | TIS-B covered by the Data Source filter; **no-position-only Done 2026-10-14** (Without position only, key G) |
| Search by callsign, registration, type, hex; comma-separated list, wildcard `*`, `!` exclude | **Done 2026-10-06** (also searches squawk and model) |
| Search by squawk | **Done** (country/flag Open) |
| Filters kept in the URL (Include Filters In URLs) | **Done** (Copy Link to This View) |
| Jump to airport or latitude,longitude (and key J: jump to hex) | **Done**: type a coordinate pair, a hex or an airport code in the search box and press Enter |

## Map behaviour and toolbar
| tar1090 | Us |
|---|---|
| Follow selected (key F) | Done |
| Multi-select several aircraft (key M), select all (key T), deselect (Esc) | **Done 2026-10-14** (each with its track; `icao=a,b,c` in the link) |
| Isolation: hide others when one is selected (key I) | Done (Hide Others) |
| Persistence mode: leave positions of aircraft that disappeared (key P) | **Done 2026-10-14** (dimmed for `live_persist_minutes`) |
| Reset map to default view (key H) | **Done** |
| Hide the buttons (Shift-H) | **Done** (hides the panels) |
| Zoom/pan keys (Q E + -, arrows), refetch (R) | **Done 2026-10-06** with a Keyboard shortcuts help (? key and a link in the filter card) |
| Dim map (key B), Dark mode | Dim **Done**; dark mode n/a (the site is dark) |
| Distance (range) rings (Distance Circles), site marker, update GPS location | Partly (range rings and site marker; not the GPS location update) |
| Auto-select plane | **Done 2026-10-14** (Select an aircraft by itself, key A) |
| Last leg only, track labels (K), debug tracks | **Done 2026-10-15**: Last Flight Only / All Flights button on the card (and Settings > Home Page and Live Map > Trail: Last Leg Only); track labels (K) were done; debug tracks are a developer tool and are not planned |
| Colored planes / colored trails by altitude | Done |
| WebGL rendering switch | n/a (Leaflet canvas; revisit if performance needs it) |
| Altitude chart (altitude over time of the selected aircraft) | **Done** (card, from the recorded track, unit-aware) |
| Export KML of the selected aircraft's track | **Done** (Export KML button on the card) |

## Labels and units
| tar1090 | Us |
|---|---|
| Labels: callsign, alt, speed, type (L), extended labels (O), track labels (K) | Done (own label designer, more fields; keys L, O, K) |
| Labels: geometric altitude (WGS84), label units, UTC time labels, slim wind labels | Geometric altitude **done** (label field); units are always shown; **UTC times Done 2026-10-14** (Display menu: Times in UTC); **slim wind Done 2026-10-15** (label field Wind: direction and speed) |
| Altitude corrected for QNH (and Altimeter: Set / Standard / From Plane) | **Done 2026-10-14** (Display: Altitude, with the aircraft's own QNH or one you type, below 18,000 ft) |
| Geometric altitude with EGM conversion | **Done 2026-10-15**: card row and label field "Geometric altitude, sea level (EGM96)" (the same egm96-universal library tar1090 uses, `static/egm96.js`, loaded the first time it is needed) |
| Units: Aeronautical (ft, kt, nm), Metric, Imperial | **Done 2026-10-06**: per-visitor choice in the filter card, site default in Settings > Home Page and Live Map, applied to list, labels, card, trail popups and wind |
| Text and icon size multipliers | Done (icon size, label size) |
| Pictures from planespotters.net / planespotting.be, route lookup | Done (own lookup service) |
| Infoblock (the details panel) on/off, wide infoblock, mouse-over block | **Done 2026-10-06** (card, Wide/Narrow button, hover block with a setting) |
| Reset all settings | **Done** (Reset My Map Settings) |

## Table / list
| tar1090 | Us |
|---|---|
| Choose columns | Done |
| Sort by any column | Done |
| More / even more / all table lines | **Done 2026-10-15**: the list shows Settings > Aircraft List: Rows Shown rows (300) and a row at its end offers Show More and Show All |
| Table in view only (V) | Done (Only aircraft in view) |

## History, replay, heatmap
| tar1090 | Us |
|---|---|
| Replay a past day with play controls (Y), speeds 1x-40x | **Done 2026-10-14**: the portal's own Replay on the live map (static/replay.js; the hub's heatmap files, 1x to 100x, date and time in the site's zone, shareable link); the classic one stays under Classic Map |
| Heatmap | **Done 2026-10-14**: the portal's own heat map on the live map (static/heatlayer.js; server adds up the hub's heatmap files on a grid that follows the zoom), any period, altitude limits, strength and spread |
| Click a trace to play it back; per-aircraft history trace for a past day | Partly (Flight Progress and History page) |

## Layers (map sources and overlays)
| tar1090 | Us |
|---|---|
| Base maps, aeronautical charts, weather overlays (NEXRAD, satellite...), airspace/outline overlays | Done (admin-managed catalog, weather panel) |
| Aircraft outlines of the receivers (range outline) | Open (our Station coverage layer is per station; a network union outline is Open) |
| Planefence-style/ADS-B Exchange-style extras | Not part of tar1090 |

## URL parameters
`icao`, `lat`, `lon`, `zoom`, filters, `hideSidebar`, `hideButtons`, `units`: **Done 2026-10-06** for the live map (`/live?lat=&lon=&zoom=&icao=&q=&mil=1&altMin=&altMax=&srcOff=&flags=&units=`), and a Copy Link to This View button writes them. `heatmap`, `replay`, `showTrace` belong to the embedded tar1090 tools until native replay and heatmap exist.

## Build order (proposal)
1. ~~Units~~ done. 2. ~~Keyboard shortcuts~~ done (M multi-select, T select all, P persistence, J jump, G no-GPS, Y replay not yet). 3. ~~Search syntax~~ done; DB-flags filter and no-GPS toggle open. 4. Multi-select, select all, persistence mode, reset view, jump to airport/coordinates/hex.
5. URL parameters and filters-in-URL. 6. Altitude chart, KML export, QNH/geometric altitude options and label options. 7. Dim map, wide info card, hover block, reset all settings.
8. Native replay and heatmap (roadmap section 3).
