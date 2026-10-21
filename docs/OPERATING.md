# Arkansas ADS-B Network: Feeder Portal

A web portal for the Arkansas ADS-B hub: accounts and roles, feeder claiming with station IDs, per-feeder
history and status, a public feeders list with rankings and a privacy-aware map, airport information, a How to
Feed guide, and an admin area for users, roles, settings, SMTP, backups and history.

Stack: Flask, PostgreSQL 17, gunicorn, plain JavaScript (Leaflet for maps). It runs as two containers.
Progress and plans are on the public roadmap at https://arksignal.net.

## How it works

- The hub's readsb publishes `clients.json` (full feeder UUID, real source IP, message and position rates).
  The portal polls `HUB_URL/data/clients.json` every 30 seconds and stores history.
- `clients.json` and `receivers.json` must never be public. Block them in Nginx Proxy Manager (see below).
- Exact feeder locations are stored privately. Public pages and `/api/feeders.json` only ever get coordinates
  through one function that applies the owner's privacy choice (General rounds to 0.1 degree, Exact, Hidden).

## Build and publish (GitHub Actions) and releases

Same flow as the photo pipeline. `build.yml` runs on every push to `main`, once a day (so base-image patches ride along) and by hand.
It publishes `mallison01/adsb-portal:latest` and a date tag (for example `2026.10.05`) to Docker Hub and GHCR. It never touches
`stable` or any version tag. A release is two manual steps once you have tried a build:

1. **Build the candidate with its version baked in:** Actions > Build and publish > Run workflow > version `1.0.0`
   (or `gh workflow run "Build and publish" -f version=1.0.0`). The version goes into the image's `BUILD_INFO`, which is how the
   running portal knows its own version (shown on Admin > Updates).
2. **Promote it after testing:** Actions > Promote to stable > Run workflow > version `1.0.0`
   (or `gh workflow run "Promote to stable" -f version=1.0.0`). This re-tags the already-built image as `stable` and `v1.0.0`
   without rebuilding, and refuses if the version typed does not match the one baked into `latest`.

The hub runs `stable`. Dependabot opens weekly pull requests for the pinned Python packages, the GitHub Actions and the Docker base
image. The portal checks Docker Hub once a day for a newer `stable` and says so on Admin > Updates and the Admin overview.

One-time setup, in the repository's Settings > Secrets and variables > Actions, add:

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | `mallison01` |
| `DOCKERHUB_TOKEN` | a Docker Hub access token with Read & Write permission |

Or from PowerShell (each command prompts for the value, so nothing is saved in your shell history):

```powershell
gh secret set DOCKERHUB_USERNAME --repo ArkSignal/ArkSignal-Portal
gh secret set DOCKERHUB_TOKEN --repo ArkSignal/ArkSignal-Portal
```

Make sure the Docker Hub repository `mallison01/adsb-portal` is set to Public.

## Deploy on the hub VM

1. Create the folders (the app runs as user 10001, the database as its own user):

   ```bash
   sudo mkdir -p /opt/adsb/portal/db /opt/adsb/portal/backups
   sudo chown localadmin:localadmin /opt/adsb/portal
   sudo chown 10001:10001 /opt/adsb/portal/backups
   ```

2. Copy `deploy/compose.yaml` to `/opt/adsb/portal/compose.yaml` and `deploy/.env.example` to
   `/opt/adsb/portal/.env`, then fill in `.env` (letters and numbers only):

   ```bash
   openssl rand -hex 24   # use for SECRET_KEY, run again for POSTGRES_PASSWORD
   nano /opt/adsb/portal/.env
   chmod 600 /opt/adsb/portal/.env
   ```

3. Start it:

   ```bash
   cd /opt/adsb/portal
   docker compose pull
   docker compose up -d
   docker compose logs --tail 20 portal-app
   ```

4. Add the NPM settings below, then sign in at `https://arksignal.net/my/login` with the
   `ADMIN_USERNAME` and `ADMIN_PASSWORD` from `.env`, and change the password.

Changing an environment value needs `docker compose up -d` (recreate), not `docker restart`.

## Nginx Proxy Manager settings

The portal now serves the whole site, including the home page at `/`, so the proxy host `arksignal.net`
forwards everything to it and only the hub's own tools keep their own locations.

1. Edit the proxy host `arksignal.net`, Details tab: set Scheme `http`, Forward Hostname `adsb-portal`,
   Forward Port `8430` (this replaces the old forward to `portal:80`, the static landing page). Keep Websockets on.
