# 00-VERIFY: Restore Local Confidence

**Started:** 2026-07-02  
**Updated:** 2026-07-11  
**Status:** In progress — local test baseline is GREEN; two external read-only checks (Polygon, Alpaca) still pending operator authorization/credentials.  
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
| Polygon read-only | UNVERIFIED | Credential present and non-placeholder; live SPY daily-bar request still not run this session (needs authorized outbound HTTPS) |
| Alpaca read-only | BLOCKED | `TRADING_PLATFORM_BROKER__ALPACA__API_KEY` and `TRADING_PLATFORM_BROKER__ALPACA__API_SECRET` are absent (shell + `.env`) |
| Kill switch runtime block | PASS (2026-07-11) | PostgreSQL-backed trip/block/reset integration covered by `test_paper_execution.py` using `ExplodingExecutionService` (raises `AssertionError` if `submit_order` is invoked); trip→block→persist→manual-reset→resume and restart-safety all green |
| Broker order submission | NOT RUN | Explicitly out of scope until read-only access is verified |

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

**Separate operator-runtime bug (NOT test-blocking, still open):** `.env` lines
23–33 are malformed — each DB key and its value sit on separate lines with no `=`
(e.g. `TRADING_PLATFORM_DATABASE__NAME` then `neondb` on the next line). This
breaks the operator's real `load_settings()` boot and must be fixed as four
`KEY=value` lines. It also requires an operator decision on the DB target (local
`127.0.0.1` vs the Neon pooled URL) and a credential rotation (a Neon password was
exposed in-session).

## Current Project State

```text
Documentation state: v1.0 complete, v1.1 Phase 7 complete
Runtime state: local test baseline green; external read-only APIs unverified
Test state: full suite green (148 passed, PostgreSQL-backed)
External API state: Polygon and Alpaca not manually verified
Trading readiness: false
Next required step: run the two external read-only checks (Polygon, Alpaca) before Phase 8
```

## Remaining Gate Work

1. ~~Isolate the test environment from the operator `.env`, then rerun the full suite.~~ **DONE 2026-07-11** — `tests/conftest.py` isolation; full suite 148 passed.
2. Run one authorized Polygon read-only SPY daily-bar request. **(operator — needs authorized outbound HTTPS)**
3. Configure Alpaca paper credentials and run only `GET account`, `GET positions`, and `GET orders`. **(operator — creds absent)**
4. ~~Run the PostgreSQL-backed kill-switch trip/block/reset integration check with an execution service that fails if invoked.~~ **DONE 2026-07-11** — covered by `test_paper_execution.py` (`ExplodingExecutionService`), green.
5. Reclassify Phase 8 readiness only after the two remaining external checks (2, 3) pass.

## Operator Follow-ups (outside gate scope, flagged 2026-07-11)

- Repair malformed `.env` DB block (lines ~23–33): rewrite as four `KEY=value` lines and decide local vs Neon target.
- Rotate the Neon DB password exposed while diagnosing the `.env` bug.
