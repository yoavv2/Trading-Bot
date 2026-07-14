---
phase: 12-structural-refactor-and-tooling
plan: 04
subsystem: execution
tags: [python, sqlalchemy, refactor, structural-split, paper-execution]

# Dependency graph
requires:
  - phase: 12-03
    provides: services/execution package (contracts.py, transition.py, idempotency.py) with lazy PEP 562 __getattr__ re-export pattern for heavy/cyclic entrypoints
provides:
  - services/execution/submit_orders.py (submission + session orchestration + intent decisions)
  - services/execution/sync_orders.py (broker-state sync: orders, fills, positions, account)
  - services/execution/_paper_common.py (shared dataclasses + cross-cutting helpers)
  - services/paper_execution.py fully deleted; all consumers repoint through services.execution
affects: [structural-refactor-and-tooling, future execution-package work]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Package split with a temporary re-export shim (Task 1) to keep the tree buildable, then delete-last after Task 2 repoints every real consumer and targeted suites are green"
    - "PEP 562 module __getattr__ lazy exports keep `services.execution` import cheap/acyclic while still resolving heavy paper-execution entrypoints for `from trading_platform.services.execution import X`"
    - "LOG-01 static-scan enforcement list entries follow a 1:1-heir mapping when a monolithic module splits: map to whichever split-off module actually retains the guarded behavior (get_logger usage), not to every split-off file, to keep the enforcement list length -- and therefore the full-suite pass count -- frozen"

key-files:
  created: []
  modified:
    - src/trading_platform/worker/__main__.py
    - tests/test_paper_execution.py
    - tests/test_paper_preflight_query_count.py
    - tests/test_execution_reconciliation.py
    - tests/test_operator_controls.py
    - tests/test_concurrency_guard_e2e.py
    - tests/test_analytics_service.py
    - tests/test_alpaca_execution.py
    - tests/test_log_enforcement.py
  deleted:
    - src/trading_platform/services/paper_execution.py

key-decisions:
  - "Split the single `paper_execution_module` test alias into two: `paper_submit_orders_module` (services.execution.submit_orders) and `paper_sync_orders_module` (services.execution.sync_orders), since monkeypatch.setattr targets and direct helper calls now resolve against two different concrete modules instead of one"
  - "Widened the grep-discovered consumer set beyond the plan's named list (test_operator_controls.py, test_concurrency_guard_e2e.py, test_analytics_service.py, test_alpaca_execution.py all imported the old module path and needed repointing before the shim could be safely deleted)"
  - "LOG-01 IN_SCOPE_MODULES: repointed the paper_execution.py entry 1:1 to submit_orders.py only (not all three split-off files), since submit_orders.py is the only one with get_logger calls -- keeps the parametrized-test count, and therefore the full-suite 306 baseline, exactly frozen"

requirements-completed: [STRUCT-04]

# Metrics
duration: ~35min (Task 2 only; Task 1 was completed and committed in a prior session)
completed: 2026-07-14
---

# Phase 12 Plan 04: Paper-Execution Package Split (Task 2 -- Repoint Consumers + Delete Shim) Summary

**Repointed every real consumer of `services/paper_execution.py` (worker CLI + 8 test files) to the new `services/execution` package/submodules, then deleted the temporary re-export shim -- full suite holds exactly at the 306/0 STRUCT-01 baseline.**

## Performance

- **Duration:** ~35 min (this session, Task 2 only)
- **Completed:** 2026-07-14T21:19:11+03:00
- **Tasks:** 1 (Task 2; Task 1 was already committed at 2b6d26b before this session started)
- **Files modified:** 9 (1 src, 8 tests); 1 file deleted

## Accomplishments
- Grepped for every real import of the old module path (excluding string-value/symbol-name false positives like the `"paper_execution"` run_type literal and `list_blocked_paper_executions`) and repointed all of them: `worker/__main__.py` plus 8 test files.
- Discovered and repointed 4 consumers beyond the plan's named set (`test_operator_controls.py`, `test_concurrency_guard_e2e.py`, `test_analytics_service.py`, `test_alpaca_execution.py`) that also imported the old module path.
- Repointed test mock.patch targets and direct private-helper calls to whichever new submodule (`submit_orders.py` vs `sync_orders.py`) actually owns the symbol now, so patching/calling behavior is preserved exactly (verified empirically: `test_partial_failure_after_broker_success_schedules_reconciliation` and `test_broker_call_failure_has_no_rollback_divergence_and_skips_reconciliation_scheduling` both pass, proving the patched `apply_order_transition`/`schedule_reconciliation_after_partial_failure` reach the real internal call sites in `submit_orders.py`).
- Updated the lifecycle-routing structural-invariant test (`test_paper_execution_module_routes_lifecycle_through_order_state_machine`) to scan the concatenated source of `submit_orders.py` + `sync_orders.py` instead of the now-deleted single file, preserving the exact same assertion text.
- Fixed a test-suite break the plan didn't anticipate: `tests/test_log_enforcement.py`'s `IN_SCOPE_MODULES` (LOG-01 static AST scan) still listed the deleted `services/paper_execution.py` path, which failed `test_import_boundary_no_direct_get_logger[services/paper_execution.py]` with `AssertionError: in-scope module not found`. Repointed 1:1 to `submit_orders.py` (the only split-off module with `get_logger` calls), keeping the parametrized list at exactly 12 entries so the full-suite pass count stayed frozen at the 306 baseline (confirmed via advisor consultation after an initial 3-way-split attempt produced 308 passed, violating the invariant).
- Deleted the temporary shim `services/paper_execution.py` only after the grep for real module imports was clean and the targeted suites were green (delete-last, per plan).
- Re-verified `PYTHONPATH=src .venv/bin/python -m trading_platform.worker --help` succeeds post-deletion.
- Full suite: **306 passed / 0 failed** -- exactly matches 12-BASELINE.md. (2 `pg_terminate_backend` teardown errors observed, the documented environmental flake per 12-BASELINE.md; ignored per its explicit comparison rule.)

