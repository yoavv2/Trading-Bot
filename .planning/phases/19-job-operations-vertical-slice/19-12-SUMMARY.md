---
phase: 19-job-operations-vertical-slice
plan: 12
subsystem: ui
tags: [nextjs, react, typescript, vitest, polling, job-orchestration, xss-safety]

# Dependency graph
requires:
  - phase: 19-job-operations-vertical-slice/19-08
    provides: console Job primitives (types.ts, useApiQuery polling, useMutationCapability, jobStatus.ts, cancellationLabel.ts, resourceRoutes.ts, consoleBoundaries.test.ts)
  - phase: 19-job-operations-vertical-slice/19-09
    provides: JobsTable list component, AutoRefreshIndicator
  - phase: 19-job-operations-vertical-slice/19-10
    provides: JobLogsPanel, JobEventsPanel, CancelJobDialog
  - phase: 19-job-operations-vertical-slice/19-11
    provides: New Job flow navigating to /jobs/{job_id} (the route this plan creates)
provides:
  - console/src/app/jobs/[jobId]/page.tsx — the /jobs/{id} route (Next 16 async params, use(params))
  - console/src/components/jobs/detail/JobDetailView.tsx — composes header/progress/resources/result/logs/events over a single polled GET /api/v1/jobs/{id} (pollIntervalMs 3000, stops at terminal)
  - console/src/components/jobs/detail/JobHeaderPanel.tsx — job_type + short id heading, status badge, D-14 honest cancellation/outcome label, every generic JOBUI-02 field row, D-21-gated Cancel Job… trigger opening CancelJobDialog
  - console/src/components/jobs/detail/JobProgressPanel.tsx — D-16 honest progress rendering (indeterminate vs determinate bar, never fabricates a percentage)
  - console/src/components/jobs/detail/JobResourcesPanel.tsx — generic resources[] renderer via lookup map (2) (resourceHref); unmapped kinds render plain text
  - console/src/components/jobs/detail/JobResultSummaryPanel.tsx — generic result_summary key/value renderer (D-06); no key ever special-cased
  - consoleBoundaries.test.ts D-17 scan made non-vacuous (EXPECTED_JOB_UI_FILES) + new dangerouslySetInnerHTML console-wide scan (T-19-12-01)
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Detail-page composition owns its own poll (useApiQuery pollIntervalMs 3000, shouldPoll = !isTerminalJobStatus) and passes the derived jobIsTerminal boolean down to JobLogsPanel/JobEventsPanel, which run their own independent 3s polls gated the same way — three separate timer chains converging on the same terminal-state stop condition rather than one shared poll driving child re-renders"
    - "Cancellation-group dl rows (CancellationRow helper) render null (both dt and dd) when their value is null, rather than always rendering with an em dash — keeps a Job with no cancellation history from showing five empty rows, and keeps the '/^Cancel/ text' D-14 test assertion honest"
    - "Explicit status===\"queued\"||status===\"running\" narrowing (not isTerminalJobStatus()) to satisfy TypeScript's control-flow narrowing when passing job.status into CancelJobDialog's jobStatus: \"queued\"|\"running\" prop"

key-files:
  created:
    - console/src/app/jobs/[jobId]/page.tsx
    - console/src/components/jobs/detail/JobDetailView.tsx
    - console/src/components/jobs/detail/JobDetailView.test.tsx
    - console/src/components/jobs/detail/JobHeaderPanel.tsx
    - console/src/components/jobs/detail/JobHeaderPanel.test.tsx
    - console/src/components/jobs/detail/JobProgressPanel.tsx
    - console/src/components/jobs/detail/JobResourcesPanel.tsx
    - console/src/components/jobs/detail/JobResultSummaryPanel.tsx
  modified:
    - console/src/lib/consoleBoundaries.test.ts

