# Strategy Research Platform: Proposal (revision 6)

**Status:** revision 6 (corrections of 2026-10-07 applied; product direction approved). Implementation planning lives in `01-IMPLEMENTATION-PLAN.md` and the specification contract in `02-STRATEGY-SPEC-V1.md`. Planning documents only. No implementation, no main-database upgrade, no Alpaca contact, no Phase 20.1 or Phase 21 work, no purchases, no commit, no push.
**Date:** 2026-10-07
**Supersedes:** revision 5 (2026-10-07) with three corrections: example-strategy parity (Part D), quantity rounding and price-scale invariance (Part J.3), exposure tracking (Part I.6). Planning defaults adopted: fractional quantities, the US stock and ETF catalog in USD, a configurable limit of 10 assets, synchronous HTTP authoring writes, Sonnet 5.5 as the provisional configurable AI model with AI disabled until credentials and spending limits exist, and the M2 order Pine export → visual builder → portfolio mode. Revision 4 remains the predecessor for the data and evaluation parts. The product decisions of 2026-10-07 replace the earlier "fixed four strategies, report-only, no UI" scope. Kept from revision 4: the isolated research environment, preserved inputs and provenance, the Tiingo facts and adapter plan, the integrity checks, the adjustment basis, the pinned metric definitions, the evidence descriptors and grades, the benchmark disclosure, the final-test discipline, the smoke run, and the static report as an export.

---

## Part A. Checkpoint and frozen work

| Item | Value |
| --- | --- |
| Branch / HEAD | `main` @ `f4ab1a5`; working tree clean except this proposal (untracked); no stash |
| Remote | 531 commits ahead of `origin/main`; not pushed |
| Tag | `checkpoint/pre-research-pivot-2026-10-06` → `f4ab1a5` (local only) |
| Main DB | `trading_platform` at alembic `0021`; code head is `0030`; not upgraded |
| Broker | Alpaca not contacted |
| Providers | Tiingo: the revision-4 read-only probe (26 requests, key in header only). This revision downloaded Tiingo's public `supported_tickers.zip` once (no key) to size the catalog, and read three official documentation pages |

**Frozen.** Phase 20.1 stays incomplete at `gaps_found` (round 5) with E2, E3, E4, E-5, D-1, OD-1, the CR-01 prohibition lift and human UAT open and unchanged. The existing trading-operation screens, Phase 21's trading-oriented read models, and all execution-side code stay as they are. Research UI is in scope; trading UI is not touched.

---

## Part B. Product decisions adopted and where they land

| Decision (2026-10-07) | Where |
| --- | --- |
| 1. Research product; trading frozen; research UI in scope | Parts A, C, L |
| 2. Study creation through the UI with readiness, progress, results; nothing silently omitted | Parts C, H, L |
| 3. Searchable asset catalog, saved lists, configurable per-study limit, download only selected assets, coverage checked | Part G |
| 4. Per-asset results central; single-asset tests vs portfolio simulation kept distinct | Parts H.2, I |
| 5. User-created strategies; bounded daily long-only specification; unsupported behaviour reported | Part D |
| 6. YAML editor and AI assistant on one specification; visual builder recommendation | Part E |
| 7. Drafts vs approved immutable versions; results reference exact versions and inputs | Part F |
| 8. Python as a later advanced mode, requirements recorded | Part E.4 |
| 9. Per-study objective and constraints; frozen criteria; cross-study exposure; separate benchmark display | Part I |
| 10. Tiingo facts, preserved inputs, isolated environment, quantity policy, smoke run | Parts G, J, M |
| 11. Results in the product; static report and JSON as exports | Parts L, N |
| 12. Bounded AI integration; deferred items | Parts E.2, K, P |

Conflicting statements from earlier revisions that no longer apply: "four fixed strategies with YAML parameters recorded verbatim" (now: any approved strategy version); "ten-symbol universe" (now: catalog selection); "CLI, Job and JSON only, no console" (now: research UI); "candidate = strategy on the whole universe" (now: candidate = strategy version on one asset); "constraint confirmed by the user before the study" (now: entered per study in the UI, frozen before the final test).

---

## Part C. First usable workflow

1. **Create a strategy.** Write YAML in the editor, or describe the idea to the assistant and inspect its draft. Validation errors and unsupported requests appear inline. The plain-language explanation is rendered from the specification itself. Approve to save an immutable version.
2. **Choose assets and study settings.** Search the catalog by ticker or name, pick assets or a saved list (up to the configured limit), choose strategy versions, dates, the three evaluation windows, capital, allocation and quantity policy, trading costs, the ranking objective and the risk constraint.
3. **Validate.** The readiness screen lists every problem before anything runs: unsupported specification features, assets without coverage for the requested range, warm-up that cannot be satisfied, integrity failures after download, limit breaches, missing settings. The study is `ready` or `not_ready` with reasons; nothing is dropped, shortened or substituted.
4. **Run.** Downloads only the selected assets for the requested range plus warm-up, freezes the inputs, runs every (strategy version × asset × window) as Jobs, and shows progress through the existing Job surfaces.
5. **Compare.** Per-asset results, equity and drawdown charts, evidence descriptors, costs, ranking with reasons, benchmark comparison, limitations, and the final-test block. Export the static report and JSON.

---

## Part D. Strategy specification v1 (canonical, declarative, bounded)

One specification serves the YAML editor, the AI assistant and the later visual builder. One interpreter executes it. No per-authoring-method engines.

**Scope.** Daily bars. Long-only. One position per asset, no pyramiding, no shorting, no leverage, no intraday, no stops at intrabar prices (a stop expressed on close is a rule like any other). Evaluated on the close of session T; the engine fills at the open of T+1 as today.

**Shape (YAML).**
```yaml
spec_version: 1
name: Trend following 50/200
description: Long when the trend is up on both averages, exit below the fast average.
timeframe: daily
direction: long_only
indicators:
  sma_fast:  {type: sma, source: close, window: 50}
  sma_slow:  {type: sma, source: close, window: 200}
entry:
  all_of:
    - {left: close, op: gt, right: sma_slow}
    - {left: sma_fast, op: gt, right: sma_slow}
exit:
  any_of:
    - {left: close, op: lt, right: sma_fast}
```

