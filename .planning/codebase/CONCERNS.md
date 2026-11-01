# Codebase Concerns

**Analysis Date:** 2026-11-01

Scope: committed HEAD `73a70e7` plus the uncommitted working tree as found (40 modified, 7 untracked, 7 deleted files; the seven `.planning/codebase/*` documents show as deleted). Uncommitted Python changes that were read: `src/trading_platform/services/research/studies.py` and `tests/test_research_study_preflight.py`. `.planning/`, `.venv/`, `.data/` and `node_modules/` were not scanned. `.env` exists at the repo root and was not read.

Priority order used below: security first, then correctness and performance, then structure. Items marked "by code reading" were not exercised against a running API.

## Tech Debt

**Explicit markers:**
- `grep -rniE "TODO|FIXME|HACK|XXX"` over `src/`, `tests/`, `console/src/`, `scripts/`, `alembic/`, `config/` and `Makefile` returned zero matches. No marked debt exists; the debt below is structural.

**Oversized modules (structural debt):**
- `src/trading_platform/services/execution/submit_orders.py` (3879 lines): legacy, described as frozen, but touched by 11 of the last 60 commits that changed `src/`.
- `src/trading_platform/services/execution/operations.py` (1946 lines), `src/trading_platform/services/recovery.py` (1805 lines, 10 of last 60 commits), `src/trading_platform/services/operator_controls.py` (1610 lines).
- `src/trading_platform/services/research/studies.py` (1616 lines, has uncommitted changes) and `src/trading_platform/services/research/assistant.py` (1582 lines).
- Fix approach: split by use case when next touched; do not grow these files further.

**Duplicated HTTP helpers across route modules:**
- `_error` is defined in `src/trading_platform/api/research/catalog.py:30`, `api/research/studies.py:34`, `api/research/strategies.py:42`, `api/routes/controls.py:43`, `api/routes/recovery.py:43`, `api/routes/jobs.py:74`, `api/routes/execution_operations.py:51`.
- `_read_body` is defined separately in `api/research/catalog.py:54`, `api/research/strategies.py:50`, `api/research/studies.py:57`, `api/routes/controls.py:47`, `api/routes/recovery.py:47`, `api/routes/execution_operations.py:63`. The copies have diverged (see Known Bugs, empty body handling).
- `_uuid` has different signatures: `api/research/catalog.py:38` takes `(value)`, `api/research/strategies.py:63` and `api/research/studies.py:42` take `(value, code)`.
- `_translate` is repeated in `api/research/strategies.py:70`, `api/research/assistant.py:90`, `api/research/studies.py:49`.
- Fix approach: one shared module, e.g. `src/trading_platform/api/http_helpers.py`, used by all routers. Keep the CSRF/content-type fix (see Security) in that single place.

**Two metrics implementations:**
- Research engine: `src/trading_platform/services/research/metrics.py:66` `compute_metrics` (float based, `_f` at line 54).
- Legacy reporting: `src/trading_platform/services/backtest_reporting.py:321` `_compute_metrics` (Decimal based).
- Impact: the same metric (CAGR, drawdown, Sharpe-like values) can differ by rounding or definition between the research console and legacy reports.
- Fix approach: pick one definition, have the legacy path call the research function or vice versa, and add a cross-check test.

**Two registries per concern:**
- Job types: `src/trading_platform/jobs/registry.py:269` `build_default_registry` (trading) and `:383` `build_research_registry`, selected by `build_registry_for` at `:437`.
- Strategies: `src/trading_platform/strategies/registry.py` (legacy) and `src/trading_platform/strategies/research_registry.py`. The API uses the legacy one for `get_strategy_registry` (`src/trading_platform/api/dependencies.py:70` area).

**Global mutable state:**
- `src/trading_platform/services/calendar.py:31` `pin_calendar_start` mutates a process-wide calendar, called from `src/trading_platform/services/research/environment.py:14-18`. There are 15 `global` statements under `src/trading_platform/` (grep). Two processes sharing one interpreter would share the pin.

