# Operator Console — Proposed Planning Changes (for review)

_Date: 2026-09-30 · Revision 8 (final planning draft; W-1 declined, End ≠ resolution, consistency pass) · Status: **applied to planning files 2026-09-30 / 2026-10-03 (see §5 note); not executed**. Plans: [04-IMPLEMENTATION-PLANS.md](04-IMPLEMENTATION-PLANS.md) · Interim runbook: [05-INTERIM-API-OPERATIONS.md](05-INTERIM-API-OPERATIONS.md)._

`ROADMAP.md`, `REQUIREMENTS.md`, `PROJECT.md`, code, APIs, schemas and UI are unchanged. Inputs: [01-IA-EXPLORATION.md](01-IA-EXPLORATION.md), [02-HYBRID-IA-PROPOSAL.md](02-HYBRID-IA-PROPOSAL.md).

Work order (settled):

```
Correctness fixes  →  Phase 21 read-model / API foundation  →  complete UI/UX design  →  Operator Console rebuild
   (Phase 20.1)          (Phase 21, v1.3 closes)                (v1.4, Phase 22)          (v1.4, Phases 23–27)
```

Evidence levels:
- **verified in code**: read on 2026-09-30, not executed;
- **verified in records**: local DB or API reads;
- **documented**: Alpaca documentation, accessed 2026-09-30 (§1.6 sources);
- **inferred**: reasoned from the above;
- **unverified**: not established, so a conservative rule applies.

No broker API was called, no order was submitted, and the broker account was not touched.

---

## 0. Decision register

### 0.1 Settled

**Operator Console (round 1):**
- **S1** Market Calendar is the source of truth for the current session; explicit `unknown` when it doesn't cover the day; never an old session as current.
- **S2** Phase 21 option (b): no temporary UI; AUD-02/NOTIF-02 go to a separate Operator Console milestone; Phase 21 = read models/APIs.
- **S3** The snapshot/buying-power mismatch is a correctness bug; no Sync-before-Trade workaround in the UX.
- **S4** Persisted worker heartbeat; states idle / busy / unavailable / unknown; no active Job ≠ healthy.
- **S5** Mobile: monitoring + emergency controls; no ordinary remote mutations before auth/network design.
- **S6** Issues are a projection, not persisted; no acknowledge/snooze.
- **S7** Technical records behind System › Technical.
- **S8** Control changes are Activity events, never Runs; translated in read models, no storage prerequisite.

**Correctness (round 1):**
- **C-A** Fix the snapshot inconsistency at its source.
- **C-B** A `SUCCEEDED` reconciliation Job is not enough to unlock retry; the result must show the books agree.
- **C-C** Partial ingestion is not an unqualified success.
- **C-D** Calendar coverage is a critical data-health condition and must never make 13 Mar current.

**Scope (round 2):**
- **SC-1** Many strategies for research/backtesting.
- **SC-2** At most one active paper strategy.
- **SC-3** Concurrent paper trading is future work.
- **SC-4** Work order as above.

**Attribution / retry / recovery (round 3):**
- **D-1** No ownership by epoch; attribution comes from platform identifiers + local records + an account-wide check; history never hides unresolved activity; no silent adoption.
- **D-2** Retry identity ≠ permission; re-check before any broker action; stop explicitly.
- **D-3** A clean reconciliation is necessary, not sufficient; establish each order's actual broker state.
- **D-4** Unrecognized activity blocks trading, raises an explicit issue, and has an audited recovery path with no dead end.
- **D-5** Handover only when the account is already flat, all broker orders are terminal, nothing is in flight, and no outcome is unresolved; no flatten operation required; disabled ≠ able to exit; exits-only needs its own spec.
- **D-6** Broker sync stays available for recovery: it submits nothing, changes no ownership, and unlocks nothing by itself.
- **D-7** Initial owner only with inventory support.

**Round 4 (this revision):**
- **E-1 (B-1)** A minimal audited "Record external activity" is **in the correctness scope**. It uses **verified broker records**, preserves **external origin**, and **triggers a fresh consistency check**. It never acknowledges or clears a finding by itself. Restrictions: external orders are terminal and net external exposure is zero. Adopting external positions is future work.
- **E-2 (B-2)** Start with **no active paper strategy**. An **account-level inspection/reconciliation path** works without an owner and assigns nothing to any strategy. The operator **explicitly selects** the first strategy after the account checks pass. No automatic trend following.
- **E-3 (B-3)** **COR-06 is in scope.** Verify duplicate-ID and HTTP retry behavior first. Specify **recovery** of ambiguous submissions, not only the removal of re-sends.
- **E-4 (B-4)** Unresolved submission uncertainty **blocks** new submissions. A verified open or partially filled order is **never resubmitted**. A known working order does not **by itself** block all other trading, but further submissions must account for filled exposure, open-order commitments and current risk limits. **If that accounting isn't reliable, block with an explicit reason.** Resolving uncertainty is separate from waiting for an order to become terminal. Handover still needs a flat account, terminal orders and no unresolved outcomes.
- **E-5** Evidence standards:
  - zero local records alone doesn't prove non-submission;
  - "not found" requires defined lookup coverage;
  - an ID format alone isn't ownership evidence;
  - the ledger stays a recommendation until justified.
- **E-6** V-1..V-4 are completed by this research (§1), with conservative rules wherever behavior is unverified.

**Round 5 (this revision):**
- **F-1** Evidence of absence (repeated not-found lookups, grace period, full list scan, no fills, unchanged positions) **does not prove non-acceptance** while broker visibility delay is undocumented. **It never authorizes resubmission after an ambiguous submission.** The outcome stays unresolved unless additional evidence establishes safety. There is a documented escalation path. No invented visibility guarantee, and operator acknowledgment is never proof of non-submission.
- **F-2** "Connection refused"–class failures count as **not sent only if the evidence covers the whole submission attempt**. A final connection failure can't erase an earlier ambiguous send or automatic re-send.
- **F-3 (P-1)** **Defer open-order-aware risk accounting.** Block further submissions while known working orders can't be reliably included in risk checks, with an explicit reason and recovery action. No exception within one operation unless combined risk is demonstrably accounted for.
- **F-4 (O-4, O-1, O-2)**
  - Paper trading that purports to execute historically is **rejected**; research, backtesting and evaluation of past sessions stay supported.
  - Current execution using the previous completed session's data is valid.
  - The data/evaluation session is distinct from execution time.
  - Calendar sync ahead of today is **allowed**, with a configurable coverage horizon.
  - Trading day, data-ready evaluation session and execution window are **three separate facts**, each keeping the explicit unknown/calendar-unavailable state.
- **F-5 (O-8)** The worker heartbeat stays in **Phase 21**. The **Operator Console milestone comes next, before Strategy Lab**.

**Round 6 (this revision; revision 5 accepted as the basis for detailed planning):**
- **G-1 Temporary limitation accepted.** A multi-order session pauses whenever a submitted order's effects can't yet be accounted for, including an immediately filled order awaiting sync. An explicit **Continue session** (sync + reconciliation + permission checks) is acceptable initially, even if it takes **repeated operator intervention within one session**. Open-order-aware risk accounting and improved continuation are **future work, not prerequisites**.
- **G-2 Current execution policy.** Regular-hours execution using the immediately preceding completed session's data is the **initial supported policy**, not a permanent platform invariant; future strategies may declare other policies. Trading day, evaluation session and execution window stay distinct.
- **G-3 Data provenance.** Execution must confirm that the evaluation used the **currently valid data version** (or equivalent provenance), not merely that it is newer than the data. Corrected or re-ingested data makes an evaluation stale when the data it read changed. Use the smallest reliable mechanism in the existing architecture.
- **G-4 Pause ≠ stop.** A failed permission check doesn't expire all remaining orders. Define:
  - which conditions **pause** (unsent orders preserved);
  - which **terminate** (unsent orders expired or cancelled);
  - which **require re-evaluation** (no silent re-plan, no changed identities).

  Temporary conditions pause. Window expiry and explicit cancellation terminate. The next action is always explicit.

**Round 7 (final planning adjustments):**
- **H-0 (D-1 = option a)** New controls are operated **through the HTTP API until the v1.4 console**. No temporary control screen; v1.4 Trading is not pulled forward.
  - The **existing console must stay truthful**: it never presents a paused operation as a success and never offers unsupported actions.
  - Smallest compatibility adjustments only; if those aren't enough, disable paper-trading initiation in the old console and state the limitation.
  - Job lifecycle status stays distinct from the execution-operation outcome.
- **H-1 (TL-4 corrected)** An ambiguous submission is **resolved when the broker order is found and its state verified**. Broker confirmation of non-receipt applies **only on the missing-order path when resubmission is being considered**; it isn't required for every ambiguous outcome. *(Superseded 2026-10-04, round 5: a broker statement of non-receipt is audited evidence only; it neither resolves the intent nor authorizes resubmission, and no request is resent while the original may still produce an execution. See 04 "Safety correction, round 5".)*
- **H-2** **Source-data provenance is separate from current portfolio risk.** Expected portfolio changes from earlier orders in the same operation don't invalidate the evaluation, but do require fresh risk checks for the remaining original orders. Corrected source data or changed strategy settings may invalidate it. Continue never re-plans, never changes identities, and never needs a new evaluation just because an earlier order filled.
- **H-3** **Symbol-metadata outcomes approved:**
  - `complete | partial | failed`, with per-symbol failure details;
  - missing required metadata keeps the symbol from being presented as ready for trading;
  - symbol-level failures are defined separately from operation-level ones.

