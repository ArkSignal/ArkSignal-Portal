# Grafana and Prometheus for the hub

Statistics graphs for the hub, in the same style as the local stations. Prometheus stores the hub's metrics for
three years; Grafana shows the community "Ultrafeeder Stats" dashboard (grafana.com id 18398, by the SDR
Enthusiasts project). The dashboard is imported automatically on first start.

Verified on the test hub: Prometheus scrapes `hub:9273` and `hub:9274` (both up), the dashboard finds data under
the job name `ultrafeeder`, the heatmap and graphs panels are pointed at the hub's public address, anonymous
visitors can view but not edit, and Grafana can be embedded in the portal.

## Prerequisites on the hub

The production hub compose already has `PROMETHEUS_ENABLE=true` (image `:telegraf`) and
`TAR1090_ENABLE_AC_DB=true`. The metrics ports 9273 and 9274 stay inside Docker; nothing is published and no
firewall change is needed. Check from the VM:

```bash
docker exec hub sh -c 'wget -qO- http://127.0.0.1:9274/metrics | head -3'
```

## Install on the hub VM

1. Create the folders (Prometheus runs as user 65534, Grafana as 472):

   ```bash
   sudo mkdir -p /opt/adsb/grafana/prometheus-data /opt/adsb/grafana/grafana-data
   sudo chown 65534:65534 /opt/adsb/grafana/prometheus-data
   sudo chown 472:472 /opt/adsb/grafana/grafana-data
   sudo chown localadmin:localadmin /opt/adsb/grafana
   ```

2. Copy these files from `deploy/grafana/` in the repository into `/opt/adsb/grafana/` (keep the layout):
   `compose.yaml`, `prometheus.yml`, `setup.sh`, `ultrafeeder-dashboard.json` and the `provisioning/` folder.

3. Create `/opt/adsb/grafana/.env` (letters and numbers only for the password):

   ```bash
   cat > /opt/adsb/grafana/.env <<'EOF'
   GRAFANA_ADMIN_PASSWORD=choose-a-long-alphanumeric-password
   HUB_PUBLIC_URL=https://arksignal.net
   EOF
   chmod 600 /opt/adsb/grafana/.env
   ```

4. (Optional) add `MAP_LAT`, `MAP_LON` and `MAP_ZOOM` to `.env` to change where the dashboard's heatmap opens (default 34.80, -92.30, zoom 7).
   To re-import the Ultrafeeder dashboard after changing them, add `FORCE_IMPORT=1`, run `docker compose up -d`, then remove that line again.

   Start it and confirm the dashboard was imported:

   ```bash
   cd /opt/adsb/grafana
   docker compose up -d
   docker logs grafana-setup
   ```

   The last line of the setup log should be `dashboard imported` (or `dashboard already present` on later starts).

5. Nginx Proxy Manager: on the proxy host `arksignal.net` add a custom location `/grafana/`, scheme `http`,
   Forward Hostname `grafana`, Forward Port `3000`, **Websockets Support on**, and do not strip the prefix.

6. The portal already has a Tools entry named Statistics Dashboard (`/view/statistics`) that shows the Ultrafeeder Stats dashboard
   at `/grafana/d/ultrafeeder/ultrafeeder-stats?orgId=1&theme=dark&kiosk`; it works as soon as the Grafana location above exists.
   A second dashboard, **Network Feeders** (`/grafana/d/feeders/network-feeders`), is imported automatically. It charts the portal's
   own feeder statistics (station IDs, online, message and position rates, uptime; no locations) from `adsb-portal:8430/metrics`,
   which Prometheus scrapes as the `portal` job. The portal and Prometheus must share the `proxy` network (they do in the standard
   setup). Portal-only data such as exact locations is never put into Grafana.

Sign in to Grafana at `https://arksignal.net/grafana/login` as `admin` with the password from `.env` to
edit dashboards. Everyone else sees them read-only without signing in.

## Notes

- The panels about UAT 978 show no data because the hub has no UAT receiver. Collapse that row if you like.
- Grafana 13 no longer loads the old Angular panel plugins that the original instructions install; the only panel
  that used them is the UAT polar plot, so no plugins are installed.
- Prometheus keeps 1095 days (`--storage.tsdb.retention.time=1095d`), matching the hub's history policy.
- To change the public address later, edit `.env` and the `HUB_PUBLIC_URL` replacement only affects a dashboard that
  has not been imported yet; for an existing one edit the two iframe panel URLs in Grafana.
- Updating: `docker compose pull && docker compose up -d` in `/opt/adsb/grafana`.

## Range panels and the Ultrafeeder dashboard

The hub has no antenna of its own, so the Ultrafeeder dashboard's SDR Gain, SDR PPM, Max-Min Range and Current Maximum Range panels were removed. The
Network Feeders dashboard instead charts each station's measured range (the portal measures it from the station locations; no locations
are exposed): Network Maximum Range, Maximum Range by Station (30 days) and Farthest Range Today by Station.

To pick up dashboard changes after a new release, copy the new files to `/opt/adsb/grafana/` and run
`docker compose up -d --force-recreate grafana-setup`. The Network Feeders dashboard is replaced on every run; the Ultrafeeder dashboard is
replaced only when `FORCE_IMPORT=1` is set in `.env` (remove it again afterwards), because you may have customised it.
