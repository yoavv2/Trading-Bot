---
phase: 20-complete-operation-migration-safety-controls
fixed_at: 2026-09-28T00:00:00Z
review_path: .planning/phases/20-complete-operation-migration-safety-controls/20-REVIEW-part-B-services-api.md
iteration: 1
findings_in_scope: 7
fixed: 7
skipped: 0
status: all_fixed
---

# Phase 20 (Part B: services, HTTP API, DB models, worker CLI): Code Review Fix Report

**Fixed at:** 2026-09-28
**Source review:** .planning/phases/20-complete-operation-migration-safety-controls/20-REVIEW-part-B-services-api.md
**Iteration:** 1

**Summary:**
- Findings in scope: 7 (CR-B-01, WR-B-01..06; Info findings out of scope)
- Fixed: 7
- Skipped: 0

Every fix has a regression test that was confirmed to fail against the pre-fix source and pass
after. Fixes were applied on `main` (per the caller's instruction), not in a worktree.

## Fixed Issues

### CR-B-01: `ingest_daily_bars` rolled back its own run row on failure

**Files modified:** `src/trading_platform/services/ingestion.py`, `tests/test_phase20_service_job_links.py`
**Commit:** b40e8ae
**Status:** fixed: requires human verification (transaction-boundary change)
**Applied fix:** The run row (with `job_id`) is now committed in its own transaction before any
work, so it is visible as `running` and survives a failure/crash (D-08/D-09, P19 D-13). Each symbol
runs in its own transaction (a per-symbol DB error no longer poisons later symbols or the final
bookkeeping; failed-symbol/`partial` semantics are otherwise unchanged). Finalization
(`succeeded`/`partial`/`failed`) happens in a separate transaction via `_finalize_run`, which also runs on
failure (the failure path never lets a bookkeeping error mask the original exception).
Tests: a failing ingest leaves a `failed` run linked to the `job_id` with `error_message` set; the
run is committed/visible (`running`, `job_id` set) while the first Polygon fetch is in flight.
`tests/test_market_data_ingestion.py`, `tests/test_ingest_bars_job_type.py` and
`tests/test_market_data_job_types_e2e.py` still pass.
**Known residual:** a hard worker crash mid-ingest now leaves a visible `running` row (which is the
intended P19 D-13 behavior), but nothing reclaims stale ingestion runs. Not blocking.

### WR-B-01: idempotent no-op kill-switch PUT overwrote "last changed" provenance

**Files modified:** `src/trading_platform/services/operator_controls.py`, `tests/test_control_routes.py`
**Commit:** 0a795e8
**Applied fix:** `_set_kill_switch_state` now mutates `SystemControl` (`state`, `last_changed_at`,
`last_change_actor`, `last_change_reason`, `last_change_run_id`) only when the state changes. The
audit `OPERATOR_CONTROL` run and `ExecutionEvent` are still always written (D-10). No existing test
pinned the old behavior. Test: trip, then trip again with another reason; the state row and
`GET /system/kill-switch` still show the first reason/run id, and exactly one more audit run + event exist.

### WR-B-02: no row lock in control mutators; first-use ensure race could 500

**Files modified:** `src/trading_platform/services/operator_controls.py`, `tests/test_control_routes.py`
**Commit:** 25fa2b7
**Status:** fixed: requires human verification (concurrency)
**Applied fix:** The kill-switch mutator loads the `SystemControl` row `FOR UPDATE`
(`_load_global_kill_switch(for_update=True)`; the read path stays unlocked). The strategy mutator
re-reads the strategy row with `refresh(..., with_for_update=True)` so `previous_status`/`changed` reflect
the committed state after any concurrent writer. The first-use get-or-create runs in a SAVEPOINT and, on
`IntegrityError` from the unique `strategy_id`, re-selects the winner's row instead of surfacing a 500.
`bootstrap.ensure_strategy_record` is unchanged (no effect on risk/backtest/reconciliation/paper callers).
Tests (deterministic, using an uncommitted holder session): mutators block on the locked row and
report `changed: False` afterwards; a loser of the first-use INSERT race does not raise.

### WR-B-03: `PUT enabled` silently un-archived a strategy

**Files modified:** `src/trading_platform/services/operator_controls.py`, `src/trading_platform/api/routes/controls.py`, `tests/test_control_routes.py`
**Commit:** 0a2d2f7
**Applied fix:** New `StrategyArchivedError`, raised for both enable and disable when the (locked)
previous status is `ARCHIVED`, before any audit row is added (zero writes). The route maps it to
`409 {"code": "strategy_archived", "strategy_id": ...}`. This is the conservative option; the GET
`enabled|disabled` vocabulary (D-10) is deliberately unchanged, so an archived strategy is still
displayed as `disabled` (not `archived`). The console has no copy for `strategy_archived`; it uses its generic fallback.
Product follow-up if wanted: expose `"archived"` on GET.

### WR-B-04: bare-text 500s from the control routes

**Files modified:** `src/trading_platform/api/routes/controls.py`, `src/trading_platform/api/app.py`, `src/trading_platform/services/operator_controls.py`, `src/trading_platform/worker/commands/operator.py`, `tests/test_control_routes.py`, `tests/test_kill_switch_trip_cli.py`
**Commits:** 4da9da5, ef5c42c (the second keeps `controls.py` free of `sqlalchemy` imports, which
`test_control_route_adapter_imports_only_allowed_layers` enforces; the first commit alone broke that test)
**Applied fix:**
- NUL in `reason` is now `422 invalid_control_reason` with zero writes (routes, and the break-glass CLI exits 2).
- `RecursionError` from deeply nested JSON is `422 invalid_control_request`.
- Missing kill-switch row / unresolvable audit anchor: `ControlStateUnavailableError` (still a `LookupError`)
  maps to `503 control_state_unavailable`.
- Database errors: the service wraps `SQLAlchemyError` as `ControlWriteError` (mutators, `503 control_write_failed`)
  or `ControlStateUnavailableError` (strategy state read, `503 control_state_unavailable`).
- An app-level `Exception` handler returns `{"detail": {"code": "internal_error"}}` (500) so no control
  or job mutation route ever emits a bare-text body. Existing codes are unchanged.
**Not changed / out of scope:** the job read routes (`GET /jobs/{id}`, `/progress`, `/logs`, `/events`) still
return string-detail 404s; `tests/test_job_api.py` pins that shape and they are not console mutation routes.
FastAPI's default array-shaped 422 for job request-body validation is also untouched. New codes
(`control_state_unavailable`, `control_write_failed`, `internal_error`, `strategy_archived`) have no console
copy and fall through `controlErrorMessage`'s generic handling; console copy was not edited (out of Part B).

### WR-B-05: kill-switch trip depended on `trend_following_daily` being in the registry

**Files modified:** `src/trading_platform/services/operator_controls.py`, `src/trading_platform/worker/commands/operator.py`, `tests/test_operator_controls.py`, `tests/test_kill_switch_trip_cli.py`
**Commit:** 8b5e31f
**Status:** fixed: requires human verification
**Applied fix:** The audit-run strategy anchor now uses the already-persisted `strategies` row directly
without consulting the registry; the registry is only needed to create the row on a brand-new database. If
neither exists, a typed `ControlStateUnavailableError` is raised (HTTP `503 control_state_unavailable`;
break-glass CLI prints a clean stderr message and exits 1). No schema change (the FK was not made nullable).
Residual: on a fresh database with an unresolvable registry the trip still fails (typed, not a traceback), and
if `build_default_registry` itself raises on a broken config before any strategy row exists the failure is
also reported as `ControlStateUnavailableError`.

### WR-B-06: default strategy status disagreed between control read and analytics

**Files modified:** `src/trading_platform/services/analytics.py`, `tests/test_operator_controls.py`
**Commit:** 8e6e6a2
**Status:** fixed: requires human verification (product-default choice)
**Applied fix:** With no `strategies` row, `StrategyAnalyticsService.summarize_strategy` now reports `active`,
matching `OperatorControlService.get_strategy_state` and what `ensure_strategy_record` persists, so every
surface reports the value that actually gates execution. Execution gating is unchanged. Test: a strategy with
`enabled: false` config and no DB row yields the same status from the pure control read, the analytics summary,
and the mutating get-or-create, with no rows written by the reads.
**Alternative the operator may prefer:** honour `metadata.enabled` everywhere including
`ensure_strategy_record`'s create path (fail-closed for config-disabled strategies). That changes gating for
every caller of `ensure_strategy_record`, so it was not applied here.

---

_Fixed: 2026-09-28_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
