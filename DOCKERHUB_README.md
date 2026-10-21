# Arkansas ADS-B Network portal

A web portal for a community ADS-B receiver network built on a tar1090/readsb hub: accounts and roles, feeder claiming with station
IDs, per-feeder status and history, a native live map (the same aircraft icons, layers and trace data as tar1090), public feeders
list with rankings and privacy-aware locations, airport pages, flight progress, alerts with email limits, an admin area, and
Grafana statistics. Source and documentation: https://github.com/ArkSignal/ArkSignal-Portal

## Tags

| Tag | Meaning |
|---|---|
| `stable` | The release you should run. Moves only when a release is promoted. |
| `vX.Y.Z` | A specific promoted release (also what `stable` currently points at). |
| `latest` | Every build, including the daily rebuild. Not necessarily tested. |
| `YYYY.MM.DD` | The build of that day, for rollback and reference. |

## Run it

Two containers: this image and PostgreSQL 17. The complete `compose.yaml` and `.env.example` are in the repository under `deploy/`
(`restart: unless-stopped`, `init: true`, joined to the same Docker network as the hub so it reaches `hub:80` and `hub:30006`).

```yaml
services:
  portal-app:
    image: mallison01/adsb-portal:stable
    restart: unless-stopped
    init: true
    environment:
      SECRET_KEY: "${SECRET_KEY}"
      ADMIN_USERNAME: "${ADMIN_USERNAME:-admin}"
      ADMIN_EMAIL: "${ADMIN_EMAIL}"
      ADMIN_PASSWORD: "${ADMIN_PASSWORD}"
      HUB_URL: "http://hub:80"
      DATABASE_URL: "postgresql://portal:${POSTGRES_PASSWORD}@portal-db:5432/portal"
    volumes:
      - ./backups:/backups
```

Update with `docker compose pull && docker compose up -d`. Database changes are applied at start-up; take a backup from
Admin > Backups first. Environment: `SECRET_KEY` (also encrypts the saved SMTP password), `HUB_URL`, `DATABASE_URL`, the first
administrator (`ADMIN_*`). Everything else is configured in the web interface (Admin > Settings).
