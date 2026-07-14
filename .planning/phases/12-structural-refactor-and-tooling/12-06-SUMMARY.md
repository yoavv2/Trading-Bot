---
phase: 12-structural-refactor-and-tooling
plan: 06
subsystem: cli
tags: [python, argparse, refactor, structural-split, worker-cli]

# Dependency graph
requires:
  - phase: 12-02
    provides: services/config package (validation.py ExecutionMode) — final config import path for every moved handler
  - phase: 12-04
    provides: services/execution package (final import paths) — paper_execute.py and reconcile.py import from it
  - phase: 12-05
    provides: services/reconciliation package (final import paths) — reconcile.py imports reconcile_paper_execution from it
provides:
  - worker/parser.py (build_parser, ~170-line argparse construction, moved verbatim)
  - worker/commands/{bootstrap,ingest,backtest,risk_check,paper_execute,reconcile,operator}.py (all 15 CLI subcommand handlers, moved verbatim, repointed to final service package paths)
  - worker/commands/__init__.py (DISPATCH map: command-string -> handler)
  - worker/__main__.py reduced to pure routing (32 lines): build parser, resolve handler, call it, top-level error
affects: [structural-refactor-and-tooling, 12-07 pre-commit gates]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dispatch-map routing: __main__.main() special-cases the two non-uniform-signature handlers (serve/dry-run, which take positional scalar args) and resolves every other subcommand through a {command_string: handler} dict imported from commands/__init__.py, rather than the prior if/elif chain"
    - "Seventh sibling command module (operator.py) added beyond STRUCT-03's six named modules — the two operator-* subcommands don't fit any of bootstrap/ingest/backtest/risk_check/paper_execute/reconcile, and leaving them inline would violate the entrypoint-must-be-pure-routing criterion. Documented explicitly per the plan's own key_facts so this isn't read as scope creep."

key-files:
  created:
    - src/trading_platform/worker/parser.py
    - src/trading_platform/worker/commands/__init__.py
    - src/trading_platform/worker/commands/bootstrap.py
    - src/trading_platform/worker/commands/ingest.py
    - src/trading_platform/worker/commands/backtest.py
    - src/trading_platform/worker/commands/risk_check.py
    - src/trading_platform/worker/commands/paper_execute.py
    - src/trading_platform/worker/commands/reconcile.py
    - src/trading_platform/worker/commands/operator.py
  modified:
    - src/trading_platform/worker/__main__.py
    - tests/test_operator_controls.py
    - tests/test_concurrency_guard_e2e.py
    - tests/test_startup_validation.py

key-decisions:
  - "operator.py added as a seventh command module beyond STRUCT-03's six named modules (see tech-stack.patterns above)"
  - "Preserved a discovered pre-existing bug verbatim rather than silently fixing it: run_sync_metadata's scripts/ path resolution used Path(__file__).resolve().parents[4] in __main__.py, which actually resolved one directory ABOVE the project root (no scripts/ there) — a real non-dry-run sync-metadata invocation was already broken pre-refactor, uncovered by any test. Moving the function one directory deeper (into commands/) would have silently 'fixed' this by coincidence if the literal index were kept, which is itself an unauthorized behavior change under this plan's zero-behavior-change contract. Adjusted to parents[5] in the new location to reproduce the exact original (broken) resolved path bit-for-bit; documented as a discovered bug, not fixed, in deferred-items.md"

requirements-completed: [STRUCT-03, STRUCT-02]

# Metrics
duration: ~30min
completed: 2026-07-15
---

# Phase 12 Plan 06: Worker CLI Command-Module Split (STRUCT-03) + Full-Suite STRUCT-02 Proof Summary

**Split the 795-line `worker/__main__.py` into `worker/parser.py` + seven `worker/commands/*.py` handler modules, reducing the entrypoint to a 32-line routing-only dispatcher — the full existing suite holds at exactly 306 passed / 0 failed, closing out both STRUCT-03 and, as the final Phase-12 code move, the phase-wide STRUCT-02 zero-behavior-change proof.**

## Performance

- **Duration:** ~30 min
- **Completed:** 2026-07-15
- **Tasks:** 2
- **Files modified:** 13 (9 created, 4 modified)

