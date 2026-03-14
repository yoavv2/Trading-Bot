---
phase: 02-data-and-strategy
plan: 01
subsystem: database
tags: [polygon, httpx, sqlalchemy, alembic, postgresql, market-data, ingestion]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: SQLAlchemy ORM base, Alembic migration stack, typed settings, CLI worker pattern, session_scope, test fixtures with temp-Postgres
provides:
  - Typed PolygonProviderSettings and MarketDataSettings in the runtime settings layer
  - DailyBarRequest, DailyBar, IngestionResult typed contracts in the market-data boundary
  - Symbol, DailyBar, MarketDataIngestionRun ORM models
  - Alembic migration 0002_phase2_mdf adding symbols, daily_bars, market_data_ingestion_runs tables
  - PolygonClient with pagination, retry/backoff, and auth error detection
  - ingest_daily_bars orchestrator with idempotent upsert and ingestion run recording
  - scripts/ingest_polygon_bars.py CLI and worker ingest-bars subcommand
  - 19 deterministic tests proving normalization, pagination, upserts, and failure handling
affects: [02-02-symbol-calendar-reads, 02-03-strategy, 03-backtest]

# Tech tracking
tech-stack:
  added:
    - httpx (promoted from dev to runtime dependency for Polygon REST calls)
  patterns:
    - PostgreSQL INSERT ... ON CONFLICT DO UPDATE with RETURNING for idempotent bar upserts
    - Per-run ingestion metadata records for auditability and replay
    - Minimal symbol catalog upsert before bar ingestion to satisfy FK without requiring full provider sync
    - PolygonAuthError / PolygonClientError hierarchy to distinguish auth gates from transient failures
    - Revision IDs capped at 32 chars to match Alembic's alembic_version varchar(32) column

key-files:
  created:
    - src/trading_platform/db/models/symbol.py
    - src/trading_platform/db/models/daily_bar.py
    - src/trading_platform/db/models/market_data_ingestion_run.py
    - alembic/versions/0002_phase2_market_data_foundation.py
    - src/trading_platform/services/polygon.py
    - src/trading_platform/services/ingestion.py
    - scripts/ingest_polygon_bars.py
    - tests/test_market_data_ingestion.py
    - tests/fixtures/polygon_daily_bars.json
  modified:
    - pyproject.toml
    - config/app.yaml
    - .env.example
    - src/trading_platform/core/settings.py
    - src/trading_platform/services/data.py
    - src/trading_platform/db/models/__init__.py
    - src/trading_platform/worker/__main__.py
    - Makefile
    - tests/test_db_migrations.py

key-decisions:
  - "Promoted httpx from dev-only to runtime dependency since the Polygon client is core production code, not test-only"
  - "Shortened Alembic revision ID to 0002_phase2_mdf to stay within the alembic_version varchar(32) limit"
  - "Used INSERT ON CONFLICT DO UPDATE RETURNING id instead of rowcount to get a reliable affected-row count from PostgreSQL upserts"
  - "Symbol catalog rows are created as minimal ticker-only stubs during bar ingestion so FK integrity is maintained without requiring a separate symbol-sync step"
  - "DailyBar uniqueness constraint spans symbol_id + session_date + adjusted + provider so future non-adjusted or multi-provider rows remain valid without duplication"

patterns-established:
  - "Idempotent upsert pattern: PostgreSQL ON CONFLICT DO UPDATE with RETURNING for deterministic row counts"
  - "Ingestion run lifecycle: start run -> per-symbol ingest with individual failure capture -> finish run with outcome status"
  - "Auth error hierarchy: PolygonAuthError for 401/403/missing-key, PolygonClientError for other failures"
  - "Revision ID naming: keep under 32 chars (alembic_version column width)"

requirements-completed:
  - REQ-04
  - REQ-06
  - REQ-11

# Metrics
duration: 9min
completed: 2026-03-14
---

# Phase 2 Plan 01: Polygon Provider, Ingestion Pipeline, and Normalization Summary

**httpx-backed Polygon daily-bar client with idempotent PostgreSQL upserts, typed ingestion-run audit records, and 19 deterministic tests — no live network required**

## Performance

- **Duration:** ~9 min
- **Started:** 2026-03-14T09:54:57Z
- **Completed:** 2026-03-14T10:04:16Z
- **Tasks:** 3
- **Files modified:** 18

## Accomplishments

- Polygon provider settings, ingest defaults, and market-data boundary are fully typed and externalized through the existing YAML+env settings stack
- Symbol catalog, normalized daily-bar, and ingestion-run tables land in a single Alembic migration with a natural uniqueness constraint that makes re-ingesting the same window idempotent
- 19 tests covering normalization helpers, auth error detection, pagination, empty responses, DB upserts, full ingest pipeline, idempotency, and per-symbol failure isolation all pass without live network calls

## Task Commits

1. **Task 1: Extend runtime settings and market-data contract** - `41de56c` (feat)
2. **Task 2: Implement Polygon client, persistence, and ingest flow** - `20c377a` (feat)
3. **Task 3: Harden operator workflow and test coverage** - `4c2945c` (feat)

## Files Created/Modified

