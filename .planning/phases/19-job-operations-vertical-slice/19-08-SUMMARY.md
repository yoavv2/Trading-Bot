---
phase: 19-job-operations-vertical-slice
plan: 08
subsystem: ui
tags: [nextjs, react, typescript, vitest, polling, job-orchestration]

# Dependency graph
requires:
  - phase: 19-job-operations-vertical-slice/19-04
    provides: GET /api/v1/job-types catalog (mutations_enabled, items[])
  - phase: 19-job-operations-vertical-slice/19-06
    provides: backtest Job type registration (job_type/failure_reason vocab exercised by the console types)
provides:
  - console/src/components/jobs/types.ts — typed Job/JobsResponse/JobDetail/JobLogsPage/JobEventsPage/JobReference/JobTypesCatalog contracts
  - console/src/lib/api.ts — submitJob/cancelJob mutating client (sole fetch site), MUTATION_ERROR_COPY closed 9-code table, mutationErrorMessage, never-throwing postJson
  - console/src/lib/useApiQuery.ts — optional {pollIntervalMs, shouldPoll} silent background polling, pause-when-hidden/resume-on-visible, non-overlapping setTimeout chain
  - console/src/lib/useMutationCapability.ts — three-state (enabled/disabled/unknown) mutation-capability signal + pure mutationCapabilityFrom()
  - console/src/lib/jobStatus.ts — TERMINAL_JOB_STATUSES/isTerminalJobStatus/jobStatusColor/JOB_STATUS_BADGE_CLASS
  - console/src/lib/cancellationLabel.ts — job-type-agnostic cancellationOutcomeLabel (D-14, 10-rule case table)
  - console/src/lib/resourceRoutes.ts — RESOURCE_ROUTES lookup map (2) + resourceHref
  - console/src/lib/consoleBoundaries.test.ts — SC6 (no raw fetch( outside api.ts) and D-17 (job-type-agnostic Job UI, single-declaration-site) structural enforcement
affects: [19-09, 19-10, 19-11, 19-12]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Never-throwing mutating client (postJson) layered on fetchApi's existing try/fetch/catch skeleton, isolated to lib/api.ts as the SC6 sole fetch site"
    - "Latest-ref pattern (assign refs in a no-dep-array/declaration-ordered useEffect, never during render) to satisfy eslint-config-next 16's react-hooks/refs rule while still giving async callbacks (timers, fetch .then) a live view of the latest inline options object"
    - "setTimeout chaining (not setInterval) for non-overlapping polling, with a mutable tickRef indirection to break the scheduleNextTick/runBackgroundTick circular useCallback dependency"
    - "Node-environment source-scan tests (node:fs/node:path off import.meta.url) as the console-side analog to the Python AST boundary tests"

key-files:
  created:
    - console/src/components/jobs/types.ts
    - console/src/lib/useMutationCapability.ts
    - console/src/lib/jobStatus.ts
    - console/src/lib/cancellationLabel.ts
    - console/src/lib/cancellationLabel.test.ts
    - console/src/lib/resourceRoutes.ts
    - console/src/lib/consoleBoundaries.test.ts
    - console/src/lib/useApiQuery.test.tsx
  modified:
    - console/src/lib/api.ts
    - console/src/lib/api.test.ts
    - console/src/lib/useApiQuery.ts

key-decisions:
  - "postJson merges a caller-supplied detailDefaults object (e.g. {job_id: jobId} in cancelJob) under the server's parsed detail, so job_not_found always interpolates a real id even if the server response ever omitted it, while a server-provided field always wins"
  - "mutationErrorMessage uses Object.hasOwn(MUTATION_ERROR_COPY, code) rather than `code in COPY` or bracket lookup, so a code equal to an Object.prototype member name (e.g. \"constructor\") cannot resolve to a prototype function"
  - "useApiQuery keeps the latest options/tick-function in refs updated by declaration-ordered no-dependency-array effects (not during render) — eslint-config-next 16 added a react-hooks/refs rule forbidding ref writes during render that the plan's original 'keep latest options in a ref, assign at the top of the hook' approach violated"
  - "polling reflects the active-per-rule state ignoring document.hidden (matches UI-SPEC's Auto-refreshing/Auto-refresh-stopped indicator contract); the underlying setTimeout is what actually pauses while hidden, not the reported flag"
  - "consoleBoundaries.test.ts's D-17 Job-UI-agnostic checks are deliberately vacuous today (zero files under components/jobs/ or app/jobs/ exist yet) — this plan does not add the non-vacuity assertion the PLAN.md text reserves for Plan 12"

patterns-established:
  - "Pure mapping functions (mutationCapabilityFrom, cancellationOutcomeLabel) extracted alongside their hooks/consumers so business logic is unit-testable without rendering"
  - "Closed error-code-to-copy Record with Object.hasOwn lookup for any future typed-error-code mapping"

requirements-completed: []  # JOBUI-01/02/04/05 all stay Pending: this plan ships console primitives (types, mutating client, polling, status/label/lookup-map helpers, structural enforcement) with no operator-visible Job screen yet — each requirement's literal text (list/detail/cancel UI, list-and-detail auto-refresh) is only satisfiable once Plans 09-12 render actual screens over these primitives, per the 19-01/19-03 precedent of not overclaiming plan-frontmatter requirements against text a plan doesn't fully deliver.

# Metrics
duration: 55min
completed: 2026-09-24
---

# Phase 19 Plan 08: Console Job UI Foundation Summary

**Typed Job contracts, a never-throwing mutating console client (submitJob/cancelJob) at the sole fetch site, silent-polling useApiQuery with pause-on-hidden/resume, a three-state mutation-capability hook, the D-14 honest cancellation/outcome label, the resources[]-kind lookup map, and a node:fs source-scan test enforcing SC6 (single fetch site) and D-17 (job-type-agnostic Job UI, single lookup-map declaration sites).**

## Performance

- **Duration:** 55 min
- **Started:** 2026-09-24T15:14:00Z
- **Completed:** 2026-09-24T16:09:00Z
- **Tasks:** 3
- **Files modified:** 11

## Accomplishments
- `console/src/components/jobs/types.ts` — full Job/JobsResponse/JobDetail/JobLogsPage/JobEventsPage/JobReference/JobTypesCatalog TypeScript contracts, verified line-by-line against `job_reads.py`, `api/routes/jobs.py`, `api/routes/job_types.py`, and `orchestration/job_mutations.py`'s actual response shapes (not just the plan's interfaces block)
- `console/src/lib/api.ts` extended with `submitJob`/`cancelJob`/`mutationErrorMessage`/`MUTATION_ERROR_COPY` — the only file in the console that calls the global `fetch(` (SC6), `fetchApi` left byte-for-byte unchanged
- `console/src/lib/useApiQuery.ts` gained optional `{pollIntervalMs, shouldPoll}` silent background polling (JOBUI-05 mechanism): non-overlapping setTimeout chain, never flips `loading` on a tick, pauses while `document.hidden`, resumes with one immediate tick + chain restart on `visibilitychange`, and a failed tick preserves the last successful result while continuing to poll
- `console/src/lib/useMutationCapability.ts` — three-state (`enabled`/`disabled`/`unknown`) D-20/D-21 signal over the job-types catalog, never assumes enabled absent a confirmed successful response
- `console/src/lib/jobStatus.ts`, `cancellationLabel.ts`, `resourceRoutes.ts` — the closed status→color map, the 10-rule D-14 honest cancellation label (zero `job_type` references, unit-tested against every UI-SPEC case-table row), and lookup map (2) for `resources[].kind → route`
- `console/src/lib/consoleBoundaries.test.ts` (new, 161 lines) — SC6 fetch-site scan and D-17 job-type-agnostic/single-lookup-map-declaration scans over `console/src`, passing vacuously today since no Job UI files exist yet

