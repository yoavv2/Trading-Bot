---
phase: 08-concurrency-guard
plan: 04
subsystem: execution
tags: [postgresql, advisory-lock, concurrency, sqlalchemy, paper-trading]

# Dependency graph
requires:
  - phase: 08-concurrency-guard (08-02)
    provides: session_run_lock()/ConcurrentRunLockedError advisory-lock primitive (LOCK-01, LOCK-06)
  - phase: 08-concurrency-guard (08-03)
    provides: find_stale_runs()/reclaim_stale_runs() stale-run detector + audited reclaim (LOCK-04, LOCK-05)
provides:
  - run_paper_order_submission() now acquires the (strategy_id, session_date) advisory lock before any write or broker call
  - StrategyRun row created at status=RUNNING as the literal first persisted write for a run (removed pre-lock PENDING insert)
  - Stale predecessor reclaim wired in immediately after the running row exists, before kill-switch/control-state checks
  - Lock-loser path proven to write zero rows and make zero broker calls; lock proven released after a kill-switch-blocked run finalizes
affects: [08-05, run_paper_session, submit-paper-orders CLI, worker paper-session commands]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lock-guarded entrypoint pattern: thin public function resolves pure state, wraps a private *_guarded() helper in `with session_run_lock(...)`, and catches only ConcurrentRunLockedError around the `with` (never inside it) so the loser writes nothing"
    - "Running-row-first: the first DB write inside a guarded region is the terminal-shape row (status=RUNNING) itself, not a placeholder PENDING row later transitioned"

key-files:
  created: []
  modified:
    - src/trading_platform/services/paper_execution.py
    - tests/test_paper_execution.py

key-decisions:
  - "Extracted _run_paper_order_submission_guarded() as a separate module-level function containing the lock-body rather than nesting ~350 lines under one `with` block, to avoid a large re-indentation diff while keeping the same invariants (lock-before-writes, running-row-first, reclaim-after-running-write, durable independent commits)"
  - "_create_paper_execution_run() dropped its strategy_status parameter entirely (was previously required) since strategy_status is genuinely unknown before the lock+running-row-write+reclaim sequence completes; the row is created with 'unknown' omitted from parameters_snapshot/result_summary and the accurate value is written into result_summary by the very next update call minutes later, once control_state is loaded. No test or downstream consumer read parameters_snapshot.strategy_status, so this is a clean removal, not a behavior gap."
  - "Kept the exact task-specified ordering (running row -> reclaim -> load kill-switch/control state -> blocked branches -> running result_summary update -> broker loop) rather than loading control state earlier, even though only the running-row-first and reclaim-after-running-write orderings are hard invariants per must_haves; this keeps the implementation literally matching the reviewed plan text."

requirements-completed: [LOCK-01, LOCK-02, LOCK-03, LOCK-05]

# Metrics
duration: ~25min
completed: 2026-07-13
---

# Phase 8 Plan 04: Lock-Guard and Reorder run_paper_order_submission Summary

**Reordered `run_paper_order_submission` so the advisory lock (08-02) is acquired before any write, the first persisted write is a `running` StrategyRun row, and stale predecessors (08-03) are reclaimed immediately after that row exists — closing the pre-lock PENDING-insert and pre-lock kill-switch-check gaps that made LOCK-01/02/03/05 false before this plan.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-07-13T06:05:00Z (approx, from session start)
- **Completed:** 2026-07-13T06:34:53Z
- **Tasks:** 2 completed
- **Files modified:** 2

## Accomplishments
- `run_paper_order_submission` now wraps its entire side-effecting body in `session_run_lock(strategy_id, session_date)`; a concurrent attempt for the same tuple raises `ConcurrentRunLockedError` before any write or broker call, and the caller/CLI (08-05) can map it to `CONCURRENT_RUN_LOCK_EXIT_CODE`.
- The StrategyRun row is now inserted directly at `status=RUNNING` — the literal first persisted write for a run — replacing the old pre-lock `PENDING` insert followed by a later `RUNNING` transition.
- `reclaim_stale_runs()` runs immediately after that running row exists, in its own committed `session_scope`, so a crashed predecessor for the same tuple is marked `STALE` with an audited `ExecutionEvent` while the fresh row (started just now, inside the timeout window) can never self-reclaim.
- Kill-switch and strategy-disabled checks now run strictly after lock acquisition, row creation, and reclaim — provably post-lock, not pre-lock as before.
- Three new integration tests prove: (1) the lock loser writes zero rows and makes zero broker calls, (2) a stale predecessor is reclaimed to STALE while the new run's own row is not, and (3) the lock is free for a subsequent acquisition after a kill-switch-blocked run finalizes (LOCK-06 release proof).

## Task Commits

Each task was committed atomically:

1. **Task 1: Lock-guard and reorder run_paper_order_submission** - `13a6025` (feat)
2. **Task 2: Concurrency integration tests for the guarded entrypoint** - `bd973a7` (test)

## Files Created/Modified
- `src/trading_platform/services/paper_execution.py` - `run_paper_order_submission` is now a thin lock-acquiring wrapper around a new `_run_paper_order_submission_guarded()` helper containing the reordered guarded body; `_create_paper_execution_run()` now inserts `status=RUNNING` directly and no longer takes a `strategy_status` parameter.
- `tests/test_paper_execution.py` - Added `session_run_lock`/`ConcurrentRunLockedError` imports and `timedelta` import; added three integration tests covering the lock-loser-writes-nothing, running-row-first-with-stale-reclaim, and kill-switch-post-lock-with-lock-release invariants.

## Decisions Made
- See `key-decisions` in frontmatter above (helper-function extraction to avoid re-indentation churn; dropping `strategy_status` from run creation since it is genuinely unknown at that point; keeping the plan's exact ordering for kill-switch/control-state loading relative to reclaim).

## Deviations from Plan

None — plan executed exactly as written. The `_create_paper_execution_run` signature change (dropping `strategy_status`) was required by the plan's own explicit reordering instruction ("Do NOT load kill-switch/control state yet" before the lock, and "AFTER reclaim, load kill-switch + control state"), not an independent deviation.

## Issues Encountered
None. All 20 tests in `tests/test_paper_execution.py` pass, including the 17 pre-existing tests (no regressions) and the 3 new concurrency tests. Full repo suite (167 tests) passes.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
`run_paper_order_submission` is now guarded end-to-end (lock-before-side-effects, running-row-first, clean typed denial for the loser, stale reclaim after the run row exists). `run_paper_session` and the `submit-paper-orders`/worker CLI entrypoints inherit the guard automatically since they call this function — but none of them currently catch `ConcurrentRunLockedError`, so a lock denial today propagates as an unhandled exception with a generic traceback/exit code. CLI exit-code mapping to `CONCURRENT_RUN_LOCK_EXIT_CODE` and the crash/restart end-to-end proof are explicitly deferred to 08-05 per this plan's success criteria — no blocker, this is in-scope for that plan.

---
*Phase: 08-concurrency-guard*
*Completed: 2026-07-13*

## Self-Check: PASSED

- FOUND: src/trading_platform/services/paper_execution.py
- FOUND: tests/test_paper_execution.py
- FOUND: .planning/phases/08-concurrency-guard/08-04-SUMMARY.md
- FOUND commit: 13a6025 (feat(08-04): lock-guard and reorder run_paper_order_submission)
- FOUND commit: bd973a7 (test(08-04): concurrency integration tests for the guarded entrypoint)
