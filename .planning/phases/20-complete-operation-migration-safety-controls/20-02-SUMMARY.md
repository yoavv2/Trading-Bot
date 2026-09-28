---
phase: 20-complete-operation-migration-safety-controls
plan: 02
subsystem: backend
tags: [sqlalchemy, backtest-reporting, operator-controls, read-path-purity, D-31]

# Dependency graph
requires:
  - phase: 20-01
    provides: migration 0021 schema spine (domain_conflict, retry lineage) that this plan built on top of without further migration
provides:
  - "build_backtest_report: pure zero-write backtest report read, replacing materialize_backtest_report"
  - "persist_backtest_metrics: the single BacktestMetric write path, called once at backtest SUCCEEDED"
  - "OperatorControlService.get_strategy_state / load_strategy_control_state: pure select with a registry-default StrategyControlState on an empty strategies table"
  - "OperatorControlService.ensure_strategy_state / ensure_strategy_control_state: get-or-create preserved for mutating callers (submit_orders.py)"
  - "tests/test_read_path_purity.py: runtime zero-write proof (engine before_cursor_execute + Session before_flush spies) for the three D-29 exempt scripts, the analytics GET route, and the control-state read/ensure pair"
affects: [20-23 (D-29 boundary test consumes this plan's now-literal exemption reasons), 20-03..20-24 (any later plan touching backtest_reporting.py or operator_controls.py)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure-read / persist-step split: a service exposes a zero-write builder function plus a separate persist_* function that mutating callers invoke inside their own transaction, rather than the read function silently upserting derived rows as a side effect."
    - "Registry-default reads: a pure read on an empty row returns the exact default value the mutating get-or-create sibling would persist, so read and write paths never disagree on an unseeded row."
    - "Runtime zero-write proof: sqlalchemy.event.listen(Engine, \"before_cursor_execute\") + event.listen(Session, \"before_flush\") spy, attached only inside a `with write_spy():` block after seeding, proving an entire call graph (CLI main() or FastAPI route) performs zero DB writes -- not just grep/inspect of the entrypoint function."

key-files:
  created:
    - tests/test_read_path_purity.py
    - .planning/phases/20-complete-operation-migration-safety-controls/deferred-items.md
  modified:
    - src/trading_platform/services/backtest_reporting.py
    - src/trading_platform/services/backtesting.py
    - src/trading_platform/services/analytics.py
    - src/trading_platform/services/operator_controls.py
    - src/trading_platform/services/operator_status.py
    - src/trading_platform/services/execution/submit_orders.py
    - tests/test_backtest_reporting.py

key-decisions:
  - "materialize_backtest_report renamed (not aliased) to build_backtest_report; every caller and test updated to the new name -- grep confirms zero remaining references to the old name."
  - "persist_backtest_metrics is called from _update_backtest_run only when status is SUCCEEDED, after session.flush() and before session.refresh(strategy_run), so the metric row lands in the same transaction that commits the run as SUCCEEDED; FAILED/RUNNING updates never call it."
  - "OperatorControlService.get_strategy_state is a plain select; on a missing row it returns StrategyControlState(status='active', updated_at=None) without creating anything -- the same ACTIVE default ensure_strategy_state would persist, pinned by test_empty_db_read_default_matches_ensure_status."
  - "submit_orders.py's two paper-submission call sites were switched from load_strategy_control_state to ensure_strategy_control_state (get-or-create), keeping the empty-DB execution-gating behavior byte-identical to pre-D-31 -- pinned by test_ensure_strategy_control_state_preserves_get_or_create."
  - "ORCH-08 (the larger 'exactly one mutation path per operation class' requirement, including removing mutating scripts/Makefile targets and the boundary test itself) is explicitly NOT marked complete by this plan -- this plan's frontmatter requirements: [ORCH-08] reflects that it is a prerequisite plan for ORCH-08, not the plan that closes it. The plan's own objective text names Plan 23 as the boundary-test owner that will consume these now-literal exemption reasons. Marking ORCH-08 complete here would overclaim; it stays Pending in REQUIREMENTS.md."
  - "Docstrings on build_backtest_report and get_strategy_state were deliberately worded to avoid the literal substrings 'session.add', '.flush(', and 'commit' so that test_read_functions_contain_no_write_calls's plain substring check (per the plan's literal action text) does not false-positive on the docstring itself describing what the function does NOT do."
  - "Post-completion advisor review found that run_backtest's final _update_backtest_run(SUCCEEDED) call -- which now also runs persist_backtest_metrics in the same transaction -- sat outside the existing try/except, so a metric-computation or flush failure would leave the run stuck RUNNING instead of landing on FAILED. Fixed by moving that call inside the try block so the existing except handler covers it (Rule 1 auto-fix); regression-pinned by test_metric_persistence_failure_lands_run_on_failed_status, confirmed to fail against the pre-fix code before being confirmed green against the fix."
  - "Also added test_write_spy_detects_writes as a positive control (advisor review) proving the write_spy instrument actually fires on a real INSERT + flush, so the purity tests' spy.is_empty assertions are evidence of purity rather than an unverified/never-dispatched listener."

patterns-established:
  - "Pure-read / persist-step split for any future service that both serializes a report and derives a row from it (see backtest_reporting.py)."
  - "Registry-default pure read matching mutating-sibling get-or-create default, for any future OperatorControlService-style state (see operator_controls.py)."

requirements-completed: []

# Metrics
duration: 15min
completed: 2026-09-28
---

# Phase 20 Plan 02: Read-Path Purity for Backtest Reports and Strategy Control State Summary

**Split backtest report materialization into a pure `build_backtest_report` read plus a `persist_backtest_metrics` write called once at backtest completion, and made `OperatorControlService.get_strategy_state` a plain select with a registry-default StrategyControlState, proven zero-write at runtime by a `tests/test_read_path_purity.py` engine/session spy across all three D-29 exempt scripts and the analytics GET route.**

## Performance

- **Duration:** ~30 min (including post-completion advisor review + fix)
- **Started:** 2026-09-28T07:39:15Z
- **Completed:** 2026-09-28T08:10:00Z
- **Tasks:** 2 completed + 1 post-completion Rule 1 fix
- **Files modified:** 9 (2 created, 7 modified)

## Accomplishments
- `backtest_reporting.build_backtest_report` performs zero writes (renamed from `materialize_backtest_report`, its `_upsert_backtest_metric` call deleted); `backtesting._update_backtest_run` now calls the new `persist_backtest_metrics(session, strategy_run)` exactly once, only on SUCCEEDED, in the same transaction.
- A successful `run_backtest` writes exactly one `backtest_metrics` row; a failed `run_backtest` (forced via monkeypatched `_execute_backtest_run`) writes zero -- both pinned by dedicated tests.
- `OperatorControlService.get_strategy_state`/`load_strategy_control_state` are pure selects; on an empty `strategies` table they return the registry default (`status="active"`, `updated_at=None`) without inserting anything, matching exactly what the new `ensure_strategy_state`/`ensure_strategy_control_state` get-or-create sibling would persist.
- `submit_orders.py`'s two paper-submission control-state loads now call `ensure_strategy_control_state`, keeping empty-DB execution gating (row created ACTIVE) unchanged.
- `tests/test_read_path_purity.py` (9 tests) proves at runtime -- via an `Engine.before_cursor_execute` + `Session.before_flush` spy -- that `export_backtest_report.py`, `report_strategy_analytics.py`, `operator_status.py`, and `GET /api/v1/analytics/strategies/trend_following_daily` all perform zero INSERT/UPDATE/DELETE and zero non-empty flushes, and that the spy itself is proven to fire on a real write (positive control).
- Post-completion advisor review caught and fixed a real regression this plan introduced (see Deviations): a metric-persistence failure at backtest completion could leave a run stuck RUNNING instead of FAILED.

## Task Commits

Each task was committed atomically:

1. **Task 1: Pure backtest report builder + persist step at backtest completion** - `3369cdb` (feat)
2. **Task 2: Pure strategy-control-state read + ensure variant for mutating callers + zero-write proof** - `96fe50e` (feat)
3. **Post-completion fix (advisor review): keep backtest SUCCEEDED update inside the failure handler** - `43b13e3` (fix)

**Plan metadata:** `500de94` (docs: complete plan), `d512d4c` (docs: append self-check/process note)

## Files Created/Modified
- `src/trading_platform/services/backtest_reporting.py` - `materialize_backtest_report` renamed to pure `build_backtest_report`; new `persist_backtest_metrics(session, strategy_run)` write helper
- `src/trading_platform/services/backtesting.py` - `_update_backtest_run` calls `persist_backtest_metrics` once, only on SUCCEEDED, same transaction; `run_backtest`'s SUCCEEDED update moved inside the try/except (post-completion fix)
- `src/trading_platform/services/analytics.py` - imports/calls `build_backtest_report` instead of the removed name
- `src/trading_platform/services/operator_controls.py` - `get_strategy_state` is now a pure select with registry default; added `ensure_strategy_state` (get-or-create) and module-level `ensure_strategy_control_state`; `StrategyControlState.updated_at` is now `str | None`
- `src/trading_platform/services/operator_status.py` - markdown renderer shows `-` for a `None` updated_at
- `src/trading_platform/services/execution/submit_orders.py` - both control-state call sites switched to `ensure_strategy_control_state`; `load_strategy_control_state` import removed
- `tests/test_backtest_reporting.py` - renamed/extended test proving exactly-once metric persistence + report purity; `test_failed_backtest_persists_no_metrics`; `test_metric_persistence_failure_lands_run_on_failed_status` (post-completion regression guard)
- `tests/test_read_path_purity.py` (new) - `write_spy` context manager, `_row_counts` helper, 9 tests covering the 3 exempt scripts, the analytics GET route, the empty-DB registry-default read, the read/ensure-status parity check, a write_spy positive control, and static no-write-call source checks
- `.planning/phases/20-complete-operation-migration-safety-controls/deferred-items.md` (new) - logs the analytics.py strategy-status-default disagreement found during review (see Deviations)

## Decisions Made
See `key-decisions` in frontmatter above. Most notably: ORCH-08 stays Pending in REQUIREMENTS.md -- this plan is a prerequisite (making the D-29 exemption reasons literally true) for the larger boundary-test/script-removal requirement that a later plan (per the plan's own objective text, Plan 23) closes.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Metric-persistence failure could strand a backtest run in RUNNING**
- **Found during:** Advisor review after both tasks were committed and the plan's own tests were green
- **Issue:** `run_backtest`'s final `_update_backtest_run(..., SUCCEEDED, ...)` call was a bare trailing statement outside the function's `try/except`. Task 1 made that same SUCCEEDED update also run `persist_backtest_metrics` in the same transaction (Decimal/`math.pow` computation + a flush). If that computation or flush raised -- an `OverflowError`, an `InvalidOperation` from `Decimal.quantize`, or a transient DB error -- the exception propagated straight out of `run_backtest` with no handler, so the run was never marked FAILED. It stayed RUNNING forever, linked to a Job that had already terminated. Before this plan's Task 1 change, the SUCCEEDED update could not fail this way, so this is a regression this plan introduced, not a pre-existing bug.
- **Fix:** Moved the SUCCEEDED `_update_backtest_run` call inside the existing `try` block so the existing `except Exception` handler (which already marks the run FAILED with zero `backtest_metrics` rows on an `_execute_backtest_run` failure) now also covers a metric-persistence failure.
- **Files modified:** `src/trading_platform/services/backtesting.py`, `tests/test_backtest_reporting.py`
- **Verification:** Added `test_metric_persistence_failure_lands_run_on_failed_status`, which monkeypatches `persist_backtest_metrics` to raise and asserts the run lands on FAILED with zero metric rows. Confirmed the test fails against the pre-fix code (via `git show HEAD:...` restored temporarily, not `git stash`) before confirming it passes against the fix.
- **Committed in:** `43b13e3`

**2. [Rule 2 - Missing Critical] write_spy instrument was unverified**
- **Found during:** Advisor review
- **Issue:** Every purity test in `tests/test_read_path_purity.py` asserted `spy.is_empty`. That result looks identical whether the code under test is genuinely pure or the `write_spy` listener is attached to the wrong target, never dispatched, or otherwise silently inert -- so the plan's central zero-write claim rested on an unverified instrument.
- **Fix:** Added `test_write_spy_detects_writes`, a positive control that calls `ensure_strategy_control_state` on an empty DB (a known INSERT + flush) inside a `write_spy()` block and asserts the spy actually captured an INSERT statement and a non-empty flush.
- **Files modified:** `tests/test_read_path_purity.py`
- **Verification:** Test passes; a quick manual check (removing the assertion body) confirmed the spy list is non-empty on a real write, i.e. the control is meaningful.
- **Committed in:** `43b13e3`

---

**Total deviations:** 2 auto-fixed (1 Rule 1 bug, 1 Rule 2 missing-critical test coverage)
**Impact on plan:** Both were necessary for correctness (Item 1 is a real production bug; Item 2 hardens the plan's own zero-write proof against a false-positive spy). No scope creep -- both stayed inside this plan's already-modified files.

### Non-blocking findings logged, not fixed

Advisor review also raised two items that were investigated and intentionally left unfixed:
- **`updated_at` nullability reaching HTTP/console consumers:** grepped `src/trading_platform/api` and `console/src` for `StrategyControlState`/`get_strategy_state`/`build_operator_status_report` -- no route or console component currently serves this field over HTTP; it is only consumed by the `operator_status.py` CLI script (already handles `None` via the `-` renderer fix in Task 2). No code change needed.
- **`analytics.py`'s no-row strategy-status default can disagree with the new pure read's default under `enabled: false` config:** logged in `.planning/phases/20-complete-operation-migration-safety-controls/deferred-items.md` rather than fixed, since `analytics.py`'s behavior was explicitly out of this plan's scope and today's config (`enabled: true`) makes the disagreement unobservable.

### Small non-behavioral adjustments

Two docstring wording adjustments were made purely to satisfy the plan's own literal `test_read_functions_contain_no_write_calls` substring check (the initial docstrings, which documented what the functions do NOT do, happened to contain the literal banned substrings "session.add"/"commit"/"ensure_strategy_record" inside their own prose describing the absence of those calls) -- documentation-only, no behavior impact.

`write_spy` in `tests/test_read_path_purity.py` is implemented as a `@contextmanager` function (used as `with write_spy() as spy:`), not a pytest `@pytest.fixture`, so it can be attached explicitly after seeding/TestClient-startup as the plan's action text requires ("Attach it only AFTER seeding and after any TestClient startup"); a pytest fixture would auto-attach at test entry, before seeding. This is a literal-vs-intent resolution in favor of the plan's own stated requirement, not a scope change.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `build_backtest_report`, `persist_backtest_metrics`, `load_strategy_control_state`, and `ensure_strategy_control_state` are all stable exports later plans (including the Plan 23 D-29 boundary test) can rely on.
- Full suite: 614 passed (up from the 603 baseline: `test_backtest_reporting.py` net +2 tests -- one renamed in place, `test_failed_backtest_persists_no_metrics` and `test_metric_persistence_failure_lands_run_on_failed_status` added; `test_read_path_purity.py` is new with 9 tests; 603 + 2 + 9 = 614).
- `ruff check`, `ruff format`, and the scoped `mypy` gate (execution/reconciliation/config) all pass; pre-commit hooks passed clean on all three commits.
- No blockers for subsequent Phase 20 plans.

## Process Note

The `docs(20-02)` tracking commit (`500de94`) was created via `gsd-sdk query commit`, which does not expose a trailer argument, so it landed without the required `Co-Authored-By` trailer -- same tool limitation documented in the 20-01/19-08 process notes. Per that precedent, this is disclosed here rather than corrected via `git commit --amend` (avoiding a history rewrite); no work was lost.

## Self-Check: PASSED

- FOUND: `src/trading_platform/services/backtest_reporting.py` (build_backtest_report, persist_backtest_metrics)
- FOUND: `tests/test_read_path_purity.py`
- FOUND: `.planning/phases/20-complete-operation-migration-safety-controls/deferred-items.md`
- FOUND commit `3369cdb` in `git log --oneline --all`
- FOUND commit `96fe50e` in `git log --oneline --all`
- FOUND commit `43b13e3` in `git log --oneline --all`

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*
