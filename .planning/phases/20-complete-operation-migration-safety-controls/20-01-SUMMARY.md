---
phase: 20-complete-operation-migration-safety-controls
plan: 01
subsystem: database
tags: [alembic, sqlalchemy, postgresql, jobs, migrations]

# Dependency graph
requires:
  - phase: 19-job-operations-vertical-slice
    provides: strategy_runs.job_id UNIQUE FK (0020), JobFailureReason.CONFIG_INVALID, submit_job/depends_on plumbing
provides:
  - "Migration 0021: JobFailureReason.DOMAIN_CONFLICT enum value (D-04)"
  - "strategy_runs.job_id: UNIQUE dropped, non-unique ix_strategy_runs_job_id added (D-07) -- multiple StrategyRun rows may now link to one Job"
  - "market_data_ingestion_runs.job_id: nullable FK -> jobs.id (SET NULL) + index (D-07)"
  - "jobs.retry_of_job_id: nullable, UNIQUE, FK -> jobs.id (SET NULL) (D-16/D-17)"
  - "submit_job(retry_of_job_id=...) persists retry lineage on the Job insert"
  - "tests/test_phase20_operations_migration.py: 8 DB-level tests pinning every Phase 20 schema invariant"
affects: [20-*-retry-endpoint, 20-*-paper-session-job, 20-*-market-data-jobs, 20-*-runner-domain-conflict, 20-*-job-reads-resources-fix]

tech-stack:
  added: []
  patterns:
    - "Migration file mirrors 0020's op.f(...) naming-convention style for FK/UNIQUE/index names so downstream code (e.g. a future _is_named_uniqueness_error dispatch) can match on exact constraint names"
    - "When a migration-scoped test file's fixture is pinned to a pre-head revision, any Job row it needs must be inserted via raw SQL rather than the ORM Job class -- the ORM model always reflects current (head) code, so an ORM insert against an older schema explicitly omits/adds columns the DB does not yet have"

key-files:
  created:
    - alembic/versions/0021_phase20_operations_safety.py
    - tests/test_phase20_operations_migration.py
  modified:
    - src/trading_platform/db/models/job.py
    - src/trading_platform/db/models/strategy_run.py
    - src/trading_platform/db/models/market_data_ingestion_run.py
    - src/trading_platform/jobs/dependencies.py
    - tests/test_phase19_job_operations_migration.py
    - tests/test_db_migrations.py

key-decisions:
  - "OPS-07, OPS-08, OPS-05 stay Pending in REQUIREMENTS.md -- this plan ships schema + submit_job plumbing only, not the handler/runner/console behavior their literal text requires (17-01/19-01/19-03 precedent)"
  - "tests/test_phase19_job_operations_migration.py's fixture is pinned to revision 0020_phase19_job_operations (not head) so its uq_strategy_runs_job_id assertions stay meaningful; the new Phase 20 ORM/DB assertions live in test_phase20_operations_migration.py instead"

patterns-established:
  - "A migration-scoped test file that pins a pre-head revision must insert any Job row via raw SQL, not the ORM Job class, since the ORM always reflects the current head schema"

requirements-completed: []  # OPS-07/OPS-08/OPS-05 deliberately left Pending -- see key-decisions

# Metrics
duration: ~30min
completed: 2026-09-28
---

# Phase 20 Plan 01: Migration 0021 + Retry Lineage Summary

**Migration 0021 lands all four Phase 20 schema changes in one file (domain_conflict enum, non-unique strategy_runs.job_id index, market_data_ingestion_runs.job_id FK, jobs.retry_of_job_id UNIQUE FK), and `submit_job` now persists `retry_of_job_id` on insert.**

## Performance

- **Duration:** ~30 min
- **Tasks:** 2 completed
- **Files modified:** 7 (3 models + dependencies.py + 3 test files), 2 created (migration + new test file)