## Accomplishments
- Moved `build_parser` (the ~170-line argparse construction) verbatim into `worker/parser.py`.
- Moved all 15 CLI subcommand handlers verbatim into seven named modules: `bootstrap.py` (serve, dry-run), `ingest.py` (ingest-bars, sync-metadata, sync-sessions), `backtest.py` (backtest, report-backtest, report-strategy-analytics), `risk_check.py` (evaluate-risk), `paper_execute.py` (submit-paper-orders, run-paper-session, sync-paper-state, plus the shared `_handle_concurrent_run_lock_denied` helper), `reconcile.py` (reconcile-paper-execution), and `operator.py` (operator-control, operator-status, plus the shared `_run_kill_switch_action` helper).
- Repointed every moved handler's service imports to the FINAL package paths established by 12-02 (`services.config.validation`), 12-04 (`services.execution`), and 12-05 (`services.reconciliation`) — grepped for stale `paper_execution`/`order_state_machine`/`order_identity`/`reconciliation_matcher`/`reconciliation_types`/`core.config_validation` references; none found.
- Built `commands/__init__.py`'s `DISPATCH` map (13 uniform-signature subcommands) that `__main__.main()` consumes; `serve`/`dry-run` stay special-cased since their handlers take positional scalar args, not `argparse.Namespace`.
- Reduced `worker/__main__.py` to 32 lines: build the parser, special-case serve/dry-run, resolve everything else through `DISPATCH`, surface the unknown-command error. Verified via `grep -qE '^\s*(async )?def (run_|_run_)'` that zero handler bodies remain.
- Repointed the three test files that imported handlers/`build_parser` directly from `worker.__main__` (`test_operator_controls.py`, `test_concurrency_guard_e2e.py`, `test_startup_validation.py`) onto `worker.parser` / `worker.commands.*` — import-line and `monkeypatch.setattr` target repoints only, zero assertion changes.
- Discovered and preserved (not fixed) a pre-existing bug in `run_sync_metadata`'s scripts-path resolution (see key-decisions above and `deferred-items.md`).
- Full suite: **306 passed / 0 failed** — exactly matches `12-BASELINE.md` (1 `pg_terminate_backend` teardown ERROR observed, the documented environmental flake; confirmed by re-running the affected test standalone, which passed).
- `PYTHONPATH=src .venv/bin/python -m trading_platform.worker --help` and a real `dry-run --strategy trend_following_daily` invocation both succeed end-to-end post-split.

## Task Commits

1. **Task 1: Extract parser + command modules from __main__.py** - `10f998a` (refactor)
2. **Task 2: Reduce __main__.py to routing-only dispatch and prove the baseline** - `c53b63f` (refactor)

## Files Created/Modified
- `src/trading_platform/worker/parser.py` - `build_parser()`, moved verbatim from `__main__.py`.
- `src/trading_platform/worker/commands/__init__.py` - `DISPATCH: dict[str, Callable[[argparse.Namespace], None]]` mapping 13 subcommand strings to handlers; re-exports `run_dry_bootstrap`/`run_placeholder_worker` for `__main__.py`'s special-cased serve/dry-run branches.
- `src/trading_platform/worker/commands/bootstrap.py` - `run_placeholder_worker`, `run_dry_bootstrap`.
- `src/trading_platform/worker/commands/ingest.py` - `run_ingest_bars`, `run_sync_metadata`, `run_sync_sessions`.
- `src/trading_platform/worker/commands/backtest.py` - `run_backtest_command`, `run_report_backtest_command`, `run_report_strategy_analytics_command`.
- `src/trading_platform/worker/commands/risk_check.py` - `run_evaluate_risk_command`.
- `src/trading_platform/worker/commands/paper_execute.py` - `run_submit_paper_orders_command`, `run_paper_session_command`, `run_sync_paper_state_command`, `_handle_concurrent_run_lock_denied`.
- `src/trading_platform/worker/commands/reconcile.py` - `run_reconcile_paper_execution_command`.
- `src/trading_platform/worker/commands/operator.py` - `run_operator_control_command`, `_run_kill_switch_action`, `run_operator_status_command`.
- `src/trading_platform/worker/__main__.py` - reduced from 795 to 32 lines; imports `DISPATCH`/`run_dry_bootstrap`/`run_placeholder_worker` from `worker.commands` and `build_parser` from `worker.parser`.
- `tests/test_operator_controls.py` - `build_parser`/`run_operator_control_command` imports repointed to `worker.parser`/`worker.commands.operator`.
- `tests/test_concurrency_guard_e2e.py` - `build_parser`/`run_submit_paper_orders_command` imports repointed to `worker.parser`/`worker.commands.paper_execute`.
- `tests/test_startup_validation.py` - two tests repointed: the lock-denial ordering test now imports `worker.commands.paper_execute` (monkeypatch target + handler call) and `worker.parser.build_parser`; the entrypoint-wiring static-inspection test now imports each of the seven command modules instead of a single `worker.__main__` alias.

