#!/bin/sh
# Build the ACARS service from the tree already pushed by deploy_test.sh and (re)start it on the test box with a throwaway database:
# containers `acars` (port 18481) and `acars-db`, reachable from both portals as http://acars:8481. Usage: sh tests/deploy_acars.sh [fresh]
cd "$(dirname "$0")/.." || exit 1
ssh -q nastemp "set -e; cd ~/adsb-portal
docker build -q -f deploy/acars/Dockerfile.acars -t adsb-acars:test . >/dev/null
docker network inspect acars_test >/dev/null 2>&1 || docker network create acars_test >/dev/null
if [ '$1' = fresh ]; then docker rm -f acars acars-db >/dev/null 2>&1 || true; docker volume rm -f acars_test_db >/dev/null 2>&1 || true; fi
docker rm -f acars >/dev/null 2>&1 || true
docker ps -a --format '{{.Names}}' | grep -qx acars-db || docker run -d --name acars-db --network acars_test --restart unless-stopped -e POSTGRES_USER=acars -e POSTGRES_PASSWORD=acars -e POSTGRES_DB=acars -v acars_test_db:/var/lib/postgresql/data postgres:17-alpine >/dev/null
docker start acars-db >/dev/null 2>&1 || true
docker run -d --name acars --network acars_test --restart unless-stopped -p 18481:8481 -e DATABASE_URL=postgresql://acars:acars@acars-db:5432/acars adsb-acars:test >/dev/null
for n in adsb-portal_internal adsbpersist_internal; do docker network connect --alias acars \$n acars >/dev/null 2>&1 || true; done
sleep 5; curl -s localhost:18481/health; echo"
