---
phase: 20-complete-operation-migration-safety-controls
plan: 11
subsystem: jobs
tags: [jobs, broker-sync, pydantic, nextjs, console]

# Dependency graph
requires:
  - phase: 20-03
    provides: shared payload_fields.py validators (parse_iso_date, map_validation_error, require_registered_strategy, require_trading_session_not_future, latest_completed_session_default)
  - phase: 20-04
    provides: JobCancellationMode.QUEUED_ONLY, registry.retry_prerequisite_for
  - phase: 20-06
    provides: console/src/components/jobs/new/jobFormKit.tsx (useJobFormSubmission, JobFormFooter, StrategySelectField)
  - phase: 20-08
    provides: ReconciliationSubmissionSpec/ReconciliationJobHandler/ReconciliationJobForm as the direct queued-only structural precedent for this plan's broker-order-sync Job type
provides:
  - "BrokerOrderSyncSubmissionSpec + BrokerOrderSyncPayloadRejection (strict {strategy_id, as_of_session} public Job contract; QUEUED_ONLY cancellation, retry_prerequisite_job_type = \"reconciliation\", D-01/D-19)"
  - "BrokerOrderSyncJobHandler (calls sync_paper_state(strategy_id, as_of_session=..., settings=...) exactly once with strategy_id passed explicitly, external_broker_sync_started marker before the call, zero cancellation checkpoints, PAPER execution mode, produces no run record)"
  - "BrokerOrderSyncJobForm.tsx (unwired form, ready for a later registry-registration plan)"
affects: [20-16, 20-19]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Phase 20 queued-only Job-type vertical slice over an opaque, no-run-record service call: spec (payload_fields.py helpers + JobCancellationMode.QUEUED_ONLY + retry_prerequisite_job_type) + handler (zero cancellation checkpoints, external_* pre-call marker, produced_run_ids == [] since the service creates no StrategyRun) + unit tests + unwired console form -- the no-lock, no-run, reconcile-first-gated sibling of the 20-08 reconciliation and 20-09 paper-session precedents"

key-files:
  created:
    - src/trading_platform/jobs/handlers/broker_order_sync_submission.py
    - src/trading_platform/jobs/handlers/broker_order_sync.py
    - tests/test_broker_order_sync_job_type.py
    - console/src/components/jobs/new/BrokerOrderSyncJobForm.tsx
    - console/src/components/jobs/new/BrokerOrderSyncJobForm.test.tsx
  modified: []

key-decisions:
  - "sync_paper_state takes no job_id or trigger_source parameters (unlike reconcile_paper_execution/run_paper_session), confirmed by direct read of sync_orders.py before writing the handler -- the call is sync_paper_state(strategy_id, as_of_session=..., settings=self._settings) only, with strategy_id always passed explicitly so the runner-settings default (execution.paper_session_runner.default_strategy_id) is never silently used."
  - "No translate_domain_conflicts() wrap: sync_paper_state takes no advisory lock and creates no StrategyRun (verified by reading its body), so no ConcurrentRunLockedError/domain-conflict translation applies here, unlike the paper-session handler."
  - "result_summary is exactly the 9 must_haves keys plus produced_run_ids == [] (no run record is ever created by this Job type) -- the handler test asserts full dict equality, not a subset, mirroring the reconciliation precedent."
  - "The fake sync_paper_state used in handler tests mirrors the real signature (strategy_id positional-or-keyword, as_of_session/settings/registry/broker_client keyword-only) so a stray or renamed kwarg fails loudly instead of silently passing (advisor-reviewed before writing tests)."
  - "broker_order_sync.py's docstring paraphrases the external_broker_sync_started marker and retry_prerequisite_job_type literal in prose rather than repeating them verbatim, so the plan's own pinned single-occurrence greps (external_broker_sync_started count==1 in the handler file; retry_prerequisite_job_type = \"reconciliation\" count==1 in the submission file) hold against the real source lines -- same grep-vs-prose pattern as 20-03/20-08/20-09."
  - "Left OPS-06 Pending in REQUIREMENTS.md (spec/handler/unit tests/unwired form ship here; build_default_registry registration and JOB_TYPE_FORMS console wiring are a later plan's scope, per registry.py's own docstring naming that later plan) -- see requirements-completed note below, mirroring the 20-08/20-09 precedent exactly."

patterns-established: []

