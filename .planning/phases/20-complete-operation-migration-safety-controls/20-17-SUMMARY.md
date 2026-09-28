---
phase: 20-complete-operation-migration-safety-controls
plan: 17
subsystem: ui
tags: [nextjs, react, typescript, vitest, job-detail]

# Dependency graph
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-06's console contract layer (retryJob client, JobDetail.payload/retry_of_job_id/retried_as_job_id/retry_blocked/cancellation_mode, MUTATION_ERROR_COPY); 20-13's server-side get_job_detail fields these types were typed against"
provides:
  - "console/src/components/jobs/RetryJobDialog.tsx: job-type-agnostic retry confirmation dialog (RetryJobDialog export)"
  - "console/src/components/jobs/detail/JobHeaderPanel.tsx: Retry trigger with D-20 disabled-reason precedence, retry lineage rows, D-03a queued-only Cancel gating"
  - "console/src/components/jobs/detail/JobResourcesPanel.tsx: honest non-terminal empty-resources copy covering the three new resource-less Job types"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "computeRetryGate() in JobHeaderPanel.tsx: a discriminated union (open/capability/already-retried/blocked) replacing an if/else reason-string cascade, so the Run-reconciliation link renders only when the active gate is literally 'blocked' rather than inferring it from reason-text equality"
    - "RetryJobDialog is mounted whenever job.status is failed/cancelled (not gated on the retry-enabled state), so an error-path onChanged() refetch that flips retried_as_job_id/retry_blocked never unmounts the dialog mid-error -- the trigger's disabled state, not the dialog's mount, carries the gating"

key-files:
  created:
    - console/src/components/jobs/RetryJobDialog.tsx
    - console/src/components/jobs/RetryJobDialog.test.tsx
    - console/src/components/jobs/detail/JobResourcesPanel.test.tsx
  modified:
    - console/src/components/jobs/detail/JobHeaderPanel.tsx
    - console/src/components/jobs/detail/JobHeaderPanel.test.tsx
    - console/src/components/jobs/detail/JobDetailView.tsx
    - console/src/components/jobs/detail/JobDetailView.test.tsx
    - console/src/components/jobs/detail/JobResourcesPanel.tsx
    - console/src/lib/consoleBoundaries.test.ts

key-decisions:
  - "OPS-07 and OPS-03 stay Pending in REQUIREMENTS.md -- this plan ships the operator-visible retry/queued-only-cancel UI (both requirements' literal text is now observable end-to-end for the first time), but 20-19-PLAN.md and 20-20-PLAN.md also declare requirements: [..., OPS-07] and 20-20/20-23 also declare OPS-03, per this phase's established precedent (20-01, 20-03, 20-19 SUMMARYs) of marking a shared-ID requirement Complete only at the plan the orchestrator designates as its closing plan. Marking either Complete here risks a later plan re-closing an already-closed requirement or masking a gap if 20-19/20-20's own scope turns out narrower than assumed."
  - "JobDetailView.test.tsx gained the repo's first next/navigation mock (vi.mock(\"next/navigation\", () => ({ useRouter: () => ({ push: vi.fn() }) })) -- no existing test exercised useRouter() before this plan (app/jobs/new/page.tsx uses it but has no dedicated test); useRouter throws outside an app-router context, so JobDetailView (which now calls it to build JobHeaderPanel's onNavigate) needed the mock to keep rendering under jsdom"
  - "Retry lineage rows ('Retry of Job' / 'Retried as Job') render dt=label / dd=Link(shortId) -- matching the existing blocking_job_id/root_cause_job_id dt/dd row structure literally, with the link text shortened to the first 8 chars per the must_haves wording, rather than embedding the full 'Retry of Job <id>' phrase as a single link text node"

patterns-established:
  - "RetryGate discriminated union pattern (computeRetryGate) is a template other Phase 20 gated-trigger UIs (control confirm dialogs, later plans) can reuse for their own multi-reason precedence instead of independent boolean+string cascades"

requirements-completed: []

# Metrics
duration: ~25min
completed: 2026-09-28
---

# Phase 20 Plan 17: Job Detail Retry Trigger, Lineage, and Queued-Only Cancel Gating Summary

**RetryJobDialog + JobHeaderPanel's D-20 gated Retry trigger/lineage rows and D-03a queued-only Cancel gating, plus JobResourcesPanel's honest non-terminal empty copy -- all built job-type-agnostically on top of Plan 06/13's server-derived fields.**

## Performance

- **Duration:** ~25 min (two TDD tasks, RED/GREEN cycles)
- **Completed:** 2026-09-28
- **Tasks:** 2
- **Files modified:** 9 (3 created, 6 modified)

## Accomplishments

