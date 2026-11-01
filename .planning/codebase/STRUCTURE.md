# Codebase Structure

**Analysis Date:** 2026-11-01

Tags used below: **active (research)** = on the product path; **shared infra** = used by research and trading; **frozen (trading)** = legacy trading/execution code, kept in the tree, not mounted in research mode, not reachable from `make dev`.

## Directory Layout

```
Trading-Bot-Project/
├── src/trading_platform/        # Python backend package (all server code)
│   ├── api/                     # FastAPI app factory, dependencies, routers
│   │   ├── app.py               # create_app(), lifespan, router tuples (shared infra + mode switch)
│   │   ├── dependencies.py      # get_settings, require_mutations_enabled, service factories (shared infra)
│   │   ├── research/            # active (research): /api/v1/research/* routers
│   │   └── routes/              # health, jobs, job_types = shared infra; the rest frozen (trading)
│   ├── core/                    # settings, startup gate, logging, clock (shared infra)
│   ├── db/                      # session.py, base.py, models/ (research.py + one file per table)
│   ├── jobs/                    # Job framework: registry, queue, runner, lifecycle, dependencies, cancellation
│   │   └── handlers/            # Job handlers + *_submission.py public contracts (paired)
│   ├── orchestration/           # Seam: job_mutations.py (shared), research_studies.py (active research)
│   ├── services/                # Domain services (flat modules + subpackages)
│   │   ├── research/            # active (research): studies, backtest, freeze, ranking, assistant, catalog…
│   │   ├── execution/           # frozen (trading): paper order submission and operations
│   │   ├── reconciliation/      # frozen (trading): broker/account reconciliation
│   │   ├── config/              # shared infra: validation (ExecutionMode), secrets, tolerances
│   │   └── *.py                 # mixed: calendar, market_data_access, tiingo = shared; risk/portfolio/alpaca/… = frozen (trading)
│   ├── strategies/              # research_registry.py + spec/ (active); registry.py + 4 strategy packages (frozen)
│   └── worker/                  # CLI entry (__main__.py), parser.py, commands/ (run-jobs = shared infra)
├── console/                     # Next.js 16 App Router console (TypeScript, React 19, Tailwind)
│   ├── src/app/                 # route tree (App Router pages and layout)
│   │   ├── research/            # active (research)
│   │   └── jobs/                # shared infra (generic Job surface)
│   ├── src/components/          # PascalCase React components, grouped by feature
│   │   ├── research/            # active (research)
│   │   ├── jobs/                # shared infra
│   │   └── paper/ runs/ strategy/ controls/ status/ shortcuts/  # frozen (trading) screens
│   ├── src/lib/                 # API client, hooks, navigation, redirects; research/ = active
│   └── public/                  # static assets
├── alembic/                     # Migrations: env.py, script.py.mako, versions/ (33 revisions, 0001 … 0033)
├── config/                      # YAML configuration
│   ├── app.yaml                 # base application settings (merged first)
│   ├── strategies/              # frozen (trading): one YAML per trading strategy
│   └── research/examples/       # active (research): example strategy specs
├── scripts/                     # Operational scripts (migrate, bootstrap_research, create_research_db, …)
├── tests/                       # pytest suite (flat test_*.py) + fixtures/ + support/
├── docs/                        # not present at the root; product docs live in README.md and .planning/
├── .planning/                   # GSD planning docs, research strategy plan (research/), codebase maps
├── .claude/                     # local Claude settings and worktrees (not product code)
├── scratchpad/                  # ad-hoc research outputs (backtest-reports/, research/)
├── Makefile                     # canonical commands (make dev, make research-bootstrap, make test …)
├── docker-compose.yml           # db (default) + migrate/api/worker behind the `stack` profile
├── Dockerfile                   # image: alembic upgrade head, then uvicorn trading_platform.api.app:app
├── render.yaml                  # Render service definition (trading-platform-api)
├── pyproject.toml               # Python project metadata, dependencies, ruff and mypy config
├── alembic.ini                  # Alembic configuration
└── .pre-commit-config.yaml      # local hooks
```

## Directory Purposes

