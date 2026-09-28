---
phase: 20-complete-operation-migration-safety-controls
plan: 06
subsystem: ui
tags: [nextjs, react, typescript, vitest, console-api-client]

# Dependency graph
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "Migration 0021 and job_reads.py's payload/retry_of_job_id/retried_as_job_id fields (plan 01/05); the D-19 reconciliation-required retry gate and the control routes are implemented server-side by later plans this plan types against"
provides:
  - "console/src/components/jobs/types.ts: JobDetail gains payload, retry_of_job_id, retried_as_job_id, retry_blocked, cancellation_mode; JobFailureReason gains domain_conflict; JobResource.links.self is optional; JobTypeCatalogItem.cancellation_mode is the closed JobCancellationMode union"
  - "console/src/lib/api.ts: retryJob(jobId, idempotencyKey) client; tripKillSwitch/resetKillSwitch/enableStrategy/disableStrategy PUT clients (no Idempotency-Key, D-10); controlErrorMessage; 9 new MUTATION_ERROR_COPY entries for retry/cancel/control rejection copy"
  - "console/src/components/jobs/new/jobFormKit.tsx: useJobFormSubmission, JobFormFooter, StrategySelectField, parseSymbolsInput/SYMBOLS_EMPTY_HELP -- the shared kit the 7 new job-type forms will compose over"
affects: [20-07-risk-evaluation-and-paper-session-forms, 20-08-reconciliation-and-ingest-forms, 20-09-retry-ui, 20-10-control-routes-and-dialogs, 20-controls-page]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "putJson<T> mirrors postJson's never-throw/parse-defensively shape but drops the Idempotency-Key header and always reports replayed:false on success -- the control-route idempotency contract (explicit target state, D-10) is structurally distinct from the Job-mutation contract (Idempotency-Key, ORCH-03)"
    - "controlErrorMessage(detail: unknown) is the single dispatch point for the three shapes a control-route rejection body can take: {code: string, ...} (mapped via the shared MUTATION_ERROR_COPY table), a plain string (rendered verbatim), or anything else, including an array (falls back to CONTROL_REQUEST_REJECTED_COPY, a single string literal declared once and reused by both the fallback and the invalid_control_request map entry)"
    - "useJobFormSubmission generalizes BacktestJobForm's inline keyRef/lastAttemptRef idempotency-key rotation into a reusable hook parameterized by jobType -- the 7 new forms compose over this hook + JobFormFooter + StrategySelectField instead of copying BacktestJobForm's body"

key-files:
  created:
    - console/src/components/jobs/new/jobFormKit.tsx
    - console/src/components/jobs/new/jobFormKit.test.tsx
  modified:
    - console/src/components/jobs/types.ts
    - console/src/lib/api.ts
    - console/src/lib/api.test.ts
    - console/src/components/jobs/detail/JobHeaderPanel.test.tsx
    - console/src/components/jobs/detail/JobDetailView.test.tsx
    - console/src/lib/cancellationLabel.test.ts

key-decisions:
  - "OPS-07, CTRL-01, CTRL-02, OPS-03 stay Pending in REQUIREMENTS.md -- this plan ships only the console contract layer (types, api.ts clients, form kit) that the operator-visible retry/control UI (later plans) will consume; no Retry button, confirmation dialog, or /controls page exists yet, so none of these requirements' operator-facing behavior is observable (20-05 precedent for shared-ID plans that only ship a partial building block)"
  - "invalid_retry_payload's reason-presence check uses .trim().length > 0 (nonblank), not .length > 0 -- a whitespace-only server-supplied reason still falls back to the generic 'the original payload is no longer valid' copy rather than rendering blank/whitespace text"
  - "JobResource.links changed from { self: string } to { self?: string } to accommodate the market_data_ingestion_run resource kind (D-07), which has no console route and so omits self entirely, rather than sending an empty string"

patterns-established:
  - "Control mutation clients (tripKillSwitch/resetKillSwitch/enableStrategy/disableStrategy) never send Idempotency-Key -- this is the one structural difference from every existing Job mutation client (submitJob/cancelJob/retryJob), and it is enforced by an explicit test asserting the header's absence, not just its presence elsewhere"

requirements-completed: []

# Metrics
duration: 22min
completed: 2026-09-28
---

# Phase 20 Plan 06: Console Contract Layer for Retry/Controls/Form Kit Summary

**Console types.ts + api.ts gain the retry client, the four no-Idempotency-Key control PUT clients (D-10), and 9 pinned rejection-copy strings; a new jobFormKit.tsx generalizes BacktestJobForm's submission mechanics into a shared hook + footer + strategy select + symbols parser for the 7 upcoming job-type forms.**

## Performance

