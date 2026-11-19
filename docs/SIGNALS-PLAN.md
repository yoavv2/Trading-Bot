# Signals product: work plan

Written 2026-11-19. This is the single entry point for continuing the work in a
new session. It records what was decided, what the repository contains now, what
is still open, and the order of the build.

## 1. Start here (new session)

1. Read this file, then `docs/STRATEGY-SPEC-V1.md` (the contract the validator
   and interpreter implement).
2. Check section 4 (blockers) and section 5 (open decisions) with the owner
   before writing code that depends on them.
3. The next engineering step is **Step 1** (section 6): one long strategy,
   end to end, up to an internal signal log. It depends on none of the
   blockers. Begin with the three shared test cases listed there.

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
- Research database `trading_research`, migrations through `0033`.
- Four approved example versions (trend following 50/200, RSI mean reversion,
  Donchian breakout, time-series momentum).

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

**Tests:** `PYTHONPATH=src .venv/bin/pytest -q`. One known old failure remains:
the `universe_size` assertion in `tests/test_app_boot.py` (trading era). The
full suite was not run after the cleanup; four focused files were (70 passed,
that one failure).

## 4. Blockers the owner must close

Neither blocks Steps 1–3. Both block publishing signals to other people.

| Blocker | Why it matters | Blocks |
|---|---|---|
| Legal check with a lawyer | Publishing trade signals for payment in Israel may count as investment advice or marketing, which requires a licence from the Israel Securities Authority. SBX states it holds none. | Step 4 onward |
| Commercial market-data licence | The Tiingo key is a personal-use licence. It does not cover distributing signals derived from the data. | Step 4 onward |

## 5. Open decisions

| Decision | Options discussed | Needed by |
|---|---|---|
| Distribution channel | WhatsApp channel, Telegram, own app with push | Step 4 |
| New UI | Not designed. Public performance page first, or member app first | Step 3 or 5 |
| Free versus paid split | SBX gives 1–2 free signals a month | Step 5 |
| Which strategy version publishes first, and on which assets | Chosen from a new v2 study (Step 1), not from v1 results | Step 1 |
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
2. Otherwise check the session's low and high against the stop and target. If
   only one is touched, exit at that level.
3. If both are touched, record a stop exit and set the flag
   `intrabar_order_unknown`. A daily bar cannot show which came first. The
   flag lets us report how much of the result depends on this assumption.
4. The strategy's own exit rule is evaluated at the close and executed at the
   next session's open. Stop and target therefore take precedence within the
   day.

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

### Step 2: daily signal engine

Hardens the daily run that Step 1 introduced.

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

### Step 3: append-only performance record

Turns Step 1's internal log into the public record.

- Signals and their events (`issued`, `entry_filled`, `stop_moved`,
  `target_hit`, `stop_hit`, `rule_exit`) are append-only. A database trigger
  refuses UPDATE and DELETE, the same pattern as approved strategy versions.
- The record is published before the outcome is known: an `issued` event's
  timestamp precedes the session it applies to, and a test asserts it.
- A read-only API returns every closed signal, losers included, with entry,
  exit, R result and running totals. Open signals are excluded from totals.
- The same page shows, for each strategy version, its backtest evidence beside
  its live record. This is the product's main claim.
- This step needs a first screen, so the UI decision in section 5 comes due.

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
- **Adjusted prices.** Research uses adjusted bars. Entry, stop and target
  shown to users must be in prices they can actually trade; decide this in
  Step 2.
- **Marketing claims.** SBX's own pages contradict each other (90% hit rate on
  the home page, 56% on the performance page). Our numbers come only from the
  append-only record.