## Task Commits

Each task was committed atomically:

1. **Task 1: Job types + mutating client in api.ts** - `d697648` (feat)
2. **Task 2: Polling in useApiQuery + mutation capability hook** - `209001a` (feat)
3. **Task 3: Status map, D-14 label, resource map (2), SC6/D-17 enforcement test** - `37f4acb` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified
- `console/src/components/jobs/types.ts` - Job/JobDetail/JobsResponse/JobLogsPage/JobEventsPage/JobReference/JobTypesCatalog TypeScript contracts
- `console/src/lib/api.ts` - added submitJob/cancelJob/mutationErrorMessage/MUTATION_ERROR_COPY/postJson; fetchApi unchanged
- `console/src/lib/api.test.ts` - 10 new cases (POST shape, 202/replay, mutations_disabled/invalid_job_payload/idempotency_key_conflict/unmapped-code copy, network failure, cancelJob null-reason body + job_not_found fallback)
- `console/src/lib/useApiQuery.ts` - added optional polling ({pollIntervalMs, shouldPoll}) and a `polling` field on QueryState
- `console/src/lib/useApiQuery.test.tsx` - 5 polling cases (jsdom, fake timers)
- `console/src/lib/useMutationCapability.ts` - three-state mutation-capability hook + pure mutationCapabilityFrom()
- `console/src/lib/jobStatus.ts` - TERMINAL_JOB_STATUSES/isTerminalJobStatus/jobStatusColor/JOB_STATUS_BADGE_CLASS
- `console/src/lib/cancellationLabel.ts` - job-type-agnostic D-14 cancellationOutcomeLabel
- `console/src/lib/cancellationLabel.test.ts` - 18 cases (case-table rows, rules 7/9, null case, job-type-agnosticism, status color/terminal)
- `console/src/lib/resourceRoutes.ts` - RESOURCE_ROUTES lookup map (2) + resourceHref
- `console/src/lib/consoleBoundaries.test.ts` - SC6/D-17 structural enforcement (9 cases)

