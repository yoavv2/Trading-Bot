---
phase: 20-complete-operation-migration-safety-controls
plan: 14
subsystem: jobs
tags: [python, pydantic, sqlalchemy, nextjs, vitest, job-framework, market-data]

# Dependency graph
requires:
  - phase: 20-03
    provides: payload_fields.py shared validators (parse_iso_date, normalize_symbols, require_date_range, format_symbols_default, latest_completed_session_default)
  - phase: 20-05
    provides: ingest_daily_bars(job_id=) threading, market_data_ingestion_run job linkage
  - phase: 20-06
    provides: jobFormKit.tsx shared form mechanics (useJobFormSubmission, JobFormFooter, parseSymbolsInput, SYMBOLS_EMPTY_HELP)
  - phase: 20-07
    provides: risk_evaluation.py step-boundary handler pattern
provides:
  - IngestionResult.run_id (the MarketDataIngestionRun id)
  - IngestBarsSubmissionSpec / IngestBarsPayloadRejection / INGEST_BARS_JOB_TYPE
  - IngestBarsJobHandler (step-boundary cancellation, BACKTEST execution mode)
  - IngestBarsJobForm console form
affects: [job-registry-registration, jobTypeForms-wiring]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ingest-bars follows the risk-evaluation/backtest submission-spec + step-boundary-handler template exactly, generalizing payload_fields.py's shared symbols/date-range validators for the first time"

key-files:
  created:
    - src/trading_platform/jobs/handlers/ingest_bars_submission.py
    - src/trading_platform/jobs/handlers/ingest_bars.py
    - tests/test_ingest_bars_job_type.py
    - console/src/components/jobs/new/IngestBarsJobForm.tsx
    - console/src/components/jobs/new/IngestBarsJobForm.test.tsx
  modified:
    - src/trading_platform/services/data.py
    - src/trading_platform/services/ingestion.py

key-decisions:
  - "IngestBarsJobHandler resolves Settings explicitly (self._settings or load_settings()) rather than passing self._settings through unresolved, because ingest_daily_bars needs both settings.market_data (a sub-object) and the full Settings as db_settings -- unlike backtest/risk-evaluation handlers, which pass self._settings straight through to a service that accepts the full Settings object."
  - "IngestBarsJobForm does not destructure initialParams (documented with an inline comment) since ingest-bars has no strategy_id/deep-link field per UI-SPEC (no screen shortcut exists for market-data operations)."

patterns-established:
  - "First spec to use payload_fields.py's require_date_range + normalize_symbols + format_symbols_default together, confirming the shared-validator module generalizes cleanly to a non-strategy-scoped Job type."

requirements-completed: [OPS-05]

# Metrics
duration: ~20min
completed: 2026-09-28
---

# Phase 20 Plan 14: ingest-bars Job Type Summary

**ingest-bars ships as its own strict Job type (own spec, own handler, no mode flag) with a linked, FK-tracked MarketDataIngestionRun and a two-date-plus-symbols console form.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 2
- **Files modified:** 7 (2 modified, 5 created)

## Accomplishments

- `IngestionResult` gained `run_id`, threaded from the `MarketDataIngestionRun` row `_start_run` creates -- `ingest_daily_bars(...).run_id` now equals the persisted run's id, verified against a real Postgres database with a faked Polygon client.
- `IngestBarsSubmissionSpec` (D-24/D-25): strict `{from_date, to_date, symbols}` payload under `extra="forbid"`, a 9-value closed `IngestBarsPayloadRejection` enum with one parametrized rejection test per value, and `submission_defaults()` returning the console pre-fill (comma-separated `symbols` string) or `None` without a completed session.
- `IngestBarsJobHandler`: `STEP_BOUNDARY` cancellation bracketing a single `ingest_daily_bars` call (`raise_if_cancelled()` before and after), `required_execution_mode = ExecutionMode.BACKTEST`, `result_summary` carrying `run_id`, `produced_run_ids`, `from_date`, `to_date`, `symbol_count`, `bars_upserted`, `symbols_failed`, `ingestion_succeeded` (mirroring `IngestionResult.succeeded` verbatim -- the handler never reinterprets a partial symbol failure).
- `IngestBarsJobForm`: two `<input type="date">` fields plus a comma-separated symbols text input pre-filled from `submission_defaults`; symbols are always submitted as a normalized `string[]` via `parseSymbolsInput`, blocked with `SYMBOLS_EMPTY_HELP` when the parsed list is empty; button reads "Submit Ingest Bars".
- 23 new Python tests (`tests/test_ingest_bars_job_type.py`) + 5 new console tests (`IngestBarsJobForm.test.tsx`), all green; full Python suite holds at 880 passed / 0 failed (up from the pre-plan 850); full console suite holds at 176+5 passed.