**Lint and type coverage gaps:**
- `pyproject.toml:58` ignores `E501` (line length) repo-wide.
- mypy is enforced only for `services/execution`, `services/reconciliation`, `services/config` (`pyproject.toml:80-84`). `api/` and `services/research/` are not type-checked.
- Eight `# type: ignore` and four `# noqa` comments exist under `src/trading_platform/`.
- Seven `except Exception:` handlers under `src/trading_platform/` (grep); each should be reviewed for swallowed errors.

**Build and deploy:**
- `Dockerfile:32` runs `alembic upgrade head` on every container start before serving. Migrations are not a separate step; a failed migration blocks the same boot that serves mutations.
- `Dockerfile:1` uses `python:3.13-slim`, while `pyproject.toml:10` declares `requires-python = ">=3.12"` and ruff/mypy target 3.12 (`pyproject.toml` `target-version = "py312"`, mypy `python_version = "3.12"`).
- `render.yaml:66` sets `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED` to `"false"`, but the file has no `TRADING_PLATFORM_RESEARCH__MODE` key (keys listed at `render.yaml:39-66`). A Render deploy therefore always serves the trading surface; the research product cannot be deployed through this file without edits.

## Known Bugs

**Empty body handling differs between routers:**
- Symptom: the draft-create handler (`src/trading_platform/api/research/strategies.py:160-162`, a `POST` on the `/api/v1/research/strategies` prefix at `:32`) fails with `body_not_json` when sent no body, because its parser (`strategies.py:50-53`, `json.loads` at `:52`) has no `or b"{}"` fallback, while `src/trading_platform/api/research/catalog.py:56` and `src/trading_platform/api/research/studies.py:59` treat an empty body as `{}` and report `missing_field`.
- Files: `src/trading_platform/api/research/strategies.py:50-53`, `catalog.py:54-57`, `studies.py:57-60`.
- Workaround: send `{}` explicitly.

**Research-mode worker claims and fails trading jobs in a shared database:**
- `src/trading_platform/jobs/queue.py:76-88` claims the next QUEUED Job by status and dependencies only, with no `job_type` filter tied to the worker's registry.
- `src/trading_platform/jobs/runner.py:142-154` turns any unregistered job type into `FAILED` with `HANDLER_ERROR` and cascades to dependents.
- Result: a worker started with `research.mode` on, pointed at a database holding trading-mode queued jobs, fails them (and the reverse for trading workers and research jobs).
- Current mitigation: the Makefile puts research on a separate database (`Makefile:15` `RESEARCH_DB ?= trading_research`, `Makefile:19` `RESEARCH_ENV`). Nothing enforces it: `src/trading_platform/core/startup.py` checks database reachability only (`:71-77`), not that the mode and database name agree.
- Fix approach: filter `claim_next` by `job_type IN registry.job_types()`, and make startup refuse a research-mode process whose database has trading-mode Jobs.

**Latent registry fallback to the trading registry:**
- `src/trading_platform/api/dependencies.py:75-79` `get_job_registry` falls back to `build_default_registry` (trading) when `app.state.job_registry` is unset. The lifespan in `src/trading_platform/api/app.py` always sets it from `build_registry_for`, so this is reachable only if the lifespan has not run (tests or misuse). It should use `build_registry_for` for consistency.

**Catalog sync assumes a single-member zip:**
- `src/trading_platform/services/research/catalog.py:94-101` `fetch_catalog_sources` reads the whole Tiingo supported-tickers archive into memory (`io.BytesIO(tiingo_zip.content)`), takes `archive.namelist()[0]` without checking, and has no size cap.

**Unpaginated list endpoint:**
- `src/trading_platform/services/research/studies.py:380-383` `list_studies` returns every study with no limit, and `src/trading_platform/api/research/studies.py:76` exposes it directly. Other research list endpoints in `api/research/` should be checked the same way.