key-decisions:
  - "JobResultSummaryPanel's file-level comment deliberately never mentions the string 'run_id' (D-06 is described only as 'no dict key is ever special-cased') so the consoleBoundaries.test.ts D-06 discipline the panel implements cannot accidentally be undermined by a future edit that greps the source for the literal — the panel's own source is part of what SC6's fixture (a result_summary containing a run_id key) proves renders as plain unlinked text"
  - "CancelJobDialog is only mounted inside JobHeaderPanel's cancellable-status branch (not unconditionally with open=false) — avoids passing a synthetically-narrowed jobStatus prop for a terminal Job that can never actually open the dialog"
  - "JobDetailView.test.tsx's fetch router matches the detail endpoint by exact pathname (/backend/api/v1/jobs/<id>, not a prefix match) so it is never confused with that Job's /logs or /events suffixes or the /jobs list endpoint — required for the polling test's 'no additional detail fetch after +15s' assertion to count only detail calls"
  - "JOBUI-03 (logs/events viewing) is marked Complete by this plan even though it was not in this plan's own frontmatter requirements list — JobLogsPanel/JobEventsPanel (built component-tested-only in Plan 10) are now mounted on the real /jobs/{id} route via JobDetailView, which is what the requirement's literal text ('Operator can view...') needed to become true, per this plan's requirements-hygiene instruction to judge JOBUI-03 against its literal text now that it has a route"
  - "OPS-01 stays Pending: its literal text requires the flow 'proven end-to-end Console → HTTP → Job → worker → existing backtest service', which needs a live walkthrough, not just landed code — same recorded close condition from 19-07/19-11's SUMMARYs. This plan lands the last missing piece (the detail route) but does not itself perform a live verification pass"

patterns-established:
  - "Cancellation-group conditional dt/dd pairs (CancellationRow) as the pattern for any future generic-panel field group whose members are either all-present or all-absent together"

requirements-completed: [JOBUI-02, JOBUI-03, JOBUI-04, JOBUI-05]

# Metrics
duration: ~25min
completed: 2026-09-24
---

# Phase 19 Plan 12: Job Detail Screen Composition Summary

**The generic /jobs/{id} operator screen: header (D-14 honest cancellation/outcome label, D-21-gated Cancel Job… trigger), D-16 honest progress bar, generic resources[]/result_summary rendering (D-04/D-06/D-17) via the map-2 lookup, Logs/Events panels mounted for the first time on a real route, and 3s auto-refresh that stops the instant status turns terminal — proven job-type-agnostic with a test-only Job type flowing through both list and detail unchanged (SC6).**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-09-24T17:50:00Z (approx.)
- **Completed:** 2026-09-24T18:00:20Z
- **Tasks:** 3
- **Files modified:** 9 (8 created, 1 modified)

## Accomplishments
- `/jobs/[jobId]/page.tsx` — the route Plan 11's New Job flow and JobsTable's "View" links already targeted, now live; follows the same Next 16 async-`params`/`use(params)` shape as `app/runs/[runId]/page.tsx`
- `JobHeaderPanel.tsx` renders every JOBUI-02 field generically (job type + short id heading, status badge, full Job ID, queued/started/completed, failure reason/message, outcome_uncertain, the cancellation field group only when non-null, blocking-Job and root-cause-Job links, dependencies) plus the D-14 honest outcome label and a D-21-gated `Cancel Job…` trigger that opens `CancelJobDialog` and triggers a detail refetch on success
- `JobProgressPanel.tsx` never fabricates a percentage (D-16): an indeterminate animated bar + step text when `percent` is null, a determinate bar/percentage when numeric
- `JobResourcesPanel.tsx`/`JobResultSummaryPanel.tsx` render `resources[]` and `result_summary` fully generically — an unrecognized resource `kind` renders `{kind}: {id}` as plain text with no link, and no `result_summary` key (including `run_id`) is ever special-cased into a link (D-04/D-06)
- `JobDetailView.tsx` composes all six panels plus the pre-existing `JobLogsPanel`/`JobEventsPanel` over one polled `GET /api/v1/jobs/{id}` (`pollIntervalMs: 3000`), stopping the instant status becomes terminal (JOBUI-05)
- SC6 proven directly: a `test_only_type` Job with an `unknown_kind` resource and a `result_summary` containing a `run_id` key renders through both `JobsTable` and `JobDetailView` with zero UI changes — the unmapped resource and the `run_id` value both render as plain unlinked text
- `consoleBoundaries.test.ts`'s D-17 scan is non-vacuous for the first time (`EXPECTED_JOB_UI_FILES`, 12 files, all asserted present on disk and scanned) and gained a console-wide `dangerouslySetInnerHTML` scan (T-19-12-01)

