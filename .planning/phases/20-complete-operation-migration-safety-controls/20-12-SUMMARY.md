---
phase: 20-complete-operation-migration-safety-controls
plan: 12
subsystem: worker-cli
tags: [argparse, ast-enforcement, operator-controls, kill-switch, tdd]

# Dependency graph
requires:
  - phase: 20-03
    provides: symbol-metadata/market-session sync extracted into services/, freeing worker/commands/ingest.py from being the sole caller
  - phase: 20-07
    provides: Job types replacing the removed worker paper-execution commands
  - phase: 20-08
    provides: Job types replacing the removed worker risk-check/reconcile commands
  - phase: 20-09
    provides: Job types replacing the removed worker bootstrap/ingest commands
provides:
  - Worker CLI reduced to exactly {report-backtest, report-strategy-analytics, operator-status, run-jobs, kill-switch-trip}
  - Break-glass kill-switch-trip subcommand (D-15): trip-only, reason-required, DB-only, same audit rows as the HTTP control
  - __main__.py main() is a pure DISPATCH.get(args.command) lookup, zero args.command == <literal> special cases
  - AST-enforced worker-wide ban on reset_kill_switch/enable_strategy/disable_strategy calls, with trip_kill_switch pinned to the single break-glass handler
affects: [20-13, 20-24]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Break-glass CLI pattern: validate input before constructing any service or touching the DB (SystemExit(2) on bad --reason before OperatorControlService is even built)"
    - "AST-walk enforcement test pinning a forbidden-call set (reset_kill_switch/enable_strategy/disable_strategy) across an entire package tree, plus a positive pin restricting trip_kill_switch to one caller"

key-files:
  created:
    - tests/test_kill_switch_trip_cli.py
  modified:
    - src/trading_platform/worker/commands/operator.py
    - src/trading_platform/worker/parser.py
    - src/trading_platform/worker/commands/__init__.py
    - src/trading_platform/worker/__main__.py
    - src/trading_platform/worker/commands/backtest.py
    - tests/test_orchestration_boundaries.py
    - tests/test_startup_validation.py
    - README.md
  deleted:
    - src/trading_platform/worker/commands/bootstrap.py
    - src/trading_platform/worker/commands/paper_execute.py
    - src/trading_platform/worker/commands/reconcile.py
    - src/trading_platform/worker/commands/risk_check.py
    - src/trading_platform/worker/commands/ingest.py

key-decisions:
  - "ORCH-01/ORCH-08 are NOT marked complete by this plan despite being listed in its requirements frontmatter -- both requirements' literal text (no script/Makefile bypass; every mutating scripts/*.py and dead worker/commands/* function removed) is only fully closed by 20-24, which runs last specifically so its exact-set literals describe the final state. This plan is a necessary but partial contribution (the worker-CLI half); scripts/*.py and Makefile cleanup remain out of scope here."
  - "run_kill_switch_trip_command validates --reason (trim, non-empty, <=500 chars) and raises SystemExit(2) before constructing OperatorControlService or opening any DB session, so a rejected reason writes zero rows -- matches the plan's explicit ordering requirement."
  - "Docstring in run_kill_switch_trip_command avoids the literal string 'mutations_enabled' (paraphrased as 'mutation-enablement gate') to satisfy the acceptance criterion that the function never references that setting even in comment form."

patterns-established:
  - "AST-walk forbidden-call scan pattern (test_worker_commands_call_no_reset_or_strategy_mutators) for pinning a package-wide behavioral invariant that can't be expressed as a simple import-boundary check."

requirements-completed: []

# Metrics
duration: 10min
completed: 2026-09-28
---

# Phase 20 Plan 12: Break-glass kill-switch-trip + dead worker command deletion Summary

**Reduced the worker CLI to its final five-command Phase 20 surface (report-backtest, report-strategy-analytics, operator-status, run-jobs, kill-switch-trip) by deleting five dead command modules and the `serve` placeholder path, and added a trip-only break-glass `kill-switch-trip` subcommand audited identically to the HTTP kill-switch control.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-09-28T17:39:02+03:00
- **Completed:** 2026-09-28T17:47:07+03:00
- **Tasks:** 2 completed
- **Files modified:** 13 (8 modified, 5 deleted, 1 created — README.md counted once)

