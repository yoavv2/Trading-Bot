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

## 12. S3: shared budget, study engine and evaluation (2026-10-07, uncommitted)

**Shared Tiingo request budget (prerequisite, closed).** `services/research/budget.py` `DatabaseRequestBudget`: admission is one short transaction that takes `pg_advisory_xact_lock(provider)`, counts `provider_request_ledger` (migration 0032) for the sliding hour and day and the calendar month's distinct symbols, refuses with `BudgetExhaustedError` (`provider_budget_exhausted`, naming the limit) when a limit would be exceeded, else inserts the attempt row (provider, symbol, purpose, process id, Job id) and commits; the outcome (`ok`, `rate_limited`, `auth_failed`, `transport_error`, `http_error`) and status code are written after the response. `TiingoClient` now requires a budget and admits **every HTTP attempt**, retries included, so accounting matches what the provider sees; ingestion and name enrichment both construct the database budget, and the adapter's unit tests use `UnlimitedRequestBudget`. Limits come from `research.tiingo.requests_per_hour` / `requests_per_day` / `unique_symbols_per_month`, defaulting to the verified free plan (50 / 1,000 / 500) and configurable to the account's entitlement. Tests (`tests/test_research_budget.py`): twelve threads against a limit of five admit exactly five; two spawned OS processes with their own engines jointly admit exactly the limit of eight; a restarted process (new instance, new process id) is refused by the attempts of its predecessor and admitted once the hour slides; daily and monthly-symbol limits; ingestion charges every attempt with purpose and symbol, stops at the shared limit with the run FAILED as `provider_budget_exhausted`; a 429 is recorded as `rate_limited` and never retried; name enrichment goes through the same ledger. **Limit:** requests made outside this application (the website, another tool, a second key) are not observable and are not counted; configure the limits with that margin; a 429 still fails visibly.

**Studies and revisions.** `services/research/studies.py` `StudyService` with the immutable `StudySettings` snapshot (mode, approved version ids, asset snapshot or saved list, range, the three windows, capital, quantity policy, costs, objective, constraint value, provider/adjusted, note); `create_study` creates revision 1, `create_revision` the next number under a per-study advisory lock. Readiness lists every failing item with the H.3 codes (`mode_not_supported_yet`, `asset_limit_exceeded`, `costs_missing`, `objective_missing`, `constraint_missing`, `windows_overlap_or_unordered`, `window_outside_range`, `calendar_start_not_pinned`, `strategy_version_not_approved`, `asset_not_in_catalog`, per (version, asset) `warmup_not_satisfiable` / `coverage_start_too_late` / `coverage_end_too_early`, `integrity_error` from the last freeze Job, `inputs_changed_after_freeze`); the required start per pair is the session `history_required` sessions before the development window on the pinned calendar. Nothing is dropped or defaulted: the run endpoint refuses with the same error list.

**Job graphs.** `StudyService` never imports the Job framework (JOB-04, pinned by `tests/test_job_import_boundary.py`): it receives a `job_submitter` seam, and `orchestration/research_studies.py` `build_study_service` supplies `jobs.dependencies.submit_job` to the instances the API and tests use; the Job handlers use a plain service. `run_initial` submits, in one transaction under the revision lock: `ingest-tiingo-bars` (assets, min required start, range end) → `research-freeze` → one `research-backtest` per (version, asset, window ∈ {development, validation}) plus a benchmark per (asset, window) → `research-evaluate(initial)`; the evaluate Job's `study_revision_jobs` row is the anchor of the partial unique index `uq_study_revision_jobs_one_graph_per_scope`, so a concurrent second start fails as `run_already_started` with nothing written. No `final_test` run exists after the initial graph (`final_test_runs_created: 0`, pinned). `run_final_test` (freeze-gated) submits exactly the frozen candidate and its benchmark on the final-test window plus `research-evaluate(final_test)` and records a `test_window_exposures` row (`run_recorded`); a second non-rerun graph is `final_test_already_run`, enforced by the same index under concurrency; `rerun=true` requires a completed final test and links each run with `rerun_of`.