requirements-completed: []  # OPS-06 stays Pending: this plan ships BrokerOrderSyncSubmissionSpec/BrokerOrderSyncJobHandler, unit tests, and an unwired form only -- neither OPS-06's "registered Job" text nor an operator-reachable UI path is true until a later plan registers BrokerOrderSyncJobHandler in build_default_registry and wires BrokerOrderSyncJobForm into JOB_TYPE_FORMS (20-08/20-09 precedent).

# Metrics
duration: ~30min
completed: 2026-09-28
---

# Phase 20 Plan 11: Broker Order Sync Job Type Summary

**BrokerOrderSyncSubmissionSpec + BrokerOrderSyncJobHandler wrapping sync_paper_state as a queued-only, reconcile-first-gated Job type, plus BrokerOrderSyncJobForm console form**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-09-28
- **Completed:** 2026-09-28
- **Tasks:** 2 completed
- **Files modified:** 5 (all created, none modified)

## Accomplishments
- `BrokerOrderSyncSubmissionSpec` validates a strict `{strategy_id, as_of_session}` payload with the full closed 7-value `BrokerOrderSyncPayloadRejection` enum, `QUEUED_ONLY` cancellation, and `retry_prerequisite_job_type = "reconciliation"` (D-19)
- `BrokerOrderSyncJobHandler` calls `sync_paper_state(strategy_id, as_of_session=..., settings=...)` exactly once with `strategy_id` always explicit, logging the `external_broker_sync_started` D-19 marker immediately before the call and containing no `raise_if_cancelled` checkpoint (D-01/D-02)
- `result_summary` carries exactly the 9 required keys with `produced_run_ids == []`, since `sync_paper_state` creates no `StrategyRun`
- `BrokerOrderSyncJobForm.tsx` composes the shared `jobFormKit` mechanics (strategy_id + as_of_session fields, "Submit Broker Order Sync" button), unwired per the established later-registration-plan pattern

## Task Commits

Each task followed the TDD RED -> GREEN cycle:

1. **Task 1: BrokerOrderSyncSubmissionSpec + BrokerOrderSyncJobHandler + unit tests**
   - `cf338c5` test(20-11): add failing tests for broker-order-sync Job type
   - `e6a1fe2` feat(20-11): add BrokerOrderSyncSubmissionSpec + BrokerOrderSyncJobHandler
2. **Task 2: BrokerOrderSyncJobForm**
   - `b5aff1b` test(20-11): add failing tests for BrokerOrderSyncJobForm
   - `e56dc44` feat(20-11): add BrokerOrderSyncJobForm

**Plan metadata:** (this commit) docs: complete broker-order-sync job type plan

## Files Created/Modified
- `src/trading_platform/jobs/handlers/broker_order_sync_submission.py` - `BrokerOrderSyncSubmissionSpec`, `BrokerOrderSyncPayloadRejection`, `BROKER_ORDER_SYNC_JOB_TYPE`
- `src/trading_platform/jobs/handlers/broker_order_sync.py` - `BrokerOrderSyncJobHandler`
- `tests/test_broker_order_sync_job_type.py` - 22 tests covering spec + handler
- `console/src/components/jobs/new/BrokerOrderSyncJobForm.tsx` - the console submission form
- `console/src/components/jobs/new/BrokerOrderSyncJobForm.test.tsx` - 4 tests covering pre-fill, submit, disabled states

## Decisions Made
See `key-decisions` in frontmatter for the full list. Highlights: `sync_paper_state` has no `job_id`/`trigger_source` params and takes no lock/creates no run, so the handler is simpler than the `reconciliation`/`paper-session` siblings (no `translate_domain_conflicts()` wrap); OPS-06 stays Pending pending registry registration and console wiring, matching the 20-08/20-09 precedent exactly.

## Deviations from Plan

None - plan executed exactly as written. The advisor's pre-implementation review caught two would-be bugs (wrong `sync_paper_state` call shape copied from the `reconciliation`/`paper-session` precedents, and grep-count literals accidentally repeated in docstrings) before any code was written, so no post-hoc fixes were needed.

## Issues Encountered
None. The known `tests/test_alpaca_execution.py::test_run_paper_order_submission_persists_idempotent_paper_orders` flake tripped once on the full-suite run and passed cleanly on an immediate rerun, per the documented deferred-items.md entry.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
`broker-order-sync` is spec+handler+form complete and ready for the later plan that registers it in `build_default_registry` and wires `BrokerOrderSyncJobForm` into `JOB_TYPE_FORMS` (same scope boundary 20-08/20-09 left for `reconciliation`/`paper-session`). No blockers.

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

All 5 created files verified present on disk; all 4 task commits (cf338c5, e6a1fe2, b5aff1b, e56dc44) verified present in git log.