**Round 8 (final):**
- **J-1 (W-1 declined)** Withdrawing a never-found order must **not** clear submission uncertainty or lift the trading block on elapsed time + absence evidence. A never-found ambiguous order stays unresolved (blocking new submissions for the strategy) until the order is found and verified~~, or a written broker statement of non-receipt is recorded~~. *(Statement clause superseded 2026-10-04 by 04 "Safety correction, round 5" item 1: a recorded non-receipt statement is audited evidence only and neither resolves the order nor authorizes a send; the order resolves only when the broker shows it (found and verified) or its own attempt history proves it was never sent.)*
- **J-2** **Ending the local execution operation ≠ resolving broker uncertainty.**
  - *End operation*: the operator abandons continuation; remaining **unsent** intents are terminated.
  - *Uncertainty*: submission uncertainty is tracked independently of the operation and keeps blocking new submissions until resolved.
  - Ending or expiring an operation never erases recovery records, never implies that submitted orders were cancelled at the broker, and never marks the account safe. Recovery stays available after the operation ends.
- **J-3** **Owner-less account checks get dedicated storage**, as a documented architectural recommendation (R-31), included in the schema inventory with its invariant impact assessed (§3.11). Account-level checks are never attached to an arbitrary strategy to satisfy current storage constraints.

### 0.2 Recommendations (proposed; approval turns them into decisions)

| # | Recommendation | § |
|---|---|---|
| R-1 | Active-paper-strategy singleton, distinct from registered / research-available / enabled | 3.1 |
| R-3 | Evidence-based attribution with **three** order classes (owned / unrecognized / recorded external). No hash-inferred "platform-submitted but missing locally" class | 3.4 |
| R-4 | COR-01: evaluation writes no account snapshot; baseline = latest broker-observed snapshot; sizing uses cash | 3.9 |
| R-5 | Only a standalone (account-level or strategy-level) reconciliation counts as the clean check for recovery | 3.7 |
| R-6 | A retry resumes the original intents and ids; `create_new_version` is forbidden on retry | 3.7 |
| R-7 | Pre-broker-action permission re-check with closed stop reasons | 3.7 |
| R-8 | New strategies created disabled | 3.1 |
| R-9 | Risk evaluation allowed for any registered strategy (research) | 3.1 |
| R-11 | Gate every `paper-session` submission (fresh or retry) on the recovery predicate | 3.7 |
| R-13 | _(settled by F-4)_ Calendar sync ahead of today | 3.10 |
| R-16 | **External-activity record table** (smallest viable), not a full attribution ledger. The ledger stays a later scaling option | 4 |
| R-17 | Complete broker-status mapping, including `replaced` and `done_for_day` | 1.3, 3.6 |
| R-18 | Record-and-check run as **one Job** (recording + fresh account-level reconciliation in the same handler) | 3.5 |
| R-19 | _(settled by F-3: deferred)_ Commitment-aware risk accounting | 3.7 |
| R-20 | Execution window default = regular hours of the session after the evaluation session, from open until a configurable cutoff (e.g. 15 min) before close. No pre-open or after-hours submission, so market/day orders don't wait overnight as working orders | 3.10 |
| R-21 | Calendar coverage horizon: configurable, default **≥ 60 trading sessions ahead**, bounded by the calendar library; `calendar_runway_low` below a threshold (default 10 sessions) | 3.10 |
| R-22 | **Durable per-attempt log** for every HTTP attempt of an order POST (attempt number, start, exception class or status), written before and after each attempt | 3.6 |
| R-23 | Escalation path for unresolved ambiguous submissions: a broker inquiry with an evidence package, and recording of the broker's written response as external evidence | 3.7 |
| R-24 | _(accepted by G-1)_ Within one operation: pause at the first order whose effects aren't accounted; resume only through an explicit continuation with fresh checks | 3.7 |
| R-25 | **Evaluation input manifest**: the risk evaluation records a canonical content digest of every bar it read (per symbol: session range, count, SHA-256 over canonical OHLCV/adjusted/provider values) plus a digest of the strategy settings, in its run's `result_summary` (existing JSON, **no schema change**). Execution recomputes it through the same accessor and compares | 3.10 |
| R-26 | **Execution operation state machine** (closed): `running`, `paused`, `requires_reevaluation`, `completed`, `terminated`, with closed reasons and one next action each; at most one open operation per strategy | 3.7 |
| R-28 | ~~Unseen-order withdrawal~~ — **declined (J-1)**. A never-found ambiguous order stays unresolved until found and verified~~, or a broker statement of non-receipt is recorded~~ *(statement clause superseded 2026-10-04 by 04 "Safety correction, round 5" item 1: the statement is audited evidence only; resolution needs the broker showing the order or attempt-history proof of non-sending)* | 3.7 |
| R-29 | **Required symbol metadata** for trading readiness: `metadata_provider` set (synced at least once), `active = true`, `market = 'stocks'`, `symbol_type ∈ {CS, ETF}`, `primary_exchange` present. A symbol failing this is `not_ready(missing_metadata)`; its candidates are rejected with a new closed risk code `symbol_not_ready` | 3.10 |
| R-30 | Legacy-console compatibility: remove paper-session initiation and Retry; show a distinct **Outcome** next to the Job status; show a read-only active-strategy line; point Paper-page reconciliation/sync shortcuts at the account scope | 04 P20.1-14 |
| R-31 | **Dedicated owner-less account-check storage** (`account_reconciliation_runs`): an architectural recommendation (J-3); impact assessed in §3.11 | 3.2, 3.11 |
| R-27 | **Lazy termination**: no scheduler exists, so an elapsed window is applied when the operation is next touched (continue, end, new session request, handover check). Reads project it immediately as "window elapsed: will end" | 3.7 |

### 0.3 Unresolved product decisions (highlighted)

**No open product decision blocks planning.** W-1 was declined (J-1). *(2026-10-04: PD-1 — `price_moved_beyond_tolerance` pauses rather than requiring re-evaluation — was open in round 6 and was approved the same day; no open product decision remains. See 04 "Planning correction, round 6".)*

P-1, O-1, O-2, O-4 and O-8 are settled by round 5 (F-3, F-4, F-5). Remaining, non-blocking:

| # | Decision | Needed before |
|---|---|---|
| O-7 | Mobile emergency controls: which, and over what network | v1.4 Phase 27 |
| O-12 | Exits-only wind-down mode (separate spec) | future |

**Planning defaults, not decisions:** grace periods, lookup repeats, cutoff minutes (R-20), coverage horizon (R-21), thresholds. They're set in planning as tested, configurable constants.

---

## 1. Verification results (V-1 … V-4, HTTP client, status mapping)

### 1.1 V-1: duplicate `client_order_id` behavior

| Finding | Level |
|---|---|
| The order API marks `client_order_id` as a unique identifier (≤ 128 chars). **No duplicate-handling behavior is documented** on the endpoint; documented errors are 403 (buying power) and 422 (input not recognized) | documented |
| Alpaca's error guide says a 422 with message **"client_order_id must be unique"** (code `40010001`) "likely means a duplicate client_order_id was used for another **active** order" | documented (guide) |
| `40010001` is **shared** with other validation errors, including the page-size error this platform hit on 29 Sep | documented + verified in records |
| Whether uniqueness holds against **terminal** orders (filled, canceled, expired) is **not documented** | **unverified** |

**Conservative rules:**
- Re-sending with the identical id is **never** treated as the dedupe mechanism.
- A duplicate reply is recognized by **message**, not code, and proves only that an active order with that id exists. The next step is always a lookup, never an inference.
- Market day orders can fill in seconds and then become terminal, so an identical-id re-send could create a **new** order.

### 1.2 V-2: lookup coverage and visibility

| Finding | Level |
|---|---|
| `GET /v2/orders:by_client_order_id?client_order_id=` returns the order. **The not-found response is not documented** | documented |
| `GET /v2/orders`: `status` (open/closed/all; default open), `limit` (default 50, max 500), `after`/`until` (submission time, exclusive), `direction`, `nested`, `symbols`, `side`, `asset_class`, **`before_order_id` / `after_order_id`** (cursor) | documented |
| The platform client pages `status=all` with `before_order_id` at limit 500, raising at a page cap. It does **not** set `after`/`until` (combining them with the order-id cursor is avoided) | verified in code |
| **No visibility-delay or consistency guarantee** is documented for a newly submitted order appearing in either lookup | **unverified** |
| Whether `GET /v2/orders` with `status=all` and no `after` has an implicit time window is **not documented** | **unverified** |

**Conservative rule:** see "absent" evidence in §3.7 B. Anything short of it is **unresolved**.

### 1.3 Order-status mapping (documented statuses vs platform)