**Runs.** `services/research/backtest.py` `run_research_backtest`: `strategy_runs` row (`trigger_source research_job`, parameters snapshot with the explicit `EngineOptions`, provider/adjusted, window, `spec_sha256`, `code_sha`, `rerun_of`), 1:1 `research_run_links` row, the bar source passed explicitly to the strategy and the engine, `input_digest` from every recorded accessor read, `results_digest` over the persisted trades and equity curve, metrics and evidence in `result_summary["research"]`. Engine (`services/backtesting.py`): `EngineOptions` (trading defaults reproduced by `from_settings`, pinned), `quantity_policy` fractional (six decimals, `ROUND_DOWN`) or whole shares, `rounding_slack` = the actual residual on research rows (`None` on trading rows), `zero_quantity_fills` counted, one slot and the study capital per research run. `BuyAndHoldSingleAssetStrategy` buys at the window's first close (filled next open), never exits, is marked at the last close; its net return exists with zero closed trades and is never gated.

**Metrics, evidence, ranking** (pure modules `metrics.py`, `evidence.py`, `ranking.py`): the I.2 table with `None` plus a note for every undefined case (sample standard deviation for Sharpe, `null` profit factor without losing trades, closed trades apart from `open_at_end`, `annualised_from_short_window`, `data_gap_affected`, `zero_quantity_fill`); descriptors, anchored 5-session clusters, grades at the pinned thresholds with the limitation text; statuses in order `not_evaluable` → `insufficient_evidence` → `not_eligible` → `eligible`, keys per objective with tie-breaks and `co_leading`, verdicts `leading_candidate_identified` / `insufficient_evidence` / `no_candidate_qualifies` (never a forced winner), profit factor never in the order, benchmark excess return always shown and never a gate; final-test outcomes in order with `thin_evidence`, the benchmark comparison separate. `comparison.json` (schema 1) is stored per evaluation in `study_evaluations` and served by `GET /comparison`.

**API** (`api/research/studies.py`): `GET/POST /api/v1/research/studies`, `GET /studies/{id}`, `POST /studies/{id}/revisions`; `GET /api/v1/research/revisions/{id}`, `/readiness`, `POST /run` (202), `GET /progress`, `/results`, `/comparison`, `/exposures`, `POST /freeze` (201; refuses `acceptance_incomplete`, `candidate_not_eligible`, `co_leading_choice_required`, `already_frozen`), `POST /final-test` (202; `{"rerun": true}` for a technical rerun), `POST /export` (writes `comparison.json`, per-run `summary.json`/`trades.csv`/`equity_curve.csv` under `.data/research/studies/<revision>/`). Reading `/results` or `/comparison` with a final-test evaluation, or exporting, flips the revision's exposures to `results_inspected`. Exposures are queried globally by asset and date overlap with the revision's final-test window across every study, revision, family and version, each with context (study, revision, version, outcome, state) and a link, plus the outside-visibility limitation text.

**Tests.** `tests/test_research_studies_pipeline.py` (readiness listing nine errors at once and refusing the run; coverage and warm-up per pair; the whole pipeline through the real Jobs with a stubbed provider: 15 Jobs, 12 runs on two windows only, benchmarks with zero closed trades and a return, concurrent run/freeze/final-test refusals, exposure states, a sibling study in a new family seeing the exposure, byte-identical rerun, a tampered rerun flagged `reproducibility_failure`, restoration from the preserved inputs reproducing the original digests, export, trading tables untouched), `tests/test_research_engine_metrics.py` (quantity policies and slack, scale invariance of the invested fraction, zero-quantity fills, trading defaults, every metric edge case, cluster and grade boundaries, both orders, ties, reject-all verdicts, final-test outcomes), `tests/test_research_budget.py`.

**Final suite after S3** (`final6_pytest.txt`, 2026-10-07 night): see the completion report; the first S3 run caught one real defect (`studies.py` importing the Job framework, `tests/test_job_import_boundary.py`), fixed by the orchestration seam above.

