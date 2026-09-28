---
phase: 20-complete-operation-migration-safety-controls
plan: 03
subsystem: jobs
tags: [pydantic, job-framework, market-data, polygon, calendar, validation]

# Dependency graph
requires:
  - phase: 19-job-operations-vertical-slice
    provides: BacktestSubmissionSpec/BacktestJobHandler pattern (P19 D-08/D-09) that this plan's shared validators generalize
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-01: migration 0021 schema spine (job_id FKs, retry_of_job_id); 20-02: pure read-path precedent this plan follows for services.* import discipline"
provides:
  - "payload_fields.py: shared strict-payload field validators (PayloadFieldRejection closed 12-value enum, normalize_symbols, map_validation_error, require_registered_strategy, require_trading_session_not_future, require_date_range, submission_defaults helpers) that every remaining Phase 20 submission-spec plan (risk-evaluation, paper-session, reconciliation, ingest-bars, sync-symbol-metadata, sync-market-sessions, broker-order-sync) will import"
  - "services/symbol_metadata_sync.py: sync_symbol_metadata service function, callable from a future sync-symbol-metadata Job handler without a scripts/ dependency"
  - "services/calendar.py::sync_market_sessions: session-opening wrapper a future sync-market-sessions Job handler can call while staying inside the services.*-only JobHandler import contract"
affects: [20-*-remaining-plans, 23-bypass-retirement]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Shared field-validator module: pydantic field_validator(mode='before') helper functions raise PydanticCustomError(type=<PayloadFieldRejection value>, ...) so map_validation_error can read the rejection reason straight off ValidationError.errors()[i]['type'] without per-spec duplication"
    - "Job-callable service wrapper: a service function that needs to open its own session_scope (because the JobHandler contract forbids importing db.session directly) is added as a thin sibling function in the same services/ module as the caller-owned-Session primitive it wraps (sync_market_sessions wraps upsert_market_sessions)"

key-files:
  created:
    - src/trading_platform/jobs/handlers/payload_fields.py
    - tests/test_job_payload_fields.py
    - src/trading_platform/services/symbol_metadata_sync.py
    - tests/test_symbol_metadata_sync.py
  modified:
    - src/trading_platform/services/calendar.py

key-decisions:
  - "map_validation_error's third precedence tier matches on ValidationError error['type'] equality against PayloadFieldRejection values (not a special-cased error location check per field) -- this only works because every custom validator here raises PydanticCustomError with a type string that is itself a PayloadFieldRejection value; any future validator added to this module must follow the same convention or map_validation_error will silently fall through to invalid_field_type."
  - "sync_market_sessions imports session_scope at module level in calendar.py (not deferred inside the function) -- verified no import cycle exists since db/session.py imports only core.settings, not services.*."
  - "MetadataSyncResult intentionally drops the retired CLI's dry_run field entirely (not just left False) since OPS-05 forbids Job behavior flags; all docstrings avoid the literal substrings 'dry_run' and 'importlib'/'sys.path' so the plan's own grep acceptance checks (both pinned at 0) hold against comments, not only against removed code."

patterns-established:
  - "Every future Phase 20 submission-spec module imports payload_fields rather than re-implementing strategy_id/as_of_session/date-range/symbols validation, keeping the closed-enum-per-type / stable-code-per-rejection convention (D-25) centralized in one tested module."

requirements-completed: []  # ORCH-02 and OPS-05 remain Pending: this plan ships the extracted service functions and payload validators only, not the sync-symbol-metadata/sync-market-sessions Job handlers, registrations, or console forms that make either requirement's end-to-end text true.

# Metrics
duration: ~15min
completed: 2026-09-28
---

# Phase 20 Plan 03: Shared Payload Validators + Metadata/Calendar Service Extraction Summary

**Closed 12-value PayloadFieldRejection validator module shared by every remaining Phase 20 Job spec, plus symbol-metadata sync and market-session sync extracted from scripts/worker into callable services.**

## Performance

