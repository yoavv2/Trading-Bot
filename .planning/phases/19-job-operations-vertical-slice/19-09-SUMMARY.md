---
phase: 19-job-operations-vertical-slice
plan: 09
subsystem: ui
tags: [nextjs, react, typescript, vitest, polling, job-orchestration]

# Dependency graph
requires:
  - phase: 19-job-operations-vertical-slice/19-06
    provides: backtest Job type registration (job_type/status/failure_reason vocab the list renders)
  - phase: 19-job-operations-vertical-slice/19-08
    provides: console Job primitives (types.ts, useApiQuery polling, useMutationCapability, jobStatus.ts, consoleBoundaries.test.ts D-17/SC6 enforcement)
provides:
  - console/src/app/jobs/page.tsx — /jobs route shell
  - console/src/components/jobs/JobsTable.tsx — job-type-agnostic list over GET /api/v1/jobs, server-side status/job_type filtering, JOBUI-05 5s auto-refresh, differentiated empty states, D-17/D-21-gated "New Job" entry point
  - console/src/components/jobs/JobFilters.tsx — controlled status/job_type filter bar, job types sourced from the live catalog
  - console/src/components/jobs/AutoRefreshIndicator.tsx — shared "Auto-refreshing every {N}s" / stopped-text indicator (reused by Plan 12's detail page)
  - console/src/components/jobs/JobsTable.test.tsx — 9 component tests (rows, filters, empty states, polling, mutation-capability gating)
  - "Jobs" nav link in console/src/app/layout.tsx
  - vitest.config.ts resolve.alias for "@/*" (fixes a latent gap: Vite/vitest never resolved the codebase's standard "@/..." import style; only surfaced now because this is the first component test to render a component using it)
affects: [19-10, 19-11, 19-12]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Component owns its own filter state (JobsTable) rather than lifting it to the page, since this plan's page.tsx renders only <JobsTable /> with no sibling needing the filter values — a deliberate deviation from RunsPage/RunFilters' page-owns-state split"
    - "vitest fetch-router test double: a single vi.fn() dispatches on request URL substring to answer both of a component's concurrent useApiQuery calls (job-types catalog + jobs list) through one shared global fetch stub, mirroring how the two hooks actually share fetch at runtime"

key-files:
  created:
    - console/src/app/jobs/page.tsx
    - console/src/components/jobs/JobsTable.tsx
    - console/src/components/jobs/JobFilters.tsx
    - console/src/components/jobs/AutoRefreshIndicator.tsx
    - console/src/components/jobs/JobsTable.test.tsx
  modified:
    - console/src/app/layout.tsx
    - console/vitest.config.ts

key-decisions:
  - "JobsTable owns {status, jobType} state internally and renders JobFilters itself; page.tsx stays a two-line shell (h1 + <JobsTable />) per the plan's explicit page.tsx scope, rather than mirroring RunsPage's page-owns-filter-state split"
  - "Job type filter options are read from useMutationCapability().catalog.items (the same /api/v1/job-types fetch already needed for New Job gating) rather than a second dedicated catalog fetch — one network call serves both concerns"
  - "vitest.config.ts gained a resolve.alias for \"@/*\" mirroring tsconfig.json's path mapping — Vite/vitest does not read tsconfig paths the way next build/tsc do, and no prior component test happened to render a component using the codebase's standard \"@/...\" import style, so this gap was latent until this plan's test tried to import JobsTable.tsx"

patterns-established:
  - "Fetch-router test double (URL-substring dispatch over one vi.fn()) for components with two concurrent useApiQuery consumers sharing global fetch"

requirements-completed: [JOBUI-01]  # JOBUI-05 stays Pending: its literal text is "Job list AND detail refresh automatically ... and stop polling at a terminal state" — this plan delivers only the list half (5s poll-while-non-terminal, stop-on-terminal, "Auto-refreshing"/"Auto-refresh stopped" indicator, all tested). Job-detail auto-refresh (3s interval per UI-SPEC) is Plan 12's scope; JOBUI-05 will be marked Complete once that lands, per the 19-01/19-08 precedent of not overclaiming a multi-part requirement from one part.

# Metrics
duration: 10min
completed: 2026-09-24
---

# Phase 19 Plan 09: Job List UI Summary

**Job-type-agnostic /jobs list (JobsTable/JobFilters/AutoRefreshIndicator) over GET /api/v1/jobs with server-side status/job_type filtering, differentiated empty states, 5s auto-refresh that stops the instant every visible row is terminal, and a mutation-capability-gated "New Job" entry point — plus the "Jobs" nav link and a vitest config fix that unblocked component testing of "@/..."-importing files.**

## Performance

- **Duration:** 10 min
- **Started:** 2026-09-24T12:38:00Z
- **Completed:** 2026-09-24T12:45:22Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- `/jobs` renders a table of Jobs with job type, status badge (closed 5-value color map), queued/started/completed timestamps, failure reason (+ "· outcome uncertain" suffix), and a "View" link to `/jobs/{id}` — zero `job_type` conditionals anywhere in the file (D-17)
- Status filter (all/queued/running/succeeded/failed/cancelled, exhaustive per the closed enum) and job-type filter (all + live catalog values, never hard-coded) both drive server-side `status=`/`job_type=` query params
- Two differentiated empty states: "No Jobs yet" / "Submit a new Job to get started." with no filters active, vs. "No Jobs match these filters." / "Clear filters or submit a new Job." once a filter is applied
- JOBUI-05 list half: polls every 5000ms while any visible row is `queued`/`running`, shows "Auto-refreshing every 5s", stops the instant every row is terminal and shows "Auto-refresh stopped — all visible Jobs finished" (verified with fake timers: a 5000ms tick fires the second fetch, a further 20000ms fires none)
- "New Job" link to `/jobs/new` (accent `sky-400` styling) when `useMutationCapability().state === "enabled"`; otherwise a disabled button plus the inline D-21 reason text (`"Mutations disabled on this deployment"` or the honest-unknown `"Mutation availability unknown — GET /api/v1/job-types failed"`)
- `consoleBoundaries.test.ts`'s D-17 job-type-agnosticism assertions are no longer vacuous — this is the first plan with files under `app/jobs/`/`components/jobs/`, and all 9 of its checks (including the earlier-vacuous ones) now pass against real code
- `JobsTable.test.tsx` (9 cases): row rendering incl. an unrecognized `job_type` value rendering without crashing, catalog-sourced filter options, filtered-fetch URL assertion, both empty states, full polling start/stop cycle, and all three New Job gating states (enabled/disabled/unknown)

## Task Commits

Each task was committed atomically:

1. **Task 1: Jobs list page, table, filters, auto-refresh indicator, nav link** - `2e67355` (feat)
2. **Task 2: JobsTable component tests** - `9267974` (test) — includes the vitest.config.ts alias fix (Rule 3, same commit as the tests it unblocks)

**Plan metadata:** commit pending (this SUMMARY + STATE/ROADMAP/REQUIREMENTS tracking commit)

## Files Created/Modified
- `console/src/app/jobs/page.tsx` - `/jobs` route shell (h1 + `<JobsTable />`)
- `console/src/components/jobs/JobsTable.tsx` - the list: fetch/filter/poll ownership, three-way render branch, gated New Job control
- `console/src/components/jobs/JobFilters.tsx` - controlled status/job_type select pair
- `console/src/components/jobs/AutoRefreshIndicator.tsx` - shared polling indicator, reused verbatim by Plan 12
- `console/src/components/jobs/JobsTable.test.tsx` - 9 component tests
- `console/src/app/layout.tsx` - added the "Jobs" nav link after Runs
- `console/vitest.config.ts` - added `resolve.alias` for `"@/*"` -> `./src`

## Decisions Made
- `JobsTable` owns its own `{status, jobType}` filter state rather than lifting it to `page.tsx`, since this plan's `page.tsx` renders only `<JobsTable />` with no sibling component needing the filter values — a deliberate, scoped deviation from the `RunsPage`/`RunFilters` page-owns-state split, not a correction to that pattern.
- The job-type filter's options are read from `useMutationCapability().catalog.items` (the same `/api/v1/job-types` fetch already needed for New Job gating) rather than issuing a second, dedicated catalog fetch.
- `vitest.config.ts` gained a `resolve.alias` for `"@/*"` mirroring `tsconfig.json`'s path mapping (see Deviations below).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] vitest had no path-alias resolution for the codebase's standard `"@/..."` import style**
- **Found during:** Task 2, first run of `npx vitest run src/components/jobs/JobsTable.test.tsx`
- **Issue:** `JobsTable.tsx` imports `useApiQuery`/`useMutationCapability`/`jobStatus`/`ErrorState`/`FetchMeta` via `"@/lib/..."`/`"@/components/..."` — the same import style used throughout `console/src/app/` and `console/src/components/` (e.g. `RunsTable.tsx`, `layout.tsx`). `next build`/`tsc --noEmit` resolve this fine via `tsconfig.json`'s `paths` mapping, but `vitest.config.ts` had no `resolve.alias` entry and Vite does not read `tsconfig.json` `paths` on its own — the test failed at import time with `Failed to resolve import "@/lib/useMutationCapability"`, before a single test case could run. This gap was latent in the repo since Plan 08 (no prior component test rendered a component using `"@/..."` imports; `SummaryMetricsPanel.test.tsx` renders a zero-import component, `useApiQuery.test.tsx`/`consoleBoundaries.test.ts` import only relative-path `lib/` modules).
- **Fix:** Added a `resolve: { alias: { "@": path.resolve(__dirname, "src") } }` block to `vitest.config.ts`, matching `tsconfig.json`'s `"@/*": ["./src/*"]` mapping exactly.
- **Files modified:** `console/vitest.config.ts`
- **Verification:** `npx vitest run src/components/jobs/JobsTable.test.tsx` (9/9 passing, was failing to even collect); whole-suite `npx vitest run` (8 files / 75 tests, all green, up from the 7 files / 66 tests baseline); `npx tsc --noEmit` and `npm run lint` unaffected (both already clean before and after, since this is a test-runner-only config change).
- **Committed in:** `9267974` (Task 2 commit — the alias fix and the tests it unblocks landed together, since the tests cannot exist as a passing artifact without it)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** No scope creep — a test-tooling config fix, not a product code change. Fixes a pre-existing repo gap that this plan's tests were the first to expose; every other test file was unaffected because none of them happened to import an `"@/..."`-using module.

## Issues Encountered
None beyond the deviation above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Plan 10 (Job detail shell) and Plan 12 (detail polling/cancel) can reuse `AutoRefreshIndicator`, `jobStatusColor`/`JOB_STATUS_BADGE_CLASS`, and the same fetch-router test-double pattern established here.
- `consoleBoundaries.test.ts`'s D-17/SC6 checks now run non-vacuously against real Job UI files and stay green — any future Job UI file that imports the (not-yet-created) `jobTypeForms.ts` lookup map, contains a `job_type` equality/switch, or the string literal `"backtest"`, or calls `fetch(` outside `lib/api.ts` will fail CI immediately.
- `vitest.config.ts`'s new `resolve.alias` benefits every future component test in the console, not just this plan's — any test that renders a component importing via `"@/..."` will now resolve correctly without needing its own workaround.
- No blockers. Baseline before this plan: 7 files / 66 tests. After this plan: 8 files / 75 tests, all green (`npm test`); `npx tsc --noEmit` clean; `npm run lint` clean.

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*