**`src/trading_platform/api/`:**
- Purpose: HTTP surface. The app factory and mode-selected router tuples live in `app.py`.
- Contains: FastAPI routers, request/response handling, error translation, dependency providers.
- Key files: `src/trading_platform/api/app.py` (factory and mode switch), `src/trading_platform/api/dependencies.py` (`get_settings`, `require_mutations_enabled`, `get_job_orchestration_service`), `src/trading_platform/api/routes/health.py` (`/health` returns `mode`), `src/trading_platform/api/routes/jobs.py` (generic Job API).

**`src/trading_platform/api/research/`** (active, research):
- Purpose: all research HTTP routes. Mounted only when `research.mode` is true.
- Key files:
  - `src/trading_platform/api/research/studies.py` — studies, revisions, run, freeze, final test, results, export (`studies_router`, `revisions_router`).
  - `src/trading_platform/api/research/strategies.py` — drafts, versions, validate, approve, edit, duplicate.
  - `src/trading_platform/api/research/catalog.py` — public asset catalog (`catalog_router`) and asset lists (`asset_lists_router`).
  - `src/trading_platform/api/research/assistant.py` — Haiku proposals and study proposals.
  - `src/trading_platform/api/research/__init__.py` — package marker.

**`src/trading_platform/api/routes/`** (mixed):
- Shared infra: `health.py`, `jobs.py`, `job_types.py`.
- Frozen (trading): `strategies.py`, `analytics.py`, `runs.py`, `operations.py`, `system.py`, `controls.py`, `recovery.py`, `execution_operations.py`, `market_data.py`.

**`src/trading_platform/core/`** (shared infra):
- Purpose: typed configuration and process startup.
- Key files: `settings.py` (all settings classes, env override model, `load_settings`, `build_settings_payload`), `startup.py` (`enforce_startup_config`), `logging.py`, `log_sanitizer.py`, `clock.py`.

**`src/trading_platform/db/`** (shared infra):
- Purpose: engine/session lifecycle and ORM models.
- Key files: `session.py` (`get_engine`, `session_scope`, `check_database_connection`), `base.py` (declarative `Base`, `TimestampedModel`).
- `db/models/` — one module per table. Research tables are all in `db/models/research.py`. Trading tables use one module each (`job.py`, `job_event.py`, `order_event.py`, `execution_operation.py`, `daily_bar.py`, …). `db/models/__init__.py` re-exports every model; import from `trading_platform.db.models`.

**`src/trading_platform/jobs/`** (shared infra):
- Purpose: durable Job queue and runner. Research and trading Job types plug in through the registry only.
- Key files: `registry.py` (`build_default_registry`, `RESEARCH_JOB_TYPES`, `build_research_registry`, `build_registry_for`), `queue.py`, `runner.py`, `lifecycle.py`, `dependencies.py`, `cancellation.py`, `progress.py`, `context.py`, `contracts.py`.
- `jobs/handlers/` — paired files per Job type:
  - Research (active): `research_jobs.py`, `research_submission.py`, `catalog_sync.py`, `catalog_sync_submission.py`, `ingest_tiingo_bars.py`, `ingest_tiingo_bars_submission.py`, `sync_market_sessions.py`, `sync_market_sessions_submission.py`.
  - Frozen (trading): `backtest.py`, `backtest_submission.py`, `broker_order_sync*.py`, `ingest_bars*.py`, `paper_session*.py`, `reconciliation*.py`, `record_external_activity*.py`, `risk_evaluation*.py`, `sync_symbol_metadata*.py`.
  - Shared helper: `payload_fields.py` (`map_validation_error`), `domain_conflicts.py`.

**`src/trading_platform/orchestration/`** (shared infra + active research seam):
- Key files: `job_mutations.py` (`JobOrchestrationService`: idempotent submit, cancel, retry), `research_studies.py` (`build_study_service`).