Documented statuses: `new`, `partially_filled`, `filled`, `done_for_day`, `canceled`, `expired`, `replaced`, `pending_cancel`, `pending_replace`, `accepted`, `pending_new`, `accepted_for_bidding`, `stopped`, `rejected`, `suspended`, `calculated`. Documented terminal ("no further updates"): `filled`, `canceled`, `expired`. `replaced` = "replaced by another order".

| Broker status | Platform today (`services/alpaca.py:28-40, 113-128`) | Proposed recovery meaning |
|---|---|---|
| new, accepted, pending_new, accepted_for_bidding, calculated, held, stopped, suspended, pending_cancel, pending_replace | `pending` | **working** |
| partially_filled | `partially_filled` | **working** (fills recorded) |
| done_for_day | → `unknown` (not mapped) | **working** (no updates until next session) |
| filled | `filled` | **terminal** |
| canceled, expired | `canceled` / `expired` | **terminal** |
| rejected | `rejected` | **terminal** (no order created) |
| replaced | → `pending` (**mis-mapped**) | **terminal for this id, with a successor order**. The platform never replaces orders, so a replacement is **external activity** (§3.5) |
| any other or missing | `unknown` | **unresolved** with reason `unmapped_broker_status` |

The mapping fix (R-17) is part of COR-06/COR-02.

### 1.4 V-3: platform `client_order_id` as ownership evidence

- The id is `prefix-YYYYMMDD-symbol8-hash18`, where the hash is over (strategy, session, symbol, side, quantity); the prefix comes from settings (`idempotency.py:47-95`; verified in code).
- Recomputing it proves only that an id **has the platform format and inputs**. Anyone with the same code and keys can produce it: another environment using the same Alpaca paper keys, a restored or replaced database, a test harness.
- It is **not ownership evidence** (E-5).
- Because every submission is preceded by a durable local registration (verified in code), a platform-format order **missing locally** can only come from data loss or another environment. It is classified **unrecognized** with origin tag `platform_format_unverified` (§3.4).

### 1.5 V-4: fills carry their order

- `FILL` account activities include `order_id`, `qty`, `cum_qty`, `leaves_qty`, `price`, `side`, `symbol`, `transaction_time`, `id`. Pagination uses `page_token` + `page_size` (max 100) (documented).
- The platform client pages at 100 with a page cap (verified in code, commit `2352df5`).
- Fills are therefore attributable through `order_id` → order classification.

### 1.6 HTTP client retry behavior (COR-06 input)

- `_request_with_retry` retries **every method**, including `POST /v2/orders`, on `httpx.TransportError` (which includes connect errors and read errors), `httpx.TimeoutException` (connect, read, write and pool timeouts) and HTTP 429/500/502/503/504, with backoff up to `max_retries` (`services/alpaca.py:416-470`; verified in code; unchanged in the version that ran on 29 Sep).
- httpx documents its exceptions only descriptively ("Failed to establish a connection", "Timed out while sending data to the host", …). **No byte-level guarantee is documented** for whether request bytes were sent. Classifying connection-establishment failures (`ConnectError`, `ConnectTimeout`, `PoolTimeout`) as "not sent" is therefore **inferred** from their semantics, and applies only per F-2.
- An order POST that reached Alpaca but timed out on the response is **re-sent**. Per §1.1 that is either rejected as a duplicate (if the first order is still active) or could create a new order (if it already went terminal; unverified).

