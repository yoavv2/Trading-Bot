# Strategy Research

Strategy Research is a local, single-user application for writing daily,
long-only trading strategies, testing them on historical U.S. stock and ETF
data, and comparing the evidence per asset. It replaced the earlier
automated-trading / operator-console direction of this repository on
2026-10-06 (`.planning/research/strategy-research-pivot/`). The trading
backend (paper execution, reconciliation, broker code, migrations 0022–0030)
stays in the repository, frozen; it is not part of the product UI.

This is a research tool. It does not place orders, does not contact a broker,
and makes no claim that a strategy will perform in the future.

## What the product does today

- **Author strategies** as a bounded YAML specification (`spec_version: 1`):
  daily bars, long-only, one asset per run, indicators `sma`, `ema`, `rsi`,
  `highest`, `lowest`, `lag`, `change_pct`, comparison and crossing operators,
  `all_of` / `any_of` groups. Validation runs on every change with closed error
  codes; a deterministic plain-language explanation is rendered from the
  specification itself. Contract: `.planning/research/strategy-research-pivot/02-STRATEGY-SPEC-V1.md`.
- **Draft with an assistant** (optional): describe an idea or ask for a
  correction; the assistant returns a candidate specification, an
  `unsupported_requests` list and a note. The same validator and explanation
  run on it; nothing is written until you click **Apply**, and approval stays a
  separate action.
- **Approve immutable versions.** Drafts are mutable; approved versions are
  append-only (a database trigger refuses UPDATE and DELETE). Editing an
  approved version opens a new draft with lineage. Four example versions
  (trend following 50/200, RSI mean reversion, Donchian breakout, time-series
  momentum) are seeded by the bootstrap after passing a parity test against the
  original Python strategies.
- **Pick assets** from a catalog of U.S. stocks and ETFs (Tiingo public list,
  names joined from Nasdaq Trader), search by ticker or name, keep saved lists.
- **Define studies.** A study revision is an immutable snapshot of: strategy
  versions, assets (up to a configured limit, default 10, never truncated),
  date range, development / validation / final-test windows, capital, quantity
  policy, trading costs, the ranking objective and the risk constraint. Costs,
  objective and constraint are entered per study; nothing is pre-filled.
- **Check readiness** before anything runs: every failing item is listed
  (coverage, warm-up, missing settings, missing exchange sessions, integrity
  findings after download, inputs changed after a freeze). Nothing is dropped,
  shortened or substituted.
- **Run** as Jobs: download only the selected assets for the range plus
  warm-up, freeze the inputs (integrity check, digest, `inputs/` export), run
  one backtest per (version, asset, window) plus a buy-and-hold benchmark per
  (asset, window), evaluate. Progress is visible on the generic Jobs pages.
- **Compare** per asset: return and risk tables, equity and drawdown charts,
  evidence descriptors and grades with their limitations, ranking by the
  study's objective with reasons, the benchmark shown beside each candidate and
  never used as a gate, a verdict that can be "insufficient evidence" or "no
  candidate qualifies".
- **Freeze and final-test.** Freezing records the chosen candidate and the
  acceptance criteria; the final test runs only after a freeze, only on the
  frozen candidate, once (a technical rerun is linked and must reproduce the
  same digests). Final-test exposures on an asset are shown to every later
  study that overlaps those dates.
- **Export** `comparison.json`, per-run `summary.json` / `trades.csv` /
  `equity_curve.csv`, and a static `report.html` / `report.md`, under
  `.data/research/` (git-ignored).

Scope and limitations are listed at the end of this file.

## Stack

- Python 3.12+, FastAPI, SQLAlchemy, Alembic, PostgreSQL 16
- Next.js console (`console/`, Node.js 22+)
- Tiingo for daily bars (header authentication; personal-use license)
- Anthropic Python SDK for the optional assistant

## Repository layout