## Security Considerations

**1. The API has no authentication or authorization:**
- Risk: any client that can reach the API port can read every research and trading route, and mutate them when mutations are enabled.
- Evidence: `src/trading_platform/api/app.py` builds FastAPI with no security dependencies, no middleware and no CORS configuration. `grep -rniE "authorization|api_key|cors|middleware"` over `src/trading_platform/api` finds no inbound auth. The only mutation gate is the config flag `require_mutations_enabled` (`src/trading_platform/api/dependencies.py`, returns 403 `mutations_disabled`), default `False` at `src/trading_platform/core/settings.py:397`.
- Bind addresses: `src/trading_platform/core/settings.py:30` defaults `ApiSettings.host` to `0.0.0.0`. `Dockerfile:32` binds `0.0.0.0`. The Makefile default is `API_HOST ?= 127.0.0.1` (`Makefile:21`), so `make dev` is loopback-only unless `API_HOST` is overridden.
- Mitigation in place: `render.yaml:66` disables mutations, and the file's comments state the read-only API omits the Polygon and Alpaca credentials.
- Fix approach: add a shared bearer token or session check on all `/api/v1` routes except `/health`, and default the bind host to `127.0.0.1` in `settings.py`.

**2. Cross-site form POST can reach blind-callable mutation routes (by code reading):**
- Risk: the custom `_read_body` helpers call `json.loads(await request.body())` and never check `Content-Type`. A `text/plain` HTML form on any web page can submit a JSON-shaped body; the browser sends it as a simple request, so no CORS preflight occurs and the response need not be readable for the write to happen.
- Why only some routes are reachable: an HTML form can send only GET or POST. PUT and DELETE routes need a preflight, which fails because the API has no CORS middleware (`src/trading_platform/api/app.py`). Routes whose path carries an ID (approve, duplicate, edit, revisions, proposal apply, operation end, broker-statement, retry, cancel) cannot be targeted blind.
- Blind-reachable with the trick (POST, no path ID, a free-text string field to absorb the form separator):
  - `POST /api/v1/research/studies` (`src/trading_platform/api/research/studies.py:81-82`, `name`; `settings` must also be a valid object).
  - `POST /api/v1/research/strategies/drafts` (`src/trading_platform/api/research/strategies.py:160-162`, `title` and `yaml_text`).
  - `POST /api/v1/research/assistant/proposals` and `POST /api/v1/research/assistant/study-proposals` (`src/trading_platform/api/research/assistant.py:122` and `:153`, `user_text`). This is the main cost: each accepted call consumes the AI daily request budget (`src/trading_platform/services/research/assistant.py:146` `CODE_DAILY_LIMIT`).
  - `POST /api/v1/research/asset-lists` (`src/trading_platform/api/research/catalog.py:91-93`, `name` and `tickers`). Only if the `tickers` list can be expressed in the same body.
- Not affected: routes that declare a Pydantic body model, e.g. `src/trading_platform/api/routes/jobs.py:88` `submit_job`. FastAPI decodes the body as JSON only when `Content-Type` is `application/json`, so a `text/plain` POST fails validation there.
- The same `_read_body` pattern is also used by trading routes that take an ID in the path (`src/trading_platform/api/routes/recovery.py:47`, `src/trading_platform/api/routes/execution_operations.py:63`), so those are protected only by the ID requirement.
- Exposure: `make dev` (`Makefile:81`) and `make dev-trading` (`Makefile:89`) force `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=true` on the loopback API while the operator's browser is open. Browser private-network restrictions were not evaluated.
- Fix approach: in every `_read_body`, reject any request whose `Content-Type` is not `application/json` (415 otherwise), and add a regression test per router that POSTs `text/plain` and expects 415.

