# Live Execution Contract and Controls Map

**Status:** planning contract, 2026-11-01. Nothing here is authorized for implementation, and no decision to trade paper or live has been taken. Facts about the code were read at HEAD `c5da6c2` (working tree with uncommitted console changes); every claim carries a `file:line` so it can be re-checked. Broker facts come from the Alpaca docs read on 2026-11-01 (`docs.alpaca.markets/us/docs/fractional-trading`, `orders-at-alpaca`, `working-with-orders`).
**Legend:** **[Settled]** required by the agreed direction (reviews of 2026-11-01) · **[Chosen]** recommended choice, changeable without a product decision · **[Open]** needs the user.
**Companion:** `.planning/research/strategy-research-pivot/02-STRATEGY-SPEC-V1.md` (what a strategy is), `.planning/codebase/CONCERNS.md` (API has no authentication; duplicated route helpers).

---

## 1. Purpose and bounds

Define, before any execution code is written, **what exactly is traded** when an approved research strategy runs against a broker account, and **which controls** the minimal execution path must preserve, reuse, replace or add. **[Settled]**

In scope: daily, long-only, single-asset strategies produced by the research product (`strategy_versions`), run one asset per deployment against Alpaca (paper first). Out of scope for this contract: shorting, intraday bars, intrabar stops or targets, portfolio rebalancing, crypto, any broker other than Alpaca, and any change to the research product's evidence rules. **[Settled]**

The three missing links named in the review stand: research → deployable artifact (§5), live signal on a closed bar (§6), signal → order under controls (§4, §8). The AI layer (MCP over the research API) is independent of all three and does not replace portfolio simulation. **[Settled]**

---

## 2. What is traded: signal semantics as implemented

These are the engine's rules today. The live path must implement **the same rules**, not a rebalancing variant. **[Settled]**

| Rule | Behaviour | Where |
|---|---|---|
| Directions | `LONG`, `EXIT`, `FLAT` only; no `SHORT` | `src/trading_platform/strategies/signals.py:18-23` |
| Evaluation | Stateless per bar: insufficient history → `FLAT`; exit tree first → `EXIT`; else entry tree → `LONG`; else `FLAT` | `src/trading_platform/strategies/spec/evaluate.py:178-198` |
| Level vs transition | `gt/ge/lt/le` are level tests, so `LONG` is re-emitted every bar the entry holds; `crosses_above/below` fire on transitions only | `spec/evaluate.py:140-155` |
| History required | `history_required = max(1, max(leaf.bars_needed()))`; evaluation reads exactly that many bars as of the evaluation date | `spec/validate.py:448-449`; `strategies/declarative.py:111-145` |
| Entry when flat | `LONG` on a symbol with no open position schedules an entry at the **next session's open** | `services/backtesting.py:500-525` |
| Entry when open | `LONG` on an open symbol is recorded `ignored_duplicate_entry`; no pyramiding, no resizing | `backtesting.py:508-511` |
| Exit | `EXIT` on an open symbol schedules a full-quantity exit at the next session's open; earliest exit fill is entry + 1 session | `backtesting.py:482-498`, `406-418` |
| Order within a session | fills due today (exits before entries) → generate signals → schedule → write signal rows → mark to market | `backtesting.py:375-579` |
| Missing bar on fill day | entry: skipped (`skipped_entry_fill_count`); exit: position stays open, not re-queued | `backtesting.py:428-431`, `388-390` |
| Last session of window | `no_next_session`: nothing fills; positions are not force-closed | `backtesting.py:485-488`, `504-507`, `601` |
| Research run shape | `max_concurrent_positions=1`, so one asset, one position, slot = initial capital | `services/research/backtest.py:86-96` |
| Data | Tiingo, `adjusted=True` (`adjOpen/adjClose…`), one study-wide `quantity_policy` with no default | `services/research/studies.py:215, 219-220`; `services/research/tiingo_ingestion.py:70-107` |

