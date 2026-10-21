#!/bin/sh
# Build the working tree into its own image tag (wip) and run ONLY the persistent test portal (:18431) from it, so a full run_all on the throwaway portal (:18430) is not disturbed.
# deploy_test.sh puts both portals back on the normal image.
cd "$(dirname "$0")/.." || exit 1
tar czf /tmp/portal-wip.tgz --exclude=.env --exclude=data --exclude=__pycache__ --exclude=.git --exclude=TEST_CREDENTIALS.md . || exit 1
scp -q /tmp/portal-wip.tgz nastemp:portal-wip.tgz || exit 1
ssh -q nastemp "rm -rf ~/adsb-wip && mkdir ~/adsb-wip && cd ~/adsb-wip && tar xzf ~/portal-wip.tgz && rm ~/portal-wip.tgz && echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker build -q -t mallison01/adsb-portal:wip . && cd ~/adsb-portal-persist && printf 'services:\n  portal-app:\n    image: mallison01/adsb-portal:wip\n' > override-wip.yaml && echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker compose -p adsbpersist -f compose.yaml -f override-wip.yaml up -d --force-recreate portal-app" 2>&1 | grep -v "^\*\*" | tail -6
