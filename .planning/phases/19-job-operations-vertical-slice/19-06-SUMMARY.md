---
phase: 19-job-operations-vertical-slice
plan: 06
subsystem: jobs
tags: [pydantic, job-framework, backtest, execution-mode, strenum]

# Dependency graph
requires:
  - phase: 19-01
    provides: "strategy_runs.job_id FK/UNIQUE, JobFailureReason.CONFIG_INVALID"
  - phase: 19-03
    provides: "run_backtest(job_id=...) threading, Job resources[]"
  - phase: 19-04
    provides: "JobRegistry.register validates description/cancellation_mode/submission_defaults"
  - phase: 19-05
    provides: "required_mode_preflight duck-typed on handler.required_execution_mode; run-jobs boots at BACKTEST level"
provides:
  - "BacktestSubmissionSpec: strict pydantic validation, 7 closed rejection reasons, exchange-calendar clock, read-time submission_defaults"
  - "BacktestJobHandler: single run_backtest call bracketed by two cancellation checkpoints, step-only progress, D-22 required_execution_mode"
  - "build_default_registry() registers backtest as the sole production Job type"
  - "SC9: single exact-set registry pin replacing all five Phase 17/18 emptiness tripwires"
  - "test_job_framework_modules_import_no_domain_layers, test_default_registry_handlers_declare_execution_mode, test_job_context_protocol_is_frozen boundary tests"
