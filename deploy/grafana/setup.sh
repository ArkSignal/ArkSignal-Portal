#!/bin/sh
# Runs inside the Grafana image at every start (see entrypoint.sh): waits for Grafana, then imports the dashboards.
# The Ultrafeeder dashboard uses Grafana library panels, which only the import API can create (file provisioning cannot).
# Every step says in the log (docker logs grafana) what happened, including the HTTP status when Grafana refuses.
G="http://127.0.0.1:3000/grafana"
AUTH="admin:${GF_SECURITY_ADMIN_PASSWORD}"

echo "[dashboards] waiting for Grafana..."
tries=0
until curl -fsS "$G/api/health" >/dev/null 2>&1; do
  tries=$((tries + 1))
  [ "$tries" -gt 100 ] && { echo "[dashboards] Grafana did not start"; exit 1; }
  sleep 3
done

# post <file> <api path> <what>: sends a dashboard and reports the outcome
post() {
  code=$(curl -sS -o /tmp/reply.txt -w '%{http_code}' -u "$AUTH" -H 'Content-Type: application/json' -X POST "$G$2" -d @"$1")
  if [ "$code" = "200" ]; then echo "[dashboards] $3: imported"; return 0; fi
  echo "[dashboards] $3: FAILED with HTTP $code: $(head -c 300 /tmp/reply.txt)"
  { [ "$code" = "401" ] || [ "$code" = "403" ]; } && echo "[dashboards] Grafana refused the admin login: the password in /opt/adsb/grafana/.env (GRAFANA_ADMIN_PASSWORD) is probably not the one Grafana has (Grafana only reads it when it first creates its database). Reset it with: docker exec grafana grafana cli admin reset-admin-password <the password from .env>"
  return 1
}

# The portal's own feeder statistics dashboard needs no editing; (re)import it every start so changes in a new release arrive.
if [ -f /dash/feeders-dashboard.json ]; then
  { printf '{"dashboard":'; cat /dash/feeders-dashboard.json; printf ',"overwrite":true}'; } > /tmp/feeders.json
  post /tmp/feeders.json /api/dashboards/db "Aircraft Feeder Statistics dashboard"
fi

# The weather and balloon dashboards (the portal's own metrics) are replaced at every start too.
for d in weather:Weather_Dashboard sondes:Radiosonde_Dashboard; do
  f="/dash/${d%%:*}-dashboard.json"
  if [ -f "$f" ]; then
    { printf '{"dashboard":'; cat "$f"; printf ',"overwrite":true}'; } > /tmp/extra.json
    post /tmp/extra.json /api/dashboards/db "$(echo "${d##*:}" | tr _ " ") dashboard"
  fi
done

# The Ultrafeeder dashboard is imported when it is missing, when FORCE_IMPORT=1 is set in .env, or when this image carries a newer
# dashboard revision (the number in /dash/REV) than the one last imported here (remembered in the Grafana data folder). A new
# revision replaces the dashboard, so edits made to it inside Grafana are lost; the revision only changes when the dashboard itself does.
REV=$(cat /dash/REV 2>/dev/null || echo 0)
MARK=/var/lib/grafana/.adsb-dashboard-rev
HAVE=$(cat "$MARK" 2>/dev/null || echo none)
if [ "${FORCE_IMPORT:-}" != "1" ] && [ "$HAVE" = "$REV" ] && curl -fsS -u "$AUTH" "$G/api/dashboards/uid/ultrafeeder" >/dev/null 2>&1; then
  echo "[dashboards] Ultrafeeder Stats dashboard is up to date (revision $REV); set FORCE_IMPORT=1 in .env to replace it"
  exit 0
fi

# Point the dashboard's heatmap and graphs panels at the hub's public address, and open the heatmap on the network's area
# (the original centres on "the receiver", which a hub with no antenna of its own does not have, so it opened mid-ocean).
sed -e "s#http://feeder_url/graphs1090#${HUB_PUBLIC_URL}/graphs1090/#g" \
    -e "s#http://feeder_url?heatmap#${HUB_PUBLIC_URL}/map/?heatmap#g" \
    -e "s#centerReceiver&zoom=7#lat=${MAP_LAT:-34.80}\&lon=${MAP_LON:--92.30}\&zoom=${MAP_ZOOM:-7}#g" \
    /dash/ultrafeeder-dashboard.json > /tmp/dash.json

{
  printf '{"dashboard":'
  cat /tmp/dash.json
  printf ',"overwrite":true,"inputs":[{"name":"DS_PROMETHEUS","type":"datasource","pluginId":"prometheus","value":"ultrafeeder-prom"}]}'
} > /tmp/payload.json

post /tmp/payload.json /api/dashboards/import "Ultrafeeder Stats dashboard (revision $REV)" && echo "$REV" > "$MARK"