## Task Commits

Each task was committed atomically:

1. **Task 1: JobHeaderPanel (fields, D-14 label, cancel trigger) + JobProgressPanel with tests** - `ddc5055` (feat)
2. **Task 2: Resources + result summary panels, JobDetailView with polling, route page** - `5cf57b2` (feat)
3. **Task 3: SC6 test-only Job type test, detail polling test, non-vacuous boundary test** - `5343b56` (test)

**Plan metadata:** commit pending (this SUMMARY + STATE/ROADMAP/REQUIREMENTS tracking commit)

## Files Created/Modified
- `console/src/components/jobs/detail/JobHeaderPanel.tsx` - generic Job header, D-14 label, gated cancel trigger
- `console/src/components/jobs/detail/JobHeaderPanel.test.tsx` - 8 component tests
- `console/src/components/jobs/detail/JobProgressPanel.tsx` - D-16 honest progress rendering
- `console/src/components/jobs/detail/JobResourcesPanel.tsx` - generic resources[] renderer (map 2)
- `console/src/components/jobs/detail/JobResultSummaryPanel.tsx` - generic result_summary renderer (D-06)
- `console/src/components/jobs/detail/JobDetailView.tsx` - detail screen composition + polling
- `console/src/components/jobs/detail/JobDetailView.test.tsx` - 6 component tests (SC6, resource linking, polling, empty-state copy)
- `console/src/app/jobs/[jobId]/page.tsx` - /jobs/{id} route shell
- `console/src/lib/consoleBoundaries.test.ts` - non-vacuous D-17 file-list assertion, dangerouslySetInnerHTML scan

## Decisions Made
See `key-decisions` in frontmatter above (D-06 comment discipline, CancelJobDialog mount scope, detail-endpoint exact-pathname matching in tests, JOBUI-03 completion judgment, OPS-01 staying Pending).

## Deviations from Plan
None — plan executed exactly as written. All acceptance-criteria greps, task-level `npx vitest run`/`npx tsc --noEmit`/`npx eslint` checks, and the whole-suite `npx vitest run`/`npx tsc --noEmit`/`npx eslint src`/`npm run build` verification all passed on first attempt with no fix-up commits needed.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- The full Job operations vertical slice (list, detail, logs, events, cancel, new-Job submission) is now code-complete and wired end to end in the console.
- OPS-01 remains Pending pending a live Console → HTTP → Job → worker → backtest-service walkthrough — this is a phase-verifier activity, not further executor work, per 19-07/19-11's recorded close condition.
- Backend was not touched by this plan; `python -m pytest -q` was not re-run (no backend files modified since the last passing backend run this milestone).
- No blockers. Baseline before this plan: 14 files / 111 tests. After this plan: 16 files / 129 tests, all green (`npm test`); `npx tsc --noEmit` clean; `npm run lint` clean; `npm run build` succeeds with `/jobs/[jobId]` registered as a dynamic route.

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*

## Self-Check: PASSED

- All 8 created files and this SUMMARY.md confirmed present on disk; `consoleBoundaries.test.ts` modification confirmed via `git diff --stat`.
- All 3 commits (`ddc5055`, `5cf57b2`, `5343b56`) confirmed present in `git log --oneline`.
- `npx vitest run` (console/): 16 files / 129 tests passed.
- `npx tsc --noEmit` (console/): clean.
- `npx eslint src` (console/): clean.
- `npm run build` (console/): succeeds.
