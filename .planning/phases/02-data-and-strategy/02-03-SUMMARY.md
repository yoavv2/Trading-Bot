---
phase: 02-data-and-strategy
plan: 03
subsystem: strategy
tags: [sma, trend-following, signals, pydantic, deterministic-tests, cli]

requires:
  - phase: 02-01
    provides: DailyBar model, ingestion pipeline
  - phase: 02-02
    provides: bars_for_sessions, session-aware market-data access layer

provides:
  - TrendFollowingDailyV1 signal generation (generate_signals → SignalBatch)
  - Typed signal output: Signal, SignalBatch, IndicatorSnapshot, SignalDirection, SignalReason
  - TrendFollowingExitSettings typed model replacing untyped dict exits
  - warmup_periods property on BaseStrategy contract
  - scripts/generate_signals.py CLI for operator signal evaluation
  - 19 deterministic strategy tests covering warmup, entry, exit, flat, and determinism
  - make generate-signals operator target

affects:
  - 03-backtesting (consumes generate_signals → SignalBatch as input)
  - future paper-execution (composes on typed Signal output)

tech-stack:
  added: []
  patterns:
    - Strategy reads bars via bars_for_sessions; no Polygon or calendar library knowledge in signal path
    - _compute_sma and _evaluate_symbol as separately-testable static/instance helpers
    - generate_signals is pure computation — no risk sizing, orders, or broker calls
    - Module-level import of bars_for_sessions allows clean patch-based test isolation

key-files:
  created:
    - src/trading_platform/strategies/signals.py
    - scripts/generate_signals.py
    - tests/test_trend_following_strategy.py
  modified:
    - config/strategies/trend_following_daily.yaml (warmup_periods, exit_window added)
    - src/trading_platform/core/settings.py (TrendFollowingIndicatorSettings.warmup_periods, TrendFollowingExitSettings replacing dict)
    - src/trading_platform/strategies/base.py (generate_signals hook, warmup_periods property)
    - src/trading_platform/strategies/trend_following_daily/strategy.py (full implementation)
    - Makefile (generate-signals target, test target updated)

key-decisions:
  - "Module-level import of bars_for_sessions (not lazy import) allows patch-based test isolation without DB"
  - "Exit rule evaluated before entry rule so a deteriorating position exits before a new entry could be flagged"
  - "TrendFollowingExitSettings typed model replaces dict[str, str] exits so exit_window is a typed int"
  - "warmup_periods defaults to long_window (200) and lives in config YAML for operator visibility"

requirements-completed:
  - REQ-03
  - REQ-11

duration: 5min
completed: 2026-03-14
---

# Phase 2 Plan 03: TrendFollowingDailyV1 Indicators and Signal Generation Summary

**Config-driven dual-SMA strategy with typed signal output (Signal, SignalBatch, IndicatorSnapshot) and 19 deterministic tests covering warmup gating, entry, exit, flat, and idempotent determinism**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-03-14T10:18:07Z
- **Completed:** 2026-03-14T10:23:27Z
- **Tasks:** 3
- **Files modified:** 8 (3 created, 5 modified)

## Accomplishments

- Extended the strategy contract with a typed `generate_signals(db_session, as_of) -> SignalBatch` hook and `warmup_periods` property on `BaseStrategy`, keeping Phase 1 strategies backward compatible
- Implemented `TrendFollowingDailyV1` with entry rule (`close > SMA200 AND SMA50 > SMA200`), exit rule (`close < SMA50`), and warmup gating (fewer than 200 bars → FLAT / INSUFFICIENT_HISTORY)
- Created `src/trading_platform/strategies/signals.py` with frozen dataclasses `Signal`, `SignalBatch`, `IndicatorSnapshot` and enums `SignalDirection`, `SignalReason` — stable, auditable, JSON-serializable output
- Replaced untyped `exits: dict[str, str]` with `TrendFollowingExitSettings` typed Pydantic model so `exit_window` is a validated int rather than a string lookup
- Added `scripts/generate_signals.py` CLI (`--strategy`, `--as-of`, `--compact`) and `make generate-signals` operator target
- Proved determinism with 19 fixture-driven tests (no DB, no network); same input window produces identical `SignalBatch.to_dict()` output across repeat evaluations

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend strategy contract and config schema** - `94b6f45` (feat)
2. **Task 2: Implement TrendFollowingDailyV1 indicators and signal generation** - `3be2197` (feat)
3. **Task 3: Makefile generate-signals target and test suite update** - `d186cc1` (feat)

## Files Created/Modified

- `src/trading_platform/strategies/signals.py` - Typed signal value objects: SignalDirection, SignalReason, IndicatorSnapshot, Signal, SignalBatch
- `scripts/generate_signals.py` - CLI entrypoint: evaluates strategy for a session, prints JSON-serialized SignalBatch
- `tests/test_trend_following_strategy.py` - 19 deterministic tests: _compute_sma boundaries, _evaluate_symbol (insufficient, entry, exit, flat, indicators), generate_signals with mocked market-data layer, signal type structural tests
- `config/strategies/trend_following_daily.yaml` - Added warmup_periods and exit_window for operator visibility
- `src/trading_platform/core/settings.py` - TrendFollowingIndicatorSettings.warmup_periods, TrendFollowingExitSettings typed model
- `src/trading_platform/strategies/base.py` - generate_signals hook (raises NotImplementedError), warmup_periods property (returns 0)
- `src/trading_platform/strategies/trend_following_daily/strategy.py` - Full implementation: generate_signals, _evaluate_symbol, _compute_sma, warmup_periods override
- `Makefile` - generate-signals target, test target includes test_trend_following_strategy.py

## Decisions Made

- **Module-level bars_for_sessions import:** Importing at module level (not lazy inside generate_signals) allows `patch("...strategy.bars_for_sessions")` to intercept calls cleanly without database access in tests.
- **Exit rule before entry rule:** The evaluation order ensures a position that should exit is always flagged EXIT before the entry check runs — prevents conflating a deteriorating position with a fresh entry condition.
- **Typed TrendFollowingExitSettings:** Replacing `dict[str, str]` exits with a typed Pydantic model gives `exit_window` an int type validated at settings load time, preventing runtime KeyError on string-based lookups.
- **warmup_periods in config YAML:** Duplicates the long_window value for operator clarity — the YAML is the source of truth for all strategy parameters; warmup being visible there avoids magic defaults buried in code.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed patch path for bars_for_sessions mock**

- **Found during:** Task 2 initial test run
- **Issue:** The lazy `from trading_platform.services.market_data_access import bars_for_sessions` inside `generate_signals` was not patchable via the module-path string used in tests (`trading_platform.strategies.trend_following_daily.strategy.bars_for_sessions`) because the name didn't exist at module level before the method was first called.
- **Fix:** Moved the import to module level so the name is always bound in the strategy module's namespace and the patch intercepts correctly.
- **Files modified:** `src/trading_platform/strategies/trend_following_daily/strategy.py`
- **Commit:** `3be2197` (included in Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - bug)
**Impact on plan:** No scope change; fix is the correct Python patching pattern.

## Next Phase Readiness

- `strategy.generate_signals(db_session, as_of)` returns a typed `SignalBatch` — Phase 3 backtesting can iterate `batch.entry_signals` and `batch.exit_signals` without coupling to indicator internals
- The signal boundary is explicit: no risk, no orders, no broker — Phase 3 composes on top cleanly
- `make generate-signals` provides the daily operator workflow for signal inspection before live paper execution

---

## Self-Check: PASSED

All key files verified present and all task commits verified in git log.

*Phase: 02-data-and-strategy*
*Completed: 2026-03-14*