## Task Commits

1. **Task 1: Move paper_execution.py into submit_orders + sync_orders + _paper_common; leave a temporary shim** - `2b6d26b` (refactor) -- completed in a prior session, not part of this execution.
2. **Task 2: Repoint all consumers + tests, run affected suites, then delete the shim** - `426a06e` (refactor)

## Files Created/Modified
- `src/trading_platform/worker/__main__.py` - `from trading_platform.services.paper_execution import (...)` repointed to `from trading_platform.services.execution import (...)`, moved to correct alphabetical import position.
- `tests/test_paper_execution.py` - import block repointed; `paper_execution_module` alias split into `paper_submit_orders_module` (`services.execution.submit_orders`) and `paper_sync_orders_module` (`services.execution.sync_orders`); all monkeypatch/direct-call sites updated to the correct submodule; structural-invariant test reads concatenated `submit_orders.py` + `sync_orders.py` source.
- `tests/test_paper_preflight_query_count.py` - `import trading_platform.services.paper_execution as paper_execution_module` repointed to `import trading_platform.services.execution.submit_orders as paper_execution_module` (alias kept, only one symbol used: `_build_paper_session_plan`).
- `tests/test_execution_reconciliation.py` - `build_client_order_id` import merged into the existing `from trading_platform.services.execution import ...` line.
- `tests/test_operator_controls.py` - `run_paper_order_submission, sync_paper_state` repointed to `trading_platform.services.execution`.
- `tests/test_concurrency_guard_e2e.py` - `run_paper_order_submission` merged into the existing `from trading_platform.services.execution import (...)` block.
- `tests/test_analytics_service.py` - `build_client_order_id` merged into the existing `from trading_platform.services.execution import OrderSide, ...` line.
- `tests/test_alpaca_execution.py` - `run_paper_order_submission` merged into the existing `from trading_platform.services.execution import (...)` block.
- `tests/test_log_enforcement.py` - `IN_SCOPE_MODULES` entry for `services/paper_execution.py` repointed 1:1 to `services/execution/submit_orders.py`; list length assertion stays `== 12`.
- `src/trading_platform/services/paper_execution.py` - **deleted** (temporary Task-1 shim, no longer needed).

