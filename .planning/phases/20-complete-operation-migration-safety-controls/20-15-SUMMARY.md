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

requirements-completed: []  # OPS-05 (owned by 20-16, 20-21) and ORCH-02 (owned by 20-24) remain Pending: see Decisions Made below.

# Metrics
duration: ~62min
completed: 2026-09-28
---

# Phase 20 Plan 15: sync-symbol-metadata + sync-market-sessions Job Types Summary

**The two remaining OPS-05 market-data Job types ship as strict, step-boundary handlers over the Plan 03 services (`sync_symbol_metadata`, `sync_market_sessions`), each with its own console form, completing the backend+form layer for all three market-data Job types (`ingest-bars`, `sync-symbol-metadata`, `sync-market-sessions`).**

## Performance

- **Duration:** ~62 min
- **Started:** 2026-09-28T18:37:25+03:00 (first task commit)
- **Completed:** 2026-09-28T19:39:45+03:00 (metadata commit)
- **Tasks:** 3
- **Files modified:** 10 created, 0 modified

## Accomplishments

- `SyncSymbolMetadataSubmissionSpec` + `SyncSymbolMetadataJobHandler`: strict `{symbols}` payload (6-value closed rejection enum), single `sync_symbol_metadata()` call, `raise_for_failures()` after the completion log so a failed ticker lands the Job FAILED (`handler_error`) with the failed tickers named — preserving the retired CLI's exit-1 semantics (ORCH-02). Verified end-to-end against `src/trading_platform/jobs/runner.py:251`: `SymbolMetadataSyncFailedError` (a plain `RuntimeError` subclass) falls into the runner's generic `except Exception` branch, so `failure_message = f"{type(exc).__name__}: {exc}"` includes `str(exc)` and therefore the failed tickers — the must_have's literal "failed tickers in `failure_message`" text is satisfied by the runner's existing generic-exception path, not just by the raised exception's own message.
- `SyncMarketSessionsSubmissionSpec` + `SyncMarketSessionsJobHandler`: strict `{from_date, to_date}` payload (6-value closed rejection enum), single `sync_market_sessions()` call, handler never opens a DB session itself (T-20-15-03 Elevation-of-Privilege mitigation — the calendar service wrapper owns the session).
- Two console forms (`SyncSymbolMetadataJobForm`, `SyncMarketSessionsJobForm`) composed over the shared `jobFormKit` mechanics, matching the `ingest-bars`/`broker-order-sync` precedents exactly.
- 39 new backend tests (19 + 20) plus 10 new console component tests, all green; full backend suite holds at 919 passed (880 baseline + 39), zero regressions.

## Task Commits

Each task was committed atomically:

1. **Task 1: sync-symbol-metadata spec + handler + tests** - `392251e` (feat)
2. **Task 2: sync-market-sessions spec + handler + tests** - `c1682c0` (feat)
3. **Task 3: SyncSymbolMetadataJobForm + SyncMarketSessionsJobForm** - `3a2ba8f` (feat)

**Plan metadata:** `e49bd35` (docs: complete plan)

## Files Created/Modified

- `src/trading_platform/jobs/handlers/sync_symbol_metadata_submission.py` - Strict `{symbols}` spec, submission_defaults from configured metadata universe
- `src/trading_platform/jobs/handlers/sync_symbol_metadata.py` - Step-boundary handler over `sync_symbol_metadata()`
- `tests/test_sync_symbol_metadata_job_type.py` - 19 tests (11 spec, 8 handler)
- `src/trading_platform/jobs/handlers/sync_market_sessions_submission.py` - Strict `{from_date, to_date}` spec, submission_defaults from injected clock
- `src/trading_platform/jobs/handlers/sync_market_sessions.py` - Step-boundary handler over `sync_market_sessions()`
- `tests/test_sync_market_sessions_job_type.py` - 20 tests (13 spec, 7 handler)
- `console/src/components/jobs/new/SyncSymbolMetadataJobForm.tsx` - Comma-separated symbols form
- `console/src/components/jobs/new/SyncSymbolMetadataJobForm.test.tsx` - 5 component tests
- `console/src/components/jobs/new/SyncMarketSessionsJobForm.tsx` - Two-date form
- `console/src/components/jobs/new/SyncMarketSessionsJobForm.test.tsx` - 5 component tests

## Decisions Made