**Remaining limitations and blockers for S4.** No console pages: the API and `comparison.json` are the contract; the static report renderer is S4 (d). Study-level ingestion still downloads the whole `[required start, end]` per asset even when bars already exist (the upsert is idempotent; the request budget is charged). A revision's `integrity_error` items come from the last freeze Job only. The smoke run on real Tiingo data (S6) has not been executed.

## 13. S4: readiness contract, research console and static report (2026-10-07, uncommitted)

**Readiness presentation (closed first).** `StudyService._readiness` now returns `preflight` (`ready`, `errors`) and `inputs` (`state` ∈ `pending` | `failed` | `stale` | `verified`, `verified`, `errors`, `attempt` {job id, status, completed_at, failure_message, link}, `attempts`, `data_freeze` {id, frozen_at, digest, integrity summary}, `downstream`); `ready`/`errors` keep the preflight verdict for compatibility. The inputs verdict follows the latest freeze attempt of the revision (its linked Job and one level of operator retries): queued/running → `pending`; failed → `failed` with each integrity finding as `{code: integrity_error, item: asset, finding, severity, session_date, detail}` and the cancellation note (the framework cascades the graph's backtests and evaluation to `cancelled`); succeeded and unchanged → `verified`; succeeded but changed → `stale` with the reasons. A revision never shows another revision's verdict, and a failed attempt stays `failed` on its own revision. Tests: `tests/test_research_console_contract.py::test_readiness_separates_preflight_from_input_states` (pending → failed with 1 succeeded, 1 failed, 9 cancelled Jobs and no evaluation → a clean second revision verified → stale after a bar changes, the first revision still failed).

**New backend surface for the console.** `GET /api/v1/research/catalog` (coverage counts, the populated-names note, the coverage note, the per-study limit), `GET .../catalog/search?q=`, `GET .../catalog/assets/{ticker}`, `GET/POST /api/v1/research/asset-lists`, `GET/PUT/DELETE .../asset-lists/{id}` (`services/research/asset_lists.py`; a study created from a list snapshots its tickers and survives the list's deletion); `GET .../revisions/{id}/runs/{run_id}/curve` (equity, drawdown, exposure series); `GET .../revisions/{id}/report[?format=md]` (the static report; reading it counts as inspecting the results); `GET /health` carries `mode`. `services/research/report.py` renders `report.html` and `report.md` with the standard library and inline SVG charts from `comparison.json` plus the run curves; `export` writes both beside `comparison.json`. The research route inventory pin now lists 41 research routes.

**Console** (`console/src/app/research/*`, `components/research/*`, `lib/research/*`, `lib/useResearchMode.ts`; research helpers appended to `lib/api.ts`): Strategies (families with latest version and count, drafts, version history with parent links; the YAML editor with debounced validation through `POST /validate`, every finding with code, path and message, the deterministic explanation beside the text, Save / Approve with an explicit confirmation / Duplicate / Delete; a failed request keeps the typed text and shows the typed error); Version detail (immutable badge, YAML as approved, explanation, lineage root-first, Edit-as-new-draft and Duplicate); Assets (catalog search with the named-versus-total count and the populated-names note, catalog dates with the coverage note, multi-select, saved lists create/edit/delete); Studies (list, wizard with approved versions, tickers or a saved list, range and windows, capital, quantity policy, costs/objective/constraint entered by the user and never pre-filled, study detail with revisions, the two readiness verdicts, run, polled Job progress linking to the generic Job pages, results from `comparison.json` with return and risk in separate groups and the benchmark beside each candidate, evidence grades and limitations, grouping by ranking/asset/version, per-run equity and drawdown charts, the freeze form, the final-test action, the outcome block with the separate benchmark comparison and reproducibility, the global exposures with states and the outside-visibility note, export, and the static report links). The Research navigation entry appears only against a research-mode API. Pins: `consoleRouteInventory.test.ts` (nine trading pages exact, nine research pages exact), `consoleMutationInventory.test.ts` (five trading mutations exact, seventeen research mutations exact, no mutation helper outside `lib/api.ts`), `tests/test_console_api_contract.py` resolves console endpoints against both API modes and recognises the research helper. Component tests (`components/research/research.test.tsx`): validation findings, every inputs state, every verdict and every final-test outcome from fixtures, the preserved text on a failed save, the trading-mode gate, the wizard mapping.

**Not in S4.** The assistant panel (S5), the visual rule builder (M2), the live-data smoke run (S6). Known gaps: the wizard offers each family's latest version only (older versions can be studied through the API); the study detail reloads the whole results document on every refresh; the catalog refresh is submitted through the generic New Job page.


## 14. S4 completion, browser validation and the S6 smoke run (2026-10-08; committed in ca199b6)

**S4 corrections (A, B).** The wizard now lists every approved version grouped by family (latest first, older ones marked `older`), each identified by name, version number, approval date, source, history and the first twelve characters of `spec_sha256`; several versions of one family can be selected and are compared as separate candidates (an amber note names the family and the count); the immutable study snapshot keeps every selected id in selection order, and the backend refuses a repeated id (`invalid_study_settings`, field `strategy_version_ids`) instead of silently doubling the pairs. The ranking table and candidate cards identify a version by name and number with the id prefix beside it. `return_first` is the default selection with the risk-cap value, both cost fields and the study name empty and marked required; no numerical product default was introduced (test `wizard defaults and version selection`). "New revision (new settings)" now works: `/research/studies/new?from=<study>` pre-fills the latest revision's settings and submits `POST /studies/{id}/revisions`; the wizard also offers the `smoke` study kind, and a smoke study carries a banner stating that its settings are test configuration and its outcome is not a research claim.

**Defects found by the browser run and fixed.**
1. Kill-switch banner on every research page: a research-mode API has no `/api/v1/system/kill-switch`, so the trading banner showed a permanent "state UNKNOWN (404)". `KillSwitchBannerForMode` renders the banner only against a trading or not-yet-known API (tests for both modes).
2. Missing exchange sessions: without `market_sessions` rows every bar is `date_not_session` and the freeze fails for a reason unrelated to the data. Readiness now lists `market_sessions_not_synced` (exchange, range, count and first/last missing session) as a preflight error and the console copy names the `sync-market-sessions` Job to submit; checked only when every pair's warm-up start is known and inside the pinned calendar.
3. A final-test submission on inputs that changed after the freeze raised in the Job, not at the API; and the route caught only `StudyError`, so the refusal surfaced as a 500. `run_final_test` now refuses with 409 `inputs_changed_after_freeze` before anything is queued, and every study route translates that error.
4. Catalog sync failed on the real public list: a ticker repeated across exchanges (and `BRK.B` beside `BRK-B` after normalisation) hit `ON CONFLICT DO UPDATE ... cannot affect row a second time`. The sync keeps one row per ticker (latest `catalog_end`, then earliest `catalog_start`) and reports `duplicate_tickers`. SEC `company_tickers.json` answered 403 to the sync's user agent; it is a logged warning and Nasdaq Trader named 12,664 of 23,200 rows.
5. `adjustment_ratio_inconsistent` on consistent real rows: prices are stored at six decimals, so per-field ratios differ from the close ratio by up to ~4e-9 at a $300–$400 price (SPY and MSFT: 1,149 findings, maximum absolute spread 9.9e-7, under one stored unit). A session is now inconsistent only when the spread exceeds both the relative tolerance (1e-9) and twice the stored unit (2e-6); a genuine 0.05 drift on one field is still flagged (fixture added).
6. Final-test action offered again while its Jobs were queued; the panel now shows "Final test running" from the revision's `final_test`-scope Jobs. The co-leading marker (★, "co-leading") appeared on a sole leader because the backend flags every candidate on the leading key; the console shows it only when more than one candidate shares that key. Wide tables scroll inside their panel and the root navigation wraps, so no research page overflows at 375 px.

**Browser validation (isolated stack).** Console `next dev` on :3010 → research API on :8010 (`TRADING_PLATFORM_RESEARCH__MODE=true`, mutations enabled, calendar pinned 2014-01-02, `research.tiingo.api_key=stub`, `research.tiingo.base_url=http://127.0.0.1:8765`) and the research worker, on the throwaway database `research_s4_browser` at `0032`; Tiingo stubbed by a local HTTP server serving a deterministic sawtooth fixture (AAA: 12 sessions up 0.4 %/day then 8 down 0.5 %/day; BBB phase-shifted; BAD = AAA with one `high < low` bar on 2017-05-15). The stub was confirmed from the API process environment, the fake server's request log (7 requests, `Authorization: Token stub`, never a `token` query parameter) and the ledger (6 admitted requests). Flows exercised by Playwright/Chromium against the real pages, all passing (`flow_log.json`, 50 checks, screenshots 01–32): invalid YAML (`stop_or_target_price_not_supported`, `unknown_field` with path), valid YAML with the rendered explanation, draft creation, a refused save (121-character title) keeping the typed text and showing the typed error, save, approve with confirmation, v2 of the same family by "edit as new draft" with lineage; asset search by name, list saved, edited and restored; wizard defaults, older + latest version selected, saved list snapshotted; readiness `market_sessions_not_synced` → `sync-market-sessions` submitted from the generic New Job page → preflight ready with inputs pending; initial run (15 Jobs, no `final_test` scope), inputs verified, verdict, ranking by name/number, charts; expected signals: closed trades per (version, asset, window) equal the fixture's independently computed trades (AAA 19/12, BBB 18/13 for both versions) and the first exposure lands on the session after the first expected entry decision; final test refused before freezing (409), freeze refused with incomplete acceptance and a second freeze refused, freeze through the form, final test run and evaluated (`frozen_criteria_met`, thin evidence, benchmark comparison separate, exposure flipped to `results_inspected`, graph exactly candidate + benchmark + evaluate), export (17 files) with `comparison.json` matching the displayed verdict and outcome, the static report; freeze failure on BAD (`ohlc_relation` on 2017-05-15, inputs Failed, 5 downstream Jobs cancelled, no results, final test refused); stale inputs after a bar change (inputs Stale, old results kept and not approving, rerun refused with the typed message); new revision from the study page; 375 px layouts without horizontal overflow; keyboard: checkboxes toggled with Space, fields in reading order, actions reached by Tab, group-by switched with Enter. Evidence (screenshots, logs, fixture, scripts) is kept under `.data/research/validation-2026-10-08/s4-synthetic/` (git-ignored).

**S6 smoke run (real Tiingo, throwaway database `trading_research_smoke_20261008` at `0032`, dropped afterwards).** Test configuration, not research objectives or product defaults: assets SPY and MSFT (catalog: NYSE ETF from 1993-01-29, NASDAQ stock from 1986-03-13); strategy `Trend following 50/200 (example)` v1 (`5ee2a5dd…`, history 200); range 2022-06-01 → 2025-09-30; development 2023-01-03 → 2024-06-28, validation 2024-07-01 → 2025-03-31, final test 2025-04-01 → 2025-09-30; required start 2022-03-17 per pair (warm-up); capital 100000, fractional quantities, slippage 5 bps per side, commission 0, `return_first`, max-drawdown cap 0.5, freeze acceptance constraint 0.5 and objective minimum −1.0. Pipeline: catalog sync (23,200 rows, 12,664 named), sessions synced 2014-01-02 → 2025-12-31 (3,018), study created and run from the console (revision 1 froze-failed on defect 5 and stays on record; revision 2 created from the study page after the fix), ingestion of 2022-03-17 → 2025-09-30 (888 sessions × 2 symbols × raw + adjusted), integrity passed, freeze `4a3b0113` with inputs export under `.data/research/freezes/4a3b0113…/inputs`, 11 initial Jobs succeeded, final test 3 Jobs succeeded, export and report written under `.data/research/studies/692c14ab…/`. Tiingo usage: 8 requests through the shared ledger (2 × metadata + 2 × prices per revision), all `ok` 200, key only in the server-side header (zero occurrences in the API and worker logs, no `token` query). Observed results (an integration outcome, not a strategy claim): verdict `leading_candidate_identified` under the smoke criteria; SPY validation net −4.38 %, max drawdown −12.37 %, 8 closed trades, grade thin (buy-and-hold +3.80 %); MSFT validation net −18.80 %, max drawdown −21.97 %, 10 closed trades, thin (buy-and-hold −16.73 %); final test on SPY: `insufficient_evidence` (no closed trades, one position open at the end, net +8.15 %, max drawdown −2.41 %; buy-and-hold +20.65 %, shown separately). Displayed and exported results agree (`comparison.json` verdict, outcome and validation returns). Evidence under `.data/research/validation-2026-10-08/s6-smoke/`.

**Opening the research console (runbook).**
```bash
# 1. research database (never the trading one); migrates to the single head (0032)
PYTHONPATH=src .venv/bin/python scripts/create_research_db.py --database trading_research
# 2. research API (port 8000) and worker, both in research mode on that database
export TRADING_PLATFORM_RESEARCH__MODE=true TRADING_PLATFORM_RESEARCH__CALENDAR_START=2014-01-02 \
       TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=true TRADING_PLATFORM_DATABASE__NAME=trading_research
PYTHONPATH=src .venv/bin/uvicorn trading_platform.api.app:app --host 127.0.0.1 --port 8000
PYTHONPATH=src .venv/bin/python -m trading_platform.worker run-jobs
# 3. console (proxies /backend to the API; open it as http://localhost:3000, not 127.0.0.1)
cd console && npm run dev -- --port 3000
```
`TIINGO_API_KEY` is read from `.env` by the server-side client only. First-time steps inside the product: submit `catalog-sync` and `sync-market-sessions` Jobs (New Job page or `POST /api/v1/jobs` with an `Idempotency-Key`), seed the example versions (`seed_example_versions`) or approve a draft under Research → Strategies, then Research → Studies → New study.

**Tests.** Console: vitest 422 passed, `tsc --noEmit` clean, eslint clean, production build (see the report). Python: research modules green after every fix; the full suite is recorded in `final8_pytest.txt`. Remaining limitations: SEC names unavailable to the sync's user agent (names come from Nasdaq Trader and Tiingo metadata on view); revision ingestion re-downloads the whole range (idempotent, charged to the ledger); the assistant (S5) is not implemented.

## 15. The research platform is the product: landing, startup and bootstrap (2026-10-08, uncommitted)

**Decision (user, 2026-10-08).** The research platform is the primary local application; the trading screens are no longer part of the product UI. The trading backend stays frozen in the repository (no execution service, migration or broker change).

**Root cause of the old console at `localhost:3000`.** `make dev` (the canonical entry point) started the API and the worker in trading mode against the `.env` database: its recipe set only `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=true`, so `GET /health` reported `"mode": "trading"`, the console's `ResearchNavLink` (rendered only against a research-mode API) stayed hidden, the root layout showed the operator navigation (System Status, Strategy, Runs, Jobs, Paper Trading, Controls) with the kill-switch banner, and `/` was the system-status page. The research stack only existed as the hand-exported environment in §14's runbook. Separately, `trading_research` had been migrated to `0032` but was empty (no example versions, catalog or sessions), so even the research pages would have shown nothing to work with.

**Startup.** `make dev` is now the research product: `RESEARCH_ENV` (`TRADING_PLATFORM_RESEARCH__MODE=true`, `TRADING_PLATFORM_RESEARCH__CALENDAR_START=2014-01-02`, `TRADING_PLATFORM_DATABASE__NAME=trading_research`; overridable `RESEARCH_DB`, `RESEARCH_CALENDAR_START`) prefixes the API and the worker, mutations stay enabled for the `make dev` API process only, the console needs no flag (it follows `/health`). The database name is forced in the recipe so no `.env` edit can point a research process at `trading_platform`. `make api` and `make worker` are research too. `make dev-trading` keeps the frozen trading recipe byte for byte (trading mode, `.env` database). `make research-bootstrap` runs `scripts/bootstrap_research.py`: create + migrate (via `create_research_db`, refuses the trading DB), then `services/research/bootstrap.py` `bootstrap_research_database` seeds the four example versions (`seed_example_versions`, keyed by `(strategy_id, spec_sha256)`), syncs the public asset catalog only when empty or on `--refresh-catalog`, and upserts XNYS sessions from the pinned start through the calendar's last available session (`get_calendar().last_session`, about one year ahead; the library refuses later dates) when not already covered. Refusals are typed (`ResearchBootstrapError`: `research_mode_off`, `calendar_start_not_pinned`). Pinned by `tests/test_dev_workflow.py` (recipes), `tests/test_orchestration_boundaries.py` (script set, thin wrapper, make targets) and `tests/test_research_bootstrap.py` (idempotence: second run keeps every row and downloads nothing; range extension adds only missing sessions).

**Console.** Root layout: product name "Strategy Research", primary navigation Strategies · Assets · Studies · Jobs (`src/lib/navigation.ts`); no trading entries and no kill-switch banner. `/` redirects to `/research/studies`; legacy URLs redirect permanently (`src/lib/legacyRedirects.ts`, applied by `next.config.ts`): `/strategy → /research/strategies`, `/runs`, `/runs/:runId → /research/studies`, `/paper`, `/controls → /research`. The trading page shells (`/`, `/strategy`, `/runs`, `/runs/[runId]`, `/paper`, `/controls`) and their two page tests were removed; the trading components under `components/{status,strategy,runs,paper,controls}` and `KillSwitchBanner` stay with their tests (frozen). `/jobs/*` stays as the generic infrastructure surface. `ResearchGate` now reports an unreachable or failing API (status, `TRADING_CONSOLE_API_BASE_URL`, `make dev`, Retry) instead of leaving every panel loading, and a trading-mode API names the start command; `useResearchMode` exposes the failed `/health`. `allowedDevOrigins: ["localhost", "127.0.0.1"]` fixes the page that never hydrated when opened as `127.0.0.1` (the Next dev server refuses its HMR/RSC assets to an origin it does not know). Route inventory test updated deliberately (root redirect + three Job pages + nine research pages; the five trading pages pinned as removed); new `consoleRedirects.test.ts` pins the redirect table, the navigation and the layout's absence of trading chrome.

**Persistent environment (`trading_research`, verified).** Alembic `0032_research_studies`; 4 example versions (v1 of each family, approved 2026-10-08); catalog 23,208 rows, 12,669 named (Nasdaq Trader; SEC 403 as before); XNYS sessions 2014-01-02 → 2027-10-08 (3,462). Second bootstrap run: everything `kept`. `trading_platform` untouched at `0021`. No prices downloaded, no Tiingo API request, no study run.

**Validation against the normal stack (not a throwaway).** Processes: `make dev` (new session, log `.data/research/logs/make-dev.log`), API pid env and worker pid env both carry the research variables, `pg_stat_activity` shows their connections on `trading_research` only, `/health` → `research`, `/api/v1/job-types` lists exactly the six research types, `/api/v1/system/kill-switch` → 404. Playwright/Chromium against `http://localhost:3000` and `http://127.0.0.1:3000` (`.data/research/validation-2026-10-08/s7-product/`, `product_flows.py`, 68/68 checks, screenshots 01–08 per host): root lands on Studies with the research navigation and title and none of the trading strings; client-side navigation works on both hosts (hydration); the four example versions are listed and a version detail opens; asset coverage and name search (`MSFT` → "Microsoft Corporation - Common Stock"); the wizard offers every example version with `return_first` selected; New Job lists the six research types and no trading type; the five legacy URLs land on the mapped research pages; reload keeps the page. Of 130 proxied requests none targeted kill-switch, system, controls or any trading route (`backend_requests.json`). The prior session's `make dev` (trading mode, this repository) was stopped; the unrelated `next start` on :3111 (another project) and PostgreSQL were left alone.

**Runbook.**
```bash
make research-bootstrap   # once, and after pulling migrations (idempotent; keeps every record)
make dev                  # research API :8000 + worker + console :3000
# open http://localhost:3000  (127.0.0.1 works too)
make dev-trading          # only if the frozen trading console is needed (trading mode, .env database)
```
