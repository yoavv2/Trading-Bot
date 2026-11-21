# Signals product: work plan

Written 2026-11-19. This is the single entry point for continuing the work in a
new session. It records what was decided, what the repository contains now, what
is still open, and the order of the build.

## 1. Start here (new session)

1. Read this file, then `docs/STRATEGY-SPEC-V1.md` and
   `docs/STRATEGY-SPEC-V2.md` (the contracts the validator and interpreter
   implement).
2. Check section 4 (blockers) and section 5 (open decisions) with the owner
   before writing code that depends on them.
3. **Step 1** (section 6) is built. The first study chose no pilot candidate;
   the second, wider study confirmed time-series momentum on its holdout. The
   owner approved the publication rule and the forward log's rules on
   2026-11-20 ("Forward log" in the Step 1 status). Read the Step 1 status
   before starting Step 2.

Working rules the owner set:

- No GSD workflow, no `.planning/` tree, no `CLAUDE.md` in this repository.
- Do not rebuild the old console. The new UI is undecided.
- Requirements are written as testable invariants: closed enums, typed errors,
  database constraints. No "direction" wording.
- Commits go on a branch; the owner merges through a GitHub pull request.

## 2. What we are building and why

A signals product in Hebrew for swing trading U.S. stocks and ETFs, with a
community built through Shorts and Reels.

The reference is SBX Capital (`app.sbxpremium.com`): 7–12 signals a month, each
published in advance with entry, stop and target; push and WhatsApp updates at
every stage; a public performance page listing every closed trade, losers
included, measured in R; free tier with 1–2 signals a month and a paid tier at
180 ₪ a month. The user places the trades at their own broker. SBX signals are
chosen by analysts.

