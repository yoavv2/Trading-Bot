---
phase: 02-data-and-strategy
plan: 02
subsystem: database
tags: [exchange-calendars, postgresql, sqlalchemy, alembic, polygon, xnys, market-sessions]

requires:
  - phase: 02-01
    provides: DailyBar model, Symbol model (ticker stubs), ingestion pipeline, Polygon client

provides:
  - Symbol table enriched with Polygon metadata fields (list_date, figi, cik, currency_name)
  - MarketSession table persisting XNYS session dates with open/close timestamps and early_close flag
  - Alembic revision 0003_phase2_metacal applying both schema changes
  - CalendarService backed by exchange_calendars XNYS (sessions_in_range, latest_session_before, upsert)
  - MarketDataAccessLayer: latest_completed_session, bars_for_sessions, missing_sessions_for_symbol
  - scripts/sync_symbol_metadata.py CLI for Polygon ticker-overview metadata upserts
  - Worker sync-metadata and sync-sessions subcommands

affects:
  - 02-03-strategy (consumes bars_for_sessions and missing_sessions_for_symbol)
  - 03-backtesting (depends on session-aware bar reads)

tech-stack:
  added:
    - exchange-calendars>=4.5,<5.0 (XNYS session truth)
    - pandas (transitive dependency of exchange-calendars, used for Timestamp alignment)
  patterns:
    - Session-aware bar reads via persisted market_sessions table, not calendar library at query time
    - Metadata upsert separates provider sync from bar ingestion (two independent operator flows)
    - Calendar service wraps exchange_calendars with a stable internal API

key-files:
  created:
    - src/trading_platform/db/models/market_session.py
    - src/trading_platform/services/calendar.py
    - src/trading_platform/services/market_data_access.py
    - scripts/sync_symbol_metadata.py
    - alembic/versions/0003_phase2_metadata_and_calendar.py
    - tests/test_market_data_access.py
  modified:
    - src/trading_platform/db/models/symbol.py (added 6 enrichment columns)
    - src/trading_platform/db/models/__init__.py (exports MarketSession)
    - src/trading_platform/core/settings.py (CalendarSettings, MetadataRefreshSettings)
    - src/trading_platform/worker/__main__.py (sync-metadata, sync-sessions commands)
    - pyproject.toml (exchange-calendars dependency)
    - config/app.yaml (calendar and metadata settings blocks)
    - Makefile (sync-metadata, sync-sessions targets; test target updated)
    - .env.example (documented calendar env var)

key-decisions:
  - "date_to_session(direction=previous) used instead of previous_session() because the latter requires a valid session as input; direction-based navigation handles non-session dates gracefully"
  - "Sessions persisted into market_sessions table rather than computed at query time — downstream queries are SQL joins, not calendar library calls"
  - "Symbol metadata enrichment columns are nullable to allow ticker-stub rows to coexist with fully-synced rows without a blocking prereq"
  - "Early close detected by checking market_close UTC hour (<20:30 UTC = before 4:30 PM ET) rather than referencing a separate early-close calendar"

requirements-completed:
  - REQ-04
  - REQ-06
  - REQ-11

duration: 6min
completed: 2026-03-14
---

# Phase 2 Plan 02: Symbol Metadata, Trading Calendar, and Reusable Reads Summary

**XNYS session persistence via exchange_calendars, enriched symbol catalog with Polygon ticker-overview upserts, and a session-aware market-data access layer (bars_for_sessions, missing_sessions_for_symbol) proven by 29 integration tests**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-14T10:07:49Z
- **Completed:** 2026-03-14T10:13:50Z
- **Tasks:** 3
- **Files modified:** 14 (7 created, 7 modified)

## Accomplishments

- Persisted XNYS trading sessions (dates, open/close timestamps, early-close flag) in a new `market_sessions` table via idempotent upserts backed by `exchange_calendars`
- Enriched `symbols` table with 6 Polygon metadata fields and added `scripts/sync_symbol_metadata.py` CLI for deterministic ticker-overview refresh
- Implemented a session-aware read layer (`bars_for_sessions`, `missing_sessions_for_symbol`, `latest_completed_session`) that strategies can call without Polygon or calendar library knowledge

## Task Commits

