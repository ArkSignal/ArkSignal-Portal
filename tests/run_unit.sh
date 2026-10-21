#!/bin/sh
# run one in-container unit script: sh run_unit.sh weather_unit.py
cd "$(dirname "$0")" || exit 1
scp -q "$1" nastemp:adsb-portal/"$1" || exit 1
ssh -q nastemp "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker cp ~/adsb-portal/$1 adsb-portal:/tmp/$1 && echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/$1" 2>&1 | grep -v "^\*\*"