**Definition of "new LONG" for the live path** **[Settled]** (review, 2026-11-01): a `LONG` evaluation on a closed bar **when the deployment holds no position and has no pending or working entry order**. No requirement that yesterday's evaluation was `FLAT`; that would be a different strategy from the one tested.

**Consequence for live retries:** if an entry order did not fill (rejected, no bar, broker closed), the next bar's `LONG` is again a "new LONG" and schedules again, exactly as the engine's skipped-fill path does. A *working* order (accepted, unfilled, partially filled) is not "no pending entry" and blocks a new order; see control C-18.

---

## 3. Quantity, timing and simulated fill: one linked decision

Three axes, one choice. They are linked because the engine computes the quantity **from the fill price itself** (`_entry_quantity`, `backtesting.py:663-689`): `affordable = min(slot_notional, cash − commission)`, quantity rounded **down** to a whole share or to 1e-6, so `quantity × fill_price ≤ affordable` always holds (`backtesting.py:672-676`). Live, the fill price is not known when the order is prepared. Every option below says how the budget is fixed, when the order is sent, and what the backtest must simulate so the study still describes the deployment.

Shared facts: `slot_notional = initial_capital / max_concurrent_positions` is fixed at run start and does not track equity (`backtesting.py:336`). Entry fill = `open × (1 + slippage_bps/10⁴)`, exit fill = `open × (1 − slippage_bps/10⁴)` (`backtesting.py:649-656`). Commission is flat per order (`backtesting.py:457, 468, 394, 402`). The existing broker client sends `qty` only, never `notional` (`services/alpaca.py:597-604`).

Alpaca facts (2026-11-01): notional and fractional orders require `time_in_force=day`; `opg` is the only order that is eligible **only** in the opening auction and it is rejected between 09:28 and 19:00 ET; a `day` market order submitted after the close is "queued and submitted the following trading day" with no auction guarantee; a timed-out submission "may have been sent to the market" and must not be resent.

| | Option A: qty from previous close, `opg` | Option B: notional `day` order after close | Option C: qty from live price, send after the open **[Chosen]** |
|---|---|---|---|
| Budget / quantity | `qty = floor(affordable / close(T) × (1 + gap_buffer))`, whole shares | `notional = affordable`; broker computes fractional qty | `qty = floor(affordable / latest_trade)` at send time, whole shares |
| When sent | after 19:00 ET on T, routed to T+1 opening auction | after close on T, queued | T+1 inside the regular-hours window, shortly after 09:30 ET |
| Fill simulated as | `open(T+1) × (1+bps)` with quantity fixed from `close(T)`: **engine change** (quantity axis) and a gap-up can break the "never overspend" invariant unless `gap_buffer` reserves cash | `open(T+1) × (1+bps)`, fractional: **engine unchanged** in form, but the real fill is "nearly instantaneous" after the open, not the auction; `slippage_bps` must be calibrated | `open(T+1) × (1+bps)`, whole shares: **engine unchanged**; the real fill is the first trade after send, so `slippage_bps` must be calibrated from paper fills vs official open |
| Fit with existing controls | Conflicts with the execution window (sends only in regular hours, cutoff before close; C-15) and the pre-send price check (price must be after session open; C-14) | Needs a `notional` payload the client does not have; same window conflict | Matches C-14 and C-15 as built; reuses `qty` payload and `whole_shares` policy |
| Equivalence claim | Exact auction open, approximated by `open × (1+bps)` | Weakest: fill time unknown within the day | Open + measured delay, approximated by `open × (1+bps)`; measurable in paper |

**Chosen for the first slice: Option C** with `quantity_policy = whole_shares` in the study, `slippage_bps` set from paper observation, send window "first N minutes after open" as a deployment parameter. **[Chosen]** The user decides whether the auction-exact fill of Option A is worth changing the quantity axis of the engine and relaxing two existing controls. **[Open: O-1]**

