---
phase: 20-complete-operation-migration-safety-controls
plan: 15
subsystem: jobs
tags: [python, pydantic, sqlalchemy, nextjs, vitest, job-framework, market-data]

# Dependency graph
requires:
  - phase: 20-03
    provides: payload_fields.py shared validators (normalize_symbols, require_date_range, exchange_today, format_symbols_default), services/symbol_metadata_sync.py::sync_symbol_metadata, services/calendar.py::sync_market_sessions
  - phase: 20-04
    provides: JobDomainConflictError/translate_domain_conflicts pattern (not used here -- neither service opens a lock) and retry_prerequisite_for registry contract
  - phase: 20-06
    provides: jobFormKit.tsx shared form mechanics (useJobFormSubmission, JobFormFooter, parseSymbolsInput, SYMBOLS_EMPTY_HELP)
  - phase: 20-07
    provides: risk_evaluation.py step-boundary spec+handler template
provides:
  - SyncSymbolMetadataSubmissionSpec / SyncSymbolMetadataPayloadRejection / SYNC_SYMBOL_METADATA_JOB_TYPE
  - SyncSymbolMetadataJobHandler (step-boundary cancellation, BACKTEST execution mode, raise_for_failures after completion log)
  - SyncMarketSessionsSubmissionSpec / SyncMarketSessionsPayloadRejection / SYNC_MARKET_SESSIONS_JOB_TYPE
  - SyncMarketSessionsJobHandler (step-boundary cancellation, BACKTEST execution mode, no direct DB session)
  - SyncSymbolMetadataJobForm / SyncMarketSessionsJobForm console forms (unwired)
affects: [job-registry-registration, jobTypeForms-wiring]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "sync-symbol-metadata's submission_defaults is the first Phase 20 spec to derive its defaults purely from configured settings (market_data.metadata.universe) rather than a DB session read -- always available, never None."
    - "sync-market-sessions's submission_defaults is the first Phase 20 spec to derive its defaults purely from the injected clock's exchange-local date (exchange_today) rather than a latest-completed-session DB query -- also always available, never None."

key-files:
  created:
    - src/trading_platform/jobs/handlers/sync_symbol_metadata_submission.py
    - src/trading_platform/jobs/handlers/sync_symbol_metadata.py
    - tests/test_sync_symbol_metadata_job_type.py
    - src/trading_platform/jobs/handlers/sync_market_sessions_submission.py
    - src/trading_platform/jobs/handlers/sync_market_sessions.py
    - tests/test_sync_market_sessions_job_type.py
    - console/src/components/jobs/new/SyncSymbolMetadataJobForm.tsx
    - console/src/components/jobs/new/SyncSymbolMetadataJobForm.test.tsx
    - console/src/components/jobs/new/SyncMarketSessionsJobForm.tsx
    - console/src/components/jobs/new/SyncMarketSessionsJobForm.test.tsx
  modified: []

key-decisions:
  - "SyncSymbolMetadataJobHandler calls result.raise_for_failures() only after the completion log has already recorded the full synced/skipped/failed split (ORCH-02), so a failed ticker's Job failure is never silent -- pinned by test_failed_tickers_raise_after_completion_log."
  - "SyncMarketSessionsSubmissionSpec.submission_defaults reuses market_data.ingest.default_lookback_days (not a separate metadata-specific setting) for its from_date offset, per the plan's explicit D-25 spec."
  - "SyncMarketSessionsPayloadRejection.INVALID_FIELD_TYPE is kept in the closed 6-value enum (matches the shared PayloadFieldRejection vocabulary and is map_validation_error's residual fallback) even though it is not reachable through this spec's own payload -- both from_date/to_date go through the shared parse_iso_date before-validator, which maps every wrong-typed or malformed value to INVALID_DATE instead, exactly like ingest-bars/backtest's own date fields (neither has a reachable invalid_field_type case via a date field either). Documented in the enum docstring and pinned by a dedicated map_validation_error fallback test rather than a fabricated reachable payload case."
  - "Neither Job type is registered in build_default_registry() nor wired into console/src/lib/jobTypeForms.ts -- both are explicitly deferred to Plan 16, per the plan's own action text and the 20-07/20-08/20-09/20-14 registration-vs-implementation precedent."

patterns-established:
  - "First two Phase 20 specs whose submission_defaults never returns None (no DB dependency), contrasting with the latest-completed-session-derived defaults every prior spec (risk-evaluation, ingest-bars, backtest) uses."

requirements-completed: []  # OPS-05 and ORCH-02 remain Pending: see Decisions Made below.

# Metrics
duration: ~23min
completed: 2026-09-28
---

# Phase 20 Plan 15: sync-symbol-metadata + sync-market-sessions Job Types Summary

**The two remaining OPS-05 market-data Job types ship as strict, step-boundary handlers over the Plan 03 services (`sync_symbol_metadata`, `sync_market_sessions`), each with its own console form, completing the backend+form layer for all three market-data Job types (`ingest-bars`, `sync-symbol-metadata`, `sync-market-sessions`).**

## Performance

- **Duration:** ~23 min
- **Started:** 2026-09-28T18:30:00+03:00 (approx, first commit 18:37)
- **Completed:** 2026-09-28T19:00:44+03:00
- **Tasks:** 3
- **Files modified:** 10 created, 0 modified

## Accomplishments

