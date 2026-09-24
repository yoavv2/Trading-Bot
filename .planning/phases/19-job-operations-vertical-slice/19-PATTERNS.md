# Phase 19: Job Operations Vertical Slice - Pattern Map

**Mapped:** 2026-09-24 (revised after two rounds of advisor review)
**Files analyzed:** 47 (backend: 25, console: 22)
**Analogs found:** 41 / 47 (6 no-analog, listed at bottom)

## File Classification

### Backend (Python)

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `alembic/versions/00XX_phase19_job_operations.py` (new) | migration | batch (schema) | `alembic/versions/0016_phase8_stale_run_status.py` (enum ADD VALUE) + `alembic/versions/0019_phase18_job_idempotency.py` (new FK/index) | exact (two precedents to combine) |
| `src/trading_platform/db/models/job.py` (modify: `JobFailureReason.CONFIG_INVALID`) | model | CRUD | same file, `JobFailureReason` class | exact |
| `src/trading_platform/db/models/strategy_run.py` (modify: add `job_id` FK) | model | CRUD | same file (`strategy_id` FK) + `job.py`'s `blocking_job_id` self-FK pattern | exact |
| `src/trading_platform/jobs/handlers/backtest.py` (new) | service (job handler) | request-response / batch | `jobs/contracts.py`'s `JobHandler` Protocol + `worker/commands/backtest.py` (CLI call shape) | role-match — no test blocks `jobs/` importing `services.*` here (see "Pinned boundary tests" #1), but see the placement-tension note in the pattern assignment below before treating this as fully settled |
| `src/trading_platform/jobs/backtest_submission.py` (new, `JobSubmissionSpec` for backtest + D-10 `submission_defaults()`) | service (validation) | request-response | `jobs/registry.py`'s `JobSubmissionSpec` Protocol + `_ProbeSubmissionSpec`/`_Phase18E2ESubmissionSpec` in the Phase 18 tests | exact |
| `src/trading_platform/jobs/registry.py` (modify: `build_default_registry` registers backtest) | service (registry) | event-driven | same file (already has the documented insertion point) | exact |
| `src/trading_platform/jobs/runner.py` (modify: D-22 per-type mode check + `config_invalid` FAILED transition) | service (framework) | event-driven | same file, `execute_job`'s unregistered-job-type FAILED branch (122-136) | exact — **must** live here, not in `run_jobs.py`; see "Pinned boundary tests" |
| `src/trading_platform/services/job_reads.py` (modify: `resources[]` in `get_job_detail`) | service (read) | request-response | same file, `get_job_detail` method (77-131) | exact |
| `src/trading_platform/services/operator_reads.py` (modify: `job_id` in `get_run_detail`/`_serialize_run_summary`) | service (read) | request-response | same file, `get_run_detail` (98-142) / `_serialize_run_summary` (~501+) | exact |
| `src/trading_platform/services/backtesting.py` (modify: `run_backtest`/`_create_backtest_run` accept `job_id`) | service (domain) | CRUD | same file, `run_backtest` (104-172) / `_create_backtest_run` (175-207) | exact |
| `src/trading_platform/api/routes/jobs.py` (modify: mutation guard wiring only) | route (controller) | request-response | same file, `submit_job`/`cancel_job` error-mapping pattern (76-157) | exact |
| `src/trading_platform/api/routes/job_types.py` (new, D-20, separate router/prefix) | route (controller) | request-response | `jobs.py`'s `list_jobs` GET pattern (160-169), adapted — see corrected section below for why this must NOT be appended to `jobs.py`'s own router | exact |
| `src/trading_platform/api/dependencies.py` (modify: `require_mutations_enabled` dependency, job-types wiring) | middleware/dependency | request-response | same file, `get_job_orchestration_service`/`get_job_registry` (60-68), `get_settings` (49-53) | exact — this is the correct home for the ORCH-07 guard; see corrected section below |
| `src/trading_platform/orchestration/job_mutations.py` (no ORCH-07 logic added here — see correction) | service (orchestration) | request-response | same file, existing typed-error classes | exact (unchanged in shape; do not add a mutation-flag check inside this module) |
| `src/trading_platform/core/settings.py` (modify: add `orchestration.mutations_enabled`) | config | CRUD (config load) | same file, `ExecutionSafetySettings` (236-246) — closed boolean flag on a `BaseModel` settings section | exact |
| `src/trading_platform/worker/commands/run_jobs.py` (modify: BACKTEST-level boot validation only) | worker command | event-driven | same file (currently `enforce_startup_config(mode=PAPER)`) | exact — this file must NOT gain the per-type `config_invalid` write; see correction |
| `src/trading_platform/services/config/validation.py` (read-only reference, no change expected) | config/utility | transform | n/a (reference only) | exact |
| `docker-compose.yml` (modify: worker command → `run-jobs`, mutation env var) | config | batch | same file, `worker:` service block | exact |
| `.env.example` (modify: enable mutations for local) | config | batch | same file (flat `KEY=value` list) | exact |
| `render.yaml` (modify: mutations disabled explicitly) | config | batch | same file, `envVars:` list under the `web` service | exact |
| `tests/test_orchestration_boundaries.py` (modify: replace 3 named Phase-18 tripwires with exact-set pin) | test | batch (static analysis) | same file, `test_default_registry_remains_empty_until_phase_19` / `_PHASE19_OPERATION_TYPES` / `test_phase18_diff_excludes_console_and_phase19_handler_registrations` | exact — **not the only tripwire, see "Pinned boundary tests"** |
| `tests/test_job_mutation_api.py`, `tests/test_job_mutation_e2e.py` (modify: enable-mutations fixture override) | test | request-response | same files, `client` fixture (`test_job_mutation_api.py:121-127`) and the inline `TestClient(app)` in `test_job_mutation_e2e.py` | exact |
| `tests/test_db_migrations.py` (modify: `job_failure_reason` exact-set assertion) | test | batch | same file, `test_alembic_upgrade_creates_phase17_job_tables` (~948-966) | exact |
| `tests/test_backtest_job_handler.py` (new) | test | request-response | Phase 18 tests' `_ProbeHandler`/`_registry()` pattern | role-match |
| `tests/test_job_operations_e2e.py` (new, SC1) | test | event-driven / batch | `tests/test_job_mutation_e2e.py` (full submit→worker→terminal E2E shape) | role-match |

