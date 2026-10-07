# Strategy Research Platform: Implementation Plan

**Status:** plan for review (2026-10-07). Implementation remains separately authorized. No code, migrations, database upgrades, broker contact, purchases, commits or pushes were made while preparing it.
**Inputs:** `00-PROPOSAL.md` revision 6 (product requirements and evaluation rules), `02-STRATEGY-SPEC-V1.md` (the specification contract), the codebase at `f4ab1a5`.
**Legend:** **[Settled]** required by the approved direction · **[Chosen]** my implementation choice, changeable without a product decision · **[Open]** needs the user; each is localized to the stage it affects and blocks nothing else.

---

## 1. Corrected assumptions in force

1. **Example-strategy parity is earned.** Each original computes on exactly `warmup_periods` loaded bars; RSI depends on that history length. The specification carries explicit `history` for recursive indicators and `history_required` per version; evaluation is stateless with exit-before-entry precedence, as the originals. The examples are seeded only after the parity matrix in `02-STRATEGY-SPEC-V1.md` §9 passes. Any surviving difference is recorded, not hidden.
2. **Rounding leaves a residual.** Fractional quantities are `ROUND_DOWN` to 6 decimals of `min(slot_notional, cash − commission) / fill_price`; overspend is impossible by construction; the residual is reported as the actual `rounding_slack`. Equality assertions where rounding applies use a policy-derived tolerance. Price-scale invariance is claimed and tested only for `price_scale_free` specifications.
3. **Exposure is global.** Recorded final-test exposures are shown for the same asset and overlapping dates across every study, revision, strategy family and version, with `run_recorded` distinguished from `results_inspected`. The application cannot see research done outside it and says so.

Planning defaults in force (2026-10-07): `ema` ships in spec v1; catalog refresh is a manual Job initially; AI stays disabled until credentials and explicit limits are configured, with no numerical budget approved; fractional quantities; US stock and ETF catalog in USD; configurable limit of 10 assets, never truncated; synchronous HTTP for authoring and configuration writes, Jobs for ingestion and backtests, a bounded synchronous call with a hard timeout for AI requests (§4, S5); `claude-sonnet-5-5` provisional and configurable, AI disabled until a key and explicit limits exist; M2 order Pine export and equivalence → visual builder → portfolio mode.

---

## 2. Architecture at a glance

```
console (Next.js, existing shell)            FastAPI (existing app, research routers added)
  /research/strategies  ─ YAML editor ──────▶ /api/v1/research/strategies/*   ─▶ spec validator ─▶ strategy_drafts / strategy_versions
  /research/strategies  ─ assistant panel ──▶ /api/v1/research/assistant/*    ─▶ AI service (bounded, provenance) ─▶ same validator
  /research/assets      ─ search, lists ────▶ /api/v1/research/catalog/*, /asset-lists/*  ─▶ asset_catalog (Tiingo public list + metadata names)
  /research/studies     ─ wizard, readiness ▶ /api/v1/research/studies/*      ─▶ study_revisions, readiness service
  (existing Job UI)     ─ progress ─────────▶ /api/v1/jobs/*  (existing)      ─▶ Jobs: ingest_tiingo_bars → freeze → research_backtest ×N → research_evaluate
  /research/studies/[id]─ results ──────────▶ /api/v1/research/studies/{id}/results, comparison, freeze, final-test, exposures, exports

engine: existing day-by-day backtester + research settings (provider/adjusted explicit, quantity policy, one-asset universe)
strategies: existing BaseStrategy + DeclarativeDailyStrategy (interpreter) resolved by version id in research mode
data: isolated trading_research DB (0001–0030 fresh + 0031_research_platform); Tiingo adapter; integrity; data_freeze; inputs export
```

Boundaries kept: the trading registry, execution services, reconciliation, recovery, Alpaca, migrations 0022–0030 and the trading console pages do not change. Research mode is a process-level switch that registers research job types only and mounts research routers only.

---

## 3. Reuse map