- **Duration:** ~15 min (commit-to-commit; context load/reading preceded the first commit)
- **Started:** 2026-09-28T11:07:25+03:00 (approx, immediately following the prior plan's completion commit)
- **Completed:** 2026-09-28T11:22:29+03:00
- **Tasks:** 2
- **Files modified:** 4 created, 1 modified

## Accomplishments
- `payload_fields.py` ships the full shared validator surface (D-21, D-22, D-24, D-25) that every one of the seven remaining Phase 20 Job types will build its submission spec on top of: a closed `PayloadFieldRejection` enum, `normalize_symbols` (whitelist + `MAX_SYMBOLS=500` cap), `parse_iso_date`, `map_validation_error` with fixed precedence, and the three semantic-check functions (`require_registered_strategy`, `require_trading_session_not_future`, `require_date_range`) that never default a value.
- `services/symbol_metadata_sync.py` moves the metadata-sync business logic (`fetch_ticker_overview`, `upsert_symbol_metadata`, `MetadataSyncResult`) out of `scripts/sync_symbol_metadata.py` with module-level imports (no function-local `httpx`/`PolygonAuthError` imports, no `importlib`/`sys.path` hack) and drops the retired CLI's `dry_run` field entirely, since a Job is never a dry run (OPS-05).
- `services/calendar.py::sync_market_sessions` gives the future `sync-market-sessions` Job handler a `services.*`-only entrypoint that opens its own `session_scope` and calls `upsert_market_sessions`, moving `worker/commands/ingest.py::run_sync_sessions`'s body one layer down.

## Task Commits

Each task was committed atomically:

1. **Task 1: Shared strict payload-field validators** - `c8ba4af` (feat)
2. **Task 2: Extract symbol-metadata sync into services + add calendar.sync_market_sessions** - `c064a38` (feat)
3. **Post-task fix: satisfy pinned acceptance-criteria greps + tighten weak assertions** - `743b86c` (fix)

**Plan metadata:** (this commit, to follow)

_Note: the fix commit corrects a grep-acceptance-criteria miss and two weak test assertions caught by advisor review before this SUMMARY was finalized — see "Deviations from Plan" below._

## Files Created/Modified
- `src/trading_platform/jobs/handlers/payload_fields.py` - Closed `PayloadFieldRejection` enum (12 values), `normalize_symbols`/`parse_iso_date` pydantic-before-validator helpers, `map_validation_error`, `exchange_today`, `require_registered_strategy`, `require_trading_session_not_future`, `require_date_range`, `latest_completed_session_default`, `format_symbols_default`
- `tests/test_job_payload_fields.py` - 21 tests covering the closed-enum test and every `<behavior>` bullet plus `map_validation_error`'s residual-fallback branch, exercised through a local pydantic model wiring the shared validators (mirrors the real submission-spec usage)
- `src/trading_platform/services/symbol_metadata_sync.py` - `fetch_ticker_overview`, `upsert_symbol_metadata`, `MetadataSyncResult` (no `dry_run`), `SymbolMetadataSyncFailedError`, `sync_symbol_metadata(symbols, *, settings)`
- `tests/test_symbol_metadata_sync.py` - 6 DB-backed tests (throwaway Postgres DB per test, migrated to head) for the synced/skipped/failed split, fetch-failure → `SymbolMetadataSyncFailedError`, idempotent re-sync (row count unchanged, `updated_at` strictly advances), `MetadataSyncResult.to_dict()` shape, and `sync_market_sessions` (exact session count via `sessions_in_range`, no duplication on repeat call)
- `src/trading_platform/services/calendar.py` - Added `MarketSessionSyncResult` dataclass and `sync_market_sessions(*, from_date, to_date, settings)`; added the `session_scope` module-level import and a `TYPE_CHECKING`-only `Settings` import for the new function's type hint

## Decisions Made
- `map_validation_error`'s custom-rejection matching relies on every `payload_fields.py` validator raising `PydanticCustomError` whose `type` string is itself a `PayloadFieldRejection` value (e.g. `PydanticCustomError(PayloadFieldRejection.EMPTY_SYMBOLS.value, ...)`), rather than inspecting `error["loc"]`. This is simpler than the `backtest_submission.py` precedent's location-based check but requires every future validator added to this module to follow the same "raise with a PayloadFieldRejection-valued type" convention or it will silently fall through to `INVALID_FIELD_TYPE`.
- `sync_market_sessions` imports `session_scope` at module level in `calendar.py` (the plan's action text allowed either module-level or function-local "if it creates no import cycle"). Verified via direct read of `db/session.py`'s imports (only `core.settings`) that no cycle exists, so module-level was used — matching the plan's stated preference.
- `MetadataSyncResult` and its module docstring avoid the literal substrings `dry_run` and `importlib`/`sys.path` (using "preview-only run" and "dynamic script-import workaround" instead) so the plan's own acceptance-check greps (both pinned at 0) hold against comments, not only against the removed field/code.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Task 2's `importlib|sys.path` acceptance-criteria grep initially failed**
- **Found during:** Post-Task-2 advisor review, before this SUMMARY was first finalized
- **Issue:** The plan pins `grep -c "importlib\|sys.path" src/trading_platform/services/symbol_metadata_sync.py` at 0, but the module docstring's explanatory sentence about the retired `worker/commands/ingest.py::run_sync_metadata` import hack contained the literal substrings `importlib` and `sys.path`, matching the grep and failing the acceptance criterion (no code uses either — comment-only).
- **Fix:** Reworded the docstring sentence to "the worker's dynamic script-import workaround" (no literal `importlib`/`sys.path` substrings), preserving the same explanation.
- **Files modified:** `src/trading_platform/services/symbol_metadata_sync.py`
- **Verification:** `grep -c "importlib\|sys.path" src/trading_platform/services/symbol_metadata_sync.py` now returns 0; full suite still 642 passed.
- **Committed in:** `743b86c`

**2. [Rule 1 - Bug] Two test assertions were weaker than the plan's literal behavior bullets**
- **Found during:** Same advisor review
- **Issue:** (a) The re-sync idempotency test asserted `updated_at >= first_updated_at`, which would pass even if `updated_at` never changed, but the plan's behavior bullet says "`updated_at` advances" (strictly). (b) The `sync_market_sessions` count test asserted `sessions_upserted > 0` plus a tautological "row count equals `sessions_upserted`" check, but the plan's behavior bullet says `sessions_upserted == number of XNYS sessions in range` — an exact, independently-derived count.
- **Fix:** (a) Changed the assertion to strict `>`, with a small `time.sleep(0.01)` between the two sync calls to guarantee distinct `datetime.now(UTC)` timestamps. (b) Changed the assertion to compare against `len(sessions_in_range(from_date, to_date, exchange))`, an independent source of truth for the expected count.
- **Files modified:** `tests/test_symbol_metadata_sync.py`
- **Verification:** Both tests still pass; the exact-count assertion is no longer circular.
- **Committed in:** `743b86c`

---

**Total deviations:** 2 auto-fixed (1 Rule 1 acceptance-criteria bug, 1 Rule 1 weak-assertion bug bundled as one fix commit covering two tests)
**Impact on plan:** Both fixes tighten conformance to the plan's own literal acceptance criteria and behavior bullets; no scope creep, no architectural change.

## Issues Encountered
None beyond the deviations above. `scripts/sync_symbol_metadata.py` and `worker/commands/ingest.py` were read but deliberately left untouched, per the plan's explicit instruction (Plans 12/23 delete them).

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `payload_fields.py` is ready to be imported by every remaining Phase 20 submission-spec plan (risk-evaluation, paper-session, reconciliation, ingest-bars, sync-symbol-metadata, sync-market-sessions, broker-order-sync) — this was the explicit purpose of this plan (it unblocks all seven).
- `services/symbol_metadata_sync.py::sync_symbol_metadata` and `services/calendar.py::sync_market_sessions` are ready to be called from their respective future Job handlers; neither handler nor registry registration exists yet (that is the scope of the plans that build the `sync-symbol-metadata`/`sync-market-sessions` Job types).
- ORCH-02 and OPS-05 are correctly left `Pending` in REQUIREMENTS.md — this plan's frontmatter lists both, but neither requirement's literal end-to-end text (a registered Job type an operator can submit) is satisfied by extracted service functions alone, following the 19-01/19-03/20-01 precedent of not overclaiming completion at the schema/service layer.
- Full suite verified green at 642 passed (0 failed) after this plan (641 immediately post-Task-2, +1 from the residual-fallback test added in the fix commit), up from the 614-pass pre-plan baseline noted in the environment facts. 27 new tests total: 21 in `test_job_payload_fields.py`, 6 in `test_symbol_metadata_sync.py`.

## Self-Check: PASSED

- FOUND: src/trading_platform/jobs/handlers/payload_fields.py
- FOUND: tests/test_job_payload_fields.py
- FOUND: src/trading_platform/services/symbol_metadata_sync.py
- FOUND: tests/test_symbol_metadata_sync.py
- FOUND (modified): src/trading_platform/services/calendar.py
- FOUND commit c8ba4af (Task 1)
- FOUND commit c064a38 (Task 2)
- FOUND commit 743b86c (fix)

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*
