---
phase: 17-job-framework
plan: 01
subsystem: database
tags: [sqlalchemy, alembic, postgresql, orm, enums]

# Dependency graph
requires: []
provides:
  - "Job ORM model with closed JobStatus enum, lease/heartbeat, terminal-outcome, cancellation, causal-chain, and progress columns"
  - "JobFailureReason and JobCancellationCause stable code vocabulary (single definition site)"
  - "JobDependency self-referential edge model with self-dependency CheckConstraint backstop"
  - "JobEvent append-only lifecycle/cancellation audit model (JobEventType, JobTransitionOutcome)"
  - "JobLog append-only structured log model with sequence-based deterministic ordering"
  - "Alembic migration 0018 creating jobs/job_dependencies/job_events/job_logs with 5 native PostgreSQL enum types"
  - "Migration enforcement tests proving JOB-01 closed-enum and D-06 self-dependency rejection at the database level"
affects: [17-02, 17-03, 17-04, 17-05, 17-06, 17-07, 17-08, 17-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "StrEnum + values_callable=_enum_values + validate_strings=True for closed-enum ORM columns (reused from strategy_run.py)"
    - "Explicit postgresql.ENUM(...).create(bind, checkfirst=False) once per type, then create_type=False on every reusing column/table in the migration (reused from 0013_phase7_order_state_kernel.py)"
    - "self-referential FK + relationship(remote_side=..., foreign_keys=[...]) for two same-table FKs on one row (reused from paper_order.py's supersedes_paper_order_id)"

key-files:
  created:
    - src/trading_platform/db/models/job.py
    - src/trading_platform/db/models/job_dependency.py
    - src/trading_platform/db/models/job_event.py
    - src/trading_platform/db/models/job_log.py
    - alembic/versions/0018_phase17_job_framework.py
  modified:
    - src/trading_platform/db/models/__init__.py
    - tests/test_db_migrations.py

key-decisions:
  - "job_status enum type is created once explicitly and reused with create_type=False across 4 columns on 2 tables (jobs.status, jobs.blocking_job_status, job_events.from_status, job_events.to_status) to avoid DuplicateObject on repeat CREATE TYPE"
  - "job_events.terminal_cause is a plain String(64), not an enum, so one column can carry either a JobFailureReason or a JobCancellationCause value"
  - "JSON not-null columns (payload, result_summary, details, context) get a table-creation-time server_default '{}'::json that is immediately dropped via alter_column, matching the 0009/0013 migration convention exactly"
  - "The enum-isolation test inserts raw SQL with status='stale' (a valid StrategyRunStatus value, invalid JobStatus value) to prove PostgreSQL enum type isolation, not just Python-side validate_strings rejection"

requirements-completed: []  # Plan frontmatter lists JOB-01/05/06/07, but this plan ships only the persistence foundation (schema, models, migration). None of these requirements describe schema alone -- each names runtime behavior (execution, dependency gating, cancellation action, API observation) that lands in later Phase 17 plans. Deliberately left Pending in REQUIREMENTS.md to avoid overclaiming; see "Requirements Frontmatter Discrepancy" note below.

# Metrics
duration: ~20min
completed: 2026-07-19
---

# Phase 17 Plan 01: Job Framework Persistence Foundation Summary

**Four Job ORM models (Job, JobDependency, JobEvent, JobLog) plus Alembic migration 0018 materializing jobs/job_dependencies/job_events/job_logs with five native PostgreSQL enum types, enforcement-tested for the closed five-state lifecycle and the self-dependency backstop.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 3 completed
- **Files modified:** 7 (5 created, 2 modified)

## Accomplishments
- `Job` model carries the full lifecycle (`JobStatus`), lease/heartbeat claim fields, terminal-outcome fields (`JobFailureReason`, `outcome_uncertain`), cancellation history, dependency causal chain (blocking/root-cause job linkage), and a generic progress snapshot — all in one operation-agnostic table.
- `JobStatus`, `JobFailureReason`, `JobCancellationCause` defined exactly once in `job.py` as the single vocabulary source for the rest of Phase 17.
- `JobDependency`, `JobEvent` (with `JobEventType`/`JobTransitionOutcome`), and `JobLog` complete the four-table persistence surface, all exported from the models barrel.
- Migration 0018 (`down_revision = "0017_phase11_query_perf_indices"`, 26-char revision id, within the 32-char limit) creates all four tables with five native enum types, verified reversible (`upgrade head` → `downgrade -1` → `upgrade head` clean against a throwaway database).
- Two enforcement tests: `test_alembic_upgrade_creates_phase17_job_tables` proves the `job_status`/`job_failure_reason`/`job_cancellation_cause` enum label sets are exactly correct and that an out-of-set literal (`'stale'`, borrowed from `StrategyRunStatus`) is rejected by PostgreSQL itself; `test_phase17_job_dependency_rejects_self_edge` proves the D-06 self-dependency CheckConstraint backstop fires at the database layer.

## Task Commits

Each task was committed atomically:

1. **Task 1: Define the Job code vocabulary and the Job ORM model** - `0551781` (feat)
2. **Task 2: Add the dependency, event, and log models and export them** - `9275530` (feat)
3. **Task 3: Alembic migration and migration enforcement test** - `b27fd08` (feat)

_No separate plan-metadata commit yet — this commit follows this SUMMARY.md's own creation._

## Files Created/Modified
- `src/trading_platform/db/models/job.py` - Job ORM model + JobStatus/JobFailureReason/JobCancellationCause enums (single vocabulary definition site)
- `src/trading_platform/db/models/job_dependency.py` - JobDependency self-referential edge model with self-dependency CheckConstraint backstop
- `src/trading_platform/db/models/job_event.py` - JobEvent append-only audit model + JobEventType/JobTransitionOutcome enums
- `src/trading_platform/db/models/job_log.py` - JobLog append-only structured log model with sequence-based ordering
- `src/trading_platform/db/models/__init__.py` - Barrel exports for all ten new names
- `alembic/versions/0018_phase17_job_framework.py` - Migration creating all four tables + five native enum types
- `tests/test_db_migrations.py` - Two Phase 17 enforcement tests

## Decisions Made
- Reused the `job_status` PostgreSQL enum type across all four columns that reference it (rather than creating four separate near-duplicate types), matching how `order_lifecycle_state` is reused across `paper_orders.status`/`order_events.from_state`/`order_events.to_state` in the existing codebase.
- `job_events.terminal_cause` is a plain `String(64)` rather than a third enum union type, since a single column needs to carry either a `JobFailureReason` or a `JobCancellationCause` value and PostgreSQL has no native sum-type mechanism for two enums in one column.
- The DB-level enum-rejection test uses a raw parameterized `INSERT` (not the ORM) so the assertion proves PostgreSQL's native type system rejects the literal, independent of SQLAlchemy's Python-side `validate_strings=True` check.

## Deviations from Plan

None - plan executed exactly as written. All acceptance criteria in Tasks 1-3 verified directly (enum membership, table/column/constraint presence, FK ondelete behavior, migration reversibility, both enforcement tests green, full `test_db_migrations.py` suite holds at 13/13 with no regressions).

## Requirements Frontmatter Discrepancy

This plan's frontmatter lists `requirements: [JOB-01, JOB-05, JOB-06, JOB-07]`, but none of these four requirements are satisfied by a persistence-only foundation plan:

- **JOB-01** ("every long-running operation executes as a Job... no state outside the enum is representable") — the closed-enum sub-claim is now proven at the DB level, but no operation executes as a Job yet; there is no queue, worker, or orchestration.
- **JOB-05** ("a dependent Job starts only after all dependencies succeed... failed dependency moves dependents to a terminal state") — this plan ships the `job_dependencies` edge table and the self-edge CheckConstraint backstop only; dependency-gated execution and cascade-cancellation logic (D-04) land in a later plan (`jobs/dependencies.py` per PATTERNS.md).
- **JOB-06** ("Operator can cancel a queued or running Job... audited") — this plan ships the cancellation columns and `job_events` audit table only; the cancellation action path (`jobs/cancellation.py`) lands in a later plan.
- **JOB-07** ("progress and structured logs observable via the API") — this plan ships the `job_logs`/progress columns only; the read-only API route (`api/routes/jobs.py`) lands in a later plan.

Per the 16-02/16-01 and 11-03 precedents in STATE.md (do not mark a requirement Complete until the behavior it describes is actually verifiable), `requirements mark-complete` was deliberately skipped for this plan. All four remain `Pending` in `REQUIREMENTS.md`. A blocker note has been added to STATE.md flagging this frontmatter/scope mismatch so a later Phase 17 plan (or `/gsd-transition`) marks each ID complete once its actual behavior ships and is verified.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All four Job tables, the closed `JobStatus` enum, and the stable `JobFailureReason`/`JobCancellationCause` vocabulary are live in PostgreSQL and importable from `trading_platform.db.models`.
- Downstream Phase 17 plans (registry, lifecycle transitions, queue/lease claim, dependencies validation, cancellation, progress/logging, API read routes) can now import `Job`, `JobDependency`, `JobEvent`, `JobLog` and their enums directly rather than redefining any vocabulary.
- No code blockers identified for 17-02 onward. See "Requirements Frontmatter Discrepancy" above for a tracking-only blocker (JOB-01/05/06/07 left Pending, needs a later plan or transition to mark complete once their behavior ships).

---
*Phase: 17-job-framework*
*Completed: 2026-07-19*

## Self-Check: PASSED

All created files verified present on disk; all four task/summary commit hashes (0551781, 9275530, b27fd08, f948341) verified present in git log.