| Existing | Role in the research product | Change needed |
| --- | --- | --- |
| `services/backtesting.py` day-by-day engine, next-open fills, bps slippage, per-order commission, trades/signals/equity persistence | The research engine | Research settings: explicit `provider`/`adjusted` on reads, `quantity_policy`, one-asset universe and one slot per run; `rounding_slack` recorded per trade |
| `strategies/base.py`, `signals.py`, `registry.py`, four strategies | Base class, signal types, parity oracles, seeded examples | Three generic `SignalReason` members; a research registry keyed by version id; strategies pass `provider`/`adjusted` explicitly |
| `services/market_data_access.py` (`bars_for_sessions`, `missing_sessions_for_symbol`, `bar_counts_through_session`) | Bar loading, coverage and warm-up checks | Signatures unchanged; callers pass provider and adjusted |
| `services/ingestion.py`, `services/polygon.py`, `market_data_ingestion_runs` | Pattern for the Tiingo adapter and ingestion Job | New adapter module; the run-bookkeeping and upsert pattern reused |
| `services/calendar.py`, `market_sessions`, `sync_market_sessions` | Session calendar | Research-pinned calendar start setting |
| `services/evaluation_manifest.py`, `read_recording.py` | Bar-read digests for `input_digest` | Wired into research runs |
| `services/backtest_reporting.py` (`_compute_metrics`, `export_backtest_report`) | Metric computation and per-run exports | Metric pins (sample variance, `null` profit factor, closed vs open trades), new descriptors |
| Job framework (`jobs/*`, registry, handlers pattern, dependencies, cancellation) and generic Job UI | Run orchestration and progress | New handlers only; nothing under `jobs/queue.py`, `lifecycle.py`, `runner.py`, `dependencies.py`, `cancellation.py` |
| `core/settings.py`, `core/log_sanitizer.py` | Configuration, secret redaction | `research` settings block; Tiingo key alias; AI settings |
| `tests/support/migrated_db.py`, `tests/conftest.py` fixtures | Throwaway migrated databases | Reused for the research DB and the smoke run |
| Console shell, nav, `lib/api.ts`, `useApiQuery`, Job components, `EquityCurveChart`, `SummaryMetricsPanel`, `RunsTable`, boundary tests | Research pages | New pages and components; boundary and route-inventory tests extended |

---

## 4. Stages

Each stage lists its goal, dependencies, changes, validation and review units. "Review unit" means one PR-sized change with its own tests.

### S0. Research foundation
**Goal:** a research process can run against an isolated database with Tiingo data, with the trading path provably unchanged.
**Depends on:** nothing.
**Changes**
- Settings: a `research` block (`mode`, `bar_provider`, `bar_adjusted`, `calendar_start`, `max_assets_per_study`, `quantity_policy_default`, `tiingo.api_key` with alias `TIINGO_API_KEY`, `ai.*`). Defaults keep trading behaviour. **[Chosen]**
- Migration `0031_research_platform` (planned, not written): tables `research_studies`, `study_revisions`, `strategy_drafts`, `strategy_versions` (append-only trigger), `ai_drafts`, `asset_catalog`, `asset_lists`, `asset_list_items`, `data_freezes`, `research_freezes`, `test_window_exposures`; nullable `strategy_runs` columns (`study_revision_id`, `strategy_version_id`, `asset`, `window_role`, `spec_sha256`, `code_sha`, `input_digest`, `rerun_of`); `daily_bars.volume` → `BIGINT`, nullable `split_factor`, `dividend_cash`; `backtest_trades.rounding_slack`. Phase 21 renumbers its `0031` if it resumes. **[Chosen]**
- `scripts/create_research_db.py`: creates `trading_research` and upgrades to head using the `migrated_db.py` approach; prints server and database. **[Chosen]**
- Research-mode registry: `TRADING_PLATFORM_RESEARCH_MODE=1` registers exactly `catalog_sync`, `ingest_tiingo_bars`, `sync_market_sessions`, `research_backtest`, `research_evaluate`; research routers mount only in research mode. **[Settled: trading frozen]**
- Provider and calendar plumbing: engine and strategies pass `provider`/`adjusted` from settings; `get_calendar()` honours `research.calendar_start` when set.
- Tiingo adapter: metadata and prices, header auth only, `token` query parameter forbidden, 50-per-hour pacing, 429 → `provider_rate_limited`; raw and adjusted rows plus factors per session; `ingest_tiingo_bars` Job scoped to explicit assets and range.
- Catalog sync Job: download the public `supported_tickers.zip`, filter to US Stock and ETF in USD on NYSE, NASDAQ, NYSE ARCA, BATS, AMEX, upsert `asset_catalog`; names joined from the public Nasdaq Trader symbol directory and the SEC `company_tickers.json` (`name_source` per row, `.`/`-` class-share normalisation); Tiingo metadata fills gaps on view. Name search covers populated names only and the UI says so with a named-versus-total count. Refresh is a manual Job initially.
- Integrity checks (proposal Part J.2), `data_freeze`, `inputs/` export with `MANIFEST.json`, restore script.
**Validation:** acceptance 1–6 of the proposal; trading-path invariance (default-config evaluation-manifest parameters byte-identical; existing suite green); migration round-trip on a throwaway DB; adapter mock-transport tests; integrity fixture per code; freeze refusal after a later ingest.
**Review units:** (a) settings + migration + create script + registry switch; (b) provider/calendar plumbing + invariance tests; (c) Tiingo adapter + ingestion Job + catalog sync; (d) integrity + freeze + export + restore.

