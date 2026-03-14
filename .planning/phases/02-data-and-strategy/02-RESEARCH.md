# Phase 2: Data and Strategy - Research

**Researched:** 2026-03-13
**Domain:** Polygon-backed daily market data and deterministic strategy evaluation for `TrendFollowingDailyV1`
**Confidence:** MEDIUM-HIGH

<planning_inputs>
## Planning Inputs

### Available Context
- No `02-CONTEXT.md` exists for this phase. Planning uses `.planning/PROJECT.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`, the completed Phase 1 summaries, and the current codebase.
- Phase 1 already established the boundaries this phase must extend: synchronous SQLAlchemy persistence, typed YAML-plus-env settings, CLI-first operator workflows, a real strategy registry, and placeholder market-data services.

### Locked Decisions From Project State
- The initial market-data source is Polygon for historical daily U.S. equities bars.
- The initial strategy remains `TrendFollowingDailyV1` over the narrow universe `SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA`, `AMD`, `META`, `AMZN`, `GOOGL`, and `TSLA`.
- The platform stays local-first, PostgreSQL-backed, and file-first for non-secret configuration.
- Strategy logic must stay deterministic and isolated. Risk sizing, broker execution, and backtest persistence belong to later phases.
- The CLI remains the primary operator surface. API growth is deferred unless a read-only boundary materially helps Phase 2 verification.

### Claude's Discretion
- Exact schema decomposition across symbol, session, bar, and ingestion-run tables.
- Whether market-data helpers live entirely in `src/trading_platform/services/data.py` or are split into adjacent service modules.
- Whether `httpx` alone is sufficient for Polygon access or whether a thin retry/backoff helper is warranted.
- Whether to compute moving averages with simple typed helpers instead of bringing in pandas at this stage.

</planning_inputs>

<research_summary>
## Summary

Phase 2 should stay boring and explicit, just like Phase 1. The best extension path is:

1. Add a real Polygon client plus an idempotent daily-bar ingestion flow.
2. Persist symbol metadata and exchange-session context separately from bar rows.
3. Expand the market-data boundary into a reusable read layer that strategies can consume without knowing anything about Polygon.
4. Extend the existing strategy contract with typed indicator and signal outputs, then implement `TrendFollowingDailyV1` on top of persisted daily bars.

This phase should be split into three sequential plans, not parallel waves. The likely file overlap is substantial: settings, the data-service boundary, the model package, Alembic revisions, worker CLI surfaces, and market-data tests all cross the same seams. The strategy plan also depends on the prior two plans yielding both persisted bars and session-aware access patterns.

**Primary recommendation:** Use a thin `httpx`-based Polygon client, keep persistence centered on normalized daily bars plus ingestion metadata, use `exchange_calendars` with `XNYS` for session truth, and keep the first strategy implementation on typed domain objects rather than pulling pandas into the platform core yet.

</research_summary>

<official_findings>
## External Findings

### Polygon Daily-Bar Semantics
- Polygon's stocks "Custom Bars (OHLC)" endpoint aggregates base trade bars into the requested timespan, returns timestamps in Eastern Time semantics for snapping/stretching, supports an `adjusted` parameter that defaults to split-adjusted data, sorts results by timestamp, paginates with `next_url`, and caps the `limit` at 50,000 base bars per request.
- Polygon also states that no aggregate bar is produced for intervals with no eligible trades.

**Planning implications**
- Daily-bar ingestion must be paginated and idempotent.
- Missing dates cannot be treated as bad data until the trading calendar says a session should exist.
- Persisting `adjusted` and provider timestamps is required for reproducibility.
- The ingestion flow should record request parameters and run status so replays are explainable.

### Polygon Symbol Metadata
- Polygon's stocks ticker reference surfaces expose the fields Phase 2 needs for symbol normalization and eligibility checks: ticker identity, market, locale, active status, type, name, list date, and primary exchange.

**Planning implications**
- Do not hardcode the universe as a bare list forever. Persist symbol metadata and refresh it from the provider.
- Daily bars should attach to persisted symbol records, not only free-form ticker strings.
- Metadata refresh and price-bar ingestion should be separate operator actions even if they share the same provider client.

### Exchange Calendar Handling
- The `exchange_calendars` project provides `XNYS`, session schedules, `sessions_in_range`, `previous_session`, `date_to_session`, and early-close-aware schedule access for exchange calendars.

**Planning implications**
- Session truth should come from `XNYS`, not weekend checks or naive date arithmetic.
- Persisting market-session rows is justified here because the roadmap explicitly calls for stored calendar context.
- The market-data read layer should answer "latest completed session", "bars for a window of sessions", and "which sessions are missing bars" without provider-specific logic leaking into strategy code.