Rules that hold under any option **[Settled]**:
- Quantity is fixed once at entry; exits close the full held quantity as reported by the broker, not the intended quantity.
- The quantity is computed from the **raw** price, never from the adjusted series the signal was evaluated on. Tiingo stores both rows (`tiingo_ingestion.py:70-107`).
- Each deployment has its own fixed budget (`initial_capital` of the study, not compounding), and budgets across deployments are independent. Sum of budgets ≤ account equity is a deployment-time check, not a shared cash model. This mirrors `max_concurrent_positions=1` per study; a shared cash model would be a portfolio simulation that does not exist yet.
- A quantity that rounds to zero is a recorded finding and no order (`backtesting.py:441-446`).

---

## 4. Signal → order path: what the minimal path must keep

The legacy chain is `run_risk_evaluation` → approved `RiskEvent` → `run_paper_session` → `AlpacaExecutionService.submit_order` (`services/risk.py:655, 706`; `services/execution/submit_orders.py:731, 3079, 2898, 2951`; `services/alpaca.py:1086`). It is frozen with Phase 20.1 open. The earlier estimate of "a thin ~300-line rebalancer" is withdrawn: the broker client alone refuses to POST without a durable attempt log and classifies every ambiguous outcome (`services/alpaca.py:579-595`, `§8` C-7 to C-9). **[Settled]**

Decision rule for the minimal path **[Settled]**: a control is **kept as is** if it lives below the strategy/risk layer (broker client, attempt log, idempotency, kill switch, send authorization, reconciliation primitives); **replaced** only where its input model is the legacy `RiskEvent`/`strategy_run` shape and the research artifact needs a different producer; **added** where §8 marks it missing. "Bypass 20.1" is not a decision this document makes; the table in §8 is the input to that decision.

---

## 5. Deployment artifact

A deployment is the only object the live path reads. **[Settled]**

**Immutable definition** (one row, insert-only, DB trigger like `strategy_versions` at `db/models/research.py:125`):
- `strategy_version_id` → the approved immutable version (`services/research/strategies.py:341-385`).
- `study_id`, `revision_id`, `freeze_id`, `final_test_run_id` → the exact frozen candidate and the once-only final test that passed (`db/models/research.py:231`; `api/research/studies.py:221-222, 244`).
- `asset` → exactly the asset the final test ran on. A passed test on one asset authorizes nothing for another asset. **[Settled]**
- Policies copied from the study, not referenced: `quantity_policy`, `bar_provider`, `bar_adjusted`, `initial_capital` (budget), `commission_per_order`, `slippage_bps`, `history_required`, calendar pin.
- Execution policy (§3): option, send window, order type, `time_in_force`.
- Broker account identifier and mode (`paper` / `live`), both part of the definition so a live deployment is a different artifact from its paper twin.

**Mutable run state** (separate table, every change audited): `enabled`, `halted_reason`, `last_evaluated_session`, `last_order_id`, `position_snapshot`. `status` is not part of the definition. **[Settled]**

Creation precondition, enforced in code and by a DB constraint where expressible: version approved ∧ freeze exists ∧ final test `passed` on the same asset ∧ no other enabled deployment for the same (account, asset). **[Chosen]**

---

## 6. Live signal and replay contract

**Live evaluation on a closed bar** **[Settled]**:
1. Trigger after session T closes and the Tiingo EOD bar for T exists. Tiingo publication timing is **unverified** (no note in code or README); the job must check presence, not assume it. A missing bar is a recorded event and no action.
2. Verify T is a market session (`market_sessions`) and that the bar's `session_date` matches; otherwise record `missing_bar` / `not_a_session`.
3. Read exactly `history_required` adjusted bars as of T, via the same reader the engine uses (`market_data_access.py:262`, `session_date ≤ as_of`), and persist a digest of that input slice with the evaluation (the research read recorder already produces `input_digest`, `services/research/backtest.py:52-63, 233-235`).
4. Evaluate with `evaluate()`; persist `live_signals (deployment_id, session_date, direction, reason, input_digest, values)` with `UNIQUE(deployment_id, session_date)`.
5. Decide "new LONG" / "EXIT" against the deployment's broker-confirmed position and open orders (§2 definition), never against the previous day's signal.

