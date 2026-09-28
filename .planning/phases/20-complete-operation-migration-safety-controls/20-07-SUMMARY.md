---
phase: 20-complete-operation-migration-safety-controls
plan: 07
subsystem: jobs
tags: [jobs, risk-engine, pydantic, nextjs, console, tdd]

# Dependency graph
requires:
  - phase: 20-03
    provides: shared payload_fields.py validators (parse_iso_date, map_validation_error, require_registered_strategy, require_trading_session_not_future, latest_completed_session_default)
  - phase: 20-04
    provides: JobCancellationMode.STEP_BOUNDARY, registry.retry_prerequisite_for
  - phase: 20-05
    provides: services/risk.py::run_risk_evaluation(strategy_id, as_of_session=, trigger_source=, settings=, registry=, job_id=) -> RiskRunReport
  - phase: 20-06
    provides: console/src/components/jobs/new/jobFormKit.tsx (useJobFormSubmission, JobFormFooter, StrategySelectField)
provides:
  - RiskEvaluationSubmissionSpec + RiskEvaluationPayloadRejection (strict {strategy_id, as_of_session} public Job contract)
  - RiskEvaluationJobHandler (single run_risk_evaluation call, step-boundary cancellation, BACKTEST execution mode)
  - RiskEvaluationJobForm.tsx (unwired form, ready for Plan 16 registration)
affects: [20-16, 20-19]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Phase 20 Job-type vertical slice: spec (payload_fields.py helpers) + handler (two-checkpoint cancellation, step-only progress, honest fixed-key result_summary) + unit tests + unwired console form, mirroring the P19 backtest precedent"

key-files:
  created:
    - src/trading_platform/jobs/handlers/risk_evaluation_submission.py
    - src/trading_platform/jobs/handlers/risk_evaluation.py
    - tests/test_risk_evaluation_job_type.py
    - console/src/components/jobs/new/RiskEvaluationJobForm.tsx
    - console/src/components/jobs/new/RiskEvaluationJobForm.test.tsx
  modified: []

key-decisions:
  - "The plan's illustrative rejection example 'as_of_session as int' maps to invalid_date (parse_iso_date raises PayloadFieldRejection.INVALID_DATE for any non-str/non-date input), not invalid_field_type as the plan's ordering implied. Used strategy_id=123 for the invalid_field_type test case (matching the backtest_submission.py precedent) and as_of_session='2024-13-01' for invalid_date, covering all 7 closed enum values without bending the validator to fit the plan's prose."
  - "OPS-02 left Pending in REQUIREMENTS.md: registration in build_default_registry (Plan 16) and the E2E test (Plan 19) are both still outstanding, per the established Phase 19/20 precedent (19-01, 19-03, 19-06, 19-07) of not marking a requirement complete until every plan closing it has landed."
  - "risk_evaluation.py declares job_type = \"risk-evaluation\" as a literal string (not imported from risk_evaluation_submission.py) after a post-hoc review found the initial draft imported RISK_EVALUATION_JOB_TYPE across handlers/ modules, which the plan's own action text restricts against ('Imports: services.* and jobs contracts/registry only'); fixed in a follow-up fix(20-07) commit to match backtest.py's literal job_type = \"backtest\" precedent before any later Phase 20 handler could copy the wrong pattern."

patterns-established: []

requirements-completed: []

# Metrics
duration: ~25min
completed: 2026-09-28
---

# Phase 20 Plan 07: risk-evaluation Job Type Summary

**RiskEvaluationSubmissionSpec + RiskEvaluationJobHandler (strict {strategy_id, as_of_session} payload, run_risk_evaluation called once with job_id threading, two cancellation checkpoints) plus an unwired RiskEvaluationJobForm console component, all TDD (test-first, 21 pytest + 17 vitest cases green).**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 completed
- **Files modified:** 5 (all new)