**`src/trading_platform/services/`** (domain logic, mixed):
- `services/research/` (active research) — 20 modules, see Key File Locations.
- `services/calendar.py`, `services/market_data_access.py`, `services/tiingo.py` (research-only consumer), `services/read_recording.py` — shared infra used by research.
- `services/calendar_facts.py`, `services/symbol_metadata_sync.py`, `services/symbol_readiness.py`, `services/ingestion.py` — mixed or trading-side; check imports before editing (`ingestion.py` is imported by `services/research/tiingo_ingestion.py` and by the trading `ingest_bars` handler).
- `services/config/` — shared infra: `validation.py` (`ExecutionMode`, `config_failure_message`), `secrets.py`, `tolerances.py`.
- `services/execution/`, `services/reconciliation/`, and `services/{alpaca,broker_jobs,broker_status,risk,portfolio,recovery,account_baseline,active_paper_strategy,paper_account_checks,operator_controls,operator_reads,operator_status,operation_reads,external_snapshot,external_activity,attribution,attribution_inputs,analytics,backtesting,backtest_reporting,batch_outcomes,stale_runs,polygon,concurrency_guard,evaluation_manifest,symbol_metadata_sync,symbol_readiness}.py` — frozen (trading). `services/job_reads.py` is read-side Job querying used by the shared `/api/v1/jobs` routes, so it is shared infra.

**`src/trading_platform/services/research/`** (active, research; 20 modules):
- `studies.py` — `StudyService` (1616 lines): studies, revisions, readiness, Job graphs, freeze, final test, exposures, results, export. Largest module in the package.
- `backtest.py`, `metrics.py`, `ranking.py`, `evidence.py`, `benchmark.py`, `report.py` — engine, metrics, ranking, evidence, benchmark, and report rendering.
- `freeze.py`, `integrity.py`, `code_identity.py`, `environment.py` — input freeze, integrity checks, code identity, process calendar pin.
- `strategies.py`, `examples.py`, `catalog.py`, `asset_lists.py` — strategy versions, examples, public asset catalog, asset lists.
- `assistant.py`, `study_assistant.py`, `budget.py` — Haiku assistant, study assistant, shared budget ledger.
- `tiingo_ingestion.py`, `bootstrap.py` — research bar ingestion and database bootstrap.

**`src/trading_platform/strategies/`** (mixed):
- Active: `research_registry.py` (resolves `strategy_versions` into `DeclarativeDailyStrategy`), `declarative.py`, `spec/` (`schema.py`, `validate.py`, `evaluate.py`, `explain.py`, `errors.py`).
- Frozen (trading): `registry.py`, `base.py`, `signals.py`, `trend_following_daily/`, `rsi_mean_reversion_daily/`, `donchian_breakout_daily/`, `time_series_momentum_daily/` (each with `__init__.py` and `strategy.py`).

**`src/trading_platform/worker/`** (shared infra for `run-jobs`):
- `__main__.py` — `main()` routing only.
- `parser.py` — argparse definitions for five subcommands.
- `commands/__init__.py` — `DISPATCH` mapping.
- `commands/run_jobs.py` — shared infra (research and trading Job executor).
- `commands/backtest.py`, `commands/operator.py` — frozen (trading) CLI handlers.

**`console/src/app/`** (route tree, App Router):
- Root layout: `layout.tsx` (fonts, `AppShell`, metadata "Strategy Research"); `globals.css`; `page.tsx` (redirects to `/research/studies`); `favicon.ico`.
- Research (active): `research/page.tsx` (redirect), `research/studies/page.tsx`, `research/studies/new/page.tsx`, `research/studies/[studyId]/page.tsx`, `research/strategies/page.tsx`, `research/strategies/new/page.tsx`, `research/strategies/drafts/[draftId]/page.tsx`, `research/strategies/versions/[versionId]/page.tsx`, `research/assets/page.tsx`.
- Jobs (shared infra): `jobs/page.tsx`, `jobs/new/page.tsx`, `jobs/[jobId]/page.tsx`.
- Legacy URLs (`/`, `/strategy`, `/runs`, `/paper`, `/controls`) are not route folders; they are permanent redirects in `console/next.config.ts` from `console/src/lib/legacyRedirects.ts`.

