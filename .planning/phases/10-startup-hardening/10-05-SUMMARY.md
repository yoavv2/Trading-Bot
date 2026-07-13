---
phase: 10-startup-hardening
plan: 05
subsystem: config
tags: [pydantic, fastapi-lifespan, argparse-cli, startup-validation, postgres]

# Dependency graph
requires:
  - phase: 10-startup-hardening (10-01)
    provides: "validate_config(payload, *, mode) / ExecutionMode / ConfigValidationError — pure raw-payload config validator"
  - phase: 10-startup-hardening (10-03)
    provides: "check_database_connection(settings) -> (bool, str) — DB reachability primitive in db/session.py"
provides:
  - "core/startup.py: enforce_startup_config(*, mode, require_database=True, payload=None) -> Settings — the one process-boot gate every entrypoint calls before constructing domain services"
  - "CONFIG_VALIDATION_EXIT_CODE = 78, distinct from CONCURRENT_RUN_LOCK_EXIT_CODE = 3"
  - "Gate wired into: api/app.py lifespan (BACKTEST, require_database=False), every worker/__main__.py subcommand (PAPER for paper-side-effect commands, BACKTEST otherwise), services/bootstrap.py run_dry_bootstrap"
affects: [10-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Startup gate at entrypoints, not inside load_settings() — load_settings stays ungated/lru_cached so the 215+ existing tests keep working; the gate is an explicit extra call each real process entrypoint makes."
    - "require_database is a call-site override, not just a function default — read surfaces (the API) can opt out of the DB preflight while write surfaces keep it."

key-files:
  created:
    - src/trading_platform/core/startup.py
  modified:
    - src/trading_platform/api/app.py
    - src/trading_platform/worker/__main__.py
    - src/trading_platform/services/bootstrap.py
    - tests/test_startup_validation.py
    - tests/test_concurrency_guard_e2e.py

key-decisions:
  - "API lifespan gate uses require_database=False: the API is a read-only mode=BACKTEST surface with its own GET /ready DB-readiness reporting (readiness.require_database), and test_app_boot.py's TestClient exercises a supported API-only-boot-without-a-live-DB path (database.host: \"db\", unreachable outside docker) that a literal require_database=True default would break."
  - "sync-metadata's --dry-run flag skips the DB reachability preflight (require_database=not args.dry_run) since the flag's whole point is avoiding DB writes."
  - "services/bootstrap.py's run_dry_bootstrap only gates when it owns settings construction (settings is None); a caller-supplied Settings (e.g. from the worker CLI's own already-gated dry-run handler) is trusted, avoiding a redundant double validation+DB-check per invocation."
  - "test_concurrency_guard_e2e.py's migrated_paper_db fixture now sets TRADING_PLATFORM_BROKER__ALPACA__API_KEY/API_SECRET — Test A exercises submit-paper-orders (mode=PAPER) end-to-end and needs the gate's own CFG-01 check to pass before it can reach the lock-contention logic under test."

requirements-completed: [CFG-04, CFG-06]

# Metrics
duration: 35min
completed: 2026-07-13
---

# Phase 10 Plan 05: Startup Validation Gate Wiring Summary

**`enforce_startup_config()` gate wired into every process entrypoint (API lifespan, all 15 worker CLI subcommands, dry-bootstrap) so invalid config or an unreachable DB exits non-zero (code 78) before any domain service is constructed.**

## Performance

- **Duration:** 35 min
- **Started:** 2026-07-13T22:30:00+03:00 (approx)
- **Completed:** 2026-07-13T22:51:02+03:00
- **Tasks:** 2 completed
- **Files modified:** 7 (1 created, 6 modified, across 3 commits)

## Accomplishments
- `core/startup.py` ships `enforce_startup_config(*, mode, require_database=True, payload=None) -> Settings`: validates the raw pre-pydantic payload via 10-01's `validate_config`, then (by default) preflights DB reachability via 10-03's `check_database_connection`, printing one actionable, field/DB-target-naming message to stderr and exiting `SystemExit(78)` on any failure — never a raw pydantic traceback, never a silent partial boot.
- The gate is invoked before any domain-service construction at all three named entrypoint classes: `api/app.py`'s FastAPI `lifespan`, every one of `worker/__main__.py`'s 15 subcommand handlers, and `services/bootstrap.py`'s `run_dry_bootstrap`.
- Mode mapping matches the plan's key facts exactly: `submit-paper-orders`, `run-paper-session`, `sync-paper-state`, `reconcile-paper-execution` gate as `ExecutionMode.PAPER` (broker secrets required); everything else gates as `ExecutionMode.BACKTEST` (empty broker keys allowed).
- An ordering test (`test_submit_paper_orders_command_exits_before_domain_service_constructed`) proves CFG-06 concretely: with the test env's default empty broker creds, `run_paper_order_submission` — monkeypatched to raise if called — is never invoked because the gate's `SystemExit` fires first.
- A static `inspect.getsource` test pins the gate call at every named entrypoint function and confirms `settings.py`/`load_settings` stays ungated (matches the plan's explicit "do not hook validation into `load_settings`" constraint).

## Task Commits

Each task was committed atomically:

1. **Task 1: enforce_startup_config gate — validate raw payload + DB preflight -> non-zero exit** - `769eb5e` (feat)
2. **Task 2: Invoke the gate before service init at every entrypoint** - `be8643f` (feat)

**Deviation fix:** `1a57f6d` (fix) — `test_concurrency_guard_e2e.py` needed valid paper broker creds once its target command started going through the mode=PAPER gate.

_Note: tdd="true" was declared on both tasks; the implementation and its test file were authored and verified together rather than as a strict separate RED-then-GREEN commit pair, since the test suite was fully written and passing before either commit landed. Both commits are test+feat combined by content, not committed as two separate RED/GREEN steps._

## Files Created/Modified
- `src/trading_platform/core/startup.py` — the gate: `enforce_startup_config`, `CONFIG_VALIDATION_EXIT_CODE`
- `src/trading_platform/api/app.py` — lifespan calls the gate (`mode=BACKTEST, require_database=False`) instead of `load_settings()`
- `src/trading_platform/worker/__main__.py` — all 15 subcommand handlers call the gate with their mapped `ExecutionMode`; unused `load_settings` import removed
- `src/trading_platform/services/bootstrap.py` — `run_dry_bootstrap` calls the gate when it owns settings construction
- `tests/test_startup_validation.py` — 9 tests: exit-code distinctness, missing-secret, out-of-range-tolerance, unreachable-DB (refused `localhost:1`), DB-check-skip when `require_database=False`, valid-config happy path, service-init ordering proof, API empty-keys-no-DB happy path, static wiring-coverage check
- `tests/test_concurrency_guard_e2e.py` — `migrated_paper_db` fixture now sets paper broker env creds so Test A can pass the gate and reach its lock-contention assertions

## Decisions Made
See `key-decisions` in frontmatter. In short: the API's DB preflight is explicitly opted out (`require_database=False`) because the API is a read-only `BACKTEST`-mode surface with its own existing `/ready` DB-readiness reporting, and `test_app_boot.py` exercises a supported no-DB API boot as a first-class scenario — a literal `require_database=True` default at that one call site would have broken it. `sync-metadata --dry-run` similarly opts out since it never touches the DB. Everywhere else keeps the function's `require_database=True` default, since those entrypoints already need a DB connection to do their actual work.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `test_concurrency_guard_e2e.py` Test A broke once wired to the PAPER-mode gate**
- **Found during:** Task 2, full-suite verification (`python -m pytest -q`)
- **Issue:** `run_submit_paper_orders_command` now calls `enforce_startup_config(mode=ExecutionMode.PAPER)` first. The test environment's broker `api_key`/`api_secret` are empty by default (`conftest.py`'s `isolate_operator_env` disables `.env` loading), so `validate_config` raised `ConfigValidationError` and the command exited with `CONFIG_VALIDATION_EXIT_CODE` (78) before ever reaching the lock-contention logic Test A asserts (`CONCURRENT_RUN_LOCK_EXIT_CODE` = 3).
- **Fix:** Added `TRADING_PLATFORM_BROKER__ALPACA__API_KEY`/`API_SECRET` env vars to the `migrated_paper_db` fixture in `test_concurrency_guard_e2e.py` (Test B is unaffected — it calls `run_paper_order_submission` directly, bypassing the CLI gate).
- **Files modified:** `tests/test_concurrency_guard_e2e.py`
- **Verification:** `python -m pytest tests/test_concurrency_guard_e2e.py -q` → 2 passed; full suite green.
- **Committed in:** `1a57f6d`

**2. [Rule 3 - Blocking, not applied — call-site parameter, not a fix] API lifespan `require_database` default would have broken `test_app_boot.py`**
- **Found during:** Task 2 planning (pre-implementation analysis, confirmed via advisor consultation)
- **Issue:** `enforce_startup_config`'s function default is `require_database=True`. Wiring the API lifespan to that literal default would attempt `check_database_connection` against `database.host: "db"` (a docker-compose-only hostname), which `test_app_boot.py`'s `TestClient` fixture cannot resolve outside a container — breaking a currently-passing test and violating the plan's own "full suite stays green" verification bar.
- **Fix:** Passed `require_database=False` explicitly at the API lifespan call site (not a change to the gate's own default/tested contract). Documented inline with a comment referencing the existing `readiness.require_database`/`/ready` precedent for DB-optional API boots.
- **Files modified:** `src/trading_platform/api/app.py`
- **Verification:** `python -m pytest tests/test_app_boot.py -q` unaffected; `test_api_lifespan_boots_with_empty_alpaca_keys_and_no_reachable_db` pins the scenario directly.
- **Committed in:** `be8643f`

---

**Total deviations:** 2 (1 auto-fixed test-setup gap, 1 call-site design decision documented rather than defaulted)
**Impact on plan:** Both were necessary to satisfy the plan's own explicit "full suite stays green" constraint without weakening the gate's actual tested contract (function default remains `require_database=True`). No scope creep beyond the plan's `files_modified` list plus the one test-fixture fix.

## Issues Encountered

**Concurrent working-tree modification by a sibling plan executor.** While this plan (10-05, wave 2, `depends_on: ["10-01"]`) was executing, a sibling plan (10-04) was executing concurrently in the same physical working directory (orchestrator parallelization is enabled, `max_concurrent_agents: 3`). At two points, edits already applied to `src/trading_platform/api/app.py` and the import lines of `src/trading_platform/worker/__main__.py` were found reverted to their pre-edit (HEAD) state on a fresh `Read`, with no corresponding edit made by this executor. Diagnosis (via `git show 769eb5e --stat`, `git status`, and process inspection) confirmed: (1) this plan's own Task 1 commit was durable and untouched; (2) the reversion was isolated to uncommitted working-tree edits on files this plan was actively editing; (3) 10-04 later committed (`8d3f416`) with its own STATE.md decision entry explicitly noting "advisor-reviewed given a concurrent sibling plan (10-05) was editing the same working tree at the time" — confirming the collision was mutual and already known to the other executor, not a rogue process. Recovery: re-read all affected files from disk (ground truth only), re-applied the missing edits, ran the full gate-wiring verification (including a new static `inspect.getsource`-based canary test designed to fail loudly on exactly this class of silent reversion), and committed immediately (`be8643f`) rather than continuing to accumulate further uncommitted work. No data was permanently lost; total added latency was one recovery cycle. Both plans' commits are now present and independently verified (`git show HEAD:<file> | grep enforce_startup_config`).

**Pre-existing, unrelated intermittent test-teardown flake.** Full-suite runs occasionally (not on every run) report 1-3 `ERROR`s at teardown of `migrated_access_db`-style fixtures (`test_market_data_access.py`, `test_market_data_ingestion.py`, `test_portfolio_service.py`, others) with `psycopg.errors.InsufficientPrivilege: must be a superuser to terminate superuser process` during their `pg_terminate_backend` cleanup step. Confirmed unrelated to this plan: the specific failing test varies non-deterministically run-to-run, each affected test passes cleanly in isolation, and a clean full-suite run (`276 passed` with zero errors) was also observed with no code changes in between. Logged in `deferred-items.md` for a follow-up (out of this plan's `files_modified` scope — the pattern is duplicated across 6+ unrelated test files).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- CFG-04 and CFG-06 are both satisfied end-to-end and marked Complete in REQUIREMENTS.md. All of CFG-01 through CFG-07 are now Complete — Phase 10's config-hardening requirement set is fully closed.
- 10-06 (logger migration + formatter backstop + emitted-line enforcement) can proceed; it is unaffected by this plan's changes (no overlap in `files_modified`), though `deferred-items.md` from both 10-02 and 10-05 have items directly in its scope (message-string sanitization gap; the DB-teardown flake, lower priority).
- The concurrent-execution collision observed during this plan (see Issues Encountered) is worth flagging to the orchestrator: two wave-parallel plans editing the same non-worktree-isolated working directory can silently revert each other's uncommitted edits. No corrective action was in this plan's scope, but future wave-parallel execution of plans with overlapping wall-clock windows should either use isolated worktrees or accept this recovery pattern (frequent small commits, static canary tests) as the mitigation.

---
*Phase: 10-startup-hardening*
*Completed: 2026-07-13*

## Self-Check: PASSED

All created/modified files confirmed present on disk; all 3 commits (`769eb5e`, `be8643f`, `1a57f6d`) confirmed in `git log`.