```text
.
├── alembic/versions/           # 0001–0030 trading schema (frozen); 0031–0033 research schema
├── config/
│   ├── app.yaml                # Base application/runtime config
│   └── strategies/*.yaml       # The four original Python strategies (parity oracles)
├── console/                    # Next.js console: Strategies, Assets, Studies, Jobs
├── scripts/
│   ├── bootstrap_research.py   # make research-bootstrap
│   ├── create_research_db.py   # Create + migrate the research database (refuses the trading name)
│   ├── migrate.py / seed_phase1.py            # Trading-database tooling (frozen)
│   └── export_backtest_report.py / generate_signals.py / operator_status.py / report_strategy_analytics.py  # read/report tools (trading)
├── src/trading_platform/
│   ├── api/research/           # Research routers (mounted only in research mode)
│   ├── api/routes/             # Trading routers (mounted only in trading mode) + health/jobs
│   ├── jobs/                   # Generic Job framework (shared); research registry of six Job types
│   ├── services/research/      # Catalog, Tiingo ingestion, budget, integrity, freeze, studies, backtest, metrics, evidence, ranking, report, assistant, bootstrap
│   ├── strategies/spec/        # Specification schema, validator, history derivation, explanation
│   ├── strategies/declarative.py              # The one interpreter for approved versions
│   └── services/, worker/, ...  # Trading backend (frozen)
├── tests/                      # tests/test_research_*.py cover the research product
├── docker-compose.yml, Dockerfile, Makefile, pyproject.toml
└── .planning/                  # Project state, roadmap, requirements, research documents
```

## Quick start

There is one supported way to run the product locally: the research API, the
Job worker and the console as host processes, started together by `make dev`,
against the PostgreSQL database `trading_research` on `localhost:5432` (for
example a native Homebrew PostgreSQL).

### One-time setup

1. Create the environment file:

   ```bash
   cp .env.example .env
   ```

   Set `TIINGO_API_KEY` (read by the server-side Tiingo client only). The
   database host and port come from the same file; the research database name
   is forced by the Makefile and cannot be redirected to the trading database.

2. Create a virtual environment and install dependencies:

   ```bash
   python3.12 -m venv .venv
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -e '.[dev]'
   make console-install
   cp console/.env.example console/.env.local
   ```

3. With PostgreSQL running on `localhost:5432`, prepare the research database
   (idempotent; re-run after pulling new migrations):

   ```bash
   make research-bootstrap
   ```

   It creates `trading_research` when missing, migrates it to the single
   Alembic head (`0033_research_assistant`), seeds the four example strategy
   versions, syncs the public asset catalog (no key needed; `--refresh-catalog`
   re-syncs) and stores XNYS exchange sessions from the pinned research
   calendar start (`RESEARCH_CALENDAR_START`, 2014-01-02) through the last
   session the calendar library can answer for (about one year ahead). Existing
   drafts, versions, lists, studies and results are kept. The trading database
   is never touched.

### Every day

```bash
make dev
```

Then open **http://localhost:3000** (`127.0.0.1` works too). It lands on
Studies; the primary navigation is **Strategies · Assets · Studies · Jobs**.

`make dev` refuses to start if port 8000 or 3000 is taken, then runs three
processes in one group (`Ctrl-C` stops all of them):

- **Research API** on `http://127.0.0.1:8000` with `--reload`
  (`TRADING_PLATFORM_RESEARCH__MODE=true`, calendar pinned, database
  `trading_research`). `GET /health` reports `"mode": "research"`. Only the
  research routers and the generic health/jobs routers are mounted; every
  trading route is a 404 in this process. Mutations are enabled for this API
  process only.
- **Job worker** (`python -m trading_platform.worker run-jobs`) in research
  mode on the same database, restarted on changes under `src/`. The research
  registry contains exactly `catalog-sync`, `ingest-tiingo-bars`,
  `sync-market-sessions`, `research-freeze`, `research-backtest`,
  `research-evaluate`; no trading Job type can be submitted.