**3. Console proxy forwards every method to the API:**
- `console/next.config.ts:25` rewrites `/backend/:path*` to the API base (`TRADING_CONSOLE_API_BASE_URL`, default `http://127.0.0.1:8000`, line 5-6). Any method and any path reaches the API through the console origin, so the console inherits the API's lack of auth.
- Fix approach: restrict the rewrite to an allow-list of methods and paths, or add the auth from item 1 at the Next.js layer.

**4. Secrets handling:**
- Tiingo key: sent only as the `Authorization: Token` header (`src/trading_platform/services/tiingo.py:161`), and the client refuses a `token` query parameter (`services/tiingo.py:49` `FORBIDDEN_QUERY_PARAMS`, `:194-195`).
- Anthropic key and Tiingo key are read from the process environment or, as fallback, directly from the dotenv file (`src/trading_platform/core/settings.py:596-663`, aliases `TIINGO_API_KEY` and `ANTHROPIC_API_KEY`). Two sources for one secret make it harder to know which key is live.
- Log redaction: `src/trading_platform/core/log_sanitizer.py:22`, `:35-36`, `:46` scrub key-named fields, embedded `key=value` pairs and bearer tokens. It is regex based; new key names not in the pattern are not scrubbed.
- `src/trading_platform/services/config/secrets.py` exists (its name matches the secrets pattern; its contents were not read for this document).
- Hard-coded fallback database password: `docker-compose.yml:8`, `:35`, `:52`, `:72` fall back to a committed default when `POSTGRES_PASSWORD` is unset. Local-only in practice, but a deploy that forgets the variable uses a known password.
- Container runs as root: `Dockerfile` has no `USER` directive.
- `.env` exists at the repo root and is listed in `.gitignore` (first line). Its contents were not read.

**5. SQL and injection review:**
- SQL goes through the SQLAlchemy ORM (`select`, `session.execute`). No string-built SQL on user input was found.
- The only f-string SQL-like fragment is `src/trading_platform/db/models/execution_operation.py:146`, which interpolates module constants (`_OPEN_STATE_VALUES`) into a partial-index predicate. Not user-controlled.
- Subprocess: `src/trading_platform/services/research/code_identity.py:21-24` runs `git` with fixed arguments and no shell. No `shell=True`, `eval`, `exec` or `pickle` usage was found under `src/` or `scripts/`.
- YAML: `src/trading_platform/core/settings.py:526` uses `yaml.safe_load`.

**6. Input validation:**
- Research routes validate keys (`_read_body` allow-lists) and then index required keys directly (`body["name"]`, `body["settings"]`, `body["yaml_text"]`), which is safe only because `required=` is checked first. Keep those two steps together when editing.
- Trading `_read_body` helpers (`controls.py:47`, `recovery.py:47`, `execution_operations.py:63`) validate only key sets; value validation is per-handler.

## Performance Bottlenecks

**Tiingo ingestion re-downloads the full requested range on every run:**
- Problem: `src/trading_platform/services/research/tiingo_ingestion.py:259-260` calls `fetch_metadata` and `fetch_daily_prices(ticker, from_date, to_date)` for every ticker, every run. Nothing checks which dates already exist in `daily_bars` before fetching.
- The range comes from the Job payload: `src/trading_platform/jobs/handlers/ingest_tiingo_bars_submission.py:53-54` (`from_date`, `to_date`). No caller computes gaps. The upsert (`upsert_tiingo_bars`, `tiingo_ingestion.py:110`) is idempotent, so the cost is quota, not correctness.
- Metadata is refetched per ticker per run (`tiingo_ingestion.py:259`), so each ticker costs two budgeted requests.
- Quota: `src/trading_platform/core/settings.py:417-419` sets `requests_per_hour=50`, `requests_per_day=1000`, `unique_symbols_per_month=500`. At two requests per ticker, one hour of budget covers 25 tickers.
- Improvement path: compute missing date spans from `daily_bars` per symbol and fetch only those; cache metadata with a TTL in `apply_metadata_to_symbol` (`tiingo_ingestion.py:135`); skip metadata when the symbol row is fresh.