### S1. Specification and interpreter
**Goal:** a validated specification runs through the existing engine and matches the four originals.
**Depends on:** S0 (b) for provider plumbing; otherwise independent of S0 (c)(d).
**Changes**
- `strategies/spec/`: pydantic schema (`extra="forbid"`), canonicaliser, `spec_sha256`, derived values (`history_required`, `history_minimum`, `scale_class`), error codes, unit check, explanation renderer. **[Chosen layout]**
- `strategies/declarative.py`: `DeclarativeDailyStrategy(BaseStrategy)` computing the §3 terms with the pinned formulas, the §5 slicing rules (shifted recursive windows, offset-1 values for crossings computed from their own complete windows) and the §6 stateless semantics; loads exactly `history_required` bars per asset through `bars_for_sessions`.
- `SignalReason` gains `rule_entry`, `rule_exit`, `rule_no_signal`, `rule_insufficient_history`.
- Research strategy registry resolving `strategy_version_id` → interpreter instance; the static trading registry untouched.
- Four example specifications and the seeding command, gated on parity.
**Validation:** the §9 parity matrix including the RSI history-dependence proof and the early-history boundaries; the §9 items 8–10 interpreter tests (shifted RSI/EMA, crossings with full-window offset-1 values, boundaries per term type); validator fixture per error code; unit-check fixtures (`close > 100` → `price_scale_dependent`; `close > volume` → `unit_mismatch`); explanation hash pins; determinism.
**Review units:** (a) schema, validator, canonicaliser, explanation; (b) interpreter + parity tests; (c) registry + seeding.

### S2. Versions and authoring API
**Goal:** drafts, validation, explanation and approval into immutable versions, over HTTP.
**Depends on:** S1 (a).
**Changes:** `services/research/strategies.py` (draft CRUD, validate, explain, approve, duplicate, version listing, lineage); router `/api/v1/research/strategies/*`; immutability trigger test; `source` and `ai_draft_id` on versions. Synchronous HTTP writes in research mode. **[Settled default]**
**Validation:** acceptance 11; a version cannot be approved from an invalid draft; UPDATE/DELETE on versions refused; approval does not create a study or touch any trading table (query-count and table-touch test).
**Review unit:** one.

### S3. Study engine and evaluation
**Goal:** a study revision runs per-asset backtests and benchmarks as Jobs and produces metrics, evidence, ranking, verdict, freeze and final test.
**Depends on:** S0 (c)(d), S1 (b), S2.
**Changes**
- Study model and revisions; readiness service with the closed codes; `research_backtest` Job (one asset, one slot, quantity policy, benchmark flag) and `research_evaluate` Job (metrics, descriptors, grades, ranking by objective, verdict, `comparison.json`); the initial Job dependency graph per revision covers the development and validation windows and their benchmarks only.
- Final test as a separate action (`run_final_test`): refused without a freeze record; builds its own two-run graph (frozen candidate and its benchmark on the final-test window) and nothing else.
- Engine: `quantity_policy` with the corrected rounding and `rounding_slack`; `buy_and_hold_single_asset` benchmark strategy; run columns filled; `input_digest` via the manifest recorder.
- Metric pins and descriptors in `backtest_reporting.py`; anchored event clusters; grades.
- Freeze record, final-test lock, outcomes, exposure log with `results_inspected_at`; the global exposure query by (asset, overlapping range) across all studies, revisions, families and versions.
**Validation:** acceptance 12–19; rounding tests (`quantity × price ≤ affordable`; slack equals the actual residual under both policies; `zero_quantity_fill` when the quantity rounds to zero under the selected policy); scale tests at k = 0.01 and 100 for scale-free fixtures with the policy tolerance; exposure visibility across families and duplicates; inspected-state flip on results read; **a regression that the initial study graph creates no `final_test` run and the results reads expose no final-test data before a freeze**.
**Review units:** (a) study model, readiness, Jobs; (b) engine quantity policy + benchmark + run columns; (c) metrics, descriptors, grades, ranking, verdict; (d) freeze, final test, exposures.

