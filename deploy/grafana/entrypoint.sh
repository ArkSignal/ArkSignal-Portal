#!/bin/sh
# Start Grafana as usual, and import the dashboards once it answers. Runs again on every (re)start, which is what makes a
# new image bring its new dashboards.
/run.sh "$@" &
grafana_pid=$!
trap 'kill -TERM "$grafana_pid" 2>/dev/null' TERM INT
( /adsb-setup.sh || echo "dashboard setup did not finish (see above); Grafana itself keeps running" ) &
wait "$grafana_pid"
status=$?
wait "$grafana_pid" 2>/dev/null
exit "$status"
