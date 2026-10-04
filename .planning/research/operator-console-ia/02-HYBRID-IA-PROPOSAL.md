# Operator Console — Hybrid Information Architecture Proposal

_Date: 2026-09-29 · Revised: 2026-09-30, twice (operator decisions and the single-active-paper-strategy scope; see §0.1) · Type: proposal (no implementation, no API changes made) · Builds on: [01-IA-EXPLORATION.md](01-IA-EXPLORATION.md) · Planning impact: [03-PLANNING-CHANGES.md](03-PLANNING-CHANGES.md) · Evidence: Paper file "Operator Console — IA Exploration" (artboards A.1–A.3, B.1–B.3, C.1)_

## 0. Summary

The hybrid combines three answers to three different operator questions:

| Operator question | Answering model | Source direction |
|---|---|---|
| "Is everything OK right now?" | **Posture**: four always-visible lanes + a backend verdict | A (A.1, A.3) |
| "How do I run today's trading, and why did it (not) trade?" | **Session pipeline**: Data → Decide → Trade → Sync → Verify, one page per trading session | B (B.1, B.2, B.3) |
| "What needs me?" | **Attention**: ranked, self-resolving issues with cause, consequence and the safe next action | C (C.1) |

Jobs become execution infrastructure: operators meet **operations** (in plain words) and their **outcomes**; the Job is the technical record behind them. **"Run" is retired as an operator concept**: its six storage types are redistributed to Sessions, Research, Activity and technical views.

Design rules used throughout:
- **Closed enums, backend-owned meaning.** Every state the operator reads (lane state, stage status, issue rule, outcome) is a closed enum computed in the backend; the frontend owns wording and layout only.
- **Honest unknowns.** Anything the system cannot observe renders as `unknown` / "not reported", never as OK.
- **One read model, many lenses.** History is one Activity read, filtered per context; issues are one Issues read, filtered per area.
- **Asymmetric safety.** Actions that reduce risk are always reachable; actions that increase exposure carry prerequisites, consequences, and (on mobile) are desktop-only.

### 0.1 Operator decisions (2026-09-30)

| # | Topic | Decision | Where applied |
|---|---|---|---|
| 1 | Current session | The **Market Calendar is the source of truth**. Never fall back silently to the latest session that exists in the database; if the calendar does not cover the relevant day, the state is explicit `unknown` ("Calendar data unavailable"). An old session is never presented as current. | §4.4, §5.2, correctness fix COR-04 |
| 2 | Phase 21 / UI | Option **(b)**: no temporary UI. AUD-02 and NOTIF-02 move to the Operator Console rebuild, a **separate milestone**. Phase 21 is re-planned around read models/APIs. | §12, 03 |
| 3 | Snapshot / buying power | A **correctness bug**. Fix the source; do **not** encode Sync-before-Trade into the UX. The stage order is finalized after the fix, from domain requirements. | §4.2, COR-01 |
| 4 | Worker heartbeat | **Approved**, persistence allowed. Worker states: alive & idle, busy, unavailable, unknown. No active Job ≠ healthy. | §10 R7, §14 |
| 5 | Mobile | Initial mobile = status, attention, activity, current session, and appropriate emergency/risk-reducing controls. No ordinary remote mutations until an authentication and network-access model exists. | §11 |
| 6 | Issues | **Projection of current state**: not persisted, no acknowledge/snooze/manual resolution. The underlying events stay in Activity. | §5 |
| 7 | Technical records | Kept, behind **System › Technical** (Jobs, raw records, logs, IDs, payloads, raw results). | §2, §6, §7 |
| 8 | Control changes | Presented as **Control Changes / Activity events**, never Runs. Read models translate storage; no storage migration is a prerequisite. | §7, §8 |

**Scope decision (2026-09-30, second round):**
- Multiple strategies are supported for research and backtesting.
- Only **one** strategy at a time may paper-trade on the Alpaca account.
- Concurrent multi-strategy paper trading is a separate future capability. Shared-capital allocation is not a prerequisite for the correctness fixes or the rebuild.

Applied in §0.2, §1, §2.1, §5.2 and 03 §1–§2.

The separation of **settled decisions / recommendations / open questions** is maintained in [03-PLANNING-CHANGES.md](03-PLANNING-CHANGES.md) §0.

Correctness work (COR-01..05 and the paper-account ownership items, specified in 03) precedes the read-model phase: `correctness fixes → Phase 21 read models → UI/UX design → Operator Console rebuild`.

### 0.2 Strategy states under the single-active-paper-strategy scope

Four distinct facts are never conflated in the UI or read models:

| Fact | Scope | Meaning | Backed by |
|---|---|---|---|
| **Registered** | every strategy in the code registry | the platform knows the strategy (rules, universe, config) | `StrategyRegistry` |
| **Available for research** | every registered strategy | may be backtested and inspected; never touches the broker | no gate (backtests ignore status today) |
| **Enabled** | per strategy | *if* it is the active paper strategy, it may submit new orders; disabled blocks submission but **does not remove exposure** | `strategies.status` (`active` in the DB = "enabled" in the UI) |
| **Active paper strategy** | **exactly zero or one** for the account | the strategy that owns the Alpaca paper account: its sessions, orders, fills and positions are the account's | new persisted singleton (03 PAPER-01) |

The DB enum value `active` means "enabled". Read models expose `enabled`, so "active" is reserved for the active paper strategy.

---

## Operator vocabulary (closed set)

| Term | Meaning | Backed by |
|---|---|---|
| **Trading permission** | Whether new paper orders may be submitted: `allowed` / `blocked`, with blocker codes (`kill_switch_tripped`, `no_active_paper_strategy`, `strategy_disabled`, `reconciliation_blocking`, `unrecognized_broker_activity`, `outcome_unresolved`, `working_order_commitments_unaccounted`) | kill switch + active paper strategy + its enabled status + latest persisted reconciliation `blocks_execution` |
| **Active paper strategy** | The single strategy that owns the paper account (§0.2) | PAPER-01 |
| **Handover** | Changing the active paper strategy under the transition rule (03 PAPER-02) | control change |
| **Posture lane** | One of four always-visible system states | derived reads (§10) |
| **Trading session** | (strategy, session date) and its pipeline | jobs + runs attributed to that `as_of_session` |
| **Stage** | Data, Decide, Trade, Sync, Verify | job types + records per stage |
| **Operation** | One thing the operator asked the platform to do, in plain words ("Ingest market data") | a Job (or a control write) |
| **Attempt** | One execution of an operation inside a stage | a Job |
| **Outcome** | What an operation actually achieved (`traded`, `no_candidates`, `partial`, `uncertain`, …) | job status + `result_summary` classification |
| **Issue** | A condition that needs a human, with cause, consequence, recommended action, and resolution rule | Issues read (§5) |
| **Control change** | Kill switch trip/reset or strategy enable/disable, with reason | operator_control run + ExecutionEvent |
| **Books vs broker** | Whether local orders/fills/positions/account agree with Alpaca | reconciliation result |
| **Backtest** | Historical simulation result | backtest run |
| **Technical record** | Job lifecycle, logs, events, payload, run IDs, raw codes | Job + StrategyRun rows |

Retired from primary UI: *Run*, *Job* (as a noun in navigation), *ARMED*, *execution findings* (split into control changes vs reconciliation findings), raw `snake_case`/`kebab-case` codes.

---

## 1. Top-level navigation