- `SyncSymbolMetadataSubmissionSpec` + `SyncSymbolMetadataJobHandler`: strict `{symbols}` payload (6-value closed rejection enum), single `sync_symbol_metadata()` call, `raise_for_failures()` after the completion log so a failed ticker lands the Job FAILED (`handler_error`) with the failed tickers named — preserving the retired CLI's exit-1 semantics (ORCH-02).
- `SyncMarketSessionsSubmissionSpec` + `SyncMarketSessionsJobHandler`: strict `{from_date, to_date}` payload (6-value closed rejection enum), single `sync_market_sessions()` call, handler never opens a DB session itself (T-20-15-03 Elevation-of-Privilege mitigation — the calendar service wrapper owns the session).
- Two console forms (`SyncSymbolMetadataJobForm`, `SyncMarketSessionsJobForm`) composed over the shared `jobFormKit` mechanics, matching the `ingest-bars`/`broker-order-sync` precedents exactly.
- 39 new backend tests (19 + 20) plus 10 new console component tests, all green; full backend suite holds at 919 passed (880 baseline + 39), zero regressions.

## Task Commits

Each task was committed atomically:

1. **Task 1: sync-symbol-metadata spec + handler + tests** - `392251e` (feat)
2. **Task 2: sync-market-sessions spec + handler + tests** - `c1682c0` (feat)
3. **Task 3: SyncSymbolMetadataJobForm + SyncMarketSessionsJobForm** - `3a2ba8f` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified

- `src/trading_platform/jobs/handlers/sync_symbol_metadata_submission.py` - Strict `{symbols}` spec, submission_defaults from configured metadata universe
- `src/trading_platform/jobs/handlers/sync_symbol_metadata.py` - Step-boundary handler over `sync_symbol_metadata()`
- `tests/test_sync_symbol_metadata_job_type.py` - 19 tests (11 spec, 8 handler)
- `src/trading_platform/jobs/handlers/sync_market_sessions_submission.py` - Strict `{from_date, to_date}` spec, submission_defaults from injected clock
- `src/trading_platform/jobs/handlers/sync_market_sessions.py` - Step-boundary handler over `sync_market_sessions()`
- `tests/test_sync_market_sessions_job_type.py` - 20 tests (12 spec, 8 handler)
- `console/src/components/jobs/new/SyncSymbolMetadataJobForm.tsx` - Comma-separated symbols form
- `console/src/components/jobs/new/SyncSymbolMetadataJobForm.test.tsx` - 5 component tests
- `console/src/components/jobs/new/SyncMarketSessionsJobForm.tsx` - Two-date form
- `console/src/components/jobs/new/SyncMarketSessionsJobForm.test.tsx` - 5 component tests

## Decisions Made

- `SyncSymbolMetadataSubmissionSpec.submission_defaults()` and `SyncMarketSessionsSubmissionSpec.submission_defaults()` are the first two Phase 20 specs whose defaults never depend on the database (metadata universe from settings; exchange-local today from the injected clock respectively) — both always return a value, never `None`.
- `SyncMarketSessionsPayloadRejection.INVALID_FIELD_TYPE` is kept in the closed enum (matches the shared `PayloadFieldRejection` vocabulary and is `map_validation_error`'s residual fallback, preventing a `ValueError`/500 if that fallback path is ever hit) but is genuinely unreachable via this spec's own payload, since both fields route through the shared `parse_iso_date` before-validator which maps every wrong type to `INVALID_DATE`. Pinned by a dedicated `map_validation_error` fallback test instead of a fabricated reachable rejection case; documented in the enum docstring.
- **OPS-05 and ORCH-02 remain Pending in REQUIREMENTS.md.** All three market-data Job types (`ingest-bars` 20-14, `sync-symbol-metadata` + `sync-market-sessions` this plan) now exist as strict spec+handler pairs with unwired console forms, but per the plan's own action text ("not added to JOB_TYPE_FORMS here (Plan 16)") neither type is registered in `build_default_registry()` nor wired into `console/src/lib/jobTypeForms.ts` — so no operator can actually submit either from the UI yet. OPS-05's literal text ("Operator can run market-data operations from the UI") and ORCH-02's literal text (CLI/scripts boundary enforcement extended to `scripts/`/Makefile) are both still unsatisfied. Mark OPS-05 complete once Plan 16's registry+form-map wiring lands; ORCH-02 needs its own separate scripts/Makefile enforcement plan. This follows the 20-03/20-07/20-08/20-09/20-14 registration-vs-implementation precedent.

## Deviations from Plan

None — plan executed exactly as written. One test-design correction made during Task 2 (documented above under Decisions Made): the plan's behavior bullet describes "one rejection case per `SyncMarketSessionsPayloadRejection` value," but `INVALID_FIELD_TYPE` is structurally unreachable for a two-date-field payload under the shared `parse_iso_date` validator (matching `ingest-bars`/`backtest`'s identical date-field behavior). Rather than fabricate a payload that doesn't actually trigger that rejection reason, the parametrized case was rewritten to pin the real behavior (`wrong_typed_date_maps_to_invalid_date` → `INVALID_DATE`) and a separate test pins `INVALID_FIELD_TYPE` as `map_validation_error`'s residual fallback directly. This was reviewed via `advisor()` before implementation.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All three OPS-05 market-data Job types now exist as strict, independently validated spec+handler pairs with console forms — Plan 16 (registry registration + `JOB_TYPE_FORMS` wiring) can proceed for all three uniformly.
- `SymbolMetadataSyncFailedError` and `MetadataSyncResult`/`MarketSessionSyncResult` (Plan 03) are consumed unchanged; no service-layer changes were needed.
- No blockers.

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

All 11 created files confirmed present on disk; all 4 commit hashes (392251e, c1682c0, 3a2ba8f, bf12bd7) confirmed in `git log`. Full backend suite: 919 passed (baseline 880 + 39 new), 0 failures. Console: `npx vitest run src/components/jobs/new` 54 passed; `npx tsc --noEmit` clean; `npx eslint` clean on all 4 new console files.