**Replay** **[Settled]** (review, 2026-11-01):
- For each past session, feed only the data that was available **as of that session** (the persisted input digest and bar snapshot), with the same adjustment policy and the same `history_required` warmup. Tiingo re-adjusts history on dividends and splits and the ingestion upserts in place (`tiingo_ingestion.py:110-132`), so a replay over today's series is not a replay of what the live job saw. Point-in-time snapshots are therefore part of the live job's output, not an afterthought.
- Replay equality (live `direction, reason` == engine `direction, reason` per session) is a necessary gate. It is **not** execution equality (fills, quantities, P&L) and it is **not** TradingView parity; those are separate anchors with separate tests.
- The replay compares the live job's persisted evaluations against an engine run over the same snapshots. Two calls to the same code on the same input are a consistency check, not an independent one; the independent check is the engine's own research backtest on the frozen study, which must agree with the snapshot replay over the overlapping window.

---

## 7. Fault scenarios and paper observation

Scenarios are produced **in a controlled environment** by simulation and fault injection against a fake broker and a fake data source; the paper period then observes the real broker connection. Time alone is not a criterion: a daily strategy may not trade for a month. **[Settled]**

Required injected scenarios, each with the expected end state and the control that produces it:

| # | Scenario | Expected | Control |
|---|---|---|---|
| F-1 | Entry signal, clean fill | one order, one position, run state updated, email sent | C-10, C-11 |
| F-2 | Exit signal, clean fill | position closed at broker-reported qty | C-18 |
| F-3 | Job runs twice for the same session | second run is a no-op (`UNIQUE(deployment, session)`), no second order | C-10, C-20 |
| F-4 | Crash after attempt row committed, before HTTP reply | restart reads the attempt as ambiguous; deployment pauses; no resend | C-7, C-9, C-19 |
| F-5 | HTTP timeout after the POST reached the broker | `AmbiguousOrderSubmissionError`; order later found by `client_order_id`; reconciled, not duplicated | C-8, C-9, C-21 |
| F-6 | Broker 5xx / 429 | ambiguous, paused | C-8 |
| F-7 | Missing EOD bar for T | `missing_bar` event, no evaluation, alert | §6 step 1 |
| F-8 | Bar present but T is a holiday row | `not_a_session`, no action | §6 step 2 |
| F-9 | Partial fill, remainder cancelled at close | position = filled qty; next session's LONG is `ignored_duplicate_entry` (position open) | C-18, C-21 |
| F-10 | Working order still open at next evaluation | new LONG blocked (pending entry exists) | C-18 |
| F-11 | Kill switch tripped mid-run | no send; audit event | C-12, C-13 |
| F-12 | Price check fails (stale, > 5 % from reference) | no send, recorded reason | C-14 |
| F-13 | Send outside the window | no send | C-15 |
| F-14 | Data re-adjusted after a dividend | replay on snapshots still equals live evaluations; replay on current series may differ and is reported as such | §6 |
| F-15 | Budget exceeds account equity at deployment time | creation refused | §5 |
| F-16 | Quantity rounds to zero | recorded finding, no order | §3 |

Paper period exit criteria **[Chosen]**: all F-1 … F-16 passed in simulation; on the real paper connection, at least one F-1 and one F-2 observed, zero unexplained orders, daily reconciliation (positions and open orders vs broker) clean, with transient differences caused by open orders or partial fills classified rather than counted as failures; fill-vs-official-open measured and fed back into `slippage_bps`.

---

## 8. Controls map

Legend for **Disposition**: **Keep** (reuse as is), **Adapt** (reuse with a new producer or input model), **Add** (missing), **Decide** (needs O-n). "Verified" means a test file exists for it per the inventory of 2026-11-01; "unverified" means no test was found or the inventory could not confirm it.

