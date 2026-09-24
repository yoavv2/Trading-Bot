---
phase: 19-job-operations-vertical-slice
plan: 07
subsystem: testing
tags: [pytest, fastapi-testclient, job-framework, backtest, e2e]

# Dependency graph
requires:
  - phase: 19-01
    provides: "strategy_runs.job_id FK/UNIQUE"
  - phase: 19-02
    provides: "ORCH-07 mutation guard (default disabled; tests enable explicitly)"
  - phase: 19-03
    provides: "job_id threading into run_backtest, Job resources[]"
  - phase: 19-04
    provides: "GET /api/v1/job-types catalog"
  - phase: 19-05
    provides: "run-jobs worker command, BACKTEST-level boot preflight"
  - phase: 19-06
    provides: "backtest registered as the sole production Job type (BacktestSubmissionSpec + BacktestJobHandler)"
provides:
  - "tests/test_job_operations_e2e.py: 9 tests proving OPS-01's Console->HTTP->Job->worker->backtest-service path against the production registry (no test-only handler override)"
  - "SC1 proof: submit -> run-jobs --once -> succeeded, progress 100%, resources[]/result_summary.run_id linkage, log/event codes, run-detail job_id/trigger_source back-link"
  - "SC2 proof: identical Idempotency-Key replay returns 200 + Idempotency-Replayed:true + same job_id; exactly one StrategyRun exists after two worker passes"
  - "SC7 proof (queued + running cancellation outcomes, D-12/D-13): cancelled QUEUED Job never executes; cancel issued mid-run_backtest lands the Job CANCELLED post-call while the linked run keeps its real SUCCEEDED status"
  - "D-09 proof: all four backtest payload rejections return typed 422 over HTTP with zero rows written to jobs/job_mutations/job_events/strategy_runs"
  - "D-10 proof: GET /api/v1/job-types lists exactly backtest with cancellation_mode step_boundary and submission_defaults derived from the seeded sessions"
affects: [19-08, 19-09, 19-10, 19-11, 19-12]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Production-registry E2E pattern: create_app() with no job_registry override, so the lifespan's build_default_registry(settings) is exercised end to end, not a test-local handler/spec."
    - "Mid-execution cancellation proof: monkeypatch the handler module's imported run_backtest name with a wrapper that issues the real HTTP cancel call (via the same TestClient) before delegating to the captured real function, deterministically simulating a cancel arriving while the service call is in flight -- no sleeps."

key-files:
  created:
    - tests/test_job_operations_e2e.py
  modified: []

key-decisions:
  - "Job.result_summary is a non-nullable JSON column defaulting to {} (src/trading_platform/db/models/job.py), not None -- both cancellation tests assert result_summary == {} rather than is None, correcting the plan's must_haves wording against the actual schema default."
  - "GET /api/v1/runs/{run_id} nests the run under a top-level 'run' key alongside 'artifact_counts' (OperatorReadService.get_run_detail) -- both run-detail assertions read response.json()['run'], not the top-level body."
  - "OPS-01 and JOBUI-04 left Pending in REQUIREMENTS.md: both requirements' literal text requires an operator-visible Console/UI path ('Operator can run a backtest from the UI', 'Operator can cancel a non-terminal Job from the console'). This plan proves the full backend vertical slice via pytest/TestClient only -- no console code was touched. Marking either Complete here would overclaim per the 19-01/19-03/19-06 precedent; both close once the Phase 19 console plans (job list/detail/cancel UI, submission form) land and are live-verified."

patterns-established:
  - "tests/test_job_operations_e2e.py is the canonical 'no test-only handler' E2E location for future production Job types (Phase 20) to extend or mirror."

requirements-completed: []

# Metrics
duration: ~25min
completed: 2026-09-24
---

# Phase 19 Plan 07: Job Operations Vertical Slice E2E Summary

**9-test production-path E2E (`tests/test_job_operations_e2e.py`) proving a backtest Job travels HTTP submit -> `JobOrchestrationService` -> the registered `backtest` handler -> the real `run-jobs` worker command -> `services.backtesting.run_backtest`, against the production Job registry with no test-only handler override.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 completed
- **Files modified:** 1 (created)