### S4. Research UI
**Goal:** the first usable end-to-end product: create a strategy, choose assets and settings, validate, run, compare.
**Depends on:** S2 for strategy pages; S0 (c) for assets; S3 for studies and results. Strategy and asset pages can start as soon as their APIs exist.
**Changes:** `console/src/app/research/*` pages and `components/research/*`; `lib/api.ts` extended; YAML editor (plain textarea with line numbers first, an editor component later **[Chosen]**); explanation and validation panels; catalog search and saved lists; study wizard with per-study objective, constraint, costs (no pre-filled figures), quantity policy, windows; readiness panel; progress via existing Job components; results pages (per-asset Return and Risk tables, equity and drawdown charts, descriptors, ranking with reasons, benchmark comparison shown separately, limitations, final-test block, exposures with context and links); exports.
**Validation:** acceptance 22; vitest rendering tests from `comparison.json` fixtures for every verdict and outcome; boundary and route-inventory tests extended; a "nothing omitted" test on the wizard (a `not_ready` study cannot be submitted and shows every reason).
**Review units:** (a) strategies pages; (b) assets pages; (c) study wizard + readiness + progress; (d) results + exports (static report renderer lives here, stdlib + inline SVG).

### S5. AI drafting assistant
**Goal:** describe → draft → inspect → revise → approve, bounded and recorded.
**Depends on:** S1 (a), S2; UI panel depends on S4 (a).
**Changes:** `services/research/assistant.py` using the Anthropic Python SDK: one `messages.create` per draft or revision with a structured output format equal to the specification JSON schema plus `unsupported_requests` and `note`; stable cached system prompt; no tools; hard timeout; one automatic retry on validation failure; counters for per-day requests and tokens; provenance rows; closed failure codes; disabled unless `research.ai.enabled`, an API key and both limits are set. **Execution approach [Chosen]:** a synchronous request inside the HTTP handler with a strict timeout (tens of seconds) and a per-process concurrency cap, because a draft is a short single call and the user is waiting; if measured latency breaks that, the fallback is a `research_ai_draft` Job reusing the existing progress UI, without changing the service contract.
**Validation:** acceptance 20 with a mock provider; key never present in stored bodies or logs; disabled-by-default test; limit and refusal paths.
**Review units:** (a) service + config + provenance; (b) assistant panel.

### S6. Integration smoke run and first substantive study
**Goal:** prove the pipeline end to end on real Tiingo data before any research claim.
**Depends on:** S0–S4 only. The assistant (S5) is not required: the smoke run uses a YAML-authored or example version with AI disabled. S5 gets its own integration check, run only when the assistant is configured and enabled, and its outcome never changes the research smoke result.
**Changes:** a smoke study kind with its banner; a documented runbook: create the research DB, sync the catalog, pick two assets, one example version, about three years, run, inspect, drop the throwaway DB.
**Validation:** acceptance 23 (research smoke, AI disabled) and, separately and only when configured, acceptance 24 (assistant check). Then the first substantive study, created in the UI with the user's own assets, objective, constraint and costs.

---

## 5. Sequence and parallelism

```
S0(a) ─▶ S0(b) ─▶ S1(a) ─▶ S1(b) ─▶ S1(c) ─┐
   └───▶ S0(c) ─▶ S0(d) ───────────────────┼─▶ S3(a..d) ─▶ S4(c)(d) ─▶ S6
                                S2 ◀────────┘      ▲
                                 └─▶ S4(a) ─▶ S5(a)(b)
                      S0(c) ─▶ S4(b) ───────────────┘
```

Two tracks can run side by side after S0(b): the data track (S0 c, d) and the specification track (S1, S2). S4(a) and S4(b) start before S3 finishes. S5 is independent of S3.

**First usable increment:** S0 + S1 + S2 + S3 + S4 give the end-to-end product with YAML authoring and are what the S6 smoke run validates; S5 adds the assistant and can land before or after S6 without affecting it. Earlier checkpoints that are demonstrable on their own: after S1, a seeded example runs through the existing backtest Job in research mode; after S2, strategies can be authored and approved over the API; after S3, a study runs headless with `comparison.json`.

---

## 6. Data model (planned for `0031_research_platform`)