| # | Control | Guarantee | Exists at | Disposition | Verification |
|---|---|---|---|---|---|
| C-1 | Mutations gate | mutating routes refuse unless `orchestration.mutations_enabled` (default False) | `core/settings.py:397`; `api/dependencies.py:58-68` | Keep; apply to every deployment route | `tests/test_mutation_guard.py` |
| C-2 | Broker secrets presence | boot fails without key/secret | `services/config/secrets.py:20-53`; `core/startup.py:62` | Keep | `tests/test_config_validation.py`, `test_startup_validation.py` |
| C-3 | Mode ↔ endpoint | paper requires `paper-api` in URL; live rejects it | `services/config/secrets.py:33-53` | Keep; **Add** exact-host match and a separate live credential set plus explicit live acknowledgement (O-3) | `tests/test_config_validation.py` |
| C-4 | Config bounds | out-of-range values rejected together | `core/settings.py` Field bounds; `services/config/validation.py:116` | Keep | `tests/test_config_validation.py` |
| C-5 | Secret redaction | keys/tokens scrubbed from logs | `core/log_sanitizer.py:17-58` | Keep | `tests/test_log_sanitizer.py` |
| C-6 | Client refuses without credentials | no HTTP without key/secret | `services/alpaca.py:551-556` | Keep | `tests/test_alpaca_execution.py` |
| C-7 | Durable attempt log before POST | an attempt row is committed before each request; append-only | `services/execution/attempts.py:501-535`; `services/alpaca.py:592-594`; trigger in `alembic/versions/0028_…append_only.py:50-104` | Keep (the new producer must bind the log the same way) | `tests/test_order_submission_attempts.py`, `test_phase20_1_attempt_log_append_only_migration.py` |
| C-8 | Single-attempt POST, ambiguous outcomes | POST never goes through generic retry; timeouts after send, 5xx, 429, unparseable 2xx → `AmbiguousOrderSubmissionError`, never resent | `services/alpaca.py:579, 681-815, 1032-1036` | Keep | `tests/test_alpaca_submission_taxonomy.py`, `test_accepted_unparseable_reply.py` |
| C-9 | Duplicate `client_order_id` reply | one lookup; snapshot must match intent or stays ambiguous | `services/alpaca.py:504, 780-` | Keep | `tests/test_alpaca_submission_taxonomy.py:313-365` |
| C-10 | Deterministic `client_order_id` + intent hash | same intent → same id; DB UNIQUE | `services/execution/idempotency.py:72-101`; `db/models/paper_order.py:50-52` | Adapt: intent derived from (deployment, session, direction) | `tests/test_paper_execution.py` |
| C-11 | Fenced send authorization (T1) | operation running, epoch/executor match, lease unexpired, intent sendable; `FOR UPDATE` | `services/execution/operations.py:1622-1780` | Adapt: same gate, deployment as the operation owner | `tests/test_send_reauthorization.py` |
| C-12 | Global kill switch | persisted, default armed; blocks start, mid-run and every T1 | `alembic/versions/0015_…kill_switch.py`; `services/operator_controls.py:1215-1280`; checked `permission.py:497`, `operations.py:1604` | Keep | `tests/test_kill_switch_trip_cli.py`, `test_operator_controls.py` |
| C-13 | Break-glass trip-only CLI | can trip, never re-arm | `worker/commands/operator.py:32-37` | Keep | `tests/test_kill_switch_trip_cli.py` |
| C-14 | Pre-send price sanity | fresh latest trade: finite, positive, after session open, age ≤ 120 s, deviation ≤ 5 % from reference | `services/execution/permission.py:283-345`; `services/alpaca.py:827-870`; `core/settings.py:373-377` | Keep; reference price = previous close under Option C | `tests/test_alpaca_market_data.py`, `test_operation_permission.py` |
| C-15 | Execution window | send only in regular hours of the evaluation session, cutoff before close | `services/execution/permission.py:265, 464-470`; `operations.py:424-460`; `core/settings.py:364-365` | Keep under Option C; **Decide** under A/B (O-1). Fix early-close heuristic first (O-4) | `tests/test_operation_permission.py`, `test_calendar_facts.py` |
| C-16 | Send deadline | expired authorization sends nothing | `services/alpaca.py:624-633`; `core/settings.py:380` | Keep | `tests/test_paper_execution.py` |
| C-17 | One owner, one open operation per strategy | `CHECK id = 1` owner row; partial unique index on open states | `alembic/versions/0022_…active_paper_strategy.py:68`; `db/models/execution_operation.py:141-147` | Adapt: one enabled deployment per (account, asset); one open operation per deployment | `tests/test_active_paper_strategy_gates.py`, `test_execution_operations.py` |
| C-18 | Pause on unaccounted effects | working orders, awaiting reconciliation, unresolved outcome, blocking reconciliation, kill switch, owner, price, risk → pause (closed enum) | `services/execution/permission.py:446-530`; `operations.py:107-124, 790-807` | Keep; this is the "no pending entry" gate of §2 | `tests/test_operation_permission.py`, `test_trading_blockers.py` |
| C-19 | Crash-mid-send recovery | open attempt reads as ambiguous; unresolved intent pauses until reconciled | `services/execution/attempts.py:170`; `services/recovery.py:113-119, 470, 1076`; `operations.py:1741-1743` | Keep | `tests/test_recovery_gates.py`, `test_recovery_terminal_states.py` |
| C-20 | Concurrent run lock | advisory lock per (strategy, session) | `services/concurrency_guard.py:36-120` | Adapt: key on deployment | `tests/test_concurrency_guard.py` |
| C-21 | Reconciliation blocking | trading refused while latest reconciliation not clean or missing | `services/execution/permission.py:372-384`; `submit_orders.py:972`; `core/settings.py:350` | Keep; **Add** explicit partial-fill quantity reconciliation (status mapping only today, `operations.py:790-807`, `recovery.py:452-461`) | `tests/test_execution_reconciliation.py`; partial-fill qty: unverified |
| C-22 | Explicit Continue from PAUSED only | precheck: paused, no working orders, reconciliation clean; CAS | `services/execution/continuation.py:56-93`; `operations.py:1061-1095` | Keep | `tests/test_continue_identity_assertion.py` |
| C-23 | CR-01 Continue/Retry prohibition | interim runbook rule | not code-enforced; only referenced in `tests/test_cr01_reuse_e2e.py:40` | **Decide** (O-5): enforce in code or close CR-01 before paper | unverified |
| C-24 | Order origin immutable | `paper_orders.strategy_run_id` immutable after insert (trigger) | `alembic/versions/0029_…order_origin_immutable.py:76-130` | Adapt: origin = (deployment, session) | `tests/test_phase20_1_order_origin_immutable_migration.py` |
| C-25 | Pinned-intent identity | sent identity equals pinned intent | `submit_orders.py:1469-1515, 2265-2310` | Adapt | `tests/test_continue_identity_assertion.py` |
| C-26 | Max positions / per-symbol single position | refuses entry at limit; refuses second position in a symbol | `services/risk.py:308-322`; `core/settings.py:81` | Adapt: per deployment, max 1 position; per account, one deployment per asset | `tests/test_risk_pipeline.py` |
| C-27 | Allocation caps and cash | strategy/total caps, insufficient cash | `services/risk.py:392-401`; `core/settings.py:293-295` | Adapt: budget per deployment; Σ budgets ≤ equity at creation (§5) | `tests/test_risk_revalidation.py` |
| C-28 | Stale data / symbol readiness | stale or unready symbols refused | `services/risk.py:273-289`; `core/settings.py:296` | Adapt to §6 steps 1–2 | unverified |
| C-29 | Worker lease and heartbeat | 60 s lease, 20 s heartbeat, lost jobs reclaimed | `jobs/queue.py:40-150`; `jobs/runner.py:194-226` | Keep | `tests/test_job_runner.py`, `test_stale_job_reclaim.py` |
| C-30 | Bounded broker pagination | complete listing or typed error | `services/alpaca.py:960-1010` | Keep | `tests/test_alpaca_pagination.py` |
| C-31 | Operator audit trail | every control writes a run and an event | `services/operator_controls.py:735-1385` | Keep; **Add** deployment create/enable/disable events | `tests/test_operator_controls.py` |
| C-32 | API authentication | callers are identified before any deployment or MCP-triggered mutation | none (`CONCERNS.md`; `api/app.py` has no auth or CORS) | **Add** before §5 routes or any MCP exposure. Localhost is not authorization. | — |
| C-33 | Daily loss limit / drawdown halt | stop sending after a loss threshold | none found (`grep daily_loss\|drawdown\|loss_limit` hits only backtest metrics) | **Add** (O-6: threshold semantics) | — |
| C-34 | Max order notional / fat-finger cap | refuse any single order above a cap independent of budget | none found | **Add** | — |
| C-35 | Automatic kill-switch trip | trip on repeated ambiguous outcomes or reconciliation failures | none (trip is manual: HTTP and CLI) | **Add** | — |
| C-36 | Scheduler | run the live evaluation after each close | none (`execution.paper_session_runner.cadence` has no consumer; `core/settings.py:335-337`) | **Add** (worker loop or system cron; not an LLM scheduled task) | — |
| C-37 | Point-in-time input snapshot | live evaluation persists the exact bar slice it saw | partial (research read recorder, `research/backtest.py:52-63`) | **Add** for the live job (§6) | — |
| C-38 | Replay gate | live evaluations equal engine evaluations on snapshots | none | **Add** (§6) | — |
| C-39 | Alerting | email on trade or issue only | none found in `src/` | **Add** | — |

