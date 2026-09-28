---
phase: 20-complete-operation-migration-safety-controls
plan: 24
subsystem: orchestration
tags: [boundary-test, ast, makefile, scripts, closed-world, bootstrap-cleanup]

requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-02 read-only report paths, 20-12 pinned worker surface, 20-03 service extraction, 20-16/19/20/21 registered and E2E-proven Job types"
provides:
  - "Ten mutation-bypass scripts, nine Makefile targets, the dry-run bootstrap service code and tests/test_dry_run.py deleted"
  - "Closed-world boundary test: exact-set scripts/ and Makefile inventories, pinned mutating entry-point AST scan, thin-wrapper pins, .claude exclusion"
affects: []

tech-stack:
  added: []
  patterns:
    - "Closed-world inventory tests: exact-set literals for scripts/ and Makefile targets so any new unclassified file or target fails"
    - "AST terminal-name scan (Name.id / Attribute.attr plus from-imports) against a pinned mutating entry-point set with a single pinned allowed pair"

key-files:
  created: []
  modified:
    - tests/test_orchestration_boundaries.py
    - tests/test_market_data_access.py
    - Makefile
    - README.md
    - src/trading_platform/services/bootstrap.py

key-decisions:
  - "services/bootstrap.py keeps only ensure_strategy_record and _strategy_payload; the six other symbols had zero references outside the file and the deleted scripts/dry_run.py (the only grep hits elsewhere were unrelated test-local _create_strategy_run helpers)."
  - "The two symbol-metadata upsert tests imported the private _upsert_symbol_metadata from the deleted script; they now import the equivalent service function trading_platform.services.symbol_metadata_sync.upsert_symbol_metadata (same signature, same assertions)."
  - "No kill-switch-trip Makefile target added (D-15 optional)."

patterns-established:
  - "Adding a script, Makefile target, worker command or a mutating call outside the pinned pair requires an explicit edit to the exemption literals in tests/test_orchestration_boundaries.py"

requirements-completed: [ORCH-01, ORCH-02, ORCH-08]

duration: ~15min
completed: 2026-09-28
---

# Phase 20 Plan 24: Orchestration Closure Summary

**Deleted every remaining mutation-bypass script/Makefile target/dry-run service and pinned the result with a closed-world AST + exact-set boundary test, closing ORCH-01/02/08.**

## Performance

- **Tasks:** 2/2
- **Files:** 11 deleted, 5 modified

## Accomplishments

- Removed the ten bypass scripts, nine Makefile targets, `run_dry_bootstrap`/`create_strategy_run`/`update_strategy_run`/`build_placeholder_services`/`PlatformServices`/`DryRunReport`, and `tests/test_dry_run.py`.
- `scripts/` now contains exactly `export_backtest_report.py generate_signals.py migrate.py operator_status.py report_strategy_analytics.py seed_phase1.py`; the Makefile has exactly the 10 kept targets and a matching `.PHONY`.
- Added 10 boundary tests (`_SCRIPT_EXEMPTIONS`, `_KEPT_MAKE_TARGETS`, `_SCRIPT_TOP_LEVEL_DEFS`, `_MUTATING_ENTRY_POINTS`, `_ALLOWED_MUTATING_CALLS`): exact script set, non-blank exemption reasons, exact Makefile targets, exact `.PHONY`, Makefile recipes reference only exempt scripts and DISPATCH commands, no mutating calls in scripts/worker (with a non-vacuity test showing exactly the `operator.py`/`trip_kill_switch` pair), thin-wrapper top-level def pins with no classes, and no `.claude` path ever scanned.
- Acceptance probe: an empty `scripts/tmp_probe.py` makes `test_scripts_directory_is_exactly_the_exempt_set` fail (probe removed).
- README no longer documents deleted scripts/targets; points operators to `/jobs/new` and `/controls`, break-glass section retained.

## Deleted paths (git rm)

scripts/dry_run.py, scripts/evaluate_risk.py, scripts/run_paper_session.py, scripts/reconcile_paper_execution.py, scripts/ingest_polygon_bars.py, scripts/sync_symbol_metadata.py, scripts/sync_paper_state.py, scripts/run_backtest.py, scripts/operator_control.py, scripts/submit_paper_orders.py, tests/test_dry_run.py

Makefile targets removed: dry-run, backtest, ingest-bars, sync-metadata, sync-sessions, submit-paper-orders, run-paper-session, sync-paper-state, reconcile-paper-execution. `tests/test_dry_run.py` dropped from the `test` target list.

## Reference grep before deleting bootstrap symbols

`grep -rn "PlatformServices|DryRunReport|build_placeholder_services|create_strategy_run|update_strategy_run|run_dry_bootstrap" src tests scripts --include='*.py'` outside `services/bootstrap.py` returned only `scripts/dry_run.py` (deleted) and unrelated test-local helpers named `_create_strategy_run` in tests/test_phase19_job_operations_migration.py and tests/test_phase20_operations_migration.py. Safe to delete.

## Task Commits

1. Task 1: delete bypass scripts, Makefile targets, dry-run bootstrap; README - `a1ff13e`
2. Task 2: closed-world boundary test (+ test_market_data_access rewire) - `f76c4dd`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] tests/test_market_data_access.py imported from the deleted script**
- **Found during:** Task 2 full-suite run (2 failures: `TestSymbolMetadataUpsert` tests did `from sync_symbol_metadata import _upsert_symbol_metadata`; my pre-delete reference grep required a `.py` suffix and missed a module import)
- **Fix:** Import `upsert_symbol_metadata` from `trading_platform.services.symbol_metadata_sync` instead; assertions unchanged
- **Files modified:** tests/test_market_data_access.py
- **Commit:** f76c4dd

Otherwise the plan executed as written.

## Verification

- Full Python suite: 954 passed before the fix, 2 failures (above); tests/test_market_data_access.py then 29/29 and tests/test_orchestration_boundaries.py 47/47. The complete suite was not re-run after the one-file import fix (targeted files green).
- `ruff check src tests scripts` clean; mypy on execution/reconciliation/config clean.
- Console vitest/tsc not re-run: no console files touched by this plan.

## Known Stubs

None.

## Threat Flags

None. T-20-24-01..04 mitigated by the deletions and the new boundary test.

## Self-Check: PASSED
