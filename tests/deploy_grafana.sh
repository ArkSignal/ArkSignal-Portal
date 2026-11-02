#!/bin/sh
# Rebuild the test box's Grafana and Prometheus from this working copy (deploy/grafana is pushed first), so the dashboards and the scrape jobs under deploy/grafana can be seen working before a release:
# container bk-grafana (published on 13000; it keeps the settings of the container it replaces) and bk-prometheus (its data volume kept), both on the test hub's network.
# The test hub's map is at the root of port 18080 (production's is under /map), so Grafana is told HUB_MAP_PATH is empty. Prometheus is also joined to the persistent portal's network and its copy of
# prometheus.yml scrapes adsb-portal-persist instead of adsb-portal, so the "portal" and "dump978" jobs read the :18431 site.
# Usage: sh tests/deploy_grafana.sh
cd "$(dirname "$0")/.." || exit 1
# the dashboards and scrape config come from this working copy, whatever deploy_test.sh last pushed
tar czf - -C deploy grafana | ssh -q nastemp "tar xzf - -C ~/adsb-portal/deploy" || exit 1
ssh -q nastemp "set -e; D=/usr/local/bin/docker; cd ~/adsb-portal
rm -rf /tmp/promctx; mkdir /tmp/promctx
sed 's/adsb-portal:8430/adsb-portal-persist:8430/' deploy/grafana/prometheus.yml > /tmp/promctx/prometheus.yml
cp deploy/grafana/Dockerfile.prometheus /tmp/promctx/Dockerfile
echo $TEST_SUDO_PASSWORD | sudo -S -p '' sh -c \"
\$D build -q -t adsb-prometheus:test /tmp/promctx >/dev/null
\$D rm -f bk-prometheus >/dev/null
\$D run -d --name bk-prometheus --restart unless-stopped --network adsb-test-hub_default --network-alias prometheus -v baked_prom-data:/prometheus adsb-prometheus:test --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/prometheus --storage.tsdb.retention.time=1095d >/dev/null
\$D network connect adsbpersist_internal bk-prometheus >/dev/null 2>&1 || true
\$D build -q -f deploy/grafana/Dockerfile.grafana -t adsb-grafana:test deploy/grafana >/dev/null
\$D inspect bk-grafana --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(GF_|HUB_PUBLIC_URL|MAP_)' > /tmp/gf.env || true
\$D rm -f bk-grafana >/dev/null
\$D run -d --name bk-grafana --restart unless-stopped --env-file /tmp/gf.env -e HUB_MAP_PATH= -p 13000:3000 --network adsb-test-hub_default --network-alias grafana adsb-grafana:test >/dev/null
for n in adsb-portal_internal adsbpersist_internal; do \$D network connect --alias grafana \$n bk-grafana >/dev/null 2>&1 || true; done
sleep 25
curl -s -m 8 http://localhost:13000/grafana/api/health | tr -d '\n '; echo
\""
