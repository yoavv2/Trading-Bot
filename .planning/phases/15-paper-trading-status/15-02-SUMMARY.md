---
phase: 15-paper-trading-status
plan: 02
subsystem: ui
tags: [nextjs, react, typescript, operator-console, paper-trading]

# Dependency graph
requires:
  - phase: 15-paper-trading-status (15-01)
    provides: /paper route, PaperAnalyticsSection, useApiQuery/ErrorState/FetchMeta console-foundation pattern, Paper Trading nav link
provides:
  - PositionsPanel (PAPR-01): self-fetching current-positions panel with open/non-open disclosure
  - OpenOrdersPanel (PAPR-02): self-fetching open-orders panel with OPEN_STATUSES filter and terminal-status disclosure
  - /paper page composing all four PAPR panels (account, reconciliation, positions, open orders)
affects: [16-analytics-and-charting]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Paper-local self-fetching panel: Section component owns unconditional useApiQuery call + FetchMeta/ErrorState chrome; Content child renders success-path table/empty-state, mirroring OrdersFillsPanel but without importing its run-detail-scoped CappedDisclosure/runScopedFilter helpers"
    - "Client-side open-status filtering with mandatory disclosure: compute open vs hidden count, render 'Hiding N non-open …' note + reveal toggle (useState boolean) so filtered-out rows are never silently dropped"
    - "100-row cap honesty: when data.count >= 100, render a truncation note even when the filtered (open) list is empty, so an empty result never reads as a definitive 'none'"

key-files:
  created:
    - console/src/components/paper/PositionsPanel.tsx
    - console/src/components/paper/OpenOrdersPanel.tsx
  modified:
    - console/src/app/paper/page.tsx

key-decisions:
  - "Mirrored OrdersFillsPanel's Section(hook)/Content(render) structure but did NOT import CappedDisclosure or runScopedFilter/filterByRun (run-detail-scoped) — inlined the hidden-count disclosure and cap check locally per the plan's explicit constraint"
  - "Empty-state branch checks the 100-row cap before falling back to a plain 'No open positions/orders' message, so an empty filtered result while data.count >= 100 always shows the truncation caveat instead of a false definitive 'none'"

patterns-established:
  - "Pattern: paper-local table panels compose directly from useApiQuery/ErrorState/FetchMeta, never from status-screen or run-detail-scoped helpers, consistent with the 13-04/14-03 scoping decisions"

requirements-completed: [PAPR-01, PAPR-02]

# Metrics
duration: ~15min
completed: 2026-07-09
---

# Phase 15 Plan 02: Positions + Open Orders Panels Summary

**PositionsPanel (PAPR-01) and OpenOrdersPanel (PAPR-02) added as self-fetching, honesty-first table panels composed into the existing `/paper` screen, completing all four Paper Trading Status panels.**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-07-09T07:15:33Z
- **Tasks:** 3
- **Files modified:** 3 (2 created, 1 modified)

## Accomplishments
- `PositionsPanel` fetches `/api/v1/operations/positions?strategy_id=trend_following_daily&limit=100`, defaults to open positions (symbol/quantity/avg-entry/cost-basis/opened), discloses+reveals hidden non-open rows with a Status column, shows an explicit "No open positions." empty state, and a 100-row truncation note when `count >= 100`.
- `OpenOrdersPanel` fetches `/api/v1/operations/orders?strategy_id=trend_following_daily&limit=100`, filters to `OPEN_STATUSES = {pending_submission, submitted, partially_filled}` by default (symbol/side/qty/status+broker_status/submitted/client_order_id, with inline red submission/sync error text), discloses+reveals hidden terminal-status rows, and shows an empty state that distinguishes "No open orders." from the truncation-caveat case.
- `/paper` now composes `PaperAnalyticsSection` + `PositionsPanel` + `OpenOrdersPanel` in a `space-y-6` container below the existing analytics section — all four PAPR panels (account, reconciliation, positions, open orders) live on one screen.
- Build and lint both pass; no component in `console/src/components/paper` calls `fetch()` directly — every fetch goes through `useApiQuery`/`fetchApi`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Current-positions panel (PAPR-01)** - `5bf01bb` (feat)
2. **Task 2: Open-orders panel (PAPR-02)** - `bbeb485` (feat)
3. **Task 3: Compose positions + open orders into the /paper page** - `483ed4b` (feat)
4. **Post-task fix: inline fetch endpoint URLs into useApiQuery calls** - `0d5f8ff` (fix, Rule 1 — see Deviations)

**Plan metadata:** (this commit, see final_commit step)

## Files Created/Modified
- `console/src/components/paper/PositionsPanel.tsx` - Self-fetching current-positions panel (PAPR-01): open-by-default with non-open disclosure/reveal, explicit empty state, 100-row truncation note
- `console/src/components/paper/OpenOrdersPanel.tsx` - Self-fetching open-orders panel (PAPR-02): OPEN_STATUSES filter with hidden-count disclosure/reveal, explicit empty state, 100-row truncation note
- `console/src/app/paper/page.tsx` - Extended to render `PositionsPanel` + `OpenOrdersPanel` below `PaperAnalyticsSection`; `layout.tsx` untouched

## Decisions Made
- Deliberately did NOT reuse `CappedDisclosure`/`filterByRun`/`isCapped` from `runs/detail` even though they solve the same problem, per the plan's explicit constraint that those are run-detail-scoped — inlined equivalent, paper-local disclosure text and cap logic in both panels instead.
- Followed the plan's `<interfaces>` block types verbatim (`quantity: number`, etc.) rather than the `string`-typed copy in `OrdersFillsPanel.tsx`, since the plan explicitly designates its interfaces block as the verified source of truth for this plan.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fetch endpoint constant broke the key_links verification pattern**
- **Found during:** Post-task self-review (advisor call after Task 3 committed)
- **Issue:** Both panels initially extracted the fetch URL into a module-level `const ENDPOINT` on a line separate from the `useApiQuery(...)` call. The plan's `key_links` patterns (`useApiQuery.*operations/positions`, `useApiQuery.*operations/orders`) are line-based regexes, so they never matched — a line-based verifier would have flagged both links as unmet even though the panels fetched the correct endpoints correctly at runtime.
- **Fix:** Inlined the endpoint URL string directly into the `useApiQuery<CollectionResponse<T>>("...")` call in both panels, matching the plan's literal task-action text and making the pattern match regardless of verifier implementation.
- **Files modified:** `console/src/components/paper/PositionsPanel.tsx`, `console/src/components/paper/OpenOrdersPanel.tsx`
- **Verification:** Re-ran `npm run build` and `npm run lint` (both clean) and confirmed `grep -nE "useApiQuery.*operations/positions"` / `.../orders` now match on a single line in each file; direct-fetch grep still returns nothing.
- **Committed in:** `0d5f8ff`

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** No behavior change — same endpoint, same fetch instrument. Purely a verification-pattern-matching fix caught before declaring the plan complete.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. Both panels consume existing `GET /api/v1/operations/positions` and `GET /api/v1/operations/orders` routes with no backend changes.

## Next Phase Readiness

All four PAPR panels (PAPR-01 through PAPR-04) are now live on `/paper`. Live behavioral verification (empty states with unconfigured Alpaca paper credentials, error states with the API stopped, reveal-toggle behavior) is deferred to the 15-03 operator checkpoint per the plan's `<verification>` section. Phase 15 is ready to proceed to 15-03.

---
*Phase: 15-paper-trading-status*
*Completed: 2026-07-09*

## Self-Check: PASSED

All created files and task commit hashes verified present on disk / in git log.