**Supported in v1.**
- Price series: `open`, `high`, `low`, `close`, `volume`.
- Indicators (each a named instance with typed parameters): `sma`, `ema`, `rsi`, `highest` (of a source over a window), `lowest`, `lag` (the source N sessions ago), `change_pct` (percent change over N sessions). Every indicator and series accepts `shift: N` (N ≥ 0 sessions back, default 0), which expresses "the preceding channel" in Donchian rules.
- Operators: `gt`, `ge`, `lt`, `le`, `crosses_above`, `crosses_below`; the right side is a series, an indicator, or a constant.
- Combinators: `all_of` (AND) and `any_of` (OR), nesting depth ≤ 3, at most 16 conditions in total.
- Parameter bounds: windows 1 to 500; RSI thresholds 0 to 100; shifts 0 to 20.
- Warm-up is derived (the maximum window plus shift plus 1 for cross operators), never declared by hand.

**Semantics pinned in `02-STRATEGY-SPEC-V1.md` and by tests.** Evaluation is stateless, like the four original strategies: on each close the `exit` rule is checked first and yields `EXIT`; otherwise `entry` yields `LONG`; otherwise `FLAT`. An `EXIT` while flat is a no-op for the engine, exactly as today. Fewer loaded bars than the specification's `history_required` yields `FLAT` with `rule_insufficient_history`. All indicators use the adjusted series chosen by the study; comparisons use `Decimal`. (Revision 5 said entry is evaluated only when flat; that would have changed the signal ledgers of the originals and is withdrawn.)

**Unsupported requests are reported, never approximated.** Closed error codes include `unknown_field`, `unsupported_indicator`, `unsupported_operator`, `unsupported_source`, `direction_not_supported` (short, long-short), `timeframe_not_supported` (intraday, weekly), `nesting_too_deep`, `too_many_conditions`, `parameter_out_of_bounds`, `undefined_reference`, `self_reference`, `stop_or_target_price_not_supported` (intrabar stops, trailing stops, take-profit prices), `position_sizing_in_strategy_not_supported` (sizing is a study setting), `multi_asset_condition_not_supported` (a rule referencing another asset), `lookahead_reference_not_supported` (negative shift).

**Reconciliation with the four originals (corrected).** Each original loads exactly `warmup_periods` bars (the last N sessions on or before the evaluation date) and computes its indicators on that truncated list; fewer bars than `warmup_periods` yields `FLAT`. Derived warm-up alone does not reproduce this. The specification therefore distinguishes two numbers per indicator: the **mathematical minimum** (bars without which the value is undefined) and the **history fed** to the computation. For `sma`, `highest`, `lowest` and `lag` the two coincide, so trend following (200), Donchian (55 + shift 1 = 56) and time-series momentum (252 + 1 = 253) load the same bars as their originals. For `rsi` (and the new `ema`) the value depends on how many bars feed the recursion: the original RSI is Wilder's smoothing seeded by the simple average of the first 14 changes and iterated over a 100-bar window, so the specification's `rsi` carries an explicit `history: 100` (minimum 15) and `history_required` is the maximum over indicators (100). The specification's RSI formula, zero-move conventions (both averages zero → 50, no losses → 100, no gains → 0), the Donchian "preceding bars" shift, session-indexed lag, and the exit-before-entry precedence are pinned to the originals in `02-STRATEGY-SPEC-V1.md`. **Exact equivalence is a claim to be earned by the parity tests, not asserted:** they run each original and its specification on shared fixtures across the early-history boundary (fewer than, exactly, and more than `history_required` bars; a missing bar inside the window) and compare signal dates and directions for every session. The four are seeded as example versions only after those tests pass. No intentional behaviour change is planned; if one is found necessary it is recorded on the seeded version as `behaviour_differs_from_original` with the reason.

**Interpreter.** One `DeclarativeDailyStrategy` built on the existing `BaseStrategy`, reading bars through the existing access layer and emitting the existing `SignalBatch`. The closed `SignalReason` enum gains three generic members (`rule_entry`, `rule_exit`, `rule_no_signal`); the existing members stay for the Python examples. The research registry resolves strategies by version id; the static trading registry is unchanged.

**Explanation.** A deterministic renderer turns a specification into numbered plain-language entry and exit rules, the derived warm-up, and a list of what the specification does not do. It is the explanation the user approves; the assistant's prose is shown separately and labelled as the assistant's.

---

## Part E. Authoring

### E.1 YAML editor
A text editor with schema validation on every change, error codes with line references, the rendered explanation beside it, and "Save as draft" / "Approve version". Duplicate copies any version into a new draft.

### E.2 AI drafting and revision assistant
- Flow: the user writes an idea (or opens an existing draft and writes a correction request) → the service sends the specification schema, the supported-feature list and the user text to the model → the model returns a candidate specification plus an `unsupported_requests` list and a short note → the same validator runs → the UI shows the YAML, the deterministic explanation, the validation result, the model's note and the unsupported list → the user edits, asks for another revision, or approves.
- The assistant never approves, never runs a study, never sees study results, has no tools, and cannot reach the broker or the main database. One bounded automatic retry when the output fails validation; after that the errors are shown and the user decides.
- Provenance is stored for every draft: provider, model id, prompt template version, request id, input and output token counts, the user's text, the returned specification hash, validation outcome, timestamp. Shown on the version after approval as "drafted by assistant, revised N times, approved by user".
- Failures are visible with closed codes: `ai_disabled`, `ai_not_configured`, `ai_daily_limit_reached`, `ai_provider_error`, `ai_timeout`, `ai_output_invalid`, `ai_refused`.
- Integration: Part K.

