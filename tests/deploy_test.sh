#!/bin/sh
# Push the working tree to the test box, rebuild, and refresh both portals (throwaway :18430 and persistent :18431).
cd "$(dirname "$0")/.." || exit 1
tar czf /tmp/portal.tgz --exclude=.env --exclude=data --exclude=__pycache__ --exclude=.git --exclude=TEST_CREDENTIALS.md . || exit 1
scp -q /tmp/portal.tgz nastemp:adsb-portal/ || exit 1
ssh -q nastemp "cd ~/adsb-portal && tar xzf portal.tgz && rm portal.tgz && echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker compose up -d --build && cd ~/adsb-portal-persist && echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker compose -p adsbpersist up -d --force-recreate portal-app" 2>&1 | grep -v "^\*\*" | tail -8