## Accomplishments
- Migration `0021_phase20_operations_safety` adds `JobFailureReason.DOMAIN_CONFLICT`, drops `uq_strategy_runs_job_id` in favor of `ix_strategy_runs_job_id`, adds `market_data_ingestion_runs.job_id` (FK + index), and adds `jobs.retry_of_job_id` (nullable, UNIQUE, FK) -- upgrade/downgrade verified round-trip clean against a real Postgres database.
- `Job`, `StrategyRun`, `MarketDataIngestionRun` ORM models updated to match; mapper configures cleanly with the new third self-referential FK on `Job`.
- `submit_job` accepts and persists `retry_of_job_id`.
- New `tests/test_phase20_operations_migration.py` (8 tests) pins every Phase 20 schema invariant at the DB layer: enum exact-set, index-not-unique, two `StrategyRun`s sharing one `job_id`, `market_data_ingestion_runs.job_id` FK/index/SET-NULL behavior, `uq_jobs_retry_of_job_id` IntegrityError with `constraint_name` check, `submit_job` persistence, downgrade-to-0020/re-upgrade-to-head, and ORM metadata.
- `tests/test_phase19_job_operations_migration.py` repinned to revision 0020 (its actual subject) since migration 0021 changes the schema it was asserting against.
- `tests/test_db_migrations.py`'s `job_failure_reason` exact-set assertion extended with `domain_conflict`; `jobs`/`market_data_ingestion_runs` column supersets extended with `retry_of_job_id`/`job_id`.

## Task Commits

1. **Task 1: Migration 0021 + ORM model edits** - `81c9038` (feat)
2. **Task 2: submit_job retry lineage + migration/constraint tests + update tests pinning the old schema** - `95b0eef` (feat)

## Files Created/Modified
- `alembic/versions/0021_phase20_operations_safety.py` - Migration: domain_conflict enum value, strategy_runs.job_id unique->index, market_data_ingestion_runs.job_id FK, jobs.retry_of_job_id UNIQUE FK
- `src/trading_platform/db/models/job.py` - `JobFailureReason.DOMAIN_CONFLICT`; `Job.retry_of_job_id` (nullable, unique, FK -> jobs.id SET NULL)
- `src/trading_platform/db/models/strategy_run.py` - `StrategyRun.job_id`: `unique=True` -> `index=True`
- `src/trading_platform/db/models/market_data_ingestion_run.py` - `MarketDataIngestionRun.job_id` added (nullable, indexed, FK -> jobs.id SET NULL)
- `src/trading_platform/jobs/dependencies.py` - `submit_job(retry_of_job_id=...)` threaded through `_submit_job_in_session` into the `Job(...)` insert
- `tests/test_phase20_operations_migration.py` - New: 8 DB-level tests for D-04/D-07/D-16/D-17
- `tests/test_phase19_job_operations_migration.py` - Fixture pinned to revision 0020; `_create_job()` switched to raw SQL insert; `column.unique is True` assertion removed
- `tests/test_db_migrations.py` - `job_failure_reason` exact-set + jobs/market_data_ingestion_runs column supersets extended