## Accomplishments
- `test_backtest_job_runs_through_production_path` (SC1): full submit -> `run-jobs --once` -> observe cycle against `create_app()`'s production registry; asserts `status="succeeded"`, `progress.percent==100`, `resources[0]` linkage (`kind=strategy_run`, `status=succeeded`, `links.self`), `result_summary.run_id == resources[0].id` (D-06), log event codes `backtest_run_started`/`backtest_run_completed`, event types `submitted`/`succeeded`, and the run-detail back-link (`job_id`, `trigger_source="job"`, D-07/D-11).
- `test_idempotent_resubmission_creates_one_run` (SC2): identical `Idempotency-Key` replay returns `200` + `Idempotency-Replayed: true` + the same `job_id`; after two worker passes exactly one `StrategyRun` is linked to that Job and exactly one BACKTEST-type run exists overall.
- `test_job_types_catalog_lists_backtest_with_defaults` (D-10): catalog lists exactly `backtest` with `cancellation_mode="step_boundary"` and `submission_defaults.to_date` matching the seeded data's latest completed session.
- `test_backtest_payload_rejections_write_nothing` (D-09, parametrized x4): `unknown_strategy_id`, `from_date_after_to_date`, `to_date_in_future`, `unknown_payload_keys` each return `422` with the exact typed detail shape and zero new rows across all four audited tables.
- `test_cancel_queued_backtest_never_executes` (SC7 queued): cancelling a QUEUED Job returns `200`/`cancelled`; after the worker runs, the Job stays `cancelled` with `resources==[]`, empty logs, zero linked `strategy_runs` rows, and the trimmed cancellation reason persisted.
- `test_cancel_running_backtest_acknowledged_after_service` (SC7 running, D-12/D-13): a wrapper around the handler's `run_backtest` call issues a real cancel HTTP request mid-execution (asserting the immediate response is `status="running"` with the request recorded), then delegates to the real service. The Job lands `cancelled` with both cancellation timestamps set and `result_summary=={}`, while the linked run stays `succeeded` and remains reachable via `resources[]` and `GET /api/v1/runs/{id}`.
- Full suite: 589 passed, 0 failed.

## Task Commits

Each task was committed atomically:

1. **Task 1: Happy path, idempotent replay, catalog, and HTTP payload rejection E2E** - `9ac779a` (test)
2. **Task 2: Queued and running cancellation outcome E2E (SC7)** - `6b7820c` (test)

**Plan metadata:** (this commit)

## Files Created/Modified
- `tests/test_job_operations_e2e.py` - 9 production-path E2E tests (see Accomplishments); reuses `tests.test_backtest_runner`'s Postgres fixtures (`migrated_backtest_db`, `strategy_config_override`, `_seed_market_data`) via an `__all__` re-export (ruff F401-clean, mirroring `tests/test_backtest_job_type.py`'s precedent).

## Decisions Made
- `Job.result_summary` defaults to `{}` (non-nullable JSON column), not `None` — both cancellation tests assert `== {}`, correcting the plan's `must_haves` wording (`result_summary null`) against the actual model.
- `GET /api/v1/runs/{run_id}` nests the run under `response["run"]` (alongside `artifact_counts`) — both run-detail assertions index into `.json()["run"]`.
- OPS-01 and JOBUI-04 left `Pending` in REQUIREMENTS.md — see Deviations/key-decisions above; both require an operator-visible Console/UI surface this plan does not touch.

## Deviations from Plan

None - plan executed exactly as written. The two structural notes above (`result_summary == {}` instead of `is None`; `.json()["run"]` nesting) are corrections against the actual, already-shipped API/schema shape discovered while writing the tests, not scope changes — the plan's own `must_haves` truths (queued: `resources == []`, running: `resources[0].status == succeeded`, etc.) are satisfied exactly as specified.

## Issues Encountered
None. Local `.venv` and PostgreSQL were both available and used directly (the STATE.md ENVIRONMENT blocker noting a broken `.venv` was already confirmed stale during 19-06).

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- OPS-01's backend vertical slice (registry, handler, worker, idempotency, cancellation, catalog) is now proven end-to-end against the production registry with zero test-only scaffolding remaining in the loop.
- Remaining Phase 19 scope (console Job list/detail/logs/events/cancel UI, submission form, `/jobs/new` type picker, `/strategy` "Run backtest" shortcut, ORCH-05 compose worker switch, run-header back-link) is unblocked and can build directly on this proven backend contract.
- No blockers for subsequent Phase 19 plans.

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*

## Self-Check: PASSED

All created files and task commit hashes verified present on disk / in git log.