**Sources** (accessed 2026-09-30):
- Create an Order: https://docs.alpaca.markets/reference/postorder
- Get Order by Client Order ID: https://docs.alpaca.markets/reference/getorderbyclientorderid
- Get All Orders (parameters, `before_order_id`): https://docs.alpaca.markets/us/reference/getallorders-1
- Placing Orders (statuses, terminal states, day orders, client order id): https://docs.alpaca.markets/us/docs/orders-at-alpaca
- Working with /orders (Using Client Order IDs): https://docs.alpaca.markets/us/docs/working-with-orders
- Account Activities (FILL fields, `page_token`, `page_size` max 100): https://docs.alpaca.markets/docs/account-activities and https://docs.alpaca.markets/reference/getaccountactivities
- How to Fix Common Trading API Errors (duplicate `client_order_id` for another active order, 422/40010001): https://alpaca.markets/learn/how-to-fix-common-trading-api-errors-at-alpaca
- httpx exceptions: https://www.python-httpx.org/exceptions/
- Non-authoritative corroboration only: public GitHub issues describing SDK re-POST on 504 leading to duplicate-id replies (e.g. https://github.com/tim1016/learn-ai/issues/2304).

---

## 2. Evidence about the 29 Sep uncertain failures

Jobs: paper-session `52468936` (09:23), paper-session `45b09f0d` (13:22), broker-order-sync `302020fc` (13:22). All three failed with `AlpacaClientError … 422 … "tried to set the page size to 500, but the maximum is 100"`.

| # | Evidence line | Level |
|---|---|---|
| 1 | **Error signature = a read.** `page_size` is used only by `GET /v2/account/activities/FILL`. In the client version that ran (`alpaca.py` at `d5579f8`, before the 14:45 fix `2352df5`), `list_fills` sent `page_size=500`; orders use `limit` | verified in code + records |
| 2 | **Call order.** In `run_paper_session`, `load_broker_state` (orders → fills → positions → account) runs **before** the in-session reconciliation run is created and before `run_paper_order_submission`. `submit_orders.py` was unchanged since 2026-09-28 (`31007e9`), so this is the code that ran. broker-order-sync never submits | verified in code |
| 3 | **Nothing downstream was created.** 0 `paper_execution` runs, 0 `order_events`, 0 `paper_orders`, and no reconciliation run from these jobs (the only two are from 15:46 and 15:47). Each job has a single log line (`external_broker_session_started` / `external_broker_sync_started`) and no submission log | verified in records |
| 4 | **Broker lists were empty afterwards.** The 15:47:16 in-session reconciliation, using the paginated client, found 0 findings with 0 local orders and fills, so the broker order and fill lists it read were empty | inferred. Caveats: coverage of `status=all` with no `after` isn't documented (§1.2), and that client's page cap applies |

**Conclusion:** lines 1–3 establish from the execution path and records that the failures occurred **before any submission step**; line 4 corroborates from the broker side. Classification: **`nothing_submitted` (strongly supported, not broker-confirmed per order)**.

The outcomes remain **unresolved** until a fresh, clean account-level reconciliation runs (§3.7 B step 4). Broker activity after 15:47 on 29 Sep is unknown.

**Inventory limitation:** the local inventory covered this project's running database (Homebrew Postgres on :5432). The other Postgres listening locally (:54322) belongs to a different project. The compose database for this project wasn't running. Any environment using the same Alpaca paper keys (another checkout, a restored database) could create activity this database doesn't know. The account-level check (§3.2) exists to surface that.

---

## 3. Specifications (Phase 20.1, all **draft**)

### 3.1 PAPER-01 — Single active paper strategy

_Status: draft._

- **Four facts:** registered / available for research / enabled / **active paper strategy** (zero or one; see 02 §0.2).
- **Draft requirement:**
  - *A persisted singleton records the active paper strategy (`strategy_id`, `since`, set by control, reason). Two owners are unrepresentable.*
  - *`paper-session` and strategy-scoped `reconciliation` for a non-owner are rejected at submit (`strategy_not_active_paper_strategy`), and ownership is re-checked before each broker action (§3.7 C).*
  - *With no owner, trading permission is `blocked` / `no_active_paper_strategy`.*
- **Initial state (E-2):** **no owner.** The operator explicitly selects the first strategy through the seeding control (§3.3). There is no automatic selection.
- **Ownership periods** are audit context and a consistency check only: the owning strategy comes from the local record (`PaperOrder` → run → strategy); an owned order outside its owner's period is a blocking anomaly.
- **Urgency:** all four strategies are enabled in the DB today (verified in records). R-8 (create disabled) is recommended alongside.

### 3.2 ACCT-01 — Account-level inspection and reconciliation (no owner)

_Status: draft; settled by E-2._

- **Account-level broker sync:**
  - updates **only** local orders that already exist (by `client_order_id`/`broker_order_id`) and ingests fills **only** for those orders;
  - records a broker-observed **account** snapshot (not attributed to a strategy);
  - never creates positions, orders or attributions.
- **Account-level reconciliation** compares all broker orders, fills and positions with:
  - local orders and fills of **every** strategy;
  - recorded external activity (§3.5).

  It produces findings plus **unexplained exposure** per symbol (§3.4). It is report-only.
- **Always available**: with or without an owner, and while trading is blocked (D-6 conditions: submits nothing, changes no ownership, unlocks nothing by itself; only its **result** feeds gates).
- It is the path for seeding, for handover-to-none and back, and for resolving a former owner's uncertainty.
- **Storage (R-31, J-3):** results go to a dedicated `account_reconciliation_runs` record, never to a `strategy_runs` row borrowed from an arbitrary strategy (`strategy_runs.strategy_id` is NOT NULL). Account snapshots use the existing nullable `account_snapshots.strategy_id` (verified).

### 3.3 PAPER-02 — Seeding and handover

_Status: draft._

**Account checks** (identical for seeding and handover; persisted evidence only, so the control stays synchronous and worker-independent):

| # | Check |
|---|---|
| A1 | No `paper-session`, broker sync or reconciliation Job queued or running (any strategy or account level) |
| A2 | **Every broker order is terminal**, per the latest account-level reconciliation using the §1.3 mapping |
| A3 | **Account flat**: 0 positions in the latest broker-observed snapshot, and 0 unexplained exposure |
| A4 | **No unrecognized items** (every broker order and fill is owned or recorded external) |
| A5 | **No unresolved uncertain outcome for any strategy.** This includes the three 29 Sep failures of `trend_following_daily`, which need the fresh account-level reconciliation |
| A6 | A **fresh, clean account-level reconciliation** completed after the latest broker-touching Job and after any recording (§3.5) |
| A7 | Handover only: the outgoing owner is **disabled** |

- **Seeding** = checks A1–A6, then the operator explicitly selects the strategy. It starts **disabled**, so enabling it is a separate act.
- **Handover** = A1–A7, then a Control Change `active_paper_strategy_changed`; the new owner starts disabled.
- **Not flat → unavailable** (D-5). A disabled strategy can't exit on its own, because the disabled gate stops the whole session, exits included.
- **Today:** the seeding checks are **not known to pass**. Broker state after 29 Sep 15:47 is unknown, and the 29 Sep outcomes are unresolved until A6 runs.

### 3.4 COR-05 — Evidence-based attribution and the account-wide check

_Status: draft._

**Order classes (R-3) and required evidence:**

| Class | Required evidence (all) | Effect |
|---|---|---|
| **owned** (strategy X) | (1) a local `PaperOrder` registered **before** submission (durable `PENDING_SUBMISSION`/`RETRY_REQUESTED`), belonging to a run of strategy X; (2) broker `client_order_id` equals the local one, or the local `broker_order_id` equals the broker id; (3) symbol, side, quantity and order type match; (4) broker `created_at` ≥ local registration time; (5) X was the active paper strategy at registration | explained. A mismatch in (3)–(5) is an **anomaly** and blocks |
| **recorded external** | a record created by the audited external-activity recording (§3.5), holding a verified broker snapshot of the order and its fills that **still matches** the broker on every check | explained. Never attributed to a strategy |
| **unrecognized** | anything else, with an origin tag: `platform_format_unverified` (platform id format but no local record), `external_format`, `status_unmapped`, `replaced_by_successor` | **blocks** new trading. Issue `unrecognized_broker_activity` |

- **Fills** inherit their order's class via `order_id` (V-4). A fill whose order is absent from the broker order list is unrecognized.
- **Exposure:** per symbol, explained quantity = net filled quantity of owned + recorded-external orders. **Unexplained exposure** = broker position quantity − explained quantity; if non-zero it blocks.
- **Rules:**
  - no hiding by history: every unrecognized item blocks wherever it is;
  - no adoption: broker sync never creates positions (§3.2);
  - positions are derived from owned fills and compared with the broker.
- **Why "platform-submitted but missing locally" isn't a class (E-5):** the id format isn't evidence of *this* deployment's submission (§1.4). Such orders go the external path. Attributing them to a strategy would be adoption, which is future work (intervention control).
- **Cost:** every check re-reads the full broker history (paged, with a page cap that raises). This is acceptable at current volume (0 orders); see §4 for when a ledger/watermark becomes necessary. The page-cap failure mode is surfaced as `unresolved: broker_history_exceeds_cap`, never as silent truncation.

### 3.5 EXT-01 — Record external activity (minimal audited recovery)

_Status: draft; settled in scope by E-1._

An operation, implemented as a **Job** because it performs broker reads, with a required reason:

1. **Input:** the unrecognized broker order ids to record (as listed by the latest account-level reconciliation).
2. **Verify at the broker** (reads only):
   - fetch each order (by id / `client_order_id`) and its fills (activities by `order_id`);
   - each order must be **terminal** (§1.3; a `replaced` order requires its successor to be included and terminal);
   - the **net external exposure** of all recorded plus to-be-recorded external activity must be **zero in every symbol**.
   - Otherwise stop with a closed reason: `external_order_not_terminal`, `external_exposure_nonzero`, `broker_record_unavailable`.
3. **Record** each order with its verified broker snapshot (ids, symbol, side, qty, filled qty, avg price, status, timestamps, fills) and a content hash, with origin preserved (`external_format`, `platform_format_unverified`, `replaced_by_successor`), the reason, and the Job id. **No strategy is assigned; no position is created.**
4. **Run a fresh account-level reconciliation in the same Job** (R-18). HTTP job submission can't set dependencies today, so the check isn't a separate dependent Job. The Job's outcome **is** that reconciliation's result.
5. **Recording doesn't lift anything.** Blocks lift only because the fresh reconciliation finds every item explained. If the broker data later diverges from a recorded snapshot, the item becomes unrecognized again and blocks.

**Not supported in this scope:** non-terminal external orders or non-zero external exposure. The operator must first make the account consistent **at the broker**; those broker actions are external activity too and are recorded the same way. Adopting external positions into a strategy is future work (intervention control).

### 3.6 COR-06 — Ambiguous order submissions: no automatic re-send, explicit recovery

_Status: draft; settled in scope by E-3. Verification done (§1.1, §1.6); duplicate behavior against terminal orders is unverified, hence the conservative rules._

**Per-attempt evidence (R-22).** Every HTTP attempt of an order POST is logged durably: attempt number, start time, and outcome (exception class or HTTP status), written before and after the attempt. The submission's class is computed over **all** attempts, not the last one (F-2).

**Attempt outcome classes (closed):**

| Attempt outcome | Class |
|---|---|
| `ConnectError`, `ConnectTimeout`, `PoolTimeout` | `pre_connection` (no connection established; *inferred* not sent) |
| the send path's wall-clock check refuses to hand the request to the HTTP client because its authorization deadline has passed (20.1-15 only) | `deadline_expired` (nothing handed to the client; established not sent). *(Added 2026-10-04: part of the closed attempt-class set and the migration-0023 CHECK from the start, plan 20.1-02.)* |
| `WriteTimeout`, `WriteError`, `ReadTimeout`, `ReadError`, `RemoteProtocolError`, any other transport error, HTTP 5xx, **HTTP 429** (conservative), an interrupted process with no recorded outcome | `ambiguous` |
| HTTP 422 with message "client_order_id must be unique" | `duplicate_reported` |
| Other 4xx | `rejected` |
| 2xx | `accepted` |

**Submission class (closed), from the whole attempt sequence:**

| Submission class | Condition | Action |
|---|---|---|
| `not_sent` | **every** attempt is `pre_connection` or `deadline_expired`, and the log is complete (no attempt without a recorded outcome) | the intent may be attempted again with the **same** id, after the permission check. Bounded automatic retry is allowed **only** while every attempt so far is established not sent |
| `ambiguous` | **any** attempt is `ambiguous`, or the log is incomplete | **no further attempt** (automatic or manual); intent → `UNKNOWN`; Job outcome uncertain; §3.7 B. A later `pre_connection`, `deadline_expired` or refused attempt **never** downgrades it |
| `exists_reported` | any attempt is `duplicate_reported` | lookup; record the broker state; never inferred |
| `rejected` | final `rejected` with all earlier attempts `pre_connection` or `deadline_expired` | no order created; intent ends `rejected` with the reason (terminal; `submission_failed` is retryable and is not used for a definitive rejection; aligned with 20.1-CONTEXT D-12 "Other 4xx → rejected" and plan 20.1-02, 2026-10-03); a new order is a new decision |
| `accepted` | a 2xx | record `broker_order_id` and status |

- GET lookups keep automatic retries. The status-mapping fix (R-17) ships with COR-06.
- **Tests:**
  - read timeout after send → one POST, `ambiguous`;
  - connect error ×2 then success → `accepted`;
  - read timeout then connect error → still `ambiguous`, no third attempt;
  - crash between attempt start and outcome → `ambiguous`;
  - duplicate message → lookup.

### 3.7 COR-02 — Uncertain-outcome recovery, working orders, retry identity and permission

_Status: draft._

**A. Two different conditions (E-4, F-3).**
- **Unresolved submission uncertainty** (some intent's broker state isn't established) blocks new submissions for the strategy: reason `outcome_unresolved`, recovery action "Sync from broker, then Check books vs broker; escalate if it persists" (B).
- **Known working order** (established as open, accepted, partially filled or done-for-day):
  - it is **never resubmitted**;
  - because open-order commitments are **not** included in risk checks (deferred, F-3), **further submissions for the strategy are blocked** with reason `working_order_commitments_unaccounted` and recovery action "Wait for the order to finish; then Sync from broker and Continue";
  - this block is **not** uncertainty. It clears when every working order is terminal, its fills are ingested, and the continuation checks (A2) pass.

**A2. Multiple orders within one operation (R-24, conservative, no same-operation exception).**
- **Sequence.** A paper session submits its plan's intents **one at a time** in plan order (already sequential in code: register → submit per candidate, `submit_orders.py:317-572`).
- **Stop at the first non-terminal order.** Immediately after each broker response:
  - `accepted` with a non-terminal status (working): the operation **pauses**. It submits nothing further; the operation becomes `paused` with reason `working_order_commitments_unaccounted` (R-26). Remaining intents are **not** registered or sent.
  - `ambiguous` / `exists_reported`: the operation **stops** with `outcome_unresolved`.
  - `rejected` or `accepted`-and-already-terminal (e.g. immediately filled): continuing to the next intent is allowed **only** after the per-intent permission check (C) against a portfolio that includes that order's result. Until fills are ingested within the operation (not today), an immediately filled order also pauses. Consistent rule: **any broker-touching result whose exposure effect isn't yet in local accounting pauses the operation.**
- **Intent states within the operation (closed):**

| State | Meaning |
|---|---|
| `planned` | in the pinned plan, never registered, never attempted |
| `registered_unsent` | durable `PENDING_SUBMISSION`, **0 attempts logged** |
| `not_sent` | attempts logged, all `pre_connection` or `deadline_expired` with complete outcomes (COR-06) |
| `submitted` | broker `accepted` (working or terminal known) |
| `ambiguous` | COR-06 `ambiguous` → `UNKNOWN` |
| `rejected` | broker rejected |

  Only `planned`, `registered_unsent` and `not_sent` (proven not sent over the whole attempt history) may ever be sent. *(2026-10-04: `broker_confirmed_not_received` removed; an in-doubt intent is never sent.)*
- **Execution operation (R-26).** One logical operation per (strategy, evaluation session, pinned risk run) spans several Jobs: the first submission plus each continuation. At most one operation per strategy may be open (`running`, `paused` or `requires_reevaluation`). A new paper-session request for that strategy is rejected with `operation_open` and a link to the open operation.

**Operation states and reasons (closed; G-4):**

| State | Entered when (closed reasons) | Unsent intents | Operator next action |
|---|---|---|---|
| `paused` (temporary) | `working_order_commitments_unaccounted` (a submitted order not terminal, or terminal but fills not yet synced) · `awaiting_reconciliation` (no fresh clean standalone check since the last broker effect) · `kill_switch_tripped` · `strategy_disabled` · `reconciliation_blocking` · `unrecognized_broker_activity` · `outcome_unresolved` (any strategy intent unresolved) · `broker_unavailable` (lookups failing) · `execution_window_not_open` (before the open on the same trading day) | **preserved** (`planned`, `registered_unsent`, `not_sent`) | Resolve the named condition (sync, reconcile, reset the kill switch, enable the strategy, record external activity…), then **Continue session**. Or **End operation** |
| `requires_reevaluation` | `evaluation_data_changed` (input manifest mismatch, G-3) · `risk_limit_failed:<code>` for the next pinned intent against the current portfolio · `strategy_settings_changed` | **preserved but frozen**: never sent under this operation | **End operation**, then Evaluate again and run a new session on the new evaluation. There is no automatic re-plan; original identities are never changed |
| `terminated` | `execution_window_elapsed` (R-27) · `cancelled_by_operator` (End operation) · `evaluation_superseded` (a new trading day makes the pinned evaluation session historical, the same as window elapsed) | **unsent only** (`planned`, `registered_unsent`, `not_sent`) → `expired_unsent` or `cancelled_unsent`; never sent. **`ambiguous` intents are not touched**: they stay tracked and blocking (J-2). **Submitted orders are not cancelled** at the broker | None for this operation's continuation. **Recovery of ambiguous intents continues** (R3/M4/M5/M14). A new session needs the strategy free of unresolved uncertainty and of unaccounted working orders |
| `completed` | every planned intent reached a terminal broker state or was rejected | — | — |

- Handover and seeding require **no open operation** (A1 extended), **and** no unresolved outcome (A5), since the latter survives operation end (J-2).
- **End/expiry never:** erases recovery records or attempt logs, cancels broker orders, resolves uncertainty, or changes trading permission. After an operation ends with ambiguous intents, the strategy still reports `outcome_unresolved`. After it ends with working orders, `working_order_commitments_unaccounted` still applies until those orders are terminal and synced.
- **Continue session** runs the checks below, then proceeds, pauses again, or moves to `requires_reevaluation` / `terminated`. It never expires unsent orders because of a temporary condition.
- **Resume = explicit "Continue session"** on the same operation (a retry that resumes the pinned plan; R-6), never automatic. It may need repeating several times within one session (G-1 temporary limitation). Before continuing, **all** of:
  1. no intent of the strategy is `ambiguous`/unresolved;
  2. every `submitted` order of the operation is **terminal** at the broker;
  3. an owner-level broker sync completed **after** the last of them became terminal (fills and positions ingested);
  4. a fresh, clean **standalone** reconciliation (owner- or account-level) completed after that sync;
  5. the evaluation input manifest still matches (G-3); otherwise `requires_reevaluation`;
  6. the **per-intent permission check** (C) for the next intent: owner, enabled, kill switch, no unrecognized activity, execution window open (else `paused`/`terminated` per the table), and risk limits against the current portfolio (else `requires_reevaluation`).
- **During continuation:** `submitted` intents are **skipped** (never resubmitted); eligible unsent intents continue in plan order and pause again at the next unaccounted order.
- **Termination is lazy (R-27).** It's applied when the operation is next touched; read models show "window elapsed: operation will end" immediately. Broker day orders expire at the broker regardless.

**B. Recovery procedure (per registered intent of the uncertain operation):**
1. **Registered intents** are the local `PaperOrder` rows committed before any broker call.
   - None: classify the operation `nothing_submitted`. Supporting evidence must include the execution-path position of the failure (§2 standard: error signature, call order, downstream records). Zero rows alone is not enough (E-5). Without that evidence the operation stays **unresolved**.
2. **Account-level broker sync** (§3.2) applies broker state to known orders (legal transitions incl. `submission_failed`/`unknown` → broker state; `transition.py:22-90`).
3. **Classify each intent:**

| Established broker state | Evidence required | Action | Resubmit? | Blocks new submissions? |
|---|---|---|---|---|
| **Not found, order validity not yet ended** | lookups/scan find nothing (evidence items a–d below) while the order could still be live (day order before its session's close) | record the evidence; **stay unresolved** (F-1) | **no** | **yes** (`outcome_unresolved`) |
| **Not found after validity ended, no resubmission** | evidence a–d collected after the validity ended | **stays unresolved (J-1)**: evidence recorded; the strategy stays blocked until the order is found and verified or proven not sent (2026-10-04: a broker statement no longer releases it). There is no withdrawal | **no** | **yes** (`outcome_unresolved`) |
| ~~**Not found, resubmission wanted**~~ *(superseded 2026-10-04: resubmission is unavailable in the initial scope)* | evidence a–d plus a written broker statement of non-receipt | stays unresolved; the statement is audited evidence only; nothing is resent | — | yes, until the order is found or proven not sent |
| **Working**: open, accepted, pending, done-for-day | broker record matched by id | record id and status | **never** | per A/A2: `working_order_commitments_unaccounted` (commitment accounting deferred, F-3); the operation pauses |
| **Partially filled** | broker record + fills | record fills; the remainder is working | **never** (not the remainder either) | as working |
| **Filled** | broker record + fills | record; intent complete | no | no |
| **Canceled / expired** (possibly after partial fills) | broker record (+ fills) | record; intent ended. A new order is a **new decision** (new evaluation/session), never a retry | no | no |
| **Rejected** | broker record | record the reason; a new order is a new decision | no | no |
| **Replaced** | broker record with successor | successor is **unrecognized** (§3.4) → EXT-01 path | no | yes (unrecognized activity) |
| **Unresolved** | any evidence missing or contradictory: lookup error, undocumented not-found response, page cap reached, unmapped status, id mismatch | issue `outcome_uncertain_unresolved`; repeatable read-only recovery; escalate to §3.5 if the item turns out to be unrecognized | **no** | **yes** (`outcome_unresolved`) |

**Absence evidence items (a–d):**
- (a) `GET /v2/orders:by_client_order_id` returns HTTP 404 at two checks separated by a grace period;
- (b) a `status=all` list scan from registration time − margin, paged with `before_order_id` to exhaustion without hitting the page cap, has no match;
- (c) no fill references it;
- (d) no unexplained exposure change in the symbol.

Absence evidence alone never authorizes resubmission (F-1).

**Found = resolved (H-1).** If the order is found (by id or `client_order_id`) and its broker state is verified, the ambiguity is resolved; no broker confirmation is needed. The state then follows its row: working, partially filled, filled, canceled/expired, rejected or replaced.

**Escalation path, only for the missing-order path (R-23):** *(Amended 2026-10-04: steps 4–5 no longer lead to a resend or a resolution; the broker inquiry may help the order be found, and its answer is recorded as evidence.)*
1. Keep the strategy's new submissions blocked (`outcome_unresolved`); trading-permission and issue text say so.
2. Keep repeating read-only recovery (account-level sync, lookups). If the order ever appears, classify it per the table. An appearance at any time is handled normally.
3. Once the absence evidence package is complete, the operator raises a **broker inquiry** (Alpaca support) with the package: `client_order_id`, request times, attempt log, lookups and scan results.
4. A **written broker statement** that no order with that `client_order_id` was received, or the broker's record of the order if it exists, is recorded through an audited operation as **external broker evidence** (attached reference, reason, Job id). ~~Only a broker statement of non-receipt makes the intent `broker_confirmed_not_received`.~~ *(2026-10-04: the statement is evidence only; the intent stays unresolved.)*
5. ~~Even then, resubmission is a **new broker action**: same intent and id, full permission check (C), **inside the execution window** (§3.10). If the window has elapsed, the intent becomes `expired_unsent`, and trading resumes only through a new session on a new evaluation.~~ *(Whole step superseded 2026-10-04 by 04 "Safety correction, round 5" item 1: there is no resubmission while the original may still execute; an in-doubt intent is never made `expired_unsent` or sendable, and the strategy stays blocked until the broker shows the order or the attempt history proves it was never sent.)*
6. Operator acknowledgment alone never establishes non-submission. While the case is open, the operator's options are to keep the strategy blocked, trip the kill switch, or (for handover) wait, since handover requires no unresolved outcomes.

4. **Resolved** = every intent is established: `nothing_submitted` (§2-standard evidence), found-and-verified (any broker state), or proven not sent (positive evidence over the whole attempt history; 2026-10-04 replaces the earlier `broker_confirmed_not_received` rule, so a non-receipt statement no longer resolves) **and** a **fresh, clean standalone reconciliation** (account-level, or strategy-level when the strategy is the owner; not the in-session check, R-5) completed after the latest broker-touching Job for the strategy.
   - One domain predicate implements this. It feeds the retry gate, the fresh-submission gate (R-11) and the uncertainty issues.

**C. Retry identity vs. permission (D-2):**
- **Identity (R-6):** a retry **resumes** the original operation (the A2 continuation). It uses the same intents and ids and the pinned original risk run (`risk_run_id: null` → concrete id at original submission). `create_new_version` is **forbidden** on the retry path; today it submits a new order without cancelling a broker-touched predecessor (`submit_orders.py:1259-1337`; verified). Only `planned`, `registered_unsent` and `not_sent` (proven not sent) intents may be sent *(2026-10-04: `broker_confirmed_not_received` removed)*.
- **Permission (R-7):** immediately before **each** broker action, re-check against current state:
  - owner and enabled;
  - kill switch armed;
  - **execution window open** for the pinned evaluation session, and that session is still the one immediately before today's session (§3.10);
  - no blocking reconciliation;
  - no unrecognized activity;
  - no `outcome_unresolved`;
  - no `working_order_commitments_unaccounted`;
  - **source-data provenance** holds: the evaluation input manifest matches (G-3/H-2; see the split below);
  - risk limits against the current portfolio, re-validated for the pinned intent. That is a **new domain function**, with no new signals.
  - A failure maps to the operation state table (A2): pause, require re-evaluation, or terminate. It never silently expires everything.
- **Explicit outcome**: `paused`, `requires_reevaluation` or `terminated`, with a closed reason and next action (A2). Never re-plan, never report a no-op.

**Provenance vs current portfolio risk (H-2):**

| Input | Class | Checked how | On change |
|---|---|---|---|
| Market bars read for signals, missing-bar checks, calendar rows used, **valuation prices read at evaluation** (the recorded requests are re-executed, not re-derived from today's positions) | **source-data provenance** | manifest (R-25) re-verified at Continue and before each order | corrected, added or removed data → `requires_reevaluation` (`evaluation_data_changed`) |
| Strategy signal settings (`settings_snapshot`: indicators, universe, exits) and strategy version | **source-data provenance** | settings digest in the manifest | → `requires_reevaluation` (`strategy_settings_changed`) |
| Pinned intents (symbol, side, quantity, `client_order_id`) | **identity**, frozen | never recomputed | never changed; an intent that no longer passes current checks is frozen, never resized |
| Account cash, positions, open orders, exposure; **fills from earlier orders in the same operation** | **current portfolio state** | read fresh (after the required sync) before **each** further order | expected changes **don't** invalidate the evaluation; they feed the fresh risk check |
| Risk limits (max positions, allocation caps, cash sufficiency, duplicate position) and their **current** configuration | **current risk policy** | `revalidate_pinned_intent` against the current portfolio before each order | a failing check → `requires_reevaluation` (`risk_limit_failed:<code>`) for that operation; nothing re-planned |
| Kill switch, owner, enabled, execution window, reconciliation, unrecognized activity, unresolved outcomes | **current permission** | before each order | pause or terminate per A2 |
| Evaluation-time portfolio basis (cash and positions at evaluation) | **audit only** | recorded on the risk run | never used to invalidate |

**Continue interaction:**
- An earlier filled order changes the portfolio, which is expected. The manifest still matches, so **no re-evaluation is required**.
- The next original intent is re-checked against the refreshed portfolio: it is sent unchanged if it passes, and the operation moves to `requires_reevaluation` if it fails.

**D. D-19 amendment (proposed text, supersedes revision 3):**

> **D-19 (amended):**
> - While any uncertain outcome for a strategy is **unresolved**, **every** `paper-session` submission (retry or fresh) for that strategy is rejected with a typed 409 (`outcome_unresolved`, `reconciliation_required` or `reconciliation_not_clean`).
> - Resolution requires the COR-02 B predicate: per-intent broker-state classification under the stated evidence rules, plus a fresh, clean standalone reconciliation after the latest broker-touching Job. A `SUCCEEDED` reconciliation alone never resolves anything.
> - An ambiguous submission resolves when the broker order is found and its state verified.
> - If it isn't found, absence evidence and elapsed time never resolve it and never authorize resubmission. Only the order appearing (or proof from its attempt history that it was never sent) resolves it (J-1; amended 2026-10-04 — a recorded broker statement of non-receipt is evidence only).
> - Ending or expiring the execution operation does not resolve uncertainty (J-2).
> - A known working order is not uncertainty. It blocks further submissions through `working_order_commitments_unaccounted` (deferred commitment accounting), and within an operation it pauses the operation (COR-02 A2).
> - Continuation needs terminal orders, a sync, a fresh clean standalone reconciliation, an open execution window and a per-intent permission check.
> - Broker sync is never gated and never resolves anything by itself.
> - Retries resume the original intents and ids (no new versions) and re-check permission before each broker action.
> - The orchestration layer calls a domain read service for the predicate (invariant 8).

> **Planning amendments (2026-10-03, rounds 1–4; 04 "Planning review, round 3" and "Final correction"; the final correction refines the S1 guarantee (a recorded timeout is uncertain; a suspended process can send its one recorded request arbitrarily late) and replaces S3-R3 with S3-R4: verified basis, risk approval and a non-replay decision fingerprint):** submission authority and the attempt row are committed before every POST, and takeover treats outcome-less attempts as in doubt (S1-R3). The guarantee is duplicate-submission prevention, not prevention of a stale executor's single late POST. Every pre-send risk check uses a fresh Alpaca latest-trade price (S2-R3). A new intent requires an evaluation whose portfolio basis postdates earlier executions; a retry or continuation never repeats an executed effect (S3-R3). Admission and handover serialize on the active-paper-strategy row (SER). Paused adds `price_unavailable` and `not_active_paper_strategy`; re-evaluation adds `price_moved_beyond_tolerance` *(round-3 design; superseded: PD-1, approved 2026-10-04, moves it to paused)*.
>
> **Safety correction, round 5 (2026-10-04; 04 "Safety correction, round 5"):** no resend while the original may still execute; broker statements are evidence only; "proven not sent" needs positive evidence over the whole attempt history; a changed decision fingerprint is replay detection only; new intents need justification by verified state and strategy rules; TL-10 one broker-reaching action per strategy, evaluation session, symbol and side; TL-11 partial-fill remainders not pursued automatically; `price_moved_beyond_tolerance` becomes a pause reason (PD-1, approved 2026-10-04; 04 "Planning correction, round 6").

### 3.8 Broker sync conditions (D-6)

- Available at strategy level (the owner) and account level (§3.2), including while trading is blocked.
- It submits nothing, changes no ownership, lifts no gate, never adopts positions or unrecognized orders, and ingests fills only for known orders.
- Tested.

### 3.9 COR-01 — Evaluation must not corrupt account truth

_Status: draft; approach = R-4 (not a decision)._

- Risk evaluation writes no `AccountSnapshot`; its portfolio basis is recorded on its run.
- The reconciliation baseline is the latest **broker-observed** snapshot (account-level).
- Sizing uses cash.
- The existing `risk_evaluation` snapshots (4) are excluded by source.
- Test: evaluate → reconcile with an unchanged broker account ⇒ no divergence.
- The stage order is finalized after the fix (02 §4.2).

### 3.10 COR-04 — Calendar, evaluation session and execution window (revised by F-4)

_Status: draft._

**Three separate facts.** They're never one "current session" field, and each has an explicit unknown.

| Fact | Definition | Closed result |
|---|---|---|
| **Trading day** | From the **persisted** calendar at server time: today's date if it is an XNYS session, else the next session; phase from the session's open/close times | `known(date, phase ∈ {pre_open, open, after_close, non_trading_day})` · `unknown(calendar_data_unavailable)` when the calendar doesn't cover now |
| **Evaluation session** | The latest **completed** session (close < now) whose required data is ready: bars for every universe symbol + ≥ `warmup_periods` of history + a calendar row. Readiness is **data-driven**, never time-assumed | `ready(S)` · `not_ready(S, reason ∈ {missing_bars, insufficient_history, calendar_gap})` · `unknown(calendar_data_unavailable)` |
| **Execution window** | For evaluation session S: the regular hours of `next_session(S)`, from open until close − cutoff (R-20). Windows are computed from calendar open/close, so early closes are respected | `open(until)` · `closed(next_opens_at)` · `unknown(calendar_data_unavailable)` |

**Initial execution policy (G-2).** The rules below are the **current supported policy** for daily strategies, not a platform invariant. The policy is a named, versioned setting (`execution_policy = regular_hours_prev_session_v1`) so future strategies can declare others.

**Paper-trading eligibility under that policy.** Checked at submit, and re-checked before each broker action (§3.7 C):
- the trading day is `known` and in phase `open`;
- the evaluation session S is `ready` **and** equals `previous_session(trading day)`. This is the freshness rule: the newest completed session, with no gap;
- now is inside S's execution window;
- the risk evaluation used is for S, **and** its **input manifest matches** the current data it read (R-25): same bars (values, count, provider, adjusted flag) for the same symbols and session ranges, and the same strategy settings digest. A re-ingest that changes any value, adds a previously missing bar, or removes one makes the evaluation **stale** (`evaluation_data_changed` → re-evaluate). A no-op re-ingest (identical values) does **not**. `updated_at` can't be used: the bar upsert sets it on every re-ingest (`services/ingestion.py:95-107`).

**Rejections at submit (closed):**
- `historical_execution_rejected`: S is older than `previous_session(trading day)` (O-4 settled; changes OPS-03);
- `outside_execution_window`;
- `evaluation_data_not_ready`;
- `evaluation_data_changed` (manifest mismatch; R-25);
- `calendar_data_unavailable`.

**Always allowed:** research, backtests, and risk evaluation of any past session. These are labelled historical and never executable.

**Calendar synchronization (F-4):**
- `sync-market-sessions` may sync **ahead of today** up to a configurable **coverage horizon** (R-21: default ≥ 60 sessions, bounded by the calendar library).
- Issue `calendar_runway_low` below a threshold; `calendar_out_of_range` when the calendar doesn't cover now.
- The `TO_DATE_IN_FUTURE` rejection changes for this job type only; `ingest-bars` keeps it, since bars can't exist in the future.

**Defaults:**
- Session-scoped submission defaults use the evaluation session S (typed reason when unknown or not ready), never "latest session with bars".
- `ingest-bars` defaults its range to end at S's expected session.
- Backtest defaults are unchanged.

**COR-03 (extended by H-3).** Batch outcomes are `complete | partial | failed` from the domain result, now also for **`sync-symbol-metadata`**:

| Failure | Scope | Effect |
|---|---|---|
| Ticker unknown to the provider (404 or empty), response missing required fields, per-ticker validation error, per-ticker fetch error after retries | **symbol** | That symbol is recorded in `symbols_failed` with a closed reason (`not_found`, `missing_required_fields`, `invalid_response`, `fetch_error`). Other symbols continue. Outcome `partial` (lifecycle SUCCEEDED), or `failed` if all fail (lifecycle FAILED) |
| Provider auth failure (401 / `PolygonAuthError`), invalid configuration, database write failure, provider unavailable for the whole request | **operation** | Stop immediately; outcome `failed` (lifecycle FAILED); no symbol is marked synced by this run |

- **Readiness (R-29):** a universe symbol lacking required metadata is `not_ready(missing_metadata)` in coverage/readiness reads. Its candidates are rejected by risk with `symbol_not_ready`, and issue `symbol_metadata_missing` recommends "Sync symbol reference data".
- Other symbols are unaffected: the evaluation session's data readiness remains a bar/history rule; metadata readiness is **per symbol**.
- Today's `raise_for_failures` (any failed ticker → FAILED) changes to these semantics.

### 3.11 Schema-change inventory and invariant impact

| # | Change | Plan | Kind | Invariant / rule impact |
|---|---|---|---|---|
| S-1 | `active_paper_strategy` singleton (owner nullable, `since`, reason, set-by run); constraint: at most one row | P20.1-01 | new table + constraint | Written only by `OperatorControlService` (invariant 1, path 2 as amended, conflict row 23). Audited as a Control Change (S8) |
| S-2 | Order-submission **attempt log** (one row per HTTP attempt: intent, attempt #, started, outcome class) | P20.1-02 | new table | Written by the execution service inside Jobs (path 1). Append-only; never deleted by End/expiry (J-2) |
| S-3 | `account_reconciliation_runs` (**R-31, recommendation**): scope `account`, `job_id`, status, `completed_at`, `blocks_execution`, findings, divergence, unexplained exposure, classification summary; **no strategy reference** | P20.1-08 | new table | Path 1 (Job). Invariant 8 unaffected (domain service, no Job import). AUD-01/Activity (P21-06) and OPR-05 must read it next to strategy reconciliation runs. The recovery predicate, handover checks A4/A6 and analytics "latest reconciliation" consult both. Phase 11: indexed by `completed_at`; query-count tests. **No strategy is borrowed** (J-3) |
| S-4 | `external_broker_activity` (immutable verified broker snapshots, fills, hash, origin, reason, `job_id`) | P20.1-09 | new table | Path 1. Never a strategy reference (no adoption, D-1) |
| S-5 | Recovery records: per-intent classification, absence evidence items, broker statements (reference, reason) | P20.1-10 | new table(s) | Broker statements via synchronous control (path 2 as amended). Never deleted by End/expiry (J-2) |
| S-6 | Execution-operation state: extend native `strategy_run_status` with `paused`, `requires_reevaluation`, `terminated`, **or** a small `execution_operations` table; **partial unique index**: at most one open operation per strategy; intent terminal states `expired_unsent` / `cancelled_unsent` | P20.1-11 | enum migration or new table + index | Closed-enum tests updated. The Job lifecycle enum (JOB-01) is **unchanged**; the operation outcome is separate (H-0) |
| S-7 | `RiskDecisionCode` gains `symbol_not_ready` | P20.1-04 | **code only** (`risk_events.decision_code` is `String(64)`, verified) | Closed-enum test updated |
| S-8 | `worker_heartbeats` | P21-01 | new table | Phase 21's pinned schema delta (exactly this table). Worker self-state, not an operator mutation |
| — | Not changed: `jobs` (invariant 2; the outcome is derived), `strategy_runs.strategy_id` nullability, `paper_orders` ownership semantics, `account_snapshots` (already nullable `strategy_id`) | — | — | — |

---

## 4. Attribution ledger vs. the smallest viable alternative (R-16)

| | **External-activity record table** (recommended now) | **Full attribution ledger** (later option) |
|---|---|---|
| **Stores** | Only recorded external items: verified broker snapshot of each order and its fills, content hash, origin tag, reason, Job id, recorded-at | One row per broker order (all classes), with classification, owning strategy if owned, evidence refs, audit refs; plus a scan watermark |
| **Why existing records are insufficient** | `PaperOrder`/`PaperFill` rows belong to a strategy run; storing external items there would assign them to a strategy, which is adoption (D-1). No existing table can hold "verified broker activity with external origin" | same, plus nothing records "already classified" |
| **Owned classification** | Derived on every check from `PaperOrder`/`PaperFill` (the §3.4 evidence) | Stored; must be kept in sync with `PaperOrder` changes |
| **Consistency** | Records are immutable. Every check re-fetches broker data for recorded items and compares hashes; a mismatch makes the item unrecognized. Nothing derived is stored | Needs invariants between ledger rows and local orders, a re-derivation or repair path, and watermark correctness; more places to drift |
| **Cost** | Full broker history re-read every check (paged; raises at the cap) | Incremental re-scan beyond the watermark |
| **When needed** | Now: EXT-01 requires it | When history approaches page caps or check latency matters. Current volume is **0 orders** |

**Recommendation:** the record table now (a 20.1 schema change); the ledger stays an architectural option triggered by volume, with `broker_history_exceeds_cap` as the observable trigger.

---

## 5. Waves and dependencies (Phase 20.1)

> Applied 2026-09-30 and 2026-10-03: `ROADMAP.md`, `REQUIREMENTS.md`, `PROJECT.md`, `STATE.md` and the Phase 20 CONTEXT annotations are updated, and the formal plans live in `.planning/phases/20.1-operator-state-correctness/` and `.planning/phases/21-operator-read-model-foundation/`. Execution waves there supersede the programme waves below (migration-chain and same-file serialization).

| Wave | Items | Depends on |
|---|---|---|
| 1 | **PAPER-01** (singleton, no-owner start, gates), R-8, **COR-06** (+ R-17 status mapping, R-22 attempt log), COR-01, COR-03, **PROV-01** evaluation input manifest (P20.1-06), **COR-04** (three calendar facts, eligibility, calendar horizon) | — |
| 2 | **ACCT-01** (account-level sync and reconciliation), **COR-05** (evidence classes, exposure check, no adoption), **EXT-01** (record table + record-and-check Job) | PAPER-01; COR-06/R-17 (status meanings) |
| 3 | **COR-02** (recovery procedure incl. escalation R-23, working-order blocking and in-operation pause/continue R-24, resolution predicate, gates R-11, resume R-6, permission R-7, D-19 amendment) | COR-01 (clean), **COR-04 (evaluation session + execution window)**, COR-05 + ACCT-01 (classification), COR-06 (attempt log R-22 + taxonomy) |
| 4 | **PAPER-02** (seeding + handover controls), then **legacy-console truthfulness** (P20.1-14), then the **API E2E gate** (P20.1-13) | ACCT-01, COR-05, EXT-01, COR-02; P20.1-14 also needs COR-06, COR-03 and the operation read |

- **Schema changes:** see the inventory in §3.11 (S-1 … S-8). Summary: active-paper-strategy singleton; external-activity record table; per-intent recovery classification (for example the A2 intent states — `broker_confirmed_not_received` removed 2026-10-04 —, the per-attempt log R-22, and recovery evidence records). Phase 21 keeps its pinned delta (`worker_heartbeats`).
- **Phase 21** depends on all of 20.1. Optional overlap: WRK-01/02, OPR-06 skeleton, OPR-07 static fields, OPR-08 harness.

### 5.1 Milestone order (F-5)

v1.3: Phase 20.1 → Phase 21 (read models + **worker heartbeat**) → v1.3 closes → **v1.4 Operator Console** (Phase 22 design; 23–27 rebuild) → **v1.5 Strategy Lab**. `PROJECT.md`'s "next milestone: Strategy Lab" is updated when the roadmap is edited.

## 6. Phase 21 deltas (from revision 2)

- **Blockers and issues added:**
  - `no_active_paper_strategy`;
  - `unrecognized_broker_activity` (safety);
  - `outcome_uncertain_unresolved` (safety);
  - `working_order_commitments_unaccounted` (blocking, Trading permission lane);
  - anomaly `owned_order_outside_ownership_period`.
- **The 2026-09-29 fixture** yields `calendar_out_of_range`, `worker_unknown`, `no_active_paper_strategy` and `outcome_uncertain_unverified` (classified `nothing_submitted`, awaiting the fresh account-level check). Trading day, evaluation session and execution window are all `unknown(calendar_data_unavailable)`.
- **OPR-01/OPR-03** expose the three calendar facts separately (never one "current session"), and the execution-operation state (`running` / `paused` / `requires_reevaluation` / `terminated` / `completed`) with its closed reason and next action. Issue `calendar_runway_low` added.
- **OPR-05** exposes account-level reconciliation, per-item class and origin, unexplained exposure, and recorded-external items.

## 7. Requirement changes (draft)

- **Moved:** AUD-02, NOTIF-02 → v1.4.
- **Kept:** AUD-01 → Phase 21.
- **New (v1.3):**
  - PAPER-01, PAPER-02 (seeding/handover controls, possibly CTRL-03/04);
  - **ACCT-01**;
  - COR-01 … COR-06;
  - **PROV-01** (evaluation input manifest; P20.1-06);
  - **COMPAT-01** (legacy-console truthfulness during the API-only interim, H-0; P20.1-14);
  - **REC-01** (recovery procedure and predicate);
  - **REC-02** (resume + permission check);
  - **EXT-01**;
  - OPR-01 … OPR-08;
  - WRK-01, WRK-02.
- **Future:** CONC-xx (concurrent paper trading), INT-01 (adopt external positions), FLAT-01 (flatten / exits-only, O-12), RISK-COMMIT-01 (commitment-aware accounting; deferred by F-3), LEDGER-01 (volume-triggered).

## 8. Conflicts (updated)

| # | Source | Conflict | Resolution |
|---|---|---|---|
| 1 | Phase 20 **D-19/D-20** | SUCCEEDED-only unlock; retry-only; sync gated; jobs-table only | Amend (§3.7 D) |
| 2 | Phase 20 **D-23** + OPS-07 | Retry can re-plan or create new versions | Pin identity; forbid `create_new_version` on retry; permission re-check |
| 3 | `_is_resubmittable_order` | "Broker never saw it" judged by a missing local `broker_order_id` | Replace with §3.7 B classification |
| 4 | `AlpacaClient._request_with_retry` | Re-sends order POSTs | COR-06 taxonomy |
| 5 | `_normalize_status` / `_PENDING_BROKER_STATUSES` | `replaced` treated as pending; `done_for_day` → unknown | R-17 mapping |
| 6 | `sync_orders._sync_positions_from_broker` | Silent adoption | ACCT-01/COR-05 |
| 7 | Phase 19 **D-08**; `TO_DATE_IN_FUTURE` for calendar sync | Silent 13 Mar default; no runway | COR-04 (F-4): defaults from the evaluation session; calendar sync ahead to the horizon |
| 8 | `default_strategy_id`; `ensure_strategy_record` creates enabled | Implicit or permissive paper eligibility | PAPER-01 (no owner), R-8 |
| 9 | Control audit attached to `trend_following_daily` | Global controls look strategy-owned | S8 translation |
| 10 | ROADMAP Phase 21 goal and criteria 2–5 | UI; "no new tables" | S2; pinned `worker_heartbeats` |
| 11 | REQUIREMENTS traceability | AUD-02/NOTIF-02 moved | Update |
| 12 | Milestone "no auth surface" + ORCH-07 | Mobile emergency controls | O-7 |
| 13 | PROJECT invariants 1–3, ORCH-08 | Heartbeat write; new controls (seed, handover); EXT-01 and ACCT-01 as Jobs; outcome placement; per-type mapping | Controls via `OperatorControlService`; broker-reading operations as Jobs; heartbeat = worker self-state; outcome in the read layer |
| 14 | JOBUI-02 | Run-detail link | Owning concept / Technical |
| 15 | **OPS-03** (run a paper session from the UI) | Historical execution now rejected; execution-window eligibility | **Amend** (F-4): paper sessions execute only for the fresh evaluation session inside its execution window |
| 16 | PROJECT "Next milestone: Strategy Lab" | v1.4 inserted before it | **Settled** (F-5): update on roadmap edit |
| 17 | Phase 11 performance invariants | Full-history re-reads | Query-count tests; `broker_history_exceeds_cap` surfaced; ledger as trigger-based option |
| 18 | **OPS-07** (retry only FAILED/CANCELLED; same payload) | Continue session resumes a paused operation whose Job ended successfully | Continue is a separate paper-session mode (`mode: continue`, `operation_id`) with its own Idempotency-Key; OPS-07 is unchanged |
| 19 | **S2** (no temporary UI) vs new 20.1 operator actions with no UI until v1.4 | The existing console can't drive seeding/continue/end/recording and misstates paused operations | **Decision D-1** in 04 §0.1: API-only until v1.4, a minimal temporary surface, or pulling the v1.4 Trading area forward |
| 20 | Phase 20 payload rules (`as_of_session_in_future`, `as_of_session_not_trading_session`) | `as_of_session` means the **evaluation (data) session**; execution time is separate | Keep the payload name; document its meaning; add execution-window checks at submit and before broker actions |
| 21 | Phase 20 pinned **five-route mutating allowlist** (ORCH-01/02/08 boundary tests) | New mutating routes: active-paper-strategy control, End operation, Record broker statement | Extend the pinned allowlist deliberately (each new route is mutation path 2, a synchronous control, or path 1, a Job; each is gated by ORCH-07). The boundary test is updated, not weakened |
| 22 | **02 §2 "Sync symbol reference data: optional, nothing depends on it"** | Symbol metadata now gates symbol readiness (R-29) | Update the IA copy; it's no longer optional for trading readiness |
| 23 | PROJECT **invariant 1**: path 2 = "immediate safety controls" | New synchronous operator actions without broker calls: seed/handover, End operation, Record broker statement | **Amend** invariant 1 so path 2 covers "synchronous operator controls that make no broker call, audited, idempotent by target state" (seed/handover and End are safety-adjacent; the evidence writes are audited records). Alternative: make the two evidence writes Jobs |
| 24 | `strategy_runs.strategy_id` **NOT NULL** (`db/models/strategy_run.py:55-58`) | An owner-less account-level reconciliation (ACCT-01) can't be stored as a strategy run | Schema choice in P20.1-08 (recommended: a dedicated `account_reconciliation_runs` record with the same result shape + `job_id`). Recovery predicate, handover checks and the reads that list reconciliations consult both |

## 9. Applying later (checklist)

Once approved:
- edit `REQUIREMENTS.md`, `ROADMAP.md`, `PROJECT.md`;
- add the D-19 amendment note to the Phase 20 CONTEXT;
- insert 20.1 as a decimal phase.

Tooling hazards: re-read the ROADMAP Progress row after every CLI update; `state.*` verbs need named flags; decision-coverage gate needs the explicit-path form; `config-get` returns empty for absent keys.
