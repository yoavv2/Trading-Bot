---
phase: 20-complete-operation-migration-safety-controls
reviewed: 2026-09-28T00:00:00Z
depth: standard
files_reviewed: 29
files_reviewed_list:
  - Makefile
  - README.md
  - src/trading_platform/api/app.py
  - src/trading_platform/api/dependencies.py
  - src/trading_platform/api/routes/controls.py
  - src/trading_platform/api/routes/jobs.py
  - src/trading_platform/db/models/job.py
  - src/trading_platform/db/models/market_data_ingestion_run.py
  - src/trading_platform/db/models/strategy_run.py
  - src/trading_platform/services/analytics.py
  - src/trading_platform/services/backtest_reporting.py
  - src/trading_platform/services/backtesting.py
  - src/trading_platform/services/bootstrap.py
  - src/trading_platform/services/calendar.py
  - src/trading_platform/services/data.py
  - src/trading_platform/services/execution/_paper_common.py
  - src/trading_platform/services/execution/submit_orders.py
  - src/trading_platform/services/ingestion.py
  - src/trading_platform/services/job_reads.py
  - src/trading_platform/services/operator_controls.py
  - src/trading_platform/services/operator_status.py
  - src/trading_platform/services/reconciliation/report.py
  - src/trading_platform/services/risk.py
  - src/trading_platform/services/symbol_metadata_sync.py
  - src/trading_platform/worker/__main__.py
  - src/trading_platform/worker/commands/__init__.py
  - src/trading_platform/worker/commands/backtest.py
  - src/trading_platform/worker/commands/operator.py
  - src/trading_platform/worker/parser.py
findings:
  critical: 1
  warning: 6
  info: 7
  total: 14
status: issues_found
---

# Phase 20 (Part B: services, HTTP API, DB models, worker CLI): Code Review Report

**Reviewed:** 2026-09-28
**Depth:** standard
**Files Reviewed:** 29
**Status:** issues_found

## Summary

Reviewed the diff `961cdab..HEAD` for the 29 files, with full-file context and call-site
checks in `orchestration/job_mutations.py`, migration `0021`, and `jobs/handlers/payload_fields.py`.
Not reviewed (Parts A and C): jobs/handlers/orchestration internals and the console.

Verified clean (no finding):
- **HTTP error mapping.** The retry route maps every typed error `JobOrchestrationService.retry`
  can raise. `_require_job` raises `JobMutationNotFoundError`, so retrying an unknown Job id is a
  404, not a 500. `JobNotCancellableRunningError` is independent of
  `JobTerminalConflictError`, so the `except` order in `cancel_job` is safe.
- **Mutation guard.** All three new mutating routes (`PUT kill-switch`, `PUT strategies/{id}`,
  `POST retry`) carry `require_mutations_enabled` at the decorator level. The controls routes
  parse the body manually, so the 403 fires before any 422.
- **Reason validation.** `reason` is `Text` in both `SystemControl` and `ExecutionEvent`, so a
  500-character reason cannot overflow a column.
- **Migration parity.** Migration `0021` matches the model changes (named UNIQUE on
  `retry_of_job_id`, non-unique `ix_strategy_runs_job_id`, ingestion `job_id` FK + index,
  enum `ADD VALUE`).
- **Backtest transaction boundary.** `persist_backtest_metrics` runs inside the SUCCEEDED
  `session_scope`. A failure rolls back and the `except` in `run_backtest` still lands FAILED
  with no metric row.
- **Read-path purity.** `build_backtest_report` and `get_strategy_state` perform no writes.
  `tests/test_read_path_purity.py` proves this with a SQL-level `INSERT`/`UPDATE`/`DELETE` spy
  plus a `before_flush` spy. No dangling `materialize_backtest_report` imports remain.
- **`job_id` threading.** It reaches all five run-creating call sites (risk, reconciliation,
  paper execution including the blocked path, backtest, ingestion).
- **Trip-only worker command.** `kill-switch-trip` is trip-only, boots at BACKTEST level (which
  needs no broker secrets), and never reads `mutations_enabled`. `parser.py` and `DISPATCH` are
  in exact agreement (five commands).

One critical finding: the ingestion run record is not durable on the failure and crash paths.
Six warnings concern control-route semantics, concurrency, and error typing.

## Critical Issues

### CR-B-01: `ingest_daily_bars` runs the whole ingestion in one transaction, so the run row (and its `job_id`) is invisible while running and is rolled back on any failure or crash