- **Console** on `http://localhost:3000`. Legacy operator-console URLs
  (`/`, `/strategy`, `/runs`, `/paper`, `/controls`) redirect permanently to
  the research pages.

Changes to `config/` or `.env` need a `make dev` restart. `make api`,
`make worker` and `make console` run one piece in the foreground (API and
worker in research mode; `make api` reads `mutations_enabled` from `.env`
like any deployment, so mutating routes return `403 mutations_disabled`
unless enabled there).

## The research workflow

1. **Author.** Strategies → New strategy (or open a draft). Write YAML, or ask
   the assistant. Findings, derived values (`history_required`,
   `history_minimum`, scale class) and the explanation update as you type.
   Save as draft.
2. **Approve.** Approve the draft into an immutable version (explicit
   confirmation). The version page shows the YAML as approved, the
   explanation, the lineage and, for assistant-drafted versions, the full
   attempt chain.
3. **Study.** Studies → New study: choose approved versions (several per
   family allowed, compared as separate candidates), tickers or a saved list,
   range and the three windows, capital, quantity policy, costs, objective and
   constraint. Readiness lists every blocking item. First time on a fresh
   database, `sync-market-sessions` is already done by the bootstrap; a study
   submits its own ingestion and freeze Jobs.
4. **Compare.** On the study page: inputs verified or not, verdict, ranking,
   per-asset tables and charts, evidence grades, limitations, the benchmark
   beside each candidate, exposures from other studies on the same assets.
   Reading results with a final-test evaluation marks the revision's
   exposures as inspected.
5. **Freeze.** Choose the leading candidate and record the acceptance criteria
   (refused while candidates are co-leading, while acceptance is incomplete,
   or when the candidate is not eligible).
6. **Final test.** Runs the frozen candidate and its benchmark on the
   final-test window once; the outcome block shows the frozen criteria, the
   result, and the benchmark comparison separately. Export the report.

Studies with changed settings become a new revision of the same study; earlier
revisions and their results remain.

## Research assistant (optional)

The Strategies editor has an assistant panel, and the study wizard has a
"Describe your study" assistant (`POST /api/v1/research/assistant/study-proposals`,
contract `study-v1`: it proposes study settings for review, resolves strategies
against approved versions and never invents costs, constraints or ids; applying
only fills the form). Both share the settings below and are **disabled by
default**; the editor and the wizard work without them. To activate it, set in `.env` (or the shell
of the API process) and restart `make dev`:

```bash
ANTHROPIC_API_KEY=...                                  # server-side only, never logged
TRADING_PLATFORM_RESEARCH__AI__ENABLED=true
TRADING_PLATFORM_RESEARCH__AI__MAX_REQUESTS_PER_DAY=10 # your choice; retries count
TRADING_PLATFORM_RESEARCH__AI__MAX_OUTPUT_TOKENS=4096  # your choice; passed as max_tokens
```

- **Model:** `claude-haiku-5-5` by default, configurable with
  `TRADING_PLATFORM_RESEARCH__AI__MODEL`; the configured model is used as is
  (no silent fallback). The provider-facing contract (system prompt and
  structured-output schema) is versioned as `s5-v2` and stamped on every
  attempt.
- **Limits** (all enforced per attempt, before the provider is called, and
  reported by `GET /api/v1/research/assistant`): input size, output tokens,
  requests per day and concurrent requests (counted in the shared
  `provider_request_ledger` across every process), revisions per draft, an
  overall deadline across both attempts (`TIMEOUT_SECONDS`, default 60), one
  automatic retry on invalid output, no implicit SDK retries. The values above
  (10 requests/day, 4096 output tokens) are the initial operating limits used
  during validation; they are request and token caps, **not a monetary
  guarantee**. Spending limits in the Anthropic console are a separate control.