- `src/trading_platform/core/settings.py` - Added PolygonProviderSettings, IngestSettings, MarketDataSettings
- `src/trading_platform/services/data.py` - Added DailyBarRequest, DailyBar, IngestionResult typed contracts
- `src/trading_platform/services/polygon.py` - PolygonClient with pagination, retry/backoff, auth error detection
- `src/trading_platform/services/ingestion.py` - ingest_daily_bars orchestrator with idempotent upsert and run recording
- `src/trading_platform/db/models/symbol.py` - Symbol catalog ORM model
- `src/trading_platform/db/models/daily_bar.py` - DailyBar ORM model with FK and unique constraint
- `src/trading_platform/db/models/market_data_ingestion_run.py` - MarketDataIngestionRun ORM model
- `src/trading_platform/db/models/__init__.py` - Updated exports
- `alembic/versions/0002_phase2_market_data_foundation.py` - Phase 2 migration (revision 0002_phase2_mdf)
- `scripts/ingest_polygon_bars.py` - Standalone CLI for historical bar ingestion
- `src/trading_platform/worker/__main__.py` - Added ingest-bars subcommand
- `Makefile` - Added make ingest-bars target
- `config/app.yaml` - Added market_data block with polygon and ingest defaults
- `.env.example` - Added TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY
- `pyproject.toml` - Promoted httpx to runtime dependency
- `tests/test_market_data_ingestion.py` - 19 tests covering full pipeline
- `tests/test_db_migrations.py` - Added Phase 2 schema assertions
- `tests/fixtures/polygon_daily_bars.json` - Stable 3-bar fixture for deterministic tests

## Decisions Made

- Promoted httpx to runtime dependency since the Polygon client is core production code, not test infrastructure
- Shortened Alembic revision ID to `0002_phase2_mdf` (from `0002_phase2_market_data_foundation`) to stay within alembic_version's varchar(32) limit
- Used `INSERT ON CONFLICT DO UPDATE ... RETURNING id` instead of `result.rowcount` because PostgreSQL returns -1 for rowcount on multi-row upserts
- Symbol catalog rows are created as minimal ticker-only stubs during ingestion so FK integrity is maintained without a prerequisite symbol-sync step
- Uniqueness constraint spans four columns (symbol_id, session_date, adjusted, provider) to support future non-adjusted or multi-provider rows cleanly

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed unreliable rowcount from PostgreSQL multi-row upsert**
- **Found during:** Task 2 (upsert_daily_bars implementation) — discovered during test execution
- **Issue:** PostgreSQL returns -1 for `result.rowcount` on multi-row ON CONFLICT DO UPDATE statements; test assertions for `bars_upserted == 3` were failing
- **Fix:** Appended `.returning(DailyBarModel.id)` to the insert statement and used `len(returned)` for the count
- **Files modified:** `src/trading_platform/services/ingestion.py`
- **Verification:** `test_upsert_daily_bars_persists_rows` and related tests all pass
- **Committed in:** `20c377a` (Task 2 commit)

**2. [Rule 1 - Bug] Shortened Alembic revision ID to respect varchar(32) column width**
- **Found during:** Task 2 — migration ran but alembic_version update failed with StringDataRightTruncation
- **Issue:** Revision ID `0002_phase2_market_data_foundation` is 34 characters; alembic_version column is varchar(32)
- **Fix:** Renamed revision to `0002_phase2_mdf`
- **Files modified:** `alembic/versions/0002_phase2_market_data_foundation.py`
- **Verification:** `test_db_migrations.py` all pass
- **Committed in:** `20c377a` (Task 2 commit)

**3. [Rule 3 - Blocking] Added ForeignKey to DailyBar.symbol_id column**
- **Found during:** Task 2 — SQLAlchemy relationship required an explicit FK declaration
- **Issue:** `symbol_id` column was declared without `ForeignKey("symbols.id")`, breaking the ORM relationship
- **Fix:** Added `ForeignKey("symbols.id", ondelete="CASCADE")` to the column definition
- **Files modified:** `src/trading_platform/db/models/daily_bar.py`
- **Verification:** Model imports and relationship traversal succeed
- **Committed in:** `20c377a` (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (2 Rule 1 bugs, 1 Rule 3 blocking)
**Impact on plan:** All three fixes were correctness-required. No scope creep.

## Issues Encountered

- PostgreSQL was not running at plan start; started the local Homebrew `postgresql@14` service and created the `trading_platform` role and database to support integration tests. Same pattern as Phase 1 (Docker daemon unavailable, local Postgres used instead).

## User Setup Required

Set `TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY` in `.env` or the shell before running live Polygon syncs:

```bash
TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY=your_key_here make ingest-bars FROM_DATE=2024-01-01 TO_DATE=2024-12-31
```

All automated tests are fully mocked and pass without a Polygon API key.

## Next Phase Readiness

- Phase 2 Plan 02 can now build symbol metadata enrichment, XNYS session persistence, and session-aware bar reads on top of the persisted symbols and daily_bars tables
- The PolygonClient is reusable for ticker reference calls in Plan 02
- The ingestion run record schema already has `request_metadata` JSON for extensibility

---
*Phase: 02-data-and-strategy*
*Completed: 2026-03-14*