## Accomplishments
- `RiskEvaluationSubmissionSpec`: strict `extra="forbid"` pydantic validation delegating to the shared `payload_fields.py` helpers (strategy registry check, non-future/trading-session check), closed 7-value `RiskEvaluationPayloadRejection` enum, `submission_defaults()` reading the latest completed session with zero writes, no `retry_prerequisite_job_type` declared.
- `RiskEvaluationJobHandler`: single `run_risk_evaluation` call passing `job_id=context.job_id` and `trigger_source="job"`, bracketed by two `raise_if_cancelled()` checkpoints, step-only progress (`resolving strategy` / `evaluating risk` / `recording result`, never a percent), `required_execution_mode = ExecutionMode.BACKTEST`, and a JSON-serializable 5-key `result_summary`.
- `tests/test_risk_evaluation_job_type.py`: 21 tests — one parametrized case per rejection enum value, normalization, both `submission_defaults` paths (Postgres-backed), registry contract, no-retry-prerequisite, and the full handler cancellation/progress/result-summary/failure-propagation/execution-mode/never-writes-status matrix.
- `RiskEvaluationJobForm.tsx`: Strategy `<select>` + `as_of_session` date input composed over the Plan 06 `jobFormKit`, submitting `{job_type: "risk-evaluation", payload: {strategy_id, as_of_session}}` with one rotating Idempotency-Key; button reads "Submit Risk Evaluation"; deliberately not registered in `JOB_TYPE_FORMS` (Plan 16's scope).

## Task Commits

Each task was committed via TDD RED -> GREEN pairs:

1. **Task 1: RiskEvaluationSubmissionSpec + RiskEvaluationJobHandler + unit tests**
   - `de2e364` (test) — 21 failing tests (ImportError, neither module existed)
   - `deb1367` (feat) — spec + handler, 21/21 green, ruff clean
   - `7a451f7` (fix) — post-hoc review deviation: switched `risk_evaluation.py`'s `job_type` from an imported `RISK_EVALUATION_JOB_TYPE` constant to a literal `"risk-evaluation"` string, matching `backtest.py`'s precedent and the plan's own import restriction; 21/21 still green, ruff clean
2. **Task 2: RiskEvaluationJobForm**
   - `cf43582` (test) — failing vitest suite (component did not exist)
   - `2b12b4f` (feat) — form component, 17/17 vitest green (component + consoleBoundaries), `tsc --noEmit` and eslint clean

**Plan metadata:** `664c297` (docs: complete risk-evaluation job type plan) — this commit was created via `gsd-sdk query commit`, which does not expose a trailer argument, so it is missing the session's required `Co-Authored-By` trailer; disclosed rather than amended (see Deviations below). A follow-up `317402c` records this in STATE.md.

## Files Created/Modified
- `src/trading_platform/jobs/handlers/risk_evaluation_submission.py` - `RiskEvaluationSubmissionSpec`, `RiskEvaluationPayloadRejection`, `RISK_EVALUATION_JOB_TYPE`
- `src/trading_platform/jobs/handlers/risk_evaluation.py` - `RiskEvaluationJobHandler`
- `tests/test_risk_evaluation_job_type.py` - 21 unit tests for both
- `console/src/components/jobs/new/RiskEvaluationJobForm.tsx` - risk-evaluation submission form
- `console/src/components/jobs/new/RiskEvaluationJobForm.test.tsx` - 4 vitest cases (pre-fill, disabled-until-filled, submit/navigate/Idempotency-Key, mutations-disabled)

## Decisions Made
- Mapped the plan's "as_of_session as int" rejection example to `invalid_date` (not `invalid_field_type`) after tracing `payload_fields.parse_iso_date`'s actual behavior, and used `strategy_id=123` for the `invalid_field_type` case instead — see key-decisions above.
- Left OPS-02 Pending in REQUIREMENTS.md (spec/handler/form ship here; registry registration is Plan 16, E2E proof is Plan 19) — see key-decisions above.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug/consistency] risk_evaluation.py imported across handlers/ modules, violating the plan's own import restriction**
- **Found during:** post-implementation advisor review, before handoff
- **Issue:** The initial `risk_evaluation.py` set `job_type = RISK_EVALUATION_JOB_TYPE`, importing that constant from `risk_evaluation_submission.py`. The plan's action text for this file says "Imports: services.* and jobs contracts/registry only," and `backtest.py` (the named template) declares its `job_type` as a literal string instead of importing a sibling constant.
- **Fix:** Changed `RiskEvaluationJobHandler.job_type` to the literal `"risk-evaluation"` and removed the cross-module import.
- **Files modified:** `src/trading_platform/jobs/handlers/risk_evaluation.py`
- **Verification:** `21/21` tests still green; `ruff check src/trading_platform/jobs/handlers` clean; all grep-count acceptance criteria re-verified.
- **Committed in:** `7a451f7`

---

**Total deviations:** 1 auto-fixed (Rule 1)
**Impact on plan:** Behavior-neutral (the constant and the literal have the same value); brings the file back in line with both the plan's stated import scope and the precedent this plan explicitly exists to establish for the six remaining Phase 20 handler plans.

## Issues Encountered
- A full pytest suite run (`.venv/bin/python -m pytest -q`, no flags) reported `693 passed, 1 warning, 1 error`, with the error attributed to `tests/test_alpaca_execution.py::test_run_paper_order_submission_persists_idempotent_paper_orders` (a teardown-stage error on a test that had passed). An immediate re-run of the same full suite reported `693 passed` with no error, and the test also passes in isolation. `test_alpaca_execution.py` collects well before `tests/test_risk_evaluation_job_type.py` in suite order and the pass count was unchanged between runs, so this plan's additions did not cause it; whether the flake predates this plan is not established from these two observations alone. Not investigated further (out of this plan's file scope) — logged in `deferred-items.md`.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `risk-evaluation` is fully unit-tested and ready for `build_default_registry` registration (Plan 16) and the E2E vertical-slice test (Plan 19), following the `backtest` precedent exactly.
- No blockers.

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

All 6 claimed files found on disk; all claimed commit hashes (de2e364, deb1367, cf43582, 2b12b4f, da05e9f, 664c297, 317402c, 7a451f7) found in git log.
