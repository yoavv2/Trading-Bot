---
phase: 13-console-foundation-and-system-status
plan: 01
subsystem: api
tags: [fastapi, kill-switch, operator-reads, pytest]

# Dependency graph
requires:
  - phase: 07-correctness-kernel-and-live-adapters (v1.1)
    provides: "Persistent global kill switch (system_controls table) and OperatorReadService.get_kill_switch_state() / OperatorControlService.trip_kill_switch()/reset_kill_switch()"
provides:
  - "GET /api/v1/system/kill-switch HTTP route exposing the persisted kill-switch state"
  - "Route-level pytest covering armed and tripped states plus response shape"
affects: [13-02, 13-03, 13-04, 16-analytics-and-charting]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Thin route pattern: FastAPI route depends on existing service via Depends(get_operator_read_service), returns service dict verbatim, maps LookupError to HTTP 503"

key-files:
  created: []
  modified:
    - src/trading_platform/api/routes/system.py
    - tests/test_api_reads.py

key-decisions:
  - "Route returns the OperatorReadService.get_kill_switch_state() payload with zero reshaping — no caching, no extra fields — to keep the backend change minimal and auditable per the approved exception (ROADMAP Known Gaps #1)."
  - "LookupError from a missing system_controls row maps to HTTP 503 with the service's descriptive detail message rather than a 200 with fabricated defaults, per must_haves truth #3."

patterns-established:
  - "Thin operator-read route pattern: no business logic in the route, just Depends() + verbatim dict return + LookupError→503 mapping. Reusable for future single-route backend exceptions."

requirements-completed: [STAT-03, KILL-01]

# Metrics
duration: 6min
completed: 2026-07-08
---

# Phase 13 Plan 01: Kill-Switch Read Route Summary

**Added the single approved backend change for v1.2 — a thin `GET /api/v1/system/kill-switch` route wired to the existing `OperatorReadService.get_kill_switch_state()`, verified with a route-level TDD test covering armed and tripped states.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-07-08T07:21:01Z (first test run)
- **Completed:** 2026-07-08T07:21:52Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 2

## Accomplishments
- `GET /api/v1/system/kill-switch` now exists and returns the exact `get_kill_switch_state()` payload (name, state, is_tripped, last_changed_at, last_change_actor, last_change_reason, last_change_run_id)
- Route correctly reflects state transitions: armed by default (post-migration), tripped after `OperatorControlService.trip_kill_switch()`
- Missing `system_controls` row maps to HTTP 503 with descriptive detail (never a fabricated 200)
- Route-level pytest (`test_system_kill_switch_route_reports_persisted_state`) added following the existing file's fixture/client pattern

## Task Commits

Each task was committed atomically (TDD: test → feat):

1. **Task 1 (RED): Add failing kill-switch route test** - `72fc712` (test)
2. **Task 1 (GREEN): Implement kill-switch route** - `bb9b234` (feat)

**Plan metadata:** _pending — this commit_

_No refactor step was needed; the implementation was a small, clean addition matching the plan's exact code snippet._

## Files Created/Modified
- `src/trading_platform/api/routes/system.py` - Added `GET /system/kill-switch` route: depends on `OperatorReadService` via existing `get_operator_read_service`, returns the service dict verbatim, maps `LookupError` to HTTP 503
- `tests/test_api_reads.py` - Added `test_system_kill_switch_route_reports_persisted_state` and the `OperatorControlService` import needed to drive the tripped-state assertion

## Decisions Made
- Returned the service payload with no reshaping/caching to honor the narrow scope of the approved backend exception (ROADMAP Known Gaps #1) — no new fields, no CORS, no other route touched.
- Used the existing `migrated_analytics_db` fixture and `_build_client()` helper rather than introducing new test infrastructure, consistent with the file's established pattern.

## Deviations from Plan

None - plan executed exactly as written. The route implementation matches the plan's provided code snippet verbatim; the test follows the plan's two-part behavior spec (armed default, then tripped after `trip_kill_switch()`).

## Issues Encountered
None. RED step confirmed a 404 (route not yet registered) as expected before implementation; GREEN step passed on first run of the full `tests/test_api_reads.py` suite (5/5 passed), so no additional debugging was required.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- The kill-switch state is now reachable over HTTP, unblocking the console's global kill-switch banner (KILL-01) and system status display (STAT-03) planned in later 13-0x plans.
- `git diff --stat` confirms changes were confined to `src/trading_platform/api/routes/system.py` and `tests/test_api_reads.py` — no other backend file was touched, preserving the narrow scope of the approved exception.
- No blockers for subsequent Phase 13 plans (13-02, 13-03, 13-04) which can now build the console frontend against this route.

---
*Phase: 13-console-foundation-and-system-status*
*Completed: 2026-07-08*

## Self-Check: PASSED

- FOUND: .planning/phases/13-console-foundation-and-system-status/13-01-SUMMARY.md
- FOUND: 72fc712 (test commit)
- FOUND: bb9b234 (feat commit)