| Table | Key columns | Notes |
| --- | --- | --- |
| `strategy_drafts` | id, title, yaml_text, source, ai_draft_id, parent_version_id, updated_at | mutable |
| `strategy_versions` | id, strategy_id (family), version_no, yaml_text, spec_json, spec_sha256, history_required, history_minimum, scale_class, explanation, source, parent_version_id, ai_draft_id, behaviour_differs_from_original, approved_at | append-only trigger; unique (strategy_id, version_no) |
| `ai_drafts` | id, provider, model, prompt_version, request_id, input_tokens, output_tokens, user_text, output_spec_sha256, validation_result, failure_code, created_at | bodies stored without secrets |
| `asset_catalog` | provider, ticker, exchange, asset_type, currency, catalog_start, catalog_end, name, names_fetched_at, synced_at | unique (provider, ticker) |
| `asset_lists`, `asset_list_items` | id, name; list_id, ticker, position | |
| `research_studies` | id, name, kind (`smoke`, `substantive`), created_at | |
| `study_revisions` | id, study_id, revision_no, settings_json (versions, assets snapshot, dates, windows, capital, quantity_policy, costs, objective, constraint, mode), ranking_criteria_hash, created_at | immutable |
| `data_freezes` | id, study_revision_id, input_digest, frozen_at, inputs_path | |
| `research_freezes` | id, study_revision_id, candidate (version id, asset), acceptance_json, ranking_criteria_hash, code_sha, input_digest, calendar_start, frozen_at, co_leading_choice_reason | one per revision |
| `test_window_exposures` | id, asset, range_start, range_end, input_digest, study_id, study_revision_id, strategy_id, strategy_version_id, run_id, reason, run_recorded_at, results_inspected_at | queried by asset and range overlap |
| `research_run_links` (1:1 with `strategy_runs`) | run_id (PK, FK), study_revision_id, strategy_version_id, asset, window_role, spec_sha256, code_sha, input_digest, rerun_of | unique (study_revision_id, strategy_version_id, asset) where window_role = final_test and rerun_of is null; `strategy_runs` itself is untouched |
| `backtest_trades` (+column) | rounding_slack | |
| `daily_bars` (changes) | volume BIGINT; split_factor, dividend_cash nullable | |

---

## 7. Open decisions, localized

| Decision | Affects | Default if unanswered |
| --- | --- | --- |
| **[Open]** AI per-day request limit and per-request output-token limit values | S5 (a) configuration values only; the code path is unaffected | AI stays disabled |
| **[Open]** Spend limit on the Anthropic console | outside the codebase | AI stays disabled |
| **[Open]** YAML editor component (plain textarea vs a code editor dependency) | S4 (a) only | textarea first |

Per-study inputs (assets, dates, windows, capital, costs, objective, constraint values) are entered in the UI and block nothing.

---

## 8. Risks and mitigations

- **Parity surprises** in S1 (b): a Decimal division-order or rounding difference between the originals and the interpreter. Mitigation: indicator values compared at tolerance 0 before signals; fix the interpreter, never the originals.
- **Rate limits** in S0 (c): 50 requests per hour on the free plan bounds a 10-asset study to one ingestion pass per hour if names are fetched too. Mitigation: names on first view only; ingestion requests counted and paced; a visible `provider_rate_limited` failure.
- **Calendar bound drift** (library default moves daily): pinned research start recorded on every revision; readiness refuses a study whose warm-up precedes it.
- **Engine shared with trading:** every engine change is behind research settings with trading-default tests (acceptance 3).
- **Migration numbering collision with the frozen Phase 21 plan:** documented; Phase 21 renumbers if resumed.
- **Licence:** Tiingo-derived data stays under `.data/research/` and the research DB; a test pins the git-ignore.

---

## 9. Status: S0 and S1 implemented (2026-10-07, uncommitted)

Implementation of S0 and S1 landed in the working tree (not committed, not pushed). The main database was not touched; every database-backed test uses a throwaway database migrated to `0031`.

**S0 delivered**
- `research` settings block with trading-preserving defaults; `TIINGO_API_KEY` alias (process env, then the dotenv file the settings already read), prefixed form wins; AI settings disabled and unusable until key and both limits exist.
- Calendar start pin (`pin_calendar_start`) applied at the single startup chokepoint; no pin means the library-default call, byte for byte.
- Migration `0031_research_platform` (tables per §6, the `research_run_links` 1:1 linkage table instead of columns on `strategy_runs` so the shared table, its ORM model and the historical-revision migration tests stay untouched, `backtest_trades.rounding_slack`, `daily_bars.volume` BIGINT plus factor columns, append-only trigger on `strategy_versions`, partial unique index for one non-rerun final test); round-trip verified.
- Engine and the four strategies pass `provider`/`adjusted` explicitly (`BaseStrategy.bar_source`); the recorded evaluation-manifest parameters of a default-config read are pinned byte-identical.
- Tiingo adapter (header auth only, token query parameter refused, closed failure codes, in-process hourly budget that fails visibly), research ingestion writing raw and adjusted rows with factors and one ingestion-run row, catalog sync from the public Tiingo list joined with Nasdaq Trader and SEC names (`name_source` per row; name search covers populated names only), integrity checks (pure per-asset function plus the DB report), data freeze with `inputs/` export, change detection, digest verification and restore, `scripts/create_research_db.py` (refuses the trading database name).
- Research-mode Job registry (`catalog-sync`, `ingest-tiingo-bars`, `sync-market-sessions`) selected by `research.mode`; the trading registry is unchanged and its pins still pass.

