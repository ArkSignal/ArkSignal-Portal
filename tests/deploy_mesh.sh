#!/bin/sh
# Build the mesh service and its broker from the tree already pushed by deploy_test.sh and (re)start them on the test box with a throwaway database:
# containers `mesh` (port 18482), `mesh-db` and `mesh-mqtt` (port 18883 for plain MQTT inside the box test, 18884 for TLS when a certificate is present), reachable from both portals as http://mesh:8482.
# Usage: sh tests/deploy_mesh.sh [fresh]
cd "$(dirname "$0")/.." || exit 1
ssh -q nastemp "set -e; cd ~/adsb-portal
docker build -q -f deploy/mesh/Dockerfile.mesh -t adsb-mesh:test . >/dev/null
docker build -q -f deploy/mesh/Dockerfile.mesh-mqtt -t adsb-mesh-mqtt:test . >/dev/null
docker build -q -f deploy/mesh/Dockerfile.mesh-wg -t adsb-mesh-wg:test . >/dev/null
docker network inspect mesh_test >/dev/null 2>&1 || docker network create mesh_test >/dev/null
if [ '$1' = fresh ]; then docker rm -f mesh mesh-db mesh-mqtt mesh-wg >/dev/null 2>&1 || true; docker volume rm -f mesh_test_db mesh_test_mqtt mesh_test_wg >/dev/null 2>&1 || true; fi
docker rm -f mesh mesh-mqtt mesh-wg >/dev/null 2>&1 || true
docker run -d --name mesh-mqtt --network mesh_test --restart unless-stopped -p 18883:1883 -p 18884:8883 -v mesh_test_mqtt:/mosquitto/config adsb-mesh-mqtt:test >/dev/null
docker run -d --name mesh-wg --network mesh_test --restart unless-stopped --cap-add NET_ADMIN -p 18851:51820/udp -v mesh_test_wg:/wg adsb-mesh-wg:test >/dev/null
docker ps -a --format '{{.Names}}' | grep -qx mesh-db || docker run -d --name mesh-db --network mesh_test --restart unless-stopped -e POSTGRES_USER=mesh -e POSTGRES_PASSWORD=mesh -e POSTGRES_DB=mesh -v mesh_test_db:/var/lib/postgresql/data postgres:17-alpine >/dev/null
docker start mesh-db >/dev/null 2>&1 || true
sleep 3
docker run -d --name mesh --network mesh_test --restart unless-stopped -p 18482:8482 -v mesh_test_mqtt:/broker:ro -v mesh_test_wg:/wg -e DATABASE_URL=postgresql://mesh:mesh@mesh-db:5432/mesh -e MESH_HOST=mesh-mqtt -e MESH_UP_ENABLED=0 adsb-mesh:test >/dev/null
for n in adsb-portal_internal adsbpersist_internal; do docker network connect --alias mesh \$n mesh >/dev/null 2>&1 || true; done
sleep 8; curl -s localhost:18482/health; echo"