**Catalog sync downloads everything on every sync:**
- `src/trading_platform/services/research/catalog.py:94-101` fetches the Tiingo supported-tickers zip, the NASDAQ traded list and the SEC company JSON in full each time (`src/trading_platform/jobs/handlers/catalog_sync.py:40`, `src/trading_platform/services/research/bootstrap.py` `public_catalog_sources`). No `ETag`, `If-Modified-Since` or size check is used.
- Improvement path: conditional requests, and skip when the stored catalog is newer than a configurable age.

**Budget accounting runs three COUNT queries per admit:**
- `src/trading_platform/services/research/budget.py:136-148` `_counts` counts ledger rows for the hour, the day and distinct symbols for the month. Indexes exist for these predicates (`src/trading_platform/db/models/research.py:336-341`, `ix_provider_request_ledger_provider_attempted_at` and `..._provider_symbol_attempted_at`), so this is bounded by the index, but the table grows without retention. No pruning was found.

**Unbounded result sets:**
- `.all()` appears 83 times under `src/trading_platform/`. Examples without a `LIMIT`: `src/trading_platform/jobs/queue.py:147`, `src/trading_platform/jobs/cancellation.py:307`, `src/trading_platform/jobs/dependencies.py:355`.
- `list_studies` (`services/research/studies.py:380-383`) is also unbounded; see Known Bugs.

**Full-history research reads (not measured):**
- Research runs call the legacy engine `_execute_backtest_run` (`src/trading_platform/services/research/backtest.py:38`, defined at `src/trading_platform/services/backtesting.py:317`) and read persisted daily bars for the full development range. Cost per study was not measured.

## Fragile Areas

**Research engine depends on frozen legacy code:**
- Files: `src/trading_platform/services/research/backtest.py:38` imports `EngineOptions` and the private `_execute_backtest_run` from `src/trading_platform/services/backtesting.py`.
- Why fragile: "legacy is frozen" is not enforced. A change to the legacy engine (its fill, slippage or commission logic, `services/backtesting.py:649-659`) silently changes research results, and the research tests are the only detector.
- Safe modification: treat `_execute_backtest_run` as a public contract; add research-side regression tests that pin a known study result before touching the legacy file.
- Test coverage: research test files such as `tests/test_research_engine_metrics.py` and `tests/test_research_studies_pipeline.py` exist; whether they exercise the legacy engine path was not verified. Neither is in `make test` (see Test Coverage Gaps).

**Append-only database guarantees live in triggers and migrations:**
- Files: `src/trading_platform/db/models/order_submission_attempt.py:5-7`, `src/trading_platform/db/models/order_event.py:57`, `src/trading_platform/db/models/recovery_record.py:6`, `src/trading_platform/db/models/research.py:5` ("refuses every UPDATE and DELETE" for approved versions), migrations `alembic/versions/0028*` through `0030*`.
- Why fragile: changing an immutable table requires a new migration that drops and recreates the trigger, and the ORM model alone does not express the rule. `pyproject.toml` (`per-file-ignores` for `alembic/versions/*`) disables several lint rules for migrations.
- Safe modification: never edit a shipped migration; add a new one; add a DB-level test that asserts UPDATE/DELETE are rejected.

**Assistant prompt and schema are coupled to a provider limit:**
- Files: `src/trading_platform/services/research/assistant.py:390` (no union types: "the provider limits a schema to 16 union-typed" properties), `:491` (system prompt), `:510` (unsupported-request codes).
- Why fragile: the JSON schema returned to the provider and the validator in `src/trading_platform/strategies/spec/validate.py` must stay in sync. A new spec field added to only one side produces either a provider schema error or accepted-but-unvalidated output.
- Safe modification: change the spec schema, the validator and the prompt in one commit; keep `tests/test_research_assistant_converter.py` and `tests/test_research_assistant.py` green.