**S1 delivered**
- `strategies/spec/`: schema, validator with every closed error code, §5 history derivation (shift adds bars; crossings evaluate both operands at offsets 0 and 1 from complete windows), unit check and scale class, canonical JSON and `spec_sha256` (numeric constants normalised so `30`, `30.0` and `"30"` hash alike), deterministic explanation.
- `DeclarativeDailyStrategy` interpreter with formulas pinned to the originals; generic `SignalReason` members; research registry keyed by version id with a stored-hash check; four example specifications and an idempotent seeder.
- Parity matrix passed for all four originals (full series including below/at/beyond history, a gap inside the window, threshold adjacency, exit-and-entry both true, RSI history dependence shown real), so the examples may be seeded. No `behaviour_differs_from_original` entry was needed.

**Implementation choices made here (changeable without a product decision)**
- Below warm-up the originals still expose partial indicator values while the interpreter exposes none; directions agree (`FLAT`), and parity compares values only at or beyond warm-up.
- `ORDERING` is reported for a future-dated bar; `DUPLICATE_KEY` and `DATE_INVALID` are unreachable through the database constraints and the adapter respectively, and are covered by the pure row-level check.
- The AI request path is not built (S5); only its configuration exists.

**Limitations carried into S2–S3**
- `research_run_links` and `rounding_slack` exist but nothing writes them yet; the engine's quantity policy is still whole shares (S3).
- The research registry builds from all `strategy_versions` rows; approval status lives on the draft/version flow of S2.
- Catalog name search returns only populated names; the UI banner and counts are S4.
- The Tiingo adapter budget is per process; two research processes do not share it.
- The smoke run (S6) has not been executed; no Tiingo data was ingested in this pass beyond the revision-4 read-only probe.

## 10. S0/S1 validation and review (2026-10-07, after a context reset)

**Final suite.** The last full run on the current tree (started after the `research_run_links` correction, output preserved in the session scratchpad as `final2_pytest.txt`): 3571 passed, 2 failed, 1 error, 398 warnings, 19m39s. No source, test, migration or config file changed between that run and the review. The two failures are the pre-existing baseline pair (`tests/test_api_reads.py::test_strategy_analytics_and_run_reads_match_shared_services`, `tests/test_app_boot.py::test_app_bootstrap_serves_foundation_endpoints`, both `assert 10 == 3` on `universe_size`), present in the 2026-10-04/05 pre-pivot runs and in the 2026-10-07 baseline (3461 passed, same 2 failed). The error is a teardown-only `InsufficientPrivilege` on `DROP DATABASE ... WITH (FORCE)` in `tests/test_alpaca_execution.py` (the test body passed); the same teardown error appears in the 2026-10-05 pre-pivot log, so it is an environment privilege issue, not a research regression. It leaves `alpaca_execution_*` databases behind on the local server (63 at review time); they are not touched by this work.

**Final suite after the review fixes** (`final3_pytest.txt`, 17:20–17:40 IDT): 3577 passed (the six new regression tests included), the same 2 baseline failures, the same single teardown-only error, exit 1 from those alone.

**Database revisions verified.** `trading_platform` → `0021_phase20_operations_safety`; `trading_research` → `0031_research_platform`.

**Review fixes applied (localized, with regression tests).**
1. `services/research/tiingo_ingestion.py`: provider-level failures (`TiingoAuthError`, `TiingoRateLimitError`, `TiingoRequestBudgetExceededError`, the closed set `PROVIDER_LEVEL_ERRORS`) now abort the run at the first occurrence, finalize it FAILED with the closed code as `error_message` (`provider_rate_limited`, `provider_budget_exhausted`, `provider_auth_failed`) and propagate so the Job fails visibly. Before, they were swallowed per asset like a bad payload, every remaining asset burned a further request against the exhausted budget, and a mixed run landed as a *succeeded* Job with `outcome: partial`, contrary to the handler docstring and proposal Part G ("a 429 fails the Job visibly"). The client is now constructed before the run row, so a missing key refuses without leaving a `running` run behind.
2. `services/research/catalog.py`: `enrich_asset_name` is a no-op once `names_fetched_at` is set, so an asset whose provider metadata carries no name is asked once, not on every view (acceptance 5).
3. `strategies/spec/explain.py`: recursive terms print the smoothing window the user wrote and the history fed to the recursion; the earlier text printed the history as the EMA window and omitted the RSI period in the text a user approves.
4. `strategies/spec/validate.py` + `schema.py`: `rsi` window is bounded to 2..200 as the contract §3 states (was 1..500 with only the lower bound checked).