### E.3 Visual rule builder: after M1
Recommendation: build it in M2 on the same specification. Reasons: the editor plus the assistant plus the deterministic explanation already give two authoring paths and a readable check; a builder is UI work with no new semantics; shipping it later costs nothing in architecture because the specification is the only contract.

### E.4 Python: a later advanced mode
Deferred from M1. Requirements recorded for later: a defined strategy interface (bars in, signals out, deterministic, no side effects); execution isolated from the application process with CPU, memory, time and no-network limits; access to research inputs only; no application secrets, no main-database connection, no filesystem outside a scratch directory; generated Python is never executed in the application process.

---

## Part F. Drafts, approval, immutable versions

- `strategy_drafts` (mutable): id, title, YAML text, source (`manual` | `assistant` | `duplicate_of_version`), `ai_draft_id`, updated time.
- `strategy_versions` (immutable, append-only): id, `strategy_id` (a stable family id), `version_no`, canonical YAML, canonical JSON, `spec_sha256`, derived warm-up, explanation text, `parent_version_id`, `source`, `ai_draft_id`, `approved_at`. A database trigger rejects every UPDATE and DELETE; a schema test pins it.
- Approval is an explicit user action that inserts a version from a validated draft. Saving a version does not start a study and cannot enable trading (research mode registers no trading job types; Part M).
- Editing an approved version opens a new draft with `parent_version_id`; approval creates the next version. Earlier versions and their results stay.
- Every run row references `strategy_version_id`, `asset`, `window_role`, `study_revision_id`, `spec_sha256`, `input_digest`, `code_sha`.

---

## Part G. Asset catalog, saved lists, coverage

**Catalog source.** Tiingo's public `supported_tickers.zip` (no key; updated daily). Observed 2026-10-07: columns `ticker, exchange, assetType, priceCurrency, startDate, endDate`; 108,954 rows; asset types `Mutual Fund` 49,880, `Stock` 49,291, `ETF` 9,783; US Stock and ETF rows on NYSE, NASDAQ, NYSE ARCA, BATS and AMEX in USD: 24,537, of which 14,110 have an `endDate` in October 2026. The file carries no names.

**Names (corrected).** Fetching names only on view cannot support discovery by company name, and a full Tiingo metadata download is not affordable on the free plan (about 14,000 current assets at 50 requests per hour). Names therefore come from two public, keyless directories joined on ticker at catalog sync, with `name_source` recorded per row: the Nasdaq Trader symbol directory (`nasdaqtraded.txt`, all exchange-listed securities with `Security Name`; observed 2026-10-07: 12,579 of the 14,067 current catalog rows named, including 5,554 of 5,852 ETFs) and the SEC EDGAR `company_tickers.json` (SEC registrants; observed 7,156 of 14,067, mostly stocks). Tiingo's metadata endpoint fills remaining gaps when an asset is viewed or selected, counted against the request budget. Tiingo's search endpoint is marked beta and is not relied on. **Name search is labelled as covering populated names only**, and the search screen shows the count of named versus total catalog rows; coverage grows with each catalog sync and each viewed asset. Class-share tickers differ between sources (Nasdaq `BRK.B`, Tiingo `BRK-B`); the join normalises `.` to `-`.

**Catalog table** `asset_catalog`: provider, ticker, exchange, asset type, currency, `catalog_start`, `catalog_end`, name (nullable), `names_fetched_at`, `catalog_synced_at`. Search by ticker prefix and by name substring over the local table. Default filter: US Stock and ETF in USD on the major exchanges; OTC and mutual funds excluded (Q3).

**Saved lists** `asset_lists` / `asset_list_items`: named, reusable, editable; a study snapshots the list contents at creation.

**Limit** `research.max_assets_per_study`, default 10, configurable; a breach is a readiness error, not a truncation. Asset count is independent of `max_concurrent_positions`, which applies only in portfolio mode (Part H.2).

**Coverage is checked, not assumed.** Catalog presence and `startDate` only say what Tiingo claims. Readiness computes, per (strategy version, asset): required start = window start minus the derived warm-up in sessions; `catalog_start ≤ required start` and `catalog_end ≥ requested end`; after download, the integrity checks (Part J) run on the actual rows for the actual dates. Delisted assets are listable (the catalog carries an `endDate`) but a window past `endDate` is `not_ready`.

**Download only selected assets.** The `ingest_tiingo_bars` Job takes the study's assets and `[required start, end]`. Rate limits (50 requests per hour on the free plan) are respected by the adapter; a 429 fails the Job visibly.

---

## Part H. Studies

### H.1 Study settings (entered in the UI; stored on an immutable study revision)
Strategy versions (approved only) · assets or a saved list · overall date range and the three windows (`development`, `validation`, `final_test`) · `initial_capital` · `quantity_policy` (Part J.3) · trading costs: slippage bps per side and commission per order, **entered by the user with no pre-filled figures**; the engine's defaults of 5 bps and $0 appear only as labelled examples · ranking objective and constraint (Part I.3) · evaluation mode (Part H.2) · optional note.

A `study_revisions` row is immutable. Changing any setting creates a new revision (or a new study); results attach to the revision that produced them.

### H.2 Evaluation modes
| Mode | What runs | In M1 |
| --- | --- | --- |
| `single_asset_independent` | For each (strategy version, asset): the engine with a one-asset universe, one position slot, the study capital; per-asset buy-and-hold benchmark of the same asset with the same costs and dates | **Yes** (the ranking basis) |
| `portfolio_combined` | The existing multi-asset engine with `max_concurrent_positions` and equal-weight slots; an equal-weight buy-and-hold benchmark of the same assets | **M2** (the engine supports it; the UI mode, ranking semantics and evidence rules for portfolios are a separate slice) |

The study spec carries `mode`; in M1 only `single_asset_independent` is accepted and `portfolio_combined` is rejected with `mode_not_supported_yet`. Results screens label single-asset results as "independent tests, one asset each; not a portfolio" and show no aggregate that could read as portfolio performance. An across-asset summary is allowed only as counts and medians over independent tests, labelled as such. This matches the first external target: one TradingView chart runs one strategy on one asset.

