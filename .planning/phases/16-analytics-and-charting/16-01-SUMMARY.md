---
phase: 16-analytics-and-charting
plan: 01
subsystem: api
tags: [analytics, backtest-reporting, fastapi, sqlalchemy, pytest]

# Dependency graph
requires:
  - phase: 16-02
    provides: EquityCurveChart component that renders backtest.equity_curve when present, currently shown in its honest "not available" state
provides:
  - "backtest.equity_curve now present in GET /api/v1/analytics/strategies/{strategy_id} responses"
  - "Service-level pytest asserting equity_curve presence and row shape (session_date, total_equity)"
affects: [16-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Read-only passthrough of already-computed report state, no new computation/route/method"

key-files:
  created: []
  modified:
    - src/trading_platform/services/analytics.py
    - tests/test_analytics_service.py

key-decisions:
  - "ANLX-01 intentionally NOT marked complete despite being listed in this plan's requirements frontmatter — per the 16-02 STATE.md decision, ANLX-01 requires both the 16-01 backend passthrough AND the 16-03 operator live-verify checkpoint to confirm the chart renders real data end-to-end. Marking it complete here would overclaim before an operator has actually viewed a populated chart."

patterns-established:
  - "Backend read-surface passthrough exceptions (ROADMAP Known Gaps) are implemented as single-line additions with a git-diff verification step baked into the plan, to keep the read-only-console scope boundary auditable."

requirements-completed: []  # ANLX-01 intentionally deferred — see key-decisions and Decisions Made below

# Metrics
duration: ~9min
completed: 2026-07-09
---

# Phase 16 Plan 01: Backend equity_curve passthrough Summary

**Single-line passthrough exposes the already-computed `equity_curve` series in `StrategyAnalyticsService._summarize_backtest`, covered by an extended service-level pytest.**

## Performance

- **Duration:** ~9 min
- **Started:** 2026-07-09T14:14:39Z
- **Completed:** 2026-07-09T14:23:32Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments
- `StrategyAnalyticsService._summarize_backtest()` now includes `equity_curve` in its returned dict, sourced directly from `materialize_backtest_report()`'s `report["equity_curve"]` — no new computation, route, or method added.
- Extended `test_strategy_analytics_service_summarizes_backtest_and_paper_state` with four assertions confirming `equity_curve` is present, non-empty, and its rows carry `session_date` and `total_equity`.
- Verified via `git diff` that the analytics.py change is exactly one added line, matching the plan's strict scope constraint (ROADMAP Known Gaps #2 exception).

## Task Commits

Each task was committed atomically:

1. **Task 1: Serialize equity_curve into the backtest block + cover with a service test** - `68151c4` (feat)

**Plan metadata:** (pending — this commit)

## Files Created/Modified
- `src/trading_platform/services/analytics.py` - Added `"equity_curve": report["equity_curve"],` to `_summarize_backtest()`'s return dict (single line, no other change)
- `tests/test_analytics_service.py` - Added four assertions to the existing backtest+paper summary test confirming `equity_curve` presence, non-emptiness, and row shape

## Decisions Made
- Confirmed via direct read of `backtest_reporting.py:73-85` that `materialize_backtest_report()` already returns `equity_curve` (sourced from `_load_equity_rows()`) before making the change, rather than trusting the plan's interfaces block alone.
- Extended the existing seeded-backtest test rather than adding a new test, per the plan's stated preference, reusing its DB/backtest fixture setup.
- **ANLX-01 requirement deliberately left incomplete in this plan's execution**, despite being listed in the plan frontmatter's `requirements: [ANLX-01]`. STATE.md's 16-02 decision explicitly states ANLX-01 needs both this backend change AND the 16-03 operator live-verify checkpoint before it can be marked complete without overclaiming. The `requirements mark-complete` step was skipped for this reason; STATE.md and REQUIREMENTS.md are updated to reflect 16-01 completion but ANLX-01 stays Pending until 16-03 confirms the chart renders real data end-to-end.

## Deviations from Plan

None - plan executed exactly as written. The analytics.py diff is exactly the one line specified; no additional computation, route, or method was introduced.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The backend now serves `backtest.equity_curve`; the 16-02 `EquityCurveChart` component (already shipped, previously rendering its honest "not available" state) should now render real data when a backtest run with equity snapshots is selected.
- 16-03 (operator live-verify checkpoint) is unblocked and can proceed to confirm ANLX-01 end-to-end and mark it complete.

---
*Phase: 16-analytics-and-charting*
*Completed: 2026-07-09*

## Self-Check: PASSED

- FOUND: src/trading_platform/services/analytics.py
- FOUND: tests/test_analytics_service.py
- FOUND: .planning/phases/16-analytics-and-charting/16-01-SUMMARY.md
- FOUND: commit 68151c4