---

## 9. Open decisions and concerns

| # | Item | Needed for |
|---|---|---|
| O-1 | Execution option A / B / C (§3). Recommendation: C. | everything in §4–§8 |
| O-2 | Market and broker: Alpaca US equities (adapter exists) vs crypto (new adapter). Assumed Alpaca here. | §3, §8 |
| O-3 | Live acknowledgement model: separate credential set, explicit `LIVE_TRADING_ACK`, exact-host endpoint match (C-3). | before any live deployment |
| O-4 | **Concern:** `early_close` is set when the close is before 20:30 UTC (`services/calendar.py:202-207`). A regular EDT session closes 16:00 ET = 20:00 UTC, which this flags as early. Consumed by `services/calendar_facts.py:207, 233` (execution window) and `research/freeze.py:59, 107`. **Confirmed 2026-11-01** by running the heuristic against `exchange_calendars` XNYS: 2026-07-15 closes 20:00 UTC → flagged early; 2026-11-27 (18:00 UTC) → early, correct; 2026-12-15 (21:00 UTC) → not early. Every EDT regular session (March–November) is misclassified. Only November/December cases are tested (`tests/test_calendar_facts.py:511-514`). Must be fixed before relying on C-15; also affects what `research/freeze.py` records. | C-15 |
| O-5 | CR-01: enforce the Continue/Retry prohibition in code, or close CR-01 (plan 20.1-26, verification 20.1-31) before paper. | C-23 |
| O-6 | Loss-limit semantics (per deployment vs per account, realized vs marked). | C-33 |
| O-7 | Tiingo EOD publication time and whether the personal-use licence covers trading on the data. Unverified. | §6 step 1 |
| O-8 | Whether the paper-only guard (C-3) and the research-mode router exclusivity (`api/app.py:150`) mean the live path runs as a third mode or inside trading mode. | §5 routes |

---

## 10. Sequence (adopted, not authorized)

1. Close O-1 … O-3; verify O-4 and O-7.
2. Write the strategy and execution contract into code as types and DB constraints (deployment definition, run state, `live_signals`).
3. Live evaluation job with snapshots, **no order path** (C-36, C-37); run it for real on paper data for at least the warmup horizon.
4. Replay gate (C-38) and fault injection F-1 … F-16 against fakes.
5. Deployment routes behind C-1 and C-32; audit events (C-31); added controls C-33 … C-35, C-39.
6. Minimal order path reusing C-7 … C-22 with the deployment as producer; paper only (C-3).
7. Paper observation against §7 exit criteria.
8. Separate decision on live.

MCP over the research API can proceed in parallel with 1–4. It touches no deployment or order route until C-32 exists.
