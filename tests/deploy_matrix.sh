#!/bin/sh
# Build the Matrix service from the tree already pushed by deploy_test.sh and (re)start it on the test box with a throwaway database:
# containers `matrix` (Synapse on port 18008, settings channel 8483), `matrix-db` and `element` (Element Web on port 18009), reachable from both portals as http://matrix:8483.
# Usage: sh tests/deploy_matrix.sh [fresh]
cd "$(dirname "$0")/.." || exit 1
ssh -q nastemp "set -e; cd ~/adsb-portal
docker build -q -f deploy/matrix/Dockerfile.matrix -t adsb-matrix:test . >/dev/null
docker build -q -f deploy/element/Dockerfile.element -t adsb-element:test . >/dev/null
docker network inspect matrix_test >/dev/null 2>&1 || docker network create matrix_test >/dev/null
if [ '$1' = fresh ]; then docker rm -f matrix matrix-db >/dev/null 2>&1 || true; docker volume rm -f matrix_test_db matrix_test_data >/dev/null 2>&1 || true; fi
docker rm -f matrix element >/dev/null 2>&1 || true
docker ps -a --format '{{.Names}}' | grep -qx matrix-db || docker run -d --name matrix-db --network matrix_test --restart unless-stopped -e POSTGRES_USER=matrix -e POSTGRES_PASSWORD=matrix -e POSTGRES_DB=matrix -e POSTGRES_INITDB_ARGS='--encoding=UTF-8 --lc-collate=C --lc-ctype=C' -v matrix_test_db:/var/lib/postgresql/data postgres:17-alpine >/dev/null
docker start matrix-db >/dev/null 2>&1 || true
sleep 4
mkdir -p /tmp/matrix_data_root
docker run -d --name matrix --network matrix_test --restart unless-stopped -p 18008:8008 -v matrix_test_data:/data -e MATRIX_DB_HOST=matrix-db -e MATRIX_DB_PASSWORD=matrix adsb-matrix:test >/dev/null
for n in adsb-portal_internal adsbpersist_internal; do docker network connect --alias matrix \$n matrix >/dev/null 2>&1 || true; done
docker run -d --name element --network matrix_test --restart unless-stopped -p 18009:8080 -e CONFIG_URL=http://adsb-portal:8430/matrix/element-config.json -e CONFIG_REFRESH=20 -e MATRIX_BASE_URL=http://test-box.invalid:18008 -e MATRIX_SERVER_NAME=matrix-test.local -e FRAME_ANCESTORS=\"'self' http://test-box.invalid:18430 http://test-box.invalid:18431 http://127.0.0.1:18430 http://localhost:18431\" adsb-element:test >/dev/null
for n in adsb-portal_internal adsbpersist_internal; do docker network connect \$n element >/dev/null 2>&1 || true; done
sleep 6; docker exec matrix python -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8483/status',timeout=3).read().decode())\"" 