- `RetryJobDialog` ships as a new job-type-agnostic confirmation dialog (`console/src/components/jobs/RetryJobDialog.tsx`), following `CancelJobDialog`'s overlay/key-per-opening mechanics exactly: one `Idempotency-Key` per dialog opening reused across confirm attempts, a generic `dl` payload preview (`Object.entries` + `JSON.stringify` for nested values), 202/200-replayed both navigating away immediately, and any error keeping the dialog open with mapped copy plus an `onChanged()` refetch
- `JobHeaderPanel` gains the Retry trigger (FAILED/CANCELLED Jobs only) with the exact D-20 three-reason precedence (mutations off -> already-retried -> reconciliation-blocked), each mutually exclusive, and a `Run reconciliation` deep-link built entirely from `job.retry_blocked.required_job_type`/`strategy_id` (never a hardcoded job-type string)
- `JobHeaderPanel` gains `Retry of Job`/`Retried as Job` lineage rows (short-id links), rendered only when the respective field is non-null, matching the existing `blocking_job_id`/`root_cause_job_id` row pattern
- D-03a queued-only Cancel gating: a `running` Job with `cancellation_mode: "queued_only"` now shows a disabled Cancel trigger with the exact inline reason `Not cancellable once running`; `queued` (any mode) or `step_boundary` Jobs are unaffected
- `JobResourcesPanel`'s non-terminal empty copy corrected to `No linked resources yet. This panel updates automatically if the Job's run creates one.` -- honest for the three new Job types that never produce a linked resource
- `JobDetailView` threads `onNavigate={(href) => router.push(href)}` into `JobHeaderPanel` via `useRouter()` from `next/navigation`
- `failure_reason: "domain_conflict"` (OPS-08) was verified to already render through the existing generic Failure reason/message rows with zero special-casing -- confirmed by a new test, no component change needed
- `consoleBoundaries.test.ts`'s D-17 job-type-agnostic boundary scan (zero `job_type` equality/`switch`, zero job-type string literals, zero raw `fetch(`) passes unmodified against every new/changed file, including the new `RetryJobDialog.tsx` added to `EXPECTED_JOB_UI_FILES`

## Task Commits

Each task followed RED (failing test) then GREEN (implementation):

1. **Task 1: RetryJobDialog**
   - `438a42b` test: add failing tests for RetryJobDialog
   - `e9f0766` feat: add RetryJobDialog (D-20)
2. **Task 2: JobHeaderPanel retry trigger, lineage, queued-only cancel gating + resources copy**
   - `fdbc567` test: add failing tests for retry trigger, lineage, cancel gating, resources copy
   - `60c5229` feat: retry trigger/lineage, queued-only cancel gating, honest resources copy

Both tasks used the plan's `tdd="true"` RED/GREEN gate: each RED commit's test run was independently verified failing (missing module / stale copy assertions / import error) before the GREEN commit landed.

## Files Created/Modified

- `console/src/components/jobs/RetryJobDialog.tsx` - retry confirmation dialog
- `console/src/components/jobs/RetryJobDialog.test.tsx` - 9 new tests
- `console/src/components/jobs/detail/JobHeaderPanel.tsx` - Retry trigger, lineage rows, queued-only cancel gating
- `console/src/components/jobs/detail/JobHeaderPanel.test.tsx` - 13 new tests; `onNavigate` prop threaded into every existing render call
- `console/src/components/jobs/detail/JobDetailView.tsx` - `useRouter()` + `onNavigate` prop
- `console/src/components/jobs/detail/JobDetailView.test.tsx` - `next/navigation` mock; updated stale empty-resources copy assertion
- `console/src/components/jobs/detail/JobResourcesPanel.tsx` - corrected non-terminal empty copy
- `console/src/components/jobs/detail/JobResourcesPanel.test.tsx` - new test file, 4 tests
- `console/src/lib/consoleBoundaries.test.ts` - `RetryJobDialog.tsx` added to `EXPECTED_JOB_UI_FILES`

## Decisions Made

See `key-decisions` in frontmatter (OPS-07/OPS-03 stay Pending; `next/navigation` mock precedent; lineage row dt/dd shape).

## Deviations from Plan

None - plan executed as written. The plan's action text anticipated exactly the files touched (RetryJobDialog + its test, JobHeaderPanel/JobDetailView/JobResourcesPanel + their tests, consoleBoundaries.test.ts), and `npx tsc --noEmit` confirmed no other file needed an `onNavigate` update beyond `JobHeaderPanel.test.tsx`.

## Issues Encountered

None. `useRouter()` (Next 16.2.10, per `console/node_modules/next/dist/docs/01-app/03-api-reference/04-functions/use-router.md`) required no `Suspense` boundary (unlike `useSearchParams()`), so `JobDetailView`'s existing composition needed no structural change beyond the hook call and mocking it in the one test file that renders through it.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Full verification: `cd console && npx vitest run` -- 216 passed (up from 192 baseline); `npx tsc --noEmit` -- clean; `npx eslint` on every created/modified file -- clean
- The `RetryGate` discriminated-union pattern in `JobHeaderPanel.tsx` is available as a precedent for the shared `ControlConfirmDialog`'s own multi-reason gating in later Phase 20 plans
- No live backend call was exercised (server-side `/api/v1/jobs/{id}/retry` and the D-19 `retry_blocked` derivation were typed against by Plan 06/13's contracts, per those plans' own summaries) -- full end-to-end operator verification of retry remains for this phase's live-verify checkpoint

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

All created files and all four task commits (438a42b, e9f0766, fdbc567, 60c5229) verified present on disk / in git log below.
