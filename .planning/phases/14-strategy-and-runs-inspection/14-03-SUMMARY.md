---
phase: 14-strategy-and-runs-inspection
plan: 03
subsystem: ui
tags: [nextjs, react, vitest, typescript, tailwind]

# Dependency graph
requires:
  - phase: 13-console-foundation-and-system-status
    provides: useApiQuery/fetchApi client, ErrorState, FetchMeta shared primitives
provides:
  - "/runs/[runId]/page.tsx run detail route shell"
  - "RunHeaderPanel (run summary + artifact counts + verbatim error)"
  - "SignalsRiskPanel (RUNS-03 signals + RUNS-04 risk decisions, run-scoped)"
  - "runScopedFilter.ts (filterByRun/isCapped) — shared primitive for 14-04"
  - "CappedDisclosure.tsx truncation banner — shared primitive for 14-04"
affects: [14-04-orders-fills-metrics, 14-05-operator-live-verify]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Run-detail page owns the single /api/v1/runs/{runId} fetch via useApiQuery and passes query state down to RunHeaderPanel as props, gating sibling audit panels on a resolved strategy_id (avoids a second fetch of the same endpoint and malformed child query strings)"
    - "Client-side run scoping: fetch strategy-wide from an endpoint with no run_id filter, then filterByRun(items, runId); isCapped(rawCount) computed from the RAW pre-filter count so the truncation disclosure fires even when the filtered result is empty"
    - "Next 16 dynamic route params are a Promise; a Client Component page resolves it with React's use(params) instead of async/await (this project's customized Next differs from pre-16 training-data conventions per console/AGENTS.md)"

key-files:
  created:
    - console/src/components/runs/detail/runScopedFilter.ts
    - console/src/components/runs/detail/runScopedFilter.test.ts
    - console/src/components/runs/detail/CappedDisclosure.tsx
    - console/src/components/runs/detail/RunHeaderPanel.tsx
    - console/src/components/runs/detail/SignalsRiskPanel.tsx
    - console/src/app/runs/[runId]/page.tsx
  modified: []

key-decisions:
  - "Run fetch owned by the page (not RunHeaderPanel) via a single useApiQuery call, with query state passed to RunHeaderPanel as props — the plan explicitly offered this as an alternative ('your call') to avoid double-fetching /api/v1/runs/{runId} and to let the page gate SignalsRiskPanel on a resolved strategy_id"
  - "isCapped is computed from data.count (the raw API count) never matched.length, so the empty-and-capped case (old run outside the 100-row window) renders CappedDisclosure instead of a bare empty state"
  - "Blocked-outcome emphasis in Risk Decisions uses a case-insensitive substring match against 'block'/'reject' rather than a hardcoded enum, since the exact outcome vocabulary isn't part of this plan's verified interface"

patterns-established:
  - "runScopedFilter.ts / CappedDisclosure.tsx as the shared run-detail primitives: any future panel that fetches strategy-wide and filters to a run reuses filterByRun + isCapped + CappedDisclosure"

requirements-completed: [RUNS-03, RUNS-04]

duration: 10min
completed: 2026-07-08
---

# Phase 14 Plan 03: Run Detail Shell, Signals & Risk Decisions Summary

**Run detail route (`/runs/[runId]`) with a run header, run-scoped signals/risk-decisions panel, and the honest MAX_LIMIT truncation-disclosure primitives (`runScopedFilter` + `CappedDisclosure`) that 14-04 reuses.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-07-08T16:22:00Z (approx, first Read)
- **Completed:** 2026-07-08T16:27:52Z
- **Tasks:** 2 completed
- **Files modified:** 6 created

