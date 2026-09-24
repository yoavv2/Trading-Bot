---
phase: 19-job-operations-vertical-slice
plan: 11
subsystem: ui
tags: [nextjs, react, typescript, vitest, job-orchestration, idempotency]

# Dependency graph
requires:
  - phase: 19-job-operations-vertical-slice/19-06
    provides: backtest Job type registration (BacktestSubmissionSpec payload contract, submission_defaults)
  - phase: 19-job-operations-vertical-slice/19-08
    provides: console Job primitives (types.ts, submitJob/api.ts, useMutationCapability, useApiQuery)
provides:
  - console/src/lib/jobTypeForms.ts — JOB_TYPE_FORMS lookup map (1), D-17 (job_type -> submission form component); imported only by NewJobView.tsx
  - console/src/components/jobs/new/BacktestJobForm.tsx — backtest submission form (strategy_id/from_date/to_date required, D-08), catalog submission_defaults pre-fill (D-10), one Idempotency-Key per form instance reused on identical retry / rotated on payload change (T-19-11-02), honest 202/replay/409 outcomes
  - console/src/components/jobs/new/NewJobView.tsx — catalog-driven type picker at /jobs/new, dispatches through map (1), unmapped-type fallback, D-21-safe on catalog failure (mapped forms stay visible-but-disabled rather than hidden)
  - console/src/app/jobs/new/page.tsx — /jobs/new route shell (useSearchParams/useRouter under Suspense)
  - console/src/components/strategy/StrategyOverviewPanel.tsx — "Run backtest" shortcut to /jobs/new?type=backtest&strategy_id=<id> (D-18), D-21-gated
  - console/src/components/runs/detail/RunHeaderPanel.tsx — "Created by" -> "Job <job_id>" back-link (D-07), RunSummary.job_id
affects: [19-12]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-form-instance Idempotency-Key with payload-fingerprint rotation: a useRef key generated once at mount, reused verbatim across a retry of the identical payload (e.g. a network failure), and regenerated only when the JSON-stringified payload differs from the previous attempt -- prevents a legitimate resubmission-after-edit from tripping a false idempotency_key_conflict"
    - "Route directly on useApiQuery + the exported mutationCapabilityFrom() pure function (rather than useMutationCapability()) when a component needs both the full ApiResult (for ErrorState) and the derived MutationCapability (for D-21 gating) from the same catalog fetch -- avoids a second network call while still reaching the raw failure detail"
    - "Locally-declared, structurally-identical props type instead of importing a shared type, to keep a D-17 lookup-map import confined to a single file that a structural (grep/AST) enforcement test checks -- TypeScript's structural typing still verifies compatibility at the point the component is assigned into the map"
key-files:
  created:
    - console/src/lib/jobTypeForms.ts
    - console/src/components/jobs/new/BacktestJobForm.tsx
    - console/src/components/jobs/new/BacktestJobForm.test.tsx
    - console/src/components/jobs/new/NewJobView.tsx
    - console/src/components/jobs/new/NewJobView.test.tsx
    - console/src/app/jobs/new/page.tsx
    - console/src/components/runs/detail/RunHeaderPanel.test.tsx
  modified:
    - console/src/components/strategy/StrategyOverviewPanel.tsx
    - console/src/components/runs/detail/RunHeaderPanel.tsx

key-decisions:
  - "BacktestJobForm declares its own local props type (structurally identical to jobTypeForms.ts's exported JobSubmissionFormProps) instead of importing it, so NewJobView.tsx remains the only file in the console that references \"jobTypeForms\" -- satisfies both consoleBoundaries.test.ts's single-lookup-map-import scope and this plan's own grep acceptance criterion; TypeScript still checks structural compatibility at the JOB_TYPE_FORMS map-literal assignment."
  - "NewJobView fetches /api/v1/job-types directly via useApiQuery and derives MutationCapability with the already-exported mutationCapabilityFrom() pure function, rather than calling useMutationCapability() -- gives access to the full ApiResult (needed for ErrorState on the type-picker's catalog failure) without a second network call, while still reusing the exact same D-20/D-21 mapping logic every other consumer uses."
  - "D-21 fix (found via advisor review before declaring the plan complete, before the deviation was shipped as part of a task commit): a job-types catalog fetch failure must not hide an already-selected, registered submission form. The branch order was corrected so the unmapped-type fallback and the mapped-form render check JOB_TYPE_FORMS[jobType] before result.ok; a mapped form renders with catalogEntry=undefined and capability derived from mutationCapabilityFrom() on the failed result (visible, disabled, honest-unknown reason) -- only the type-picker itself (jobType === null) still shows ErrorState on catalog failure, since it has nothing else to render without the catalog."
  - "OPS-01 stays Pending in REQUIREMENTS.md. This plan's onNavigate targets /jobs/<job_id>, but that route (app/jobs/[jobId]/page.tsx) does not exist until Plan 12 -- an operator submitting today lands on a 404 after a successful Job creation. 19-07's own SUMMARY explicitly recorded OPS-01's close condition as the console plans landing AND being live-verified; this plan lands the submission UI (component-tested only, no live browser walkthrough occurred) but neither the detail-page half nor the live-verification half is satisfied yet. Confirmed via advisor review before finalizing."

