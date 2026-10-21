#!/bin/sh
# Build the APRS service from the tree already pushed by deploy_test.sh and (re)start it on the test box with a throwaway database:
# containers `aprs` (port 18480) and `aprs-db`, reachable from both portals as http://aprs:8480. Usage: sh tests/deploy_aprs.sh [fresh]
cd "$(dirname "$0")/.." || exit 1
ssh -q nastemp "set -e; cd ~/adsb-portal
docker build -q -f deploy/aprs/Dockerfile.aprs -t adsb-aprs:test . >/dev/null
docker network inspect aprs_test >/dev/null 2>&1 || docker network create aprs_test >/dev/null
if [ '$1' = fresh ]; then docker rm -f aprs aprs-db >/dev/null 2>&1 || true; docker volume rm -f aprs_test_db >/dev/null 2>&1 || true; fi
docker rm -f aprs >/dev/null 2>&1 || true
docker ps -a --format '{{.Names}}' | grep -qx aprs-db || docker run -d --name aprs-db --network aprs_test --restart unless-stopped -e POSTGRES_USER=aprs -e POSTGRES_PASSWORD=aprs -e POSTGRES_DB=aprs -v aprs_test_db:/var/lib/postgresql/data postgres:17-alpine >/dev/null
docker start aprs-db >/dev/null 2>&1 || true
docker run -d --name aprs --network aprs_test --restart unless-stopped -p 18480:8480 -e DATABASE_URL=postgresql://aprs:aprs@aprs-db:5432/aprs adsb-aprs:test >/dev/null
for n in adsb-portal_internal adsbpersist_internal; do docker network connect --alias aprs \$n aprs >/dev/null 2>&1 || true; done
sleep 5; curl -s localhost:18480/health; echo"