## Decisions Made
- OPS-07, OPS-08, OPS-05 are NOT marked complete in REQUIREMENTS.md. This plan ships only the shared schema spine (migration + models + `submit_job` kwarg); OPS-07's literal "operator retry" needs the retry API route and console UI, OPS-08's literal "domain conflict as distinct failure_reason" needs the `runner.py` translation branch (D-04's `JobDomainConflictError` mechanism, not built here), and OPS-05's three market-data Job types need their handlers. Matches the 17-01/19-01/19-03 precedent of leaving requirement IDs Pending until the plan that ships their end-to-end/operator-visible behavior.
- `tests/test_phase19_job_operations_migration.py`'s `migrated_phase19_db` fixture is now pinned to `"0020_phase19_job_operations"` rather than `"head"`, since this file's entire purpose is pinning migration 0020's own schema (the `uq_strategy_runs_job_id` UNIQUE constraint 0021 later drops). The Phase 20 head-state equivalents live in the new `test_phase20_operations_migration.py` instead.
- `_create_job()` in `test_phase19_job_operations_migration.py` was switched from an ORM `Job(...)` insert to a raw SQL `INSERT`, because the Phase-20-era `Job` ORM class always includes `retry_of_job_id` (value `NULL`) in its generated INSERT statement regardless of the target DB's revision -- at revision 0020 that column does not exist yet, so the ORM insert raised `UndefinedColumn`. This is a required fix, not a scope choice: any test file pinning a pre-head revision and needing a `Job` row must insert via raw SQL going forward.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `_create_job()` raw-SQL insert needed `result_summary` column**
- **Found during:** Task 2 (test run)
- **Issue:** `jobs.result_summary` is `NOT NULL` with no `server_default` (only a Python-side ORM `default=dict`); the raw-SQL `_create_job()` insert (see Decision above) omitted it, raising `NotNullViolation`.
- **Fix:** Added `result_summary` (cast to `JSON`, value `{}`) to the raw INSERT statement.
- **Files modified:** `tests/test_phase19_job_operations_migration.py`
- **Verification:** `tests/test_phase19_job_operations_migration.py` full file green (5/5)
- **Committed in:** `95b0eef` (Task 2 commit)

**2. [Rule 1 - Bug] `test_phase19_migration_downgrade_and_reupgrade`'s final re-upgrade target changed from `"head"` to `"0020_phase19_job_operations"`**
- **Found during:** Task 2 (planning, before running tests)
- **Issue:** The test downgrades to `0019` then re-upgrades to prove 0020's own reversibility; re-upgrading to `"head"` would now also apply migration 0021, which drops the very `uq_strategy_runs_job_id` constraint the test asserts is restored.
- **Fix:** Changed the re-upgrade target to `"0020_phase19_job_operations"`, keeping the test scoped to migration 0020's reversibility as originally intended.
- **Files modified:** `tests/test_phase19_job_operations_migration.py`
- **Verification:** Test passes; DB-level downgrade/re-upgrade round-trip for 0021 itself is separately covered by the new `test_phase20_downgrade_and_reupgrade` in `test_phase20_operations_migration.py`.
- **Committed in:** `95b0eef` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 - bugs found while making the plan's own instructed test changes work against a real database)
**Impact on plan:** Both fixes were necessary for the affected tests to pass; no scope creep beyond the plan's own `files_modified` list.

## Issues Encountered

- **Acceptance-criteria grep mismatch (not fixed, documented):** The plan's Task 2 acceptance criteria states `grep -c "retry_of_job_id=retry_of_job_id" src/trading_platform/jobs/dependencies.py` returns 1. Threading the parameter naturally through `submit_job`'s two call sites (caller-owned session, standalone session) into `_submit_job_in_session`'s `Job(...)` constructor produces 3 occurrences of that exact string (one per call site plus the `Job(...)` kwarg), not 1. Verified this is inherent to the existing `depends_on` plumbing pattern in the same function (which has the analogous multiplicity) rather than an implementation defect; did not force a single-line workaround to game the grep count, per the instruction to avoid cramming arguments to satisfy a literal count.

## User Setup Required

None - no external service configuration required. Note: any local Postgres database used for manual API/worker runs (not the throwaway test databases) will still be at revision 0020 until `alembic upgrade head` (or `scripts/migrate.py`) is run against it; this plan does not run that migration against any persistent local database.

## Next Phase Readiness

- Migration 0021 and all four Phase 20 schema invariants are in place and DB-verified (upgrade, downgrade, re-upgrade).
- `submit_job(retry_of_job_id=...)` is ready for the retry API route (D-16) to call.
- Full suite: 603 passed (baseline 595 + 8 new Phase 20 migration tests), 0 failed.
- Still open for later Phase 20 plans (per 20-PATTERNS.md): `jobs/runner.py`'s `JobDomainConflictError` -> `domain_conflict` outcome branch (OPS-08), `services/job_reads.py`'s `resources[]` query fix (`.scalar_one_or_none()` -> `.scalars().all()`, required once `paper-session` links 2 `StrategyRun` rows), the retry API route + `JobOrchestrationService.retry()` (OPS-07), and the 7 new Job types (OPS-05 market-data jobs among them).

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

- FOUND: alembic/versions/0021_phase20_operations_safety.py
- FOUND: tests/test_phase20_operations_migration.py
- FOUND: .planning/phases/20-complete-operation-migration-safety-controls/20-01-SUMMARY.md
- FOUND commit: 81c9038 (Task 1)
- FOUND commit: 95b0eef (Task 2)
- FOUND commit: e915d84 (docs: summary)

## Process Note

The final state-tracking commit (`c3703f8`, `docs(20-01): complete migration-0021-and-retry-lineage plan`) was made via `gsd-sdk query commit`, which has no argument for a trailer and so landed without the `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` line this session's attribution instructions require (same gap the 19-08 summary recorded). Per the git safety protocol's explicit preference for new commits over `--amend`, and the 19-08 precedent flagging amend-to-fix-a-trailer as itself a rule violation, this was disclosed rather than corrected via amend. No work was lost; this is a metadata-only gap on a docs-only tracking commit.