## Decisions Made
- `postJson` merges caller-supplied `detailDefaults` (e.g. `{job_id: jobId}` for `cancelJob`) under the server's parsed `detail` object so `job_not_found` always interpolates a real id; a server-provided field wins if present.
- `mutationErrorMessage` looks up `MUTATION_ERROR_COPY` via `Object.hasOwn(...)` rather than `code in COPY`, closing off prototype-pollution-style lookups (e.g. a code literally named `"constructor"`).
- `useApiQuery`'s "keep the latest options/tick function in a ref" mechanism is implemented via declaration-ordered, no-dependency-array `useEffect` calls rather than assigning `ref.current` directly during render — `eslint-config-next` 16 ships a `react-hooks/refs` rule (new since the plan text was written) that forbids ref writes during render; `npx eslint src/lib` confirmed clean only after this restructuring.
- `polling` in `QueryState` reports the active-per-rule state *ignoring* `document.hidden` (per UI-SPEC's Auto-refreshing/Auto-refresh-stopped indicator contract) — the underlying `setTimeout` is what actually pauses while hidden, not the reported boolean.
- `consoleBoundaries.test.ts`'s D-17 checks are deliberately vacuous today (no `components/jobs/`/`app/jobs/` files exist); this plan does not add the non-vacuity assertion the PLAN.md body explicitly reserves for Plan 12.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `useApiQuery`'s ref-write-during-render pattern violated a react-hooks lint rule not anticipated by the plan text**
- **Found during:** Task 2 (`npx eslint src/lib`)
- **Issue:** The plan's literal instruction ("keep the latest options object in a ref rather than putting it in effect/callback deps") was implemented by assigning `optionsRef.current = options` and `tickRef.current = runBackgroundTick` directly in the hook body (during render). `eslint-config-next@16.2.10`'s bundled `react-hooks` plugin flags this as `Cannot access refs during render` (`react-hooks/refs`) — this is a newer rule than the codebase's existing `useApiQuery` precedent (13-03's eslint-disable was for a different rule, `set-state-in-effect`).
- **Fix:** Moved both ref assignments into declaration-ordered, no-dependency-array `useEffect` calls (`useEffect(() => { optionsRef.current = options; })` and `useEffect(() => { tickRef.current = runBackgroundTick; }, [runBackgroundTick])`), placed before the mount-fetch effect so both refs are current by the time any async callback (fetch `.then()`, timer) reads them. Verified no behavior change: all 8 `useApiQuery.test.tsx` cases still pass unmodified.
- **Files modified:** `console/src/lib/useApiQuery.ts`
- **Verification:** `npx eslint src/lib` exits clean; `npx vitest run src/lib/useApiQuery.test.tsx` still 8/8 passing; whole-suite `npx vitest run` 64/64.
- **Committed in:** `209001a` (Task 2 commit — found and fixed within the same task, before commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** No scope creep — the fix only changed *where* two ref assignments happen (render vs. effect), not any observable polling/mutation-capability behavior. All plan-specified test cases pass unchanged.

## Issues Encountered
None beyond the deviation above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Plans 09-12 (Job list/detail/logs/events/resources/cancel screens, submission form) can now build directly on `useApiQuery`'s polling, `submitJob`/`cancelJob`, `useMutationCapability`, `jobStatusColor`, `cancellationOutcomeLabel`, and `resourceHref` without re-deriving any of these primitives.
- `console/src/lib/consoleBoundaries.test.ts` will start enforcing D-17 job-type-agnosticism and the fetch-site rule (SC6) the moment those plans add files under `components/jobs/`/`app/jobs/` — no further wiring needed.
- No blockers. Baseline before this plan was 4 test files / 19 tests; after this plan, 7 test files / 64 tests, all green, `tsc --noEmit` and `eslint src` both clean.

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*
