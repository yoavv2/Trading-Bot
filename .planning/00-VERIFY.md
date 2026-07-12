# 00-VERIFY: Restore Local Confidence

**Started:** 2026-07-02  
**Updated:** 2026-07-12  
**Status:** ✅ GREEN — local test baseline and both external read-only checks (Polygon, Alpaca) verified. Phase 8 is unblocked.  
**Scope:** Verification only; no Phase 8 work, feature work, refactor, live trading, or broker submission.

## Readiness Gate

| Check | Result | Evidence |
|---|---|---|
| Local Python environment | PASS | `.venv/bin/python --version` → Python 3.13.1 |
| PostgreSQL reachable | PASS | Local PostgreSQL answered on `127.0.0.1:5432`; `trading_platform` exists |
| Migrations at head | PASS | `scripts/migrate.py upgrade head` applied through `0015_phase7_kill_switch` |
| Full pytest baseline | PASS (2026-07-11) | `148 passed in ~23s`, 0 skipped/xfail/error; 32 PostgreSQL-backed tests included and green against local `127.0.0.1:5432` |
| Focused app boot | PASS (2026-07-11) | Both `test_app_boot.py` tests pass after test-env isolation (`tests/conftest.py` disables operator `.env` for the suite) |
| Focused DB migrations + dry run | PASS | 13 passed |
| Focused strategy registry | PASS | 2 passed |
| Polygon read-only | PASS (2026-07-12, operator) | Authorized read-only HTTPS probe succeeded; no database writes |
| Alpaca read-only | PASS (2026-07-12, operator) | Paper credentials configured; `GET /v2/account` → 200 ACTIVE, `GET /v2/positions` → `[]`, `GET /v2/orders?status=all` → `[]`; no write operations |
| Kill switch runtime block | PASS (2026-07-11) | PostgreSQL-backed trip/block/reset integration covered by `test_paper_execution.py` using `ExplodingExecutionService` (raises `AssertionError` if `submit_order` is invoked); trip→block→persist→manual-reset→resume and restart-safety all green |
| Broker order submission | NOT RUN | Out of scope for this gate — read-only verification only; no order ever submitted |

## Commands and Results

```text
PYTHONPATH=src .venv/bin/python scripts/migrate.py upgrade head
PASS — database upgraded from 0005_phase3_btr through 0015_phase7_kill_switch

PYTHONPATH=src .venv/bin/pytest tests/test_app_boot.py -q --tb=short
PASS — 2 passed (after tests/conftest.py env isolation)

PYTHONPATH=src .venv/bin/pytest tests/test_strategy_registry.py -q --tb=short
PASS — 2 passed

PYTHONPATH=src .venv/bin/pytest tests/test_db_migrations.py tests/test_dry_run.py -q
PASS — 13 passed

PYTHONPATH=src .venv/bin/pytest -q          # full suite
PASS — 148 passed in ~23s, 0 skipped/failed/error

# External read-only verification (operator-run, 2026-07-12)
Polygon authorized read-only HTTPS probe
PASS — response received, no database writes

Alpaca Paper read-only
PASS — GET /v2/account → 200 (ACTIVE); GET /v2/positions → []; GET /v2/orders?status=all → []
```

## Root Cause: Test Env Bleed (2026-07-11)

`EnvironmentOverrides` (src/trading_platform/core/settings.py:267) hardcodes
`env_file=".env"`, so every test loaded the operator's live `.env`. That flipped
`app.environment` from `test`→`local` (the original documented failure) and, once
the operator `.env` was edited on 2026-07-10, injected four `None` DB values that
failed settings validation before any test body ran.

**Fix:** `tests/conftest.py` adds an autouse fixture that sets
`EnvironmentOverrides.model_config["env_file"] = None` for the whole suite. DB
fixtures were already independent (`os.getenv(..., "localhost")` + `monkeypatch`),
so nothing regressed.

**Separate operator-runtime bug (RESOLVED 2026-07-12):** the malformed `.env` DB
block was repaired to valid `KEY=value` entries. DB target is split by environment
— local development uses PostgreSQL at `127.0.0.1`; production receives Neon
credentials from Render environment variables. The Neon password exposed while
diagnosing the bug was rotated and updated in Render (backend healthy).

## Current Project State

```text
Documentation state: v1.0 complete, v1.1 Phase 7 complete
Runtime state: local test baseline green; external read-only APIs verified
Test state: full suite green (148 passed, PostgreSQL-backed)
External API state: Polygon and Alpaca read-only verified (2026-07-12)
Trading readiness: read-only verified; no write/order path exercised
Next required step: Phase 8 (Concurrency Guard) — migrate detail + LOCK-01..06 into active planning files, then plan
```

## Remaining Gate Work

1. ~~Isolate the test environment from the operator `.env`, then rerun the full suite.~~ **DONE 2026-07-11** — `tests/conftest.py` isolation; full suite 148 passed.
2. ~~Run one authorized Polygon read-only SPY daily-bar request.~~ **DONE 2026-07-12 (operator)** — authorized read-only HTTPS probe, no DB writes.
3. ~~Configure Alpaca paper credentials and run only `GET account`, `GET positions`, and `GET orders`.~~ **DONE 2026-07-12 (operator)** — account 200/ACTIVE, positions `[]`, orders `[]`, no writes.
4. ~~Run the PostgreSQL-backed kill-switch trip/block/reset integration check with an execution service that fails if invoked.~~ **DONE 2026-07-11** — covered by `test_paper_execution.py` (`ExplodingExecutionService`), green.
5. ~~Reclassify Phase 8 readiness only after the two remaining external checks (2, 3) pass.~~ **DONE 2026-07-12** — all checks green; gate marked GREEN, Phase 8 unblocked.

## Operator Follow-ups (RESOLVED 2026-07-12)

- ~~Repair malformed `.env` DB block.~~ Fixed with valid `KEY=value` entries; local dev → `127.0.0.1`, production → Neon via Render env vars.
- ~~Rotate the exposed Neon DB password.~~ Rotated and updated in Render; backend healthy.