**Document corrections.** Proposal acceptance 9 restated to the stateless Part D semantics (its revision-6 text still carried the withdrawn "entry only when flat" wording); acceptance 2 notes the three-of-five registry pin; contract §12 records that a series operand carries no `shift` field (use `lag`), the rsi bound, and the explanation wording.

**Reviewed and found consistent (no change).** Research-DB isolation (throwaway migrated DBs in every DB-backed test; `create_research_db.py` refuses the trading name; main DB untouched); `build_registry_for` selection and the closed research registry; calendar pin applied at the single startup chokepoint and cleared outside research mode (exchange_calendars 4.13 caches factory output by `(name, start, end, side)`, so pinned and default calendars never alias); explicit `provider`/`adjusted` on every strategy and engine read with the manifest-parameter byte pin; migration 0031 (FKs, 1:1 `research_run_links` keyed by `run_id` with CASCADE, partial unique index for one non-rerun final test, append-only trigger, BIGINT volume, round-trip downgrade/upgrade, historical-revision migration tests green); Tiingo adapter (header auth only, token query refused, 429/401/403/budget closed codes, first-ten-character dates); catalog scope, name join and `name_source`; integrity codes (each with a fixture; the adjustment-ratio tolerance of 1e-9 is three orders above the 1e-12 spread observed on real Tiingo rows in the revision-4 probe); freeze digest, change detection, export under `.data/research/` (git-ignored) and digest-verified restore; validator error codes, nesting/condition/indicator limits, unit check and scale class, canonical JSON and hash; §5 slicing, fresh offset-1 recursion for crossings, early-history boundaries per term type; stateless exit-before-entry; the parity matrix for all four originals (seeded only after it passes; no `behaviour_differs_from_original`).

**Limitations noted for later stages.**
- In research mode the API still mounts every trading router (only the Job registry is research-only); S2 should add the research routers with a mount-exclusivity pin, and S3 should pin that no trading mutation route is reachable in research mode.
- RSI history dependence (§9 item 3) is proven at the formula level (`compute_rsi` at 50/100/300) and the wiring of the `history` parameter through the interpreter by §9 item 8; a spec-level `history: 50` versus `history: 100` signal comparison is not a separate test.
- The RSI threshold-adjacency fixture compares every date whose RSI lies within 2 points of 30 or 70 on a natural series (values at tolerance 0), rather than constructing closes within 0.01 of the thresholds.
- `research_run_links`, `rounding_slack` and the quantity policy are written by nothing yet (S3); `restore_inputs` expects an empty target scope (it inserts, it does not upsert).
- The Tiingo request budget is per process.
- No proposal acceptance-1 path-diff test exists; the frozen paths were verified by `git diff --stat` at review time (no change under `services/execution/`, `services/reconciliation/`, `services/alpaca.py`, `services/recovery.py`, `alembic/versions/0022`–`0030`, or the console).

## 11. Gap closure and S2 (2026-10-07, uncommitted)

**Gap 1: research-mode API isolation (closed).** `create_app(research_mode=None)` reads `settings.research.mode` and mounts either the trading routers or the research routers, never both; the infrastructure routers (`health`, `jobs`, `job_types`) are mounted in both modes. The research surface lives in `src/trading_platform/api/research/` (outside `api/routes`, whose eight-route mutating allowlist stays pinned and untouched). The lifespan raises if `research.mode` at startup differs from the mode the routers were built for. Tests (`tests/test_research_api_isolation.py`): the research route inventory is pinned as an exact set (12 infrastructure + 15 research routes); the trading inventory is unchanged (no `/api/v1/research` path, exactly the eight mutating routes); nine representative trading mutations and reads on a research process are route misses (404) with spies on `OperatorControlService`, `run_paper_session` and `AlpacaClient` proving nothing was reached and with zero rows written; every trading Job type submitted to a research process is `unknown_job_type` with zero Job rows while `catalog-sync` is accepted; the research writes are 403 `mutations_disabled` when mutations are off; an AST pin of the research package mirrors the trading allowlist pin.

**Gap 2: RSI history regression through the interpreter (closed).** Contract §12 records the two tests; the in-memory one asks the loader for exactly 50 and 100 bars, finds a direction disagreement, and matches the original strategy at `history: 100` on every session; the database one does the same through approved versions and the research registry on real `bars_for_sessions` reads. No defect was revealed by either.