**Shared study coverage logic:**
- Files: `src/trading_platform/services/research/studies.py:535` `_coverage_checks` is used by both readiness and the preflight endpoint (`:602-693`, `api/research/studies.py` preflight route). The file has uncommitted edits.
- Safe modification: change `_coverage_checks` only with `tests/test_research_study_preflight.py` and `tests/test_research_studies_pipeline.py` run together.

**Uncommitted work touches the research surface:**
- `git status` shows 40 modified, 7 untracked and 7 deleted files, including `src/trading_platform/services/research/studies.py` and console research components. Analysis reflects the working tree as found; behavior may change on commit.

## Legacy and Research Coexistence Risks

- **Shared job queue and worker** (see Known Bugs). Highest-risk coexistence issue.
- **Shared database schema.** Research migrations `alembic/versions/0031_research_platform.py`, `0032_research_studies.py`, `0033_research_assistant.py` and legacy migrations share one schema. Research reads always pass the provider and adjustment flags explicitly (`src/trading_platform/services/research/environment.py:21-24`, `bar_source`). Legacy readers were not verified to filter by provider; `src/trading_platform/services/market_data_access.py:106` carries `provider` through but no filter was found. Mixed Polygon and Tiingo bars for the same symbol and date are a possible silent input difference.
- **Two market-data providers in both surfaces.** Tiingo research ingestion (`src/trading_platform/services/research/tiingo_ingestion.py`) and legacy Polygon (`src/trading_platform/services/polygon.py`), with `src/trading_platform/jobs/handlers/ingest_tiingo_bars.py` registered in the research registry (`jobs/registry.py` around `:395`).
- **Process-level mode switch.** `src/trading_platform/api/app.py` mounts `_RESEARCH_ROUTERS if mode else _TRADING_ROUTERS` and the lifespan refuses to start if `research.mode` changed between construction and startup. This is a good guard and the reason the two surfaces are disjoint. The guard does not cover the worker (see Known Bugs).
- **Config mode flag is the only switch.** `src/trading_platform/core/settings.py:458` documents that `research.mode` selects the registry; `make dev` and `make dev-trading` (`Makefile:79-91`) differ only by `RESEARCH_ENV`.
- **Tests:** `tests/test_research_api_isolation.py` checks the mount separation (mitigation).

## Scaling Limits

**Tiingo (research ingestion):**
- Requests per hour: 50 (`src/trading_platform/core/settings.py:417`).
- Requests per day: 1000 (`:418`).
- Unique symbols per month: 500 (`:419`).
- Enforced in-process by `DatabaseRequestBudget` (`src/trading_platform/services/research/budget.py:100-148`) against the provider ledger.
- Max assets per study: 10 (`settings.py:470`).

**AI assistant (research drafting):**
- Disabled unless a key and explicit limits exist; `max_requests_per_day` and `max_output_tokens` default to `0` (`settings.py:435-437`, `usable` check at `:450-451`).
- Input cap 20000 characters (`settings.py:438`), 5 revisions per draft (`:439`), 1 concurrent request (`:441`).
- Daily cap error code: `ai_daily_limit_reached` (`services/research/assistant.py:146`).

**Job worker:**
- Single lease-based claim per poll (`jobs/queue.py:76-88`, `FOR UPDATE SKIP LOCKED`). Throughput is one job at a time per worker process; more workers need more processes and rely on the lease.
- Lease and stale-run defaults: `stale_run_timeout_minutes` default 30 (`settings.py:351`).

**Database growth:**
- `order_submission_attempts`, `order_events` and `recovery_records` are append-only by trigger (see Fragile Areas) and no retention or archival job was found, so they only grow.
- `provider_request_ledger` (research Tiingo and AI request accounting) has no retention or pruning job; no append-only trigger was found for it in `alembic/versions/0032_research_studies.py` or `0033_research_assistant.py`.