### H.3 Readiness (before any run)
Closed codes, each listing the affected item: `spec_unsupported_feature`, `strategy_version_not_approved`, `asset_limit_exceeded`, `asset_not_in_catalog`, `coverage_start_too_late`, `coverage_end_too_early`, `warmup_not_satisfiable`, `windows_overlap_or_unordered`, `window_outside_range`, `costs_missing`, `objective_missing`, `constraint_missing`, `mode_not_supported_yet`, `calendar_start_not_pinned`, `integrity_error` (after download), `inputs_changed_after_freeze`. Readiness is `ready` only with zero errors.

### H.4 Run and progress (initial evaluation only)
The initial study Job graph runs the **development and validation windows only**: `ingest_tiingo_bars` → integrity and `data_freeze` → one `research_backtest` per (version, asset, window ∈ {development, validation}) plus the per-asset benchmarks for those two windows → `research_evaluate` (metrics, evidence, ranking, verdict). No `final_test` run is created, scheduled or exposed by this graph, and the results API returns no final-test data for a revision without a freeze. **Final-test execution is a separate action** (Part I.5): available only after the candidate and its acceptance criteria are frozen, it runs exactly the frozen candidate and its benchmark on the final-test window. The existing generic Job list, detail, progress, logs and cancel surfaces show every step; the study page shows an aggregated progress view linking to them.

---

## Part I. Evaluation

### I.1 Candidates and benchmark
A candidate is one (strategy version, asset) pair. Its benchmark is buy-and-hold of the same asset: buy at the open of the window's second session (the "buy" decision on the first close, filled next open like every candidate), hold, marked at the last close, same capital, same costs on the entry, same quantity policy. The comparability table in the report is generated from the actual run settings.

### I.2 Metrics (pinned; unchanged from revision 4)
Per window, net of the stated costs, on the adjusted total-return basis.

| Metric | Definition | Undefined or edge case |
| --- | --- | --- |
| `net_total_return` | (final equity − initial capital) / initial capital; open position marked at the last close | — |
| `excess_return_vs_benchmark` | candidate − benchmark `net_total_return`; always shown; never a selection gate or ranking key | benchmark missing → `null`, candidate `not_evaluable` |
| `cagr` | (final / initial)^(365.25 / calendar days) − 1 | window under 365 days → flagged `annualised_from_short_window` |
| `max_drawdown` | minimum over sessions of equity / running peak − 1; signed, ≤ 0; closer to zero means less drawdown | never below a peak → `0.0` |
| `drawdown_duration` | longest run of sessions below the prior peak | none → 0 |
| `closed_trades` | trades with an exit fill inside the window | — |
| `open_at_end` | the position still open at the last session, with unrealized P&L; excluded from every closed-trade metric | — |
| `holding_period` | sessions from entry fill to exit fill | no closed trades → `null` |
| `win_rate` | winning closed trades / closed trades | 0 closed → `null` |
| `profit_factor` | gross profit / gross loss over closed trades; report only | no losing trades → `null` ("no losing trades"); 0 closed → `null` |
| `expectancy` | mean net P&L per closed trade | 0 closed → `null` |
| `sharpe_daily_ann` | mean / sample standard deviation (n − 1) of daily returns × √252; risk-free 0 | zero deviation or fewer than 2 returns → `null` |
| `sortino_daily_ann` | mean / √(mean over all days of min(r, 0)²) × √252 | no negative day → `null` |
| `exposure` | mean gross exposure / equity | — |
| `turnover` | (entry + exit notional) / mean equity; not annualised | — |
| `total_costs` | slippage plus commission, currency and % of initial capital | — |
| `rounding_slack` | the actual residual `affordable_notional − quantity × fill_price` per fill, summed per window, under either quantity policy (Part J.3) | no fills → 0 |
| `skipped_fills` | fills skipped for a missing bar | > 0 → window flagged `data_gap_affected` |

### I.3 Evidence descriptors and grades (research rules; unchanged from revision 4)
Descriptors per candidate per window: closed trades, open at end, measured sessions, median and mean holding period, exposure, concentration (best trade, best three trades, best calendar month as shares of gross profit), event clusters (anchored: a cluster opens at an entry fill and absorbs entries within the next 5 sessions), total costs. In single-asset mode "best symbol share" is omitted and clusters are per asset.

Grade from `holding_ratio` = measured sessions ÷ median holding period and `clusters`: `insufficient` (0 closed trades, or clusters < 5, or ratio < 5); `thin` (clusters < 10, ratio < 10, or best-three share > 50 %); otherwise `meets_predefined_study_conditions`. Only `insufficient` excludes from ranking. Printed with every grade: the thresholds and the cluster span are heuristics fixed before results; clusters do not establish independent observations; no grade certifies statistical reliability; the median holding period is measured in the same window it grades.

### I.4 Objective and constraint (per-study UI choices, stored and displayed)
| Objective | Constraint (validation window) | Ranking key (validation window) | Tie-breaks |
| --- | --- | --- | --- |
| `return_first` (**default selection in the form**) | `max_drawdown ≥ −cap`, cap entered by the user as a magnitude (no approved default value) | `net_total_return` descending | `max_drawdown` descending (closer to zero first), then `co_leading` |
| `risk_first` | `cagr ≥ floor`, floor entered by the user (may be negative, zero or positive) | `max_drawdown` descending (closer to zero first) | `net_total_return` descending, then `co_leading` |

Candidate status in order: `not_evaluable` → `insufficient_evidence` → `not_eligible` (constraint fails on validation) → `eligible`. Development-window values are shown beside validation values and are not gates. Benchmark excess return is always visible and never a gate or key. Ranking is across all (version, asset) candidates of the study revision; the result page also groups by asset and by strategy version. No composite score; profit factor never affects the order.

Study verdict: ≥ 1 eligible → `leading_candidate_identified` ("leading candidate under these criteria"); none eligible and any `insufficient_evidence` or `not_evaluable` → `insufficient_evidence`; none eligible, all `not_eligible` → `no_candidate_qualifies`. Changing the objective or constraint creates a new study revision; earlier verdicts stay on record.