- **Duration:** ~22 min (RED/GREEN cycles across three TDD tasks)
- **Started:** 2026-09-28T12:06:53Z
- **Completed:** 2026-09-28T12:12:49Z
- **Tasks:** 3
- **Files modified:** 8 (2 created, 6 modified)

## Accomplishments
- `JobDetail` now carries every Phase 20 field (`payload`, `retry_of_job_id`, `retried_as_job_id`, `retry_blocked`, `cancellation_mode`) and `JobFailureReason` gains `domain_conflict`, unblocking every later console plan that reads these fields
- `retryJob` and the four control PUT clients (`tripKillSwitch`/`resetKillSwitch`/`enableStrategy`/`disableStrategy`) exist, fully tested against the exact D-10/D-16 wire contracts (headers, bodies, no-Idempotency-Key)
- 9 new `MUTATION_ERROR_COPY` entries pin every Phase 20 rejection string verbatim from the UI-SPEC, including the `invalid_retry_payload` reason-interpolation and the `controlErrorMessage` three-way dispatch (object-with-code / plain-string / defensive-fallback)
- `jobFormKit.tsx` extracts `BacktestJobForm`'s idempotency-key rotation, footer markup, and strategy select into a reusable kit so the 7 new forms will be thin compositions, not 7 more copies

## Task Commits

Each task followed RED (failing test) then GREEN (implementation):

1. **Task 1: Job detail types + retry client + queued-only/retry error copy**
   - `8ec2264` test: add failing tests for retryJob client and retry/cancel error copy
   - `bb2f4e3` feat: add Job detail retry/domain-conflict fields and retryJob client
2. **Task 2: Control PUT client (no Idempotency-Key) + control error copy**
   - `79c0ce5` test: add failing tests for control PUT client and control error copy
   - `f3a94fd` feat: add control-route PUT client with no Idempotency-Key (D-10)
3. **Task 3: Shared job-form kit (submission hook, footer, strategy select, symbols parser)**
   - `489cbee` test: add failing tests for the shared job-form kit
   - `573a240` feat: add shared job-form kit for the seven Phase 20 submission forms

**Plan metadata:** (recorded below, this commit)

_All three tasks used the plan's `tdd="true"` RED/GREEN gate: each RED commit's test run was verified failing (missing export / unresolved import) before the GREEN commit landed._

## Files Created/Modified
- `console/src/components/jobs/types.ts` - JobDetail/JobFailureReason/JobResource/JobTypeCatalogItem Phase 20 fields
- `console/src/lib/api.ts` - retryJob, tripKillSwitch/resetKillSwitch/enableStrategy/disableStrategy, putJson, controlErrorMessage, 9 new MUTATION_ERROR_COPY entries
- `console/src/lib/api.test.ts` - retryJob and control-mutation behavior tests (20 new tests)
- `console/src/components/jobs/detail/JobHeaderPanel.test.tsx` - fixture-only: 5 new JobDetail fields
- `console/src/components/jobs/detail/JobDetailView.test.tsx` - fixture-only: 5 new JobDetail fields
- `console/src/lib/cancellationLabel.test.ts` - fixture-only: 5 new JobDetail fields
- `console/src/components/jobs/new/jobFormKit.tsx` - useJobFormSubmission, JobFormFooter, StrategySelectField, parseSymbolsInput, SYMBOLS_EMPTY_HELP
- `console/src/components/jobs/new/jobFormKit.test.tsx` - kit behavior tests (9 new tests)

## Decisions Made
- See `key-decisions` in frontmatter (requirements stay Pending; nonblank-reason check for invalid_retry_payload; JobResource.links.self made optional).

## Deviations from Plan

None - plan executed exactly as written. The plan's own action text anticipated the fixture-file edits (JobHeaderPanel.test.tsx, JobDetailView.test.tsx, cancellationLabel.test.ts) and the tsc run confirmed exactly those three files needed changes, no more.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `console/src/lib/api.ts` and `console/src/components/jobs/types.ts` are stable for every later Phase 20 console plan (Retry UI, `/controls` page, the 7 new forms) to build on without touching these hot shared files again
- `jobFormKit.tsx` is ready for the 7 new form components; none have been created yet (out of this plan's scope)
- The server-side retry/control routes (`/api/v1/jobs/{id}/retry`, `/api/v1/controls/*`) are not yet implemented as of this plan -- the console client layer types against the contracts other Phase 20 plans (05, 10, 13 per the plan's `<interfaces>` note) implement server-side; full end-to-end verification (`npx vitest run && npx tsc --noEmit`) passed, but no live backend call was exercised
- `npx vitest run` (console): 159 passed (up from 130 baseline); `npx tsc --noEmit`: clean; `npx eslint` on new files: clean

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

All created/modified files and all six task commits (8ec2264, bb2f4e3, 79c0ce5, f3a94fd, 489cbee, 573a240) verified present on disk / in git log.