**`console/src/components/`**:
- `research/` (active): `StudiesView.tsx`, `StudyWizard.tsx`, `StudyAssistant.tsx`, `StudyDetail.tsx`, `ReadinessPanel.tsx`, `ProgressPanel.tsx`, `ValidationPanel.tsx`, `FinalTestPanel.tsx`, `ResultsView.tsx`, `CurveChart.tsx`, `ResearchPeriod.tsx`, `AssistantPanel.tsx`, `StrategiesView.tsx`, `DraftEditor.tsx`, `VersionDetail.tsx`, `AssetsView.tsx`, `AssetPicker.tsx`, `ResearchGate.tsx`.
- `jobs/` (shared infra): `JobsTable.tsx`, `JobFilters.tsx`, `CancelJobDialog.tsx`, `RetryJobDialog.tsx`, `AutoRefreshIndicator.tsx`, `types.ts`, `detail/` (`JobDetailView.tsx`, `JobHeaderPanel.tsx`, `JobResourcesPanel.tsx`, `JobResultSummaryPanel.tsx`), `new/` (per-type submit forms such as `BacktestJobForm.tsx`, `IngestBarsJobForm.tsx`, `jobFormKit.tsx`).
- Top level (shared): `AppShell.tsx`, `ErrorState.tsx`, `FetchMeta.tsx`, `KillSwitchBanner.tsx`.
- Frozen (trading) screens: `paper/`, `runs/` (`runs/detail/`), `strategy/`, `controls/`, `status/`, `shortcuts/`. Not linked from the research navigation.

**`console/src/lib/`**:
- Shared: `api.ts` (`fetchApi`, `ApiResult`, `MutationResult`), `useApiQuery.ts`, `useLastKnownData.ts`, `useMutationCapability.ts`, `idempotencyKey.ts`, `jobStatus.ts`, `jobOutcome.ts`, `cancellationLabel.ts`, `jobTypeForms.ts`, `navigation.ts` (`PRIMARY_NAVIGATION`, `HOME_HREF`), `legacyRedirects.ts`, `resourceRoutes.ts`, `useActivePaperStrategy.ts` (frozen-trading consumer, kept by the shell).
- Research (active): `useResearchMode.ts`, `useStudyPreflight.ts`, `research/` (`types.ts`, `preflight.ts`, `windows.ts`, `proposal.ts`, `present.ts`, `assets.ts`).
- Tests co-located as `*.test.ts` / `*.test.tsx` (for example `consoleRedirects.test.ts`, `research/preflight.test.ts`).

**`alembic/`** (shared infra):
- `env.py` (migration environment), `script.py.mako` (template), `versions/` (33 revision files; research line `0031_research_platform.py` → `0032_research_studies.py` → `0033_research_assistant.py`; earlier trading and Phase lines `0001_phase1_foundation.py` … `0030_phase20_1_evidence_update_guards.py`).

**`config/`**:
- `app.yaml` — base settings; the first layer in the merge order (`core/settings.py::build_settings_payload`).
- `strategies/*.yaml` — frozen (trading) strategy parameters for four trading strategies.
- `research/examples/` — active (research) example strategy specifications (seeded by `services/research/examples.py` through `make research-bootstrap`).

**`scripts/`**:
- Active (research): `bootstrap_research.py` (`make research-bootstrap`), `create_research_db.py` (creates and migrates `trading_research`, refuses the protected name `trading_platform`).
- Shared: `migrate.py` (`make migrate`), `seed_phase1.py` (`make seed`, legacy seed data).
- Frozen (trading): `generate_signals.py`, `export_backtest_report.py`, `report_strategy_analytics.py`, `operator_status.py`.

**`tests/`** (shared):
- Flat `test_*.py` files (159 modules), named by subject. Research coverage uses the `test_research_*.py` prefix: `test_research_api_isolation.py`, `test_research_studies_pipeline.py`, `test_research_strategies_api.py`, `test_research_bootstrap.py`, `test_research_budget.py`, `test_research_assistant*.py`, `test_research_study_preflight.py`, `test_research_console_contract.py`, `test_research_data_pipeline.py`, `test_research_engine_metrics.py`, `test_research_foundation.py`.
- `conftest.py` — shared pytest fixtures.
- `support/` — helper modules imported by tests (`migrated_db.py`, `calendar_facts.py`, `paper_*.py`, `recovery_*.py`, `price_source.py`, `query_counter.py`, `budget_worker.py`, and others).
- `fixtures/` — static data such as `polygon_daily_bars.json`.
- The `make test` target runs a fixed subset of six modules; the rest run with a plain `pytest` invocation (`.venv/bin/pytest`).

**`console/` test layout**:
- Vitest (`vitest.config.ts`, `npm test` → `vitest run`). Test files sit beside the code: `console/src/components/research/research.test.tsx`, `studyWizard.test.tsx`, `console/src/components/jobs/*.test.tsx`, `console/src/lib/*.test.ts`.

## Key File Locations