**File:** `src/trading_platform/services/ingestion.py:216-283`
**Issue:** `_start_run` only `flush()`es. The run row, every bar upsert and `_finish_run(...)`
all live inside a single `with session_scope(db_settings)`.
- **Failure path.** The outer `except Exception` (line 267) calls `_finish_run(error_message=...)`
  and re-raises (line 275). `session_scope` then rolls back the transaction, which discards the
  `failed` run row, its `job_id`, and every bar upserted before the error. A per-symbol DB error
  (caught by the inner `except`, line 253) leaves the session in a failed-transaction state.
  Every subsequent symbol then fails, `_finish_run` raises `PendingRollbackError`, and the whole
  ingest is rolled back.
- **Crash path.** A worker crash, lease expiry, or cancellation-timeout kill also rolls the
  transaction back, because nothing was ever committed.
- **Running state.** During a normal (possibly minutes-long) ingest, `resources[]` is empty,
  because the row is uncommitted and invisible to `JobReadService`.

The result is a FAILED or `lease_expired` `ingest-bars` Job with `resources[] == []` and no
`market_data_ingestion_runs` row. That contradicts D-08 (one `market_data_ingestion_run`
resource), D-09 ("every run this Job created appears in resources[]") and P19 D-13 ("a stuck,
timed-out, or cancelled Job never hides its run"). It also loses the failure audit record. The
strategy-run services (risk, reconciliation, backtest, paper) all commit the run row in its own
transaction first; ingestion is the odd one out.
**Fix:** Commit the run in its own short transaction, then do the work in separate transactions,
and finalize the run in a final transaction that also runs on failure:
```python
with session_scope(db_settings) as session:
    run = _start_run(session, ..., job_id=job_id)   # commits on exit
    run_id = run.id

try:
    with PolygonClient(settings.polygon) as client:
        for ticker in symbols:
            try:
                with session_scope(db_settings) as sym_session:   # per-symbol tx
                    symbol = upsert_symbol(sym_session, ticker)
                    bars = client.fetch_daily_bars(DailyBarRequest(...))
                    total_bars += upsert_daily_bars(sym_session, bars, symbol.id)
            except Exception:
                failed_symbols.append(ticker)
    finalize(run_id, error_message=None)
except Exception as exc:
    finalize(run_id, error_message=str(exc))   # own session_scope; committed
    raise
```
where `finalize` reloads the run by id inside its own `session_scope` and calls `_finish_run`.
Add a test that a raised exception mid-ingest leaves a `failed` run with `job_id` set and that
job detail lists it under `resources[]`.

## Warnings

### WR-B-01: An idempotent no-op kill-switch PUT overwrites the "last changed" provenance

**File:** `src/trading_platform/services/operator_controls.py:418-424`
**Issue:** `_set_kill_switch_state` assigns `control.state`, `last_changed_at`,
`last_change_actor`, `last_change_reason` and `last_change_run_id` unconditionally, even when
`changed` is `False`. D-10 only requires the unchanged audit rows (`OPERATOR_CONTROL` run +
`ExecutionEvent`). It does not require moving the `SystemControl` row's change provenance.
After `PUT {state: "tripped"}` on an already-tripped switch (a double-click, a retried request,
or the break-glass CLI run after the UI), `GET /system/kill-switch` reports the reaffirmation's
actor, reason, timestamp and run id as the "last change". The banner's "last changed" is then
wrong, and the original trip reason is lost from the state row. The strategy path
(`_set_strategy_status`) correctly writes the status only `if changed`, so the two paths are
inconsistent.
**Fix:** Only mutate the state row when the state actually changes; always write the audit run and event:
```python
if changed:
    control.state = target_state
    control.last_changed_at = changed_at
    control.last_change_actor = actor
    control.last_change_reason = reason
    control.last_change_run_id = strategy_run.id
    session.flush()
session.refresh(control)
```

### WR-B-02: Safety-control mutators take no row lock, and concurrent first-use `ensure_strategy_record` can 500

**File:** `src/trading_platform/services/operator_controls.py:242-247, 390-395`
**Issue:**
- `_load_global_kill_switch` and `ensure_strategy_record` read the target row without
  `FOR UPDATE`. Two concurrent PUTs to the same target (UI double-submit, or UI plus break-glass)
  both read `previous_state != target`, so both audit rows and both responses report
  `changed: true`. "Idempotent by target state" is then not reflected in `changed` or
  `previous_state`.
- Opposing concurrent requests (trip vs reset) each compute `previous_state` from a stale read.
  The audit trail can record a transition that never occurred.
- On a fresh DB (no `strategies` row yet), two concurrent `ensure_strategy_record` calls both
  `INSERT`, and one fails with `IntegrityError` on the unique `strategy_id`. The route does not
  catch it, so that request returns an unhandled 500.

**Fix:** Lock the control row for the read-modify-write, and make the strategy get-or-create race-safe:
```python
control = session.execute(
    select(SystemControl)
    .where(SystemControl.name == GLOBAL_KILL_SWITCH_NAME)
    .with_for_update()
).scalar_one_or_none()
```
For strategies, use `pg_insert(...).on_conflict_do_nothing()` and then select `FOR UPDATE` (or
lock the `Strategy` row after `ensure_strategy_record` and re-read `status`).

### WR-B-03: The controls API collapses `ARCHIVED` into "disabled", and `PUT enabled` silently un-archives a strategy

**File:** `src/trading_platform/api/routes/controls.py:121, 151`; `src/trading_platform/services/operator_controls.py:229-282`
**Issue:** `StrategyStatus` is a three-value enum (`ACTIVE`, `DISABLED`, `ARCHIVED`). Both the
PUT and GET responses map anything not `"active"` to `"disabled"`, so an archived strategy is
reported as merely disabled. `_set_strategy_status(target=ACTIVE)` overwrites `ARCHIVED` with
`ACTIVE` and returns `changed: true`. A single unguarded HTTP call therefore resurrects an
archived strategy for paper execution. D-10 defines the API status vocabulary as
`enabled | disabled` only, so the API cannot even represent the true state.
**Fix:** In `_set_strategy_status`, refuse to transition out of `ARCHIVED` (raise a typed
`StrategyArchivedError`) and map it in the route to `409 strategy_archived`. Alternatively expose
`"archived"` in the GET response and reject `enabled`/`disabled` PUTs for archived strategies.

### WR-B-04: Unhandled exceptions from the control routes return non-JSON 500s, contradicting the route's own console contract

**File:** `src/trading_platform/api/routes/controls.py:35-49, 76-99`; `src/trading_platform/services/operator_controls.py:387, 583`
**Issue:** `_read_body`'s docstring says the console requires `detail` to always be a JSON
object, yet several reachable inputs and states escape as bare `Internal Server Error` text:
1. A `reason` containing `\x00`. `_validate_reason` accepts it, and PostgreSQL rejects NUL in
   `text` (psycopg `ValueError`/`DataError`), so this is a 500 after the request has been accepted.
   It should be a typed 422 with zero writes (D-11). The break-glass CLI has the same gap.
2. Deeply nested JSON (`"[" * 100000`) raises `RecursionError` in `json.loads`, which is not
   caught (only `JSONDecodeError`/`UnicodeDecodeError` are).
3. A missing global kill-switch row raises `LookupError` (`operator_controls.py:583`).
4. `IntegrityError` (see WR-B-02) and transient `OperationalError` are also unmapped.

For a safety-control endpoint the operator gets an opaque failure with no code.
**Fix:**
```python
if "\x00" in trimmed: raise _error(422, "invalid_control_reason")
# in _read_body:
except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc: ...
# in the two PUT handlers, around the run_in_threadpool call:
except LookupError as exc:
    raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "control_state_unavailable") from exc
except SQLAlchemyError as exc:
    raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "control_write_failed") from exc
```

### WR-B-05: Kill-switch trip (HTTP and break-glass) depends on `trend_following_daily` being in the strategy registry

**File:** `src/trading_platform/services/operator_controls.py:32, 387-390`
**Issue:** `_set_kill_switch_state` resolves the hard-coded `"trend_following_daily"` from the
registry and `ensure_strategy_record`s it, purely to obtain a `strategy_runs.strategy_id` FK for
the audit row. If that strategy's YAML is removed, renamed or fails to load, `registry.resolve`
raises `UnknownStrategyError`. The kill switch can then be neither tripped over HTTP (500) nor
via the break-glass CLI (traceback). D-15 exists precisely so the switch can always be tripped,
and that only holds while an unrelated strategy config is healthy.
**Fix:** Fall back to any registered strategy (`next(iter(registry.list()))`) if the default is
unknown, or make the audit `strategy_id` FK nullable for global-scope control runs. At minimum,
map `UnknownStrategyError` in both entrypoints to a clear message and add a test.

### WR-B-06: `get_strategy_state` and the analytics summary disagree on the default status for a strategy with no DB row

**File:** `src/trading_platform/services/operator_controls.py:160-181`; `src/trading_platform/services/analytics.py:99-106`
**Issue:** With no `strategies` row, the D-31 pure read returns `ACTIVE` unconditionally (its
docstring says this mirrors what `ensure` would persist). `StrategyAnalyticsService` returns
`ACTIVE if metadata.enabled else DISABLED` for the same situation. For a strategy configured
`enabled: false`, `operator-status` and `GET /controls/strategies/{id}` say enabled while the
analytics API says disabled, and the first mutating call materializes `ACTIVE` anyway. This is a
latent divergence on an execution-gating flag.
**Fix:** Decide one default. Either honour `metadata.enabled` in both `get_strategy_state` and
`ensure_strategy_record`'s create path, or use `ACTIVE` in analytics. Then add a test for a
config-disabled strategy with no DB row.

## Info

### IN-B-01: `render_operator_control_report` is now dead code

**File:** `src/trading_platform/services/operator_controls.py:524`
**Issue:** Its only caller (`worker/commands/operator.py::run_operator_control_command`) was deleted
in this phase. A grep across `src/`, `scripts/` and `tests/` finds only the definition.
**Fix:** Delete it (and `OperatorControlReport.to_dict` if nothing else uses it).

### IN-B-02: Stale and incorrect README content

**File:** `README.md:3, 19, 165`
**Issue:**
- Line 3 has a typo: "and and the initial".
- Line 19 still says Compose has "a placeholder worker". The `serve` placeholder was deleted, and
  the compose worker now runs `run-jobs`.
- Line 165 cites `docs/gsd/decisions/D-15`, but no `docs/` directory exists.
**Fix:** Fix the typo, describe the worker as the Job runner, and point to
`.planning/phases/20-.../20-CONTEXT.md` (D-15) or drop the reference.

### IN-B-03: Docstrings cite deleted modules

**File:** `src/trading_platform/services/calendar.py:219`; `src/trading_platform/services/symbol_metadata_sync.py:12`
**Issue:** Both reference `worker/commands/ingest.py::run_sync_sessions` / `run_sync_metadata`,
which no longer exist. The context is only useful as history.
**Fix:** Reword to "moved from the retired worker command", without a path.

### IN-B-04: Unused Makefile variables

**File:** `Makefile:6-8`
**Issue:** `FROM_DATE`, `TO_DATE` and `SYMBOLS` were consumed only by the deleted targets.
**Fix:** Remove them.

### IN-B-05: Symbol-metadata sync swallows the cause and mishandles `active: null`

**File:** `src/trading_platform/services/symbol_metadata_sync.py:94, 175-181`
**Issue:**
- `overview.get("active", True)` returns `None` when Polygon sends an explicit `"active": null`.
  `symbols.active` is `NOT NULL`, so the upsert fails and the ticker is counted as failed.
- The per-ticker `except Exception` also swallows `PolygonAuthError`. A bad or missing API key
  therefore retries every symbol. The Job's `failure_message` ("failed for: A, B, ...") never
  says why; the cause is only in logs. The retired script behaved the same way, so this is not a
  regression, but it is now operator-visible.
**Fix:** Use `overview.get("active") is not False` (or `bool(overview.get("active", True))`).
Re-raise `PolygonAuthError` (or record the first error string in `MetadataSyncResult`).

### IN-B-06: The control routes build a second strategy registry and reformat inconsistently

**File:** `src/trading_platform/api/dependencies.py:85-86`; `src/trading_platform/api/routes/jobs.py:202`; `src/trading_platform/services/backtest_reporting.py:90`
**Issue:**
- `get_operator_control_service` constructs `OperatorControlService(settings=...)` without a
  registry. The route resolves the strategy with the request's registry (built from settings),
  and the service silently builds another via `build_default_registry`. That is two YAML loads
  per request, and the 404 pre-check and the service could diverge if the registry ever becomes
  injectable.
- `jobs.py:202` is 113 characters (project `line-length = 100`).
- `persist_backtest_metrics(session, ...)` has an untyped `session` parameter.

**Fix:** Pass `registry=get_strategy_registry(request)` into the service. Wrap the long line.
Annotate `session: Session`.

### IN-B-07: Ingestion resources report empty `links`

**File:** `src/trading_platform/services/job_reads.py:587-595`
**Issue:** `market_data_ingestion_run` resources are emitted with `"links": {}`, unlike
`strategy_run` (`links.self`). Consumers cannot navigate to the run, and generic list/detail
components must special-case the empty map. There is also no `/api/v1` read route for ingestion
runs to link to.
**Fix:** Either add a read route and a `self` link, or document `links: {}` as intentional
in the resource contract.

---

_Reviewed: 2026-09-28_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