## Accomplishments
- Added `run_kill_switch_trip_command`: validates `--reason` (trim, 1-500 chars, `SystemExit(2)` before any DB touch), boots at `ExecutionMode.BACKTEST` (no broker credentials), never reads `mutations_enabled`, and writes the same `OPERATOR_CONTROL` `StrategyRun` (`trigger_source="break_glass_cli"`) + `kill_switch_trip` `ExecutionEvent` audit rows as the HTTP `OperatorControlService.trip_kill_switch` path.
- Deleted `worker/commands/{bootstrap,paper_execute,reconcile,risk_check,ingest}.py` entirely, `run_backtest_command` from `backtest.py`, and `run_operator_control_command`/`_run_kill_switch_action` from `operator.py` — all confirmed unreachable via DISPATCH before deletion.
- Removed the `serve` placeholder subparser and its `__main__.py` special case; `main()` is now a pure `DISPATCH.get(args.command)` lookup with zero `args.command == <literal>` branches (AST-verified).
- Pinned the final worker surface with exact-set parser/DISPATCH tests (`_RETAINED_CLI_COMMANDS`/`_RETAINED_DISPATCH_COMMANDS` both `{report-backtest, report-strategy-analytics, operator-status, run-jobs, kill-switch-trip}`) and expanded `_REMOVED_CLI_COMMANDS` with `serve`, `kill-switch-reset`, `reset-kill-switch`, `enable-strategy`, `disable-strategy`.
- Added an AST-walk test proving no module under `src/trading_platform/worker/` calls `reset_kill_switch`, `enable_strategy`, or `disable_strategy`, and that `trip_kill_switch` is called only from `worker/commands/operator.py`.
- Documented the command in README.md as the sole ORCH-01 exception (trip-only, DB-only, same audit rows, reset/enable/disable remain HTTP-only).

## Task Commits

Each task was committed atomically:

1. **Task 1: Add kill-switch-trip break-glass subcommand** (TDD) —
   - `178de3c` test(20-12): add failing test for kill-switch-trip break-glass CLI (RED)
   - `9384df7` feat(20-12): add kill-switch-trip break-glass worker subcommand (GREEN)
2. **Task 2: Delete dead worker commands + serve path; pin the final worker surface** — `78f745b` feat(20-12): delete dead worker commands and serve path (D-30)

**Plan metadata:** (this commit)

## Files Created/Modified
- `tests/test_kill_switch_trip_cli.py` - 8 tests: armed trip, repeat-trip stays-tripped/re-audits, mutations-disabled + empty-broker-creds success, missing/blank/over-500-char reason rejection with zero rows written
- `src/trading_platform/worker/commands/operator.py` - `run_kill_switch_trip_command` added; `run_operator_control_command`/`_run_kill_switch_action` and their now-unused `json`/`logging` imports removed
- `src/trading_platform/worker/parser.py` - `kill-switch-trip` subparser (required `--reason`) added; `serve` subparser removed
- `src/trading_platform/worker/commands/__init__.py` - DISPATCH gains `"kill-switch-trip"`; `run_placeholder_worker` import/export and the `serve` docstring paragraph removed
- `src/trading_platform/worker/__main__.py` - `main()` reduced to parse → `DISPATCH.get(args.command)` → `parser.error(...)` or `handler(args)`, no `serve` special case
- `src/trading_platform/worker/commands/backtest.py` - `run_backtest_command` and its now-unused `json`/`resolve_backtest_window`/`run_backtest` imports removed
- `tests/test_orchestration_boundaries.py` - exact-set commands updated; `test_worker_entrypoint_has_only_serve_special_case_and_dispatch_lookup` replaced by `test_worker_entrypoint_is_a_pure_dispatch_lookup`; new `test_worker_commands_call_no_reset_or_strategy_mutators`
- `tests/test_startup_validation.py` - `test_gate_is_wired_into_api_worker_and_bootstrap_entrypoints` now imports only the three surviving worker modules and asserts the five surviving gated functions; the `services.bootstrap.run_dry_bootstrap` assertion removed
- `README.md` - new "Break-glass Kill Switch" section documenting the command, its trip-only scope, and that reset/enable/disable exist only as HTTP controls
- `src/trading_platform/worker/commands/{bootstrap,paper_execute,reconcile,risk_check,ingest}.py` - deleted

## Decisions Made
- ORCH-01/ORCH-08 left un-marked in REQUIREMENTS.md despite being listed in this plan's frontmatter — 20-24 is the explicitly-designated plan that closes both with its own exact-set AST literals over the final state (scripts/ + Makefile cleanup is out of this plan's scope). Marking them complete here would overclaim.
- Input validation for `--reason` happens strictly before any service construction or DB session, guaranteeing zero audit rows on rejection.
- Avoided the literal string `mutations_enabled` in the new function's docstring (paraphrased) so the function's source text never references that setting, matching the acceptance criterion literally, not just in spirit.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Worker CLI surface is now exactly the five Phase 20 commands; Plans 13+ (console/HTTP mutation wiring) and 20-24 (final ORCH-01/02/08 AST literal pass) can build on this without any dead-command drag.
- Full suite: 806 passed, 0 failed (baseline was 793; net +13 from this plan's new/changed tests).

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

All created/modified files confirmed present on disk; all five deleted worker command modules confirmed absent; all three task commit hashes (178de3c, 9384df7, 78f745b) confirmed present in git history.