2. Keep the custom locations that go to the hub and to Grafana, unchanged:

   | Location | Forward to | Notes |
   |---|---|---|
   | `/map/` | `hub/` port 80 | prefix stripped (tar1090; the Classic Map and Heatmap tools use it) |
   | `/graphs1090/` | `hub` port 80 | prefix kept |
   | `/grafana/` | `grafana` port 3000 | only after the Grafana stack is installed; Websockets on |

   The old per-path locations for `/feeders/`, `/feed/`, `/airports/`, `/my/`, `/admin/`, `/api/`, `/view/` and `/live`
   are not needed any more: they all fall through to the portal.
3. Keep this block in the host's Advanced tab so the private files are not exposed through the map:

   ```nginx
   location ~ ^/map/data/(clients|receivers)\.json$ {
       return 404;
   }
   ```

After this the old static landing page container (`portal`) is no longer used and can be removed from the hub stack
once you have confirmed the new home page.

The portal reads the visitor's real IP from NPM's `X-Forwarded-For` header (one proxy hop), which is what the
claim-by-IP and Am I Feeding features rely on. Check it after deploying: sign in, open
`/my/claim` and confirm the IP shown is your public IP.

## First steps after deploying

1. Admin > Settings: check site name, feed hostname, timezone and map defaults.
2. Admin > Airports: Download and Update Now (loads about 25,000 US airports). The live map's airport layer uses it.
3. Admin > SMTP: configure email when a relay is available (the portal works without it).
4. Admin > Backups: confirm the daily schedule, then Create Backup Now once.
5. Admin > Tools: check the embedded tools (the live map, heatmap, replay and the Statistics Dashboard are added for you; the dashboard needs the Grafana stack from `deploy/grafana`); add Planefence here later.
6. Claim your own feeders under My Feeders and choose station IDs.

## Backup and restore

Admin > Backups takes consistent snapshots of the whole database (a `.tar.gz` of one CSV per table), on a
schedule or on demand, keeps the newest N scheduled ones, and lets you download, delete and restore. A restore
first takes an automatic safety backup, validates the archive, and replaces the data in one transaction.
Backups contain password hashes and the encrypted SMTP password, so store downloaded files securely.

For a copy outside the VM, download a backup from the page, or copy `/opt/adsb/portal/backups` with your
normal VM backups. To restore on a new server: deploy the stack as above, sign in with the bootstrap admin,
then Admin > Backups > Restore from a File. If `SECRET_KEY` differs from the original, re-enter the SMTP password.

## Updating

```bash
cd /opt/adsb/portal
docker compose pull
docker compose up -d
```

Database changes are applied automatically at start-up. Take a backup first (Admin > Backups).

## Development and testing

`compose.yaml` in the repository root builds the image locally with a throwaway database, for the test box.
Copy `.env.example` to `.env`, set `HUB_URL` to a reachable hub's tar1090 address, and run
`docker compose up -d --build`. Set `FORCE_INSECURE_COOKIES=1` only when testing over plain http.


## Per-feeder aircraft filter and station pages (hub port 30006)

The live map's Feeders picker and the station pages (`https://your-host/kd5vcf`) show only the aircraft chosen feeders are hearing. readsb's
aircraft.json does not say which receiver heard an aircraft, so the portal reads the hub's *beast reduce plus* output
(`--net-beast-reduce-out-port=30006`, with `--net-receiver-id`, which the production hub already uses). Requirements:

1. The portal container must reach `hub:30006`. In the standard production setup the portal and the hub are both on the `proxy` network, so nothing else is needed; otherwise
   publish 30006 on the hub host (restrict it to the LAN or the portal host with your firewall) and set Admin > Settings > Feeder Filter >
   Hub Receiver Feed Host to the hub's address.
2. Nothing is stored: the portal keeps only the last minute of "this feeder heard this aircraft" in memory (adjustable in Settings).
3. If the feed is unreachable the map says so and the unfiltered map keeps working.


## Visitor addresses behind the proxy

The Am I Feeding check, the sign-in history and the audit log record the visitor's public address. Behind Nginx Proxy Manager
(and Cloudflare, if used) the portal reads the first valid address from the headers listed in Admin > Settings > Accounts > Visitor
Address Headers (default `CF-Connecting-IP, X-Real-IP`), then falls back to the proxy-corrected address. Because those headers can be
set by whoever connects, the portal must only be reachable through the proxy, never directly from the internet.
