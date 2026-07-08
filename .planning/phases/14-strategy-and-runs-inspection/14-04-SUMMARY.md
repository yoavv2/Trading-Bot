---
phase: 14-strategy-and-runs-inspection
plan: 04
subsystem: ui
tags: [nextjs, react, typescript, tailwind]

# Dependency graph
requires:
  - phase: 14-strategy-and-runs-inspection (14-03)
    provides: runScopedFilter.ts (filterByRun/isCapped), CappedDisclosure.tsx, run detail page shell + RunDetailContext (runId/strategyId/runType)
provides:
  - "OrdersFillsPanel: run-scoped orders + fills with client_order_id intent lineage (RUNS-05)"
  - "MetricsPanel: run-type-aware persisted metrics via the per-run analytics endpoint (RUNS-06)"
  - "Completed run-detail audit trail: header -> signals/risk -> orders/fills -> metrics, all mounted on /runs/[runId]"
affects: [14-05-operator-live-verify, 16-analytics-and-charting]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "MetricsPanel gates on runType at the render boundary (mounting a MetricsFetchContent child only for backtest/paper_execution) rather than conditionally calling useApiQuery inline, so every hook call stays unconditional per react-hooks/rules-of-hooks while still avoiding a doomed 400/404 fetch for run types with no per-run analytics"
    - "Open-ended metrics/summary dicts rendered generically via Object.entries + JSON.stringify for nested values, matching the 14-01 pattern for open-ended config dicts, so the metrics section stays honest across metric-set changes"

key-files:
  created:
    - console/src/components/runs/detail/OrdersFillsPanel.tsx
    - console/src/components/runs/detail/MetricsPanel.tsx
  modified:
    - console/src/app/runs/[runId]/page.tsx

key-decisions:
  - "MetricsPanel splits into a non-fetching parent (decides which of three states to render based on runType) and a MetricsFetchContent child that unconditionally calls useApiQuery — avoids a conditional-hook-call lint failure while still never calling the analytics endpoint for run types verified to 400/404 on it"
  - "OrdersFillsPanel keeps orders and fills as two fully independent sub-sections (own useApiQuery, own FetchMeta, own ErrorState, own CappedDisclosure) rather than a combined fetch, since the two source endpoints are unrelated and a fills-load failure shouldn't blank out orders or vice versa"
  - "page.tsx's 14-03 drop-in comment showed OrdersFillsPanel taking a runType prop; the plan's own component spec did not, so OrdersFillsPanel was implemented with only { runId, strategyId } (matching the plan) and the stale comment was replaced entirely rather than preserved"

requirements-completed: [RUNS-05, RUNS-06]

duration: 20min
completed: 2026-07-08
---

# Phase 14 Plan 04: Orders, Fills & Persisted Metrics Summary

**OrdersFillsPanel (run-scoped orders with client_order_id/supersedes intent lineage + fills) and a run-type-aware MetricsPanel reading the per-run analytics endpoint, both mounted onto `/runs/[runId]` to complete the audit trail.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-08T19:30:00Z (approx, first Read)
- **Completed:** 2026-07-08T19:50:40Z
- **Tasks:** 2 completed
- **Files modified:** 2 created, 1 modified

## Accomplishments
- `OrdersFillsPanel.tsx` renders two independent run-scoped sub-sections (Orders, Fills), each reusing 14-03's `filterByRun`/`isCapped`/`CappedDisclosure` primitives with the same empty-vs-capped honesty rule as `SignalsRiskPanel`
- Orders table surfaces the RUNS-05 intent lineage: monospace `client_order_id`, and — when present — a `supersedes: {client_order_id}` line from `intent_context.supersedes_client_order_id`, plus inline `last_submission_error`/`last_sync_error` when non-null
- `MetricsPanel.tsx` is run-type-aware (RUNS-06): backtest runs fetch `?backtest_run_id=`, paper_execution runs fetch `?paper_run_id=`, and every other run type (risk_evaluation, reconciliation, dry_bootstrap, operator_control, etc.) renders an honest static "no persisted per-run metrics for {runType} runs" state without ever calling the endpoint
- The run-type gating happens by conditionally rendering a child component that itself calls `useApiQuery` unconditionally, rather than conditionally invoking the hook — avoids a `react-hooks/rules-of-hooks` violation while still never issuing a doomed 400/404 request
- Metrics render generically via `Object.entries` over the `summary`/`metrics` (backtest) or whole-block (paper) dicts, so the section stays correct as the metric-set evolves
- `/runs/[runId]/page.tsx` now mounts `OrdersFillsPanel` and `MetricsPanel` below `SignalsRiskPanel`, completing the header -> signals/risk -> orders/fills -> metrics top-to-bottom audit trail

## Task Commits

1. **Task 1: OrdersFillsPanel (orders + fills with lineage, reusing the disclosure)** - `273975d` (feat)
2. **Task 2: MetricsPanel (run-type-aware) + mount both panels in the detail page** - `3422e71` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified
- `console/src/components/runs/detail/OrdersFillsPanel.tsx` - Run-scoped orders (client_order_id + supersedes lineage, submission/sync errors) and fills, each with FetchMeta/ErrorState/CappedDisclosure
- `console/src/components/runs/detail/MetricsPanel.tsx` - Run-type-aware persisted metrics via `/api/v1/analytics/strategies/{id}`, honest no-metrics state for non-backtest/paper run types
- `console/src/app/runs/[runId]/page.tsx` - Mounts `OrdersFillsPanel` and `MetricsPanel` in the resolved-context block, replacing 14-03's commented placeholder

## Decisions Made
- MetricsPanel's parent component never calls `useApiQuery` itself; it renders one of three branches (backtest-fetch child, paper-fetch child, or static no-metrics text), keeping every hook call in the tree unconditional.
- OrdersFillsPanel treats orders and fills as fully independent fetches/sections so a failure in one doesn't blank the other.
- Followed the plan's literal `OrdersFillsPanel` prop signature (`{ runId, strategyId }`, no `runType`) over the stale 14-03 drop-in comment that had shown a `runType` prop on it.

## Deviations from Plan

None - plan executed exactly as written. The only divergence from written artifacts was replacing 14-03's stale drop-in comment (which showed `runType` on `OrdersFillsPanel`) with the actual mount matching the plan's own component spec — not a deviation from the plan, just a correction of a comment left by the prior plan.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `/runs/[runId]` now renders the full audit trail (header, signals/risk, orders/fills with intent lineage, persisted metrics) required for 14-05's operator live-verify checkpoint.
- `npm run build`, `npm run lint`, and `npx vitest run` all pass on the full console workspace.
- 14-05 (operator live-verify) can now exercise a paper run (orders/fills/paper metrics), a backtest run (backtest metrics), and a risk_evaluation run (honest no-metrics state) end to end.

---
*Phase: 14-strategy-and-runs-inspection*
*Completed: 2026-07-08*

## Self-Check: PASSED

All 3 files (OrdersFillsPanel.tsx, MetricsPanel.tsx, page.tsx) verified present on disk; both task commit hashes (273975d, 3422e71) verified present in git log.
