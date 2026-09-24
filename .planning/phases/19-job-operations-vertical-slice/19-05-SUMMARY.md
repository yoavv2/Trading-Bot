---
phase: 19-job-operations-vertical-slice
plan: 05
subsystem: jobs
tags: [job-framework, worker, config-validation, docker-compose, postgres]

requires:
  - phase: 19-job-operations-vertical-slice
    provides: "19-01 migration 0020 (JobFailureReason.CONFIG_INVALID enum value already present); 19-02 ORCH-07 mutations_enabled flag default False"
provides:
  - "Generic pre-dispatch preflight hook on jobs/runner.py (execute_job/run_worker_loop) that lands a Job FAILED/config_invalid before any JobContext/heartbeat exists"
  - "services/config/validation.py: config_failure_message(payload, *, mode) -> str | None, secret-safe field-naming failure text"
  - "worker/commands/run_jobs.py boots at BACKTEST level (no broker credentials required) and injects required_mode_preflight, which validates a handler's duck-typed required_execution_mode per dispatch"
  - "docker-compose.yml worker service runs run-jobs (not the placeholder serve loop); local api service has mutations enabled"
  - "tests/test_deploy_config.py: yaml.safe_load proof no deploy config starts the serve loop, local/render mutation defaults correct"
  - "tests/test_job_runner_preflight.py: end-to-end D-22 proof against real Postgres"
affects: [20-complete-operation-migration-and-safety-controls]

tech-stack:
  added: []
  patterns:
    - "Injected preflight callable: jobs/runner.py stays free of trading_platform.services imports; worker/commands/run_jobs.py owns the closure that reaches into services.config.validation."
    - "Duck-typed handler attribute (required_execution_mode) read via getattr, not added to the frozen JobHandler/JobContext Protocol contracts."

key-files:
  created:
    - tests/test_deploy_config.py
    - tests/test_job_runner_preflight.py
  modified:
    - src/trading_platform/jobs/runner.py
    - src/trading_platform/services/config/validation.py
    - src/trading_platform/worker/commands/run_jobs.py
    - docker-compose.yml
    - tests/test_startup_validation.py

key-decisions:
  - "preflight lives in jobs/runner.py (test-enforced: test_run_jobs_is_a_thin_worker_loop_adapter forbids JobStatus/select(/apply_job_transition in run_jobs.py), obtained via an injected JobPreflight callable rather than a direct services import in runner.py"
  - "Adapted tests/test_startup_validation.py::test_run_jobs_command_exits_before_worker_loop_constructed to the new BACKTEST-level boot: an invalid PAPER config no longer exits this gate (D-22), so the CFG-06 ordering proof now uses an unreachable-DB payload instead of empty broker keys"

requirements-completed: [ORCH-05]

duration: ~40min
completed: 2026-09-24
---

# Phase 19 Plan 05: Worker Runner Switch + Config Preflight Summary

**Compose worker now runs the production Job runner (`run-jobs`) instead of the placeholder `serve` loop; the worker boots at BACKTEST level with no broker credentials, and each Job type's declared `ExecutionMode` is validated immediately before dispatch via an injected preflight hook that lands failures as FAILED/`config_invalid` without crashing the worker.**

## Performance