**Persistent header (every page, desktop and mobile):**
1. **Environment + mode chip**: `Paper · Alpaca` and, when `mutations_enabled=false`, `Read-only`. The operator must never wonder whether an action is live or possible.
2. **Posture strip**: four lanes (compact on every page, expanded on Overview). Each lane opens its owning area.
3. **Attention badge**: count of open issues, colored by the worst severity, opens the Attention drawer.
4. **Operations tray**: in-flight operations with progress. Hidden when nothing is running.
5. **Stop trading**: always visible; opens the stop sheet (A.3).

**Primary navigation (desktop left rail; mobile tabs + More):**

| # | Area | One-line purpose |
|---|---|---|
| 1 | **Overview** | Is everything OK, and what should I do next? |
| 2 | **Trading** | Run and understand trading sessions; trading permission and controls |
| 3 | **Portfolio** | What I hold and whether my books agree with the broker |
| 4 | **Market data** | Is the data the strategy needs present and fresh? |
| 5 | **Research** | Strategy definition and backtests |
| 6 | **Activity** | Everything that happened, filterable |
| 7 | **System** | Operations engine, technical Jobs explorer, health and configuration |

Posture lane → owning area (closed mapping):

| Lane | Area it opens | States (closed) |
|---|---|---|
| Trading permission | Trading › Permission | `allowed` · `blocked` |
| Market data | Market data | `ok` · `attention` · `blocking` · `unknown` |
| Books vs broker | Portfolio › Books vs broker | `ok` · `attention` · `blocking` · `unknown` |
| Operations engine | System › Worker health | `ok` · `attention` · `blocking` · `unknown` (from worker health `idle`/`busy` → ok, `unavailable` → blocking, `unknown` → unknown; queue/failure rules → attention) |