**Entry Points:**
- `src/trading_platform/api/app.py`: FastAPI application (`app = create_app()`), router composition, mode switch.
- `src/trading_platform/worker/__main__.py`: worker CLI entry (`python -m trading_platform.worker`).
- `console/src/app/layout.tsx`: console root layout; `console/src/app/page.tsx`: home redirect.
- `scripts/bootstrap_research.py`: research database bootstrap.

**Configuration:**
- `config/app.yaml`: base application settings.
- `config/strategies/`: frozen trading strategy parameters.
- `config/research/examples/`: research example strategies.
- `src/trading_platform/core/settings.py`: settings schema, env prefix `TRADING_PLATFORM_` with `__` nesting, and the merge order.
- `console/next.config.ts`: API proxy (`/backend/*`), dev-origin allowance, legacy redirects.
- `console/src/lib/legacyRedirects.ts`: legacy URL map.
- `Makefile`: `RESEARCH_ENV`, `DEV_PORTS`, `API_CMD`, `WORKER_CMD`, `CONSOLE_CMD`.
- `docker-compose.yml`, `Dockerfile`, `render.yaml`: deployment.
- `pyproject.toml`: Python dependencies and tool configuration; `alembic.ini`: migration configuration.
- `.env` and `.env.example` exist at the root. They are the local secret and override files, and are not documented here.

**Core Logic (research):**
- `src/trading_platform/services/research/studies.py`: `StudyService`.
- `src/trading_platform/orchestration/research_studies.py`: service wiring.
- `src/trading_platform/jobs/handlers/research_jobs.py`: research Job handlers.
- `src/trading_platform/strategies/research_registry.py`: research strategy resolution.
- `src/trading_platform/db/models/research.py`: research ORM models.

**Core Logic (shared):**
- `src/trading_platform/jobs/runner.py`: worker loop and `execute_job`.
- `src/trading_platform/jobs/queue.py`: claim, lease, reclaim.
- `src/trading_platform/jobs/lifecycle.py`: transition funnel.
- `src/trading_platform/orchestration/job_mutations.py`: idempotent submit, cancel, retry.

**Testing:**
- `tests/conftest.py`, `tests/support/`, `tests/fixtures/`.
- `console/vitest.config.ts`.

## Naming Conventions

**Python files and modules:**
- `snake_case.py`. Job pairs use `<name>.py` for the handler and `<name>_submission.py` for the contract, for example `jobs/handlers/research_jobs.py` and `research_submission.py`.
- Test modules: `tests/test_<subject>.py`; research tests are `tests/test_research_<subject>.py`.
- Alembic revisions: `NNNN_<slug>.py`, sequential four-digit prefix; the slug names the phase or feature (`0031_research_platform.py`). The revision id equals the file stem.

**Python identifiers:**
- Classes: `PascalCase`, with suffixes by role: `*Service` (`StudyService`, `JobOrchestrationService`), `*Handler` (`ResearchBacktestJobHandler`), `*SubmissionSpec` (`ResearchBacktestSubmissionSpec`), `*Error` (exceptions, `StudyError` and subclasses), `*Settings` (settings blocks), `*Router` variables (`studies_router`).
- Functions: `snake_case`. Builder functions use `build_*` (`build_registry_for`, `build_study_service`); predicates use `is_*`/`_*_for` (`recovery_gated_for`, `admission_check_for`).
- Job types (strings): `kebab-case` — `research-freeze`, `research-backtest`, `research-evaluate`, `catalog-sync`, `ingest-tiingo-bars`, `sync-market-sessions`.
- Strategy ids: `snake_case` — `trend_following_daily`, `donchian_breakout_daily`.
- Private helpers: leading underscore (`_read_body`, `_error`, `_lock`).

**Database:**
- Tables: `snake_case`, plural (`research_studies`, `study_revisions`, `test_window_exposures`).
- Named constraints: `uq_<table>_<purpose>` (`uq_study_revision_jobs_one_graph_per_scope`).

**HTTP:**
- Paths: `/api/v1/<area>/<resource>`, with `kebab-case` segments for multi-word resources (`/api/v1/research/asset-lists`, `/api/v1/research/study-proposals`, `/api/v1/operations/account-snapshots`).
- Error bodies: `{"detail": {"code": "<snake_case_code>", ...}}`.

