---
phase: 18-orchestration-surface
plan: 01
subsystem: database
tags: [postgresql, sqlalchemy, alembic, idempotency, jobs]

requires:
  - phase: 17-job-framework
    provides: jobs table and generic Job persistence model
provides:
  - endpoint-scoped durable idempotency identities linked to original Jobs
  - reversible PostgreSQL schema enforcement for Job mutation keys
affects: [18-02, 18-03, job-orchestration]

tech-stack:
  added: []
  patterns: [named database uniqueness backstop, real-PostgreSQL migration reversibility tests]

key-files:
  created:
    - src/trading_platform/db/models/job_mutation.py
    - alembic/versions/0019_phase18_job_idempotency.py
    - tests/test_job_mutation_migration.py
  modified:
    - src/trading_platform/db/models/__init__.py

key-decisions:
  - "Use a separate job_mutations table with endpoint/key uniqueness rather than an application-side lookup."
  - "Use RESTRICT on the Job foreign key so replay and conflict identities always retain their original Job link."

patterns-established:
  - "Mutation identities are endpoint-scoped, not globally keyed."
  - "Concurrent uniqueness proof holds one PostgreSQL transaction while a second connection attempts the duplicate insert."

requirements-completed: [ORCH-03]

duration: 12min
completed: 2026-07-21
---

# Phase 18 Plan 01: Durable Job Mutation Schema Summary

**PostgreSQL-backed endpoint-scoped Job mutation identities with a named uniqueness constraint, RESTRICT Job linkage, and reversible Alembic schema.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-07-21T17:01:51Z
- **Completed:** 2026-07-21T17:08:46Z
- **Tasks:** 2/2
- **Files modified:** 4

## Accomplishments

- Added the `JobMutation` ORM model with bounded endpoint/key/fingerprint fields and a non-null original Job reference.
- Added reversible Alembic revision `0019_phase18_job_idempotency` without altering existing Job tables or data.
- Proved schema shape, two-connection duplicate rejection, endpoint scoping, foreign-key enforcement, downgrade, and re-upgrade against PostgreSQL.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add the JobMutation model and reversible revision 0019** - `667af79` (feat)
2. **Task 2: Prove schema shape, concurrent uniqueness, and reversibility** - `c1e6629` (test)

## Files Created/Modified

- `src/trading_platform/db/models/job_mutation.py` - Durable endpoint-scoped mutation identity model.
- `src/trading_platform/db/models/__init__.py` - Exports `JobMutation` for ORM metadata discovery.
- `alembic/versions/0019_phase18_job_idempotency.py` - Creates and reverses the isolated mutation table.
- `tests/test_job_mutation_migration.py` - Real-PostgreSQL schema, constraint, concurrency, FK, and reversibility proof.

## Decisions Made

- Used a separate `job_mutations` table and the named `uq_job_mutations_endpoint_key` database constraint as the authoritative concurrency backstop.
- Scoped keys by stable mutation endpoint IDs, allowing the same client key on submission and cancellation routes.
- Used `ondelete="RESTRICT"` so a durable idempotency identity cannot lose the original Job used in replay or conflict responses.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Repaired the local pre-commit entrypoint after repository relocation**
- **Found during:** Task 1 commit
- **Issue:** The local `.venv/bin/pre-commit` launcher referenced the old repository path and could not start Python.
- **Fix:** Updated the ignored local launcher path to the active repository location; no tracked project files or dependencies changed.
- **Verification:** Both task commits completed through the configured pre-commit hooks.

---

**Total deviations:** 1 auto-fixed (1 blocking environment repair)
**Impact on plan:** No source-scope change; the repair only enabled the required hooks to run.

## Issues Encountered

- Ruff sorted the new model export and formatted the new test/migration files before their respective verification and commits.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plans 18-02 and 18-03 can compose their orchestration service with an atomic endpoint/key persistence backstop.
- The migration is isolated, reversible, and leaves existing Phase 17 Job tables unchanged.

## Self-Check: PASSED

- Confirmed all four plan artifacts exist.
- Confirmed task commits `667af79`, `c1e6629`, and summary commit `f7ba426` exist.

---
*Phase: 18-orchestration-surface*
*Completed: 2026-07-21*