- `SyncSymbolMetadataSubmissionSpec.submission_defaults()` and `SyncMarketSessionsSubmissionSpec.submission_defaults()` are the first two Phase 20 specs whose defaults never depend on the database (metadata universe from settings; exchange-local today from the injected clock respectively) — both always return a value, never `None`.
- `SyncMarketSessionsPayloadRejection.INVALID_FIELD_TYPE` is kept in the closed enum (matches the shared `PayloadFieldRejection` vocabulary and is `map_validation_error`'s residual fallback, preventing a `ValueError`/500 if that fallback path is ever hit) but is genuinely unreachable via this spec's own payload, since both fields route through the shared `parse_iso_date` before-validator which maps every wrong type to `INVALID_DATE`. Pinned by a dedicated `map_validation_error` fallback test instead of a fabricated reachable rejection case; documented in the enum docstring.
- **OPS-05 and ORCH-02 remain Pending in REQUIREMENTS.md.** All three market-data Job types (`ingest-bars` 20-14, `sync-symbol-metadata` + `sync-market-sessions` this plan) now exist as strict spec+handler pairs with unwired console forms, but per the plan's own action text ("not added to JOB_TYPE_FORMS here (Plan 16)") neither type is registered in `build_default_registry()` nor wired into `console/src/lib/jobTypeForms.ts` — so no operator can actually submit either from the UI yet. OPS-05's literal text ("Operator can run market-data operations from the UI") and ORCH-02's literal text (CLI/scripts boundary enforcement extended to `scripts/`/Makefile) are both still unsatisfied. Confirmed neither ID is orphaned: `grep -ln "OPS-05\|ORCH-02" .planning/phases/20-*/20-1[6-9]-PLAN.md .planning/phases/20-*/20-2[0-4]-PLAN.md` returns `20-16-PLAN.md` (registry+form-map wiring), `20-21-PLAN.md` (also lists OPS-05), and `20-24-PLAN.md` (ORCH-01/ORCH-02/ORCH-08 scripts/Makefile boundary enforcement). Mark OPS-05 complete once 20-16 (and/or 20-21) lands and is operator-invocable; mark ORCH-02 complete at 20-24. This follows the 20-03/20-07/20-08/20-09/20-14 registration-vs-implementation precedent.

## Deviations from Plan

Each `tdd="true"` task landed as a single `feat` commit (no separate `test()` RED commit), matching the 20-14 precedent — tests and implementation were developed together and verified green before committing, rather than committing a deliberately-failing RED state first.

### Auto-fixed Issues

**1. [Rule 1 - Test design bug] Rewrote the `sync-market-sessions` `invalid_field_type` rejection test case to match actual reachable behavior**
- **Found during:** Task 2 (`SyncMarketSessionsSubmissionSpec` tests)
- **Issue:** The plan's behavior bullet describes "one rejection case per `SyncMarketSessionsPayloadRejection` value." The first-draft parametrized case (`{"from_date": 123, ...}` → expected `INVALID_FIELD_TYPE`) failed: both `from_date`/`to_date` route through the shared `parse_iso_date` before-validator (`payload_fields.py`), which maps *every* wrong-typed or malformed value to `INVALID_DATE` instead — identical to how `ingest-bars`/`backtest`'s own date fields behave (neither has a reachable `invalid_field_type` case via a date field either; both reach it only via a non-date field, `symbols`/`strategy_id`, that this spec does not have). `INVALID_FIELD_TYPE` is therefore structurally unreachable through this spec's own payload.
- **Fix:** Rewrote the parametrized case to pin the real, reachable behavior (`wrong_typed_date_maps_to_invalid_date` → `INVALID_DATE`) instead of asserting a false expectation. Kept `INVALID_FIELD_TYPE` in the closed 6-value enum (it is `map_validation_error`'s residual fallback, matching the shared `PayloadFieldRejection` vocabulary every Phase 20 spec draws from, and prevents a `ValueError`/500 if that fallback path is ever exercised by a future field addition) and added a dedicated `test_invalid_field_type_member_is_map_validation_error_fallback` that feeds `map_validation_error` a synthetic `int_type` pydantic error and asserts it resolves to `INVALID_FIELD_TYPE`, round-tripping through the spec's own enum. Documented the reachability gap in the enum's docstring.
- **Files modified:** `src/trading_platform/jobs/handlers/sync_market_sessions_submission.py`, `tests/test_sync_market_sessions_job_type.py`
- **Verification:** `.venv/bin/pytest tests/test_sync_market_sessions_job_type.py -q` — 20/20 passed
- **Committed in:** `c1682c0` (part of Task 2 commit)
- **Reviewed via:** `advisor()` before implementation, to confirm the fix should pin actual behavior rather than special-case the shared `parse_iso_date` validator or drop the enum member.

**2. [Rule 2 - Missing critical] Added an explicit `dry_run` absence test for `sync-symbol-metadata`**
- **Found during:** Task 1 (`SyncSymbolMetadataSubmissionSpec`/`SyncSymbolMetadataJobHandler`)
- **Issue:** The plan's Task 1 acceptance criteria include a grep check (`grep -c "dry_run" ... returns 0 for both`) but the behavior bullets described only manual verification, not an automated regression test — a future edit could silently reintroduce a `dry_run`/mode flag (OPS-05 forbids one) without failing the test suite.
- **Fix:** Added `test_no_dry_run_field_anywhere`, asserting `"dry_run" not in inspect.getsource(...)` for both `sync_symbol_metadata.py` and `sync_symbol_metadata_submission.py`.
- **Files modified:** `tests/test_sync_symbol_metadata_job_type.py`
- **Verification:** `.venv/bin/pytest tests/test_sync_symbol_metadata_job_type.py -q` — 19/19 passed
- **Committed in:** `392251e` (part of Task 1 commit)

---

**Total deviations:** 2 auto-fixed (1 test-design correction, 1 missing regression coverage)
**Impact on plan:** Both changes strengthen test coverage/correctness within Task 1/2's own declared scope; no architectural change, no scope creep. Reviewed via `advisor()` prior to and after implementation.

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