### Console (TypeScript/React)

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `console/src/lib/api.ts` (modify: add mutating `submitJob`/`cancelJob` functions + typed-error-code mapping) | utility (API client) | request-response | same file, `fetchApi<T>` (whole file) | exact |
| `console/src/lib/useApiQuery.ts` (modify: add polling option) | hook | polling | same file, whole hook | exact |
| `console/src/lib/jobTypeForms.ts` (new, map 1: `job_type → form`) | provider/lookup-map | transform | none — first lookup-map file | no analog (net-new pattern per D-17) |
| `console/src/lib/resourceRoutes.ts` (new, map 2: `resources[].kind → route`) | provider/lookup-map | transform | none | no analog (net-new pattern per D-17) |
| `console/src/lib/cancellationLabel.ts` (new, D-14 pure function) | utility | transform | `RunsTable.tsx`'s `statusColor()` (34-39) — small pure status→string function, though D-14's case table is materially larger | role-match |
| `console/src/app/jobs/page.tsx` (new) | route/page | polling | `console/src/app/runs/page.tsx` (whole file) | exact |
| `console/src/app/jobs/[jobId]/page.tsx` (new) | route/page | polling | `console/src/app/runs/[runId]/page.tsx` (whole file) | exact |
| `console/src/app/jobs/new/page.tsx` (new) | route/page (form) | request-response | `console/src/app/strategy/page.tsx` (thin shell delegating to a panel) — no existing form/submit page precedent | role-match |
| `console/src/components/jobs/JobsTable.tsx` (new) | component (list) | polling | `console/src/components/runs/RunsTable.tsx` (whole file) | exact |
| `console/src/components/jobs/JobFilters.tsx` (new) | component (filter controls) | transform | `console/src/components/runs/RunFilters.tsx` | exact |
| `console/src/components/jobs/detail/JobHeaderPanel.tsx` (new) | component (detail header) | polling | `console/src/components/runs/detail/RunHeaderPanel.tsx` (whole file) | exact |
| `console/src/components/jobs/detail/JobProgressPanel.tsx` (new) | component | polling | `console/src/components/runs/detail/MetricsPanel.tsx` (fetch-scoped sub-panel pattern) | role-match |
| `console/src/components/jobs/detail/JobLogsPanel.tsx` (new) | component | polling / cursor pagination | `console/src/components/runs/detail/OrdersFillsPanel.tsx` / `CappedDisclosure.tsx` | role-match |
| `console/src/components/jobs/detail/JobEventsPanel.tsx` (new) | component | polling | same analogs as JobLogsPanel | role-match |
| `console/src/components/jobs/detail/JobResourcesPanel.tsx` (new, generic resource-link renderer, map 2 consumer) | component | transform | `StrategyOverviewPanel.tsx`'s `KeyValueSection` (generic key/value renderer) | role-match |
| `console/src/components/jobs/detail/JobResultSummaryPanel.tsx` (new) | component | transform | `StrategyOverviewPanel.tsx`'s `KeyValueSection` (24-56) verbatim generic-dict-render pattern | exact |
| `console/src/components/jobs/CancelJobDialog.tsx` (new) | component (confirmation dialog) | request-response | none — no dialog exists in the console yet | no analog (net-new; UI-SPEC pins the exact overlay markup) |
| `console/src/components/jobs/new/BacktestJobForm.tsx` (new) | component (form) | request-response | none — no submission form exists in the console yet | no analog (net-new) |
| `console/src/app/layout.tsx` (modify: add "Jobs" nav link) | provider (layout) | n/a | same file, existing `<Link>` list (37-48) | exact |
| `console/src/components/runs/detail/RunHeaderPanel.tsx` (modify: D-07 back-link) | component | request-response | same file (the `run_id` `<dd>` row, 123-124) | exact |
| `console/src/app/strategy/page.tsx` / `StrategyOverviewPanel.tsx` (modify: "Run backtest" button, D-18) | component | request-response | same file(s) | exact |
| Various `*.test.tsx` (new, component tests incl. SC6 job-type-agnostic test) | test | n/a | `console/src/components/runs/detail/SummaryMetricsPanel.test.tsx` (whole file) | exact |
| `console/*.test.ts` (new, SC6 "no raw `fetch(` outside `api.ts`" enforcement test) | test | n/a | `tests/test_orchestration_boundaries.py`'s source-scan style (Python side) — **no console-side equivalent exists yet** | no analog — see corrected SC6 section below |

## Pinned Boundary & Enum Tests (read before placing new code)

These existing tests encode hard constraints that several placements below depend on. Get the placement wrong and CI fails on day one.

1. **`tests/test_job_import_boundary.py`** — JOB-04 reverse boundary. `SERVICE_MODULES` is every `*.py` under `src/trading_platform/services/` ONLY. It asserts those files never import `trading_platform.jobs`/`api`/`worker`/`fastapi`/`starlette`/`apscheduler`/`celery`. **It does not scan `jobs/` at all**, so it is not a reason to relocate `jobs/handlers/backtest.py` or `jobs/backtest_submission.py` outside `jobs/` — no test in the repo forbids either file importing `services.*`, and `jobs/contracts.py:107` ("A handler may import and call `trading_platform.services.*` only") explicitly documents handler modules doing exactly this. There is a separate, non-test-enforced design-intent tension worth flagging to the planner rather than resolving here — see the placement note under `jobs/handlers/backtest.py` in "Pattern Assignments" below.

