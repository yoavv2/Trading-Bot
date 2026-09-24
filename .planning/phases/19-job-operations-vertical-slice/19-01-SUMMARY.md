---
phase: 19-job-operations-vertical-slice
plan: 01
subsystem: database
tags: [alembic, sqlalchemy, postgresql, migration, job-framework]

# Dependency graph
requires:
  - phase: 18-orchestration-surface
    provides: Job/JobMutation ORM models, migration 0019, job_failure_reason native enum
provides:
  - "strategy_runs.job_id: nullable UNIQUE FK -> jobs.id ON DELETE SET NULL (D-01)"
  - "JobFailureReason.CONFIG_INVALID enum member + job_failure_reason DB enum value (D-22 schema half)"
  - "Migration 0020_phase19_job_operations, verified round-trip (upgrade/downgrade/re-upgrade)"
  - "Local trading_platform database upgraded to head (0020)"
affects: [19-02, 19-03, 19-04, 19-05, 19-06, 19-07, 19-08, 19-09, 19-10, 19-11, 19-12]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Native Postgres ALTER TYPE ... ADD VALUE IF NOT EXISTS for closed-enum extension, with a documented no-op downgrade (0016 precedent)"
    - "Combining an enum-add and a FK/UNIQUE column-add in a single migration file when both are needed by the same phase"

key-files:
  created:
    - alembic/versions/0020_phase19_job_operations.py
    - tests/test_phase19_job_operations_migration.py
  modified:
    - src/trading_platform/db/models/job.py
    - src/trading_platform/db/models/strategy_run.py
    - tests/test_db_migrations.py
    - tests/test_job_mutation_migration.py

key-decisions:
  - "Followed the plan's exact migration shape: single op.execute for the enum ADD VALUE (no autocommit_block needed, mirrors 0016), then add_column/create_foreign_key/create_unique_constraint for job_id, all inside one alembic transaction."
  - "Pinned the Phase 18 downgrade round-trip test to the explicit revision 0018_phase17_job_framework instead of head-relative '-1', since 0020 is now head."
  - "Local trading_platform database was stale at revision 0015 before this plan (Phases 8/11/17/18 migrations were never applied locally); upgraded straight through to 0020 as part of Task 2's [BLOCKING] step."

patterns-established:
  - "Native-enum extension migrations stay a documented no-op on downgrade (PostgreSQL cannot drop a single enum value without a full type rewrite) — second instance of this pattern after 0016."

requirements-completed: [OPS-01, JOBUI-02]

# Metrics
duration: ~15min
completed: 2026-09-24
---

# Phase 19 Plan 01: Job -> StrategyRun Schema Link Summary

**Migration 0020 adds strategy_runs.job_id (nullable UNIQUE FK -> jobs.id, ON DELETE SET NULL) and JobFailureReason.CONFIG_INVALID to the native job_failure_reason enum, applied to the local database and proven by round-trip + constraint-enforcement tests.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-09-24T08:41:02Z (per STATE.md session start)
- **Completed:** 2026-09-24T08:45:22Z
- **Tasks:** 2
- **Files modified:** 6 (2 created, 4 modified)

## Accomplishments
- Migration `0020_phase19_job_operations` ships both the D-22 enum extension (`config_invalid`) and the D-01 `strategy_runs.job_id` FK/UNIQUE column, verified to upgrade, downgrade to `0019_phase18_job_idempotency`, and re-upgrade cleanly.
- `JobFailureReason.CONFIG_INVALID` and `StrategyRun.job_id` (nullable, unique, `ForeignKey("jobs.id", ondelete="SET NULL")`) added to the ORM models, matching the migration exactly (pinned by `test_orm_metadata_matches_migration`).
- New `tests/test_phase19_job_operations_migration.py` (5 tests) proves: FK/UNIQUE/enum shape after upgrade; UNIQUE rejects a second non-null `job_id` on `strategy_runs` while multiple `NULL` rows coexist; deleting a `jobs` row sets the linked `strategy_runs.job_id` to `NULL`; downgrade/re-upgrade round-trip; ORM metadata matches the migration.
- The local `trading_platform` Postgres database, previously stale at revision `0015`, was upgraded through `0016`-`0020` to head as the plan's Task 2 `[BLOCKING]` step required.

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 0020 + ORM model edits** - `eb24f32` (feat)
2. **Task 2: [BLOCKING] Apply migration and add migration enforcement tests** - `9ae2b22` (test)

**Plan metadata:** (this commit, docs: complete plan)

## Files Created/Modified
- `alembic/versions/0020_phase19_job_operations.py` - New migration: `ALTER TYPE job_failure_reason ADD VALUE IF NOT EXISTS 'config_invalid'`, then `add_column`/`create_foreign_key`/`create_unique_constraint` for `strategy_runs.job_id`; documented no-op enum downgrade.
- `src/trading_platform/db/models/job.py` - `JobFailureReason.CONFIG_INVALID = "config_invalid"` added as the fifth member.
- `src/trading_platform/db/models/strategy_run.py` - `job_id: Mapped[uuid.UUID | None]` mapped column (nullable, unique, FK `jobs.id` ON DELETE SET NULL).
- `tests/test_phase19_job_operations_migration.py` - New: 5 tests proving the FK/UNIQUE/enum constraints, cascade behavior, round-trip, and ORM-migration parity.
- `tests/test_db_migrations.py` - `job_failure_reason` exact-set assertion extended to include `"config_invalid"`.
- `tests/test_job_mutation_migration.py` - Phase 18 downgrade round-trip test pinned to explicit revision `0018_phase17_job_framework` instead of head-relative `"-1"`.

## Decisions Made
- Combined the enum-add and the FK/UNIQUE column-add into a single migration file (0020) rather than splitting into two files, per the plan's explicit instruction and since the enum value is never referenced within the same transaction.
- Used the plan-specified constraint names via `op.f()` (`fk_strategy_runs_job_id_jobs`, `uq_strategy_runs_job_id`), which match the NAMING_CONVENTION-derived names the ORM's `unique=True`/`ForeignKey(...)` declaration produces automatically — verified by `test_orm_metadata_matches_migration`.
- Discovered the local `trading_platform` database was at revision `0015` (Phase 8) rather than head before this plan ran; treated bringing it to head as in-scope per Task 2's explicit `[BLOCKING] Apply the migration` instruction rather than a separate deviation.

## Deviations from Plan

None - plan executed exactly as written. The local-database staleness (revision 0015 instead of 0019) was already anticipated by the plan's Task 2 `[BLOCKING]` step ("run `python scripts/migrate.py upgrade head`... Type checks passing is not evidence the migration works — this step is mandatory"), so bringing it to head is the plan's own instruction, not an unplanned fix.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `strategy_runs.job_id` and `JobFailureReason.CONFIG_INVALID` are live in the local database and ORM, unblocking every later Phase 19 plan that threads `job_id` through `run_backtest`/`_create_backtest_run`, reads `resources[]` on the Job detail API, or writes the `config_invalid` FAILED transition in the worker runner.
- No blockers identified for 19-02 onward.

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*