## Accomplishments
- `runScopedFilter.ts` (`filterByRun`, `isCapped`, `OPERATIONS_MAX_LIMIT`) built TDD-first with 8 vitest cases, including the empty-and-capped combined case that the whole truncation-disclosure mechanism depends on
- `CappedDisclosure.tsx` amber banner renders only when capped, with copy that reads correctly at `matchedCount === 0`
- `/runs/[runId]` route shell resolves the Promise-typed `params` via React's `use()`, fetches the run once, and gates child panels on a resolved `strategy_id`
- `RunHeaderPanel` shows display name, status, run type, trigger, as-of session, timestamps, run id, artifact-count chips, and the verbatim `error_message`
- `SignalsRiskPanel` fetches `/api/v1/operations/risk-events?strategy_id=...&limit=100`, filters to the run, renders `CappedDisclosure` above the content, and separates Signals from Risk Decisions with visual emphasis on blocked outcomes/reasons (RUNS-03, RUNS-04)
- Left a commented drop-in point in `page.tsx` for 14-04's `OrdersFillsPanel`/`MetricsPanel` using the same `RunDetailContext` (`runId`/`strategyId`/`runType`)

## Task Commits

1. **Task 1: runScopedFilter pure helper (TDD) + CappedDisclosure banner**
   - `4122697` (test) — failing test for runScopedFilter (RED)
   - `d6fe579` (feat) — runScopedFilter + CappedDisclosure implementation (GREEN)
2. **Task 2: Run detail route shell + RunHeaderPanel + SignalsRiskPanel** - `9f3dd2d` (feat)

**Plan metadata:** (this commit)

_Note: Task 1 followed the TDD RED → GREEN flow; no refactor commit was needed._

## Files Created/Modified
- `console/src/components/runs/detail/runScopedFilter.ts` - Pure filterByRun/isCapped helpers, cap tied to OPERATIONS_MAX_LIMIT=100
- `console/src/components/runs/detail/runScopedFilter.test.ts` - vitest coverage incl. the empty-and-capped case
- `console/src/components/runs/detail/CappedDisclosure.tsx` - Amber truncation banner, renders only when capped
- `console/src/components/runs/detail/RunHeaderPanel.tsx` - Run summary header + artifact-count chips + verbatim error
- `console/src/components/runs/detail/SignalsRiskPanel.tsx` - Run-scoped signals + risk decisions, blocked-outcome emphasis
- `console/src/app/runs/[runId]/page.tsx` - Run detail route shell; owns the run fetch, composes header + signals panel, 14-04 drop-in comment

## Decisions Made
- Run fetch is owned by the page, not `RunHeaderPanel`, per the plan's explicit "your call" alternative — avoids a second fetch of `/api/v1/runs/{runId}` and cleanly gates `SignalsRiskPanel`'s query string on a resolved `strategy_id`.
- `isCapped` is always computed from the raw API `count`, never the post-filter `matched.length`, so the empty-and-capped case is never mistaken for "this run had none."
- Blocked-outcome emphasis uses a case-insensitive substring match (`block`/`reject`) against `outcome` rather than a hardcoded enum, since the exact outcome vocabulary wasn't part of this plan's verified interface.

## Deviations from Plan

None - plan executed exactly as written, using the plan's own explicitly-authorized alternative for where the run fetch lives (see Decisions Made).

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `runScopedFilter.ts` and `CappedDisclosure.tsx` are stable, tested shared primitives ready for 14-04 (`OrdersFillsPanel`, `MetricsPanel`) to import directly.
- `page.tsx` has a clearly-commented drop-in point and already threads `runId`/`strategyId`/`runType` (via `run.run_type`) for 14-04's panels.
- `npm run build`, `npm run lint`, and `npx vitest run` all pass on the full console workspace as of this plan (verified alongside sibling plans 14-01/14-02 executing in parallel on the same tree).

---
*Phase: 14-strategy-and-runs-inspection*
*Completed: 2026-07-08*

## Self-Check: PASSED

All 6 created files verified present on disk; all 3 task commit hashes (4122697, d6fe579, 9f3dd2d) verified present in git log.