requirements-completed: []  # OPS-01 and ORCH-07 are this plan's frontmatter requirements. ORCH-07 was already Complete (from 19-02's mutation guard) before this plan started -- no change needed. OPS-01 stays Pending: see key-decisions above (route doesn't exist until 19-12; 19-07 requires live verification, not just landed code, to close it).

# Metrics
duration: ~17min
completed: 2026-09-24
---

# Phase 19 Plan 11: New Job Flow, Backtest Form, Strategy Shortcut, Run Back-link Summary

**The generic /jobs/new type-picker dispatching through a single job_type -> form lookup map (D-17), the backtest submission form with catalog pre-fill and per-form-instance idempotent submission (D-08/D-10), the /strategy "Run backtest" deep-link shortcut (D-18), and the run-detail "Created by Job <id>" back-link (D-07) -- all disabled-with-reason when mutations are off (D-21), including on a job-types catalog fetch failure.**

## Performance

- **Duration:** ~17 min
- **Started:** 2026-09-24T13:06:17Z
- **Completed:** 2026-09-24T13:22:34Z
- **Tasks:** 3 (+ 1 post-review fix)
- **Files modified:** 9

## Accomplishments
- `jobTypeForms.ts` declares `JOB_TYPE_FORMS: Readonly<Record<string, ComponentType<JobSubmissionFormProps>>> = { backtest: BacktestJobForm }` — the console's map (1), imported only by `NewJobView.tsx` (verified: `grep -rln "jobTypeForms" console/src` returns exactly that one non-test file)
- `BacktestJobForm.tsx` requires `strategy_id`/`from_date`/`to_date` (D-08, submit disabled until all three are set), pre-fills dates from the catalog's `submission_defaults` when present and otherwise leaves them empty (never computed client-side — verified: zero `new Date()`/`Date.now()` occurrences), and submits with one `Idempotency-Key` per form instance, reused across an identical-payload retry and rotated the moment the payload changes (T-19-11-02)
- Submission outcomes are honest: `202`/non-replayed `200` navigate to `/jobs/{job_id}`; a true replay (`Idempotency-Replayed: true`) shows "Already submitted — opening existing Job" and still navigates; a `409 idempotency_key_conflict` shows the conflict copy, keeps the operator's input, and never navigates
- `NewJobView.tsx` renders the catalog-driven type picker (`/jobs/new`, no `?type`), dispatches to the mapped form (`/jobs/new?type=backtest`), or the static "No submission form is available..." fallback for an unmapped type — and, after a post-review fix, keeps a registered form's submit control visible-but-disabled (D-21) rather than replacing it with `ErrorState` when the catalog fetch itself fails
- `app/jobs/new/page.tsx` reads `type`/other query params via `useSearchParams` under a `Suspense` boundary (Next.js 16 requirement) and navigates via `useRouter().push`
- `StrategyOverviewPanel.tsx` gained a header-row "Run backtest" shortcut linking to `/jobs/new?type=backtest&strategy_id=<id>` (D-18) when mutations are enabled, or a disabled button plus the D-21 reason text otherwise
- `RunHeaderPanel.tsx` renders "Created by" → `Job <job_id>` (linking to `/jobs/<job_id>`) immediately after the Run ID row, only when `run.job_id` is non-null (D-07); `RunSummary` gained the `job_id: string | null` field the backend has served since Plan 03

## Task Commits

Each task was committed atomically:

1. **Task 1: Map (1), BacktestJobForm with idempotent submission, tests** - `6e103e6` (feat)
2. **Task 2: New Job view/page (type picker + map dispatch) and Run backtest shortcut** - `ccfdeef` (feat)
3. **Task 3: Run header back-link to originating Job (D-07)** - `56555b6` (feat)