**S2 delivered.** `services/research/strategies.py` (`ResearchStrategyService`, `validate_yaml_text`) and `api/research/strategies.py` under `/api/v1/research/strategies`:

| Method and path | Behaviour |
| --- | --- |
| `GET` | families: one row per `strategy_id` with latest version and count; `as_of` |
| `GET /drafts`, `POST /drafts` | list; create (`title`, `yaml_text`), 201 |
| `GET /drafts/{id}`, `PUT /drafts/{id}`, `DELETE /drafts/{id}` | read; edit title and/or text; delete |
| `GET /drafts/{id}/validation` | the shared validator's findings, or derived values (`spec_sha256`, `history_required`, `history_minimum`, `scale_class`, terms, operators) and the deterministic explanation |
| `POST /validate` | the same for submitted `yaml_text`; writes nothing |
| `POST /drafts/{id}/duplicate` | a new manual draft with the same text (lineage kept), 201 |
| `POST /drafts/{id}/approve` | explicit approval: validate, allocate `version_no` under a per-family `pg_advisory_xact_lock`, insert the immutable version (canonical `spec_json`, `spec_sha256`, explanation, derived values from the same `validate` call), delete the draft, one transaction; 201, or 422 `draft_invalid` with every finding, or 404 once approved |
| `GET /versions[?strategy_id]`, `GET /versions/{id}`, `GET /versions/{id}/lineage` | listing; detail with YAML, canonical JSON and explanation; ancestors root-first |
| `POST /versions/{id}/edit` | a draft in the same family (`edit_of_version`, `parent_version_id`), 201 |
| `POST /versions/{id}/duplicate` | a draft that approves into a new family (`duplicate_of_version`, `parent_version_id`), 201 |

Closed error codes: `draft_not_found`, `version_not_found`, `draft_invalid`, `invalid_draft_input`, `invalid_request`, `mutations_disabled`. Draft sources: `manual`, `assistant`, `edit_of_version`, `duplicate_of_version` (the version keeps the draft's source; the examples keep `example`). No schema change was needed: 0031 already carries `strategy_drafts.parent_version_id` and the append-only trigger.

**Guarantees and their tests** (`tests/test_research_strategies_api.py`): invalid drafts cannot be approved (422, no version); approval stores hash, explanation and derived values consistent with an independent `validate_yaml` of the same text; UPDATE and DELETE on `strategy_versions` are refused by the trigger; editing and duplicating an approved version create drafts with lineage; four concurrent approvals of edits of one version get version numbers 2..5 with no integrity error, and three concurrent approvals of one draft yield exactly one version and two `draft_not_found`; across the whole flow the counts of `jobs`, `job_mutations`, `strategy_runs`, `strategies`, `paper_orders`, `execution_operations` and `system_controls` stay unchanged while `AlpacaClient` and `JobOrchestrationService.submit` are patched to fail if reached; the process is in research mode on a throwaway research database.

**Final suite after S2** (`final4_pytest.txt`, 2026-10-07 evening): 3608 passed (31 new tests), the same 2 baseline failures, 2 teardown-only errors in `tests/test_alpaca_execution.py` (both the `InsufficientPrivilege` on `DROP DATABASE ... WITH (FORCE)`; both also present in the 2026-10-05 pre-pivot log). One intermittent failure of `test_create_research_db_script_creates_migrates_refuses_trading_name` was seen once in a 20-file slice and not in three reruns; the single leftover `research_script_b3eaa905` database proves it failed in its teardown, which terminated backends without the `usename = current_user` filter (the test role is not a superuser, so an autovacuum worker on the fresh database raises InsufficientPrivilege, the same mechanism as the alpaca_execution teardown errors). The filter is now applied; the leftover database was left for the operator to drop.

**Limitations carried forward.**
- *Per-process Tiingo request budget (to resolve before parallel ingestion in S3):* `TiingoClient` keeps its sliding hourly window in memory, so two research processes (or two workers) ingesting at once each see a full 50-request budget and can jointly exceed the plan's limit; a 429 then aborts the run visibly. S3 must either serialize ingestion Jobs (one worker, or a Job-level mutual exclusion on `ingest-tiingo-bars`) or move the budget into the research database (a request ledger consulted before each call). It does not block S2.
- The research strategy registry still builds from every `strategy_versions` row; "approved only" is the table's invariant (every row is an approval), so no filter is needed until drafts gain states.
- Research-mode processes do not serve `/api/v1/system`, `/api/v1/runs` or market-data reads; S3 decides which research reads replace them.
- The research surface has no console pages yet (S4).

