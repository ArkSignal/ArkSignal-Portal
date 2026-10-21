# Test suite

End-to-end checks that run from the Windows PC against the portal on the XPEnology test box
(`http://test-box.invalid:18430`). They read the admin password from `TEST_CREDENTIALS.md` (not committed).

- `run_all.py` resets the test database, loads airport data, then runs every suite in order and prints a summary.
  It needs `ssh nastemp` to work (it recreates the containers and copies helper scripts into them).
- Run a single suite with `python <name>_test.py` (after `run_all.py` has set the baseline users, or on a fresh database).
- `smtp_test.py` needs `fake_smtp.py` running on the PC (ports 2525-2527) and a `cert/key` pair; see its header.
- `collector_unit.py`, `prune.py`, `sched.py`, `nearest.py` run inside the container:
  `docker cp` the file to `/tmp`, then `docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/<file>`.
- The helpers use `/tmp/t` paths from the original working session; adjust paths if you move them.

## Test box configuration notes
- The test box `.env` sets `TOOLS_BASE_URL=http://test-box.invalid:18080` so the seeded Tools point at the test hub (the box does not serve `/map/`). `tools_test.py` switches the tools to the production paths for its checks and back afterwards.
- `run_all.py` turns the public data cache off (`public_cache_seconds=0`) because suites write rows straight into the database.
- In-container unit scripts (copy to the container, run with `docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/<file>`): `collector_unit.py`, `receivers_unit.py`, `range_unit.py`, `csrf_unit.py`, `update_unit.py`, `digest_unit.py`, `schedule_unit.py`, `prune.py`, `sched.py`, `nearest.py`.
- `feederfilter_test.py` needs the test hub's port 30004 (it feeds a fake second feeder) and 30006 published.

- `jscheck_test.py` parses every page's inline scripts with the `esprima` package (`pip install esprima` on the PC); it is skipped when the package is missing.
