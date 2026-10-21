#!/bin/sh
# Build the prediction service from the tree already pushed by deploy_test.sh and (re)start it on the test box with a throwaway database:
# containers `predict` (port 18470) and `predict-db`, reachable from both portals as http://predict:8470. Usage: sh tests/deploy_predict.sh [fresh]
cd "$(dirname "$0")/.." || exit 1
ssh -q nastemp "set -e; cd ~/adsb-portal
docker build -q -f deploy/predict/Dockerfile.predict -t adsb-predict:test . >/dev/null
docker network inspect predict_test >/dev/null 2>&1 || docker network create predict_test >/dev/null
if [ '$1' = fresh ]; then docker rm -f predict predict-db >/dev/null 2>&1 || true; docker volume rm -f predict_test_db >/dev/null 2>&1 || true; fi
docker rm -f predict >/dev/null 2>&1 || true
docker ps -a --format '{{.Names}}' | grep -qx predict-db || docker run -d --name predict-db --network predict_test --restart unless-stopped -e POSTGRES_USER=predict -e POSTGRES_PASSWORD=predict -e POSTGRES_DB=predict -v predict_test_db:/var/lib/postgresql/data postgres:17-alpine >/dev/null
docker start predict-db >/dev/null 2>&1 || true
docker run -d --name predict --network predict_test --restart unless-stopped -p 18470:8470 -e DATABASE_URL=postgresql://predict:predict@predict-db:5432/predict adsb-predict:test >/dev/null
for n in adsb-portal_internal adsbpersist_internal; do docker network connect --alias predict \$n predict >/dev/null 2>&1 || true; done
sleep 4; curl -s localhost:18470/health; echo"
