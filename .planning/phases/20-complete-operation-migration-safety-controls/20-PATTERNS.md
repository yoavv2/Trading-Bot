# Phase 20: Complete Operation Migration & Safety Controls - Pattern Map

**Mapped:** 2026-09-27
**Files analyzed:** ~55 (7 job types × 4 files, retry, controls, boundary, deletions, console)
**Analogs found:** all backend/console additions have a strong existing analog; two structural additions (retry lineage FK, `domain_conflict` enum migration) have no direct prior-phase analog and are flagged under "No Analog Found."

This phase is a mechanical repeat of the Phase 19 `backtest` Job-type vertical slice, seven times, plus three genuinely new mechanisms (retry, domain-conflict signaling, queued-only cancellation) that are themselves near-copies of adjacent existing code, plus the CTRL-01/02 HTTP routes over an already-complete `OperatorControlService`. Read the "Shared Patterns" and "Planner-Facing Constraints" sections before assigning files to plans — several files listed as "new" have a load-bearing existing-code caveat that changes what the analog looks like once copied.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `jobs/handlers/risk_evaluation.py` (+`_submission.py`) | handler + submission-spec | request-response → CRUD | `jobs/handlers/backtest.py` + `backtest_submission.py` | exact |
| `jobs/handlers/paper_session.py` (+`_submission.py`) | handler + submission-spec | request-response → CRUD (queued-only cancel) | `jobs/handlers/backtest.py` + `backtest_submission.py` | role-match (no `raise_if_cancelled`, new cancellation_mode) |
| `jobs/handlers/reconciliation.py` (+`_submission.py`) | handler + submission-spec | request-response → CRUD (queued-only cancel) | `jobs/handlers/backtest.py` + `backtest_submission.py` | role-match |
| `jobs/handlers/ingest_bars.py` (+`_submission.py`) | handler + submission-spec | request-response → batch | `jobs/handlers/backtest.py` + `backtest_submission.py` | role-match (multi-value `symbols` field) |
| `jobs/handlers/sync_symbol_metadata.py` (+`_submission.py`) | handler + submission-spec | request-response → batch | `jobs/handlers/backtest.py` + `backtest_submission.py` | role-match (no run record; needs new `services/` extraction first) |
| `jobs/handlers/sync_market_sessions.py` (+`_submission.py`) | handler + submission-spec | request-response → batch | `jobs/handlers/backtest.py` + `backtest_submission.py` | role-match (no run record; handler needs its own `session_scope`, see constraints) |
| `jobs/handlers/broker_order_sync.py` (+`_submission.py`) | handler + submission-spec | request-response (queued-only cancel) | `jobs/handlers/backtest.py` + `backtest_submission.py` | role-match (no run record) |
| `services/symbol_metadata_sync.py` (new) | service | batch / external I/O | `scripts/sync_symbol_metadata.py::_fetch_ticker_overview`/`_upsert_symbol_metadata` (deletion source) | exact — literal code move |
| `services/calendar.py::sync_market_sessions_job` or similar wrapper (new function) | service | CRUD | `worker/commands/ingest.py::run_sync_sessions` (session-opening wrapper) | exact — literal shape move |
| `jobs/registry.py` (edit: `JobCancellationMode.queued_only`, 7 `register()` calls) | config/registry | — | existing `STEP_BOUNDARY` member + `build_default_registry`'s one `register()` call | exact |
| `jobs/contracts.py` (edit: `JobDomainConflictError`) | contract/exception | event-driven | `JobCancelledError` in same file | exact |
| `jobs/runner.py` (edit: new outcome branch) | orchestration | event-driven | existing `except JobCancelledError` / `except Exception` branches | exact |
| `jobs/dependencies.py::submit_job` (edit: `retry_of_job_id` param) | service | CRUD | existing `depends_on` param plumbing in same function | exact |
| `orchestration/job_mutations.py::retry()` (new method) | orchestration | CRUD, idempotent | `JobOrchestrationService.submit()`/`.cancel()` in same file | exact |
| `orchestration/job_mutations.py::cancel()` (edit: queued-only rejection) | orchestration | CRUD | same method, existing terminal-conflict branch | exact |
| `services/job_reads.py` (edit: `resources[]` fix + retry lineage + `market_data_ingestion_run` kind) | service (read) | CRUD (read) | same file, `get_job_detail`'s existing `StrategyRun` query | exact (bugfix-in-place) |
| `db/models/job.py` (edit: `JobFailureReason.domain_conflict`, `retry_of_job_id` column) | model | — | same file's existing enum members / `blocking_job_id` FK pattern | exact |
| `db/models/strategy_run.py` (edit: drop UNIQUE on `job_id`) | model | — | same file | exact |
| `alembic/versions/00XX_phase20_...py` (new migration) | migration | schema | `alembic/versions/0020_phase19_job_operations.py` | exact for FK/UNIQUE ops; **no analog** for `ADD VALUE` + later downgrade-drop sequencing on a value added in a *different* migration — flag |
| `api/routes/jobs.py` (edit: `POST /{job_id}/retry`) | route (controller) | request-response | same file's `submit_job`/`cancel_job` routes | exact |
| `api/routes/controls.py` (new) | route (controller) | request-response, sync | `api/routes/strategies.py` (404 pattern) + `api/routes/jobs.py` (`_error()` helper, `require_mutations_enabled` guard) | exact composite |
| `api/dependencies.py` (edit: `get_operator_control_service` or similar) | dependency wiring | — | `get_job_orchestration_service` in same file | exact |
| `worker/commands/operator.py` (edit: add `kill-switch-trip`, delete `run_operator_control_command`) | CLI command | event-driven (break-glass) | same file's `_run_kill_switch_action`'s `trip-kill-switch` branch | exact |
| `worker/parser.py` (edit: drop `serve`, add `kill-switch-trip`) | CLI config | — | same file's `run_jobs_parser`/`operator_status_parser` subparser blocks | exact |
| `worker/commands/__init__.py` (edit: DISPATCH) | CLI dispatch | — | same file | exact |
| `worker/__main__.py` (edit: remove `serve` special case) | CLI entrypoint | — | same file, current `DISPATCH.get()` fallback branch | exact |
| `tests/test_orchestration_boundaries.py` (edit: 5-route allowlist, exact-set scans) | test | — | same file's existing `test_api_route_modules_have_only_the_two_job_mutation_decorators` / `test_runtime_application_has_exactly_two_mutating_job_routes` | exact |
| `tests/test_job_operations_e2e.py` additions (7 new test functions/classes) | test (E2E) | request-response | same file's `test_backtest_job_runs_through_production_path` | exact |
| `tests/test_<type>_job_type.py` (7 new unit-test files) | test (unit) | — | `tests/test_backtest_job_type.py` | exact |
| `tests/test_job_orchestration.py` additions (retry + cancel-race tests) | test | — | same file's `test_concurrent_same_key_submission_has_one_persisted_mutation` (race) + `JobTerminalConflictError` tests (cancel) | exact |
| `console/src/components/jobs/new/*JobForm.tsx` (7 new) | component (form) | request-response | `console/src/components/jobs/new/BacktestJobForm.tsx` | exact |
| `console/src/lib/jobTypeForms.ts` (edit: 7 map entries) | config/lookup-map | — | same file, existing `backtest` entry | exact |
| `console/src/lib/api.ts` (edit: `retryJob`, `tripKillSwitch`, `resetKillSwitch`, `enableStrategy`, `disableStrategy`, new `MUTATION_ERROR_COPY`/control-error-copy entries) | API client | request-response | same file's `submitJob`/`cancelJob`/`postJson` | exact for retry; needs a new no-idempotency-key PUT sibling for controls (see constraints) |
| `console/src/components/ControlConfirmDialog.tsx` (new) | component (dialog) | request-response | `console/src/components/jobs/CancelJobDialog.tsx` | exact |
| `console/src/app/controls/page.tsx` (new) | route (page) | — | `console/src/app/strategy/page.tsx` (thin page wrapping one panel) | role-match |
| `console/src/components/KillSwitchBanner.tsx` (edit: armed-state bar, `renderAction`-fed trigger) | component | request-response | same file (existing tripped-state trigger slot is new; pattern is `KillSwitchPanel`'s single-fetch composition) | exact |
| `console/src/components/status/KillSwitchPanel.tsx` (edit: `renderAction` slot) | component | — | same file | exact |
| `console/src/components/strategy/StrategyStatusBadge.tsx` (new, extracted) | component | — | `console/src/components/strategy/StrategyOverviewPanel.tsx`'s inline `<span>` badge | exact — literal extraction |
| `console/src/components/jobs/detail/JobHeaderPanel.tsx` (edit: Retry button + lineage rows + cancel-mode gating) | component | request-response | same file's existing Cancel-trigger block + `CancellationRow` pattern | exact |
| `console/src/components/jobs/types.ts` (edit: `payload`, `retry_blocked`, `retried_as_job_id`, `retry_of_job_id`, `"domain_conflict"`) | types | — | same file | exact |
| `console/src/app/layout.tsx` (edit: `Controls` nav link) | layout | — | same file's existing nav `<Link>` list | exact |
| `scripts/*.py` (10 deletions) | script | — | n/a — deletion, not creation | n/a |
| `worker/commands/{paper_execute,reconcile,risk_check,ingest,bootstrap}.py` (deletion, partial for `backtest.py`/`operator.py`) | CLI command | — | n/a — deletion | n/a |

---

## Pattern Assignments

### Template: any new Job type (risk-evaluation / paper-session / reconciliation / ingest-bars / sync-symbol-metadata / sync-market-sessions / broker-order-sync)

**Analog:** `src/trading_platform/jobs/handlers/backtest.py` + `src/trading_platform/jobs/handlers/backtest_submission.py` (full files read; reproduced patterns below). Unit-test analog: `tests/test_backtest_job_type.py` (functions listed by name below). E2E analog: `tests/test_job_operations_e2e.py::test_backtest_job_runs_through_production_path`.

**Handler shape** (`backtest.py:36-100`):
```python
class BacktestJobHandler:
    job_type = "backtest"
    required_execution_mode = ExecutionMode.BACKTEST   # BACKTEST | PAPER | LIVE, services/config/validation.py:51-60

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def run(self, context: JobContext) -> dict[str, Any]:
        context.report_progress(step=STEP_RESOLVING)
        strategy_id = context.payload["strategy_id"]
        context.raise_if_cancelled()          # pre-call checkpoint — STEP_BOUNDARY types ONLY, see below
        context.report_progress(step=STEP_RUNNING)
        context.log(level="info", event_code="backtest_run_started", message="...", context={...})
        report = run_backtest(strategy_id, ..., trigger_source="job", job_id=context.job_id, settings=self._settings)
        context.report_progress(step=STEP_RECORDING)
        context.log(level="info", event_code="backtest_run_completed", message="...", context={...})
        context.raise_if_cancelled()          # post-call checkpoint — STEP_BOUNDARY types ONLY
        return {"run_id": report.run_id, "strategy_id": report.strategy_id, "run_status": report.status, ...}
```
- **For the three queued-only types** (`paper-session`, `broker-order-sync`, `reconciliation`, D-01/D-02): drop **both** `raise_if_cancelled()` calls entirely — cancellation-mode alone makes RUNNING-cancel impossible, enforced earlier in `JobOrchestrationService.cancel()`, never inside the handler.
- The `_DISPLAY_SUMMARY_KEYS` carry-forward pattern (`backtest.py:28-33`, `90-99`) generalizes to each type's own result-summary keys; D-09 requires an explicit **produced-run-ids** key in `result_summary` for every type that creates a run (`risk-evaluation`, `reconciliation`, `paper-session`, `ingest-bars`) — pick one consistent key name across all four (e.g. `run_ids: [...]`) since `services/job_reads.py`'s generalized resources-linkage test (Pitfall/D-09) reads it generically.

**Submission-spec shape** (`backtest_submission.py`, full file):
```python
class _XPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")           # D-25: every payload strict
    strategy_id: StrictStr = Field(min_length=1, max_length=64)
    # ... type-specific fields, each with a `mode="before"` validator normalizing/parsing

class XPayloadRejection(StrEnum):                        # closed, stable machine-readable reasons (D-25)
    UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_TYPE = "invalid_field_type"
    # ... type-specific semantic rejections (e.g. UNKNOWN_STRATEGY_ID, AS_OF_SESSION_IN_FUTURE)

def _map_validation_error(exc: ValidationError) -> XPayloadRejection: ...   # fixed precedence, backtest_submission.py:85-97

class XSubmissionSpec:
    job_type = X_JOB_TYPE
    description = "..."                                  # 1-200 chars, enforced by registry.register()
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY  # or .queued_only for the 3 broker-touching types

    def __init__(self, settings: Settings, *, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or _default_clock

    def validate_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        # pydantic shape parse -> typed InvalidJobPayloadError -> semantic checks (registry lookup,
        # date/session validation via injectable clock) -> return normalized dict. NEVER defaults (D-08/D-21).

    def submission_defaults(self) -> dict[str, str] | None:
        # read-only, computed at catalog-read time; exceptions propagate (route swallows them, job_types.py:44-49)
```
- `as_of_session` validation (D-21) for `risk-evaluation`/`paper-session`/`reconciliation`/`broker-order-sync`: mirror `_BacktestPayload`'s `to_date`-in-future check (`backtest_submission.py:140-147`, using `self._clock()` against the exchange calendar), but validate it **is** a real trading session (new check — use `services/calendar.py::get_calendar`/`sessions_in_range`, no existing exact analog for "is this a session" in a submission spec; closest is `services/market_data_access.py::latest_completed_session`).
- `symbols` normalization (D-24/D-25: upper-case, dedupe, sort) has no existing submission-spec analog — write a `field_validator(mode="before")` following `_BacktestPayload._strip_strategy_id`'s shape (`backtest_submission.py:70-75`).

**Unit test file shape** (`tests/test_backtest_job_type.py`, functions to mirror by name):
`test_rejection_enum_is_closed`, `test_validate_payload_rejects` (parametrized, one case per `XPayloadRejection` member — D-25's "every rejection has a test"), `test_validate_payload_normalizes`, `test_submission_defaults_none_without_sessions`, `test_submission_defaults_from_latest_session`, `test_spec_satisfies_registry_contract`, `test_handler_passes_job_id_and_job_trigger_source`, `test_handler_progress_steps_have_no_percent`, `test_cancel_before_start_never_calls_service` / `test_cancel_during_call_acknowledged_after_service` (STEP_BOUNDARY types only — for queued-only types, replace with a test asserting `raise_if_cancelled` is never called), `test_result_summary_carries_run_id`, `test_service_failure_propagates`, `test_handler_declares_backtest_mode` (rename per type's `ExecutionMode`), `test_handler_never_writes_run_status`.

**E2E test shape** (`tests/test_job_operations_e2e.py:1-140`, full pattern above `test_backtest_job_runs_through_production_path`): production `create_app()` registry, real `run-jobs --once` via `_run_worker_once()`, assert `detail["resources"]` per D-08's table, assert `logs`/`events` event codes.

---

### Per-type deltas (service call signature + job_id threading — the mechanical, error-prone part)

| Job type | Service call (existing signature, verified) | `job_id` kwarg status | Notes |
|---|---|---|---|
| `risk-evaluation` | `services/risk.py::run_risk_evaluation(strategy_id, *, as_of_session, trigger_source="risk_script", settings=None, registry=None)` (risk.py:467-476) | **absent — must add**, thread to `_create_risk_run` (risk.py:577-597) | `resolve_evaluation_session` (risk.py:449) is the "latest completed session" default logic for `submission_defaults` — must NOT be called inside `validate_payload` (P19 D-08) |
| `paper-session` | `services/execution/submit_orders.py::run_paper_session(strategy_id=None, *, as_of_session, risk_run_id=None, trigger_source=None, settings=None, registry=None, execution_service=None, broker_client=None)` (submit_orders.py:680-690) | **absent — must add**, and thread to the internal `reconcile_paper_execution(...)` call at `submit_orders.py:775` so both linked runs share one `job_id` (D-08's "up to 2") | Defaults `strategy_id`/`trigger_source` from `runner_settings` when None — handler must pass both explicitly, never rely on the default, so the Job payload is authoritative |
| `reconciliation` | `services/reconciliation/report.py::reconcile_paper_execution(strategy_id=None, *, as_of_session, settings=None, registry=None, broker_client=None, broker_state=None, recovered_order_count=0, trigger_source="paper_reconciliation")` (report.py:277-287) | **absent — must add**, thread to `_create_reconciliation_run` (report.py:747-753) | Report-only per D-06 — handler must NOT call `apply_reconciliation_corrections` |
| `ingest-bars` | `services/ingestion.py::ingest_daily_bars(*, from_date, to_date, symbols, settings: MarketDataSettings, trigger_source="cli", db_settings=None)` (ingestion.py:183-190) | **absent — must add**, thread to `_start_run` (ingestion.py:113-121) | Note the unusual signature: takes `settings.market_data` (a `MarketDataSettings`, not the top-level `Settings`) plus a separate `db_settings` — copy `worker/commands/ingest.py::run_ingest_bars`'s exact call shape (`ingest.py:17-38`), not `backtest.py`'s |
| `sync-symbol-metadata` | **does not exist as a service function yet** — must be extracted (see below) | new function, should accept `job_id` even though D-08 says no resource (for consistency / future-proofing; or omit — Claude's discretion, no resource is created either way) | See "New service extraction" below |
| `sync-market-sessions` | `services/calendar.py::upsert_market_sessions(session: Session, start, end, exchange=_DEFAULT_EXCHANGE)` (calendar.py:116-127) | N/A — takes a caller-owned `Session`, not settings; no run record (D-08) | **Constraint:** this function needs an open `Session`, but `JobHandler`s may only import `services.*` (contracts.py:107-113), not `db.session` directly. Analog for the fix: `worker/commands/ingest.py::run_sync_sessions` (ingest.py:110-131) opens its own `session_scope` and calls `upsert_market_sessions(db_session, ...)` — but that pattern lives in `worker/commands`, which is being deleted. **Wrap this in a new thin `services/` function** (e.g. `services/calendar.py::sync_market_sessions(settings, from_date, to_date)` that opens `session_scope` internally and calls `upsert_market_sessions`), mirroring `run_sync_sessions`'s body but moved one layer down so the handler stays inside the "services.* only" contract |
| `broker-order-sync` | `services/execution/sync_orders.py::sync_paper_state(strategy_id=None, *, as_of_session, settings=None, registry=None, broker_client=None)` (sync_orders.py:49-56) | N/A — no run record (D-08); no `job_id` threading needed | Defaults `strategy_id` from runner settings — handler passes it explicitly |

**New service extraction (`ORCH-02`, Pitfall 3):**
**Analog/source:** `scripts/sync_symbol_metadata.py:43-58` (`_fetch_ticker_overview`) and `:90-105+` (`_upsert_symbol_metadata`), plus `MetadataSyncResult` at `:142-157`. Move these three into a new `services/symbol_metadata_sync.py` module (name is Claude's discretion, per RESEARCH A2), giving the moved functions the module-level (not function-local) imports they currently have inline (`httpx`, `PolygonAuthError`, `sqlalchemy.select`). The extracted module becomes the new handler's sole service dependency. Delete the broken `importlib`/`sys.path` hack in `worker/commands/ingest.py::run_sync_metadata` (`ingest.py:47-105`) as part of the same change — do not preserve it, since D-30 deletes that whole file anyway.

---

### Domain conflict signaling (OPS-08, D-04)

**Analog:** `jobs/contracts.py`'s existing `JobCancelledError` (contracts.py:18-31) for the new exception; `jobs/runner.py`'s existing two-branch outcome dispatch for the new branch.

```python
# jobs/contracts.py — new exception, same shape as JobCancelledError
class JobDomainConflictError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
```
**Runner change** — exact insertion point, verified via `grep -n`:
- `runner.py`'s `try/except` around `handler.run(context)` currently has exactly `except JobCancelledError: outcome_kind = "cancelled"` then `except Exception as exc: outcome_kind = "error"` (the generic handler-error path that hardcodes `failure_reason=JobFailureReason.HANDLER_ERROR` in the terminal-write section below). Insert a new `except JobDomainConflictError as exc: outcome_kind = "domain_conflict"; failure_message = exc.message` branch **before** the generic `except Exception`, and a matching terminal-write branch (mirroring the existing `outcome_kind == "error"` block) that sets `failure_reason=JobFailureReason.domain_conflict` instead of `HANDLER_ERROR`.
- Each affected handler (today: only wherever `ConcurrentRunLockedError` can surface — `run_paper_order_submission`/`run_paper_session`/`sync_paper_state`/`reconcile_paper_execution`, all via `services/concurrency_guard.py::ConcurrentRunLockedError`, full definition read at `concurrency_guard.py:1-60`) catches `ConcurrentRunLockedError` and re-raises `JobDomainConflictError(message=f"...")`, naming the specific strategy+session lock holder (D-04's `failure_message` requirement).
- `jobs/` must not import domain exceptions (JOB-04) — `ConcurrentRunLockedError` is caught and translated **inside the handler module** (which may import `services.*`), never inside `runner.py` itself.

---

### Queued-only cancellation mode (OPS-03, D-01/D-02)

**Analog:** `jobs/registry.py`'s `JobCancellationMode.STEP_BOUNDARY` (registry.py:19-29) for the new enum member; `orchestration/job_mutations.py::cancel()`'s existing terminal-conflict pattern for the new rejection.

```python
# jobs/registry.py
class JobCancellationMode(StrEnum):
    STEP_BOUNDARY = "step_boundary"
    QUEUED_ONLY = "queued_only"        # exact wire value pinned by UI-SPEC: "queued_only"
```
**Cancel-time rejection** — exact insertion point in `orchestration/job_mutations.py::cancel()` (job_mutations.py:269-342): the row-locked `job = self._require_job(session, job_id, lock=True)` at line 297 is already the correct lock scope. Insert the queued-only check **immediately after** that line and **before** `session.begin_nested()` (line 302) — mirroring how the existing terminal check (`if job.status in (SUCCEEDED, FAILED): raise JobTerminalConflictError(...)`, line 298-299) also runs before the nested block, so a rejected cancel writes **zero** rows (no `JobMutation`, matching D-02's "nothing is recorded"):
```python
if job.status is JobStatus.RUNNING:
    spec = self._registry.resolve_submission_spec(job.job_type)   # requires JobRegistry on the service, already present
    if spec.cancellation_mode is JobCancellationMode.QUEUED_ONLY:
        raise JobNotCancellableRunningError(job_id=job_id)   # new typed exception -> 409 job_not_cancellable_running
```
- New typed exception name and HTTP code string are Claude's discretion (D-01); `api/routes/jobs.py::cancel_job` gets one new `except` clause mapping it to `_error(409, "job_not_cancellable_running", ...)`, following the exact shape of the existing `except JobTerminalConflictError` clause (`jobs.py:141-147`).
- **Race test analog:** `tests/test_job_orchestration.py::test_concurrent_same_key_submission_has_one_persisted_mutation` (lines 206-229, full function read) — `threading.Barrier(2)` + two `threading.Thread`s racing `_service().submit(...)`. Pin the QUEUED-vs-RUNNING cancel/claim race (D-02) the same way: one thread cancels, one thread claims (via the run-jobs claim path), assert exactly one outcome.

---

### Retry (OPS-07, D-16..D-20)

**Analog:** `orchestration/job_mutations.py::submit()` for the new `retry()` method's whole shape (idempotency key validation → fingerprint → `_existing_outcome` lookup → `session.begin_nested()` → insert).

```python
RETRY_ENDPOINT_ID = "POST:/api/v1/jobs/{job_id}/retry"   # mirrors SUBMIT_ENDPOINT_ID / CANCEL_ENDPOINT_ID, job_mutations.py:26-27

def retry(self, *, job_id: UUID, idempotency_key: str | None) -> MutationResult:
    key = self._validate_idempotency_key(idempotency_key)          # job_mutations.py:159-165
    fingerprint = _request_fingerprint({"job_id": str(job_id)}, job_type="retry")  # mirrors cancel(), lines 283-285
    with session_scope(self._settings) as session:
        existing = self._existing_outcome(session, endpoint_id=RETRY_ENDPOINT_ID, key=key, fingerprint=fingerprint)
        if existing is not None:
            return existing
        original = self._require_job(session, job_id, lock=True)   # same row-lock helper, line 176-181
        # D-17: terminal-status check BEFORE session.begin_nested(), same placement as cancel()'s
        # JobTerminalConflictError check at line 298-299, so a rejected retry writes zero rows
        if original.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
            raise JobNotRetryableError(job_id=job_id, status=original.status.value)      # -> 409
        if original.retried_as_job_id is not None:   # via a reverse query or a new column-based check
            raise RetryAlreadyExistsError(job_id=job_id, existing_retry_id=...)          # -> 409
        # D-19 reconcile-first block: applies only to paper-session/broker-order-sync
        if _reconcile_first_blocked(session, original):
            raise ReconciliationRequiredError(job_id=job_id)                              # -> 409
        spec = self._registry.resolve_submission_spec(original.job_type)
        try:
            revalidated_payload = spec.validate_payload(original.payload)                 # D-18
        except InvalidJobPayloadError as exc:
            raise InvalidRetryPayloadError(...) from exc                                  # -> 422, zero rows
        with session.begin_nested():
            new_job_id = submit_job(job_type=original.job_type, payload=dict(revalidated_payload),
                                     retry_of_job_id=job_id, session=session)              # new kwarg, see below
            session.add(JobMutation(endpoint_id=RETRY_ENDPOINT_ID, idempotency_key=key,
                                     request_fingerprint=fingerprint, job_id=new_job_id))
            session.flush()
        return MutationResult(reference=self._reference(self._require_job(session, new_job_id)), replayed=False, created=True)
```
- **`_is_named_uniqueness_error`** (job_mutations.py:147-149) hardcodes `"uq_job_mutations_endpoint_key"`. The new `uq_jobs_retry_of_job_id` constraint (named exactly this, mirroring `uq_strategy_runs_job_id`'s `op.f(...)` convention from `0020_phase19_job_operations.py:34`) needs its **own** dispatch: on `IntegrityError`, check `exc.orig.diag.constraint_name` against both names and map the retry-constraint hit to a `retry_exists` 409 carrying the winning retry's id (a fresh read after the rollback), not a blind re-raise.
- **`submit_job`** (`jobs/dependencies.py:261-268`) needs a new `retry_of_job_id: uuid.UUID | None = None` parameter threaded to the `Job(...)` insert, mirroring how `depends_on` is already threaded through the same function signature.
- **D-19 reconcile-first check** — "reads only the jobs table," no domain-report coupling: a plain `select(Job).where(Job.job_type == "reconciliation", Job.status == JobStatus.SUCCEEDED, Job.completed_at > original.completed_at, ...)` filtered by matching `strategy_id` (read from `original.payload["strategy_id"]`, both types have it per D-22). **No existing analog** for reading `strategy_id` out of `Job.payload` in the orchestration layer — this is new, but structurally trivial (payload is already a `dict` column).
- **API route** (`api/routes/jobs.py`): new `@router.post("/{job_id}/retry", dependencies=[Depends(require_mutations_enabled)])`, copying `cancel_job`'s exact shape (route signature, `_error()` calls, `_mutation_response`).
- **Console:** `retryJob` in `api.ts` mirrors `cancelJob` (`api.ts:241-252`) exactly — same `postJson` call shape, same `Idempotency-Key` requirement. New `MUTATION_ERROR_COPY` entries per the UI-SPEC's Retry rejection table (`job_not_cancellable_running` already needed for D-03a's 409 surfacing too).

---

### Safety controls API (CTRL-01/02, D-10/D-11/D-12)

**Analog:** `api/routes/strategies.py::strategy_detail`'s 404 pattern (`resolve_strategy_metadata`, dependencies.py:126-134) + `api/routes/jobs.py`'s `_error()` helper (jobs.py:63-64) and `require_mutations_enabled` guard (dependencies.py:56-66, already used verbatim on `submit_job`/`cancel_job`). `OperatorControlService` itself needs **zero** changes — `enable_strategy`/`disable_strategy`/`trip_kill_switch`/`reset_kill_switch` (`services/operator_controls.py:170-202`, `323-351`) already return `changed: bool` and write the audit `StrategyRun(OPERATOR_CONTROL)` + `ExecutionEvent` rows unconditionally (`_set_strategy_status`/`_set_kill_switch_state`, full bodies read).

```python
# api/routes/controls.py (new)
router = APIRouter(prefix="/api/v1/controls", tags=["controls"])

class KillSwitchControlRequest(BaseModel):
    state: str  # validated in-handler, NOT via Literal[...] — see constraint below
    reason: str

@router.put("/kill-switch", dependencies=[Depends(require_mutations_enabled)])
def set_kill_switch(request: KillSwitchControlRequest, service: Annotated[OperatorControlService, Depends(get_operator_control_service)]) -> dict:
    reason = _validate_reason(request.reason)          # trim, blank/>500 -> _error(422, "invalid_control_reason")
    if request.state not in {"tripped", "armed"}:
        raise _error(422, "invalid_control_target")
    report = service.trip_kill_switch(reason=reason, ...) if request.state == "tripped" else service.reset_kill_switch(reason=reason, ...)
    return {"state": report.current_state, "changed": report.changed, ...}

@router.put("/strategies/{strategy_id}", dependencies=[Depends(require_mutations_enabled)])
def set_strategy_status(strategy_id: str, request: StrategyControlRequest, registry: ..., service: ...) -> dict:
    resolve_strategy_metadata(strategy_id=strategy_id, registry=registry)   # 404 BEFORE calling the service — dependencies.py:126-134
    reason = _validate_reason(request.reason)
    if request.status not in {"enabled", "disabled"}:
        raise _error(422, "invalid_control_target")
    report = service.enable_strategy(strategy_id, reason=reason, ...) if request.status == "enabled" else service.disable_strategy(...)
    return {"status": report.current_status, "changed": report.changed, ...}
```
- **Constraint (UI-SPEC-mandated, not optional):** do NOT use a pydantic `Literal["tripped", "armed"]` field for `state`/`status` — an unguarded shape-validation failure produces FastAPI's default `{"detail": [...]}` array body, which the console's `{code: ...}` dispatch cannot parse. Validate `state`/`status`/`reason` manually inside the handler and raise `HTTPException(422, detail={"code": "invalid_control_target"|"invalid_control_reason", ...})`, exactly mirroring `_error()`'s shape (`jobs.py:63-64`).
- Both routes sit behind `require_mutations_enabled` (D-12's five-route allowlist) — reuse the dependency unchanged.
- New `get_operator_control_service` dependency in `api/dependencies.py`, mirroring `get_job_orchestration_service` (dependencies.py:80-81) exactly.

---

### Safety controls console (D-13/D-14)

**Analogs (all full files read):** `console/src/components/jobs/CancelJobDialog.tsx` (dialog mechanics — `role="dialog"`, one `Idempotency-Key`-per-opening pattern, though controls send **no** key), `console/src/components/status/KillSwitchPanel.tsx` (single-`useApiQuery`, now gaining a `renderAction` slot), `console/src/components/strategy/StrategyOverviewPanel.tsx` (badge markup to extract + `capability.state`-gated trigger button pattern already used for `Run backtest`, lines 79-101), `console/src/components/KillSwitchBanner.tsx` (three-state banner, gaining an armed-state bar).

- **`ControlConfirmDialog`**: copy `CancelJobDialog.tsx`'s structure (state refs for idempotency-opening-tracking pattern minus the key itself, `useEffect` reset-on-open, `Escape`-to-close, `role="dialog"`/`aria-modal`/backdrop classes) but with props `currentState`, `targetState`, `actionLabel`, `requiresTypedConfirmation?: "RESET"`, `onConfirm(reason) => Promise<{changed: boolean}>`. Confirm button reuses the exact destructive class string from `CancelJobDialog.tsx:145` (`rounded border border-red-700 bg-red-900 px-3 py-1 text-red-50 hover:bg-red-800 disabled:cursor-not-allowed disabled:opacity-50`).
- **`StrategyStatusBadge`**: extract `StrategyOverviewPanel.tsx:126-134`'s inline `<span className={strategy.enabled ? "...emerald..." : "...zinc..."}>` verbatim into a new component taking `{enabled: boolean}`.
- **`KillSwitchPanel.tsx` `renderAction` slot**: add `renderAction?: (data: KillSwitchData) => ReactNode` prop, rendered inside the existing `<StatusPanel>` render-prop body (`KillSwitchPanel.tsx:24-30`), fed from the same `result`/`useApiQuery` call already there — do not add a second fetch.
- **`KillSwitchBanner.tsx` armed-state bar**: today `if (!result.data.is_tripped) return null;` (`KillSwitchBanner.tsx:60-62`) — replace with a slim single-row bar reusing the existing `<FetchMeta>` composition pattern from the tripped-state branch (`:67-79`), adding a `Trip Kill Switch` trigger and a `window.addEventListener("killswitch:changed", refetch)` alongside the existing pathname-change `useEffect` (`:36-41`).
- **`/controls` page**: thin wrapper mirroring `console/src/app/strategy/page.tsx`'s "one page, one panel" shape — mount `<KillSwitchPanel renderAction={...} />` and a new Strategy-control section reusing `StrategyOverviewPanel`'s existing `/api/v1/strategies/trend_following_daily` fetch pattern.
- **`api.ts` control mutations**: `postJson` (api.ts:153-224) hardcodes `method: "POST"` and always sends `Idempotency-Key`. Controls need a sibling (e.g. `putJson`) with `method: "PUT"` and no `Idempotency-Key` header — copy the body/error-parsing logic verbatim, changing only the fetch options and dropping the header line.

---

## Shared Patterns

### `require_mutations_enabled` guard (ORCH-07)
**Source:** `src/trading_platform/api/dependencies.py:56-66`
**Apply to:** every one of the five allowlisted mutating routes — the 7 job-type submissions already inherit it via the existing `submit_job` route; the new `retry` route and both `controls.py` routes must each declare `dependencies=[Depends(require_mutations_enabled)]` explicitly, matching `cancel_job`'s decorator (`jobs.py:120`).

### `_error()` typed-HTTPException helper
**Source:** `src/trading_platform/api/routes/jobs.py:63-64`
**Apply to:** every new/edited route handler (`retry`, both `controls.py` routes) — never let a pydantic `Literal` or default `RequestValidationError` produce an unstructured 422 body.

### `resolve_strategy_metadata` 404 pattern
**Source:** `src/trading_platform/api/dependencies.py:126-134`
**Apply to:** `PUT /api/v1/controls/strategies/{strategy_id}` — call this before invoking `OperatorControlService`, since the service's own `registry.resolve()` raises an unhandled `UnknownStrategyError` otherwise.

### One `job_id` kwarg per run-creating service, threaded to `_create_*_run`
**Source:** `src/trading_platform/services/backtesting.py:104-135` (`run_backtest`/`job_id` → `_create_backtest_run`)
**Apply to:** `run_risk_evaluation`→`_create_risk_run`, `reconcile_paper_execution`→`_create_reconciliation_run`, `ingest_daily_bars`→`_start_run`, and `run_paper_session`'s two internal run-creating calls (its own + the nested `reconcile_paper_execution` call at `submit_orders.py:775`).

### Idempotent mutation shape (submit / cancel / retry)
**Source:** `src/trading_platform/orchestration/job_mutations.py` (full file read — `submit()` lines 197-267, `cancel()` lines 269-342)
**Apply to:** the new `retry()` method — validate key → fingerprint → `_existing_outcome` lookup → locked pre-check → `session.begin_nested()` insert → named-constraint IntegrityError fallback.

### `postJson` mutation-client shape
**Source:** `console/src/lib/api.ts:153-224`
**Apply to:** `retryJob` (identical shape to `cancelJob`) and a new `putJson` sibling for the four control mutations (same body, no `Idempotency-Key`, method `PUT`).

---

## Planner-Facing Constraints (surfaced by direct code inspection — resolve explicitly per plan, do not silently pick one option)

1. **`cancellation_mode` is not on `JobDetail` today**, but `JobHeaderPanel.tsx` (job-UI-scope, `consoleBoundaries.test.ts`-enforced job-type-agnostic) needs it for D-03a's disabled-Cancel-with-reason UX. `services/job_reads.py` cannot resolve it itself — its own docstring (`job_reads.py:8-16`) forbids importing the `jobs` package. Resolve this at the **API route layer** (`api/routes/jobs.py::job_detail`), which already has `get_job_registry` available (same pattern `job_types.py:32-35` uses to read `spec.cancellation_mode`) — merge `job_reads.get_job_detail(...)` output with a registry-derived `cancellation_mode` field before returning. Same composition point is the natural home for `retry_blocked`/`retried_as_job_id` (D-19/D-20's server-derived fields) if those also need job-type-registry lookups.
2. **`retry_of_job_id` reverse lookup (`retried_as_job_id`)** has no existing "derived at read time" analog in `job_reads.py` — it's a plain `select(Job.id).where(Job.retry_of_job_id == job_uuid)` added alongside the existing `resources[]` query in `get_job_detail`.
3. **The `resources[]` fix is a required bugfix, not an addition.** `job_reads.py:124-136` uses `.scalar_one_or_none()`; once `paper-session` legitimately links 2 `StrategyRun` rows (D-08) this raises `MultipleResultsFound`. Change to `.scalars().all()` plus a loop, and add the parallel `MarketDataIngestionRun` query for `ingest-bars` (D-07's new `resources[].kind`).
4. **`sync-market-sessions` handler cannot open its own DB session directly** per the `JobHandler` "services.* only" import contract (`contracts.py:107-113`) — route the `session_scope`-opening logic through a new thin `services/` wrapper (see per-type table above), not inline in the handler.
5. **Migration has no direct analog for two of its four operations:** `0020_phase19_job_operations.py` (full file read) shows the `ALTER TYPE ... ADD VALUE IF NOT EXISTS` pattern for one prior enum addition and the FK+UNIQUE add/drop pattern, both directly reusable for `domain_conflict` and `retry_of_job_id`. But dropping `uq_strategy_runs_job_id` (added by 0020) and adding `jobs.retry_of_job_id UNIQUE` are new compositions this migration must sequence — reference 0020 for style, not for the specific ops. **No Alembic migration in this repo has ever added a nullable-UNIQUE self-referential-adjacent FK** (`retry_of_job_id` FK → `jobs.id`) — closest structural analog is `Job.blocking_job_id`/`root_cause_job_id` (`db/models/job.py:143-161`, `ForeignKey("jobs.id", ondelete="SET NULL")`, no UNIQUE) — add the UNIQUE constraint via `op.create_unique_constraint(op.f("uq_jobs_retry_of_job_id"), "jobs", ["retry_of_job_id"])` following the exact `op.f(...)` naming convention from 0020 line 34.
6. **`worker/commands/operator.py::run_operator_control_command`/`_run_kill_switch_action`** (full file read) is scheduled for **deletion** (D-26/D-30) but is the literal source pattern for the new `kill-switch-trip` subcommand — its `trip-kill-switch` branch inside `_run_kill_switch_action` (lines ~86-96 of that file) is what to copy into a new, much smaller function before the rest of the file is deleted. Sequence: copy first, delete second.
7. **`scripts/sync_symbol_metadata.py`** is likewise slated for deletion but is the sole literal source of the metadata-sync business logic — extract before delete (Pitfall 3, confirmed by direct read of `_fetch_ticker_overview`/`_upsert_symbol_metadata`/`MetadataSyncResult`).

---

## No Analog Found

| File / mechanism | Role | Data Flow | Reason |
|---|---|---|---|
| `jobs.retry_of_job_id` UNIQUE self-adjacent FK migration | migration | — | No prior migration in this repo adds a UNIQUE FK column pointing back into the same table's id space (closest is the non-unique `blocking_job_id`/`root_cause_job_id` pair) — use `db/models/job.py`'s existing FK style + 0020's `op.f(...)` UNIQUE-naming convention as the compositional template, not a literal precedent |
| D-19 "reconcile-first" jobs-table-only predicate | orchestration query | CRUD (read) | New query shape (compare `Job.completed_at` across two job types via `payload["strategy_id"]`) — no prior orchestration-layer code reads inside `Job.payload` for a cross-Job comparison |
| Exact-set closed-world boundary scanner extension (Makefile/DISPATCH/`scripts/` exact-set equality, not just call-matching) | test/tooling | — | `tests/test_orchestration_boundaries.py`'s existing scanners check call-targets and import-boundaries; RESEARCH.md's own recommendation to add **exact-set membership** assertions (not just call-matching) is a genuinely new test-authoring pattern in this file, not a copy of an existing assertion |

---

## Metadata

**Analog search scope:** `src/trading_platform/jobs/`, `orchestration/`, `services/` (backtesting, risk, ingestion, calendar, execution/{submit_orders,sync_orders}, reconciliation/report, operator_controls, concurrency_guard, job_reads), `db/models/` (job, strategy_run, market_data_ingestion_run), `api/routes/` (jobs, strategies, system, job_types), `api/dependencies.py`, `worker/` (parser, __main__, commands/{operator,ingest,__init__}), `scripts/sync_symbol_metadata.py`, `alembic/versions/0020_phase19_job_operations.py`, `tests/` (test_backtest_job_type, test_job_operations_e2e, test_job_orchestration, test_orchestration_boundaries, test_job_import_boundary), `console/src/` (lib/{api,jobTypeForms}.ts, components/jobs/{types,CancelJobDialog,detail/JobHeaderPanel}.tsx, components/jobs/new/{BacktestJobForm,NewJobView}.tsx, components/{KillSwitchBanner,status/KillSwitchPanel,strategy/StrategyOverviewPanel}.tsx, app/layout.tsx).
**Files scanned (full-file reads):** ~35 backend + ~10 console + 3 test files.
**Pattern extraction date:** 2026-09-27
