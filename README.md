# ArkSignal portal

ArkSignal is a volunteer network for Arkansas: ADS-B aircraft tracking, radio (APRS, ACARS, weather balloons and more) and a mesh network, with
accounts and roles, feeder claiming, a live map, public feeder lists with privacy-aware locations, alerts, an admin area, and Grafana statistics.
This repository is the portal's source code (Flask, PostgreSQL 17, gunicorn, plain JavaScript) plus the Docker files for its helper services.

**Live site:** https://arksignal.net

## This is a mirror

The project is developed in a private repository; this public repository receives one commit per release (`ArkSignal <version>`), with
internal operating notes and test-box details removed. Source for the version a site runs is always here, as the AGPL requires.
Issues are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md) for how changes are taken in.

## Run it with Docker

The images are built by this repository's GitHub Actions (`build.yml`, `promote.yml`) and published on Docker Hub as `mallison01/adsb-*` (for example `mallison01/adsb-portal:stable`; tags `stable`, `vX.Y.Z`, `latest`).
The simplest start is the portal plus PostgreSQL:

1. Copy `deploy/compose.yaml` and `deploy/.env.example` to a folder, rename the example to `.env` and fill in `SECRET_KEY`, `POSTGRES_PASSWORD` and the first administrator.
2. `docker compose up -d`, then open the port the compose file publishes.
3. Update with `docker compose pull && docker compose up -d` (take a backup from Admin > Backups first).

The portal expects a tar1090/readsb hub reachable as `HUB_URL`. Everything else is configured in the web interface (Admin > Settings).
The full operating guide (folders, reverse proxy, first steps, backup and restore, updating) is [docs/OPERATING.md](docs/OPERATING.md). More detail is in [DOCKERHUB_README.md](DOCKERHUB_README.md) and the per-service folders under `deploy/`.

## Tests

`tests/` holds the suite. Most tests drive a running test instance: set `TEST_HOST` to its address, and `TEST_SUDO_PASSWORD` where a test runs docker through ssh
(see `tests/README.md`).

## License

[GNU Affero General Public License v3.0](LICENSE). If you run a modified version as a network service, you must offer its users the source of your version.
