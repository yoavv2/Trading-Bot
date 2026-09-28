---
phase: 20-complete-operation-migration-safety-controls
plan: 20
subsystem: testing
tags: [e2e, job-orchestration, paper-session, cancellation, domain-conflict, retry, pytest]

# Dependency graph
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-09 (paper-session spec/handler + job_id threading), 20-10 (orchestration cancel/retry, reconcile-first predicate), 20-13 (cancel/retry routes + retry_blocked on detail), 20-16 (production registry + console form registration), 20-19 (E2E precedent and faked-broker seam)"
provides:
  - "tests/test_paper_session_job_e2e.py: 6-case API -> real run-jobs --once -> real handler/run_paper_session E2E over the production create_app() registry"
  - "Faked-broker seam for the broker-SUBMITTING path: fake Alpaca creds + monkeypatch of AlpacaClient on services.reconciliation.report (state reads) and AlpacaExecutionService on services.execution.submit_orders (order submission), one shared FakeExecutionService for counting submissions"
affects: [20-24-phase-verification]

tech-stack:
  added: []
  patterns:
    - "Mid-run HTTP from inside a wrapped service symbol (handler module attribute) to prove a RUNNING-state API contract, then delegate to the real service"
    - "D-19 lift-predicate negative cases: an earlier same-strategy SUCCEEDED job (via API), a later other-strategy SUCCEEDED row (direct jobs insert, since the API cannot create it), a same-strategy CANCELLED-while-queued job (via API); only a later same-strategy SUCCEEDED job lifts the block"

key-files:
  created:
    - tests/test_paper_session_job_e2e.py
  modified: []

key-decisions:
  - "Queued paper-session cancel asserts HTTP 200, not the plan's 202: the shipped cancel route returns 200 for every accepted cancel (only submit/retry return 202), same as the 20-19 and backtest cancel E2Es. The plan text was wrong; the test pins actual, already-shipped behavior."
  - "The D-19 'submission step raises after the external marker' failure is injected by replacing submit_orders.run_paper_order_submission (module global looked up by run_paper_session at call time) rather than the handler symbol, so the real handler, real external_broker_session_started log, real internal reconciliation and real runner classification are all exercised."
  - "The different-strategy decoy for the D-19 predicate is a direct Job row (job_type reconciliation, SUCCEEDED, completed_at = failure + 1 min, strategy_id 'some_other_strategy'); the registry knows only trend_following_daily so the API cannot produce it. The predicate reads only the jobs table, so this is faithful."
  - "OPS-03, OPS-07, OPS-08 marked Complete: OPS-03 (paper-session registered Job, queued-only cancellation, catalog statement, console form wired in 20-16) and OPS-08 (domain_conflict as a distinct failure_reason on the production path) are proven here; OPS-07 is closed here after 20-19 proved retry lineage/idempotency/rejection for other types and this plan proved it, plus the reconcile-first block and lift, for paper-session (retry dialog exists in the console from earlier plans)."

patterns-established:
  - "Broker-submitting E2E: shared fake execution service counts every order the production path would have sent; assert zero submissions on every non-submitting outcome (queued cancel, blocked, lock conflict, forced failure)"

requirements-completed: [OPS-03, OPS-08, OPS-07]

# Metrics
duration: ~30min
completed: 2026-09-28
---

# Phase 20 Plan 20: Paper-Session Job E2E Summary

**Production-path E2E (create_app() -> POST /api/v1/jobs -> real `run-jobs --once` -> real PaperSessionJobHandler/run_paper_session, broker faked at the AlpacaClient/AlpacaExecutionService symbols) proving two-run linkage, honest queued-only cancellation, blocked-as-SUCCEEDED, domain_conflict, and the D-19 reconcile-first retry block including its exact lift condition.**

## Performance

- **Duration:** ~30 min
- **Tasks:** 2
- **Files created:** 1 (tests/test_paper_session_job_e2e.py, ~540 lines, 6 test cases)