## Decisions Made
- Split the single `paper_execution_module` test alias into two names bound to the two concrete submodules, since patch targets/direct-call sites for the submission side (`apply_order_transition`, `schedule_reconciliation_after_partial_failure`, both used inside `submit_orders.py`) and the sync side (`_ingest_paper_fills`, `_load_existing_paper_fill_ids`, `_PAPER_FILL_DEDUP_CHUNK_SIZE`, all in `sync_orders.py`) now live in different modules. Monkeypatching an attribute on a *wildcard-imported* alias (the old shim) never affected the real internal call site -- this was the literal reason the 8 pre-existing "expected intermediate" failures occurred, since the shim exists only via `from ... import *`, which copies names into a separate namespace disconnected from the module that actually calls them.
- Widened the "known consumers" set beyond the plan's named list. The plan named `worker/__main__.py`, `operator_reads.py`, `operator_status.py`, `stale_runs.py`, and 3 test files, but instructed "grep to confirm the full set." The grep surfaced 4 additional test-file consumers (`test_operator_controls.py`, `test_concurrency_guard_e2e.py`, `test_analytics_service.py`, `test_alpaca_execution.py`) that also imported from the old module path and would have broken on shim deletion. `operator_reads.py`, `operator_status.py`, `stale_runs.py` were confirmed via grep to reference only the `"paper_execution"` string value / `list_blocked_paper_executions` symbol name, not the module import -- correctly left untouched per the plan's own carve-out.
- LOG-01 enforcement list: chose the 1:1-heir mapping (`submit_orders.py` only) over a 1-to-3 expansion (`submit_orders.py` + `sync_orders.py` + `_paper_common.py`) because the latter changed the full-suite pass count from 306 to 308 (each new parametrize entry is a new test-run instance), violating the plan's explicit hard invariant. Verified via grep that `sync_orders.py` and `_paper_common.py` have zero `get_logger`/`logging.getLogger` calls today, so no enforcement coverage is actually lost by this choice; noted below as a deferred item if either module gains logging in the future.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `tests/test_log_enforcement.py` LOG-01 in-scope module list referenced the deleted `paper_execution.py` path**
- **Found during:** Full-suite re-run after shim deletion
- **Issue:** `IN_SCOPE_MODULES` (the LOG-01 static AST-scan enforcement list, not named in the plan's `files_modified`) still listed `_SRC / "services" / "paper_execution.py"`. After deletion, `test_import_boundary_no_direct_get_logger[services/paper_execution.py]` failed with `AssertionError: in-scope module not found`, dropping the full suite to 305 passed / 1 failed.
- **Fix:** Repointed the entry 1:1 to `services/execution/submit_orders.py` (verified via grep to be the only split-off module that actually calls `get_logger`), following the exact "frozen list length" precedent 12-02/12-03 established for prior 1:1 relocations in this same file. Kept the length-guard assertion at `== 12`.
- **Files modified:** tests/test_log_enforcement.py
- **Verification:** `PYTHONPATH=src .venv/bin/pytest tests/test_log_enforcement.py -q` -> 18 passed. Full suite re-run -> exactly 306 passed / 0 failed, matching 12-BASELINE.md.
- **Committed in:** 426a06e (Task 2 commit)

**2. [Rule 1 - Bug] Initial LOG-01 fix attempt (1-to-3 expansion) broke the frozen pass-count invariant**
- **Found during:** Full-suite verification, first attempt
- **Issue:** Initially repointed the deleted entry to all three split-off modules (`submit_orders.py`, `sync_orders.py`, `_paper_common.py`), reasoning that full structural coverage of the original monolithic file's scope should be preserved. This grew `IN_SCOPE_MODULES` from 12 to 14 entries, which -- because `test_import_boundary_no_direct_get_logger` is parametrized over that list -- added 2 new test-run instances, producing 308 passed instead of the mandated exact 306.
- **Fix:** Consulted the advisor tool, which confirmed the 306 invariant is dispositive and directed reverting to the 1:1-heir mapping (deviation #1 above). Reverted to `submit_orders.py` only, restored the `== 12` length guard.
- **Files modified:** tests/test_log_enforcement.py (same file, superseding edit)
- **Verification:** Full suite re-run -> 306 passed / 0 failed.
- **Committed in:** 426a06e (final state; the intermediate 14-entry version was never committed)

**3. [Rule 1 - Bug] Grep-discovered consumers outside the plan's named `files_modified` list**
- **Found during:** Task 2, step 1 (grep for real module imports)
- **Issue:** `tests/test_operator_controls.py`, `tests/test_concurrency_guard_e2e.py`, `tests/test_analytics_service.py`, `tests/test_alpaca_execution.py` all imported `trading_platform.services.paper_execution` directly and were not named in the plan's `files_modified` list. Deleting the shim without fixing these would have broken them.
- **Fix:** Repointed each to `trading_platform.services.execution` (import-line only, no assertion changes).
- **Files modified:** tests/test_operator_controls.py, tests/test_concurrency_guard_e2e.py, tests/test_analytics_service.py, tests/test_alpaca_execution.py
- **Verification:** `PYTHONPATH=src .venv/bin/pytest tests/test_operator_controls.py tests/test_concurrency_guard_e2e.py tests/test_analytics_service.py tests/test_alpaca_execution.py -q` -> 21 passed. Confirmed again in the full-suite run.
- **Committed in:** 426a06e (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (all Rule 1 - bug fixes required to keep the suite green / hold the exact 306 invariant). No architectural changes, no scope creep beyond what "grep to confirm the full set" (the plan's own instruction) required.
**Impact on plan:** All three were necessary corrections to reach the plan's own stated success criteria (grep-clean, targeted suites green, full suite == 306/0). No behavior changed; only import lines, a mock-patch alias split, and one enforcement-list path entry were touched.

## Issues Encountered
None beyond the deviations documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- STRUCT-04 is now fully Complete: `services/execution` holds all four declared modules (`contracts.py`, `transition.py`, `idempotency.py` from 12-03; `submit_orders.py`, `sync_orders.py`, `_paper_common.py` from 12-04). `services/paper_execution.py` and the standalone pre-package modules are all deleted; every consumer resolves through `trading_platform.services.execution`.
- Full suite holds at the immutable 306 passed / 0 failed Phase-12 baseline with zero assertion changes -- the zero-behavior-change contract for this plan is satisfied.
- No blockers for the remaining Phase 12 plans (12-05, 12-06, 12-07).

---
*Phase: 12-structural-refactor-and-tooling*
*Completed: 2026-07-14*

## Self-Check: PASSED
- FOUND: .planning/phases/12-structural-refactor-and-tooling/12-04-SUMMARY.md
- FOUND: shim deleted (src/trading_platform/services/paper_execution.py no longer exists)
- FOUND: commit 2b6d26b (Task 1)
- FOUND: commit 426a06e (Task 2)
