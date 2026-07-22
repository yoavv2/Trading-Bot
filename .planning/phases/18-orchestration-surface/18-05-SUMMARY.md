---
phase: 18-orchestration-surface
plan: 05
subsystem: worker-cli-boundaries
tags: [python, argparse, fastapi, postgresql, architecture-tests, jobs]

requires:
  - phase: 18-orchestration-surface
    provides: thin idempotent Job mutation routes and an intentionally empty production Job registry
  - phase: 17-job-framework
    provides: queue worker loop, registry, and domain-service import boundary
provides:
  - exact infrastructure/read-only worker CLI whitelist with direct mutation commands removed
  - AST and runtime fences for Job mutation routes, adapters, orchestration, services, and registry boundaries
  - service-level operator-control and lock-contention safety coverage
affects: [18-06, operation-triggers, worker-cli, job-api]

tech-stack:
  added: []
  patterns: [exact CLI allowlists, AST adapter-boundary tests, service-level safety invariants]

key-files:
  created:
    - tests/test_orchestration_boundaries.py
  modified:
    - src/trading_platform/worker/parser.py
    - src/trading_platform/worker/commands/__init__.py
    - src/trading_platform/worker/__main__.py
    - tests/test_operator_controls.py
    - tests/test_concurrency_guard_e2e.py

key-decisions:
  - "The worker CLI exposes only serve, run-jobs, and read/report commands; all direct manual mutation entries are absent from parser and dispatch."
  - "Retired CLI behavior is tested through durable domain-service invariants rather than preserving unreachable command handlers."

patterns-established:
  - "Boundary tests combine AST source assertions with effective FastAPI route aggregation so included routers cannot evade mutation allowlists."
  - "Retained run-jobs remains a routing adapter over run_worker_loop, while production Job registry registration remains deferred to Phase 19."

requirements-completed: [ORCH-01, ORCH-02]
duration: 7min
completed: 2026-07-21
---

# Phase 18 Plan 05: Worker CLI Boundary Summary

**HTTP-only Job orchestration protected by an exact worker CLI allowlist, adapter architecture fences, and service-level control/concurrency safety tests.**

## Performance

- **Duration:** 7 min
- **Started:** 2026-07-21T18:15:25Z
- **Completed:** 2026-07-21T18:22:20Z
- **Tasks:** 3/3
- **Files modified:** 6

## Accomplishments

- Removed every direct mutating/manual worker command from parser construction, command dispatch, and entrypoint routing while retaining `serve`, `run-jobs`, and report/status commands.
- Added non-emptiable AST and runtime checks that pin the two Job POST endpoints, adapter imports, orchestration dependencies, service-boundary scope, thin worker adapter, and empty production registry.
- Migrated legacy operator-control and lock-contention coverage away from removed CLI handlers without weakening persistence, typed-error, zero-side-effect, broker, or crash-reclaim invariants.

## Task Commits

Each task was committed atomically:

1. **Task 1: Remove mutating CLI registrations and retain the exact infrastructure/read whitelist** - `1840a5d` (feat)
2. **Task 2: Enforce adapter and allowed-layer boundaries with non-emptiable AST/runtime checks** - `74abd51` (test)
3. **Task 3: Migrate legacy CLI-coupled safety tests to service-level invariants** - `aea8725` (test)

## Files Created/Modified

- `src/trading_platform/worker/parser.py` - Exposes only the approved worker CLI commands.
- `src/trading_platform/worker/commands/__init__.py` - Dispatches only report/status and generic worker-loop adapters.
- `src/trading_platform/worker/__main__.py` - Retains only the `serve` special case and generic dispatch routing.
- `tests/test_orchestration_boundaries.py` - Mechanically enforces CLI, route, adapter, orchestration, and registry boundaries.
- `tests/test_operator_controls.py` - Keeps control-service persistence and audit coverage independent of retired CLI paths.
- `tests/test_concurrency_guard_e2e.py` - Proves typed lock contention with no writes or fake broker submissions at the service boundary.

## Decisions Made

- Direct mutation commands are removed from every callable worker CLI surface rather than replaced with aliases or adapter-local business logic.
- Existing safety assertions now invoke their public service contracts directly, preserving historical correctness evidence after command removal.

## Verification

- `pytest tests/test_orchestration_boundaries.py tests/test_job_import_boundary.py tests/test_job_registry.py tests/test_operator_controls.py tests/test_concurrency_guard_e2e.py -x -q` — 73 passed.
- `ruff check src/trading_platform/worker tests/test_orchestration_boundaries.py tests/test_operator_controls.py tests/test_concurrency_guard_e2e.py` — passed.
- `mypy src/trading_platform/worker` — passed with no issues.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 19 can add production operation handlers only through the HTTP Job orchestration surface; the default registry and retained worker CLI are mechanically fenced against bypasses.
- Phase 18 Plan 06 can build on the stable boundary suite without weakening the domain-service import prohibition.

## Self-Check: PASSED

- Confirmed all six owned source/test files exist.
- Confirmed task commits `1840a5d`, `74abd51`, and `aea8725` exist in Git history.

---
*Phase: 18-orchestration-surface*
*Completed: 2026-07-21*