- **Duration:** ~40 min
- **Completed:** 2026-09-24T11:40:23Z
- **Tasks:** 2
- **Files modified:** 6 (4 modified, 2 created — `tests/test_startup_validation.py` was an additional required adaptation beyond the plan's declared `files_modified`)

## Accomplishments
- `jobs/runner.py`'s `execute_job`/`run_worker_loop` gained an optional injected `preflight: JobPreflight | None` callable, landing a non-`None` return as FAILED/`config_invalid` before any `DatabaseJobContext`/heartbeat thread exists — `handler.run` is never invoked and zero `job_logs` rows are written for that attempt.
- `services/config/validation.py` gained `config_failure_message(payload, *, mode)`, a secret-safe wrapper around `validate_config` that returns only sorted, de-duplicated dotted field paths (never expected-shape text, never a configured value).
- `worker/commands/run_jobs.py` now boots via `enforce_startup_config(mode=ExecutionMode.BACKTEST)` (no broker credentials required to start) and supplies `required_mode_preflight`, which reads a handler's duck-typed `required_execution_mode` and calls `config_failure_message` freshly per dispatch.
- `docker-compose.yml`: worker service command switched to `["python", "-m", "trading_platform.worker", "run-jobs"]`; api service environment gained `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED: "true"` for local use (`.env.example` already had this from 19-02; `render.yaml` already set it to `"false"` from 19-02).
- Worker survives a config-invalid Job and keeps processing: proven end-to-end in `test_worker_continues_after_config_invalid` (a PAPER-mode Job fails config_invalid, the following BACKTEST-mode Job on the same `run_worker_loop(max_jobs=2)` call SUCCEEDED).

## Task Commits

1. **Task 1: Runner preflight hook + validation helper + worker wiring** - `ebd775e` (feat)
2. **Task 2: Compose worker switch + preflight and deploy-config tests** - `b07e1e9` (feat)

**Plan metadata:** (this commit) `docs: complete 19-05 plan`

## Files Created/Modified
- `src/trading_platform/jobs/runner.py` - `JobPreflight` type alias; `preflight` kwarg on `execute_job`/`run_worker_loop`; pre-dispatch FAILED/`CONFIG_INVALID` branch mirroring the existing unregistered-job-type branch
- `src/trading_platform/services/config/validation.py` - `config_failure_message(payload, *, mode) -> str | None`
- `src/trading_platform/worker/commands/run_jobs.py` - BACKTEST-level boot; `required_mode_preflight(handler) -> str | None`; wired into `run_worker_loop(preflight=...)`
- `docker-compose.yml` - worker `command` -> `run-jobs`; api `MUTATIONS_ENABLED: "true"`
- `tests/test_deploy_config.py` (new) - 4 tests: compose worker runs job runner, no deploy config starts serve, compose enables local mutations, render disables mutations
- `tests/test_job_runner_preflight.py` (new) - 8 tests: config_invalid-before-dispatch, secret-safe message, worker survival across a failed+succeeded pair, undeclared-mode fail-closed, contained preflight exception, unchanged no-preflight (Phase 17) behavior, `run_jobs_command` boots at BACKTEST level, `config_failure_message` contract
- `tests/test_startup_validation.py` - adapted `test_run_jobs_command_exits_before_worker_loop_constructed` to the new BACKTEST-level boot (see Deviations)

## Decisions Made
- Preflight placement follows the plan's stated design choice exactly (injected callable owned by `run_jobs.py`, write owned by `runner.py`) — this is the only shape that satisfies `test_run_jobs_is_a_thin_worker_loop_adapter`'s literal-source-text prohibition on `JobStatus`/`select(`/`apply_job_transition` in `run_jobs.py`.
- `required_execution_mode` stays a duck-typed `getattr` read on `JobHandler`, not an addition to the frozen `jobs/contracts.py` Protocol (D-03 precedent: that contract stays frozen for the phase). A handler lacking the attribute fails closed (`config_invalid`, "declares no required_execution_mode") rather than running unchecked.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Adapted a pre-existing test whose premise the plan's required BACKTEST-boot change invalidated**
- **Found during:** Task 1 verification (`tests/test_startup_validation.py::test_run_jobs_command_exits_before_worker_loop_constructed` failed)
- **Issue:** This Phase-10 test asserted `run_jobs_command` exits at the startup gate on an invalid PAPER config (empty broker keys) before `run_worker_loop` is constructed. D-22 (this plan's explicit, must-have requirement) changes `run_jobs_command` to boot at BACKTEST level, where empty broker keys are valid — so the test's premise became false and it started calling the mocked `run_worker_loop` (which raises `AssertionError` by design), rather than raising `SystemExit`.
- **Fix:** Rewrote the test to keep its actual invariant (CFG-06 ordering: the startup gate exits before `run_worker_loop` is ever constructed) using an unreachable-DB payload (`_refused_port_db_payload()`, the same helper the file's own DB-unreachable tests use) instead of empty broker credentials, monkeypatching `run_jobs_commands.enforce_startup_config` the same way the file's existing `test_api_lifespan_rejects_empty_alpaca_keys_when_db_is_unreachable` test does for the API lifespan.
- **Files modified:** `tests/test_startup_validation.py`
- **Verification:** `PYTHONPATH=src .venv/bin/pytest tests/test_startup_validation.py -q` — 12 passed. Full suite also green (553/553).
- **Committed in:** `ebd775e` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug/pre-existing-test-adaptation)
**Impact on plan:** Necessary consequence of the plan's own explicit D-22 requirement; no scope creep — no other test file needed adjustment (confirmed by full-suite run).

## Issues Encountered
- The local `.venv` flagged as broken in STATE.md's "Active v1.3 concerns" (no `python` interpreter, stale `pyvenv.cfg`) was found working during this session (`.venv/bin/python` present, Python 3.13, imports succeed, `pytest`/`yaml`/`psycopg`/`alembic` all available) — no venv recreation was needed. Leaving the STATE.md concern note for the orchestrator to resolve/remove since a stale note about a now-working venv is misleading, but not editing it directly here since that concern isn't scoped to this plan's `files_modified`.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- ORCH-05 fully satisfied end-to-end (compose worker runs `run-jobs`; `test_deploy_config.py` proves no deploy config starts `serve`) and marked Complete in REQUIREMENTS.md.
- D-22's worker-side mechanism (BACKTEST-level boot, per-type mode preflight, `config_invalid` failure reason, worker survival) is fully implemented and tested — ready for Phase 19's remaining plans to register the `backtest` Job handler with `required_execution_mode = ExecutionMode.BACKTEST` and rely on this preflight without any further `jobs/runner.py` or `worker/commands/run_jobs.py` changes.
- No blockers identified for subsequent Phase 19 plans (backtest handler registration, catalog wiring, console Job UI).

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*