**Console (TypeScript/React):**
- Components: `PascalCase.tsx`, one component per file; feature folders are lowercase (`components/research/`, `components/jobs/`).
- Hooks: `useCamelCase.ts` (`useResearchMode.ts`, `useApiQuery.ts`).
- Library modules: `camelCase.ts` (`idempotencyKey.ts`, `legacyRedirects.ts`); research helpers live under `lib/research/`.
- Tests: `<Module>.test.tsx` or `<module>.test.ts`, co-located.
- Route folders: lowercase; dynamic segments in brackets (`[studyId]`, `[draftId]`, `[versionId]`, `[jobId]`).

**Configuration and environment:**
- Environment overrides: `TRADING_PLATFORM_<SECTION>__<KEY>` (for example `TRADING_PLATFORM_RESEARCH__MODE`, `TRADING_PLATFORM_DATABASE__NAME`, `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED`). `TIINGO_API_KEY` is the un-prefixed alias for the Tiingo key.
- YAML keys: `snake_case`.

## Where to Add New Code

**New research feature (service logic):**
- Domain logic: `src/trading_platform/services/research/<topic>.py`. Do not import `trading_platform.jobs`, `trading_platform.orchestration`, or `trading_platform.api` from here; receive a `job_submitter` if the feature must submit Jobs (see `StudyService.__init__`, `services/research/studies.py:368`).
- Wire Job submission through `src/trading_platform/orchestration/research_studies.py` (or a sibling `orchestration/` module following the same pattern).
- Database models: add to `src/trading_platform/db/models/research.py` and export from `src/trading_platform/db/models/__init__.py`. Add an Alembic revision `alembic/versions/0034_<slug>.py` with `down_revision = "0033_research_assistant"`. (`0033_research_assistant` is the current head: no revision names it as `down_revision`.)

**New research HTTP endpoint:**
- Router: `src/trading_platform/api/research/<resource>.py`, exposing an `APIRouter` with the `/api/v1/research/...` prefix.
- Register it in the `_RESEARCH_ROUTERS` tuple in `src/trading_platform/api/app.py` (around line 118). Do not add it to `_TRADING_ROUTERS`.
- Write routes depend on `require_mutations_enabled` (`api/dependencies.py`). Use the `_error`/`_read_body` conventions from `api/research/studies.py`.
- Test: `tests/test_research_<resource>_api.py`, and extend `tests/test_research_api_isolation.py` if the surface boundary changes.

**New research Job type:**
1. Handler: `src/trading_platform/jobs/handlers/<name>.py` — class with `job_type = "<kebab-name>"`, `required_execution_mode = ExecutionMode.BACKTEST`, and `run(self, context)`. Delegate to a service; do not write to the database directly.
2. Public contract: `src/trading_platform/jobs/handlers/<name>_submission.py` — pydantic model with `extra="forbid"`, and a spec class with `job_type`, `description` (≤ 200 characters), `cancellation_mode`, `validate_payload`, `submission_defaults`.
   - Declare `console_submission`. Research specs inherit `console_submission = ConsoleSubmission.API_ONLY` from `_ResearchSubmissionSpec` (`jobs/handlers/research_submission.py:61-63`), so research Job types are submitted by the study service and the API, not typed in the console. Use `ConsoleSubmission.INTERACTIVE` only for a type an operator should submit from the console.
   - An `INTERACTIVE` type also needs a submit form in `console/src/components/jobs/new/` and an entry in the form map in `console/src/lib/jobTypeForms.ts` (for example `"sync-market-sessions": SyncMarketSessionsJobForm`). `API_ONLY` types need neither.
3. Register both in `build_research_registry` (`src/trading_platform/jobs/registry.py`, around line 383) and add the name to `RESEARCH_JOB_TYPES` (line 370). Do not change `queue.py`, `runner.py`, `lifecycle.py`, or `dependencies.py` (see the module docstring in `runner.py`).
4. Tests: `tests/test_<name>_job_type.py` (existing examples: `test_backtest_job_type.py`, `test_ingest_bars_job_type.py`).

**New research strategy capability:**
- Specification keywords and validation: `src/trading_platform/strategies/spec/` (`schema.py`, `validate.py`, `evaluate.py`).
- Research resolution: `src/trading_platform/strategies/research_registry.py`.
- Example specs: `config/research/examples/<name>.yaml`, seeded through `services/research/examples.py`.