affects: [19-07, 20-orchestration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "jobs/handlers/ package: concrete Job type handlers/specs may import services.*; framework modules (queue/lifecycle/runner/dependencies/cancellation/context/contracts/progress) must not"
    - "Local (function-body) import of handler modules inside build_default_registry to avoid a jobs/registry.py <-> jobs/handlers/* circular import"
    - "pydantic strict-shape model + fixed-precedence error-class mapping (extra_forbidden > missing > field-located > catch-all) for one stable machine-readable rejection reason per InvalidJobPayloadError"

key-files:
  created:
    - src/trading_platform/jobs/handlers/__init__.py
    - src/trading_platform/jobs/handlers/backtest_submission.py
    - src/trading_platform/jobs/handlers/backtest.py
    - tests/test_backtest_job_type.py
  modified:
    - src/trading_platform/jobs/registry.py
    - tests/test_orchestration_boundaries.py
    - tests/test_job_mutation_e2e.py
    - tests/test_job_registry.py

key-decisions:
  - "grep-precision over readability: registry.register(BacktestJobHandler(...), submission_spec=...) kept on one physical line so the plan's acceptance-criteria grep pattern 'register(BacktestJobHandler' matches (ruff E501 is repo-wide excluded, per 12-07)."
  - "OPS-01 left Pending in REQUIREMENTS.md -- this plan delivers the registry/handler/service layer only; the requirement's literal text needs an operator-visible UI path, which is plan 19-07's scope (precedent: 19-01, 19-03)."

patterns-established:
  - "Job type handler placement: trading_platform.jobs.handlers.<type>[_submission] is the standing location for every future Phase 20 operation type."

requirements-completed: []

# Metrics
duration: 35min
completed: 2026-09-24
---

# Phase 19 Plan 06: Backtest Job Type Registration Summary

**Registered `backtest` as the first production Job type: a strict pydantic-validated submission spec (7 closed rejection codes, exchange-calendar future-date check) and a cancellation-aware handler wrapping the existing `run_backtest` service, replacing all five Phase 17/18 registry-emptiness tripwires with one exact-set pin (SC9).**

## Performance

- **Duration:** ~35 min
- **Tasks:** 3 completed
- **Files modified:** 8 (4 created, 4 modified)

## Accomplishments
- `BacktestSubmissionSpec.validate_payload` enforces `{strategy_id, from_date, to_date}` with `extra="forbid"`, never defaults a date (D-08), and judges "future" against the exchange-local date via an injectable clock (never the host date) — 7 stable `BacktestPayloadRejection` codes, one test case each.
- `BacktestSubmissionSpec.submission_defaults` computes `{from_date, to_date}` from the latest completed session at read time (D-10), returning `None` when no session exists.
- `BacktestJobHandler` calls `run_backtest(..., trigger_source="job", job_id=context.job_id)` exactly once between two `raise_if_cancelled()` checkpoints (D-12), reports step-only progress with no `percent` (D-16), never touches `StrategyRunStatus` (D-13), and declares `required_execution_mode = ExecutionMode.BACKTEST` (D-22, consumed unchanged by 19-05's `required_mode_preflight`).
- `build_default_registry()` now registers `backtest` as the sole production Job type; `list_job_types() == ["backtest"]`.
- All five Phase 17/18 registry-emptiness tripwires replaced by `test_default_registry_registers_exactly_the_phase19_job_types` (SC9), plus three new boundary pins: `test_job_framework_modules_import_no_domain_layers`, `test_default_registry_handlers_declare_execution_mode` (D-22), `test_job_context_protocol_is_frozen` (D-03).
- Full suite: 580 passed, 0 failed.

## Task Commits

Each task was committed atomically:

1. **Task 1: BacktestSubmissionSpec** - `f7d6f4c` (feat)
2. **Task 2: BacktestJobHandler** - `ca25e89` (feat)
3. **Task 3: Register backtest + replace tripwires** - `c5fd1c7` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified
- `src/trading_platform/jobs/handlers/__init__.py` - package docstring documenting the handler-module import boundary
- `src/trading_platform/jobs/handlers/backtest_submission.py` - `BacktestPayloadRejection`, `BacktestSubmissionSpec` (D-08/D-09/D-10)
- `src/trading_platform/jobs/handlers/backtest.py` - `BacktestJobHandler` (D-11/D-12/D-13/D-16/D-06/D-22)
- `tests/test_backtest_job_type.py` - 25 tests covering both spec and handler
- `src/trading_platform/jobs/registry.py` - `build_default_registry` registers `backtest` via a function-body import
- `tests/test_orchestration_boundaries.py` - removed 3 tripwires, added 4 boundary tests (SC9 pin, framework import boundary, D-22, D-03)
- `tests/test_job_mutation_e2e.py` - removed the 2-assertion emptiness/disjoint tripwire and its now-unused import
- `tests/test_job_registry.py` - `test_build_default_registry_is_empty_in_phase_17` replaced with `test_build_default_registry_registers_backtest`

## Decisions Made
- `registry.register(BacktestJobHandler(...), submission_spec=BacktestSubmissionSpec(...))` kept on a single physical line specifically to satisfy the plan's `grep -c "register(BacktestJobHandler"` acceptance check; the repo's ruff config already excludes E501 (12-07 precedent) so this has no lint cost.
- OPS-01 stays `Pending` in REQUIREMENTS.md: this plan delivers the registration/handler/service layer end of the vertical slice, not the operator-visible Console → HTTP path. Marking it Complete here would overclaim per the 19-01/19-03 precedent already recorded in STATE.md; plan 19-07 is expected to close it end-to-end.
- `job_id`/`strategy_id`/date fields flow through the pydantic model as the single source of shape validation; semantic checks (strategy existence, date ordering, future-date) run afterward in `validate_payload` against the already-typed values, keeping the precedence rule (extra > missing > field-located > date) simple and exhaustively testable.

## Deviations from Plan

None - plan executed exactly as written. The two structural notes above (single-line register call, OPS-01 left Pending) are plan-consistent applications of the plan's own acceptance criteria and the project's established Pending-until-end-to-end convention, not deviations from what the plan specified.

## Issues Encountered
None. Local venv and PostgreSQL were both available and used directly (the STATE.md ENVIRONMENT blocker noting a broken `.venv` was stale — `.venv/bin/python` exists and works).

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `backtest` is fully registered, validated, and handler-executable; the worker (19-05) can now actually dispatch and complete a real backtest Job end-to-end at the service layer.
- Plan 19-07 (or later) still needs to wire the Console submission form / job-type catalog consumption and drive the full Console → HTTP → Job → worker → `run_backtest` path to close OPS-01 literally.
- No blockers for subsequent Phase 19 plans.

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*

## Self-Check: PASSED

All created files and task commit hashes verified present on disk / in git log.
