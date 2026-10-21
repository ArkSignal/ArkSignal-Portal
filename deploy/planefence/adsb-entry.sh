#!/bin/bash
# First start only: create planefence.config from the upstream template with this network's values from the environment. After that the file lives in the
# persistent folder and is changed through Planefence's own configuration page (Admin > Planefence in the portal), never by hand.
P=/usr/share/planefence/persist
T=/usr/share/planefence/stage/persist/planefence.config.RENAME-and-EDIT-me
mkdir -p "$P"
if [ ! -f "$P/planefence.config" ] && [ -f "$T" ]; then
  cp "$T" "$P/planefence.config"
  setkey() { # setkey KEY VALUE  (VALUE is written verbatim after =)
    local k="$1" v="$2"
    v=${v//\/\\}; v=${v//&/\&}; v=${v//|/\|}
    if grep -q "^$k=" "$P/planefence.config"; then sed -i "s|^$k=.*|$k=$v|" "$P/planefence.config"; else echo "$k=$v" >> "$P/planefence.config"; fi
  }
  setkey FEEDER_LAT "${FEEDER_LAT:-34.80}"
  setkey FEEDER_LON "${FEEDER_LON:--92.30}"
  setkey PF_SOCK30003HOST "${PF_SOCK30003HOST:-hub}"
  setkey PF_SOCK30003PORT "${PF_SOCK30003PORT:-30003}"
  setkey PF_MAXDIST "${PF_MAXDIST:-160}"
  setkey PF_MAXALT "${PF_MAXALT:-5000}"
  setkey PF_DISTUNIT "${PF_DISTUNIT:-nauticalmile}"
  NAME="${PF_NAME:-Arkansas ADS-B}"
  MOTD1="${PF_MOTD:-<b>Aircraft that flew over Arkansas, as heard by the stations of the network.</b>}"
  MOTD2="${PA_MOTD:-<b>Interesting aircraft over Arkansas.</b>}"
  setkey PF_NAME "\"$NAME\""
  setkey PF_MOTD "\"$MOTD1\""
  setkey PA_MOTD "\"$MOTD2\""
  [ -n "$PF_TRACKSERVICE" ] && setkey PF_TRACKSERVICE "$PF_TRACKSERVICE" && setkey PA_TRACKSERVICE "$PF_TRACKSERVICE"
  setkey PF_FUDGELOC "${PF_FUDGELOC:-0}"
  # the alert list comes from the portal's Interesting Aircraft list (Admin > Interesting Aircraft), one list kept in one place
  setkey PF_ALERTLIST "${PF_ALERTLIST:-http://adsb-portal:8430/api/plane-alert-list.csv}"
  echo "adsb-entry: wrote first-run planefence.config (hub ${PF_SOCK30003HOST:-hub}, ${PF_MAXDIST:-160} nm around ${FEEDER_LAT:-34.80},${FEEDER_LON:--92.30})"
fi
# One-time tuning, approved by Marty (2026-10-09): the statewide fence kept every aircraft at any altitude (48 pages a day). It now keeps low-flying aircraft, up to 5,000 ft, which is
# what a fence is for. Only a config still at the old built-in 100000 is changed, and only once (the marker file); after that the value is Marty's, on Planefence's own configuration page.
if [ -f "$P/planefence.config" ] && [ ! -f "$P/.tuned-maxalt-5000" ]; then
  if grep -q '^PF_MAXALT=100000$' "$P/planefence.config"; then
    sed -i 's|^PF_MAXALT=.*|PF_MAXALT=5000|' "$P/planefence.config"
    echo "adsb-entry: PF_MAXALT lowered from 100000 to 5000 ft (one time)"
  fi
  touch "$P/.tuned-maxalt-5000"
fi
exec /init "$@"
