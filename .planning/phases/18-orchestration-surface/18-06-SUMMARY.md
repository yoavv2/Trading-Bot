---
phase: 18-orchestration-surface
plan: 06
subsystem: job-orchestration-api
tags: [fastapi, postgresql, job-registry, worker, ast-boundaries, pytest]

requires:
  - phase: 18-orchestration-surface
    provides: idempotent Job mutation service, HTTP adapters, and worker CLI boundaries
  - phase: 17-job-framework
    provides: Job registry, worker runner, durable lifecycle, and linked read endpoints
provides:
  - database-required mutation API startup with injectable test registry
  - test-only HTTP submit-to-worker-to-observation E2E proof
  - runtime schema-mutation and Phase 19 scope fences
affects: [phase-19-operation-triggers, api-startup, worker-cli, jobs]

tech-stack:
  added: []
  patterns: [lifespan startup preflight, injected JobRegistry, linked Job observation, AST runtime denylist]

key-files:
  created:
    - tests/test_job_mutation_e2e.py
  modified:
    - src/trading_platform/api/app.py
    - tests/test_app_boot.py
    - tests/test_startup_validation.py
    - tests/test_orchestration_boundaries.py
    - tests/test_db_migrations.py

key-decisions:
  - "The mutation-capable API performs required PostgreSQL preflight before creating registry or boot state."
  - "The Phase 18 execution proof uses one test-local handler/spec registry while the production registry remains empty."

patterns-established:
  - "API E2E tests inject the exact JobRegistry instance into create_app and run_worker_loop."
  - "AST fences scan explicit runtime package roots for schema mutation and fixed-base scope violations."

requirements-completed: [ORCH-01, ORCH-02, ORCH-03, ORCH-04]
duration: 20min
completed: 2026-07-21
---

# Phase 18 Plan 06: Mutation-Ready Startup and E2E Proof Summary

**Database-gated FastAPI Job mutations with a test-only registry completing POST submission, worker execution, and linked terminal observation.**

## Performance

- **Duration:** 20 min
- **Started:** 2026-07-21T18:36:00Z
- **Completed:** 2026-07-21T18:56:05Z
- **Tasks:** 3/3
- **Files modified:** 7

## Accomplishments

- Made API startup fail before serving when PostgreSQL is unavailable, while preserving explicit test registry injection and an empty production default registry.
- Added a real migrated-PostgreSQL E2E test that rejects invalid payloads before persistence, replays an idempotent accepted submission, executes one handler, and follows all compact-reference links.
- Added deterministic AST/runtime gates for schema-mutation calls, Alembic subprocess literals, Phase 19 handler registrations, and console scope changes.

## Task Commits

Each task was committed atomically:

1. **Task 1: Require database-ready API startup and support explicit test registry injection** - `c55945f` (feat)
2. **Task 2: Prove POST submission through worker execution and linked terminal observation** - `ce29220` (test)
3. **Task 3: Run phase-wide contract, security, boundary, and scope-fence verification** - `7f9eff8` (test)

## Files Created/Modified

- `src/trading_platform/api/app.py` - Requires PostgreSQL before boot and retains an injected/default JobRegistry through lifespan.
- `tests/test_app_boot.py` - Pins the startup call and injected registry identity.
- `tests/test_startup_validation.py` - Replaces the retired command test with retained `run-jobs` startup gating.
- `tests/test_job_mutation_e2e.py` - Proves type-specific validation, idempotent replay, worker execution, logs/events/progress, and linked reads.
- `tests/test_orchestration_boundaries.py` - Enforces closed runtime schema mutation and Phase 19 scope denylist checks.
- `tests/test_db_migrations.py` - Updates legacy readiness coverage for database-required API startup.
- `.planning/phases/18-orchestration-surface/deferred-items.md` - Records unrelated repository-wide Ruff violations.

## Decisions Made

- API lifespan now constructs the empty default Job registry only after successful database preflight; callers can inject the same test registry used by the worker.
- The E2E handler and submission specification remain exclusively in the test module, preserving the Phase 19 production-handler handoff.

## Verification

- `pytest tests/test_job_mutation_migration.py tests/test_job_dependencies.py tests/test_job_cancellation.py tests/test_job_orchestration.py tests/test_job_api.py tests/test_job_mutation_api.py tests/test_orchestration_boundaries.py tests/test_job_import_boundary.py tests/test_job_registry.py tests/test_job_runner.py tests/test_job_mutation_e2e.py tests/test_app_boot.py tests/test_startup_validation.py tests/test_operator_controls.py tests/test_concurrency_guard_e2e.py -x -q` — 176 passed.
- `pytest -x -q` — 500 passed.
- Configured `mypy` scope — 51 source files, no issues.
- Owned-file Ruff checks and formatting — passed.
- Repository-wide `ruff check .` remains blocked by 12 unrelated historical migration/script violations recorded in `deferred-items.md`; no Phase 18 files caused those findings.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated the legacy readiness test for mutation-ready startup**
- **Found during:** Task 3
- **Issue:** `tests/test_db_migrations.py` expected the retired DB-optional API lifespan and attempted to query `/ready` after an unreachable-database startup.
- **Fix:** Asserted lifespan exits before `bootstrapped` state when the DB port is unreachable.
- **Files modified:** `tests/test_db_migrations.py`
- **Verification:** Targeted regression passed; full suite passed (500 tests).
- **Committed in:** `7f9eff8`

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** The update aligns existing coverage with the required mutation-capable startup invariant without changing production scope.

## Known Stubs

None.

## Issues Encountered

- Repository-wide Ruff reports 12 pre-existing violations in historical Alembic and script files outside this plan's scope. They are recorded for follow-up; tests, owned lint checks, and configured mypy are green.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 19 can register real operation handlers through the tested HTTP/worker path without changing the generic Job framework.
- Production registry stays empty and runtime schema mutation remains structurally blocked until that work is explicitly planned.

## Self-Check: PASSED

- Confirmed `src/trading_platform/api/app.py`, `tests/test_job_mutation_e2e.py`, and `tests/test_orchestration_boundaries.py` exist.
- Confirmed task commits `c55945f`, `ce29220`, and `7f9eff8` exist in Git history.

---
*Phase: 18-orchestration-surface*
*Completed: 2026-07-21*