</official_findings>

<recommendations>
## Recommended Architecture

### Runtime Additions
- Add `httpx` to runtime dependencies for Polygon REST calls instead of introducing a heavier SDK.
- Add `exchange-calendars` once session-aware reads and persisted market-session context land.
- Keep the service synchronous and reuse the existing SQLAlchemy + Alembic + pytest stack.

### Data Model Shape
- `symbols`: persisted provider-backed symbol catalog for the initial universe and later expansion.
- `daily_bars`: one normalized row per `symbol_id + session_date + adjusted + provider`, with provider timestamp and optional trade-count/vwap fields if available.
- `market_data_ingestion_runs`: records request window, provider parameters, symbol count, status, pagination counts, and failures.
- `market_sessions`: persisted `XNYS` session dates plus open/close timestamps and early-close markers.

### Normalization Rules
- Normalize all bars onto a `session_date` key that strategies consume.
- Persist the raw provider timestamp alongside normalized fields so future audits can explain how the row was derived.
- Use upserts on the natural uniqueness boundary for bars and symbol metadata.
- Prefer provider request metadata and per-run summaries over storing full raw payload blobs row-by-row in Phase 2.

### Service Boundaries
- Expand `MarketDataService` beyond `get_daily_bars(symbols)` so it can support:
  - provider-backed sync operations
  - session-aware range reads
  - lookback-window access for indicators
  - missing-session and stale-data checks
- Keep Polygon-specific logic in a dedicated client/service module and keep repositories or read helpers provider-agnostic.

### Strategy Boundaries
- Extend `BaseStrategy` with typed signal-generation hooks rather than bolting signal logic onto the Phase 1 `dry_run()` method.
- Introduce explicit signal and indicator snapshot types for deterministic tests.
- Keep the first strategy long-only and deterministic:
  - entry when `close > SMA 200` and `SMA 50 > SMA 200`
  - exit when `close < SMA 50`
- Do not add risk sizing, order intents, broker calls, or backtest persistence here.

### Testing Approach
- Use mocked Polygon HTTP payloads and the temporary-Postgres pattern already established in `tests/test_db_migrations.py` and `tests/test_dry_run.py`.
- Use fixed bar fixtures for deterministic signal tests rather than live provider calls.
- Verify idempotent upsert behavior, calendar completeness checks, and indicator warmup behavior explicitly.

</recommendations>

## Validation Architecture

- `tests/test_market_data_ingestion.py`
  - mock Polygon daily-bar responses
  - verify normalization, pagination handling, idempotent upserts, and ingestion-run persistence
- `tests/test_market_data_access.py`
  - verify symbol metadata sync, `XNYS` session persistence, latest-session resolution, and missing-session detection
- `tests/test_trend_following_strategy.py`
  - verify indicator warmup behavior, deterministic entry/exit outputs, and no-signal behavior on insufficient history
- Extend `tests/test_db_migrations.py` only where schema assertions materially improve migration coverage for the new tables
- Keep the full validation loop on `pytest`; no manual-only checks are required for this phase

**Quick command:** `PYTHONPATH=src .venv/bin/pytest tests/test_market_data_ingestion.py tests/test_market_data_access.py tests/test_trend_following_strategy.py -q`

**Full command:** `PYTHONPATH=src .venv/bin/pytest tests -q`

## Plan Split Recommendation

### 02-01: Polygon provider, ingestion pipeline, and normalization
- Own provider config, REST client behavior, normalized bar persistence, and CLI/operator ingestion entrypoints.
- Create the minimum schema required to ingest bars reproducibly.

### 02-02: Symbol metadata, trading calendar, and reusable reads
- Own symbol enrichment, `XNYS` session truth, market-session persistence, and session-aware bar access patterns.
- Turn the ingestion-only data layer into a reusable market-data boundary.

### 02-03: `TrendFollowingDailyV1` indicators and signals
- Own strategy contract expansion, typed signals, config-driven indicator windows, and deterministic evaluation.
- Consume the session-aware market-data boundary from the prior plans.

## Sources

- Polygon stocks aggregates docs: https://polygon.io/docs/rest/stocks/aggregates/custom-bars
- Polygon ticker overview docs: https://polygon.io/docs/rest/stocks/tickers/ticker-overview
- Polygon all tickers docs: https://polygon.io/docs/rest/stocks/tickers/all-tickers
- `exchange_calendars` official repository and README: https://github.com/gerrymanoim/exchange_calendars

---
*Phase: 02-data-and-strategy*
*Research completed: 2026-03-13*