## Accomplishments
- `test_paper_session_job_links_both_runs` (SC1/OPS-03, D-08/D-09): `risk_run_id: null` runs to SUCCEEDED with action `submitted_missing_orders`; detail lists exactly two `strategy_run` resources (reconciliation + paper_execution), equal as a set to `result_summary.produced_run_ids`, both runs carry `job_id == Job id`; `external_broker_session_started` is in the Job logs; the fake broker received exactly the two approved orders.
- `test_cancel_while_running_is_rejected_and_submission_completes` (SC2/D-03, T-20-20-01): a cancel POSTed from inside the wrapped `run_paper_session` returns 409 `job_not_cancellable_running`; the Job ends SUCCEEDED with `cancellation_requested_at`/`cancellation_acknowledged_at`/`cancellation_cause` all null, no `cancellation_requested` JobEvent, and both orders submitted.
- `test_cancel_queued_paper_session_never_executes` (D-02): cancel while QUEUED -> 200/`cancelled`; service sentinel never fires, no broker state client built, zero submissions.
- `test_blocked_session_is_succeeded_with_action` (D-05): strategy disabled via `PUT /api/v1/controls/strategies/...` -> Job SUCCEEDED with `action == "blocked_strategy_disabled"`, only the execution run linked, no broker contact.
- `test_lock_conflict_lands_as_domain_conflict` (SC3/OPS-08/D-04): with `session_run_lock` held by the test for the same (strategy, session), the Job ends FAILED `domain_conflict` (not `handler_error`), `outcome_uncertain` false, message names strategy id and session date, exactly one linked run of type reconciliation, `retry_blocked` null, and `POST /retry` returns 202; nothing submitted.
- `test_uncertain_failure_requires_later_reconciliation_before_retry` (D-19, T-20-20-02): a submission-step failure after the external marker -> FAILED `handler_error`, `outcome_uncertain` true, `retry_blocked == {code: reconciliation_required, required_job_type: reconciliation, strategy_id}`, `POST /retry` 409 with the same detail and no retry Job created. The block survives (1) a same-strategy reconciliation that SUCCEEDED before the failure, (2) a SUCCEEDED reconciliation for another strategy completed after it, and (3) a same-strategy reconciliation cancelled while queued. A later SUCCEEDED same-strategy reconciliation lifts it (`retry_blocked` null) and `POST /retry` returns 202 with `retry_of_job_id == original`, same job_type/payload, QUEUED. Mutation-checked: pointing the decoy at the same strategy makes the test fail.

## Task Commits

1. **Task 1: happy path, blocked outcome, queued and running cancellation** - `8f33149` (test)
2. **Task 2: domain conflict and D-19 reconcile-first retry cycle** - `5bedf2d` (test)

## Verification
- `pytest tests/test_paper_session_job_e2e.py tests/test_concurrency_guard_e2e.py -q`: 8 passed (6 new + 2 existing).
- `ruff check` and `ruff format --check` clean on the new file.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug in plan text] Queued-cancel status code 200, not 202**
- **Found during:** Task 1
- **Issue:** Plan D-02 truth says cancelling a QUEUED paper-session Job returns 202; the shipped cancel route (`api/routes/jobs.py::cancel_job`) returns 200 for every accepted cancel.
- **Fix:** Test asserts 200 (matching 20-19 and the backtest cancel E2E); no production change.
- **Files modified:** tests/test_paper_session_job_e2e.py
- **Commit:** 8f33149

**2. [Scope note] Extra negative case for the D-19 lift predicate**
- Added a same-strategy reconciliation cancelled while queued (SUCCEEDED-only clause of the predicate) beyond the two cases the plan named; no scope impact, test file only.

Otherwise the plan executed as written; no production code changed.

## Known Stubs
None.

## Threat Flags
None. All broker access is faked (fake credential strings, in-memory fakes); no network calls (T-20-20-03).

## Issues Encountered
None. Both task test runs passed on first execution; the D-19 decoy was mutation-checked to confirm the test is not vacuous.

## Self-Check: PASSED
- tests/test_paper_session_job_e2e.py exists; commits 8f33149 and 5bedf2d exist.
