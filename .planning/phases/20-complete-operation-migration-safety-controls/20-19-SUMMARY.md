---
phase: 20-complete-operation-migration-safety-controls
plan: 19
subsystem: testing
tags: [e2e, job-orchestration, risk-evaluation, reconciliation, broker-order-sync, retry, pytest]

# Dependency graph
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-07/08/11 (the three job types), 20-10 (orchestration retry), 20-13 (retry route + job-detail retry_blocked), 20-16 (production registry registration)"
provides:
  - "tests/test_strategy_job_types_e2e.py: 10-case API -> real run-jobs --once -> service E2E over the production create_app() registry for risk-evaluation, reconciliation, broker-order-sync, operator retry and the D-19 reconcile-first block"
  - "A faked-broker seam pattern for PAPER-mode Job types: fake Alpaca creds + monkeypatch of the AlpacaClient symbol on services.reconciliation.report and services.execution.sync_orders"
affects: [20-20-paper-session-retry-wiring, 20-21-market-data-e2e, 20-24-phase-verification]

tech-stack:
  added: []
  patterns:
    - "PAPER-mode E2E: set non-empty fake TRADING_PLATFORM_BROKER__ALPACA__API_KEY/SECRET and monkeypatch the AlpacaClient factory on the service modules (never handler code) with a mutable BrokerScript that can flip to an exploding client"
    - "D-06 test made discriminating: seed paper state, assert the run really found findings (finding_count > 0) AND PaperOrder sync-failure fields are byte-identical before/after"

key-files:
  created:
    - tests/test_strategy_job_types_e2e.py
  modified: []

key-decisions:
  - "Cancel of a QUEUED reconciliation Job asserts HTTP 200, not the plan's 202: the cancel route (api/routes/jobs.py::cancel_job) returns 200 for every accepted cancel and only submit/retry return 202 (same as the existing backtest cancel E2E). The plan text was wrong; the test pins actual, already-shipped route behavior."
  - "Both plan tasks landed in one commit because they share one new file; Task 1 (-k 'not retry') and Task 2 verify commands both pass."
  - "OPS-02, OPS-04, OPS-06 marked Complete (each is proven end-to-end here: production registry, POST /api/v1/jobs, real run-jobs --once, linked run/resources, and the console forms were registered in 20-16). OPS-07 left Pending: this plan proves retry lineage/idempotency/rejection/reconcile-first for risk-evaluation and broker-order-sync, but 20-20 also declares OPS-07 for paper-session retry and is the later closing plan per the phase precedent."

patterns-established:
  - "Failure injection for retry E2E: wrap the handler module's service attribute so it raises on the first call only, then delegates to the real service (retry then succeeds on the real path)"

requirements-completed: [OPS-02, OPS-04, OPS-06]

# Metrics
duration: ~25min
completed: 2026-09-28
---

# Phase 20 Plan 19: Strategy Job Types + Retry E2E Summary

**Production-path E2E (create_app() -> POST /api/v1/jobs -> real `run-jobs --once` -> existing services) proving risk-evaluation, reconciliation, broker-order-sync, operator retry lineage/idempotency, and the D-19 reconcile-first gate with its lift condition, against a faked broker seam.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 (single new test file, single commit)
- **Files created:** 1 (tests/test_strategy_job_types_e2e.py, ~450 lines, 10 test cases)

## Accomplishments
- `test_risk_evaluation_job_runs_through_production_path`: SUCCEEDED, exactly one strategy_run resource whose id is in `produced_run_ids`, run has `trigger_source == "job"` and `job_id == Job id`; broker seam never touched (BACKTEST-mode type).
- `test_reconciliation_job_runs_report_only`: seeds paper state, empty fake broker, run really produces findings (`finding_count > 0`) yet every `PaperOrder` `sync_failure_count/last_sync_error/last_sync_failure_at` is unchanged (D-06).
- `test_broker_order_sync_job_runs_with_no_resources`: SUCCEEDED, `resources == []`, `orders_synced`/`fills_ingested` in `result_summary`, `outcome_uncertain` false.
- `test_cancel_queued_reconciliation_never_executes`: sentinel replaces the handler's service function; cancel on QUEUED -> CANCELLED, sentinel and broker seam never invoked.
- `test_missing_as_of_session_rejected_for_each_type` (x3): 422 `invalid_job_payload` / `missing_required_field`, `_counts()` unchanged.
- `test_generated_resources_match_produced_run_ids`: D-09 generalization across all three types.
- `test_retry_failed_risk_evaluation_end_to_end`: first-call failure -> FAILED handler_error (`outcome_uncertain` false); retry 202 with equal job_type/payload, `retry_of_job_id`/`retried_as_job_id` lineage both directions; same key replay 200 + `Idempotency-Replayed: true` + same id; fresh key 409 `retry_exists` with `existing_retry_job_id`; next worker pass SUCCEEDED (real service, second call); retry of the SUCCEEDED retry 409 `job_not_retryable`.
- `test_broker_order_sync_uncertain_failure_requires_reconciliation`: broker fails after `external_broker_sync_started` -> FAILED, `outcome_uncertain` true, detail `retry_blocked == {code, required_job_type, strategy_id}`, retry 409 `reconciliation_required` (no retry Job created), a reconciliation Job SUCCEEDED lifts the block (`retry_blocked` null), retry then 202.

## Task Commits

1. **Task 1 + Task 2: E2E for the three job types, retry, and the reconcile-first block** - `9afd78e` (test)

**Plan metadata:** (docs commit following this summary)

## Files Created/Modified
- `tests/test_strategy_job_types_e2e.py` - new E2E module; reuses `job_operations_env`, `_run_worker_once`, `_counts` from `tests/test_job_operations_e2e.py`, `FakeBrokerClient` from `tests/test_paper_execution.py`, `_seed_paper_operational_state` from `tests/test_analytics_service.py`.

## Decisions Made
See frontmatter key-decisions (cancel route returns 200; single commit for a single file; OPS-07 stays Pending until 20-20).

## Deviations from Plan

### Plan-text corrections

**1. [Plan inaccuracy] Queued-cancel status code is 200, not 202**
- **Found during:** Task 1 (reading `cancel_job` in `api/routes/jobs.py`)
- **Issue:** The plan's D-02 truth says the cancel returns 202; the shipped route always returns 200 for accepted cancels (202 is reserved for submit/retry), and the existing backtest E2E already asserts 200.
- **Fix:** Test asserts 200 plus `status == "cancelled"`; no route change (changing an already-shipped, already-tested contract is out of scope).
- **Files modified:** tests/test_strategy_job_types_e2e.py

### Auto-fixed Issues

None - no production code changed; all tests passed on first run against the shipped mechanism.

## Issues Encountered
None. `-k "not retry"` (Task 1 verify) and the full file both pass; `tests/test_job_operations_e2e.py` still passes alongside (19 passed together).

## Known Stubs
None.

## Threat Flags
None - test-only change. T-20-19-01 mitigated (409 `reconciliation_required` gate and its lift condition pinned end-to-end); T-20-19-02 mitigated (fake credential strings, `FakeBrokerClient` seam, zero network calls; constructed-client counters assert the seam was the only broker path).

## Next Phase Readiness
- OPS-07 remains for 20-20 (paper-session retry wiring); the faked-broker seam pattern here is reusable there.

## Self-Check: PASSED
- tests/test_strategy_job_types_e2e.py exists; commit 9afd78e exists; 10 tests pass.