2. **`tests/test_orchestration_boundaries.py::test_run_jobs_is_a_thin_worker_loop_adapter`** (212-227) — asserts, on `worker/commands/run_jobs.py`'s literal source text: `"JobStatus" not in source`, `"select(" not in source`, `"apply_job_transition" not in source`, and that no import besides `services.config.validation` is used. **Hard, test-enforced consequence:** the `config_invalid` FAILED-transition *write* (which needs `apply_job_transition`/`JobStatus`) CANNOT be added to `run_jobs.py` under any circumstance — it must live in `jobs/runner.py`, extending the existing unregistered-job-type FAILED branch (`execute_job`, lines 122-136) with an equivalent pre-dispatch check, on `ConfigValidationError` writing `apply_job_transition(..., failure_reason=JobFailureReason.CONFIG_INVALID, ...)` before `handler.run(context)` is invoked — same shape, same file, as the existing unregistered-type path.

   **Separately, an open (not test-enforced) design question:** *how* `jobs/runner.py` obtains the per-type mode-validation result is not pinned by any test — `grep`ing `tests/test_log_enforcement.py` and `tests/test_job_runner.py` (which does `from trading_platform.jobs import runner as runner_module`) turns up no test asserting `runner.py`'s own import list the way `test_run_jobs_is_a_thin_worker_loop_adapter` pins `run_jobs.py`'s. So `runner.py` importing `services.config.validation` directly and calling `validate_config(...)` itself would pass CI today. However, CONTEXT's canonical-refs section explicitly names "keeping `jobs/` free of domain imports" as an open placement concern for this exact decision (line 124) — a recorded design intent, not (yet) a test. Two options, both CI-clean, presented for the planner to choose between rather than a ruling here:
      - **(a) Direct import:** `jobs/runner.py` imports `services.config.validation.validate_config`/`ConfigValidationError` and calls it inline before dispatch. Simplest, but adds a `services.config` import to the framework's most central module, in tension with the "jobs/ free of domain imports" intent even though no test currently catches it.
      - **(b) Injected callable (preferred, keeps `jobs/` import-clean):** `worker/commands/run_jobs.py` builds `build_settings_payload()` once at boot (an import it and `services.config.validation`'s `ExecutionMode` already use) and passes a small `validate_mode(mode: ExecutionMode) -> str | None` closure into `run_worker_loop(...)` as a new parameter; `jobs/runner.py`'s `execute_job` reads a duck-typed `handler.required_mode` (or an equivalent registry-supplied value) and calls the injected closure, writing the FAILED transition itself on a non-`None` (error-message) return. `run_jobs.py` gains no new forbidden literals (`JobStatus`/`select(`/`apply_job_transition` still don't appear in its source) and `runner.py` imports nothing new — `run_worker_loop`'s signature already accepts a `settings: Settings | None` parameter today, so an added optional callable parameter is a small, backward-compatible extension of an existing pattern.

3. **`tests/test_orchestration_boundaries.py::test_job_route_adapter_imports_only_allowed_layers`** (173-189) — asserts `api/routes/jobs.py` imports no `services.*` module except `services.job_reads`. **Consequence:** the `/job-types` implementation must not add a new services import to `jobs.py`. Build the catalog response from `get_job_registry(request)` and `get_settings(request)` (both already-importable via `trading_platform.api.dependencies`) — `registry.list_job_types()` for the type list, `registry.resolve_submission_spec(job_type)` per type for `cancellation_mode`/description, and (per D-10) call `spec.submission_defaults()` if the spec exposes it, wrapped in try/except per the catalog-resilience discretion note, **and** wrapped to tolerate `UnknownJobTypeError` from `resolve_submission_spec` for any runner-only (non-publicly-submittable) registered type, which per `registry.py`'s own docstring (87-98) is an expected, non-exceptional case — a naive un-guarded loop over every `list_job_types()` entry would 500 the whole catalog the moment a runner-only type exists. The `submission_defaults()` method itself lives on the `BacktestSubmissionSpec` object under `jobs/`, which — per finding 1 above — is already permitted to call `services.*`.

   **Mount path — do not nest under the existing `router` (blocking correction):** `api/routes/jobs.py`'s `router = APIRouter(prefix="/api/v1/jobs", ...)` already declares `@router.get("/{job_id}")`, which matches any single path segment. A route added as `@router.get("/job-types")` on this same router would (a) resolve to `/api/v1/jobs/job-types`, not the `/api/v1/job-types` path D-20/SC4 require, and (b) even if the path were changed, sits in the same prefix as the greedy `/{job_id}` route and risks being shadowed/misrouted depending on declaration order (FastAPI matches routes in registration order, and `/{job_id}` would attempt UUID coercion on the literal segment `job-types` and 422 rather than matching). **Use a separate router** — either a second `APIRouter(prefix="/api/v1/job-types", tags=["job-types"])` inside `jobs.py`, or a new `api/routes/job_types.py` module — registered via its own `app.include_router(...)` call in `api/app.py:create_app` (60-73), alongside the existing `app.include_router(jobs_router)` line. If placed in a new module, note `test_job_route_adapter_imports_only_allowed_layers` only scans `api/routes/jobs.py` by path, so a new file is not itself constrained by that test — but should still follow the same "no new arbitrary services import" discipline for consistency, and `test_jobs_router_exposes_exact_allowed_methods` (`tests/test_job_api.py`, ~536-556) filters routes by `path.startswith("/api/v1/jobs")`, which `/api/v1/job-types` does NOT match (`"/api/v1/job-types"[:12] == "/api/v1/job-"`, not `"/api/v1/jobs"`) — confirmed by direct string comparison — so a correctly-separate-prefixed `/api/v1/job-types` router does not trip that pinned exact-dict assertion, while an incorrectly-nested `/api/v1/jobs/job-types` route would have.

4. **`tests/test_orchestration_boundaries.py::test_orchestration_layer_has_no_transport_or_domain_service_dependencies`** (192-209) — asserts `orchestration/job_mutations.py` imports no `services.*`, `fastapi`, or `starlette`. This does not technically block reading a settings flag (settings is `core.settings`, already imported there), but per the corrected ORCH-07 placement below, the guard does not live in this file regardless — for ordering and Phase-20-reuse reasons, not to satisfy this test.

5. **`tests/test_db_migrations.py::test_alembic_upgrade_creates_phase17_job_tables`** (~948-966) — asserts, by exact set equality: `enums["job_failure_reason"] == {"handler_error", "worker_lost", "lease_expired", "cancellation_timeout"}`. **This test breaks the moment the Phase 19 migration adds `config_invalid`**, unless it is updated in the same change to include the fifth value. Flag for the planner as a required same-PR edit, not an incidental regression to debug later.

6. **A fourth, unnamed registry-emptiness tripwire** — `tests/test_job_mutation_e2e.py::test_submit_execute_and_observe_with_test_only_handler` independently asserts `build_default_registry().list_job_types() == []` and `production_operations.isdisjoint(build_default_registry().list_job_types())` (with its own locally-defined `production_operations` set matching `_PHASE19_OPERATION_TYPES`). Roadmap SC9 names only three tripwires (`test_default_registry_remains_empty_until_phase_19`, the `_PHASE19_OPERATION_TYPES` denylist, `test_phase18_diff_excludes_console_and_phase19_handler_registrations`) — **this fourth assertion is a real, additional site that must also be updated/removed**, or the E2E test fails immediately once `backtest` is registered, independent of anything in `test_orchestration_boundaries.py`.

7. **A fifth tripwire, in a Phase-17 test file untouched by Phase 18/19 boundary work:** `tests/test_job_registry.py::test_build_default_registry_is_empty_in_phase_17` (lines 88-91) asserts `build_default_registry().list_job_types() == []` verbatim (its own name even says "in Phase 17"). This file is not mentioned anywhere in CONTEXT's canonical refs or in roadmap SC9's three named tripwires, but it will fail identically to the other four the moment `backtest` is registered. Grep confirms this is the full list of `build_default_registry`/`list_job_types()` emptiness assertions tied to the Job registry specifically (as opposed to the unrelated `trading_platform.strategies.registry.build_default_registry`, a same-named but different function used throughout the paper/backtest/analytics test suite — do not confuse the two when searching). **Total: five call sites across four files** (`tests/test_orchestration_boundaries.py` ×3 assertions/helpers, `tests/test_job_mutation_e2e.py` ×2 assertions, `tests/test_job_registry.py` ×1 assertion) must all be updated in the same change that registers `backtest`.

## ORCH-07 Mutation Guard — Placement (corrected)

**Do not** raise a "mutations disabled" error from inside `JobOrchestrationService` (as a naive reading of the existing `submit()`/`cancel()` exception-mapping idiom might suggest). Three reasons:

1. **Ordering — verified directly against the installed FastAPI source** (`.venv/lib/python3.13/site-packages/fastapi/routing.py`), not just inferred:
   - The ASGI `app(request)` closure (lines 406-473) reads and parses the request body **before** `solve_dependencies` is ever called (line 481). A syntactically invalid JSON body raises `RequestValidationError` (422) at lines 451-465, or `HTTPException(400, "There was an error parsing the body")` for other parse failures (469-473) — **strictly before any dependency, including a route-decorator-level guard, ever runs.** A malformed-JSON request therefore always returns 422/400 regardless of the mutation flag; this is expected and does not need to be "fixed."
   - Once the body is parsed (as a dict, even if schema-invalid), `solve_dependencies` (`fastapi/dependencies/utils.py`) iterates `dependant.dependencies` — which includes anything passed via the route decorator's `dependencies=[...]` list — at line 619, and only calls `request_body_to_args` (the step that validates the parsed dict against the pydantic `SubmitJobRequest` model and raises the field-level 422) afterward, at line 702. So a `dependencies=[Depends(require_mutations_enabled)]` entry on the `@router.post(...)` decorator raises its 403 **before** schema-validation 422s (e.g. a syntactically-valid-JSON body missing the required `job_type` field), but **not** before syntax-level 422/400s.
   - **Practical consequence for the ordering test CONTEXT's discretion note asks for:** pin it as "missing `Idempotency-Key` → 403 (guard fires before the header dependency)" and "schema-invalid JSON body (e.g. `{}`) on a disabled deployment → 403, not 422" — do not attempt to pin "malformed/non-JSON body → 403"; that case is architecturally always 422/400 first, and a test asserting otherwise will fail.
2. **Reuse.** Phase 20 SC7 puts `OperatorControlService`'s control endpoints under the *same* mutation guard. A dependency defined once in `api/dependencies.py` (reading `get_settings(request).orchestration.mutations_enabled`) is trivially attachable to those future routes too; logic embedded inside `JobOrchestrationService` is not.
3. **Type safety.** `JobOrchestrationService.__init__(self, settings: Settings | DatabaseSettings, registry: JobRegistry)` accepts either type. Reading `settings.orchestration.mutations_enabled` inside the service would break at runtime for any caller (present or future) that constructs it with a bare `DatabaseSettings`. The HTTP-layer dependency always has the full `Settings` object via `get_settings(request)`.

**Correct pattern** — mirror `api/dependencies.py`'s existing dependency-function shape (`get_settings`, 49-53):
```python
def get_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Application settings not loaded yet.")
    return settings
```
New:
```python
def require_mutations_enabled(request: Request) -> None:
    if not get_settings(request).orchestration.mutations_enabled:
        raise HTTPException(status_code=403, detail={"code": "mutations_disabled"})
```
Attached in `api/routes/jobs.py` as `@router.post("", dependencies=[Depends(require_mutations_enabled)])` and the same on the cancel route — reusing the file's existing `Depends(...)` import and style (already imported at line 9). This keeps `orchestration/job_mutations.py` untouched (satisfies pinned-test #4 above trivially, as a side effect rather than the reason) and adds zero new imports to `jobs.py` beyond one more name from `trading_platform.api.dependencies`, which it already imports six names from.

**Blast-radius consequence of this placement (good news):** `tests/test_job_orchestration.py` constructs `JobOrchestrationService(load_settings(), _registry())` directly and calls `.submit()`/`.cancel()` on it (~20 call sites, lines 119-315) — entirely bypassing FastAPI/HTTP. Since the guard lives at the HTTP dependency layer, **none of these call sites need modification.** Only genuinely HTTP-routed tests are affected (next section).

## Mutation-Flag Blast Radius (D-19 default-disabled)

Every test that exercises `POST /api/v1/jobs` or `POST /api/v1/jobs/{id}/cancel` through an actual `TestClient`/FastAPI app will start receiving 403 the moment the flag defaults to disabled, unless its fixture explicitly enables it:

| File | Mechanism | Call sites | Fix |
|---|---|---|---|
| `tests/test_job_mutation_api.py` | Shared `client` fixture (`app.state.job_registry = _registry()`, `TestClient(app)`, 121-127) reused by ~9 test functions (`test_submit_accepts_new_job_and_replays_exact_request`, `test_submit_conflicts_for_changed_registered_type`, `test_cancel_handles_queued_running_and_cancelled_repeats`, etc.) | ~15 `client.post("/api/v1/jobs...")` calls total | Add `monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")` inside the `client` fixture (it already receives `monkeypatch` transitively via `migrated_job_mutation_api_db`), alongside `_set_database_env`. Add one new dedicated test, e.g. `test_submit_rejected_when_mutations_disabled_by_default`, using a client built **without** the override, asserting 403 + `_counts()` unchanged (mirrors `test_submit_rejections_are_typed_and_write_nothing`, line 207). |
| `tests/test_job_mutation_e2e.py` | Own inline `TestClient(app)` construction inside `test_submit_execute_and_observe_with_test_only_handler` (own `migrated_job_mutation_e2e_db` fixture, separate from the file above) | 3 `client.post(...)` calls (rejected/submitted/replay) | Same env-var override, applied via the `migrated_job_mutation_e2e_db` fixture's `monkeypatch` parameter. |
| `tests/test_job_orchestration.py` | Calls `JobOrchestrationService(...).submit()`/`.cancel()` directly — no `TestClient`, no FastAPI app | ~20 call sites | **Unaffected** — no change needed, because the guard lives at the HTTP dependency layer (see placement correction above), not inside the service. |
| `tests/test_orchestration_boundaries.py` | Route-shape/import-boundary static tests (`test_runtime_application_has_exactly_two_mutating_job_routes`, etc.) | 0 behavioral call sites | Unaffected by the flag itself; only the registry-emptiness tripwires (separate concern, see "Pinned Boundary Tests" #6) need editing here. |
| `tests/test_db_migrations.py` | Enum exact-set assertion, not a mutation call | n/a | Unaffected by the flag; needs the `config_invalid` addition (see "Pinned Boundary Tests" #5). |

New E2E tests written for SC1/SC2 (backtest full path, idempotent resubmission) must explicitly enable the flag in their own fixtures from the start — do not write them against the default.

**`.env` trap for the disabled-by-default test specifically.** `core/settings.py`'s `EnvironmentOverrides` class loads `env_file=".env"` (pydantic-settings `SettingsConfigDict`). D-19 requires `.env.example` to enable mutations for local development; once a developer copies that to a real `.env` (a normal, expected setup step), any test relying on "no override present" to prove the disabled default would silently start reading `mutations_enabled=true` from that file instead. Two mitigations, both needed:
- The new `test_submit_rejected_when_mutations_disabled_by_default`-style HTTP test must explicitly `monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "false")` (or `monkeypatch.delenv(...)` combined with a guaranteed-absent `.env` in the test's working directory) rather than relying on ambient absence of configuration — do not assume "nobody set it" holds in every environment.
- Additionally pin the *model-level* default independent of any `.env`/env-var state, e.g. `assert OrchestrationSettings().mutations_enabled is False` (or the equivalent field access on a bare `Settings()`), which proves the Pydantic field default itself is `False` regardless of what any test runner's ambient `.env` happens to contain.

## Pattern Assignments

### `alembic/versions/00XX_phase19_job_operations.py` (migration)

**Two analogs, for two distinct operations in the same migration:**

**Analog A — adding an enum value** (`JobFailureReason.CONFIG_INVALID`): `alembic/versions/0016_phase8_stale_run_status.py` (full file, 23 lines):
```python
def upgrade() -> None:
    op.execute("ALTER TYPE strategy_run_status ADD VALUE IF NOT EXISTS 'stale'")

def downgrade() -> None:
    # PostgreSQL cannot drop a single enum value in place without recreating
    # the whole type (rewriting every dependent column). That rewrite is
    # intentionally not performed here, so this downgrade is a documented
    # no-op: 'stale' remains a valid value after downgrading past this revision.
    pass
```
Apply verbatim for `job_failure_reason`: `op.execute("ALTER TYPE job_failure_reason ADD VALUE IF NOT EXISTS 'config_invalid'")`. This project uses **native Postgres enums** (`sa.Enum`/`postgresql.ENUM`), never `varchar + CHECK`, for every closed-vocabulary column — see `alembic/versions/0018_phase17_job_framework.py` lines 15-48 where five enum types are created via `postgresql.ENUM(*VALUES, name=...).create(bind, checkfirst=False)` and every column references them with `create_type=False`. `CheckConstraint` (also present in `0018`, lines 110-113 / `job.py` lines 74-82) is reserved for numeric-range invariants (`progress_percent` 0-100), not closed string vocab. **Do not introduce a varchar+CHECK enum for `config_invalid` — follow the native-enum `ALTER TYPE ADD VALUE` precedent**, and update `tests/test_db_migrations.py`'s exact-set assertion in the same change (see "Pinned Boundary Tests" #5).

Caveat inherited from `0016`: `ALTER TYPE ... ADD VALUE` cannot safely run in the same transaction as other DDL that references the new value in some Postgres versions. The `0016` migration keeps this as its only statement. Consider isolating the enum-add from the FK/column changes below, or splitting into two migration files, if this becomes an issue.

**Analog B — new nullable-unique FK column**: `alembic/versions/0019_phase18_job_idempotency.py` (full file, 49 lines) shows the `sa.ForeignKeyConstraint`/`sa.UniqueConstraint`/`op.create_index` shape:
```python
sa.Column("job_id", sa.UUID(), nullable=True),
...
sa.ForeignKeyConstraint(
    ["job_id"], ["jobs.id"],
    name=op.f("fk_strategy_runs_job_id_jobs"),
    ondelete="SET NULL",
),
...
sa.UniqueConstraint("job_id", name="uq_strategy_runs_job_id"),
```
Since `strategy_runs` already exists (unlike `0019`'s new table), use `op.add_column` / `op.create_foreign_key` / `op.create_unique_constraint` instead of `op.create_table`. Name constraints via `op.f()` to match this project's naming convention (`src/trading_platform/db/base.py`: `NAMING_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_N_name)s", "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s", "ck": "ck_%(table_name)s_%(constraint_name)s", ...}`) — a plain `unique=True` on the ORM column (`strategy_run.py`) will auto-derive the identical `uq_strategy_runs_job_id` name from this same convention at the model level, so the migration's explicit name and the model's declarative name will agree.

---

### `src/trading_platform/db/models/job.py` (modify)

**Analog:** same file, `JobFailureReason` (lines 45-56)
```python
class JobFailureReason(StrEnum):
    HANDLER_ERROR = "handler_error"
    WORKER_LOST = "worker_lost"
    LEASE_EXPIRED = "lease_expired"
    CANCELLATION_TIMEOUT = "cancellation_timeout"
```
Add `CONFIG_INVALID = "config_invalid"` as a fifth member — `_enum_values()` (66-67) derives the SQLAlchemy `Enum(..., values_callable=_enum_values)` list automatically (110-118), no other model change needed.

---

### `src/trading_platform/db/models/strategy_run.py` (modify: `job_id` FK)

**Analog:** same file's existing `strategy_id` FK (55-58) and `job.py`'s self-referential nullable FK pattern (`blocking_job_id`, 142-146):
```python
strategy_id: Mapped[uuid.UUID] = mapped_column(
    ForeignKey("strategies.id", ondelete="CASCADE"),
    nullable=False,
)
```
```python
blocking_job_id: Mapped[uuid.UUID | None] = mapped_column(
    Uuid(as_uuid=True),
    ForeignKey("jobs.id", ondelete="SET NULL"),
    nullable=True,
)
```
New field per D-01: `job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, unique=True)`.

---

### `src/trading_platform/services/backtesting.py` (modify: `job_id` threading)

**Analog:** same file, `run_backtest` (104-172) and `_create_backtest_run` (175-207):
```python
def run_backtest(
    strategy_id: str, *, from_date: date, to_date: date,
    trigger_source: str = "backtest_script",
    settings: Settings | None = None, registry: StrategyRegistry | None = None,
) -> BacktestRunReport:
    ...
    run_id = _create_backtest_run(resolved_settings, metadata, trigger_source=trigger_source, from_date=from_date, to_date=to_date)
```
Per D-02, add an opaque `job_id: uuid.UUID | None = None` keyword parameter to both `run_backtest` and `_create_backtest_run`, and set it on the `StrategyRun(...)` construction inside `_create_backtest_run` (185-204) in the same `session.add(strategy_run); session.flush()` transaction (205-206). `trigger_source` for Job-originated calls is the literal `"job"` per D-11 — reuse the existing keyword, do not add a new one.

---

### `src/trading_platform/jobs/registry.py` (modify: register backtest)

**Analog:** same file, `build_default_registry` docstring (107-123) — the exact, already-documented insertion point:
```python
def build_default_registry(settings: Settings | None = None) -> JobRegistry:
    _ = settings or load_settings()
    return JobRegistry()
```
Becomes:
```python
def build_default_registry(settings: Settings | None = None) -> JobRegistry:
    resolved = settings or load_settings()
    registry = JobRegistry()
    registry.register(BacktestJobHandler(), submission_spec=BacktestSubmissionSpec())
    return registry
```
`registry.register()`'s signature/validation (56-79) is unchanged. This is also the exact site that five separate assertions across four test files currently pin as empty/disjoint from production operation names — see "Pinned Boundary Tests" #6-7 and `test_orchestration_boundaries.py`'s three named tripwires; all five must be updated/replaced, not left passing, per SC9.

---

### `src/trading_platform/jobs/handlers/backtest.py` (new, `JobHandler` implementation)

**Analog:** `jobs/contracts.py`'s `JobHandler` Protocol (103-124) for shape, plus `worker/commands/backtest.py` (whole file, 87 lines) for the actual `resolve_backtest_window`/`run_backtest` call shape:
```python
class JobHandler(Protocol):
    @property
    def job_type(self) -> str: ...
    def run(self, context: JobContext) -> Mapping[str, Any]: ...
```
```python
from_date, to_date = resolve_backtest_window(settings=settings, from_date_arg=args.from_date, to_date_arg=args.to_date)
report = run_backtest(args.strategy, from_date=from_date, to_date=to_date, trigger_source=args.trigger_source, settings=settings)
```
Per finding 1 in "Pinned Boundary Tests," no test in the repo blocks this file importing `services.backtesting` — `test_job_import_boundary.py` (the only reverse-boundary test) scans `services/` only, and `jobs/contracts.py:107` explicitly documents "a handler may import and call `trading_platform.services.*` only" as the intended contract for handler modules specifically. That said, `jobs/registry.py` importing this handler module to register it (so `build_default_registry` can construct it) makes the `jobs/` package transitively depend on `services.backtesting`, which is in tension with CONTEXT's separate "keeping `jobs/` free of domain imports" note (line 124, made in the context of the *runner's* mode-check placement, not the handler itself) — flagging this tension for the planner rather than treating the handler's placement as a closed question: if a stricter separation is wanted, the handler/spec pair could instead live under a new top-level package (e.g. `trading_platform.job_handlers`) that `jobs/registry.py` imports from, with no test currently distinguishing between the two layouts. The handler's `run(context)` implements D-12's cancellation checkpoints (`context.raise_if_cancelled()` before and after the single `run_backtest(job_id=context.job_id, ...)` call) and returns the report's dict as `result_summary` (containing `run_id` per D-06). Per D-16, call `context.report_progress(step=...)` at each stage (`resolving strategy` → `running backtest` → `recording result`) with `percent=None` throughout — the framework sets 100 on SUCCEEDED automatically (`jobs/runner.py`'s `_progress.mark_completed`, line 242).

---

### `src/trading_platform/jobs/runner.py` (modify: D-22 per-type mode check)

**Analog:** same file, `execute_job`'s unregistered-job-type FAILED branch (122-136):
```python
try:
    handler = registry.resolve(job_type)
except UnknownJobTypeError:
    with session_scope(settings) as session:
        apply_job_transition(
            session, job_id=job_id,
            request=JobTransitionRequest(
                event_type=JobEventType.FAILED,
                failure_reason=JobFailureReason.HANDLER_ERROR,
                failure_message=f"No handler registered for job type '{job_type}'.",
                outcome_uncertain=False,
            ),
        )
        cascade_dependency_outcome(session, terminal_job_id=job_id)
    return JobStatus.FAILED
```
Immediately after this block (handler successfully resolved, before `handler.run(context)` at line ~185), add an equivalent check: obtain the handler's declared mode-validation result, and on failure write `apply_job_transition(..., failure_reason=JobFailureReason.CONFIG_INVALID, failure_message=<names the failing fields>, outcome_uncertain=False)` then `cascade_dependency_outcome(...)` and `return JobStatus.FAILED` — same shape as the block above. **The write itself cannot live in `worker/commands/run_jobs.py`** (test-enforced — see "Pinned Boundary Tests" #2); *how* the mode-validation result is obtained (direct `services.config.validation` import in this file, vs. an injected callable from `run_jobs.py`) is an open, not-test-enforced choice — see the two options laid out in "Pinned Boundary Tests" #2.

---

### `src/trading_platform/services/job_reads.py` (modify: `resources[]`)

**Analog:** same file, `get_job_detail` (77-131), especially the existing `dependencies`/`blocking_dependencies` derived-at-read-time list construction (85-105):
```python
dependency_rows = session.execute(
    select(JobDependency, Job).join(Job, Job.id == JobDependency.depends_on_job_id).where(JobDependency.job_id == job_uuid)
).all()
dependencies: list[dict[str, Any]] = []
for _edge, dependency_job in dependency_rows:
    entry = {"id": str(dependency_job.id), "job_type": dependency_job.job_type, "status": dependency_job.status.value}
    dependencies.append(entry)
```
Mirror this shape for `resources[]` (D-04): query `StrategyRun` where `StrategyRun.job_id == job_uuid`; if found, append `{"kind": "strategy_run", "id": str(run.id), "status": run.status.value, "links": {"self": f"/api/v1/runs/{run.id}"}}`; empty list otherwise. Add `StrategyRun` to this module's existing `from trading_platform.db.models import (...)` block (29-35) — importing an additional model is fine; importing from `trading_platform.jobs` or `trading_platform.api` is what this module's boundary comment (8-16) forbids.

---

### `src/trading_platform/services/operator_reads.py` (modify: `job_id` on run detail)

**Analog:** same file, `get_run_detail` (98-142) and `_serialize_run_summary` (~501+). The existing `select(StrategyRun, Strategy)` query already selects the full ORM object, so `strategy_run.job_id` is available without a query change — add `"job_id": str(strategy_run.job_id) if strategy_run.job_id else None` to the serializer dict, mirroring the existing `"trigger_source": strategy_run.trigger_source` line (~509).

---

### `src/trading_platform/api/routes/jobs.py` (modify: `require_mutations_enabled` guard wiring only)

**Guard wiring** — see the dedicated "ORCH-07 Mutation Guard — Placement" section above for the full rationale; the concrete change to this file is adding `dependencies=[Depends(require_mutations_enabled)]` to the `@router.post("")` and `@router.post("/{job_id}/cancel")` decorators, and importing `require_mutations_enabled` alongside the other names already imported from `trading_platform.api.dependencies` (13-19). **`/job-types` does NOT belong on this file's `router` object** — see the next entry.

---

### `src/trading_platform/api/routes/job_types.py` (new — separate router, D-20)

**Why a separate file/router, not appended to `jobs.py`:** `jobs.py`'s `router = APIRouter(prefix="/api/v1/jobs", ...)` already declares a greedy `@router.get("/{job_id}")`. Mounting `/job-types` on this router either produces the wrong path (`/api/v1/jobs/job-types` instead of the required `/api/v1/job-types`) or, if forced onto a path-matching workaround, risks colliding with the `{job_id}` UUID-typed path converter. Use a second, independently-prefixed router — see "Pinned Boundary Tests" #3 for the full mount-path correction.

**Analog:** `list_jobs` GET pattern in `jobs.py` (160-169) for the delegate-to-a-dependency shape, adapted to build the response inline (per finding 3, no new `services.*` import is available here beyond what `job_reads` already allows on the *other* file — this new file has no such restriction imposed by any existing test, but should still avoid adding unnecessary service imports for consistency):
```python
router = APIRouter(prefix="/api/v1/job-types", tags=["job-types"])

@router.get("")
def list_job_types(
    registry: Annotated[JobRegistry, Depends(get_job_registry)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    items = []
    for job_type in registry.list_job_types():
        try:
            spec = registry.resolve_submission_spec(job_type)
        except UnknownJobTypeError:
            # Runner-only registrations (no public submission spec) are
            # expected per registry.py's own docstring (87-98) -- omit
            # them from the public catalog rather than 500ing the whole
            # response.
            continue
        entry = {"job_type": job_type, "description": ..., "cancellation_mode": ...}
        if hasattr(spec, "submission_defaults"):
            try:
                defaults = spec.submission_defaults()
                if defaults is not None:
                    entry["submission_defaults"] = defaults
            except Exception:
                pass  # catalog resilience discretion note: omit, don't fail the whole catalog
        items.append(entry)
    return {"mutations_enabled": settings.orchestration.mutations_enabled, "items": items}
```
Register via `app.include_router(job_types_router)` in `api/app.py:create_app` (60-73), alongside the existing router registrations. This adds no new `services.*` import to `jobs.py` (the file the pinned import-boundary test actually scans) — the catalog logic lives entirely in this new module instead.

---

### `src/trading_platform/api/dependencies.py` (modify: `require_mutations_enabled`)

**Analog:** same file, `get_settings` (49-53):
```python
def get_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Application settings not loaded yet.")
    return settings
```
New:
```python
def require_mutations_enabled(request: Request) -> None:
    if not get_settings(request).orchestration.mutations_enabled:
        raise HTTPException(status_code=403, detail={"code": "mutations_disabled"})
```
Same file already exposes `get_job_registry` (60-64) for the `/job-types` route to depend on.

---

### `src/trading_platform/core/settings.py` (modify: mutation flag)

**Analog:** same file, `ExecutionSafetySettings` (236-246):
```python
class ExecutionSafetySettings(BaseModel):
    repeated_failure_threshold: int = Field(default=3, ge=1)
    block_on_unresolved_reconciliation: bool = True
    stale_run_timeout_minutes: int = Field(default=30, ge=1)
```
Add a new section, e.g. `class OrchestrationSettings(BaseModel): mutations_enabled: bool = False`, registered on **both** `Settings` (259+) and the parallel `EnvironmentOverrides` `BaseSettings` class immediately below it — this file declares every settings section twice (once per class) and both must be updated together, per its existing dual-declaration pattern (e.g. `execution: ExecutionSettings = ExecutionSettings()` appears in both classes). Default must be `False` per D-19.

---

### `src/trading_platform/worker/commands/run_jobs.py` (modify: BACKTEST-level boot only)

**Analog:** same file (whole file, 43 lines), one-line change:
```python
settings = enforce_startup_config(mode=ExecutionMode.PAPER)
```
→
```python
settings = enforce_startup_config(mode=ExecutionMode.BACKTEST)
```
No other change belongs in this file — per "Pinned Boundary Tests" #2, the per-type `config_invalid` check and write live in `jobs/runner.py`, not here.

---

### `docker-compose.yml` (modify: ORCH-05, D-19)

**Analog:** same file, `worker:` service block (35-47):
```yaml
command: ["python", "-m", "trading_platform.worker", "serve", "--interval-seconds", "30"]
```
→
```yaml
command: ["python", "-m", "trading_platform.worker", "run-jobs"]
```
plus `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED: "true"` in both the `api:` and `worker:` `environment:` blocks, mirroring the existing flat `TRADING_PLATFORM_DATABASE__*` env var style.

---

### `render.yaml` (modify: ORCH-07, D-19)

**Analog:** same file, `envVars:` list (34-58):
```yaml
- key: TRADING_PLATFORM_DATABASE__PORT
  value: "5432"
```
Add `- key: TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED` / `value: "false"` in the same style. **Note (non-blocking doc drift):** this file's header comment currently states "Every route is read-only (GET)" (~line 12) — that becomes inaccurate once `POST /api/v1/jobs`/`POST /api/v1/jobs/{id}/cancel` exist on this same deployed API service (they'll just be guarded 403 by the disabled flag). Flag for the planner to reword the comment; not a test-breaking issue.

---

### `tests/test_orchestration_boundaries.py` (modify: SC9 tripwire replacement)

**Analog:** same file — the three named tripwires:
```python
_PHASE19_OPERATION_TYPES = {"backtest", "risk", "paper", "reconciliation", "market-data", "broker-order-lifecycle"}
def test_default_registry_remains_empty_until_phase_19() -> None: ...
def test_phase18_diff_excludes_console_and_phase19_handler_registrations() -> None: ...
```
Replace with a single pinned-set test:
```python
def test_default_registry_contains_exactly_the_phase19_job_types() -> None:
    from trading_platform.jobs.registry import build_default_registry
    assert build_default_registry().list_job_types() == ["backtest"]
```
Delete `test_default_registry_remains_empty_until_phase_19`, `test_phase18_diff_excludes_console_and_phase19_handler_registrations`, and the AST-scanning helpers (`_registered_operation_types`/`_class_job_types`/git-diff logic, ~322-388) that existed solely for the "stays empty" invariant. **Also fix the fourth and fifth tripwires** in `tests/test_job_mutation_e2e.py` and `tests/test_job_registry.py` respectively (see "Pinned Boundary Tests" #6-7) — neither is in this file but both will fail identically. Keep every other test in `test_orchestration_boundaries.py` unchanged.

---

### `tests/test_db_migrations.py` (modify: enum exact-set assertion)

**Analog:** same file, `test_alembic_upgrade_creates_phase17_job_tables`:
```python
assert enums["job_failure_reason"] == {
    "handler_error", "worker_lost", "lease_expired", "cancellation_timeout",
}
```
Add `"config_invalid"` to this literal set in the same change that adds the migration — this is an exact-equality assertion, not a subset check, so it fails deterministically otherwise.

---

### `src/trading_platform/jobs/dependencies.py` (reference only — SUBMITTED event precedent)

No change expected. Relevant for the UI-SPEC's "Events panel should never be empty" claim: `submit_job`/`_submit_job_in_session` (174-261) writes a `JobEventType.SUBMITTED` event (line 211) in the same transaction that creates the Job row — this is the backend guarantee behind the console's defensive-only "No events recorded yet" empty state (UI-SPEC line 202).

---

### `console/src/lib/api.ts` (modify: mutating client, SC6 sole-fetch-site)

**Analog:** same file, `fetchApi<T>` (whole file, 76 lines):
```typescript
export async function fetchApi<T>(endpoint: string): Promise<ApiResult<T>> {
  let response: Response;
  try {
    response = await fetch(`/backend${endpoint}`, { cache: "no-store" });
  } catch {
    return { ok: false, endpoint, status: null, message: `${endpoint} is unreachable...`, asOf: new Date() };
  }
  ...
}
```
New mutating functions (`submitJob`, `cancelJob`) must live in this file only (SC6). Per UI-SPEC's "Mutation error copy" section, the failure branch must additionally extract `body.detail.code` — an **object**, not the string `fetchApi` currently unwraps at lines 56-63 — and map it through the closed code table; this is new logic layered on the same `try/fetch/catch` skeleton, with `method: "POST"`, `Idempotency-Key`/`Content-Type` headers, and a JSON body — none of which exist in `api.ts` today (no prior POST call in this file).

Test precedent: `console/src/lib/api.test.ts` (whole file) — the `vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(...)))` pattern is the template for testing the new mutating functions.

---

### `console/src/lib/useApiQuery.ts` (modify: add polling)

**Analog:** same file (whole hook, 52 lines) — `runFetch`/`mountedRef`/`requestIdRef` stale-response-guard pattern. Add an optional `pollIntervalMs` parameter that triggers a **silent** background fetch (must not flip `loading`, per UI-SPEC's polling contract — background ticks must not flicker `FetchMeta`'s "Refreshing…" state) on an interval, gated on `!document.hidden` (pause-when-tab-hidden) and a caller-supplied is-terminal predicate that stops the interval. No existing `document.hidden` precedent in this codebase — this mechanism is net-new, layered onto the existing guarded-`.then()` idiom.

---

### `console/src/app/jobs/page.tsx` / `JobsTable.tsx` (new)

**Analog:** `console/src/app/runs/page.tsx` (whole file, 31 lines) for the page shell, and `console/src/components/runs/RunsTable.tsx` (whole file, 139 lines) for the table — `buildRunsEndpoint()` query-string builder, `useApiQuery<RunsResponse>`, three-way `!result / !result.ok / empty / rows` render branch, `statusColor()` mapping (34-39, replace with the closed 5-value JOBUI status→color map), and the `<Link href={...} className="text-xs font-semibold text-sky-400 hover:underline">View</Link>` row-action (124-129). UI-SPEC's differentiated empty-state copy (zero-jobs vs. zero-matches) is an enhancement over `RunsTable.tsx`'s single undifferentiated empty state (line 75) — not a straight copy. The "New Job" primary CTA has no button-styling precedent on the Runs screen; the closest button-class precedent in the codebase is `FetchMeta.tsx:28`, cited directly by UI-SPEC for the (unrelated, secondary-styled) Cancel trigger button — the accent-styled "New Job" CTA itself has no existing button markup to copy and should follow the Color contract's `sky-400` accent spec directly.

---

### `console/src/app/jobs/[jobId]/page.tsx` (new)

**Analog:** `console/src/app/runs/[runId]/page.tsx` (whole file, 65 lines) — verbatim structural template (fetch owned by the page via `use(params)`, header panel gets `loading`/`result`/`refetch` props, sibling panels gated on resolved data). **Note the one thing NOT to copy:** `RunDetailPage`'s `{run.run_type === "backtest" ? <BacktestAnalyticsSection .../> : null}` conditional (54-59) IS a run/job-type branch — the Job detail page must contain zero such conditionals (D-17/lookup-map discipline); every Job-detail panel is generic over job type.

---

### `console/src/components/jobs/detail/JobResultSummaryPanel.tsx` / `JobResourcesPanel.tsx` (new, generic renderers)

**Analog:** `console/src/components/strategy/StrategyOverviewPanel.tsx`'s `KeyValueSection` (24-56), copied near-verbatim (UI-SPEC explicitly cites this as the precedent). `JobResourcesPanel` additionally needs the `resources[].kind → route` lookup (map 2): render `{kind}: {id}` per row, and only when `kind === "strategy_run"` wrap it in a `<Link href={\`/runs/${id}\`} className="text-xs font-semibold text-sky-400 hover:underline">` — same `<Link>` styling as `RunsTable.tsx`'s "View" link (125-129).

---

### `console/src/components/jobs/CancelJobDialog.tsx` (new — no analog)

No existing dialog/modal in the console. UI-SPEC's constraint section is authoritative: build a plain React-state-controlled overlay (`role="dialog"`, `aria-modal="true"`, `zinc-950/80` backdrop, `zinc-900` panel / `zinc-800` border) — do not use the native `<dialog>` element (`HTMLDialogElement` is a no-op stub under the installed `jsdom@29.1.1`). Closest structural precedent for "derive a discrete UI state and branch the whole render tree on it" is `KillSwitchBanner.tsx`'s three-way conditional (43-80), though visually unrelated.

---

### `console/src/app/layout.tsx` (modify: Jobs nav link)

**Analog:** same file, existing `<Link>` list (37-48): `<Link href="/runs" className="text-zinc-400 hover:text-zinc-100">Runs</Link>` → add the identical shape for `/jobs`.

---

### `console/src/components/runs/detail/RunHeaderPanel.tsx` (modify: D-07 back-link)

**Analog:** same file, the `run_id` `<dd>` row (123-124):
```tsx
<dt className="text-zinc-500">Run ID</dt>
<dd className="break-all text-zinc-300">{run.run_id}</dd>
```
Add a `job_id`-gated row, rendered only when non-null, using `RunsTable.tsx`'s accent-link styling:
```tsx
{run.job_id ? (
  <>
    <dt className="text-zinc-500">Created by</dt>
    <dd className="text-zinc-300">
      <Link href={`/jobs/${run.job_id}`} className="text-xs font-semibold text-sky-400 hover:underline">
        Job {run.job_id}
      </Link>
    </dd>
  </>
) : null}
```
Also extend the `RunSummary` type (7-20) with `job_id: string | null`.

---

## SC6 Console Enforcement Test — No Existing Analog, One Known Trap

`find`/`grep` across `console/src` confirm **no console-side boundary/enforcement test exists today** (no `*boundary*`/`*enforce*` files; the only structural fences in the repo are the Python-side AST scans in `test_orchestration_boundaries.py`). The planner must write this test from scratch for SC6 ("no `fetch(` exists outside `console/src/lib/api.ts`", "Job list/detail/log/event components contain no job-type-specific branches").

**Known trap, verified directly against the current tree:** a naive `grep -l "fetch("` over `console/src` currently matches `console/src/components/KillSwitchBanner.tsx` (line 39: `refetch();`) and the docstring in `console/src/lib/useApiQuery.ts` (line 15: "a manual `refetch()`") — both are substring false-positives on `refetch(`, not real `fetch(` calls. `console/src/lib/api.ts:21` (`await fetch(...)`) is the one genuine call site. **Any new source-scan test must use a word-boundary-safe pattern** (e.g. a regex like `/\bfetch\(/` rather than a plain substring `includes("fetch(")`) or it will either false-fail on `KillSwitchBanner.tsx`/`useApiQuery.ts` today, or — worse — pass trivially while missing a real violation elsewhere later. The `job_type`-branch / map-1-import check (D-17/SC6) has the same shape (scan component source text or, more robustly, parse with a TS-aware tool) and the same false-positive risk for naive substring matching (e.g. a variable merely named `jobType` inside an unrelated string).

## D-10 `submission_defaults` — Do Not Reuse `resolve_backtest_window` As-Is

**Trap:** `services/backtesting.py`'s `resolve_backtest_window` (73-101) falls back to `date.today() - timedelta(days=1)` (the **host** date) when `latest_completed_session` returns `None` (lines 88-89: `if resolved_to is None: resolved_to = date.today() - timedelta(days=1)`). CONTEXT explicitly forbids this for D-10: `to_date` in the future must be judged against an exchange-calendar date via an injectable clock, never the host's local date, and the catalog resilience note requires **omitting** `submission_defaults` entirely (not falling back to a host-clock guess) when no sessions exist or the DB is unavailable.

**Correct pattern for `BacktestSubmissionSpec.submission_defaults()`:** call `services.market_data_access.latest_completed_session` directly (the same primitive `resolve_backtest_window` calls internally, lines 83-87), and if it returns `None` or raises, return `None` from `submission_defaults()` so the `/job-types` route's existing try/except (see the ORCH-07/D-20 pattern assignment above) omits the field for that entry — do not call `resolve_backtest_window` itself, and do not add a `date.today()` fallback anywhere in this new code path.

## Shared Patterns

### Query-param collection response envelope (backend)
**Source:** `src/trading_platform/api/dependencies.py`, `build_collection_response` (145-155) + `serialize_job_filters` (137-142)
**Apply to:** any new filterable/paginated list response. Not needed for `/job-types` (D-20's shape has no filters).

### Typed-exception-to-HTTPException mapping (backend)
**Source:** `src/trading_platform/api/routes/jobs.py`, `_error()` helper (62-63) + the `except SomeTypedError as exc: raise _error(...) from exc` chain (88-109, 131-151)
**Apply to:** any new submission-validation errors (D-09: unknown `strategy_id`, `from_date > to_date`, `to_date` in future, unknown payload keys) — raise these from `BacktestSubmissionSpec.validate_payload()` as `InvalidJobPayloadError(job_type=..., reason=...)`, already mapped to 422 `invalid_job_payload` (98-103); do not invent a new exception type. The console's mutation-error-copy table's `invalid_job_payload` row reads `exc.reason` directly, so keep reasons short and stable.

### Native Postgres enum for closed vocabularies (backend/DB)
**Source:** `src/trading_platform/db/models/job.py`'s `_enum_values()` helper (66-67) + `alembic/versions/0018_phase17_job_framework.py` (enum `.create()` calls, 36-48)
**Apply to:** any new closed enum this phase introduces (e.g. `cancellation_mode`, per Claude's discretion).

### Read-service transport-agnostic dict serialization (backend)
**Source:** `src/trading_platform/services/job_reads.py`, `_dt()`/`_enum_value()`/`_uuid_value()` helpers (283-293)
**Apply to:** `resources[]` serialization and any new `operator_reads.py` fields — never return an ORM object or raw enum member from a service method.

### `session_scope` + single-transaction FK write (backend)
**Source:** `src/trading_platform/services/backtesting.py`, `_create_backtest_run` (175-207)
**Apply to:** the `job_id` write into `_create_backtest_run` per D-02 — must happen inside the same `with session_scope(...)` block that creates the row, not a follow-up update.

### `useApiQuery` + `FetchMeta` + `ErrorState` three-way render branch (console)
**Source:** `console/src/components/runs/detail/RunHeaderPanel.tsx` (69-95) and `console/src/components/runs/RunsTable.tsx` (55-138)
**Apply to:** every new Job panel — the console's single established data-fetch-to-render idiom.

### Generic key/value dict rendering (console)
**Source:** `console/src/components/strategy/StrategyOverviewPanel.tsx`, `KeyValueSection` (24-56)
**Apply to:** `result_summary` and any other open-ended JSON dict — `Object.entries()` + `JSON.stringify()` for nested values, never a hand-enumerated field list.

### Status-color closed-map function (console)
**Source:** `console/src/components/runs/RunsTable.tsx`, `statusColor()` (34-39)
**Apply to:** the new Job status→color map — same small pure-function shape, but note the weight deviation UI-SPEC calls out (600 not 700) and the unpadded-badge markup difference; do not copy the `font-bold`/`px-2 py-0.5` styling verbatim.

### Nav link markup (console)
**Source:** `console/src/app/layout.tsx`, existing `<Link>` list (37-48)
**Apply to:** the new "Jobs" nav entry.

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `console/src/lib/jobTypeForms.ts` (map 1) | provider/lookup-map | transform | D-17's first job-type-keyed lookup map; no prior lookup-map module exists. |
| `console/src/lib/resourceRoutes.ts` (map 2) | provider/lookup-map | transform | Second and only other lookup map; UI-SPEC's "Lookup-map discipline" is authoritative. |
| `console/src/components/jobs/CancelJobDialog.tsx` | component (dialog) | request-response | No dialog/modal component exists anywhere in the console (verified by search); UI-SPEC pins the exact overlay markup and forbids the native `<dialog>` element. |
| `console/src/components/jobs/new/BacktestJobForm.tsx` | component (form) | request-response | No submission/mutation form exists in the console yet; build from UI-SPEC's Copywriting Contract directly. |
| `console/*.test.ts` (SC6 enforcement test) | test | n/a | No console-side boundary/enforcement test exists today (verified: no `*boundary*`/`*enforce*` files under `console/`); see the dedicated "SC6 Console Enforcement Test" section above for the false-positive trap to avoid. |
| `src/trading_platform/jobs/runner.py`'s D-22 mode-declaration mechanism | service (framework) | event-driven | No existing "declare a required mode per registered type, validate immediately before dispatch" pattern; CONTEXT explicitly flags the placement as open. The FAILED-transition *outcome* mirrors the existing unregistered-type branch (see Pattern Assignments above), but the mode-declaration mechanism itself (e.g. new registry metadata) is net-new. |

## Metadata

**Analog search scope:** `src/trading_platform/{db/models,jobs,orchestration,services,api,worker,core}`, `alembic/versions`, `tests/`, `console/src/{app,components,lib}`
**Files scanned:** ~50 read in full or targeted ranges, plus directory listings across `db/models`, `jobs`, `orchestration`, `services`, `worker/commands`, `api/routes`, `console/src/app`, `console/src/components`, `console/src/lib`
**Pinned tests read in full:** `tests/test_orchestration_boundaries.py`, `tests/test_job_import_boundary.py`; targeted excerpts from `tests/test_job_mutation_api.py`, `tests/test_job_mutation_e2e.py`, `tests/test_job_orchestration.py`, `tests/test_db_migrations.py`
**Pattern extraction date:** 2026-09-24 (revised after two rounds of advisor review — round 1: ORCH-07 placement, D-22 placement, `/job-types` import surface, blast radius, SC6 enforcement; round 2: `/job-types` mount path and router-collision fix verified against FastAPI source and `test_jobs_router_exposes_exact_allowed_methods`, FastAPI dependency-vs-body-validation ordering verified against installed `fastapi/routing.py` and `fastapi/dependencies/utils.py` source, fifth registry-emptiness tripwire in `tests/test_job_registry.py`, `.env`-file default-flag trap, softened handler-placement and D-22-mechanism-placement claims to open decisions)