**Strategy scope in the header:** the environment chip reads `Paper · Alpaca · Active: <strategy>`, or `Active: none`. **None is the settled initial state (03 E-2)**: the operator selects the first strategy after the account checks pass (03 §3.3). Trading, Portfolio and the posture lanes are scoped to the active paper strategy (the account's owner). Research lists every registered strategy. Activity shows everything, with a strategy column.

Rationale from the evidence: A.1 showed lanes answer W1 on every page but "Allowed" alone was misleading while nothing could trade. The **verdict** (§3) is what combines lanes into a conclusion. The header therefore carries lanes; Overview carries the verdict.

---

## 2. Responsibility of every top-level area

### Overview
Answers W1 and "what next". Owns: verdict, next action, expanded posture, top issues, evaluation session summary, account summary, recent activity. Owns no actions except the single recommended next action and Stop trading.

### Trading
Owns the trading workflow (W2, W3, W4, W5).
- **Next execution**: the pipeline for the evaluation session whose execution window is next or open (B.1). This replaces the former "Current session" view (§4.4).
- **Sessions**: journal, one row per session date with an outcome sentence (B.3 Journal).
- **Session page**: frozen pipeline for a historical evaluation session or the one being executed. Stage detail: decisions table, orders, attempts, technical records (B.2).
- **Permission & controls**: trading permission with each gate explained, kill switch trip/reset, strategy enable/disable, control-change history (the current `/controls`, reframed).

Actions owned: Evaluate signals & risk, Run trading session, Sync from broker, Check books vs broker (session-scoped), Stop trading / Re-arm, Disable / Enable strategy.

### 2.1 Strategy scope across areas

| Area | Scope |
|---|---|
| Overview, Trading, Portfolio | The active paper strategy = the account owner. With no active strategy, Trading shows "No active paper strategy" and the handover action |
| Trading › Permission & controls | Active paper strategy (name, since, reason), **Change active strategy** (desktop only; the PAPER-02 checklist is shown as live preconditions), enable/disable, kill switch |
| Research | All registered strategies, each badged `research only` or `active paper strategy`, with enabled status shown |
| Market data | Union of universes of registered strategies (identical today) |
| Activity | All strategies; handovers are Control Changes |

### Portfolio
Owns positions, open orders (with lifecycle state), fills, account (cash, equity, buying power, exposure) with its **as-of and source** (`broker_sync` vs `risk_evaluation`), and **Books vs broker**: latest check result, findings by category and symbol, account divergence, history of checks. Actions: Sync from broker, Check books vs broker.

### Market data
Owns W8: calendar range, latest session with complete universe data, per-symbol coverage for the evaluation session and the warmup window, ingestion history including **partial** ingestions, symbol reference data. Actions: Ingest market data (prefilled from gaps, A.2), Sync market calendar, Sync symbol reference data. Required metadata gates each symbol's readiness for trading; a symbol missing it shows `not_ready(missing_metadata)` (03 R-29, H-3).

### Research
Owns W7: strategy definition (rules, universe, parameters, config source, version), backtests list/detail (metrics, equity curve, trades, assumptions), and later Strategy Lab work (Stage 2). Action: Run backtest. **Does not own** enable/disable: that is a trading gate and lives in Trading › Permission.

### Activity
Owns W9: one chronological, filterable stream of operations, control changes, session outcomes and reconciliation results, all in operator words (§8). Replaces the Runs list as the place to answer "what happened". No actions except re-running an operation from its item (it opens the owning area's action with the same parameters).

### System
Owns W10 and infrastructure:
- **Worker health**: worker state (idle · busy · unavailable · unknown), queue, running operations.
- **Health**: health, readiness, configuration, database.
- **Technical** (decision 7): the deep-observability area, deliberately outside the normal operator workflow.
  - **Jobs**: the current generic Job list and detail, kept unchanged, with cancel and retry.
  - **Records**: raw StrategyRun rows (all six run types), execution events, ingestion runs.
  - Logs, internal IDs, payloads, raw `result_summary` JSON.

System › Technical is the only place the word "Job" appears as a navigation noun.

---

## 3. Overview information hierarchy

Top to bottom; the order is the priority.

1. **Verdict** (one sentence) + **next action** (one button with a consequence line).
   - Backend returns `verdict_code` + params; frontend renders the sentence.
   - Closed `verdict_code`: `all_clear`, `trading_blocked`, `will_not_trade`, `needs_verification`, `operation_in_progress`, `attention_needed`, `unknown_state`.
   - S1 renders `needs_verification`: "Earlier broker operations ended uncertain and haven't been verified, and nothing can trade: the market calendar ends on 13 Mar 2026." The next action is **Check books vs broker** (precedence rule 2), then **Sync market calendar** (rule 5).
   - The three uncertain failures are classified `nothing_submitted`: 0 locally registered intents, and in code every broker submission is preceded by a durable registration. They are **unresolved** until a clean, account-wide standalone reconciliation follows the latest broker-touching Job (03 §1.5 B).
   - Under the current scope S1 also shows `no_active_paper_strategy` if the recommended no-owner seed (03 R-2) is adopted. This is backed by a live DB read: `market_sessions` spans 2024-12-20 → 2026-03-13, and `daily_bars` max is 2026-03-13.
2. **Posture, expanded**: four lanes with reason lines (A.1 strip, expanded).
3. **Attention**: open issues ranked by severity (top 3 + "all N"), each with its recommended action (C.1).
4. **Current session**: a mini pipeline (five stage chips + blocking stage named) linking to Trading › Current session (B.1).
5. **Account**: equity, cash, open positions, open orders; **as-of and source** always shown.
6. **Recent activity**: last five Activity items.

**Not on Overview**: DB host/driver, schema tooling, versions (System); Job tables; raw codes.

**Next-action precedence** (closed, first match wins; testable):
1. `kill_switch_tripped` → none automatic; offer "Review stop reason" (re-arm is deliberate, never recommended).
2. `outcome_uncertain_unverified` → **Check books vs broker**.
3. `reconciliation_blocking` → **Sync from broker**, then **Check books vs broker** (see §4 loop).
4. `strategy_disabled` → "Review strategy status" (no automatic enable).
5. Earliest blocking pipeline stage for the evaluation session → that stage's primary action.
6. `operation_failed_repeatedly` → "Open latest failure".
7. Otherwise → none (`all_clear`).

---

## 4. The Data → Decide → Trade → Sync → Verify workflow

### 4.1 Stages

| Stage | Operator question | Operations (job types) | Primary record | Done means |
|---|---|---|---|---|
| **Data** | Do we have the bars and calendar this session needs? | `ingest-bars`, `sync-market-sessions` | coverage read (§10 R4) | Every universe symbol has a bar for the session and ≥ `warmup_periods` sessions of history; the calendar covers the session |
| **Decide** | What does the strategy want, and what does risk allow? | `risk-evaluation` | risk_evaluation run + risk events | A succeeded evaluation exists for the session **and** is newer than the latest data change for that session |
| **Trade** | Were approved orders submitted? | `paper-session` | paper_execution run (+ its internal reconciliation run) | Session ran with outcome `traded`, `no_candidates` or `existing_orders`, and no uncertain attempt is unverified |
| **Sync** | Do my books reflect the broker? | `broker-order-sync` | broker_sync account snapshot, order states, fills, positions | A sync completed after the last Trade attempt |
| **Verify** | Do books and broker agree? | `reconciliation` | reconciliation run (standalone) | The latest check has `blocks_execution=false` and 0 findings |

**Stage status (closed):** `not_started`, `in_progress`, `complete`, `complete_with_warnings`, `outdated`, `blocked`, `failed`, `uncertain`.
- `outdated`: its inputs changed after it completed (Decide older than the latest ingest; Verify older than the latest Trade).
- `blocked`: a prerequisite stage isn't complete.
- `uncertain`: a broker-touching attempt failed with `outcome_uncertain` and no **clean** verification followed.

Every stage shows: status, one explanation line, **one** primary action with its consequence line, attempts (Jobs) with outcome, and links to the records.

### 4.2 The pipeline is not strictly linear: the loops the backend enforces

1. **Trade contains its own Verify.** Every paper session recovers in-flight orders and reconciles before submitting. A drifted book blocks the session (`blocked_reconciliation`) even if the standalone Verify stage is green.
2. **Verify after Trade.** Fills and positions arrive only through **Sync**; Trade never produces them. The honest post-trade order is Trade → Sync → Verify.
3. **Uncertain → Verify before retry.** After any broker-touching attempt ends `outcome_uncertain`, the next step is Verify; the Trade action is locked until a **clean** check (`blocks_execution=false`) exists.
   - **Definition — resolved (03 §1.5 B; draft):** a clean reconciliation is **necessary but not sufficient**. Recovery classifies every registered intent of the failed operation by its actual broker state:
     - not found;
     - open;
     - partially filled;
     - filled;
     - canceled/expired;
     - rejected;
     - unresolved.

     Only then, **plus** a fresh, clean standalone reconciliation (account-level or owner-level) after the latest broker-touching Job, is the outcome resolved. "Absent" needs the full lookup-evidence rule (03 §3.7 B); anything less stays unresolved.
   - **Resolving uncertainty ≠ waiting for an order to finish.** A verified working order is never resubmitted and isn't uncertainty. It blocks new submissions only as `working_order_commitments_unaccounted`, while commitment-aware risk accounting is deferred (03 F-3). Within one operation, the first working order pauses the operation; continuing requires terminal orders, a sync, a fresh clean check, an open execution window and a permission re-check (03 §3.7 A2).
   - **Recommendation (revised):** only a **standalone** reconciliation (report-only, nothing submitted after it within the same operation) counts. The in-session pre-check is followed by submissions in the same run, so it can't certify the state it leaves behind. Open question 03 §0.3 O-3.
   - **Gate scope (03 R-11/R-12):** while an uncertain failure is unverified, **every** Run trading session action is locked (fresh or retry), not only Retry. Sync from broker stays available, because it is the recovery step.
   - In S1 the three uncertain failures had **no registered intents**, so they are classified `nothing_submitted`. That is necessary but not sufficient: they stay unresolved until a clean, account-wide standalone reconciliation runs (none has).
   - Today's backend retry gate (Phase 20 D-19) accepts any newer *succeeded* reconciliation Job, even one that found drift. **COR-02** changes the gate to this same clean-verification predicate, implemented once in the backend and shared by the retry gate and the issue rule. Whether the in-session check counts for the retry unlock is an open question in 03.
   - Under the alternative (count in-session checks), the 15:47:16 in-session check would have cleared them.
4. **Decide pollutes the account snapshot. This is a backend defect, confirmed on live data.**
   - `risk-evaluation` writes a strategy AccountSnapshot with `buying_power = cash` (`services/portfolio.py:191-201`).
   - Reconciliation compares the **latest** strategy snapshot, whatever its source, against the broker account (`services/reconciliation/report.py:353-361, 586-592`).
   - Live evidence, 2026-09-29:
     - The risk snapshot at 13:22 said buying power $100,000; the broker says $400,000.
     - The standalone reconciliation at 15:46:50 therefore returned `blocks_execution=true` with 0 findings and `account_divergence.buying_power`.
     - The broker sync at 15:46:58 wrote a broker snapshot, and the in-session check at 15:47:16 was clean.
     - Today's console showed only the later, clean check.
   - **Decision 3:** this is a correctness bug, tracked as **COR-01** (03-PLANNING-CHANGES.md). The IA does **not** encode a Sync-before-Trade workaround.
   - The canonical order stays Data → Decide → Trade → Sync → Verify, **provisionally**. It is finalized after COR-01 from the domain requirement it settles: where account truth comes from, and what an evaluation may assume about the account.
5. **Trade without Decide is unsafe to attempt.** *Inferred, not executed:* the paper-session handler logs `external_broker_session_started` before the risk-run lookup (`jobs/handlers/paper_session.py`). A missing risk run would then fail with `outcome_uncertain=true` and trigger the reconcile-first lock.
   - Therefore **Run trading session is disabled until Decide is complete for that session.**
   - This must be a machine-readable prerequisite in the job-type catalog (§10 R8), not UI copy.

### 4.3 Session attribution (backend rule)

| Kind | Attribution |
|---|---|
| `risk-evaluation`, `paper-session`, `broker-order-sync`, `reconciliation` | payload `as_of_session` |
| `ingest-bars`, `sync-market-sessions` | every session within `[from_date, to_date]` (shown in the Data stage of each covered session) |
| `backtest`, `sync-symbol-metadata` | no session (Research / Market data) |
| Control changes | **global**, never session-attributed. Shown in Activity and Trading › Permission, and in a session's timeline only as "permission changed during this period". Resolves the open question on B.2. |

### 4.4 Calendar, evaluation session and execution window (three separate facts; revised 2026-09-30, 03 §3.10)

"Current session" is **retired as a single field**. The backend computes three closed facts, each with its own `unknown` state:

| Fact | Meaning | Closed states |
|---|---|---|
| **Trading day** | The relevant trading day and its phase according to the **persisted Market Calendar** at server time | `known(date, phase: pre_open \| open \| after_close \| non_trading_day)` · `unknown(calendar_data_unavailable)` |
| **Evaluation session** | The latest **completed** session whose required data is ready (bars for every universe symbol + warmup history). It is the data date signals are computed from | `ready(session)` · `not_ready(session, reason)` · `unknown(calendar_data_unavailable)` |
| **Execution window** | When orders from an evaluation of session S may be submitted: the regular hours of the **next trading session after S**, from open until a configurable cutoff before close | `open(until)` · `closed(next_opens_at)` · `unknown(calendar_data_unavailable)` |

- **Paper trading is eligible** only when all hold:
  - the trading day is known and in phase `open`;
  - now is inside the execution window of evaluation session S;
  - S is the session **immediately before** today's session (no gap: fresh);
  - S is `ready`;
  - the evaluation used is newer than the latest data change for S.
- **Historical execution is rejected** (`historical_execution_rejected`). Research, backtests and risk evaluation of any past session stay available.
- Using the **previous completed session's data during today's window is the normal, valid case**, not "historical".
- **Calendar coverage:** sync ahead of today up to a configurable horizon (03 R-21). An issue fires when the coverage runway falls below a threshold.
- **S1 (verified):** the calendar ends 2026-03-13, so the trading day, evaluation session (for execution) and execution window are all `unknown(calendar_data_unavailable)`. `calendar_out_of_range` is the root blocker. 13 Mar remains usable for research and is labelled historical.
- **UI wording:** "Trading day: Tue 29 Sep, open · Evaluating: Mon 28 Sep (data ready) · Execution window: open until 15:45". It is never one "current session" label.
- The session picker (B.1) selects an **evaluation session** for inspection; a past one is labelled **historical** and cannot be executed.

---

## 5. Global attention / issues

### 5.1 Issue shape (Issues read, §10 R2)

`{ key, rule, severity, lane, subject, title_params, cause_params, consequence_code, recommended_actions[{operation, params, consequence_code}], evidence[{kind, id}], opened_at, resolves_when_code }`

- **A projection of current state (decision 6)**: computed from current records on every read. Not persisted. No acknowledge, snooze or manual resolution. The condition clears → the issue disappears. The underlying events remain in Activity.
- `key` = `rule:subject` is stable, so the UI can highlight new issues.
- **Severity (closed):** `safety` > `blocking` > `action_needed` > `unknown` > `info`.

### 5.2 Rules (closed list; every rule maps to exactly one lane)

| Rule | Severity | Lane | Condition (from existing data unless noted) | Recommended action | Resolves when |
|---|---|---|---|---|---|
| `kill_switch_tripped` | safety | Trading permission | kill switch `tripped` | Review stop reason (re-arm is deliberate) | re-armed |
| `no_active_paper_strategy` | blocking | Trading permission | no strategy owns the paper account | Choose active strategy (desktop) | an owner exists |
| `strategy_disabled` | blocking | Trading permission | the active paper strategy is `disabled` | Review strategy | enabled |
| `handover_blocked` | info | Trading permission | a handover was requested, but PAPER-02 preconditions are unmet (open orders or positions remain) | Show what remains | preconditions met or request withdrawn |
| `outcome_uncertain_unverified` | safety | Books vs broker | a failed Job with `outcome_uncertain=true` that isn't **resolved** (03 §1.5 B: per-intent broker-state classification + clean account-wide standalone reconciliation). Same backend predicate as the retry and fresh-submission gates | Sync from broker, then Check books vs broker | resolved |
| `working_order_commitments_unaccounted` | blocking | Trading permission | a known working order (open, partially filled, done for day) exists, and risk accounting can't include open-order commitments yet (03 §3.7 A, P-1). Distinct from uncertainty; the order is never resubmitted | Wait for the order to finish; see the order | the order is terminal and the continuation checks pass |
| `outcome_uncertain_unresolved` | safety | Books vs broker | recovery ran but at least one intent's broker state is still unresolved (lookup error, ambiguous, grace period exceeded) | Sync from broker (repeatable); escalate if it persists | classified |
| `unrecognized_broker_activity` | safety | Books vs broker | any broker order/fill/exposure not attributable to platform records (03 §1.3), anywhere in account history | Review items; recovery import (platform-originated) or "Record external activity" (03 §1.6) | every item attributed or recorded; unexplained exposure = 0 |
| `reconciliation_blocking` | blocking | Books vs broker | latest reconciliation `blocks_execution=true` (findings, account divergence, or threshold breach) | Sync from broker → Check books vs broker | a later check is clean |
| `order_state_unknown` | safety | Books vs broker | any open order in `unknown`/`submission_failed` state, or a `reconciliation_scheduled` event since the last clean check | Sync from broker | order resolved |
| `broker_sync_stale` | action_needed | Books vs broker | open orders exist and the last broker sync is older than the threshold | Sync from broker | sync after threshold |
| `market_data_incomplete` | blocking | Market data | evaluation session: a universe symbol is missing a bar or lacks warmup history | Ingest market data (prefilled symbols/range) | coverage complete |
| `market_data_stale` | action_needed | Market data | latest complete-data session is behind the evaluation session | Ingest market data | caught up |
| `calendar_out_of_range` | blocking | Market data | the persisted calendar doesn't cover the relevant trading day → evaluation session `unknown` (COR-04) | Sync market calendar | calendar covers the relevant day |
| `calendar_runway_low` | action_needed | Market data | calendar covers today but ends within N sessions (only if COR-04 allows syncing ahead) | Sync market calendar | runway ≥ N |
| `ingestion_partial` | action_needed | Market data | an ingest operation whose outcome is `partial` (COR-03 semantics; the 15:48 QQQ case) and whose failed symbols are still missing | Open ingestion detail | a later ingest covers the failed symbols |
| `evaluation_outdated` | action_needed | Market data | Decide stage missing for the evaluation session, or its **input manifest no longer matches** current data (corrected or re-ingested bars, changed settings; 03 R-25) | Evaluate signals & risk | a fresh evaluation whose manifest matches |
| `operation_failed_repeatedly` | action_needed | Operations engine | ≥ 2 failures of one job type in 24 h and no later success | Open latest failure | a later success |
| `queue_stalled` | unknown | Operations engine | oldest queued Job older than the threshold and nothing running | Check worker (System) | queue moves |
| `worker_unavailable` | safety | Operations engine | worker health `unavailable` (last heartbeat older than threshold, or stopped) | Check the worker (System) | a fresh heartbeat |
| `worker_unknown` | unknown | Operations engine | worker health `unknown` (no heartbeat ever recorded) | — | a heartbeat is recorded |
| `mutations_disabled` | info | Operations engine | `mutations_enabled=false` | — | flag on |

**Unknown evaluation session (decision 1):** when the evaluation session is `unknown`, every session-dependent rule (`market_data_incomplete`, `market_data_stale`, `evaluation_outdated`) is **suppressed**, never evaluated against an older session. `calendar_out_of_range` alone makes the Market data lane blocking. The verdict may still mention the latest session with data as historical context, for example "calendar ends 13 Mar; even 13 Mar is missing bars for 6 symbols".

Thresholds live in settings, not code. Adding a rule requires adding it to this closed enum and its lane mapping (tested, §14).

> **Approved contract change (2026-10-03, 21-CONTEXT D-03a):** `handover_blocked` is removed. Its trigger ("a handover was requested") has no stored source. Handover availability and its reasons come from the PAPER-02 checks A1–A7 (20.1-12), and a non-flat account is normal trading state, not an issue.

### 5.3 Surfacing

- **Header badge** (every page): count + worst severity; opens the **Attention drawer**, a full ranked list.
- **Lanes** take the worst severity among their rules. A lane is never green while one of its rules is open.
- **Overview**: top 3 issues with actions.
- **Area pages**: each area shows the issues whose lane it owns at the top (Market data shows `market_data_*`, and so on).
- **Stage chips**: a stage shows the issue that makes it `blocked`/`uncertain`/`outdated`.
- **Resolved issues** are not stored (decision 6). Activity shows the events that resolved them, for example "Books vs broker: clean".

---

## 6. Where Jobs live and how users encounter them

Jobs are the execution transport, and operators meet them in four places, in this order of prominence:

1. **As operations in context.** Every action button is an operation in plain words ("Ingest market data", "Run trading session"). After submit, the operation appears as an **attempt** in its stage or area (B.1) and in the **Operations tray** with progress.
2. **As Activity items.** Each finished operation is one Activity item with an **outcome**, not a Job status (§8).
3. **Through "Technical record".** Every attempt, Activity item and issue evidence link opens the Job detail: lifecycle events, logs, progress, payload, retry lineage, linked records. This is the existing job-type-agnostic Job detail UI (Phase 19), unchanged in function.
4. **In System › Technical › Jobs** (decision 7). The existing generic Job list/filters/cancel/retry, kept as the technical explorer. It is the only navigation entry that says "Jobs".

Operation labels (frontend copy, closed mapping from `job_type`):

| job_type | Operation label | Stage/area | Broker effect |
|---|---|---|---|
| `ingest-bars` | Ingest market data | Data / Market data | none (Polygon) |
| `sync-market-sessions` | Sync market calendar | Data / Market data | none |
| `sync-symbol-metadata` | Sync symbol reference data | Market data | none |
| `risk-evaluation` | Evaluate signals & risk | Decide | none (the account-snapshot side effect is bug COR-01, not a designed effect) |
| `paper-session` | Run trading session | Trade | **submits orders** |
| `broker-order-sync` | Sync from broker | Sync | reads broker, updates books |
| `reconciliation` | Check books vs broker | Verify | reads broker, report-only |
| `backtest` | Run backtest | Research | none |

Cancel and retry are offered on the attempt, with consequence copy:
- **Cancel**: `queued_only` types can't be stopped once running; say so before submit.
- **Retry**: the reconcile-first lock is shown as a stage state, not as a 409 after clicking.

The generic `/jobs/new` form disappears from primary UI; each operation's form lives with its owner. The eight forms are preserved (§13).

---

## 7. How Runs are represented or replaced

`StrategyRun` is a storage container with six unrelated meanings. The operator sees each meaning in its own concept; the word "Run" is retired from primary UI.

| `run_type` | Operator concept | Where it appears | Notes |
|---|---|---|---|
| `backtest` | **Backtest** | Research › Backtests | Current run-detail analytics (metrics, equity curve, trades, assumptions) move here intact |
| `risk_evaluation` | **Evaluation** (Decide stage record) | Trading › Session › Decide | Signals + risk decisions table in plain language (B.2); codes in the technical view |
| `paper_execution` | **Submission** (Trade stage record) | Trading › Session › Trade | Orders, order lifecycle, gate outcome |
| `reconciliation` (in-session) | **Pre-trade check** | Session › Trade (gate) and Portfolio › Books vs broker history | Label its source: "inside trading session" |
| `reconciliation` (standalone Job) | **Books check** | Session › Verify and Portfolio › Books vs broker | Findings + account divergence (§10 R5) |
| `operator_control` | **Control change** (decision 8) | Activity; Trading › Permission history | **Never** listed as a trading run or "latest run"; read models translate the storage row |
| `dry_bootstrap` | none | System › Technical › Records only | Legacy |
| _(no run)_ `broker-order-sync` | **Sync** | Session › Sync; Portfolio | Evidenced by the broker_sync snapshot + Job |
| _(separate table)_ ingestion runs | **Ingestion** | Market data; Session › Data | Partial status surfaced (`ingestion_partial`) |

Decision 8: control writes keep creating `operator_control` StrategyRun rows for now. Read models translate them into Control Changes. A storage separation may come later and is **not** a prerequisite for the rebuild.

---

## 8. History / Activity

**One read model** (§10 R6) powers every history surface; each context is a filter.

**ActivityItem**: `{ id, occurred_at, kind, domain, operation?, outcome, session_date?, title_params, actor, reason?, links{job_id?, run_ids[], session?, ingestion_run_id?} }`

- `kind` (closed): `operation`, `control_change`, `books_check`, `session_outcome`.
- `domain` (closed): `data`, `decide`, `trade`, `sync`, `verify`, `research`, `safety`, `system`.
- `outcome` (closed): `succeeded`, `partial`, `no_op`, `blocked`, `failed`, `uncertain`, `cancelled`, `changed`, `unchanged`, `in_progress`.
  - `unchanged` = a control request that re-affirmed the current state. The backend writes audit rows even then, and Activity says so plainly.

Outcome classification examples (backend):
- paper-session SUCCEEDED + `noop_no_candidates` → `no_op`;
- `blocked_*` → `blocked`;
- ingest SUCCEEDED + ingestion run `partial` → `partial`;
- FAILED + `outcome_uncertain` → `uncertain`.

Lenses (same read, different filters):
- Activity page (all, default last 7 days);
- Session page timeline (`session_date`);
- Market data "Recent" (`domain=data`);
- Research "Recent" (`domain=research`);
- Trading › Permission history (`kind=control_change`);
- Overview "Recent" (limit 5).

Control-change items carry the **reason**. Kill-switch items carry what was and wasn't affected (A.3 stop-sheet wording).

The read satisfies AUD-01 in Phase 21 when it covers every Job and every control change. The Activity **page** (AUD-02) ships in the v1.4 Operator Console milestone (decision 2; 03 §3).

---

## 9. Backend vs frontend interpretation

**Principle**: if a meaning is safety-relevant, rule-based, needed by more than one client (desktop + mobile), or must stay consistent with what the engine actually does, the backend computes it as a closed enum + parameters. The frontend owns words, formatting, layout and navigation.

| Interpretation | Owner | Reason |
|---|---|---|
| Trading permission (`allowed`/`blocked` + blocker codes) | **Backend** | Must use the same gate functions and the same persisted field the session uses |
| Posture lane states + reason codes | **Backend** | Safety state; one consistent snapshot |
| Verdict code + next-action code | **Backend** | Precedence list is a tested rule (§3) |
| Issues | **Backend** | Rules, thresholds, evidence joins |
| Stage statuses, session attribution, evaluation session | **Backend** | Cross-record joins; must match engine semantics |
| Operation outcome classification | **Backend** | Reads `result_summary` + ingestion status |
| Data coverage / freshness | **Backend** | DB aggregation |
| Prerequisites and broker effect per operation | **Backend** (job-type catalog) | Must match handler behavior |
| Labels for enums (operation names, risk codes → plain language, stage names) | **Frontend** | Copy |
| Verdict/issue sentences from codes + params | **Frontend** | Copy; localizable |
| Consequence text per operation | **Frontend**, keyed by backend `broker_effect` | Copy tied to a backend fact |
| Relative time, local timezone, number formatting | **Frontend** | Presentation |
| Layout, grouping, drill-down navigation | **Frontend** | Presentation |

**Forbidden in the frontend**: computing a safety state by combining several independently fetched reads. That produces torn snapshots, for example a lane saying "allowed" from one poll and "blocked" from another.

**Precision on trading permission**: the permission read reports the kill switch and strategy status via the session's own gate functions, and the books gate from the **latest persisted** reconciliation's `blocks_execution`, labelled "as of last check hh:mm". It **cannot** predict a session outcome, because every session re-reconciles against live Alpaca state. The UI states this: "Allowed as of last check; the session re-checks the broker before submitting."

---

## 10. Required read models / API capabilities

All are read-only GETs under `/api/v1`, need no mutation path, and every response carries server `as_of` (server time) so staleness is always computable. "Schema impact" is relative to today's database.

| # | Read | Returns (key fields) | Sources | Query bound | Schema impact |
|---|---|---|---|---|---|
| **R1** | `GET /operator/overview?strategy_id=` | `as_of`, `environment{mode, broker, mutations_enabled}`, `trading_permission{state, blockers[], books_checked_at}`, `posture{lane: {state, reason_code, params}}`, `verdict{code, params}`, `next_action{operation, params, reason_code}\|null`, `current_session` (compact R3), `account{…, snapshot_source, snapshot_at}`, `issue_counts{severity: n}`, `recent_activity` (R6, 5) | Extends `OperatorStatusService` (already aggregates kill switch, latest control, snapshot, paper execution, reconciliation, blocking events, failed runs) | Constant: sum of R2+R3(compact)+R6(5), target ≤ 15 queries | none |
| **R2** | `GET /operator/issues?strategy_id=&lane=` | `items[Issue]` (§5.1) | kill switch, strategy status, reconciliation runs, jobs (failed/queued/uncertain), orders, execution events, R4 | Constant per rule, target ≤ 12 queries | none |
| **R3** | `GET /operator/sessions?strategy_id=&limit=` · `GET /operator/sessions/{session_date}?strategy_id=` | `session_date`, `is_current`, `current_session_rule`, `sessions_behind_today`, `stages[{stage, status, reason_code, params, records{risk_run_id, execution_run_id, reconciliation_run_ids[], ingestion_run_ids[], snapshot_id}, attempts[{job_id, job_type, status, outcome, outcome_uncertain, queued_at, completed_at}]}]`, `outcome{code, counts}` | jobs (payload `as_of_session`), runs (`parameters_snapshot`/`result_summary` session), ingestion runs (date range), R4 | **Bounded window**: the last N (default 200) Jobs of the four session types + runs in the same window; session keys parsed in the service. Target ≤ 10 queries per request. | none now. **If** benchmarks exceed bounds: indexed `session_date` column on `jobs`/`strategy_runs` (a migration; conflicts with Phase 21 "no new columns") |
| **R4** | `GET /market-data/coverage?strategy_id=&session_date=` | `calendar{first_session, last_session, covers_today}`, `current_session`, `latest_complete_session`, `sessions_behind`, `symbols[{symbol, last_bar_date, has_session_bar, history_sessions, history_required, status: complete\|missing_bar\|insufficient_history\|no_data}]`, `recent_ingestions[{id, job_id, status, symbols_requested, symbols_failed, bars_upserted, error_message, completed_at}]` | `daily_bars` (index `symbol_id, session_date`), `market_sessions`, `market_data_ingestion_runs`, strategy config (universe, `warmup_periods`) | ≤ 5 queries: grouped max/count over the universe (10 symbols) and warmup window; bounded ingestion list | none |
| **R5** | `GET /reconciliations?strategy_id=&limit=` · `GET /reconciliations/{run_id}` | `run_id`, `source: session_precheck\|standalone`, `job_id`, `session_date`, `completed_at`, `status`, `blocks_execution`, `finding_count`, `blocking_count`, `account_divergence{field: {local, broker}}`, `threshold_breach[]`, `findings[{category, symbol, message, paper_order_id, broker_order_id, details}]`, `snapshot_compared{id, source, snapshot_at}` | reconciliation StrategyRun `result_summary` (already stores findings, divergence, breaches) + ExecutionEvents | List: 1 query; detail: ≤ 3 | none. `snapshot_compared` requires recording which snapshot was compared: it is **not persisted today**; derivable approximately (latest strategy snapshot before `completed_at`) and labelled as such |
| **R6** | `GET /operator/activity?strategy_id=&kind=&domain=&outcome=&session_date=&from=&to=&cursor=&limit=` | `items[ActivityItem]`, `next_cursor` | jobs (+ job events for cancellation/uncertainty), operator_control runs + control ExecutionEvents, ingestion runs (merged into their Job's item), reconciliation runs | Keyset pagination on `(occurred_at, id)`; one bounded query per source (≤ 4) + k-way merge | none (Phase 21-compatible) |
| **R7** | `GET /system/worker-health` | `state: idle\|busy\|unavailable\|unknown` (+ `reason_code`), `workers[{worker_id, hostname, version, started_at, last_seen_at, current_job_id, state}]`, `queue{queued_count, oldest_queued_age_s, running_count}`, `running[{job_id, job_type, heartbeat_at, lease_expires_at, progress}]`, `stalled` | new `worker_heartbeats` rows (approved, decision 4) + jobs table (serialize existing `heartbeat_at`, `lease_owner`, `lease_expires_at`) | ≤ 3 queries | **One new table** (`worker_heartbeats`); the worker writes its own infrastructure state (§10 notes) |
| **R8** | `GET /job-types` (extend) | add `broker_effect: none\|reads_broker\|submits_orders`, `session_scoped: bool`, `prerequisites[{code, job_type, scope}]` (e.g. paper-session requires a succeeded risk-evaluation for the same session; retry after uncertainty requires a clean reconciliation), `retry_prerequisite` | registry specs | 0 queries (static) | none |
| **R9** | `GET /jobs` (extend list items) | add `outcome` (the classification of §8) and `failure_message` (truncated) | jobs | removes today's N+1 detail calls | none |

> **Planning amendments (2026-10-03, see 04 rows 11, 14, 18):** R1 and R2 are account-level under the single-owner scope; their `?strategy_id=` parameter is removed and rejected with HTTP 422 `strategy_filter_not_supported` (documented contract change; research reads R3–R6 keep it). R1 keeps the evaluation-session pipeline summary (the compact R3, named `evaluation_session_pipeline`, never `current_session`) and `recent_activity` (R6, 5 items). All query targets above stand and count the total statements of a request, reused components included (Phase 21 plans, D-08).

**Worker health semantics (R7; decision 4; closed states):**
- `busy`: a fresh heartbeat (within 3× the write interval) reporting a current Job.
- `idle`: a fresh heartbeat with no current Job.
- `unavailable`: the latest heartbeat is stale, or the worker recorded a graceful stop and none is fresh.
- `unknown`: no heartbeat has ever been recorded (for example a deploy without a worker, or before the first poll). It is never rendered as OK.
- **The absence of an active Job is never evidence of health**; only a fresh heartbeat is.
- **Write path:** the run-jobs loop upserts its own row, throttled (for example every 10 s, not every 2 s poll), and writes `stopped` on graceful shutdown. This is the worker recording its **own infrastructure state**, like today's per-Job `heartbeat_at`; it is not an operator mutation path. The writer lives in the `jobs/` infrastructure layer so the ORCH-08 closed-world boundary test is unaffected (to verify).
- **Retention:** worker ids change on restart, so rows accumulate; prune rows not seen for a retention window on write (policy in 03).
- **Public read-only deploy (no worker):** reads `unknown` with `reason_code=no_worker_expected` when a `worker_expected=false` setting is set. Open question in 03: reason code vs a fifth state.
- Worker health stays **out of `/ready`**.

**Also required, derivable today:**
- `environment` block in R1: `mode=paper`, `broker=alpaca`, `mutations_enabled`. This is the header chip.
- `as_of` on every response. This is the "freshness is always computable" invariant.

---

## 11. Mobile information architecture

**Tabs**: **Status** (verdict, four lanes, issues) · **Session** (vertical pipeline stepper, B.3) · **Activity** · **More** (Portfolio, Market data, Research read-only, System read-only).
**Header**: environment chip, attention badge, **Stop** (A.3 sheet).

| Screen | Mobile shape |
|---|---|
| Status | Verdict sentence, four stacked lanes (tap → area), top issues as cards (A.3 left) |
| Session | Stepper; only the blocking/uncertain stage expands by default (B.3) |
| Issue | Full-screen card: what happened · why it matters · recommended action · technical record link |
| Activity | Single-column list, filter chips (domain/outcome) |
| Portfolio | Account + positions + open orders as lists; Books vs broker summary |
| Market data | Coverage list per symbol (status only); ingest history |
| Research | Backtest list + summary metrics (read-only) |
| System | Worker health + Technical › Jobs (read-only) |

**Initial mobile scope (decision 5):** Status, Attention, Activity, Current session, and **emergency / risk-reducing controls only**. No ordinary remote mutations until an authentication and network-access model exists.

**Operations from mobile (policy: asymmetric safety; closed list; tested). The table is the long-term policy; the initial release enables only the first row, and only under the network precondition below:**

| Class | Operations | Mobile |
|---|---|---|
| Risk-reducing / emergency | Trip kill switch (reason required), Disable strategy, Cancel a queued operation | **Initial release**, subject to the network precondition |
| Read / report-only | Check books vs broker, Sync from broker | **Not in the initial release** (ordinary mutations; decision 5). Candidate after auth |
| Data-only (no broker) | Ingest market data (prefilled from an issue only), Sync market calendar, Evaluate signals & risk | **Not in the initial release** (ordinary mutations; decision 5). Candidate after auth |
| Exposure-increasing or judgment-heavy | Run trading session, Re-arm kill switch, Enable strategy, **Change active paper strategy**, Retry an uncertain operation, Run backtest, custom-range ingest | **Desktop only**, permanently. Re-arm is never exposed on mobile |

**Precondition (outside v1.3):**
- The console has no authentication, and the only remote deploy has mutations disabled. Even the emergency controls assume the console is reachable from the phone on a deploy with mutations enabled. **Open question (03):** over which network the initial emergency controls are offered (local network / VPN) or whether they wait for authentication. Until answered, the mobile build ships read-only with the Stop control disabled and explaining why.
- The break-glass `kill-switch-trip` CLI remains the path when the API is down.

---

## 12. Changes to Phase 21 scope (decided: option (b); full plan in 03-PLANNING-CHANGES.md)

> **Superseded by decision 2 (2026-09-30):** option (b) is chosen, and the rebuild is a separate milestone. The analysis below is kept for traceability. The authoritative revised scope, phases, dependencies and conflicts are in [03-PLANNING-CHANGES.md](03-PLANNING-CHANGES.md).

Current Phase 21 goal: one filterable history of Jobs and control changes (AUD-01/02) + a global failure indicator (NOTIF-02) + UX consistency; no new tables or columns. Proposed disposition per success criterion:

| # | Current criterion | Proposal |
|---|---|---|
| 1 | Every Job and control change inspectable (AUD-01) from existing rows | **Keep.** Satisfied by R6 Activity; add sessions/ingestion/reconciliation items without new tables |
| 2 | History endpoint + `/history` page filtered by kind/type/outcome/date (AUD-02) | **Rewrite.** The endpoint becomes R6 (operator vocabulary, closed `outcome`). The page is **Activity**, deferred to the UI phase below. Phase 21 may ship the read + contract tests only |
| 3 | Test: no new tables/columns; every mutating route produces a history entry | **Keep** for Phase 21. R7 Tier 2 (heartbeat table) and any indexed `session_date` column move to a later phase |
| 4 | Global failure indicator on every route (NOTIF-02) | **Rewrite.** Becomes the attention badge fed by R2 Issues: failures **plus** uncertainty, stale data, blocking reconciliation, stalled queue |
| 5 | Shared status badge; Jobs, History, Controls in nav | **Drop the nav clause** (it contradicts the hybrid: Jobs moves to System, History becomes Activity, Controls becomes Trading › Permission). The shared badge moves to the design-system phase |

**Proposed split (decision for the user; milestone boundary left open):**
- **Phase 21 — Operator read models & attention (backend + contract tests, design-independent):** R1, R2, R3, R4, R5, R6, R7 Tier 1, R8, R9. No new tables/columns. AUD-01, AUD-02 (read side) and NOTIF-02 (read side) are satisfied by R6 and R2.
- **Design track (per your sequence):** inspiration → design system → representative screen (Overview or Current session) → iteration.
- **Later UI phase(s) — Console IA rebuild:** header (environment, posture, attention, tray, Stop), Overview, Trading (session pipeline, sessions, permission), Portfolio, Market data, Research, Activity, System; then mobile.
- **Separate decision items:** R7 Tier 2 heartbeat table (+ invariant-1 exemption); the §4.2 loop 4 snapshot defect fix; the mobile auth/exposure decision.

**AUD-02 and NOTIF-02 cannot be completed by reads alone**; both are operator-visible console requirements. Two honest options:
- **(a)** Phase 21 also ships a *minimal, temporary* UI in the current console: an Activity list page on R6 plus a global attention badge on R2. It closes v1.3 on schedule, and both are replaced by the IA rebuild.
- **(b)** Phase 21 ships reads + contract tests only; AUD-02 and NOTIF-02 move to the IA rebuild phase, and v1.3 closes only when that lands (or the requirements are re-targeted).

`REQUIREMENTS.md` and `ROADMAP.md` are not edited by this proposal.

This keeps v1.3's "no new tables" promise intact and lets backend reads land before visual design is chosen. Whether the UI rebuild closes v1.3 or opens its own milestone before Strategy Lab is left to you.

---

## 13. Nothing lost: current console → hybrid

| Current | Hybrid home |
|---|---|
| `/` Health panel | System › Health |
| `/` Readiness panel | System › Health (feeds Operations engine lane only for API/DB) |
| `/` System Info panel | System › Health › Configuration |
| `/` Kill Switch panel | Header lane + Trading › Permission |
| `/` Latest Run panel | Replaced by Overview › Current session + Recent activity (control runs excluded) |
| `KillSwitchBanner` (global) | Header: Trading-permission lane + Stop trading |
| `/strategy` overview (rules, universe, params, config ref) | Research › Strategy |
| `/strategy` Enable/Disable | Trading › Permission |
| `/strategy` Run backtest shortcut | Research › Backtests |
| `/strategy` Evaluate risk shortcut | Trading › Current session › Decide |
| `/runs` list + filters | Activity (all kinds) + Research › Backtests + Trading › Sessions; raw list in System › Technical › Records |
| `/runs/[id]` header (type, status, trigger, session, IDs, created-by Job) | Technical record on the owning concept |
| `/runs/[id]` artifact counts | Technical record |
| `/runs/[id]` Signals & Risk decisions | Session › Decide (plain language + codes in technical view) |
| `/runs/[id]` Orders & Fills | Session › Trade; Portfolio › Orders / Fills |
| `/runs/[id]` Metrics + summary | Research › Backtest detail |
| `/runs/[id]` Equity curve / analytics | Research › Backtest detail |
| `/jobs` list + filters + auto-refresh | System › Technical › Jobs (unchanged function) |
| `/jobs/[id]` header, progress, logs, events, resources, result summary, cancel, retry | Technical record (same detail UI), reachable from every attempt/activity/issue |
| `/jobs/new` backtest form | Research › Run backtest |
| `/jobs/new` ingest-bars form | Market data › Ingest (+ prefilled from issues/stages) |
| `/jobs/new` sync-market-sessions form | Market data › Sync calendar |
| `/jobs/new` sync-symbol-metadata form | Market data › Symbol reference data |
| `/jobs/new` risk-evaluation form | Session › Decide |
| `/jobs/new` paper-session form | Session › Trade |
| `/jobs/new` broker-order-sync form | Session › Sync; Portfolio |
| `/jobs/new` reconciliation form | Session › Verify; Portfolio › Books vs broker |
| `/paper` Account snapshot | Portfolio › Account (+ Overview summary) |
| `/paper` Reconciliation summary | Portfolio › Books vs broker (R5 detail) |
| `/paper` Recent execution findings | Split: control events → Activity; reconciliation findings → Books vs broker; order events → order detail |
| `/paper` Positions / Open orders | Portfolio |
| `/paper` Job shortcuts | Stage actions in Trading; Portfolio actions |
| `/paper` analytics section (strategy paper metrics) | Portfolio › Performance (paper) |
| `/controls` kill switch + strategy controls + confirm dialogs | Trading › Permission (+ Stop sheet everywhere) |
| Per-panel "as of / Refresh" (FetchMeta) | Kept, driven by server `as_of` |

---

## 14. Testable invariants for the implementation phases

1. **Closed mappings**: every `job_type` maps to exactly one operation label, stage/area and `broker_effect`; every `run_type` maps to exactly one operator concept (§7). A new enum value without a mapping fails a test.
2. **Issue closure**: every issue rule has exactly one severity, one lane, one resolution code; lane state = worst open issue in that lane (test enumerates rules).
3. **Permission parity**: the kill-switch and strategy gates in the trading-permission read call the same functions `run_paper_session` uses. The books gate is the latest **persisted** reconciliation's `blocks_execution`, labelled with its time. It is **not** a prediction of the next session, which runs a fresh reconciliation. Tested by fixtures toggling each gate.
4. **Resolved means classified and clean**: `outcome_uncertain_unverified` clears only when every registered intent is classified by broker state **and** a clean, account-wide standalone reconciliation follows the latest broker-touching Job. Tested with:
   - a succeeded-but-blocking reconciliation (15:46:50 must not resolve it);
   - an open order with a clean reconciliation (resolved, but resubmission forbidden);
   - zero registered intents (`nothing_submitted`).
5. **Controls are not runs**: no operator read other than System › Technical › Records returns `operator_control` records as runs or sessions.
6. **History completeness (AUD-01)**: walking the mutating-route allowlist, every mutation produces exactly one Activity item.
7. **Outcome honesty**: a SUCCEEDED Job whose `result_summary` reports no-op, blocked or partial never classifies as `succeeded`.
8. **Freshness**: every operator read includes server `as_of`; every account/reconciliation/coverage value includes its own as-of.
9. **Unknown is not OK**: with no heartbeat ever recorded, worker health is `unknown`; with only stale heartbeats, `unavailable`; a queued-but-idle system with no fresh heartbeat never reads `idle`.
10. **Query bounds**: R1–R7 query counts asserted by tests (Phase 11 style) at the targets in §10.
11. **Mobile policy**: the mobile-allowed operation set equals the closed list in §11.

---

## 15. Backend findings surfaced by this work (now correctness work COR-01..04, see 03)

| Finding | Evidence | Impact |
|---|---|---|
| Risk-evaluation snapshot (`buying_power=cash`) is reconciled against the broker account and blocks execution | 15:46:50 reconciliation: `blocks_execution=true`, 0 findings, `account_divergence.buying_power` 100000 vs 400000; code `portfolio.py:191-201`, `report.py:353-361, 586-592` | Forces Sync between Decide and Trade; hid behind a later clean check in today's console |
| Retry unlock accepts a blocking reconciliation | `job_mutations.py` reconcile-first rule checks only for a newer succeeded reconciliation Job; the 15:46 Job succeeded while blocking | "Verified" in the backend ≠ books agree; the console rule is stricter (§5, invariant 4) |
| Partial ingestion reported as a SUCCEEDED Job | Job 710d46bf: SUCCEEDED, `ingestion_succeeded=false`, `symbols_failed=["QQQ"]` | Needs `outcome=partial` + `ingestion_partial` issue |
| Paper session without a risk run likely lands "outcome uncertain" | *Inferred*: `external_broker_session_started` logged before risk-run lookup | Machine-readable prerequisite (R8); consider moving the log after the lookup |
| Kill switch with nothing to trade reports `noop_*`, not blocked | Gate order in `submit_orders.py` | Permission must come from gates (R1), never from the last session outcome |
| Market calendar ends 2026-03-13 | Local DB: `market_sessions` 2024-12-20 → 2026-03-13; `daily_bars` max 2026-03-13 | Root cause of the scenario: every default session is 13 Mar; nothing in today's console says the calendar stops there |
| Session dates for runs live in JSON | `stale_runs.py`, run `parameters_snapshot` | Session reads rely on bounded windows unless a column is added |

## 16. Corrections to the Paper artboards (quota-blocked; apply when Paper resets)

- **A.1** operations list "15:46 Reconcile · Books agree · 0 findings", **A.1/A.2** lane "Reconciled 15:46, clean", **B.1** Verify stage "Books match broker · 0 findings · 15:46", **B.2** "verified clean at 15:46", **C.1** resolved item and timeline "15:46 Reconciled: books agree":
  - The 15:46 standalone check **blocked** (buying-power divergence).
  - The clean check was **15:47, inside the trading session**, after the 15:46:58 broker sync.
- **C.1** "Ingest has failed 3 times": the 15:48 ingest covered only QQQ/SPY and QQQ failed inside it (see 01 errata).

## 17. Decisions: resolved and remaining

Resolved on 2026-09-30: see §0.1 (current session → refined into trading day / evaluation session / execution window in §4.4, Phase 21 option (b) + separate milestone, snapshot bug, worker heartbeat, mobile scope, issues as projection, System › Technical, control changes).

Settled decisions, recommendations and open questions are kept separately in [03-PLANNING-CHANGES.md](03-PLANNING-CHANGES.md) §0 (0.1 settled · 0.2 recommendations · 0.3 open).
