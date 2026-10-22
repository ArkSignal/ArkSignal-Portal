#!/bin/sh
# One-time switch of the hub to automatic updates. Run on the hub VM as the user that runs docker (localadmin):
#   docker run --rm --pull always ghcr.io/arksignal/arksignal-portal:stable cat /app/deploy/switch-to-auto-updates.sh | sh
# What it does: replaces the portal's and Grafana's compose files with the current ones (the old ones are kept as compose.yaml.bak-<time>),
# adds a random WATCHTOWER_TOKEN to the portal's .env, and starts everything including Watchtower. Data folders are not touched.
# Afterwards nothing needs copying by hand: Watchtower installs new stable releases every night at 4 a.m., or press Update Now on the
# portal's Admin > Updates page. Safe to run again.
set -eu
IMG="${IMAGE:-ghcr.io/arksignal/arksignal-portal:stable}"
P="${PORTAL_DIR:-/opt/adsb/portal}"
G="${GRAFANA_DIR:-/opt/adsb/grafana}"
ts=$(date +%Y%m%d-%H%M%S)

[ -f "$P/compose.yaml" ] && [ -f "$P/.env" ] || { echo "Cannot find $P/compose.yaml and $P/.env. Is this the hub VM?"; exit 1; }
[ -n "${IMAGE:-}" ] || docker pull "$IMG" >/dev/null   # IMAGE is set only when rehearsing with a locally built image
docker network inspect proxy >/dev/null 2>&1 || { echo "The docker network 'proxy' does not exist. Is this the hub VM?"; exit 1; }

swap() {   # swap <dir> <path inside the image>
  cp "$1/compose.yaml" "$1/compose.yaml.bak-$ts"
  docker run --rm "$IMG" cat "$2" > "$1/compose.yaml.new"
  [ -s "$1/compose.yaml.new" ] || { echo "Could not read $2 from the image"; rm -f "$1/compose.yaml.new"; exit 1; }
  mv "$1/compose.yaml.new" "$1/compose.yaml"
  echo "Updated $1/compose.yaml (old one saved as compose.yaml.bak-$ts)"
}

echo "== Portal and update service"
swap "$P" /app/deploy/compose.yaml
grep -q '^WATCHTOWER_TOKEN=' "$P/.env" || { echo "WATCHTOWER_TOKEN=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> "$P/.env"; echo "Added WATCHTOWER_TOKEN to $P/.env"; }
( cd "$P" && docker compose pull && docker compose up -d --remove-orphans )

if [ -f "$G/compose.yaml" ] && [ -f "$G/.env" ]; then
  echo "== Grafana and Prometheus"
  swap "$G" /app/deploy/grafana/compose.yaml
  ( cd "$G" && docker compose pull && docker compose up -d --remove-orphans )
else
  echo "== No Grafana folder at $G; skipped."
fi

echo "== Stack updater (keeps every hub service in step with the compose files in its image; new services appear by themselves)"
S="${STACK_DIR:-/opt/adsb/stack}"
mkdir -p "$S"
[ -f "$S/compose.yaml" ] && cp "$S/compose.yaml" "$S/compose.yaml.bak-$ts"
docker run --rm "$IMG" cat /app/deploy/stack/compose.yaml > "$S/compose.yaml.new" && [ -s "$S/compose.yaml.new" ] && mv "$S/compose.yaml.new" "$S/compose.yaml" || { echo "Could not read the stack compose file from the image"; exit 1; }
if ! grep -q '^STACK_TOKEN=' "$S/.env" 2>/dev/null; then
  tok=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' 
')
  echo "STACK_TOKEN=$tok" >> "$S/.env"
  grep -q '^STACK_TOKEN=' "$P/.env" || echo "STACK_TOKEN=$tok" >> "$P/.env"
fi
( cd "$S" && docker compose pull && docker compose up -d )
( cd "$P" && docker compose up -d )   # picks up STACK_TOKEN so Update Now can reach the stack updater

echo "== Done. Running containers:"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' | grep -E 'NAMES|adsb-portal|adsb-stack|watchtower|grafana|prometheus'
echo "Sign in as an administrator and open Admin > Updates: Update Now should be active there."
