#!/bin/sh
# Runs the suites that cover what a change touched, one after another, against the test portal (the portal must be deployed and running).
# Usage: sh targeted.sh suite_one suite_two ...   Prints one line per suite and ends with TARGETED PASS or TARGETED FAILED.
cd "$(dirname "$0")" || exit 1
bad=0
for t in "$@"; do
  out=$(timeout 700 python3 "$t.py" 2>&1)
  if echo "$out" | grep -q "^FAIL"; then
    echo "FAIL $t: $(echo "$out" | grep '^FAIL' | head -3 | cut -c1-300)"; bad=1
  elif echo "$out" | grep -qE "ALL PASS|SMOKE PASS"; then
    echo "OK   $t: $(echo "$out" | grep -c '^PASS') checks"
  else
    echo "FAIL $t: no pass marker: $(echo "$out" | tail -2 | cut -c1-200)"; bad=1
  fi
done
[ "$bad" = 0 ] && echo "TARGETED PASS" || echo "TARGETED FAILED"
