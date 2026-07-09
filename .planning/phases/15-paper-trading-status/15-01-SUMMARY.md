---
phase: 15-paper-trading-status
plan: 01
subsystem: ui
tags: [nextjs, react, typescript, operator-console, paper-trading]

# Dependency graph
requires:
  - phase: 13-console-foundation-and-system-status
    provides: useApiQuery/fetchApi shared fetch instrument, ErrorState/FetchMeta shared chrome, App Router layout with nav
  - phase: 14-strategy-and-runs-inspection
    provides: precedent for a section owning a single shared fetch and passing data down to presentational sub-panels (14-03), and the confirmed analytics response shape (MetricsPanel)
provides:
  - "/paper route reachable from a 'Paper Trading' top-nav link"
  - "PaperAnalyticsSection: single useApiQuery to /api/v1/analytics/strategies/trend_following_daily feeding PaperAccountPanel + PaperReconciliationPanel"
  - "PaperAccountPanel: PAPR-04 account snapshot display with explicit null-snapshot empty state"
  - "PaperReconciliationPanel: PAPR-03 reconciliation summary + blocks_execution badge + scope-labelled strategy-wide recent execution findings table"
affects: [15-02-positions-and-open-orders, 15-03-paper-trading-checkpoint]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Section-owns-fetch: PaperAnalyticsSection is the sole useApiQuery caller for the analytics endpoint; PaperAccountPanel/PaperReconciliationPanel are pure presentational components receiving already-fetched props (mirrors 14-03's run-detail page owning the run fetch)"
    - "Explicit null-object empty states distinct from zero-value fields: a null latest_account_snapshot/latest_reconciliation renders a dedicated empty-state paragraph, never a fabricated zeros card; a present snapshot's individual 0.0 fields (backend _decimal_value(None) quirk) render verbatim"
    - "Scope-honest heading for strategy-wide vs run-scoped data: recent_execution_findings heading explicitly states 'strategy-wide, most-recent' to avoid implying it belongs to the single latest reconciliation run"

key-files:
  created:
    - console/src/components/paper/types.ts
    - console/src/components/paper/PaperAccountPanel.tsx
    - console/src/components/paper/PaperReconciliationPanel.tsx
    - console/src/components/paper/PaperAnalyticsSection.tsx
    - console/src/app/paper/page.tsx
  modified:
    - console/src/app/layout.tsx

key-decisions:
  - "Extracted shared AccountSnapshot/Reconciliation/ExecutionFinding/PaperBlock/AnalyticsResponse types into components/paper/types.ts (not in the plan's files_modified list, but explicitly authorized by Task 1's action text to avoid duplication) rather than duplicating the type block into both panel files"
  - "This plan owns the Phase 15 nav edit (adds 'Paper Trading' link to layout.tsx after 'Runs') so 15-02 does not touch layout.tsx, mirroring the 14-01 precedent of one plan owning a phase's nav edits to avoid parallel-plan merge conflicts"
  - "Kept the useApiQuery<AnalyticsResponse>(...) call and its endpoint string literal on a single line (matching StrategyOverviewPanel's convention) so the plan's key_link grep pattern (useApiQuery.*analytics/strategies/trend_following_daily) matches"

requirements-completed: [PAPR-03, PAPR-04]

# Metrics
duration: ~20min
completed: 2026-07-09
---

# Phase 15 Plan 01: Paper Account & Reconciliation Panels Summary

**Analytics-fed PaperAccountPanel (PAPR-04) and PaperReconciliationPanel (PAPR-03) at a new `/paper` route, both driven by a single shared `useApiQuery` fetch of `/api/v1/analytics/strategies/trend_following_daily`, with honest null-state rendering for the very-likely-null account snapshot and reconciliation.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-09T09:50:00+03:00 (approx)
- **Completed:** 2026-07-09T10:09:00+03:00
- **Tasks:** 2 completed
- **Files modified:** 6 (5 created, 1 modified)

## Accomplishments
- `/paper` route live, reachable from a new "Paper Trading" top-nav link (placed after "Runs")
- `PaperAnalyticsSection` fetches the analytics endpoint exactly once via `useApiQuery` and shares the result between both sub-panels, with a single `FetchMeta`/`ErrorState` chrome for the whole section
- `PaperAccountPanel` shows total_equity/cash/buying_power (PAPR-04) plus gross_exposure/open_positions/snapshot_source/snapshot_at, with an explicit "No account snapshot recorded yet." empty state when `latest_account_snapshot` is null (the expected default state today, since Alpaca paper credentials are not configured)
- `PaperReconciliationPanel` shows status/as_of_session/finding_count/blocking_count/completed_at plus an unambiguous red "BLOCKS EXECUTION" / zinc "does not block execution" badge (PAPR-03), and lists `recent_execution_findings` under a heading that explicitly states its true scope ("Recent execution findings (strategy-wide, most-recent)") rather than implying they belong to the one reconciliation run, with an explicit "No reconciliation has been recorded yet." empty state when `latest_reconciliation` is null

## Task Commits

Each task was committed atomically:

1. **Task 1: Presentational account + reconciliation panels** - `b9fdc2d` (feat)
2. **Task 2: Analytics section (single shared fetch) + /paper route + nav link** - `c7b6895` (feat)

**Plan metadata:** (this commit, docs: complete plan)

## Files Created/Modified
- `console/src/components/paper/types.ts` - Shared AccountSnapshot/Reconciliation/ExecutionFinding/PaperBlock/AnalyticsResponse types copied from the plan's verified interface block
- `console/src/components/paper/PaperAccountPanel.tsx` - Presentational PAPR-04 account snapshot dl with explicit null-snapshot empty state
- `console/src/components/paper/PaperReconciliationPanel.tsx` - Presentational PAPR-03 reconciliation summary, blocks_execution badge, and scope-labelled findings table with explicit null-reconciliation empty state
- `console/src/components/paper/PaperAnalyticsSection.tsx` - Client section owning the single useApiQuery fetch, rendering FetchMeta/ErrorState chrome once and passing the `.paper` slice down to both panels
- `console/src/app/paper/page.tsx` - New `/paper` route composing PaperAnalyticsSection (positions + open orders land in 15-02)
- `console/src/app/layout.tsx` - Added "Paper Trading" nav link (`href="/paper"`) after "Runs", using the same className as sibling links

## Decisions Made
- Extracted shared types to `components/paper/types.ts` rather than duplicating the interface block in both panel files, per Task 1's explicit "your choice — but no duplication" instruction
- This plan owns the Phase 15 layout.tsx nav edit so 15-02 (positions/open-orders) doesn't need to touch it, following the 14-01 precedent
- Kept the `useApiQuery<AnalyticsResponse>("/api/v1/analytics/strategies/trend_following_daily")` call on one line to satisfy the plan's line-based `key_links` grep pattern

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `/paper` route and `PaperAnalyticsSection` are in place for 15-02 to extend with positions + open-orders panels beneath the account/reconciliation section
- Both panels' honest null/empty states are ready for the 15-03 live-verification checkpoint, where the operator is expected to see the null-account/null-reconciliation state first (Alpaca paper credentials not yet configured, per STATE.md)
- No blockers

---
*Phase: 15-paper-trading-status*
*Completed: 2026-07-09*

## Self-Check: PASSED

All created files verified present on disk; both task commits (b9fdc2d, c7b6895) verified present in git log.
