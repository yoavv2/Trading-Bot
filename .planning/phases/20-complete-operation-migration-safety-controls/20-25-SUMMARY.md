---
phase: 20-complete-operation-migration-safety-controls
plan: 25
subsystem: jobs, market-data-ingestion
tags: [ingest-bars, D-08a, gap-closure, uat-gap-1, OPS-05]
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "ingest-bars Job type, MarketDataIngestionRun.job_id linkage (20-03, 20-14, 20-21)"
provides:
  - "Service-owned _derive_run_status predicate: zero succeeded symbols means run FAILED"
  - "IngestionAllSymbolsFailedError + IngestionResult.raise_for_all_symbols_failed()"
  - "ingest-bars Job FAILED/handler_error (retryable) when every symbol fails"
  - "D-08a amendment and D-05 invariant-2 scope note in 20-CONTEXT.md"
affects: [phase-21-history-failure-indicator, 20-HUMAN-UAT test 5c re-verification]
tech-stack:
  added: []
  patterns:
    - "Service-defined typed error raised by a result method, propagated unchanged by the handler (precedent: SymbolMetadataSyncFailedError)"
    - "Status derived once in the service and copied into a required, default-less result field"
key-files:
  created: []
  modified:
    - src/trading_platform/services/data.py
    - src/trading_platform/services/ingestion.py
    - src/trading_platform/jobs/handlers/ingest_bars.py
    - tests/test_market_data_ingestion.py
    - tests/test_ingest_bars_job_type.py
    - tests/test_market_data_job_types_e2e.py
    - .planning/phases/20-complete-operation-migration-safety-controls/20-CONTEXT.md
key-decisions:
  - "D-08a: 0 succeeded symbols (including 0 requested) is FAILED; >=1 ok and >=1 failed stays PARTIAL with a SUCCEEDED Job"
  - "All-fail wins over a concurrent cancel: handler raises the typed error before the post-call raise_if_cancelled()"
  - "No new JobFailureReason value and no alembic migration"
requirements-completed: [OPS-05]
duration: ~35min
completed: 2026-09-29
---

# Phase 20 Plan 25: ingest-bars all-symbols-fail semantics (D-08a) Summary

An ingest-bars Job in which every symbol fails now ends with a FAILED ingestion run and a FAILED/handler_error Job (retryable); a partial ingest (at least one ok, at least one failed) stays a SUCCEEDED Job with a PARTIAL run.

## Accomplishments

- **Service-owned rule (invariant 2):** `services.ingestion._derive_run_status(succeeded_count, failed_count, run_error)` is a pure predicate: `failed` when a run-level error exists or zero symbols succeeded, `partial` when at least one succeeded and one failed, else `succeeded`; negative counts raise `ValueError`. A symbol counts as succeeded when its fetch and upsert complete without raising (zero bars counts).
- **Typed result:** `IngestionRunStatus` Literal, `IngestionAllSymbolsFailedError(RuntimeError)`, and `IngestionResult.run_status` (required keyword-only, no default) plus `run_error_message` and `raise_for_all_symbols_failed()`. `run_status` is computed once and equals the persisted row status.
- **Handler:** `IngestBarsJobHandler.run` adds `run_status` to the `ingest_bars_completed` log context and calls `result.raise_for_all_symbols_failed()` after that log and before the post-call `raise_if_cancelled()`. The 8-key result summary is unchanged.
- **Docs:** D-08a decision and D-05 scope note in 20-CONTEXT.md; WR-A-02 "open product decision" wording retired in 20-SECURITY, 20-VALIDATION, 20-VERIFICATION, 20-REVIEW-FIX, 20-REVIEW-FIX-part-A-jobs and 20-14-SUMMARY.

## Exact all-fail error_message format

`0 of {n} symbols succeeded; failed: {T1} ({ExcClass1}), {T2} ({ExcClass2})` in request order. Example: `0 of 2 symbols succeeded; failed: AAPL (PolygonAuthError), SPY (PolygonAuthError)`. Exception class names only, never `str(exc)` (T-20-25-01). Job `failure_message` is `IngestionAllSymbolsFailedError: Ingestion run {run_id} failed: {error_message}`.

## Task Commits

1. Task 1, D-08a doc amendment: `8641925`
2. Task 2, service predicate, typed error, `run_status`: `18860c4`
3. Task 3, handler propagation plus handler and E2E tests: `8497a3c`

## Tests added

- `tests/test_market_data_ingestion.py`: `test_derive_run_status_truth_table` (10 rows), `test_derive_run_status_rejects_negative_counts`, `test_ingestion_run_status_literal_is_closed`, `test_ingestion_result_run_status_is_required`, `test_all_symbols_failed_error_shape`, and in `TestIngestionAllFailSemantics`: `test_ingest_all_symbols_failed_finalizes_failed_run`, `test_ingest_mixed_exception_classes_named_per_symbol`, `test_ingest_empty_bars_counts_as_success`, `test_ingest_one_ok_one_failed_is_partial_without_error`. Existing happy-path and `test_ingest_records_failed_symbol` gained `run_status` assertions.
- `tests/test_ingest_bars_job_type.py`: `test_handler_raises_when_all_symbols_failed`, `test_cancel_during_all_fail_call_lands_failed_not_cancelled`, `test_partial_result_returns_summary_without_raising`. `_fake_result` defaults `run_status="succeeded"`.
- `tests/test_market_data_job_types_e2e.py`: `test_ingest_bars_all_symbols_failed_fails_job` (includes retry returning 202) and `test_ingest_bars_one_ok_one_fail_stays_succeeded`.

## Verification

- Full backend suite: 1040 passed.
- Targeted suites (ingestion, job type, E2E, job resources, service job links): all pass; ruff clean on all touched source and test files.
- `tests/test_orchestration_boundaries.py` run read-only: 47 passed.
- NO_MIGRATION and USER_FILES_UNCHANGED gates both print as required. In `ingest_bars.py`, `raise_for_all_symbols_failed()` (line 89) sits after the `ingest_bars_completed` log (line 76) and before the post-call `raise_if_cancelled()` (line 95).

## Deviations from Plan

None. The plan was executed as written. TDD note: the new service tests were written first and failed at collection (missing `IngestionAllSymbolsFailedError`), then went green after implementation. The Task 3 tests passed on first run because the handler change and tests were written together; no separate RED commit was made for them.

## Residuals

- T-20-25-04 (accepted): the run-level outer `except` path still writes `str(exc)` into `error_message` (pre-existing CR-B-01 behaviour, unchanged).
- `tests/test_orchestration_boundaries.py` was not touched or staged; none of the six user-owned files were modified or staged.

## Known Stubs

None.

## Self-Check: PASSED

Commits 8641925, 18860c4 and 8497a3c exist on main. All modified source, test and doc files are present.