**New research console page:**
- Route: `console/src/app/research/<area>/page.tsx` (or `[param]/page.tsx`). Keep the page thin: it renders a component from `console/src/components/research/`.
- Component: `console/src/components/research/<Name>.tsx`. Wrap the page content in `ResearchGate` (`console/src/components/research/ResearchGate.tsx`) so a trading-mode or unreachable API is reported consistently.
- Navigation: add the destination to `PRIMARY_NAVIGATION` in `console/src/lib/navigation.ts` only when it is a top-level destination.
- Data: call the API with `useApiQuery` and `fetchApi` (`console/src/lib/`). Add types to `console/src/lib/research/types.ts`.
- Test: `console/src/components/research/<name>.test.tsx` (existing pattern: `research.test.tsx`, `studyWizard.test.tsx`).

**New shared utility (Python):**
- `src/trading_platform/services/<topic>.py` for domain helpers, or `src/trading_platform/core/` for process-level helpers. Keep `core/` free of domain imports.

**New console utility:**
- Hook: `console/src/lib/use<Name>.ts`; pure helper: `console/src/lib/<name>.ts`; research-only helper: `console/src/lib/research/<name>.ts`.

**New script:**
- `scripts/<verb>_<object>.py` (for example `bootstrap_research.py`, `create_research_db.py`). Run it through a Makefile target that sets `PYTHONPATH=src` and `RESEARCH_ENV` where the research database is involved.

**New test:**
- Python: `tests/test_<subject>.py`. Shared fixtures go into `tests/support/<topic>.py`; fixed data goes into `tests/fixtures/`.
- Console: co-located `<module>.test.ts(x)`.

## Special Directories

**`.venv/`:**
- Purpose: local Python virtual environment used by the Makefile (`PYTHON ?= .venv/bin/python`).
- Generated: Yes. Committed: No (gitignored).

**`.data/`:**
- Purpose: local data directory (`PathSettings.data_dir` defaults to `PROJECT_ROOT/.data`).
- Generated: Yes. Committed: No (gitignored).

**`console/node_modules/`, `console/.next/`:**
- Purpose: npm dependencies and Next.js build output. `console/tsconfig.tsbuildinfo` is a TypeScript build cache.
- Generated: Yes. Committed: No.

**`.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `__pycache__/`:**
- Purpose: tool caches.
- Generated: Yes. Committed: No.

**`.claude/`:**
- Purpose: local Claude Code settings (`settings.local.json`) and `worktrees/`. Not product code.
- Generated: Partly. Committed: Not part of the product; do not search or edit it when working on product code.

**`scratchpad/`:**
- Purpose: ad-hoc outputs. `backtest-reports/` and `research/` hold generated reports, not source.
- Generated: Yes. Committed: Not part of the product.

**`console/AGENTS.md` and `console/CLAUDE.md`:**
- Purpose: agent guidance files for the console. Not read as part of this mapping; load them directly before changing console framework code.
- Generated: No. Committed: Yes.

**`alembic/versions/__pycache__/`:**
- Generated: Yes. Committed: No.

**`.planning/`:**
- Purpose: GSD planning artifacts. `.planning/research/strategy-research-pivot/` holds the current research product plan (documents 00, 01, 02). `.planning/codebase/` holds these maps.
- Generated: Partly (maps are generated). Committed: Yes.

## Naming and Layering Summary

- Product path: `console/src/app/research/*` → `console/src/components/research/*` → `console/src/lib/*` → `/backend` proxy → `src/trading_platform/api/research/*` → `src/trading_platform/orchestration/research_studies.py` → `src/trading_platform/services/research/*` → `src/trading_platform/db/*`. Background work: `src/trading_platform/worker/commands/run_jobs.py` → `src/trading_platform/jobs/*` → `src/trading_platform/jobs/handlers/research_jobs.py` → `services/research/studies.py`.
- Frozen trading path (not mounted in research mode): `api/routes/*` → `services/execution/*`, `services/reconciliation/*`, `services/risk.py`, … → `jobs/handlers/paper_session.py`, `broker_order_sync.py`, … ; console `components/paper`, `components/runs`, `components/controls`, `components/strategy`, `components/status`.

---

*Structure analysis: 2026-11-01*
