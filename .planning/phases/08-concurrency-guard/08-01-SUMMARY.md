---
phase: 08-concurrency-guard
plan: 01
subsystem: database
tags: [sqlalchemy, alembic, postgres, pydantic-settings, closed-enum]

# Dependency graph
requires: []
provides:
  - "StrategyRunStatus.STALE closed-enum member (Python + PG strategy_run_status enum via migration 0016)"
  - "execution.safety.stale_run_timeout_minutes config setting (default 30, env-overridable)"
affects: [08-02, 08-03, 08-04, 08-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "PG enum ADD VALUE IF NOT EXISTS with documented no-op downgrade (single-value additions cannot be cleanly reverted without a full type recreation)"

key-files:
  created:
    - alembic/versions/0016_phase8_stale_run_status.py
    - tests/test_stale_run_config.py
  modified:
    - src/trading_platform/db/models/strategy_run.py
    - src/trading_platform/core/settings.py

key-decisions:
  - "Migration 0016 downgrade is an intentional no-op (documented inline) rather than attempting a partial ALTER TYPE ... DROP VALUE, matching the plan's explicit instruction and PostgreSQL's lack of native enum-value removal."
  - "stale_run_timeout_minutes placed on ExecutionSafetySettings (execution.safety block) alongside repeated_failure_threshold, not a new settings block, consistent with the config-externalization principle already established there."

patterns-established:
  - "Stale-run timeout is read from resolved_settings.execution.safety.stale_run_timeout_minutes, not a hardcoded constant — the pattern 08-03/08-04 will consume."

requirements-completed: [LOCK-04]

# Metrics
duration: ~15min
completed: 2026-07-12
---

# Phase 8 Plan 01: Stale-Run Enum + Config Foundation Summary

**Added a closed-enum `STALE` status to `StrategyRunStatus` (with PG migration 0016) and externalized a 30-minute `stale_run_timeout_minutes` safety setting, proven end-to-end against a migrated PostgreSQL database — the representation/config foundation LOCK-04 and all later Phase 8 detection/reclaim logic build on.**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-07-12
- **Tasks:** 2/2 completed
- **Files modified:** 4 (2 created, 2 modified)

## Accomplishments
- `StrategyRunStatus.STALE = "stale"` added as a fourth closed-enum value alongside pending/running/succeeded/failed; the existing `Enum(..., values_callable=_enum_values, validate_strings=True)` column mapping picked it up with zero column-definition changes.
- `alembic/versions/0016_phase8_stale_run_status.py` chains cleanly from head (`0015_phase7_kill_switch`), adds `'stale'` to the PostgreSQL `strategy_run_status` enum idempotently, and documents why downgrade is a no-op.
- `ExecutionSafetySettings.stale_run_timeout_minutes` (default 30, `ge=1`) added, resolvable via `resolved_settings.execution.safety.stale_run_timeout_minutes` and overridable via `TRADING_PLATFORM_EXECUTION__SAFETY__STALE_RUN_TIMEOUT_MINUTES`.
- `tests/test_stale_run_config.py` proves all three claims: default value, env override, and a `StrategyRun` row with `status=StrategyRunStatus.STALE` round-tripping against a freshly migrated temp PostgreSQL database.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add STALE to StrategyRunStatus + alembic migration 0016** - `2e39682` (feat)
2. **Task 2: Externalize stale_run_timeout_minutes + migration/config test** - `92c4276` (feat)

**Plan metadata:** pending (this commit)

## Files Created/Modified
- `src/trading_platform/db/models/strategy_run.py` - Added `STALE = "stale"` to `StrategyRunStatus`
- `alembic/versions/0016_phase8_stale_run_status.py` - New migration adding `'stale'` to the PG `strategy_run_status` enum; documented no-op downgrade
- `src/trading_platform/core/settings.py` - Added `stale_run_timeout_minutes: int = Field(default=30, ge=1)` to `ExecutionSafetySettings`
- `tests/test_stale_run_config.py` - New test file: 2 config unit tests (default, env override) + 1 migrated-DB integration test (STALE status round-trip), following the `tests/test_paper_execution.py` temp-DB fixture pattern

## Decisions Made
- Followed the plan's explicit instruction for the migration downgrade: PostgreSQL cannot drop a single enum value without a full type recreation, so `downgrade()` is a documented no-op rather than a partial/unsafe `ALTER TYPE ... DROP`.
- Used the inline-comment style already established in `settings.py` (e.g. `TrendFollowingIndicatorSettings.warmup_periods`) for documenting `stale_run_timeout_minutes` rather than an unconventional bare-string field docstring.
- Reused the `_admin_connection_settings`/`_connect_admin`/`_set_database_env` temp-DB fixture helpers verbatim from `tests/test_paper_execution.py` (per the plan's explicit instruction to copy this pattern) rather than introducing a shared `conftest.py`, keeping this test file self-contained per the repo's "no conftest.py, per-file setup" convention documented in `TESTING.md`.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None. During execution, `src/trading_platform/services/concurrency_guard.py` and `tests/test_concurrency_guard.py` appeared in the working tree partway through this plan's Task 2. This was initially flagged as an odd untracked artifact, but is explained: plan 08-02 (`wave: 1`, `depends_on: []` — same wave as this plan) was executed by a parallel Wave-1 executor concurrently and committed independently (`6c235b2`, `50de304`). Not part of this plan's scope; not touched, modified, or committed here.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `StrategyRunStatus.STALE` and `execution.safety.stale_run_timeout_minutes` are available for 08-02 (advisory lock primitive), 08-03 (stale-run detection query), and 08-04 (integration/reordering) to consume.
- Migration head is now `0016_phase8_stale_run_status`; subsequent Phase 8 migrations should chain from it.
- No blockers for 08-02.

---
*Phase: 08-concurrency-guard*
*Completed: 2026-07-12*

## Self-Check: PASSED

- FOUND: alembic/versions/0016_phase8_stale_run_status.py
- FOUND: tests/test_stale_run_config.py
- FOUND commit: 2e39682
- FOUND commit: 92c4276
