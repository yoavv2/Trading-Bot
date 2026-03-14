---
status: complete
phase: 03-backtest-and-reporting
source: 03-01-SUMMARY.md, 03-02-SUMMARY.md, 03-03-SUMMARY.md
started: 2026-03-14T12:10:00Z
updated: 2026-03-14T12:35:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: Kill any running server/service. Run `alembic upgrade head` from scratch (or confirm migrations are at head). Both Phase 3 migrations (0004 and 0005) apply without errors. Then run `make backtest` — the backtest completes and prints a JSON run summary to the terminal without crashes.
result: pass

### 2. Run Backtest via Script CLI
expected: Run `python scripts/run_backtest.py` (or `make backtest`). The backtest executes the TrendFollowingDailyV1 strategy over persisted market data, prints a JSON summary showing run ID, strategy name, ending equity, and trade/signal counts, and exits cleanly.
result: pass

### 3. Backtest Produces Deterministic Results
expected: Run the same backtest twice with identical config. Both runs produce the same ending equity, same number of trades, and same number of signals. The second run does not crash or produce different metrics.
result: pass

### 4. Generate Backtest Report via Script CLI
expected: Run `python scripts/export_backtest_report.py` (or `make export-backtest-report`). A markdown summary is printed to the terminal showing: strategy name, run assumptions (capital, fees, slippage), trade count, total return, max drawdown, and win rate. No errors or missing data.
result: pass

### 5. CSV Trade and Equity Exports
expected: After running the export script, CSV files for trades and equity snapshots are written to disk. The trade CSV contains columns for symbol, entry/exit prices, PnL, and costs. The equity CSV contains session-level equity values.
result: pass

### 6. Worker Backtest Subcommand
expected: Run `trading-platform-worker backtest` (or equivalent). The worker executes the backtest and produces the same results as the standalone script. No import errors or missing CLI wiring.
result: pass

### 7. Worker Report Subcommand
expected: Run `trading-platform-worker report-backtest` (or equivalent). The worker renders the report for the latest successful backtest run without requiring a run ID argument. Output matches the standalone script.
result: pass

### 8. No-Trade Run Safety
expected: If a backtest produces zero trades (e.g., with a very short date range or data that never triggers signals), the report still generates with zeroed metrics instead of errors, null values, or divide-by-zero crashes.
result: pass

## Summary

total: 8
passed: 8
issues: 0
pending: 0
skipped: 0

## Gaps

[none yet]