## Dependencies at Risk

**Python:**
- `pyproject.toml:12-22` declares ranges only (`>=x,<y`). No lockfile exists (`uv.lock`, `poetry.lock`, `requirements*.txt` not found), so builds are not reproducible.
- `anthropic>=1.12.0,<2.0.0` (`pyproject.toml`): the research assistant depends on provider schema constraints (see Fragile Areas); an SDK or model change can change accepted output.
- Container Python `3.13` vs declared `>=3.12` (see Tech Debt).

**Console:**
- `console/package.json` pins `next` `16.2.10`, `react` `19.2.4`, `recharts` `3.9.2` exactly; `console/package-lock.json` is present.
- Dev tooling uses caret ranges (`eslint ^9`, `vitest ^4.1.10`, `tailwindcss ^4`, `typescript ^5`).

**Market data:**
- Tiingo free-plan limits are encoded as defaults (`settings.py:417-419`). A plan change needs a settings change; there is no runtime discovery.
- Tiingo, Polygon and Alpaca adapters all exist (`services/tiingo.py`, `services/polygon.py`, `services/alpaca.py`). Each has its own retry and timeout settings (`settings.py:202-204`, `:309-311`, `:411-413`).

## Missing Critical Features

- **Authentication and authorization:** Not detected (see Security item 1).
- **CI pipeline:** Not detected.
- **Pagination on research list endpoints:** partial. Shared constants `DEFAULT_LIMIT = 20` and `MAX_LIMIT = 100` exist in `src/trading_platform/api/dependencies.py`, but research `list_studies` (`services/research/studies.py:380-383`) takes no limit.
- **Rate limiting on the HTTP API:** Not detected. Only provider-side budgets exist.
- **Backup and restore procedure for the research database:** Not detected in the repo.
- **Error tracking service:** Not detected. Logs are JSON to stdout via `core/logging.py`; `src/trading_platform/api/app.py` logs unhandled errors and returns `{"detail": {"code": "internal_error"}}`.

## Test Coverage Gaps

**The default test target skips most research tests:**
- `Makefile:111-112` `make test` runs six files: `tests/test_app_boot.py tests/test_db_migrations.py tests/test_strategy_registry.py tests/test_market_data_ingestion.py tests/test_market_data_access.py tests/test_trend_following_strategy.py`. None of the `tests/test_research_*.py` files is included.
- `pyproject.toml` sets `testpaths = ["tests"]`, so a bare `pytest` runs everything; nothing in the repo runs it automatically.
- No CI configuration exists: `.github/` is absent, and no other CI file was found at the repo root.
- Priority: High. Add `make test-research` (or extend `make test`) and run it in a pre-merge hook.

**Type checking excludes the research surface:**
- `pyproject.toml:80-84` mypy covers `services/execution`, `services/reconciliation`, `services/config` only. `api/research/` and `services/research/` are not type-checked.

**Security-relevant tests missing:**
- No test asserts the mutation gate is enforced on every mutating route (only `tests/test_research_api_isolation.py` checks mount separation).
- No test sends a mutation with a non-JSON `Content-Type`; this is why the CSRF gap in Security item 2 is not caught.
- No test covers the worker mode/database mismatch (Known Bugs).

**Console:**
- 51 Vitest files exist under `console/src/` (e.g. `console/src/components/research/research.test.tsx`, `console/src/components/research/studyWizard.test.tsx`).
- `console/package.json` declares no end-to-end framework (no Playwright or Cypress dependency). End-to-end coverage of the research flows is Not detected in the repo.

**Name-based coverage check (weak signal):**
- A search for each `src/trading_platform` module (over 150 lines) by basename under `tests/` found one with no reference: `src/trading_platform/db/models/recovery_record.py` (196 lines). This heuristic only shows a basename never appears in a test; it does not show the tests are adequate.

---

*Concerns audit: 2026-11-01*