- **Accounting and provenance:** every attempt is recorded in `ai_drafts`
  (provider, model, prompt version, request id, token usage incl. cache
  read/creation, validation result, specification hash, failure code,
  timestamps). The chain is shown on the draft and on the approved version.
  The API key never appears in stored bodies or logs.
- **What it never does:** approve, run a study, see results, call tools, reach
  a broker or the trading database.

## Common commands

```bash
make research-bootstrap  # Prepare trading_research (migrate, examples, catalog, sessions); idempotent
make dev                 # Research API + worker + console (the product)
make api                 # Research API only, with --reload
make worker              # Research Job worker only, restarted on code changes
make console             # Console only
make console-install     # npm install for the console
make test                # A focused subset of the Python suite (six trading test files); see Testing
make dev-trading         # Frozen trading stack (API + worker + console) against the .env database
make migrate / make seed # Trading-database tooling (frozen)
make up / down / logs    # Alternative Compose stack (stack profile); never alongside make dev
```

Long-running work (catalog sync, ingestion, freeze, backtests, evaluation)
runs as Jobs submitted by the studies or from the console's `/jobs/new` page.
Authoring writes (drafts, versions, asset lists, study definitions, freezes)
are synchronous HTTP writes on the research database.

## Databases and isolation

| Database | Alembic revision | Role |
| --- | --- | --- |
| `trading_research` | `0033_research_assistant` (head) | The product. Created and migrated by `make research-bootstrap`. |
| `trading_platform` | `0021_phase20_operations_safety` | The frozen trading database. Not upgraded to 0022–0030 (Phase 20.1 work) and never touched by research processes. |

Research mode is a process-level switch: it selects the research Job registry
and the research routers only. Research reads carry provider `tiingo` and the
adjusted flag explicitly; trading defaults are unchanged.

## Configuration

Settings are assembled from built-in defaults, `config/app.yaml`,
`config/strategies/*.yaml` and environment overrides with the
`TRADING_PLATFORM_` prefix and `__` as the nested delimiter. Research settings
live under `TRADING_PLATFORM_RESEARCH__*`:

```bash
TRADING_PLATFORM_RESEARCH__MODE=true                 # set by the Makefile for every research process
TRADING_PLATFORM_RESEARCH__CALENDAR_START=2014-01-02 # pinned first session (Makefile RESEARCH_CALENDAR_START)
TRADING_PLATFORM_RESEARCH__MAX_ASSETS_PER_STUDY=10
TRADING_PLATFORM_RESEARCH__QUANTITY_POLICY_DEFAULT=fractional   # or whole_shares
TRADING_PLATFORM_RESEARCH__TIINGO__REQUESTS_PER_HOUR=50          # shared ledger limits; default = Tiingo free plan
TRADING_PLATFORM_RESEARCH__TIINGO__REQUESTS_PER_DAY=1000
TRADING_PLATFORM_RESEARCH__TIINGO__UNIQUE_SYMBOLS_PER_MONTH=500
TIINGO_API_KEY=...                                   # alias of TRADING_PLATFORM_RESEARCH__TIINGO__API_KEY
```

Assistant settings are listed above. `.env.example` carries the full set with
comments.

## Testing

The research product is covered by `tests/test_research_*.py` (foundation,
API isolation, strategies API, data pipeline, studies pipeline, engine
metrics, budget, console contract, bootstrap, assistant, assistant converter)
and by the console's vitest suite (`cd console && npm test`). Most Python
tests need a reachable PostgreSQL on the configured host/port and create
throwaway migrated databases.

Run everything with:

```bash
PYTHONPATH=src .venv/bin/pytest -q
```

`make test` runs only six trading-era test files and is not the full suite.

Known baseline issues in the full suite, present before the research pivot and
unchanged by it: two trading-era failures (`universe_size` assertions in
`tests/test_api_reads.py` and `tests/test_app_boot.py`) and an intermittent
teardown-only `InsufficientPrivilege` error when dropping throwaway databases
under a non-superuser role (it leaves `alpaca_execution_*` databases behind).
Full-suite evidence, in order:

- **After S4/S6, before S5** (2026-10-08 01:16, `final8_pytest.txt`, kept
  under `.data/research/validation-2026-10-08/`): 3662 passed, the 2 known
  baseline failures, 1 known teardown `InsufficientPrivilege` error.
- **After S5 and the `s5-v2` corrections:** focused checks only, recorded in
  the implementation plan §16: the research assistant and converter modules,
  the research test modules touched, the console suite (431 vitest tests,
  `tsc`, eslint, production build), the S5 browser run and four live requests.
- **No full-suite run is recorded on the final post-S5 tree** (`519e73f`).

No fully green full-suite run is claimed.

## Scope and limitations

Supported scope:

- Daily bars, long-only, one position per asset, no pyramiding, no leverage.
- U.S. stocks and ETFs in USD from the Tiingo catalog.
- Each (version, asset, window) is an independent single-asset test with one
  slot and the study capital. Results are **not** a portfolio simulation
  (`portfolio_combined` is refused as `mode_not_supported_yet`).
- One validation window and one final test per revision; evidence grades are
  heuristics and say so.

Not expressible in the specification (reported as such, never silently
approximated): shorting or long-short, intraday or weekly bars, intrabar stop
or target prices, trailing stops, position sizing inside the strategy,
conditions on other assets, pyramiding, arbitrary code, look-ahead shifts.

Remaining limitations of the implemented increment:

- **Calendar range.** Exchange sessions are stored from the pinned start
  (2014-01-02) through the calendar library's last available session (about
  one year ahead). Studies outside that range are not ready
  (`calendar_start_not_pinned`, `market_sessions_not_synced`, window checks).
- **Partial name coverage.** The catalog sync names rows from Nasdaq Trader
  only (about 12.7k of 23.2k rows at the last sync); the SEC name source
  answers 403 to the sync. Name search covers populated names only and the
  Assets page says so. Tiingo metadata fills gaps on view, once per asset.
- **Repeat ingestion.** A revision downloads the whole required range per
  asset even when bars already exist (the upsert is idempotent; every request
  is charged to the shared Tiingo ledger).
- **Assistant proposal after reload.** The panel is per page load: a proposal
  not yet applied is not restored after a reload (every proposal remains
  retrievable by id through the API). Live model latency has been observed
  only on four validation requests.
- **Baseline test and environment issues** listed under Testing.
- **Not implemented:** Pine Script export and equivalence checking, the
  portfolio mode UI, the visual rule builder, Python authoring, parameter
  search, walk-forward, cost sweeps, whole-market scanning. Order of the next
  agreed increments: Pine export and equivalence → visual builder → portfolio
  mode (`.planning/ROADMAP.md`). None is authorized for implementation by this
  document.

## Frozen trading backend

`make dev-trading` starts the former operator stack (trading-mode API, worker
and console) against the database named in `.env`. Its pages are no longer
served by the console; only its API and the generic Jobs pages remain. Phase
20.1 (operator-state correctness) is incomplete with open verification
findings, and Phase 21 was never started; see `.planning/ROADMAP.md`
"Historical / frozen". The break-glass kill-switch trip
(`python -m trading_platform.worker kill-switch-trip --reason ...`) still
exists for that stack (decision D-15 in
`.planning/phases/20-complete-operation-migration-safety-controls/20-CONTEXT.md`).

### Alternative: Docker Compose stack (trading-era)

The containerized stack (`db`, one-shot `migrate`, `api`, `worker`) sits behind
the Compose `stack` profile (`make up` / `make down` / `make logs`). It was
built for the trading stack: it migrates and runs against its own `db`
volume with the environment in `docker-compose.yml` and does not set research
mode. Use it instead of `make dev`, never alongside it (same ports). A bare
`docker compose up` starts only `db`; set `POSTGRES_HOST_PORT` if a native
PostgreSQL already owns 5432.
