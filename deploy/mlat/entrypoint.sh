#!/bin/sh
# Settings come from environment variables (set in the compose file, so every change ships as an image + compose release).
set -e
exec python3 /opt/mlat-server/mlat-server \
  --work-dir /work \
  --client-listen "0.0.0.0:${MLAT_CLIENT_PORT:-31090}" \
  --basestation-listen "0.0.0.0:${MLAT_RESULTS_PORT:-30105}" \
  --motd "${MLAT_MOTD:-Arkansas ADS-B Network MLAT}" \
  --status-interval "${MLAT_STATUS_INTERVAL:-60}" \
  ${MLAT_EXTRA_ARGS:-}