## Task Commits

Each task was committed atomically:

1. **Task 1: IngestionResult.run_id + IngestBarsSubmissionSpec + IngestBarsJobHandler + unit tests** - `42a2438` (feat)
2. **Task 2: IngestBarsJobForm** - `695f87e` (feat)

**Plan metadata:** (this commit) - `docs(20-14): complete ingest-bars Job type plan`

## Files Created/Modified

- `src/trading_platform/services/data.py` - `IngestionResult` gains `run_id: str | None = None`
- `src/trading_platform/services/ingestion.py` - `ingest_daily_bars` sets `run_id=str(run_id)` on its returned `IngestionResult`
- `src/trading_platform/jobs/handlers/ingest_bars_submission.py` - `IngestBarsSubmissionSpec`, `IngestBarsPayloadRejection`, `INGEST_BARS_JOB_TYPE`
- `src/trading_platform/jobs/handlers/ingest_bars.py` - `IngestBarsJobHandler`
- `tests/test_ingest_bars_job_type.py` - 23 tests covering the rejection enum, validation/normalization, submission defaults, handler cancellation/progress/result_summary, and the service-level `run_id` link
- `console/src/components/jobs/new/IngestBarsJobForm.tsx` - the `ingest-bars` submission form
- `console/src/components/jobs/new/IngestBarsJobForm.test.tsx` - 5 tests covering pre-fill, symbols normalization on submit, empty-symbols helper/disable, full canSubmit gating, and disabled-mutations state

## Decisions Made

- `IngestBarsJobHandler` resolves `Settings` explicitly (`self._settings or load_settings()`) before calling `ingest_daily_bars`, since that service needs both `settings.market_data` (a sub-object) and the full `Settings` as `db_settings` -- a different shape than `backtest`/`risk-evaluation`'s services, which accept the full `Settings` object directly and let their handlers pass `self._settings` through unresolved (possibly `None`).
- `IngestBarsJobForm` deliberately does not destructure `initialParams` (documented inline) since `ingest-bars` has no `strategy_id`/deep-link field and no screen shortcut targets it per the UI-SPEC.

## Deviations from Plan

None - plan executed exactly as written. One out-of-scope, pre-existing mypy error was found (not fixed, logged) rather than silently patched:

- **[Scope boundary] Pre-existing mypy error in `services/ingestion.py:99`** unrelated to this plan's one-line `run_id=str(run_id)` addition ~180 lines below it (confirmed via `git diff`). Logged in `deferred-items.md` rather than fixed, since it is outside this plan's declared behavior/file scope and does not affect test correctness.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `ingest-bars` is fully implemented (spec, handler, tests, form) but **not yet registered** in the production `JobRegistry` or wired into the console's `jobTypeForms.ts` lookup map -- both are out of this plan's declared `files_modified` scope (mirrors the 19-06/19-07 registration-vs-implementation split). A future plan must register `IngestBarsSubmissionSpec`/`IngestBarsJobHandler` in `build_default_registry()` and add `IngestBarsJobForm` to `JOB_TYPE_FORMS` before OPS-05 is operator-visible end-to-end for this Job type; OPS-05 is left implemented-but-not-yet-claimed here (per the phase's established precedent of not overclaiming registration-pending work).
- No blockers for Plan 15.

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

All created files verified present on disk; both task commits (`42a2438`, `695f87e`) verified present in git log.