Our difference: **every signal comes from an approved, rule-based strategy
version, and the backtest evidence for that version is shown next to its live
record.** SBX has no backtesting, walk-forward or out-of-sample evidence. That
is also the content angle for the videos ("I tested this strategy over ten
years, here is what came out"), and the research tool already produces the
charts and numbers for it.

We are not copying SBX's analyst desk, academy or events. Those are operations,
not software.

## 3. Repository state on 2026-11-19

`main` is the only branch, in sync with `origin` (HEAD `5939187`).

**Kept and working (the research backend):**

- Strategy specification v1: `src/trading_platform/strategies/spec/`
  (`schema.py`, `validate.py`, `evaluate.py`, `explain.py`, `errors.py`).
  Daily bars, long-only, one asset per run, closed error codes.
- The one interpreter: `src/trading_platform/strategies/declarative.py`
  (`DeclarativeDailyStrategy.generate_signals(db_session, as_of)` already
  returns a `SignalBatch` for a date; evaluation is stateless).
- Research services: `src/trading_platform/services/research/` (catalog, Tiingo
  ingestion, integrity, freeze, studies, backtest, metrics, evidence, ranking,
  report, assistant).
- Research API: `src/trading_platform/api/research/`, mounted only in research
  mode.
- Job framework: `src/trading_platform/jobs/`. The research registry holds six
  Job types: `catalog-sync`, `ingest-tiingo-bars`, `sync-market-sessions`,
  `research-freeze`, `research-backtest`, `research-evaluate`.
- Research database `trading_research`, migrations through `0034`. The Step 2
  branch adds `0035` to `0037`; the local database is migrated when that
  branch is merged.
- Four approved example versions (trend following 50/200, RSI mean reversion,
  Donchian breakout, time-series momentum), and a v2 version of each with the
  base risk block (`config/research/examples_v2/`), seeded by the study
  script.

**Removed on 2026-11-19 (recoverable from git history):**

- `console/` (Next.js UI). The last state, including unfinished redesign work,
  is commit `37e5e80`.
- `.planning/` (GSD roadmap, phases, milestones). Only the specification
  contract was kept, as `docs/STRATEGY-SPEC-V1.md`.
- The Vercel project `trading-platform-console` and the local `.vercel/` link.

**Frozen, not part of the product:** the trading backend (paper execution,
reconciliation, broker code, migrations `0022`–`0030`). The signals product
places no orders and contacts no broker, so none of it is needed.

**Running it:** `make research-bootstrap` once, then `make dev` (research API
on `127.0.0.1:8000` plus the Job worker). There is no UI.

**Tests:** `PYTHONPATH=src .venv/bin/pytest -q`. Seven old failures remain on
`main` and are unrelated to the signals work: the `universe_size` assertion in
`tests/test_app_boot.py`, one test in `tests/test_api_reads.py`, three in
`tests/test_dev_workflow.py` and two in
`tests/test_orchestration_boundaries.py`. Most still expect the removed
console (`npm run dev`, Makefile console targets).

## 4. Blockers the owner must close

Neither blocks building Steps 1–3. Both block showing signals or the
performance record to anyone outside: until the legal check and the commercial
data licence are both closed, the Step 3 screen and API stay internal.

| Blocker | Why it matters | Blocks |
|---|---|---|
| Legal check with a lawyer | Publishing trade signals for payment in Israel may count as investment advice or marketing, which requires a licence from the Israel Securities Authority. SBX states it holds none. | Making the Step 3 record public; Step 4 onward |
| Commercial market-data licence | The Tiingo key is a personal-use licence. It does not cover distributing signals derived from the data. | Making the Step 3 record public; Step 4 onward |

## 5. Open decisions

| Decision | Options discussed | Needed by |
|---|---|---|
| Distribution channel | WhatsApp channel, Telegram, own app with push | Step 4 |
| New UI | Not designed. Public performance page first, or member app first | Step 3 or 5 |
| Free versus paid split | SBX gives 1–2 free signals a month | Step 5 |
| Which strategy version publishes first, and on which assets | Decided 2026-11-20: time-series momentum v2 on the 42 assets of the wide study, published under a cap of 10 open signals, first in the internal forward log (Step 1 status, "Forward log") | Closed |
| Short signals, ATR and indicator stops | Deferred until a strategy under study needs them | After Step 3 |
| Hosting | Local only today | Step 3 |

## 6. Build order

Each step lists what must be true when it is done. These are the acceptance
conditions; write them as tests.

### Step 1: one long strategy, from specification to an internal signal log

Agreed 2026-11-19. This is a narrow slice through the specification, the
backtest engine and the daily engine, for one long strategy. It comes before
any UI or payment work. Steps 2 and 3 then widen it.

The v1 contract rejects stop and target prices
(`stop_or_target_price_not_supported`). A signal needs entry, stop and target.

**Specification v2 (long only, minimal):**

- `spec_version: 2` adds a required `risk` block with three fields:
  `stop` (percent below the reference price), `target` (multiple of the
  planned risk) and `min_reward_risk` (see entry rule 3).
- `direction` stays `long`. Short, ATR stops and indicator stops are not in
  this step; requesting them returns a closed error code.
- A v1 document stays valid and evaluates exactly as before. The four example
  versions keep passing the existing parity tests.
- Every new rejection has a code in the closed error list. The plain-language
  explanation renders the stop, target and minimum reward-to-risk.

**Entry rules:**

1. A signal is produced after the close of session D. Its reference price is
   that close. Stop and target are computed from the reference price, stored
   with the signal and never edited.
2. The simulated entry is the open of session D+1.
3. The signal expires without a trade if the D+1 open is at or beyond the stop,
   at or beyond the target, or leaves a reward-to-risk ratio below
   `min_reward_risk`. Example: reference 100, stop 95, target 110. An open at
   111 expires. An open at 102 gives reward 8 against risk 7, a ratio of 1.14;
   it opens only if `min_reward_risk` is 1.14 or lower.
4. R for the performance record is measured against the actual initial risk
   (entry minus stop), 7 per share in the example.

**Exit rules for an open trade, in this order within a session:**

1. If the session opens at or beyond the stop or the target, exit at the open.
2. If the strategy's exit rule was true at the previous close, exit at the
   open. A later touch of either level occurs after the position has closed.
3. Otherwise check the session's low and high against the stop and target. If
   only one is touched, exit at that level.
4. If both are touched, record a stop exit and set the flag
   `intrabar_order_unknown`. A daily bar cannot show which came first. The
   flag lets us report how much of the result depends on this assumption.

**One implementation for both engines:** the entry and exit rules above live
in one shared function used by the backtest engine and the daily engine, so
backtest evidence and the live record are comparable.

**First tests to write, shared by both engines:**

- the open is beyond the target before entry (signal expires, no trade);
- a gap through the stop on an open trade (exit at the open);
- one bar touches both levels (stop exit, flag set).

**Evidence:** adding a stop and a target changes a strategy's results; a
percent stop can cut a trend strategy's winners short. The v1 study results do
not carry over. Run a new study on the v2 versions and choose the first
publishing strategy from it.

**Done when:** one approved v2 long version produces daily signals into an
internal, append-only log, and each logged signal can be compared with the
backtest of the same version under the same rules.

**Status 2026-11-19: built; the study ran and chose no pilot candidate.**

Built (contract in `docs/STRATEGY-SPEC-V2.md`):

- Specification v2: `strategies/spec/schema.py`, `validate.py`, `explain.py`.
  The four v1 digests are pinned in `tests/test_strategy_spec_v2.py`.
- The shared rules: `strategies/trade_rules.py` (`plan_levels`,
  `resolve_entry`, `resolve_exit`, `r_multiple`, `net_r_multiple`).
- Backtest engine: `services/backtesting.py` calls the shared rules when the
  strategy declares a risk block. A trade stores its levels, the rule entry
  and exit prices (before costs), `exit_reason` and `intrabar_order_unknown`.
- Daily engine: `services/signals/engine.py` (`run_signal_session`,
  `run_signal_sessions`), writing `signal_log` and `signal_session_runs`.
  Both tables refuse UPDATE and DELETE (migration `0034_signals_step1`).
- Comparison: `services/signals/comparison.py`. For every signal it compares
  the levels, the final state (`expired`, `open`, `closed_stop`,
  `closed_target`, `closed_rule`), the entry and exit sessions and rule
  prices, the exit reason, the flag and gross R. Net R comes from the
  backtest's cost model and is reported separately (`net_r_from_backtest`).
- Metrics in R: `services/research/risk_metrics.py`, stored in each v2 run's
  `result_summary["research"]["risk_metrics"]`: signals and trades a month,
  expiries by reason, mean and total gross and net R, the R drawdown, the
  share of flagged trades and the result if the target had come first.
- Selection rules: `services/research/signal_selection.py`, with closed
  failure codes and verdicts.
- Study runner: `services/research/signal_study.py` and
  `scripts/run_signals_v2_study.py`, driven by the protocol
  `config/research/signals_v2_study.yaml`.
- Tests: `tests/test_trade_rules.py`, `tests/test_strategy_spec_v2.py`,
  `tests/test_signals_step1_engines.py`, `tests/test_signal_study.py`.

The study (`docs/studies/signals-v2-base-risk-2026-11-19/`: `REPORT.md` and
`results.json` with every version id, run id, digest, window and the cost
model):

- Setup, fixed in the protocol before any run: the four example rule sets
  with stop 5%, target 2R, minimum reward-to-risk 1.5; NVDA, PLTR, QQQ, SMCI
  and URA on adjusted Tiingo bars; development 2021-10-07 to 2024-10-04,
  validation 2024-10-07 to 2025-10-06, holdout 2025-10-07 to 2026-10-07;
  5 bps slippage on each fill and 1 USD per order.
- The daily engine and the backtest agreed on all 45 runs: 1,686 signals and
  1,139 closed trades compared, 0 differences.
- Three versions met every selection criterion on development and
  validation. Donchian breakout ranked first (mean net R 0.40 and 0.50) and
  was the only version run on the holdout. It failed there: 21 closed trades,
  mean net R −0.05, total −1.13 R. **No pilot candidate is chosen.**
- Trend following and time-series momentum also qualified. They were not run
  on the holdout; trying them there now would turn the holdout into a
  selection window. RSI mean reversion had too few trades (28 in three years).
- About 30% of entry signals expired because the next open left a
  reward-to-risk below 1.5. With these values that is any open more than 1%
  above the reference close.
- No trade had a bar that touched both levels, so the stop-first assumption
  changed nothing in this study.
- Limits: five assets and five years; three of the assets were picked after
  large rises, so the development and validation figures overstate what an
  asset list fixed in advance would give. Prices are adjusted prices.

The second study, run 2026-11-20
(`docs/studies/signals-v2-wide-2026-11-19/`, protocol
`config/research/signals_v2_wide_study.yaml`, clean revision `e4c1b3c`):

- Setup, fixed in the protocol before any run on these assets: the same four
  rule sets and base risk block; 42 assets (the 30 Dow members of 2015-01-02
  and SPY, IWM, DIA and nine sector funds); development 2015-01-02 to
  2022-10-06, validation 2022-10-07 to 2024-10-04, holdout 2024-10-07 to
  2026-10-07; the same costs. The profitable-asset criterion kept its ratio
  (26 of 42).
- The daily engine and the backtest agreed on all 378 runs: 11,117 signals
  and 9,543 closed trades compared, 0 differences.
- Time-series momentum and trend following qualified. Time-series momentum
  ranked first (mean net R 0.10 in development, 0.22 in validation) and was
  the only version run on the holdout. It was confirmed there: 858 closed
  trades, mean net R 0.22, total 192.3 R, 31 of 42 assets positive.
  **Pilot candidate: `time_series_momentum_daily` v2.**
- RSI mean reversion and Donchian breakout failed two development criteria
  each: total net R below the largest R drawdown, and too few profitable
  assets.
- Limits: the holdout's calendar period was seen in the first study on five
  other assets, so it is new for these assets and not for the market. The
  edge is thin in development (0.10 R a trade). The pooled R drawdown on the
  holdout is 47.5 R and the worst single-asset equity drawdown is 57%. The
  study does not compare against holding the assets, and both later windows
  were rising markets. R is measured against the actual initial risk, so an
  entry that opens close to the stop gives very large multiples (best trade
  22.5 R, worst −11.4 R on the holdout). 27 holdout trades with an initial
  risk under 2% of the entry carry 41.7 of the 224.2 gross R; with every
  trade limited to between −1.5 R and 2.5 R the gross total is 134.2 R, still
  positive. The median trade loses 0.56 R. The rate is about 37 trades a month across the 42
  assets, several times what the product would publish. Prices are adjusted
  prices. One interrupted earlier attempt left an unused study row
  (`6c38f2c8-31e6-4963-92eb-91282ac779aa`) with 330 selection runs and no
  holdout exposure.

The publication-filter study, run 2026-11-20
(`docs/studies/signals-v2-publication-filters-2026-11-20/`, protocol
`config/research/signals_v2_publication_filters.yaml`, code
`services/research/publication_filters.py`, clean revision `b9c0320`):

- What it measures: the candidate's stored signals and trades from the wide
  study's development and validation windows, with one rule deciding which
  are published. Each rule is applied on its own. No backtest is run and the
  holdout is not read. A signal a rule holds back still runs internally and
  is never published later.
- Minimum initial risk (a signal is withdrawn at the open when
  (open − stop) / open is below 2%, 3% or 4%): keeps the edge at all three
  values and removes the very large multiples (best trade in development
  10.0 R unfiltered, 4.5 R at 3%). It withdraws 2% to 13% of the trades, so
  the rate stays at 23 to 30 trades a month. It does not solve the rate.
- Cap on open published signals (5, 8, 10 or 12 across all assets, same-day
  signals taken in a fixed hash order): the rate follows the cap, about one
  trade a month for each place. Cap 10 is the only rule that keeps the edge
  and meets the 7 to 12 a month target in both windows (9.0 and 10.4 trades a
  month, mean net R 0.09 and 0.24). Cap 8 meets the rate and fails the edge
  reading: its development total (47.5 R) is below its R drawdown (56.4 R).
  Cap 12 keeps the edge and is just above the rate in validation (12.1).
  Cap 5 keeps the edge at 4 to 5 trades a month.
- Limits: the cap results rest on one fixed order of same-day signals;
  another order publishes other trades, and the differences between caps 8,
  10 and 12 are within what that could move. The two rules were not combined.
  No rule has been tested on data that no study has read.

Still open in Step 1:

- The forward log below has to run; it starts once Step 2 can run it.
- The first study's run recorded a dirty Git revision. Its exact source
  snapshot cannot be verified from that identifier.
- The study HTML report (`services/research/report.py`) does not show the R
  metrics; the study script writes its own Markdown report.
- The AI assistant still writes v1 documents only. This can wait.

**Forward log: rules approved by the owner on 2026-11-20, before it starts.**

The owner chose the cap of 10 open signals on its own (no minimum initial
risk) and the internal daily log going forward as the test on unseen data.
Changing anything below after the log has started is a new test.

- Purpose: to check that the daily engine works in real time and that the
  rate meets the target. Profitability is reported as an observation. The log
  is not asked to decide whether the edge is real: at about 10 trades a month
  it cannot do that within a year.
- What runs: `time_series_momentum_daily` v2 on the 42 assets of the wide
  study.
- Publication: at most 10 published signals open at once across all assets,
  counted as the filter study counts them
  (`services/research/publication_filters.py`). When one session has more
  signals than free places, they are taken in ascending order of
  `sha256("<session date>:<asset>")`. A signal held back is never published
  later.
- Summary date: the first date on which at least six months have passed since
  the first session of the log and at least 60 published trades have closed.
- Operational success, all three:
  1. the mean over the whole period is 7 to 12 published trades a month,
     counted as in the studies (single months may fall outside);
  2. the cap held on every session;
  3. a replay over the same input data gives the same publication, entry and
     exit decisions as the daily engine recorded.
- Early stop: the log stops if the fall of the total net R of published
  trades from its previous peak exceeds 70 R. The largest such fall in the
  development window under this cap was 70.32 R; 70 is that figure rounded,
  chosen in advance.
- Profitability: mean net R, total net R and the largest R drawdown are
  reported. Apart from the early stop, the financial result changes neither
  the summary date nor the operational verdict.

Left to Step 2 on purpose: a Job type or command that runs the daily engine
(today it is a function), the `skipped` result for a missing bar (today a
typed refusal, `missing_bar`), the input data digest on each signal, and
transition order enforced by the database.

### Step 2: daily signal engine

Hardens the daily run that Step 1 introduced. Move the Step 1 research study
runner to a Job and retire its script and mutation-boundary exemption.

- A Job type `signals-daily` runs after the U.S. close for a configured set of
  (approved version, asset) pairs: ingest the latest bar, check integrity,
  evaluate, write the results.
- Signal lifecycle is a closed state machine:
  `issued → open → closed_target | closed_stop | closed_rule`, and
  `issued → expired` under entry rule 3 of Step 1. Illegal transitions are refused by
  the database.
- One signal per (version, asset, session date). A re-run of the same date
  writes nothing new (unique constraint, tested by running the Job twice).
- A session with missing or failed-integrity data produces a recorded
  `skipped` result with a reason code. It never produces a signal and is never
  silently ignored.
- Exchange holidays and half days come from the existing sessions calendar; no
  run happens on a non-session date.
- Each signal stores the version id, the specification digest and the input
  data digest it was computed from.
- The publication rule of the forward log (Step 1 status) is applied by the
  daily run and its decision is recorded for every signal with a closed
  outcome (`published`, `held_back_open_cap`). The decision is written once
  and a re-run or a replay over the same data reproduces it.

**Status 2026-11-20: the daily run is built; the forward log has not started.**

Built on branch `feat/signals-step2-daily-engine`:

- Migration `0035_signals_step2`. `signal_log` and `signal_session_runs`
  carry `input_data_digest`, the SHA-256 of the market-data reads of the
  session. A transition guard refuses events out of order, an event whose
  levels differ from the issued signal, and a new signal while another of the
  same (version, asset) is active. A session is recorded only after every
  session already recorded.
- Skips. A session whose bar is missing or whose data failed the integrity
  check is not processed. `signal_session_skips` records it once per reason
  (`missing_bar`, `integrity_failed` with the integrity code). The session
  stays the next one to process and blocks the sessions after it, so no stop
  check is lost; it is processed late when its data arrives.
- Migration `0036_signal_books`. A book is a fixed (version, assets, cap,
  first session), read from `config/signals/<name>.yaml` and recorded once
  with the file's SHA-256; a changed file is refused (`book_changed`).
  `signal_publications` holds one decision for each issued signal. The
  database computes the outcome and refuses any other: `held_back_late` when
  the decision is recorded at or after the next session's open, otherwise
  `held_back_open_cap` when `max_open` published signals are still open,
  otherwise `published`. `tests/test_signal_publication.py` shows the daily
  decisions equal the cap rule of the filter study on the same log.
- Job type `signals-daily` (`services/signals/daily.py`, payload `book`,
  `session_date`, `ingest`): download the history range with one Tiingo
  request an asset, check integrity, process every unprocessed session of
  each asset in its own transaction, decide publications. The range that is
  downloaded and checked starts from the history of the first session still
  to be processed, so a book resumes after an interruption of any length. A date that is not
  a session, has not closed or is before the book's first session is refused
  with a closed code. A re-run writes nothing new.
- Tests: `tests/test_signals_step2_engine.py`,
  `tests/test_signal_publication.py`, `tests/test_signals_daily_job.py`.

- Adjusted levels (owner's decision, 2026-11-21). The engine reads adjusted
  bars, and a dividend or a split after the signal session rescales the whole
  adjusted history. The levels a signal was issued with are never changed. On
  every later session the engine derives the stop and the target in that
  session's price scale from the issued levels and the factor `adjusted close
  of the signal session as read today / issued reference price`. Example:
  reference 100 read today as 99 gives factor 0.99, stop 95 becomes 94.05 and
  target 110 becomes 108.90. Deriving from the issued levels every time means
  an adjustment is never applied twice. Each event records
  `adjustment_factor`, `effective_stop_price` and `effective_target_price`
  (migration `0037_signal_inputs`); a user is shown the derived levels, and
  the issued ones stay in the history. R is measured in one price scale
  (`services/signals/comparison.py`).
- One decision function. `services/signals/session_rules.py` holds
  `decide_session`, a pure function of the session's inputs and the active
  signal. The daily engine and the replay both call it.
- Kept inputs. The daily Job stores the bars every processed session read in
  `signal_session_inputs` (append-only, with their SHA-256). A later download
  rewrites adjusted history, so this is the only place a past session's
  inputs can be read back.
- Replay (`services/signals/replay.py`, Job type `signals-replay`, read-only).
  It recomputes every event from the kept inputs and every publication
  decision from the recorded order and times, compares both with the record
  and lists each difference with a closed kind (`event_differs`,
  `publication_differs`, `inputs_digest_mismatch`, `cap_exceeded` and others).
- Tests of the owner's two conditions for starting the forward log
  (`tests/test_signals_adjustment_and_replay.py`): two dividends in a row and
  a 2-for-1 split on an open trade; a book run day by day with a dividend on
  an active signal replays with no difference, also after the adjusted
  history is rewritten again; a changed record is reported.
- The daily Job adds a third publication outcome to the two of the frozen
  rules: `held_back_late`.

- The forward log's book: `config/signals/forward-log-2026-11.yaml`, 42
  assets, cap 10, first session Monday 2026-11-23 (owner's decision,
  2026-11-21). If the merge, the migration or the first run is late, the
  first session moves to the next future session, in a new file under a new
  name, so the first record is a decision made in real time.
- Daily start: `launchd` on the owner's Mac (`deploy/launchd/`). The agent
  runs `run-signals-daily.sh` at 02:00 local time, Tuesday to Saturday: it
  starts the research API, submits `signals-daily` for the book, runs the
  worker once and logs the Job's result with UTC times. A machine asleep at
  02:00 runs it on waking. The Job result carries `started_at` and
  `decided_at`; a signal decided after the next open is `held_back_late`.
  The agent is installed after the merge and the local migration, not before.
- Trial on real Tiingo data, 2026-11-21, in a separate database
  (`trading_research_trial`, a copy of the research database migrated to
  `0037`), with a trial book identical to the forward log's except for its
  name and a first session of 2026-11-18. One run up to the closed session of
  2026-11-20: 42 requests, 25,284 bars written, no integrity error, no asset
  skipped, 126 sessions processed, 108 events, 54 publication decisions (10
  published, 1 held back by the cap, 43 late because the run caught up two
  sessions after their next open). Replay: 126 sessions, 108 events and 54
  decisions recomputed, 0 differences. A second run wrote nothing. The
  launchd script submitted and ran the forward log's Job against the trial
  database; it was refused with `before_first_session`, as it must be before
  2026-11-23. Not exercised by the trial: a dividend or split on an active
  signal in real data (every factor was 1), and a day-after-day sequence of
  real runs.
- Observation from the trial: a book starts with no open signals, so its
  first sessions issue signals for most assets at once (54 in three sessions)
  and the cap fills on the first day in the fixed hash order.

Not built yet:

1. The study runner as a Job, retiring `scripts/run_signals_v2_study.py` and
   its mutation-boundary exemption.

### Step 3: append-only performance record

Turns Step 1's internal log into the public record.

- Signals and their events (`issued`, `expired`, `entry_filled`,
  `rule_exit_signalled`, `closed_stop`, `closed_target`, `closed_rule`) are
  append-only. A database trigger refuses UPDATE and DELETE, the same pattern
  as approved strategy versions. There is no stop-moved event: in
  specification v2 the stop is fixed when the signal is issued.
- The record is published before the outcome is known: an `issued` event's
  timestamp precedes the session it applies to, and a test asserts it.
- A read-only API returns every closed signal, losers included, with entry,
  exit, R result and running totals. Open signals are excluded from totals.
- The same page shows, for each strategy version, its backtest evidence beside
  its live record. This is the product's main claim.
- This step needs a first screen, so the UI decision in section 5 comes due.
- The screen and the API stay internal (no public URL, no access for anyone
  outside) until both blockers in section 4 are closed: the legal check and
  the commercial data licence.

### Step 4: free distribution

Requires both blockers in section 4 to be closed.

- Each signal event is delivered once to the chosen channel. Delivery is
  recorded per event; a retry does not send a duplicate.
- A message carries: asset, direction, entry, stop, target, the reason in
  plain language (from the specification's explanation), and a link to the
  record.
- A fixed risk disclaimer is attached to every message.

### Step 5: accounts and payment

- Sign-up, sign-in, free and paid tiers. The free tier's signal allowance is
  enforced on the server.
- Monthly subscription in shekels with cancellation effective at the end of
  the paid period.
- Tools around the signals, in this order if wanted: position-size calculator,
  trade journal, price alerts.

### In parallel, no code needed: content

- Start the Shorts and Reels now. Source material: study results, equity and
  drawdown charts, and the static `report.html` the research tool already
  exports.
- Format that fits what we have: one strategy, one asset, ten years, the
  honest result including drawdowns.
- Do not publish live signals or performance claims until the legal check is
  done.

## 7. Risks to keep in view

- **Backtest and live must not drift.** One shared evaluation path for both;
  otherwise the evidence shown beside a signal is not evidence for it.
- **Few signals.** Four strategies on a short asset list may produce far fewer
  than SBX's 7–12 a month. Measure the expected rate from backtests before
  promising a cadence.
- **Adjusted prices.** Research uses adjusted bars. Decided in Step 2: the
  levels are issued in the prices of the signal session (on that day the
  adjusted close equals the traded close) and are rescaled after a dividend or
  a split; users see the rescaled stop and target.
- **Marketing claims.** SBX's own pages contradict each other (90% hit rate on
  the home page, 56% on the performance page). Our numbers come only from the
  append-only record.