**Post-review fix:** `83cf0cd` (fix) — D-21 catalog-failure branch reorder, found via advisor review after Task 3

**Plan metadata:** commit pending (this SUMMARY + STATE/ROADMAP/REQUIREMENTS tracking commit)

## Files Created/Modified
- `console/src/lib/jobTypeForms.ts` - lookup map (1), `JOB_TYPE_FORMS` + `JobSubmissionFormProps`
- `console/src/components/jobs/new/BacktestJobForm.tsx` - backtest submission form
- `console/src/components/jobs/new/BacktestJobForm.test.tsx` - 8 component tests
- `console/src/components/jobs/new/NewJobView.tsx` - type picker + map dispatch, D-21-safe on catalog failure
- `console/src/components/jobs/new/NewJobView.test.tsx` - 6 component tests
- `console/src/app/jobs/new/page.tsx` - `/jobs/new` route shell
- `console/src/components/strategy/StrategyOverviewPanel.tsx` - "Run backtest" shortcut (D-18)
- `console/src/components/runs/detail/RunHeaderPanel.tsx` - "Created by Job <id>" back-link (D-07)
- `console/src/components/runs/detail/RunHeaderPanel.test.tsx` - 2 component tests

## Decisions Made
See `key-decisions` in frontmatter above (local props type to keep map-1 import single-site, direct `useApiQuery` + `mutationCapabilityFrom()` in `NewJobView` for `ErrorState` access, the D-21 catalog-failure fix, and the OPS-01 Pending determination).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] NewJobView hid an already-selected, registered submission form on a job-types catalog fetch failure (D-21 violation)**
- **Found during:** advisor review after Task 3, before declaring the plan complete
- **Issue:** The original branch order checked `result.ok` before dispatching on `jobType`, so any catalog fetch failure (e.g. a `500` from `GET /api/v1/job-types`) rendered `ErrorState` for every route, including `/jobs/new?type=backtest` — completely hiding the `Submit Backtest` control instead of leaving it visible-but-disabled with the honest-unknown reason. This directly contradicted the plan's own must-have ("when capability is 'disabled' or 'unknown', ... render disabled with the capability reason text visible"), D-21 ("stay visible but disabled"), and the UI-SPEC's "Catalog unavailable" copy row.
- **Fix:** Reordered `NewJobView`'s branches so the unmapped-type fallback and the mapped-form dispatch both check `JOB_TYPE_FORMS[jobType]` before `result.ok`. A mapped form now renders with `catalogEntry={undefined}` and `capability` derived from `mutationCapabilityFrom(result)` on the failed result (which already produces the exact "Mutation availability unknown — GET /api/v1/job-types failed" reason) — only the type-picker itself (`jobType === null`, which has nothing else to render without a loaded catalog) still shows `ErrorState`.
- **Files modified:** `console/src/components/jobs/new/NewJobView.tsx`
- **Verification:** New regression test — catalog `500` + `jobType="backtest"` — asserts `Submit Backtest` is present and disabled with the honest-unknown reason visible. Whole-suite `npm test` (14 files / 111 tests, up from 110), `npx tsc --noEmit` clean, `npm run lint` clean.
- **Committed in:** `83cf0cd` (separate `fix(19-11)` commit, after the three task commits)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** No scope creep — a correctness fix to code shipped in this same plan (Task 2), caught before the plan was declared complete. All originally-specified test cases pass unchanged; one new regression case added.

## Issues Encountered
None beyond the deviation above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Plan 12 (Job detail screen composition) creates `app/jobs/[jobId]/page.tsx`, which is what `BacktestJobForm`'s `onNavigate` and `JobsTable`'s "View" links already target — no further wiring needed on this plan's side once that route lands.
- `OPS-01` will be eligible to close once Plan 12's detail page is live and the full Console → HTTP → Job → worker → backtest-service path is live-verified end to end (not just component-tested), per 19-07's recorded close condition.
- No blockers. Baseline before this plan: 11 files / 95 tests. After this plan: 14 files / 111 tests, all green (`npm test`); `npx tsc --noEmit` clean; `npm run lint` clean.

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*

## Self-Check: PASSED

- All 9 created/modified files under `console/src/` and this SUMMARY.md confirmed present on disk.
- All 4 commits (`6e103e6`, `ccfdeef`, `56555b6`, `83cf0cd`) confirmed present in `git log --oneline --all`.
- `npm test` (console/): 14 files / 111 tests passed.
- `npx tsc --noEmit` (console/): clean.
- `npm run lint` (console/): clean.
