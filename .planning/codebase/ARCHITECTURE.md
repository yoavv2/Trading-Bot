<!-- refreshed: 2026-11-01 -->
# Architecture

**Analysis Date:** 2026-11-01

## System Overview

The repository holds one Python backend package (`src/trading_platform/`) that runs in two mutually exclusive modes selected by `research.mode`, plus a Next.js console (`console/`) that talks to the backend only through a same-origin proxy.

- **Active product path (research):** Strategy Research. Research routers under `src/trading_platform/api/research/`, research services under `src/trading_platform/services/research/`, research Job types, research database (`trading_research`), and console pages under `console/src/app/research/` and `console/src/components/research/`.
- **Shared infrastructure (used by research):** settings, database session and models, the Job framework (queue, lease, lifecycle, dependencies, cancellation), the generic `/api/v1/jobs` surface, `/health`, the calendar service, the Tiingo provider, and the generic `/jobs/*` console pages.
- **Frozen (trading):** the paper-trading, broker-execution, reconciliation, risk, and operator surfaces. Still in the tree and still importable, but not mounted in research mode and not reachable from `make dev`. Reachable only through `make dev-trading` or a non-research process.

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                 Next.js console  `console/src/app` (App Router)            │
│   research pages (active)  ·  /jobs/* (shared infra)  ·  legacy redirects │
│   fetchApi → `/backend/*`  (console/src/lib/api.ts)                        │
└───────────────┬───────────────────────────────────────────────────────────┘
                │ rewrite `/backend/:path*` → TRADING_CONSOLE_API_BASE_URL
                │ (console/next.config.ts; default http://127.0.0.1:8000)
                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  FastAPI app factory  `api/app.py` :: create_app(research_mode)            │
│  infrastructure routers (all modes): health, jobs, job_types              │
│  ┌──────────────────────────────┐  ┌────────────────────────────────────┐ │
│  │ research routers (active)    │  │ trading routers (frozen)           │ │
│  │ `api/research/*`             │  │ `api/routes/*` (except the three   │ │
│  │ mounted iff research.mode    │  │ infra routers) — mounted iff NOT   │ │
│  └──────────────┬───────────────┘  │ research.mode                      │ │
│                 │                  └──────────────┬─────────────────────┘ │
└─────────────────┼─────────────────────────────────┼───────────────────────┘
                  ▼                                 ▼
┌──────────────────────────────────┐  ┌───────────────────────────────────┐
│ orchestration (seam)             │  │ services (trading, frozen)        │
│ `orchestration/research_studies` │  │ `services/execution`, `reconcil…` │
│ `orchestration/job_mutations`    │  │ `services/risk`, `services/…`     │
└──────────────┬───────────────────┘  └───────────────┬───────────────────┘
               ▼                                      │
┌──────────────────────────────────┐                  │
│ domain services (research)       │                  │
│ `services/research/studies.py`   │                  │
│ + backtest/freeze/integrity/…    │                  │
└──────────────┬───────────────────┘                  │
               │ job_submitter seam (injected)        │
               ▼                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Job framework (shared)  `jobs/`: registry · queue · runner · lifecycle    │
│ `jobs/dependencies.py` · `jobs/cancellation.py` · `jobs/handlers/*`        │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ SQLAlchemy 2 sessions (`db/session.py`)
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ PostgreSQL 16  — `trading_research` (research/make dev) or              │
│ `trading_platform` (trading); schema via Alembic `alembic/versions/`     │
│ ORM: `db/models/research.py` (research) + one module per legacy table    │
└──────────────────────────────────────────────────────────────────────────┘
                                ▲
                                │ claim / renew / reclaim / transition
┌───────────────────────────────┴──────────────────────────────────────────┐
│ Worker `python -m trading_platform.worker run-jobs`                       │
│ `worker/__main__.py` → `worker/commands/run_jobs.py` → `jobs/runner.py`   │
│ registry chosen by mode: `jobs/registry.py::build_registry_for`           │
└──────────────────────────────────────────────────────────────────────────┘
```

## Pattern Overview

**Overall:** Modular monolith with a mode-selected composition root. One codebase, one package, two process "personalities" fixed at process start: the API mounts a different router set and the worker registers a different Job-type set, both derived from the same `Settings.research.mode` flag. Long-running research work is expressed as a Job graph in a PostgreSQL queue and executed by a polling worker. The console is a thin client over JSON endpoints.

**Key Characteristics:**
- **Mode is a process-level, load-time switch.** `create_app()` runs at import time (`src/trading_platform/api/app.py:155`, `app = create_app()`) and the lifespan refuses to start if the mode changed between construction and startup (`api/app.py:50-53`).
- **Two disjoint API surfaces** share only the infrastructure routers `(health_router, jobs_router, job_types_router)` (`api/app.py:100`). Research routers are `_RESEARCH_ROUTERS` (`api/app.py:118`); trading routers are `_TRADING_ROUTERS` (`api/app.py:104`).
- **Domain services never import the Job framework or the HTTP layer.** `services/research/` has zero imports from `trading_platform.jobs`, `trading_platform.orchestration`, or `trading_platform.api`. Job submission is injected: `StudyService(settings, job_submitter=...)` (`services/research/studies.py:368-373`), wired in `orchestration/research_studies.py::build_study_service`.
- **Handlers are thin adapters.** Job handlers in `jobs/handlers/research_jobs.py` construct a `StudyService` and call one method; all persistent writes live in the service.
- **Jobs run with no database session open.** `jobs/runner.py::execute_job` resolves and runs the handler outside any `session_scope` (stated at `jobs/runner.py:94-130` and the `handler.run(context)` call inside the try block).
- **Mutations are gated twice.** A global switch `orchestration.mutations_enabled` (default `False`, `core/settings.py:397`) is enforced per route by `require_mutations_enabled` (`api/dependencies.py:58`), and mutating Job submissions require an `Idempotency-Key` (`orchestration/job_mutations.py:254-259`).

## Layers

**Composition root / API bootstrap:**
- Purpose: build the FastAPI app for one mode, validate config, and build the Job registry on startup.
- Location: `src/trading_platform/api/app.py`
- Contains: `lifespan`, `create_app`, router tuples, the global exception handler `_unhandled_exception_handler`, and `main()` for direct uvicorn runs.
- Depends on: `core/settings.py`, `core/startup.py`, `core/logging.py`, `jobs/registry.py`, every router module.
- Used by: uvicorn (`trading_platform.api.app:app`) in `make dev`, `make api`, Compose, and the Dockerfile.

**HTTP routers (active, research):**
- Purpose: parse and validate requests, translate domain errors to typed HTTP error bodies, and call one orchestration or service object per request.
- Location: `src/trading_platform/api/research/`
  - `studies.py` — prefixes `/api/v1/research/studies` (`studies_router`) and `/api/v1/research/revisions` (`revisions_router`).
  - `strategies.py` — prefix `/api/v1/research/strategies` (drafts, versions, validate, approve, edit, duplicate).
  - `catalog.py` — prefixes `/api/v1/research/catalog` (`catalog_router`) and `/api/v1/research/asset-lists` (`asset_lists_router`).
  - `assistant.py` — prefix `/api/v1/research/assistant` (proposals, study proposals, apply).
- Depends on: `api/dependencies.py`, `orchestration/research_studies.py`, `services/research/*`.
- Used by: the research console pages through `/backend/api/v1/research/*`.
- Error contract: typed bodies `{"detail": {"code": ..., ...}}` built by `_error()` in `api/research/studies.py`. Unknown JSON keys are rejected with 422 `invalid_request` by `_read_body` (`api/research/studies.py`).

**HTTP routers (infrastructure, every mode):**
- Purpose: liveness/readiness, the generic Job surface, and the Job-type catalog.
- Location: `src/trading_platform/api/routes/health.py` (`/health` returns `mode: "research" | "trading"` at line 33; `/ready`), `api/routes/jobs.py` (`/api/v1/jobs` list/detail/progress/logs/events, `POST /api/v1/jobs` submit at line 88, cancel, retry), `api/routes/job_types.py`.
- Note: `POST /api/v1/jobs` is the same endpoint for both modes; the registry it resolves against decides which Job types exist.

**HTTP routers (frozen, trading):**
- Purpose: strategy catalog, analytics, runs, operator status, paper/execution operations, recovery, kill-switch controls, market data state.
- Location: `src/trading_platform/api/routes/` — `strategies.py`, `analytics.py`, `runs.py`, `operations.py`, `system.py`, `controls.py`, `recovery.py`, `execution_operations.py`, `market_data.py`.
- Mounted only when `research.mode` is false (`api/app.py:104-116`).

**Orchestration seam (shared):**
- Purpose: the only place that combines the Job framework with a domain service, and the idempotent Job submit/cancel/retry transaction.
- Location: `src/trading_platform/orchestration/`
  - `job_mutations.py` — `JobOrchestrationService` (`submit` at line 291, `cancel` at line 368, retry). Enforces Idempotency-Key, fingerprints the request, runs the admission hook, and inserts the Job through `jobs/dependencies.py::submit_job` inside one transaction.
  - `research_studies.py` — `build_study_service(settings)` returns `StudyService` wired with `submit_in_session` (`orchestration/research_studies.py:23-28`).
- Depends on: `jobs/*`, `db/models`, and `db/session`.

**Domain services (active, research):**
- Purpose: all research business rules — assets, strategy versions and drafts, studies and immutable revisions, readiness and preflight, freeze, backtests, evaluation ranking, final test, exposures, exports, and the Haiku assistant.
- Location: `src/trading_platform/services/research/`
  - `studies.py` (1616 lines; `StudyService` at line 368) — studies, revisions, readiness, Job graph construction, progress, freeze, final test, exposures, results, export.
  - `backtest.py`, `metrics.py`, `ranking.py`, `evidence.py`, `benchmark.py`, `report.py` — engine, metrics, ranking, evidence bundle, and report rendering.
  - `freeze.py`, `integrity.py`, `code_identity.py`, `environment.py` — input freeze, integrity checks, code SHA, process-level calendar pin.
  - `strategies.py`, `catalog.py`, `asset_lists.py`, `examples.py`, `bootstrap.py` — strategy versions, public asset catalog, asset lists, example seeds, DB bootstrap.
  - `assistant.py`, `study_assistant.py`, `budget.py` — Anthropic-backed assistant and its shared request budget ledger.
  - `tiingo_ingestion.py` — research bar ingestion via `services/tiingo.py`.
- Rule: no imports from `trading_platform.jobs`, `trading_platform.orchestration`, or `trading_platform.api` (verified by grep).

**Domain services (shared, used by research):**
- `services/calendar.py`, `services/market_data_access.py`, `services/tiingo.py`, `services/read_recording.py` (`canonical_json`, `sha256_hex`), `services/config/validation.py` (`ExecutionMode`, `config_failure_message`). Trading-side calendar helpers (`services/calendar_facts.py`) and mixed ingestion code (`services/ingestion.py`) are not part of the research path.

**Domain services (frozen, trading):**
- `services/execution/` (paper order submission, idempotency, send guard, operations), `services/reconciliation/`, `services/risk.py`, `services/portfolio.py`, `services/alpaca.py`, `services/recovery.py`, `services/operator_*.py`, `services/stale_runs.py`, `services/backtesting.py` (trading backtest), `services/analytics.py`, `services/attribution*.py`, `services/active_paper_strategy.py`, `services/broker_*.py`, `services/polygon.py` (trading market data).

**Job framework (shared):**
- Purpose: durable queue, lease-based claim, lifecycle transitions, dependencies, cancellation, progress and logs.
- Location: `src/trading_platform/jobs/`
  - `registry.py` — `JobRegistry`, `build_default_registry` (trading), `RESEARCH_JOB_TYPES` (line 370), `build_research_registry` (line 383), `build_registry_for` (line 437).
  - `queue.py` — `claim_next_job` (line 45, `SELECT … FOR UPDATE SKIP LOCKED` at line 85), `renew_lease` (104), `find_lost_job_ids` (133), `reclaim_lost_jobs` (150). Constants `LEASE_SECONDS = 60`, `HEARTBEAT_SECONDS = 20`, `POLL_INTERVAL_SECONDS = 2` (lines 40-42).
  - `runner.py` — `execute_job` (line 94) and `run_worker_loop` (line 369).
  - `lifecycle.py` — `apply_job_transition`, `JobTransitionRequest`, `IllegalJobTransition`; the runner's terminal writes and the lost-lease sweep both go through it. Cancellation acknowledgement has its own path (`acknowledge_cancellation`, `cancellation.py`).
  - `dependencies.py` — `submit_job` (line 263), `find_ready_job_ids` (338), `cascade_dependency_outcome` (358).
  - `cancellation.py` — `request_cancellation`, `acknowledge_cancellation` (240), `sweep_cancellation_timeouts` (310), `CANCELLATION_GRACE_SECONDS = 300` (line 59).
  - `contracts.py` — `JobHandler` protocol (line 128) and `JobContext` protocol (line 59); `JobCancelledError`, `JobDomainConflictError`.
  - `progress.py`, `context.py` (`DatabaseJobContext`), `handlers/` (see below).
- Rule: nothing in `queue.py`, `lifecycle.py`, `runner.py`, `dependencies.py`, or `cancellation.py` names a concrete Job type (`jobs/runner.py` module docstring).

**Job handlers and submission specs (research):**
- Location: `src/trading_platform/jobs/handlers/`
  - Research handlers: `research_jobs.py` (`ResearchFreezeJobHandler`, `ResearchBacktestJobHandler`, `ResearchEvaluateJobHandler`), `catalog_sync.py`, `ingest_tiingo_bars.py`, `sync_market_sessions.py`.
  - Research submission specs (pydantic `extra="forbid"`): `research_submission.py`, `catalog_sync_submission.py`, `ingest_tiingo_bars_submission.py`, `sync_market_sessions_submission.py`.
- The pair convention: `<name>.py` holds the handler (`job_type`, `required_execution_mode`, `run(context)`); `<name>_submission.py` holds the public input contract (`job_type`, `description`, `cancellation_mode`, `validate_payload`, `submission_defaults`). Both are registered together in `jobs/registry.py`.

**Job handlers (frozen, trading):**
- `backtest.py`, `broker_order_sync*.py`, `ingest_bars*.py`, `paper_session*.py`, `reconciliation*.py`, `record_external_activity*.py`, `risk_evaluation*.py`, `sync_symbol_metadata*.py`. Registered only in `build_default_registry` (`jobs/registry.py:269`).

**Persistence:**
- Purpose: engine/session lifecycle and ORM models.
- Location: `src/trading_platform/db/session.py` (`get_engine` line 70, `session_scope` line 96, `check_database_connection`, `clear_engine_cache`); `src/trading_platform/db/base.py`; `src/trading_platform/db/models/`.
- Engines and session factories are cached in module dicts `_ENGINE_CACHE` and `_SESSION_FACTORY_CACHE` keyed by `(database.url, database.echo)` — not process singletons, so tests can rebind the database.
- Research ORM models: `src/trading_platform/db/models/research.py` (403 lines) — tables `asset_catalog`, `asset_lists`, `asset_list_items`, `data_freezes`, `strategy_drafts`, `strategy_versions`, `ai_drafts`, `research_studies`, `study_revisions`, `research_freezes`, `test_window_exposures`, `research_run_links`, `provider_request_ledger`, `study_evaluations`, `study_revision_jobs`.
- Generic Job tables: `db/models/job.py`, `job_event.py`, `job_log.py`, `job_dependency.py`, `job_mutation.py`.

**Strategies:**
- Trading: `src/trading_platform/strategies/registry.py` (static registry), `trend_following_daily/`, `rsi_mean_reversion_daily/`, `donchian_breakout_daily/`, `time_series_momentum_daily/`, `base.py`, `signals.py`.
- Research: `src/trading_platform/strategies/research_registry.py` resolves approved `strategy_versions` rows into `DeclarativeDailyStrategy` (`strategies/declarative.py`). The declarative spec pipeline is `strategies/spec/` (`schema.py`, `validate.py`, `evaluate.py`, `explain.py`, `errors.py`). The static trading registry is untouched by the pivot.

**Core:**
- `src/trading_platform/core/settings.py` — typed settings, env overrides, YAML merge.
- `src/trading_platform/core/startup.py` — `enforce_startup_config` (line 47): validates config and DB reachability, then applies the research environment (line 69).
- `src/trading_platform/core/logging.py`, `log_sanitizer.py`, `clock.py`.

**Worker:**
- Entry: `src/trading_platform/worker/__main__.py` → `DISPATCH` in `worker/commands/__init__.py` → `worker/commands/run_jobs.py`. Parser in `worker/parser.py` defines five subcommands: `run-jobs` (research and trading Job executor), and the frozen trading CLI `report-backtest`, `report-strategy-analytics` (`worker/commands/backtest.py`), `operator-status`, `kill-switch-trip` (`worker/commands/operator.py`).
- The worker has no HTTP surface; its mode-awareness comes only from `build_registry_for(settings)` (`worker/commands/run_jobs.py`).

**Console (Next.js App Router):**
- Routes: `console/src/app/` — research: `research/studies/`, `research/studies/new/`, `research/studies/[studyId]/`, `research/strategies/`, `research/strategies/new/`, `research/strategies/drafts/[draftId]/`, `research/strategies/versions/[versionId]/`, `research/assets/`, `research/page.tsx` (redirect); Jobs (shared): `jobs/`, `jobs/new/`, `jobs/[jobId]/`; root `page.tsx` redirects to `/research/studies`.
- Components: `console/src/components/research/` (StudiesView, StudyWizard, StudyAssistant, StudyDetail, ResultsView, ValidationPanel, FinalTestPanel, StrategiesView, DraftEditor, VersionDetail, AssetsView, AssetPicker, ResearchGate, …); `console/src/components/jobs/` (shared Job table, detail, submit forms); `console/src/components/AppShell.tsx` (shell and navigation).
- Client libraries: `console/src/lib/api.ts` (`fetchApi`, `MutationResult`), `useApiQuery.ts`, `useResearchMode.ts` (reads `/health` `mode`), `useMutationCapability.ts`, `idempotencyKey.ts`, `navigation.ts`, `legacyRedirects.ts`, `research/*` (`preflight.ts`, `windows.ts`, `proposal.ts`, `types.ts`, `assets.ts`, `present.ts`).

## Data Flow

### Primary Request Path — research study submission (console to Job graph)

Example: operator starts an initial run for a study revision.

1. Console client posts to `/backend/api/v1/research/revisions/{revision_id}/run`. `fetchApi` prefixes `/backend` (`console/src/lib/api.ts`); `next.config.ts` rewrites it to the API base URL.
2. FastAPI resolves the route on `revisions_router` (`api/research/studies.py`, `POST /{revision_id}/run`). The route-level dependency `require_mutations_enabled` (`api/dependencies.py:58`) returns `403 mutations_disabled` unless `orchestration.mutations_enabled` is true. `make dev` sets it for the API process only.
3. The handler obtains its service through `get_study_service` → `build_study_service(get_settings(request))` (`api/research/studies.py`, `orchestration/research_studies.py:23-28`). `get_settings` reads `request.app.state.settings`, set in the lifespan (`api/app.py:58`).
4. `StudyService.run_initial` (`services/research/studies.py:976`) opens a session, takes the revision row lock, verifies readiness, and submits the initial Job graph through the injected `job_submitter` (`submit_in_session` → `jobs/dependencies.py::submit_job`, `orchestration/research_studies.py:18-20`). The graph, as submitted:
   - one `ingest-tiingo-bars` Job (ingest for the study's assets);
   - one `research-freeze` Job that depends on the ingest Job;
   - for each asset, for each window role in `INITIAL_WINDOWS` (`development`, `validation`), one `research-backtest` Job per strategy version plus one `research-backtest` benchmark Job;
   - one `research-evaluate` Job for the initial scope that depends on the backtest Jobs.
   Each submitted Job is recorded as a `StudyRevisionJob` row with its role and scope. The partial unique index `uq_study_revision_jobs_one_graph_per_scope` (`db/models/research.py:378-390`) is `UNIQUE (study_revision_id, scope) WHERE role = 'evaluate' AND is_rerun = false`. It makes one initial graph per revision a database fact under concurrent requests.
5. The route returns the created job references; the console then polls the generic Job surface and the revision progress route (`GET .../progress`).

Study endpoints do not use the `Idempotency-Key` header. Their single-graph guarantee comes from the database constraint in step 4. The generic `POST /api/v1/jobs` does require the header (see the next flow).

### Generic Job submission (any mode)

1. `POST /api/v1/jobs` (`api/routes/jobs.py:88`) passes `require_mutations_enabled`, then `get_job_orchestration_service` (`api/dependencies.py`), which builds `JobOrchestrationService(settings, get_job_registry(request))`.
2. `JobOrchestrationService.submit` (`orchestration/job_mutations.py:291`):
   - validates the `Idempotency-Key` (`_validate_idempotency_key`, lines 254-259; max 255 characters);
   - resolves the handler and its submission spec from the registry (`resolve`, `resolve_submission_spec`) — an unknown or unregistered type is refused here;
   - runs `spec.validate_payload(payload)` and fingerprints `{job_type, payload}`;
   - inside one `session_scope`, returns the stored outcome if the key already exists for `POST:/api/v1/jobs` (`SUBMIT_ENDPOINT_ID`, line 38); otherwise runs the admission hook (`_run_admission_check`) and inserts the Job plus a `JobMutation` row in a nested transaction. A named unique-constraint violation replays the first outcome.
3. Replays return `replayed: true`. A reused key with a different fingerprint raises `IdempotencyConflictError`.

### Worker execution path (claim, run, land)

1. `python -m trading_platform.worker run-jobs` → `run_jobs_command` (`worker/commands/run_jobs.py`): `enforce_startup_config(mode=BACKTEST)` (`core/startup.py:47`), then `build_registry_for(settings)` (`jobs/registry.py:437`) picks the research set (`RESEARCH_JOB_TYPES`, `jobs/registry.py:370`) or the trading set.
2. `run_worker_loop` (`jobs/runner.py:369`) installs SIGTERM/SIGINT handlers. Each iteration, in one transaction:
   - `reclaim_lost_jobs(session)` (`jobs/runner.py:428`, `jobs/queue.py:150`) moves RUNNING Jobs whose `lease_expires_at` is in the past to a terminal state through `apply_job_transition`;
   - `sweep_cancellation_timeouts(session)` (`jobs/cancellation.py:310`) finalizes cancellations past the grace period;
   - `claim_next_job(session, worker_id=…)` (`jobs/queue.py:45`) selects the oldest ready Job with `FOR UPDATE SKIP LOCKED` (`queue.py:85`), sets `lease_owner`, `lease_expires_at = now + 60 s`, and `heartbeat_at`.
   - Ready means "all dependencies satisfied" (`find_ready_job_ids`, `jobs/dependencies.py:338`).
3. `execute_job` (`jobs/runner.py:94`) reads the Job's `job_type` and `payload`, closes the session, resolves the handler, then runs the optional `preflight` (mode-validity check for the handler's `required_execution_mode`).
4. A daemon thread renews the lease every 20 s (`renew_lease`, `jobs/queue.py:104`). If renewal reports the lease lost, the handler's terminal write is skipped because a sweep has already recorded the outcome.
5. `handler.run(context)` runs with no DB session open. The handler reports progress and logs through `DatabaseJobContext` (`jobs/context.py`) and checks cooperative cancellation at step boundaries.
6. Outcome mapping, all through `apply_job_transition` (`jobs/lifecycle.py`):
   - normal return → `SUCCEEDED` with `result_summary`;
   - `JobCancelledError` → `acknowledge_cancellation` (`jobs/cancellation.py:240`);
   - `JobDomainConflictError` → domain-conflict failure, uncertain flag from the exception;
   - any other exception → `FAILED / HANDLER_ERROR`, forced to `outcome_uncertain` when the Job logged an `external_*` event (the "external side effect may have happened" rule).
   On any FAILED or CANCELLED outcome, `cascade_dependency_outcome` (`jobs/dependencies.py:358`) updates unstarted dependents in the same transaction.
7. A worker crash leaves the lease to expire; the next sweep on any worker reclaims the Job. `make dev` sends SIGINT to the worker with a 30 s timeout (`Makefile`, `WORKER_CMD`), so an in-flight Job normally finishes before the kill.

### Research Job handler (example: freeze)

1. The `research-freeze` Job's `run(context)` (`jobs/handlers/research_jobs.py:20-30`) constructs `StudyService(settings)` with no submitter (handlers never submit Jobs).
2. It calls `perform_freeze(revision_id, job_id=context.job_id)` (`services/research/studies.py:1069`), which writes `research_freezes` and `data_freezes` rows.
3. `ResearchFreezeJobHandler` returns a report dict; `execute_job` persists it as `result_summary`.

### Console data loading

1. Components call `useApiQuery<T>("/api/v1/research/...")` with an un-prefixed API path (for example `console/src/components/research/StudyDetail.tsx`). `useApiQuery` (`console/src/lib/useApiQuery.ts`) delegates to `fetchApi` (`console/src/lib/api.ts`). Results are an explicit `ApiSuccess | ApiFailure` union that always names the endpoint.
2. Every research page renders inside `ResearchGate` (`console/src/components/research/ResearchGate.tsx`), which calls `useResearchMode()` → `GET /backend/health` and reads `mode`. A trading-mode or unreachable API shows an explanatory alert instead of research panels.
3. Generic Job submissions send an `Idempotency-Key` header (`console/src/lib/idempotencyKey.ts`). Study mutations do not use the header. Mutation failures are mapped from the typed `detail.code` (`MutationResult` in `console/src/lib/api.ts`).

### State Management

- Server state is the PostgreSQL database. There is no server-side in-memory cache of research results.
- Immutable revisions: a study revision is a settings snapshot; changing settings creates a new revision (`services/research/studies.py` module docstring).
- Job state is the `jobs` row plus `job_events`; all status changes go through `apply_job_transition`.
- Console state is per-page React state and `useApiQuery` results. `useLastKnownData.ts` keeps the last successful payload when a refresh fails.

## Key Abstractions

**`JobRegistry` (mode-selected handler set):**
- Purpose: maps a `job_type` string to a runner handler and, optionally, a public submission spec.
- Examples: `src/trading_platform/jobs/registry.py` (class at line 178; `register`, `resolve`, `resolve_submission_spec`, `list_job_types`).
- Pattern: explicit registration with duplicate and mismatch validation. Runner-only handlers have no submission spec and are not publicly submittable.

**`JobHandler` / `JobContext` protocols:**
- Purpose: the only contract a Job type implements to run.
- Examples: `src/trading_platform/jobs/contracts.py` (`JobContext` line 59, `JobHandler` line 128); `src/trading_platform/jobs/handlers/research_jobs.py`.
- Pattern: class attributes `job_type` and `required_execution_mode`, plus `run(context)`. Optional duck-typed spec attributes (`cancellation_mode`, `recovery_gated`, `check_admission`, `lock_admission`) are read with `getattr` and are deliberately not Protocol members.

**`StudyService` (research domain facade):**
- Purpose: one entry point for every research use case; takes a `Settings` and an optional `job_submitter`.
- Examples: `src/trading_platform/services/research/studies.py:368`.
- Pattern: each method opens its own `session_scope`, locks rows where needed, and returns plain dicts (`study_to_dict`, `revision_to_dict`, …). Errors are `StudyError` subclasses with `status`, `code`, and `detail` (lines 120-175), translated by `_translate` in the router.

**`JobOrchestrationService` (transport-neutral submit/cancel/retry):**
- Purpose: idempotent Job submission used by the generic HTTP route.
- Examples: `src/trading_platform/orchestration/job_mutations.py:246`.
- Pattern: validates the key, resolves the spec, fingerprints, and stores a `JobMutation` row keyed by `(endpoint_id, idempotency_key)`.

**`DeclarativeDailyStrategy` and `CompiledSpec` (research strategies):**
- Purpose: a strategy version stored as a validated declarative spec, compiled to an executable strategy.
- Examples: `src/trading_platform/strategies/research_registry.py`, `src/trading_platform/strategies/declarative.py`, `src/trading_platform/strategies/spec/validate.py`.
- Pattern: `validate_spec` returns a `CompiledSpec`; `UnknownStrategyVersionError` for a missing version id.

**`Settings` (typed, layered configuration):**
- Purpose: one typed tree for every process.
- Examples: `src/trading_platform/core/settings.py` — `Settings` (~line 476), `EnvironmentOverrides` (~line 493), `build_settings_payload` (merge order below), `load_settings` (~line 668, `@lru_cache(maxsize=1)`).
- Pattern: merge order is Pydantic defaults, then `config/app.yaml`, then `config/strategies/*.yaml`, then `TRADING_PLATFORM_*` environment variables (`env_prefix="TRADING_PLATFORM_"`, `env_nested_delimiter="__"`). A `.env` file is read by the settings loader when present; its contents are not documented here. `TIINGO_API_KEY` is an un-prefixed alias (`_apply_tiingo_key_alias`).

**`TestWindowExposure` / `ResearchFreeze` (research integrity):**
- Purpose: record every final-test run as an exposure to the held-out window and freeze the inputs before that test.
- Examples: `src/trading_platform/db/models/research.py:256` and `:231`; `services/research/freeze.py`, `services/research/integrity.py`.
- Pattern: a rerun must reproduce the original `results_digest`; a difference is recorded as `reproducibility_failure` (per `services/research/studies.py` module docstring).

## Entry Points

**API application (research or trading, chosen by settings):**
- Location: `src/trading_platform/api/app.py` (`app = create_app()` at line 155; `main()` for direct runs).
- Triggers: `uvicorn trading_platform.api.app:app` (see Commands below).
- Responsibilities: validate config and DB (`enforce_startup_config`), build the job registry if none injected, mount the mode's routers, set `app.state.settings`, `started_at`, `bootstrapped`.

**Worker (Job executor):**
- Location: `src/trading_platform/worker/__main__.py` (`main`), dispatched to `src/trading_platform/worker/commands/run_jobs.py`.
- Triggers: `python -m trading_platform.worker run-jobs` (`make worker`, `make dev`, Compose `worker` service, `WORKER_CMD` under watchfiles).
- Options: `--once`, `--max-jobs`, `--worker-id`, `--compact` (see `worker/parser.py`).

**Research bootstrap (one-time, idempotent):**
- Location: `scripts/bootstrap_research.py` → `scripts/create_research_db.py` (creates and migrates the database, refuses `trading_platform`) → `src/trading_platform/services/research/bootstrap.py::bootstrap_research_database` (example strategy versions, public asset catalog, exchange sessions from `RESEARCH_CALENDAR_START`).
- Triggers: `make research-bootstrap`.

**Migrations:**
- Location: `scripts/migrate.py` (`make migrate`, runs `upgrade head`); `alembic/env.py`; revisions in `alembic/versions/` (33 revision files; the research line is `0031_research_platform` → `0032_research_studies` → `0033_research_assistant`).
- Dockerfile `CMD` also runs `alembic upgrade head` before uvicorn.

**Console:**
- Location: `console/src/app/layout.tsx` (root layout, `AppShell`), `console/src/app/page.tsx` (redirects to `/research/studies`).
- Triggers: `make console` or `npm run dev` inside `console/`.
- Redirect table: `console/src/lib/legacyRedirects.ts`, applied by `console/next.config.ts`.

## Commands and Process Topology

| Command | Mode | Processes | Notes |
| --- | --- | --- | --- |
| `make dev` | research | API (uvicorn `--reload`, mutations on), worker (watchfiles), console | Canonical host development. `RESEARCH_ENV` forces `TRADING_PLATFORM_RESEARCH__MODE=true`, `TRADING_PLATFORM_DATABASE__NAME=trading_research`, calendar start. Fails if :8000 or :3000 is busy. |
| `make dev-trading` | trading | API (mutations on), worker, console | Frozen trading surface; uses the `.env` database. |
| `make api` / `make worker` | research | one process each | Same `RESEARCH_ENV`; mutations off for `make api` (app default). |
| `make research-bootstrap` | research | one script | Creates and seeds `trading_research`. |
| `make up` | trading (by default) | `migrate`, `api`, `worker`, plus `db` | Compose `stack` profile. Sets `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=true` on `api` but does not set `TRADING_PLATFORM_RESEARCH__MODE`. |
| `docker compose up` | none | `db` only | Stack services are behind the `stack` profile. |
| Dockerfile `CMD` | trading (by default) | uvicorn on `$PORT` | `alembic upgrade head && uvicorn trading_platform.api.app:app`. Research mode is not set by the image. |

**Important:** the `make up` Compose stack and the Dockerfile image run the **trading** surface unless `TRADING_PLATFORM_RESEARCH__MODE=true` is supplied at deploy time. The default is `research.mode: false` (`src/trading_platform/core/settings.py:466`). `render.yaml` defines the `trading-platform-api` service and contains no research setting.

## Architectural Constraints

- **Threading:** the API is FastAPI with synchronous `def` route handlers (run in FastAPI's threadpool) and `async def` handlers only where the body must be read (`_read_body`, `api/research/studies.py`). The worker is single-threaded at the loop level; each executing Job gets one daemon heartbeat thread (`jobs/runner.py`, `threading.Thread(... daemon=True)`). Signal handlers are installed only inside `run_worker_loop`.
- **Process-level mode:** `research.mode` is read at import (`create_app()`), at startup (`enforce_startup_config`), and when the worker builds its registry. Changing it requires a restart; the lifespan check (`api/app.py:50-53`) refuses to serve when construction and startup disagree.
- **Global state (module level, deliberate):**
  - `_ENGINE_CACHE` and `_SESSION_FACTORY_CACHE` in `src/trading_platform/db/session.py` (keyed by database URL).
  - `load_settings` is cached with `@lru_cache(maxsize=1)` (`src/trading_platform/core/settings.py:667`).
  - The exchange calendar's pinned start is process-global: `apply_research_environment` (`src/trading_platform/services/research/environment.py`) calls `pin_calendar_start` from `enforce_startup_config` (`core/startup.py:69`) and clears it in trading mode.
  - `app = create_app()` (`api/app.py:155`) is a module-level application object.
- **Circular imports:** `jobs/registry.py` imports handler modules inside `build_default_registry` and `build_research_registry` because those modules import `InvalidJobPayloadError` and `JobCancellationMode` from the registry module (documented in the function docstring).
- **Layering rule:** `services/research/` must not import `jobs`, `orchestration`, or `api`. Submission goes through the injected `job_submitter`. `jobs/runner.py` must not name Job types.
- **No DB session across external calls:** `jobs/runner.py::execute_job` runs `handler.run` outside any `session_scope`. Research handlers rely on `StudyService` opening its own short transactions.

## Anti-Patterns

### Mode-blind Job registry fallback

**What happens:** `api/dependencies.py::get_job_registry` (lines 75-79) returns `build_default_registry(...)` (the trading Job set) when `app.state.job_registry` is unset. `get_strategy_registry` in the same file always builds the static trading strategy registry (`strategies/registry.py`), whatever the mode.
**Why it's wrong:** a request handled by a process whose app state was not populated by the lifespan would resolve research routes against the trading Job set. In normal serving the lifespan sets `app.state.job_registry` first (`api/app.py:56-57`), so the fallback is latent rather than hit on served requests.
**Do this instead:** derive the fallback from the mode, using `build_registry_for(get_settings(request))` (`jobs/registry.py:437`). New research dependencies should read the research registry, not `get_strategy_registry`. Strategy resolution for research goes through `strategies/research_registry.py`, keyed by `strategy_versions` ids.

### Mode enforced only by router inclusion

**What happens:** the mode is enforced by which routers are mounted (`api/app.py:104-125`) and which registry the worker builds, not by a check inside each handler.
**Why it's wrong:** a route added to the wrong tuple, or a shared helper that calls a trading service, runs in both modes without a guard.
**Do this instead:** add research endpoints only to `_RESEARCH_ROUTERS` and research Job types only to `RESEARCH_JOB_TYPES` (`jobs/registry.py:370`). `tests/test_research_api_isolation.py` covers this boundary.

## Error Handling

**Strategy:** typed domain exceptions, translated to HTTP at the router boundary, with a global last-resort handler.

**Patterns:**
- Domain errors carry `status`, `code`, and `detail`: `StudyError` and subclasses (`services/research/studies.py:120-175`), `JobSubmissionConflictError` (`jobs/registry.py`), `InvalidJobPayloadError`, and the mutation errors in `orchestration/job_mutations.py`.
- Routers translate them with `_translate` / `_error` into `HTTPException(detail={"code": ...})` (`api/research/studies.py`).
- The app-level handler returns `{"detail": {"code": "internal_error"}}` for anything unhandled and logs the exception (`api/app.py::_unhandled_exception_handler`).
- Job failures are never swallowed: the runner maps every exception to a terminal Job state (`jobs/runner.py::execute_job`). Failure messages are truncated to 2000 characters and tracebacks are logged, not stored.
- Config failures stop the process: `enforce_startup_config` raises `SystemExit` (`core/startup.py:47-58`).
- Research routers parse JSON bodies by hand through `_read_body` (`api/research/studies.py`), which rejects unknown keys with a 422 `invalid_request` body. Reuse it, or an equivalent closed-key helper, for JSON objects with a fixed key set rather than adding a new parser.

## Cross-Cutting Concerns

**Logging:** structured JSON logs via `core/logging.py` (`configure_logging`, `get_logger`), with `core/log_sanitizer.py` masking values. Event names are snake_case (`application_started`, `job_runner_config_invalid`, `worker_run_jobs_completed`). Job-visible logs are written to the `job_logs` table through `DatabaseJobContext.log` and exposed at `GET /api/v1/jobs/{id}/logs`.

**Validation:** Pydantic models at three levels — settings (`core/settings.py`), Job payload contracts (`jobs/handlers/*_submission.py`, `extra="forbid"`), and research study settings (`services/research/studies.py::StudySettings`, lines 182-246). Strategy specs are validated by `strategies/spec/validate.py`.

**Authentication:** none in the application layer. The API has no login or token check; access control is network-level and the mutation switch `orchestration.mutations_enabled`. The Tiingo key is sent only as an `Authorization: Token` header (`core/settings.py`, `TiingoProviderSettings`); the Anthropic key is read from settings for the assistant (`ResearchAiSettings`).

**Budgets:** the Tiingo and assistant request budgets are enforced in a shared database ledger (`services/research/budget.py`, `provider_request_ledger` table) so every process shares one count.

**Idempotency:** generic Job submission uses `Idempotency-Key` and the `JobMutation` table (`orchestration/job_mutations.py`); the console generates the key (`console/src/lib/idempotencyKey.ts`). Study graphs use the partial unique index (`db/models/research.py:385`).

---

*Architecture analysis: 2026-11-01*