## Decisions Made
See `key-decisions` in frontmatter: (1) `operator.py` added as a documented seventh command module; (2) the pre-existing `run_sync_metadata` scripts-path bug was preserved verbatim (index adjusted for the new file depth), not silently fixed, per the zero-behavior-change contract — logged in `deferred-items.md` for a future follow-up plan.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Three test files imported handlers/build_parser directly from worker.__main__**
- **Found during:** Task 2 (slimming __main__.py)
- **Issue:** `tests/test_operator_controls.py`, `tests/test_concurrency_guard_e2e.py`, and `tests/test_startup_validation.py` all imported `build_parser` and/or specific `run_*_command` handlers directly from `trading_platform.worker.__main__`. Once those names were removed from `__main__.py` (the entire point of Task 2), these imports would `ImportError` and block the suite.
- **Fix:** Repointed each import to the new home (`worker.parser.build_parser`; `worker.commands.operator.run_operator_control_command`; `worker.commands.paper_execute.run_submit_paper_orders_command`/`run_paper_order_submission` monkeypatch target; and, in `test_startup_validation.py`'s static-inspection test, each of the seven `worker.commands.*` modules). Import-line and monkeypatch-target changes only — no assertion body was modified.
- **Files modified:** tests/test_operator_controls.py, tests/test_concurrency_guard_e2e.py, tests/test_startup_validation.py
- **Verification:** Full suite run — 306 passed / 0 failed, matching 12-BASELINE.md.
- **Committed in:** c53b63f (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 3 — blocking import repoints required to keep the suite green after the entrypoint slim-down). No architectural changes; no scope creep beyond what the plan's own "Import-line edits in src AND tests are required and are NOT assertion changes" key_fact authorized.

## Issues Encountered
None beyond the deviation documented above and the pre-existing `run_sync_metadata` bug documented in `deferred-items.md` (discovered but deliberately not fixed).

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- STRUCT-03 is Complete: `worker/__main__.py` is routing-only (32 lines: parser build, serve/dry-run special-cases, DISPATCH lookup, unknown-command error); all domain command logic lives in `worker/commands/{bootstrap,ingest,backtest,risk_check,paper_execute,reconcile,operator}.py`.
- STRUCT-02 is Complete: this plan is the last Phase-12 code move, and the full suite holds at the immutable 306 passed / 0 failed baseline with zero assertion changes — proving the entire phase's refactor sequence (12-01 through 12-06) introduced zero behavior change end-to-end.
- Marked STRUCT-02 and STRUCT-03 Complete in `.planning/milestones/v1.1-paused/REQUIREMENTS.md` (checklist items + traceability table rows).
- No blockers for 12-07 (pre-commit gates), the final Phase-12 plan.

---
*Phase: 12-structural-refactor-and-tooling*
*Completed: 2026-07-15*

## Self-Check: PASSED
- FOUND: src/trading_platform/worker/parser.py
- FOUND: src/trading_platform/worker/commands/__init__.py
- FOUND: src/trading_platform/worker/commands/{bootstrap,ingest,backtest,risk_check,paper_execute,reconcile,operator}.py
- FOUND: .planning/phases/12-structural-refactor-and-tooling/12-06-SUMMARY.md
- FOUND: commit 10f998a (Task 1), commit c53b63f (Task 2), commit 82d7882 (docs)
- SUITE: 306 passed / 0 failed (baseline held)