### I.5 Freeze and final test
1. **Freeze record** per study revision: the chosen candidate (a `co_leading` tie needs the user's recorded choice and reason), its version and `spec_sha256`, assets, windows, capital, quantity policy, costs, objective, constraint, the user's **final-test acceptance values** (constraint value for the test window, and a minimum for the objective dimension: net return for return-first, drawdown for risk-first), `ranking_criteria_hash`, `code_sha`, `input_digest`, pinned calendar start, timestamp. Criteria are frozen here; a later change means a new revision.
2. **One final-test run**, triggered by its own action after the freeze, of the frozen candidate and its benchmark only; it is never part of the initial study graph. DB unique constraint on (`study_revision_id`, candidate, `final_test`, `rerun_of IS NULL`); refused without a freeze record and for any other candidate.
3. **Outcome, evaluated in order:** `insufficient_evidence` (test-window grade `insufficient`, `data_gap_affected`, or not evaluable); `frozen_criteria_met` (constraint value and objective-dimension minimum both hold on the test window; labelled `thin_evidence` if the grade is thin); `frozen_criteria_not_met` (otherwise).
4. **Displayed separately, never part of the outcome:** the benchmark comparison on the test window (return better or worse, drawdown better or worse, excess return). Beating buy-and-hold is not required for `frozen_criteria_met`.
5. **Wording.** The outcome means "met or did not meet the user's pre-registered conditions on one held-out window". The page says it does not certify statistical reliability or future profitability.
6. Nothing is re-ranked; no other candidate runs on `final_test` in that revision.

### I.6 Exposure across related studies
`test_window_exposures` records every final-test run or rerun with: asset, the test date range, `input_digest`, study and revision, strategy family and version, reason, and `results_inspected_at`. Before any freeze and on every final-test block, the UI shows **every recorded exposure for the same asset whose date range overlaps the proposed test window, across all studies, revisions, strategy families and versions**, including siblings, duplicates and unrelated families, each with its context (study, revision, version, outcome) and a link. Two states are distinguished: `run_recorded` (the final test ran; results exist but no one opened them) and `results_inspected` (the results view or export was opened; the application records the first time). A new study id, a duplicated strategy or a new family never resets or hides this. The report prints the count by state. Limitation stated in the UI and the report: the application records only what happened inside it; research done in other tools, exports read elsewhere, or knowledge from outside the application cannot be detected. Technical reruns must be byte-identical; any difference is recorded as `reproducibility_failure`.

---

## Part J. Data, reproducibility, quantities

### J.1 Tiingo facts in force (observed 2026-10-07)
Free plan: 30+ years of history, 50 requests per hour, 1,000 per day, 500 unique symbols per month, personal use only, no display or sharing with another person. Fields: raw OHLCV, adjusted OHLCV (CRSP method, splits and dividends), `divCash`, `splitFactor`. Header authentication `Authorization: Token`. Dates are UTC midnight carrying the session date. Observed coverage for the ten currently configured symbols ranges from 1980 (AAPL) to 2012 (META); samples showed zero integrity findings; adjusted volume is raw volume times later split factors.

**Licence consequences.** Downloaded bars, preserved inputs, reports and anything derived from Tiingo data live under the git-ignored `.data/research/` and the research database only; never committed, never published as an Artifact, never shared. Committed fixtures are synthetic in Tiingo's response shape.

### J.2 Adjustment basis, integrity, preserved inputs (unchanged from revision 4)
- Signals, fills, marks and benchmarks use the CRSP-adjusted series (total-return basis). Raw rows and factors are stored alongside. Prices and quantities are in adjusted units, not historically traded prices; every later dividend or split rescales earlier adjusted prices, so re-fetches differ and reproducibility rests on preserved inputs.
- Integrity checks before `data_freeze`, errors blocking the freeze: `date_invalid` (first 10 characters, no timezone conversion), `date_not_session`, `ordering`, `duplicate_key`, `price_nonfinite`, `price_nonpositive`, `ohlc_relation` (raw and adjusted), `volume_invalid`, `missing_session`, `pair_missing`, `adjustment_ratio_inconsistent`, `source_mixed`, `factor_missing`; warnings `extreme_move_adjusted`, `extreme_move_raw_unexplained`, `zero_volume_session`.
- Preserved per study revision: frozen research DB (`data_freeze`; later changes refuse Jobs with `inputs_changed_after_freeze`); `inputs/` export with raw and adjusted rows, factors, sessions, symbols, `INTEGRITY.json`, `MANIFEST.json` with SHA-256 per file and `input_digest`; `study_spec.json`, the strategy specifications by hash, `settings_snapshot.json`, `code_sha` with dirty flag, `requirements_freeze.txt`, Python version; a restore script.
- Reproducible (bit-identical): the same spec at the same `code_sha` on the frozen or restored DB. Detectable only: input changes after the freeze; provider restatements. Not reproducible: the original fetch; environment drift.

### J.3 Research quantity policy (new)
The problem: with whole-share rounding, the invested amount per fill is `floor(notional / price) × price`, so it depends on the price scale, and adjusted prices change scale with every later split or dividend. Two assets, or one asset fetched on two dates, would be invested to different degrees for the same notional.

Policy:
- `quantity_policy ∈ {fractional, whole_shares}`, a study setting.
- `fractional` (the research default): quantity = `(affordable_notional / fill_price)` quantized to 6 decimal places with `ROUND_DOWN`, where `affordable_notional = min(slot_notional, cash − commission)` as today. Rounding down guarantees `quantity × fill_price ≤ affordable_notional`, so a fill can never overspend. The residual `affordable_notional − quantity × fill_price` lies in `[0, fill_price × 1e-6)` and is **reported as the actual `rounding_slack`** per fill and summed per window; it is small, not zero.
- `whole_shares`: today's engine rule (`ROUND_DOWN` to a whole share); `rounding_slack` per fill and per window is reported.
- Under either policy, a fill whose computed quantity rounds to zero (whole shares: price above the affordable notional; fractional: affordable notional below `fill_price × 1e-6`) is recorded as a `zero_quantity_fill` finding, not a silent skip.
- Price-scale invariance is claimed only for **scale-free specifications** under stated conditions. The validator assigns every series and indicator a unit (`price`: open, high, low, close, sma, ema, highest, lowest, lag of a price series; `dimensionless`: rsi, change_pct; `volume`) and marks a specification `price_scale_dependent` when a constant is compared with a `price`-unit term (for example `close > 100`) or when two different units are compared (which is also a validation error). The four examples are scale-free. A `price_scale_dependent` version is allowed, but its reports carry the note that its rules contain absolute price levels and therefore depend on the adjusted price scale at fetch time. Tests scale a fixture by k = 0.01 and k = 100 for scale-free specifications only and assert: trade dates and directions identical (exact); returns, drawdowns and costs in percent equal within a tolerance derived from the policy, `Σ over entry fills of fill_price × 1e-6 / initial_capital` plus floating-point epsilon, under `fractional`; and under `whole_shares` equal within the reported `rounding_slack`. Per-share commissions, minimum lots or price-dependent slippage would break the claim and are not offered. Nothing is claimed for arbitrary authored strategies.
- TradingView note for later: `fractional` corresponds to `default_qty_type = percent_of_equity`; `whole_shares` to a fixed quantity.
- The trading path keeps whole shares; the policy applies in research mode only.

---

## Part K. AI integration (small, explicit, bounded)

- Provider and SDK: the official Anthropic Python SDK (`anthropic`), the project's language. Provisional model `claude-sonnet-5-5`, configurable, never hard-coded; the assistant stays disabled until an API key and explicit per-day and per-request limits are configured. No numerical budget is approved.
- Configuration under `research.ai`: `enabled` (default false), `provider`, `model`, `api_key` (from `ANTHROPIC_API_KEY`, never logged; the log sanitizer already redacts `api_key` and `authorization`), `timeout_seconds`, `max_requests_per_day`, `max_output_tokens`, `max_input_characters`, `max_revisions_per_draft`. Limits are enforced in the service and counted in the database; the Anthropic console's own spend limit is a separate control the user sets.
- Request shape: one `messages.create` per draft or revision, with adaptive thinking left at its default, a structured output format (`output_config.format`) that is the specification JSON schema plus `unsupported_requests` and `note`, no tools, no forced tool choice (rejected on current models), no prefill. The system prompt (schema, supported features, semantics, examples) is stable and cached with `cache_control`. A `refusal` stop reason surfaces as `ai_refused`.
- Validation runs on the returned specification exactly as on hand-written YAML. Schema validity is not semantic correctness; the UI says so and requires explicit approval.
- Provenance: `ai_drafts` rows (Part E.2). Request and response bodies are stored without the key.
- Out of scope: autonomous tuning against results, running studies, broker access, web access, multi-agent loops.

---

## Part L. Research UI and API

**Console** (existing Next.js app; reuse the shell, navigation, `lib/api.ts` fetch boundary, Job list/detail/progress/log components, `EquityCurveChart`, `SummaryMetricsPanel`, table and filter primitives, the console boundary tests). New top-level section `Research` with pages:
- `/research/strategies`: list, versions, duplicate; `/research/strategies/new` and `/research/strategies/drafts/[id]`: YAML editor, assistant panel, explanation, validation, approve.
- `/research/assets`: catalog search, saved lists.
- `/research/studies`: list; `/research/studies/new`: wizard; `/research/studies/[id]`: revisions, readiness, progress, results (per-asset tables, charts, evidence, ranking, benchmark, limitations, final test, exposures, exports).
- Trading pages are untouched. The research section is visible only when the API runs in research mode.

**API** under `/api/v1/research/`: strategies (drafts CRUD, validate, explain, approve, versions), assistant (draft, revise), catalog (search, asset detail, sync), asset lists, studies (create, revisions, readiness, run, results, comparison, freeze, final test, exposures, exports), AI usage. Reads return `as_of`. Mutations are typed and refuse with closed codes.

**Mutation paths (amendment for research mode).** Long-running work stays on the Job path (download, backtests, evaluation). Research authoring writes (drafts, versions, asset lists, study definitions, freezes) are synchronous HTTP writes in research mode only, on the research database, audited by row history and the immutability triggers. No research route exists when research mode is off; the trading allowlist is unchanged. **Mount exclusivity (S2, 2026-10-07):** a research process mounts the research routers plus the infrastructure routers only (`/health`, `/ready`, the generic `/api/v1/jobs*` surface over the research-only registry, `/api/v1/job-types`); every trading router (strategies, analytics, runs, operations, system, controls, recovery, execution operations, market data) is absent, so a trading mutation on a research process is a route miss (404) that reaches no service and no broker client. The lifespan refuses to start when `research.mode` differs from the mode the routers were mounted for. Research writes carry the same `require_mutations_enabled` guard as trading mutations; `POST /api/v1/research/strategies/validate` is the one research POST that writes nothing.

---

## Part M. Research environment (updated)

Isolated `trading_research` database on the local Postgres server, migrated `0001`–`0030` fresh, then `0031_research_platform`:
- new tables: `research_studies`, `study_revisions`, `strategy_drafts`, `strategy_versions` (append-only trigger), `ai_drafts`, `asset_catalog`, `asset_lists`, `asset_list_items`, `data_freezes`, `research_freezes`, `test_window_exposures`;
- `research_run_links`, a 1:1 linkage table keyed by run id (`study_revision_id`, `strategy_version_id`, `asset`, `window_role`, `spec_sha256`, `code_sha`, `input_digest`, `rerun_of`); the shared `strategy_runs` table and model stay untouched;
- `daily_bars.volume` widened to `BIGINT`; nullable `split_factor`, `dividend_cash`.
- Phase 21's frozen plan also names `0031`; it renumbers if it ever resumes. Phase 21 files are not edited now.

Research settings (defaults keep the trading path unchanged): bar provider and adjusted flag passed explicitly by the engine and strategies; pinned research calendar start; `max_assets_per_study`; quantity policy; the Tiingo key alias for `TIINGO_API_KEY`; `research.ai.*`.

With `TRADING_PLATFORM_RESEARCH_MODE=1` the worker registers exactly `ingest_tiingo_bars`, `sync_market_sessions`, `research_backtest`, `research_evaluate`, `catalog_sync`. The main database stays at `0021`.

---

## Part N. Results in the product; exports

Per study revision: verdict and label; the stated cost model and quantity policy; data source, coverage, integrity, freeze; windows; per-asset comparison tables (Return and Risk, candidates and benchmark); equity and drawdown charts per asset and window; evidence descriptors and grades with limitations; ranking with objective, constraint, status and reasons; final-test block (not frozen / frozen / outcome / benchmark comparison / exposures); limitations (adjusted prices, hindsight asset selection, single validation window, heuristics); reproducibility manifest. Exports: the static `report.html` and `report.md` (stdlib renderer, inline SVG), `comparison.json`, per-run `summary.json`, `trades.csv`, `equity_curve.csv` through the existing exporter. Exports are written under `.data/research/` only.

---

## Part O. TradingView handoff (described; implementation in M2)

Per-asset results map to one chart each. Signal equivalence (per-bar chart export vs `backtest_signals`) stays distinct from trade/fill equivalence (Strategy Tester list vs `backtest_trades`). The adjustment basis must match on both sides: a split-only series can be derived from stored raw prices and factors; which TradingView settings are available is to be verified in M2. Assumptions to pin: feed, session dates, `calc_on_every_tick=false`, `process_orders_on_close=false`, commission and slippage mapping, `pyramiding=0`, quantity policy mapping, warm-up via `bar_index`, bar-history limits, rounding tolerance. Pine generation from the specification, converters and comparators are M2 work; the specification's bounded indicator and operator set is chosen so each element has a direct Pine equivalent.

---

## Part P. Delivery: waves, slices, acceptance

### Waves (dependency order; each slice a reviewable unit)
| Wave | Slices |
| --- | --- |
| W0 Foundation | R1 research DB, `0031`, research settings, research-mode registry · R2 provider and calendar plumbing with trading-path invariance tests · R3 Tiingo adapter, `ingest_tiingo_bars`, catalog sync, names cache · R4 integrity checks, `data_freeze`, inputs export, restore |
| W1 Specification | R5 schema, validator, error codes, canonicaliser, explanation renderer · R6 `DeclarativeDailyStrategy`, parity with the four Python examples, seeded examples · R7 drafts, versions, approval, immutability, strategies API |
| W2 Study engine | R8 study and revision model, readiness, `research_backtest` and `research_evaluate` Jobs, single-asset mode, quantity policy, per-asset benchmark · R9 metrics, evidence, objectives, ranking, verdicts · R10 freeze, final test, cross-study exposures |
| W3 Research UI | R11 strategies pages with YAML editor, explanation, versions · R12 assets search and saved lists · R13 study wizard, readiness, progress · R14 results pages and exports (report renderer) |
| W4 Assistant | R15 AI service, configuration, limits, provenance · R16 assistant UI (draft, revise, approve) |
| W5 Gate | R17 integration smoke run on the Tiingo path (YAML-authored or example version, two assets, about three years including warm-up, throwaway DB); depends on W0–W3 only, never on W4; then the first substantive study. R18 assistant integration check: runs only when the assistant is configured and enabled, separately from the research smoke run |

Size: about three times revision 4's M1. The product is usable after W3; W4 completes the first usable product as decided; the W5 research smoke run gates substantive research and does not depend on the assistant, which stays disabled until credentials and explicit limits exist.

### Acceptance criteria (testable)
1. **Isolation and scope pins.** `alembic_version` is `0021…` on the main DB and `0031…` on the research DB; a path-diff test shows no change under `services/execution/`, `services/reconciliation/`, `services/alpaca.py`, `services/recovery.py`, `alembic/versions/0022…0030`, or the console's trading pages.
2. **Research-mode registry** contains exactly the five research job types; no trading job type can be submitted in research mode. (S0/S1 register the three that exist, `catalog-sync`, `ingest-tiingo-bars`, `sync-market-sessions`, pinned as a closed set; `research-backtest` and `research-evaluate` join the pin in S3.)
3. **Trading-path invariance.** With default settings every bar read on the risk and paper paths carries `provider="polygon"`, `adjusted=True`, whole-share quantities, and the library-default calendar; the recorded evaluation-manifest parameters are byte-identical before and after W0; the existing suite passes.
4. **Tiingo adapter.** Mock transport: header auth only, no `token` query parameter; 429 → `provider_rate_limited`; session date from the first 10 characters; raw and adjusted rows with factors per session; adjusted volume above 2,147,483,647 inserts.
5. **Catalog.** Sync parses the six columns; the default filter yields only US Stock and ETF in USD on the major exchanges; search by ticker prefix and name substring; a name is fetched at most once per asset and cached; a study with more than `max_assets_per_study` assets is `not_ready` with `asset_limit_exceeded` and no asset is dropped.
6. **Coverage.** A (version, asset) whose warm-up precedes `catalog_start` is `not_ready` with `warmup_not_satisfiable`; a window past `catalog_end` is `coverage_end_too_early`; readiness lists every failing item.
7. **Specification validator.** A fixture per error code; nesting depth 4 and 17 conditions are rejected; negative shift is `lookahead_reference_not_supported`; derived warm-up equals max window + shift + 1 for cross operators.
8. **Parity (earned, not assumed).** For each of the four originals, the specification and the Python strategy produce identical signal dates and directions on shared fixtures that cover fewer than, exactly, and more than `history_required` bars, a missing bar inside the window, and threshold-adjacent indicator values; RSI parity is tested at `history: 100` and shown to fail at a different history so the dependence is real; seeding of the examples happens only after these pass; a change to either side fails the test; any accepted difference is recorded as `behaviour_differs_from_original`.
9. **Semantics.** Stateless evaluation as the originals (Part D): on every close the exit rule is checked first and wins when both rules are true; entry is evaluated regardless of position and the engine ignores a redundant signal; fewer bars than `history_required` yields `FLAT` with `rule_insufficient_history`; pinned by fixtures. (Revision 6 text that said "entry only when flat, exit only when in position" was a leftover of the withdrawn revision-5 wording.)
10. **Explanation** is deterministic (same spec → same text) and changes when the spec changes (hash pin).
11. **Immutability.** UPDATE and DELETE on `strategy_versions` are refused by trigger; approval inserts exactly one version; editing an approved version creates a draft with `parent_version_id`; runs reference `strategy_version_id` and `spec_sha256`.
12. **No silent substitution.** A study whose readiness has any error cannot run; the run endpoint returns the same codes; a test asserts no code path removes assets, trims dates or replaces settings.
13. **Study revisions.** Changing objective, constraint, costs, windows, assets or versions creates a new revision; prior results remain queryable; the run row's `study_revision_id` matches the settings used.
14. **Single-asset mode.** Each (version, asset, window) run has a one-asset universe and one slot; its benchmark run uses the same asset, dates, capital, costs and quantity policy; `portfolio_combined` is rejected with `mode_not_supported_yet`; results views carry the "independent tests, not a portfolio" label (rendered-output test).
15. **Quantity policy.** Under `fractional`, every fill satisfies `quantity × fill_price ≤ affordable_notional` and the reported `rounding_slack` equals the actual residual; scaling a scale-free fixture by k = 0.01 and k = 100 leaves trade dates and directions identical and metrics equal within the policy-derived tolerance; under `whole_shares` the difference is within the reported slack; a `price_scale_dependent` specification is excluded from the invariance test and its report carries the note; a quantity that rounds to zero under the selected policy yields `zero_quantity_fill`.
16. **Metrics and evidence.** Golden tests for every I.2 edge case; anchored cluster tests (+4, +5, +6 sessions; an entry every 3 sessions over 30 sessions gives 5 clusters); grade boundaries at 4/5, 9/10 and 50 %; the limitation text is present.
17. **Objectives.** Fixtures prove both sort orders, tie-breaks, `co_leading`, the signed drawdown ordering, and that a constraint breached only in development leaves a candidate eligible; profit factor never changes the order; the form's default selection is `return_first` with an empty constraint value that must be entered.
18. **Freeze and final test.** A regression proves the initial study graph creates no `final_test` run for any candidate or benchmark and that the results and comparison reads expose no final-test data before a freeze; freeze refused while a `co_leading` tie is unresolved; final test refused without a freeze, for other candidates, and for a second non-rerun run; fixtures for `insufficient_evidence` (checked first), `frozen_criteria_met`, `frozen_criteria_met` with `thin_evidence`, and `frozen_criteria_not_met` under both objectives; the benchmark comparison is present and independent of the outcome.
19. **Exposure.** Final-test runs on the same asset with overlapping dates appear in the exposure view of a new study regardless of strategy family, version lineage, duplication or study id, each with context and link; opening the results view or an export flips the state to `results_inspected` once; the outside-the-application limitation text is present; a rerun that differs logs `reproducibility_failure`.
20. **Assistant.** Mock provider: output is validated by the same validator; invalid output triggers one retry then `ai_output_invalid`; `unsupported_requests` is shown; a draft cannot be approved without the explicit action; provenance rows carry provider, model, prompt version, token counts and spec hash; the daily limit yields `ai_daily_limit_reached`; a refusal yields `ai_refused`; no key appears in any stored body or log (sanitizer test).
21. **Licensing.** Report writer, inputs exporter and downloads write only under `.data/research/`; a test asserts the path is git-ignored; fixtures under `tests/` are synthetic.
22. **UI boundaries.** Console boundary tests extend to the research pages (`lib/api.ts` is the only fetch site; no domain logic in components); results components render from `comparison.json` fixtures for every verdict and outcome.
23. **Smoke run** completes end to end on the Tiingo path in a throwaway database with the smoke banner, with the assistant disabled, and is dropped afterwards.
24. **Assistant integration check** (only when `research.ai.enabled` with a key and limits configured): one real draft request succeeds, its provenance row is written, and the research smoke run's result is unchanged by whether this check ran.

---

## Part Q. Deferred (recorded)

Portfolio mode UI, ranking and evidence semantics (M2) · visual rule builder (M2) · Pine generation, converters, parity comparators (M2) · Python strategy mode with the Part E.4 requirements · automatic parameter search, grids, walk-forward, cost sweeps · whole-market scanning · composite scoring and return ÷ drawdown · survivorship-free or point-in-time universes · exact dividend-factor reconciliation · CSV import · Polygon plan change · Alpaca market data · Phase 21 · v1.4 console · Phase 20.1 open items.

---

## Part R. Decisions: adopted defaults and what remains

Adopted on 2026-10-07 as planning defaults: Anthropic SDK with `claude-sonnet-5-5` as the provisional, configurable model, AI disabled until credentials and explicit spending limits are configured (no numerical budget approved); `fractional` quantities with the corrected rounding rules; the US stock and ETF catalog in USD on the major exchanges; a configurable limit of 10 assets per study with no silent truncation; synchronous HTTP for authoring and configuration writes with ingestion and backtests as Jobs; M2 order Pine export and equivalence checking → visual builder → portfolio mode.

Genuinely open items are listed, localized to the stage they affect, in `01-IMPLEMENTATION-PLAN.md` Part 7. Objective values, constraint caps and floors, costs, windows and assets are per-study inputs and block nothing.

---

*Checkpoint tag:* `checkpoint/pre-research-pivot-2026-10-06` (local). *Nothing pushed. No main-DB upgrade. No broker contact. No purchases. Tiingo contacted read-only in revision 4 only; this revision used public documentation and the public ticker list.*