Each task was committed atomically:

1. **Task 1: Persist symbol enrichment and exchange-session context** - `a979d7f` (feat)
2. **Task 2: Calendar service, metadata sync, and market-data access layer** - `65992d0` (feat)
3. **Task 3: Session-aware query and missing-session detection tests** - `9f6bd25` (test)

## Files Created/Modified

- `src/trading_platform/db/models/market_session.py` - ORM model for persisted XNYS sessions
- `alembic/versions/0003_phase2_metadata_and_calendar.py` - Migration adding symbol enrichment columns and market_sessions table
- `src/trading_platform/services/calendar.py` - Calendar service backed by exchange_calendars XNYS
- `src/trading_platform/services/market_data_access.py` - Session-aware bar read and missing-session detection layer
- `scripts/sync_symbol_metadata.py` - CLI for Polygon ticker-overview metadata upserts
- `tests/test_market_data_access.py` - 29 tests: calendar unit tests, session persistence, metadata upsert, bars_for_sessions, missing_sessions_for_symbol, schema assertions
- `src/trading_platform/db/models/symbol.py` - Added list_date, currency_name, cik, composite_figi, share_class_figi, metadata_provider
- `src/trading_platform/core/settings.py` - Added CalendarSettings, MetadataRefreshSettings to MarketDataSettings
- `src/trading_platform/worker/__main__.py` - sync-metadata and sync-sessions subcommands
- `pyproject.toml` - Added exchange-calendars runtime dependency
- `config/app.yaml` - Documented calendar.exchange and metadata.universe settings

## Decisions Made

- **date_to_session over previous_session:** `exchange_calendars.previous_session()` requires a valid session as input and raises `NotSessionError` on weekends/holidays. Used `date_to_session(direction="previous")` which handles non-session dates correctly.
- **Persist sessions to DB:** Sessions are written to `market_sessions` and join-queried with `daily_bars`. This avoids running calendar library code in every read path and makes missing-session detection a SQL query.
- **Nullable enrichment columns:** Symbol enrichment columns are nullable so existing ticker-stub rows created during bar ingestion do not need to be blocked on a prior metadata sync.
- **Early close by UTC hour threshold:** `market_close < 20:30 UTC` (4:30 PM ET) treated as early close — avoids a separate `early_closes` table while correctly flagging Black Friday and Christmas Eve sessions.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed latest_session_before raising NotSessionError on non-session dates**

- **Found during:** Task 2 / Task 3 test execution
- **Issue:** `exchange_calendars.previous_session(ts)` requires `ts` to be a valid session; passing a Saturday date raised `NotSessionError`
- **Fix:** Replaced `previous_session(ts)` with `date_to_session(ts, direction="previous")` which correctly resolves any date to the nearest prior session
- **Files modified:** `src/trading_platform/services/calendar.py`
- **Verification:** `test_latest_session_before_returns_friday_for_saturday` passes
- **Committed in:** `65992d0` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - bug)
**Impact on plan:** Necessary for correctness; `previous_session` semantics are stricter than expected. No scope creep.

## Issues Encountered

- Pre-existing teardown error in `tests/test_db_migrations.py::test_ready_endpoint_reflects_database_connectivity` (insufficient privilege to terminate superuser connections in local PostgreSQL@14 Homebrew) — this is a known pre-existing issue documented in STATE.md, not introduced by this plan. All 4 DB migration tests pass; only the fixture teardown after the last test raises the error.

## User Setup Required

The `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY` environment variable is required for live metadata refresh via `scripts/sync_symbol_metadata.py`. Set it in `.env` before running the sync script against Polygon.

See `.env.example` for full documentation.

## Next Phase Readiness

- `bars_for_sessions(session, symbol, n_sessions, as_of)` is ready for strategy indicator computation
- `missing_sessions_for_symbol` is ready for stale-data guard logic
- `upsert_market_sessions` is ready for daily workflow automation
- Phase 2 Plan 03 (`TrendFollowingDailyV1` indicators and signals) can import directly from `market_data_access` without any Polygon or calendar library coupling

---

## Self-Check: PASSED

All key files verified present and all task commits verified in git log.

*Phase: 02-data-and-strategy*
*Completed: 2026-03-14*
