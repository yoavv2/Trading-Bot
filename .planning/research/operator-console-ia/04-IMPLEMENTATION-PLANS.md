# Operator Console Programme — Implementation Plans (draft for review)

_Date: 2026-09-30 (formal plans 2026-10-03) · Status: **formalized**: plans in `.planning/phases/20.1-operator-state-correctness/` and `.planning/phases/21-operator-read-model-foundation/`, wired into `ROADMAP.md`/`REQUIREMENTS.md`; not executed · Specification basis: [03-PLANNING-CHANGES.md](03-PLANNING-CHANGES.md) revision 8 · Interim operation: [05-INTERIM-API-OPERATIONS.md](05-INTERIM-API-OPERATIONS.md) · IA basis: [02-HYBRID-IA-PROPOSAL.md](02-HYBRID-IA-PROPOSAL.md)_

These plans become GSD `PLAN.md` files under `.planning/phases/20.1-…` and `.planning/phases/21-…` **after** you approve the roadmap/requirements edits (03 §9 checklist). Until then they live here.

**Conventions:**
- Every plan includes the acceptance criterion: **API changes are additive and `tests/test_console_api_contract.py` stays green** (TL-9 / D-1).
- Every acceptance criterion is a test, a query-count assertion, or a named verification step.
- Plan IDs: `P20.1-NN` and `P21-NN`.
- "Files" lists the primary modules touched; the tests go under `tests/`.
- Every plan ends green on the pre-commit gates (ruff + mypy) and the full test suite.
- **No console UI changes in Phase 20.1 or 21.** The console rebuild is v1.4.

---

## 0. Temporary limitations (accepted; stated in the product and docs)

| # | Limitation | Consequence for the operator | Future work (not a prerequisite) |
|---|---|---|---|
| TL-1 | **Sequential execution with pauses** (G-1). A multi-order session pauses whenever a submitted order's effects aren't yet accounted, including an immediately filled order awaiting sync | Several **Continue session** actions may be needed within one session, each after sync + reconciliation | Open-order-aware risk accounting; in-operation fill ingestion; smoother continuation |
| TL-2 | **Working orders block further submissions** for the strategy (`working_order_commitments_unaccounted`) | Nothing else trades until the order is terminal and synced | Commitment-aware risk accounting (RISK-COMMIT-01) |
| TL-3 | **One execution policy**: regular hours of the session after the evaluation session, from the open until a cutoff before the close (G-2) | No pre-open or after-hours submission; historical execution rejected | Per-strategy execution policies |
| TL-4 | **Ambiguous submission (H-1, J-1).** If the broker order is **found** and its state verified, it resolves normally. If it is **never found**, the strategy stays blocked for new submissions until the order appears or its attempt history proves it was never sent; *(amended 2026-10-04: Alpaca's written non-receipt is audited evidence only, never a release or a resend; no product-level release; ownership changes are blocked too via A5)*. Elapsed time and absence evidence never clear it; ending the operation doesn't either (J-2) | Blocking may last until the broker replies | None planned: follows from undocumented broker visibility |
| TL-5 | **External activity only when terminal and net-zero** (E-1) | Non-zero external exposure must be neutralized at the broker first | Intervention control (INT-01) |
| TL-6 | **Handover only when flat** (D-5) | A strategy with positions can't be switched | Flatten / exits-only (FLAT-01, O-12) |
| TL-7 | **Full broker history re-read on every check** | Latency grows with account history; `broker_history_exceeds_cap` surfaces the limit | Attribution ledger / watermark (LEDGER-01) |
| TL-8 | **No scheduler**: window expiry is applied lazily (R-27) | An elapsed operation shows "will end" until it's touched | Paper Automation milestone |
| TL-9 | **New operator actions are API-only until v1.4** (H-0, decision D-1 = a): seed/change active strategy, Continue, End operation, Record external activity, Record broker statement, starting paper sessions | The operator uses the runbook in 05. The old console **no longer starts paper sessions**, shows Outcome next to Job status, and hides paper-session Retry (P20.1-14). UAT runs through the API (05 §3) | v1.4 Trading area |
| TL-10 | **One broker-reaching action per (strategy, evaluation session, symbol, side)** (round 5, 2026-10-04; initial product limitation, not a permanent invariant). Consumed by any earlier intent with that key that reached or may have reached the broker; not by intents never sent | A partially filled exit leaves no second sell in that evaluation session; a failed (rejected/expired) submitted action is retried only by a later evaluation session | Intent-level re-entry rules; target-position semantics |
| TL-11 | **Partial-fill remainders are not pursued automatically** (round 5) | The remaining position is kept, shown and risk-checked; a buy remainder is never topped up; a sell remainder exits on a later session per the strategy's exit rule (04 round 5 table) | Target-position semantics |

## 0.1 Blocking issues

_Schema-change inventory and invariant impact: 03 §3.11 (S-1 … S-8)._

**None.** D-1 = option (a), API-only interim operation. W-1 declined (J-1). End ≠ resolution (J-2). Dedicated account-check storage is a documented recommendation (J-3, 03 §3.11 S-3). Every plan carries the criterion: **API changes are additive; existing console contract tests (`tests/test_console_api_contract.py`) stay green**, plus the P20.1-14 truthfulness adjustments.

**Implementation-level choices made inside plans (not product decisions):**
- P20.1-11: execution-operation persistence. Extend the native `strategy_run_status` enum with `paused` / `requires_reevaluation` / `terminated`, or add a small `execution_operations` table. Decide in plan discussion; both need a migration.
- P20.1-02: the exact not-found response of `GET /v2/orders:by_client_order_id` is undocumented, so the client treats **only** HTTP 404 as not-found evidence and everything else as `unresolved`.

O-5 is approved (H-3): symbol-metadata outcomes `complete | partial | failed`, with per-symbol reasons and readiness gating.

---

## 1. Phase 20.1 — Operator-State Correctness & Paper-Account Ownership (v1.3)

**Goal:** trading safety semantics are correct and explicit before any read model is built:
- single active paper strategy (starting with none);
- evidence-based attribution with an account-level check;
- audited external-activity recording;
- unambiguous order submission and recovery;
- a correct account baseline;
- honest batch outcomes;
- calendar/evaluation/execution facts;
- evaluation data provenance;
- a paused/resumable execution operation.

### Dependency graph

> **Execution waves (authoritative, 2026-10-03):** the formal plans in `.planning/phases/20.1-operator-state-correctness/` and `ROADMAP.md` use waves based on true dependency depth. The Alembic chain 0021→0027 is serialized, and plans that edit the same files never share a wave:
>
> | Wave | Plans |
> |---|---|
> | W1 | 01, 03 |
> | W2 | 02, 04 |
> | W3 | 05, 07 |
> | W4 | 06, 08 |
> | W5 | 09 |
> | W6 | 10 |
> | W7 | 11 |
> | W8 | 12, 15 |
> | W9 | 16 |
> | W10 | 14 |
> | W11 | 13 |
>
> **Declared dependency edges added during formal planning (no wave changed):** 02←01 and 10←09 (Alembic chain); 04←03, 05←01/02, 06←03/04/05 (wave-1 same-file conflicts); 05←04, 07←03, 08←03/04/05, 10←04/06 (files shared across waves are now ordered by declared dependencies, not only by wave number). The frontmatter `depends_on` of each plan is authoritative.
>
> **Split of the former 20.1-11 (applied 2026-10-03):** 11 (storage, state machine, S1 primitives, reads, End; W7) ← 05/06/10; 15 (permission, risk revalidation, sequential loop, start-mode gates; W8) ← 11; 16 (Continue mode, S1 takeover and concurrency acceptance; W9) ← 15; 14 ← 15/16 and 13 ← 15/16 (14 moved W9 → W10, 13 moved W10 → W11); 12 stays W8 (needs only 11's effective state).
>
> **Phase 21 waves (authoritative):** W1 21-01 · W2 21-02 (depends on 21-01: the Operations-engine lane and worker issue predicates read `services/worker_health.py`) · W3 21-03..21-07 (all depend on 21-02; 21-03 also on 21-01) · W4 21-08 (Overview completion; depends on 21-02, 21-03, 21-04, 21-06). 21-02 owns all shared route scaffolding in `api/app.py`, the `as_of`/query-count harness and the 2026-09-29 fixture, so wave-3 plans touch disjoint files.
>
> The programme grouping below is kept for traceability.

```
Wave 1:  01 PAPER-01 ─┐   02 COR-06 ─┐   03 COR-01   04 COR-03   05 COR-04   06 PROVENANCE
                      │              │
Wave 2:  07 COR-05 (01,02) ── 08 ACCT-01 (01,07) ── 09 EXT-01 (07,08)
                                          │
Wave 3:  10 REC-01 recovery (02,03,05,07,08) ── 11 REC-02 operation storage/state machine/End (05,06,10)
                                          │       └─ 15 REC-02 submission loop/permission (11) ── 16 REC-02 Continue (15)
                                          │
Wave 4:  12 PAPER-02 seeding/handover (01,08,09,10,11) ── 14 Legacy-console truthfulness (02,04,11,12,15,16) ── 13 Phase gate API E2E (all)
```

---

### P20.1-01 — Single active paper strategy (PAPER-01)

- **Wave 1 · Depends on:** — · **Requirements:** PAPER-01, R-8.
- **Files:** `db/models/` (new singleton model), a new Alembic migration, `services/operator_controls.py`, `services/bootstrap.py`, `core/settings.py` (retire `paper_session_runner.default_strategy_id` as the paper default), `jobs/handlers/paper_session_submission.py`, `reconciliation_submission.py`, `services/execution/submit_orders.py` (run-time gate), `api/routes/controls.py` (read only in this plan).
- **Tasks:**
  1. Persisted singleton `active_paper_strategy` (`strategy_id` nullable, `since`, `set_by_run_id`, `reason`), with a DB constraint making two owners unrepresentable. Seed = **no owner**.
  2. Submit-time gate: `paper-session` and strategy-scoped `reconciliation` for a non-owner → typed conflict `strategy_not_active_paper_strategy`. With no owner → `no_active_paper_strategy`.
  3. Run-time gate in `run_paper_session`, before any broker call and before each submission, alongside the kill-switch check: blocked outcome with reason `not_active_paper_strategy`.
  4. `ensure_strategy_record` creates new strategies **disabled** (R-8). Existing rows are unchanged; they're all enabled today, but inert without ownership.
  5. Control read `GET` for the active paper strategy (read-only). The seeding/handover mutation comes in P20.1-12.
- **Acceptance criteria:**
  - A DB-level test shows two owners can't be inserted.
  - For each gated job type × {owner, non-owner, no owner}, the typed result is asserted.
  - A handover fixture between submit and claim → the session blocks with `not_active_paper_strategy` and **zero** broker calls (a fake broker records calls).
  - A newly registered strategy row is `disabled`.
  - Backtests and risk evaluation for non-owners still succeed (research unaffected).
- **Out of scope:** the seeding/handover controls (P20.1-12).

### P20.1-02 — Order submission attempt log, failure taxonomy, status mapping (COR-06, R-17, R-22)

- **Wave 1 · Depends on:** — · **Requirements:** COR-06.
- **Files:** `services/alpaca.py` (`_request_with_retry`, `_normalize_status`, `_PENDING_BROKER_STATUSES`, new `get_order_by_client_order_id`), `services/execution/submit_orders.py`, `services/execution/contracts.py`, `db/models/paper_order.py` / new attempt-log model, a new migration, `services/execution/transition.py` (only if a new event type is needed).
- **Tasks:**
  1. Order POSTs bypass generic retries. A dedicated submit path logs each HTTP attempt durably **before** and **after** it (attempt #, start, outcome class). The log is written in its own committed transaction so a crash leaves an attempt with no outcome.
  2. Attempt classes: `pre_connection` (`ConnectError`, `ConnectTimeout`, `PoolTimeout`), `deadline_expired` (*added 2026-10-04*: the send path's wall-clock check refused to hand the request to the HTTP client; written only by 20.1-15, stored and classified by 20.1-02 / migration 0023), `ambiguous` (all other transport errors, timeouts, 5xx, 429, missing outcome), `duplicate_reported` (422 + exact message "client_order_id must be unique"), `rejected` (other 4xx), `accepted` (2xx).
  3. Submission class over the whole sequence: `not_sent` only if **all** attempts are `pre_connection` or `deadline_expired` with complete outcomes; a later `deadline_expired` never erases an earlier ambiguous or incomplete attempt. Automatic retry is allowed only while that holds. Any `ambiguous` → stop, intent → `UNKNOWN`, Job `outcome_uncertain`. `duplicate_reported` → lookup.
  4. Status mapping: `done_for_day` → working; `replaced` → a terminal-with-successor marker (handled as unrecognized successor in P20.1-07); anything unmapped → `unknown` + reason `unmapped_broker_status`.
  5. `get_order_by_client_order_id` (GET, retries allowed). Only HTTP 404 counts as not-found evidence.
  6. Intent states within an operation: `planned`, `registered_unsent`, `not_sent`, `submitted`, `ambiguous`, `rejected` (derived from local order status + attempt log; no free-text).
- **Acceptance criteria:**
  - Fake transport:
    - read timeout after send → exactly **1** POST, intent `UNKNOWN`, Job uncertain;
    - connect error ×2 then 2xx → 3 attempts, `accepted`;
    - read timeout then connect error → still `ambiguous`, no 3rd POST;
    - a simulated crash between attempt start and outcome → `ambiguous`;
    - 422 duplicate → one lookup, no inference;
    - 422 page-size-style message with the same code 40010001 → `rejected`, not duplicate.
  - Status table test: each of the 16 documented statuses maps to exactly one of {working, terminal, terminal-with-successor, unknown}.
  - GET list/lookup calls still retry.

### P20.1-03 — Evaluation must not corrupt account truth (COR-01)

- **Wave 1 · Depends on:** — · **Requirements:** COR-01, R-4.
- **Files:** `services/risk.py` (`run_risk_evaluation`), `services/portfolio.py` (`load_state`, `persist_snapshot` call sites), `services/reconciliation/report.py` (baseline selection), `services/analytics.py` / `operator_reads.py` (latest-account reads).
- **Tasks:**
  1. Risk evaluation stops persisting `AccountSnapshot`; it records its portfolio basis (cash, exposure, positions, source snapshot id and age, or `configured_starting_cash`) in its run `result_summary`.
  2. Sizing reads cash from the latest **broker-observed** snapshot (`snapshot_source='broker_sync'`), falling back to configured starting cash, recorded as such.
  3. The reconciliation baseline is the latest broker-observed **account** snapshot. Existing `risk_evaluation` snapshots are excluded by source in baselines and "latest account" reads (no data migration).
- **Acceptance criteria:**
  - A replay of 29 Sep (evaluate, then reconcile against an unchanged fake broker account with buying power 4× cash) → `account_divergence == {}` and `blocks_execution == false`.
  - After an evaluation, the latest-account read still shows the broker snapshot.
  - Sizing never uses buying power (property test over cash/buying-power pairs).
  - Canonical stage order: a test documents that Trade needs no Sync after Decide.

### P20.1-04 — Batch operation outcomes (COR-03)

- **Wave 1 · Depends on:** — · **Requirements:** COR-03.
- **Files:** `jobs/handlers/ingest_bars.py`, `services/ingestion.py`, a new domain outcome helper, `services/job_reads.py`, `services/operator_reads.py`.
- **Tasks:**
  1. Closed outcome `complete | partial | failed`, derived from `MarketDataIngestionRun.status` / `symbols_failed`, exposed in `result_summary` and reads. **Not** a Job column (invariant 2).
  2. `sync-symbol-metadata` (H-3):
     - per-symbol results with closed reasons `not_found`, `missing_required_fields`, `invalid_response`, `fetch_error` → `complete | partial | failed`;
     - operation-level failures (auth / `PolygonAuthError`, invalid config, database write, provider unavailable for the whole request) stop the run as `failed`, with no symbol marked synced;
     - replaces today's `raise_for_failures` (any failed ticker → FAILED).
  3. Symbol readiness (R-29): required metadata = `metadata_provider` set, `active`, `market = 'stocks'`, `symbol_type ∈ {CS, ETF}`, `primary_exchange` present. A readiness function returns `ready | not_ready(missing_metadata)` per symbol. Risk rejects candidates for not-ready symbols with a new closed decision code `symbol_not_ready` (`services/risk.py` `RiskDecisionCode`).
- **Acceptance criteria:**
  - The Job `710d46bf` fixture → `partial`.
  - All symbols failed → Job FAILED + `failed` (the Phase 20 behavior is kept).
  - No read returns `succeeded` for a non-empty failed set.
  - A schema test confirms `jobs` is unchanged.
  - Metadata: one unknown ticker → `partial` with `not_found` for it, the others synced; auth failure → `failed`, no symbol marked synced; all tickers fail → FAILED lifecycle.
  - A universe symbol with missing required metadata → readiness `not_ready(missing_metadata)` and its risk candidate rejected `symbol_not_ready`; the other symbols are unaffected.
  - The `RiskDecisionCode` closed-enum test is updated (exactly one new member).

### P20.1-05 — Calendar, evaluation session, execution window (COR-04)

- **Wave 1 · Depends on:** — · **Requirements:** COR-04, F-4, G-2.
- **Files:** `services/calendar.py`, `services/market_data_access.py`, `core/settings.py` (execution policy name, cutoff minutes, coverage horizon, runway threshold), `jobs/handlers/payload_fields.py`, `sync_market_sessions_submission.py`, the `*_submission.py` defaults, `paper_session_submission.py`, `services/execution/submit_orders.py`.
- **Tasks:**
  1. Domain functions returning closed results:
     - `trading_day(now)` → `known(date, phase) | unknown(calendar_data_unavailable)`;
     - `evaluation_session(now, strategy)` → `ready | not_ready(reason) | unknown`;
     - `execution_window(S, policy)` → `open(until) | closed(next_opens_at) | unknown`.

     They're built from persisted `market_sessions` open/close (early closes respected).
  2. `execution_policy = regular_hours_prev_session_v1` as a named, versioned setting (G-2). Eligibility rules as in 03 §3.10.
  3. `paper-session` submit-time rejections: `historical_execution_rejected`, `outside_execution_window`, `evaluation_data_not_ready`, `calendar_data_unavailable`. Paper-session is **not** gated on market hours for research job types.
  4. `sync-market-sessions` may sync ahead up to the coverage horizon (default ≥ 60 sessions, bounded by the calendar library); `ingest-bars` keeps `TO_DATE_IN_FUTURE`.
  5. Session-scoped submission defaults use the evaluation session, with a typed reason when unknown/not ready; backtest defaults are unchanged.
  6. Readiness reports per symbol (bars, history, **metadata** per P20.1-04). The evaluation session's `ready` stays a bar/history rule; a symbol missing metadata is `not_ready(missing_metadata)` individually and never presented as ready for trading.
- **Acceptance criteria:**
  - Clock-injected tests across pre-open / open / cutoff / after-close / weekend / holiday / early close.
  - The calendar ending 2026-03-13 at 2026-09-29 → all three facts `unknown(calendar_data_unavailable)`, and no default of 13 Mar for session-scoped types.
  - A paper session for 13 Mar → `historical_execution_rejected`.
  - At 10:00 on session D with data ready for `previous_session(D)` → eligible.
  - Calendar sync to today + horizon succeeds; bar ingest into the future is still rejected.

### P20.1-06 — Evaluation input manifest (data provenance; G-3, R-25)

- **Wave 1 · Depends on:** — · **Requirements:** new PROV-01 (under COR-04 umbrella).
- **Files:** `services/market_data_access.py` (`bars_for_sessions`, `bars_for_session_date`: a recording hook), `services/risk.py`, strategy `generate_signals` call path (no strategy code change expected), new `services/evaluation_manifest.py`.
- **Tasks:**
  1. During a risk evaluation, record every **read request** made through the shared accessors, not just the rows returned. Each request (accessor, symbol(s), session range or limit, as-of) is stored with the digest of its result, **including empty results**, so a later backfill of a symbol that was entirely missing is detected. Covered reads:
     - bar reads (`bars_for_sessions`, used by all four strategies; verified);
     - missing-bar checks (`missing_bars_for_session`);
     - portfolio valuation prices (`bars_for_session_date` in `services/portfolio.py`);
     - calendar session rows used (latest completed session, session sequence).

     Digests are SHA-256 over canonical `(session_date, open, high, low, close, volume, vwap, trade_count, adjusted, provider)`, plus a digest of the strategy `settings_snapshot` and the manifest version. Stored in the risk run `result_summary` (existing JSON; **no schema change**).
  1b. Time-relative lookups (e.g. latest completed session, session sequences) are recorded with their **resolved as-of bound and result**. Verification re-runs them against that bound, so a calendar sync ahead or the passage of a day doesn't falsely mark the evaluation stale.
  1a. Completeness guard: a boundary test fails if strategy or risk code reads `DailyBar` / `MarketSession` except through the recorded accessors (for example a direct `select(DailyBar)`).
  2. `verify_manifest(run)` re-reads the same symbols and ranges through the same accessors and compares.
     - **Stale** if any value differs, a bar was added or removed, or the settings digest differs.
     - A no-op re-ingest with identical values is **not** stale; `updated_at` is ignored.
  3. The paper-session eligibility check (P20.1-05) and the per-action permission check (P20.1-11) call `verify_manifest`. A mismatch → `evaluation_data_changed` (data) or `strategy_settings_changed` (settings digest).
  4. **Provenance scope (H-2):** the manifest covers source data and signal settings **only**. Current cash, positions, open orders, fills from earlier orders in the same operation, and current risk-limit configuration are **not** in it; they're checked fresh by P20.1-11. The recorded requests are re-executed as recorded (same symbols and ranges), **not** re-derived from today's positions, so a new position from an earlier fill never alters the verification set.
- **Acceptance criteria:**
  - Evaluate → re-ingest identical bars → manifest matches.
  - Evaluate → correct one close price → `evaluation_data_changed`.
  - Evaluate with a missing bar → backfill it → stale.
  - Evaluate with a symbol **entirely absent** → ingest that symbol → stale (29 Sep shape).
  - The boundary test rejects a direct `DailyBar` query in strategy code.
  - Change a strategy parameter → stale (`strategy_settings_changed`).
  - A fill that opens a new position after the evaluation → the manifest **still matches** (valuation reads re-executed as recorded).
  - A risk-limit configuration change → the manifest still matches (it's handled by the fresh risk check).
  - Calendar synced ahead by 60 sessions after the evaluation → the manifest still matches.
  - Query-count test: verification of a 10-symbol × 260-session window uses a bounded number of queries (target ≤ 3).
  - A read-only test confirms manifest verification writes nothing.

### P20.1-07 — Evidence-based attribution and no adoption (COR-05)

- **Wave 2 · Depends on:** 01, 02 · **Requirements:** COR-05, R-3.
- **Files:** `services/reconciliation/matcher.py`, `findings.py`, `report.py`, `services/execution/sync_orders.py` (`_sync_positions_from_broker`), new `services/attribution.py`.
- **Tasks:**
  1. Classify broker orders as `owned` / `recorded_external` / `unrecognized` (origin tags: `platform_format_unverified`, `external_format`, `status_unmapped`, `replaced_by_successor`). "Owned" requires all five evidence conditions (03 §3.4). A mismatch → blocking anomaly (incl. `owned_order_outside_ownership_period`).
  2. Fills take their order's class via `order_id`. Unexplained exposure per symbol = broker quantity − (owned + recorded-external net fills).
  3. Broker sync **no longer creates positions** for untracked broker positions. Local positions derive from owned fills; broker positions are only compared.
  4. Page-cap overflow surfaces as `broker_history_exceeds_cap` (unresolved), never truncation.
- **Acceptance criteria:**
  - A platform-format id with no local record → `unrecognized/platform_format_unverified` (never owned).
  - A local record with a different quantity → anomaly.
  - A prior owner's orders stay owned by that owner and don't block the new owner.
  - A manual order anywhere in history blocks.
  - A sync against a fake broker with an unknown position creates **no** local position.
  - Unexplained exposure ≠ 0 blocks.
  - Existing reconciliation tests keep passing, or are updated with a documented reason.

### P20.1-08 — Account-level sync and reconciliation without an owner (ACCT-01)

- **Wave 2 · Depends on:** 01, 07 · **Requirements:** ACCT-01, D-6.
- **Files:** `jobs/handlers/broker_order_sync*.py`, `reconciliation*.py` (a `scope: account | strategy` payload field; `strategy_id` optional for account scope), `services/execution/sync_orders.py`, `services/reconciliation/report.py`, `jobs/registry.py`.
- **Tasks:**
  1. Account scope:
     - sync updates only known local orders and ingests fills only for known orders;
     - it records a broker-observed **account** snapshot;
     - reconciliation compares broker state with all strategies' local records + recorded external activity.

     Report-only; assigns nothing.
  2. Always available (no owner required), including while trading is blocked. It never lifts a gate itself; only its result is read by gates.
  3. The catalog marks both `broker_effect = reads_broker`.
  4. **Storage (R-31, architectural recommendation; 03 §3.11 S-3):** `strategy_runs.strategy_id` is NOT NULL, so account-level results go to a dedicated `account_reconciliation_runs` record with the same result shape + `job_id` (new migration). They are **never attached to an arbitrary strategy** (J-3). Account snapshots use the existing nullable `account_snapshots.strategy_id` (verified). The recovery predicate, handover checks, analytics `latest_reconciliation` and the reconciliation reads consult both scopes.
- **Acceptance criteria:**
  - With no owner: account sync + reconciliation run, write no position or attribution, and report findings.
  - A test pins that neither job submits orders (fake broker POST count = 0) or changes ownership.
  - The 29 Sep account state (empty lists) → clean account-level result, stored without any strategy reference.
  - A strategy-level reader (e.g. the recovery predicate) sees the account-level run as a qualifying standalone reconciliation.
  - No `strategy_runs` row is written by an account-scope reconciliation (asserted).

### P20.1-09 — Record external activity (EXT-01)

- **Wave 2 · Depends on:** 07, 08 · **Requirements:** EXT-01, E-1.
- **Files:** new `external_broker_activity` model + migration, new `jobs/handlers/record_external_activity*.py`, `jobs/registry.py`, `services/reconciliation/*`.
- **Tasks:**
  1. A Job with a required reason and the listed unrecognized order ids. It re-fetches each order (by id / `client_order_id`) and its fills (activities by `order_id`) from the broker.
  2. Preconditions:
     - every order terminal (a `replaced` order needs its successor included);
     - net external exposure zero in every symbol;
     - otherwise stop with `external_order_not_terminal` / `external_exposure_nonzero` / `broker_record_unavailable`.
  3. Store immutable rows: verified broker snapshot, fills, content hash, origin tag, reason, Job id. No strategy, no position.
  4. In the same handler, run a fresh account-level reconciliation (R-18). The Job outcome is that result.
  5. Every later check re-fetches recorded items and compares hashes; a mismatch → unrecognized again.
- **Acceptance criteria:**
  - Recording an external buy + sell pair (net 0) → the fresh check is clean.
  - A non-terminal external order → rejected with a reason, nothing stored.
  - Non-zero exposure → rejected.
  - Tampered broker data after recording → blocks again.
  - Recording never writes `paper_orders`, `paper_fills`, `positions` or ownership (asserted).
  - Recording alone, with a failing fresh check, leaves the block in place.

### P20.1-10 — Uncertain-outcome recovery and gates (REC-01; COR-02 part 1)

- **Wave 3 · Depends on:** 02, 03, 05, 07, 08 · **Requirements:** REC-01, C-B, D-3, F-1, R-11, R-23.
- **Files:** new `services/recovery.py` (domain read predicate), `orchestration/job_mutations.py` (`_retry_block_for` replaced; fresh-submission gate), `jobs/handlers/paper_session_submission.py`, `api/routes/jobs.py`, a new Job/control to record broker evidence, and a Phase 20 CONTEXT D-19 amendment note (docs, at roadmap-edit time).
- **Tasks:**
  1. Per-intent classification of an uncertain operation:
     - `nothing_submitted` requires execution-path evidence checkable in code:
       - for `paper-session`: **no `paper_execution` run is linked to the Job**. The submission stage's first persisted write is that run, committed with `job_id` before any intent registration (`submit_orders.py:185-197`, "the literal first persisted write"; `job_id` threaded since `31007e9`, before 29 Sep). A linked run → per-intent evidence from the attempt log (new) or order records;
       - for `broker-order-sync`: the job type never submits;
       - zero order rows alone are never sufficient (E-5);
     - **found and verified** (by `broker_order_id` / `client_order_id` via sync or lookup) → resolved, whatever the state: working, partially filled, filled, canceled/expired, rejected, or replaced (successor → unrecognized path). **No broker statement needed** (H-1);
     - **not found while the order could still be live** → unresolved;
     - **not found**, at any time and whatever the evidence → **unresolved (J-1)** until the order is found and verified or proven not sent (2026-10-04: a broker statement no longer resolves). There is no withdrawal.
     - ~~**not found and resubmission wanted** → broker statement → sendable once~~ *(superseded 2026-10-04, round 5: no resubmission; a statement is evidence only).*
  2. Resolution predicate: all intents established (found and verified, `nothing_submitted`, or proven not sent; 2026-10-04 removes `broker_confirmed_not_received`) **and** a fresh, clean standalone reconciliation (account or owner level) after the latest broker-touching Job.
  3. Gates: **every** `paper-session` submission (fresh or retry) for the strategy is rejected while unresolved (`outcome_unresolved`, `reconciliation_required`, `reconciliation_not_clean`). Broker sync is never gated.
  4. Controls (synchronous; reason required; ORCH-07 gated; added to the pinned mutating-route allowlist):
     - (no withdrawal control: W-1 declined);
     - **Record broker statement** (`not_received` | `order_record`).

     Plus the recovery read (R3 in 05) with the evidence package export.
  5. The issue projection inputs `outcome_uncertain_unverified` / `outcome_uncertain_unresolved` use the same predicate (read in Phase 21).
- **Acceptance criteria:**
  - A SUCCEEDED-but-blocking reconciliation never resolves (the 15:46:50 fixture).
  - Order found `filled` after an ambiguous POST → resolved with no broker statement; never re-POSTed.
  - Absence evidence (404 ×2 + full scan + no fills) during validity → still unresolved; resubmission rejected; no route clears it.
  - After close + fresh evidence → **still unresolved**; no route can clear it on time or absence alone.
  - ~~A broker statement `not_received` → resolved; eligible to resend once inside the window only while the operation is open; after End/expiry → resolved, not resent.~~ *(Superseded 2026-10-04 by "Safety correction, round 5" item 1.)* A broker statement `not_received` → recorded as audited evidence only: the intent stays unresolved, the strategy stays blocked (`outcome_unresolved`), `resubmission_permitted` is false (`resubmission_unavailable`), and nothing is resent, whether the operation is open or ended. Complete absence evidence, executor termination, elapsed time, End, expiry, re-evaluation, a new order version and ownership changes do not release it either. Only the broker showing the order (found and verified) or the intent's own attempt history proving it not sent resolves it.
  - End operation with an ambiguous intent → the operation is `terminated`, the intent stays `ambiguous` and blocking, and the recovery read still lists it (J-2).
  - An open order found + clean reconciliation → resolved, but that intent is never resubmitted.
  - A fresh paper-session submission while unresolved → 409.
  - Broker sync while unresolved → allowed, and it doesn't resolve.
  - ~~A recorded broker statement of non-receipt → uncertainty resolved; resending only via P20.1-11 checks while the operation is open.~~ *(Superseded 2026-10-04 by "Safety correction, round 5" item 1.)* A recorded broker statement of non-receipt → uncertainty **not** resolved; no send of that intent is authorized by any path (P20.1-11/15/16 T1 refuses every intent not proven not sent).
  - The 29 Sep jobs → `nothing_submitted` per intent, overall **unresolved** until a fresh clean account-level reconciliation, then resolved.

### P20.1-11 — Execution operation: pause / re-evaluate / terminate, resume, permission (REC-02; COR-02 part 2)

> **Formal plans (split applied 2026-10-03):** this programme plan is delivered by three formal plans: `20.1-11` (persistence, state machine, S1 fencing primitives, real OperationView, R2 reads, End), `20.1-15` (sequential loop and pause points, per-action permission check, `revalidate_pinned_intent`, S2/S3, start-mode gates) and `20.1-16` (Continue mode, run-time takeover, retry resolution, S1 concurrency acceptance, and the proof that no in-doubt intent is ever resent; *2026-10-04: the earlier "resend" assignment is superseded by "Safety correction, round 5" item 1 — there is no resend path*).

- **Wave 3 · Depends on:** 05, 06, 10 · **Requirements:** REC-02, D-2, F-3, G-1, G-4, R-6, R-7, R-24, R-26, R-27.
- **Files:** `services/execution/submit_orders.py` (sequential loop, per-action permission check, pause points, `create_new_version` forbidden on continuation), `services/risk.py` (new `revalidate_pinned_intent`), `jobs/handlers/paper_session*.py` (a "continue" mode resuming an operation; `risk_run_id: null` resolved to a concrete id at first submission), the operation-state persistence (enum extension or a small table + migration), `orchestration/job_mutations.py` (End operation), `api/routes/jobs.py` (typed errors).
- **Tasks:**
  1. The operation record ties the Jobs of one (strategy, evaluation session, pinned risk run). At most one open operation per strategy, enforced by a **database constraint** (a partial unique index over the open states) as well as the typed `operation_open` rejection.
  1a. **Continue session is its own paper-session mode** (`mode: continue`, `operation_id`), with its own `Idempotency-Key`. It is **not** an OPS-07 retry, which applies only to FAILED/CANCELLED Jobs. The continuation checks and every submission run inside the existing (strategy, evaluation session) advisory lock.
  2. State machine `running → paused | requires_reevaluation | terminated | completed`, with the closed reasons and next actions of 03 §3.7 A2.
  3. **Pause** after any broker result whose effects aren't accounted: working order; filled order not yet synced; `ambiguous` → the unresolved path. Remaining intents stay unsent.
  4. **Continue session:**
     - checks: terminal orders; owner sync after they finished; fresh clean standalone reconciliation; manifest match; per-intent permission;
     - skips `submitted` intents;
     - sends only `planned` / `registered_unsent` / `not_sent` (proven not sent) intents with their original ids; an in-doubt intent is never sent (round 5).
  5. `revalidate_pinned_intent`: current risk limits (current configuration) for the pinned (symbol, side, qty) against the **current** portfolio, refreshed after the required sync, **including fills of earlier orders in the same operation**. No signal generation; quantities are never resized. A failure → `requires_reevaluation` (`risk_limit_failed:<code>`). An earlier fill **alone never triggers re-evaluation** (H-2).
  6. **End operation** (explicit; a synchronous control, refused with `operation_running` while a Job of the operation runs) → `terminated/cancelled_by_operator`.
     - **Only unsent** intents → `cancelled_unsent`.
     - `ambiguous` intents stay tracked and blocking, with recovery records retained.
     - `submitted` orders are **not** cancelled at the broker.
     - Trading permission is unchanged. The response lists remaining working orders and unresolved intents (J-2).
  7a. Execution-operation read (R2 in 05): state, reason, next action, intents; this minimal read ships in 20.1 so the interim API is operable. Phase 21 OPR-03 extends it.
  7. Lazy termination (R-27) on the next touch once the window elapses → `terminated/execution_window_elapsed`; unsent → `expired_unsent`.
- **Acceptance criteria:**
  - Three-intent plan, first order stays working → operation `paused`, intents 2–3 **unsent and preserved**.
  - Order fills immediately but isn't synced → `paused` (TL-1).
  - Continue after sync + clean reconciliation → intent 2 sent with its **original** `client_order_id`; intent 1 **not** resubmitted.
  - Kill switch tripped between steps → `paused/kill_switch_tripped`, unsent preserved; after reset + checks → continues.
  - Manifest changed → `requires_reevaluation`; nothing sent; End operation → `cancelled_unsent`.
  - Risk limit fails for the next intent → `requires_reevaluation`, no re-plan, identities unchanged.
  - Intent 1 fills (cash reduced as planned) → Continue → the manifest matches, intent 2 passes the fresh risk check and is sent **unchanged**; no re-evaluation requested.
  - End operation while a continuation Job is running → `operation_running` refusal.
  - Clock past the cutoff → the next touch terminates with `execution_window_elapsed` and `expired_unsent`, for unsent intents only.
  - End or expiry with one working order and one ambiguous intent → operation `terminated`; the working order still blocks (`working_order_commitments_unaccounted`) until terminal and synced; the ambiguous intent still blocks (`outcome_unresolved`). Recovery reads and attempt logs are intact; zero broker cancel calls.
  - A new session request while an operation is open → `operation_open`.
  - `create_new_version` is unreachable on continuation (test).
  - Two concurrent Continue jobs for the same operation → at most one proceeds; no intent is ever submitted twice (the attempt log shows one submission sequence per intent).
  - Inserting a second open operation for a strategy fails at the DB level.

### P20.1-12 — Seeding and handover controls (PAPER-02)

- **Wave 4 · Depends on:** 01, 08, 09, 10, 11 · **Requirements:** PAPER-02, E-2, D-5.
- **Files:** `services/operator_controls.py`, `api/routes/controls.py`, the singleton model, audit via existing `operator_control` run + ExecutionEvent (translated in reads, S8).
- **Tasks:**
  1. Synchronous controls (mutation path 2 as amended in 03 conflict row 23; idempotent by target state; reason required):
     - **Set active paper strategy** (seed from none);
     - **Change active paper strategy** (A → B or A → none).
  2. Checks, from persisted evidence only (no broker call):
     - A1: no queued/running broker-touching Jobs **and no open operation**;
     - A2: all broker orders terminal;
     - A3: flat, with 0 unexplained exposure;
     - A4: no unrecognized items;
     - A5: no unresolved outcome for any strategy;
     - A6: a fresh clean account-level reconciliation after the latest broker-touching Job;
     - A7 (handover): outgoing owner disabled.
  3. The new owner starts **disabled**. The Control Change is recorded.
- **Acceptance criteria:**
  - Each failing check yields a typed refusal naming it.
  - Seeding from none succeeds only after a fresh clean account-level reconciliation.
  - Seeding today requires resolving the 29 Sep outcomes (fixture).
  - Handover with a position, an open order or an open operation is refused.
  - The control succeeds with no worker running (a CTRL-02-style test).
  - Reaffirming the same owner → `changed=false`, audited.

### P20.1-13 — Phase gate: API end-to-end safety scenarios

- **Wave 4 · Depends on:** all · **Requirements:** all 20.1.
- **Scenarios:** **E1–E15 in [05 §3](05-INTERIM-API-OPERATIONS.md)**, executed over HTTP (TestClient) against a scripted fake broker and a real DB. They supersede the list below, which is kept as a summary. The human UAT follows the 05 §2 procedures through the API.
- **Summary** (fake broker + real DB; each is a named test):
  1. Fresh install: no owner → account check → seed → enable → evaluate → session inside the window → one order working → paused → sync → reconcile → continue → completed.
  2. Ambiguous submit (read timeout) → strategy blocked → sync shows the order open → resolved (never resubmitted) → working-order pause → finishes.
  3. Ambiguous submit, order never visible → stays unresolved; no resubmission; ~~a recorded broker statement → eligible only inside the window~~ *(superseded 2026-10-04, round 5 item 1)* a recorded broker statement → still unresolved and nothing is resent (E8 variants A–C).
  4. External manual trade (buy then sell) → blocked → record external activity → fresh check clean → trading allowed.
  5. Data correction after evaluation → `requires_reevaluation` → End → re-evaluate → new operation.
  6. Historical execution request → rejected; backtest of the same date → succeeds.
  7. Handover attempt with an open position → refused; after flat + checks → succeeds; the new owner starts disabled.
- **Acceptance:** all scenarios pass; zero broker POSTs in every scenario where none is expected; UAT script written for the human pass.

### P20.1-14 — Legacy console truthfulness (H-0; smallest compatibility adjustments)

- **Wave 4 · Depends on:** 02, 04, 11, 12 · **Requirements:** H-0 (no temporary control screen).
- **Files:**
  - backend (additive): `api/routes/job_types.py` (`console_submission: "interactive" | "api_only"`); `services/job_reads.py` (`outcome`, `operation {id, state, reason}`); the active-paper-strategy GET (P20.1-01);
  - console: `lib/jobTypeForms.ts`, `components/jobs/new/NewJobView.tsx`, `components/paper/PaperJobShortcuts.tsx`, `components/jobs/detail/JobHeaderPanel.tsx`, `components/jobs/JobsTable.tsx`, `components/controls/*` / `components/strategy/StrategyOverviewPanel.tsx`.
- **Tasks:**
  1. `paper-session`, `record-external-activity` and continue mode are marked `api_only`. The picker shows "Operated through the API in this version" (reusing the existing unmapped-type fallback); `PaperSessionJobForm` is unregistered.
  2. `/paper` "Run paper session" shortcut → removed; notice shown. The "Run reconciliation" / "Sync broker orders" shortcuts **and** the `/jobs/new` reconciliation/broker-sync forms submit `scope: "account"` by default.
  2a. `/paper` reconciliation panel: show the latest reconciliation of either scope with its time. When trading is blocked for another reason (no owner, unresolved outcome), show "Trading blocked: <reason>" instead of "does not block execution".
  3. Job detail: Retry hidden for `api_only` types (notice); other types unchanged.
  4. Jobs list and Job header show **Outcome** next to lifecycle status ("Succeeded · Paused: working order", "Succeeded · Partial"). A non-final operation never gets success styling.
  5. One read-only line on `/controls` and `/strategy`: "Active paper strategy: <name|none> (managed through the API)".
- **Acceptance criteria:**
  - `tests/test_console_api_contract.py` green, plus new tests:
    - a paused operation's Job never renders as plain success;
    - no paper-session start or Retry control is reachable;
    - the shortcuts and the two forms post `scope: "account"`;
    - the reconciliation panel never says "does not block execution" while trading permission is blocked;
    - the active-strategy line renders `none`.
  - No new pages, routes or controls are added to the console (a route-inventory test is unchanged except for removed affordances).
  - **Fallback:** if task 4 can't be done within these components, paper-session rows show "Outcome via API only" and never a success badge (the criterion still holds).

---

## 2. Phase 21 — Operator Read-Model Foundation (v1.3 closes)

**Goal:** 03 §2 as amended in revisions 3–6. Every read is read-only, bounded, returns a server `as_of`, and uses closed enums. No console changes.

| Plan | Depends on | Summary | Key acceptance criteria |
|---|---|---|---|
| **P21-01 Worker heartbeat** (WRK-01/02) | 20.1 (default sequential; optional overlap with 20.1 if you choose) | `worker_heartbeats` table (the only new table in Phase 21); throttled upsert from the run-jobs loop in `jobs/`; graceful `stopped`; retention pruning; `GET /system/worker-health` → `idle \| busy \| unavailable \| unknown` | State-matrix test; no heartbeat ever → `unknown`; stale → `unavailable`; the ORCH-08 boundary test is unchanged; `/ready` is unchanged; schema-delta test pins exactly one new table |
| **P21-02 Overview + harness** (OPR-01, OPR-08) | 20.1 | Trading permission (blockers incl. `no_active_paper_strategy`, `unrecognized_broker_activity`, `outcome_unresolved`, `working_order_commitments_unaccounted`), posture lanes, verdict + next action (closed precedence), environment (mode, broker, mutations flag, active strategy or none), the three calendar facts, account with source/as-of; `as_of` + query-count harness | Permission-parity test (same gate functions as the session); 29 Sep fixture → verdict and blockers as specified; query-count bound asserted |
| **P21-03 Issues** (OPR-02) | P21-02 | Closed rule enum incl. calendar, runway, data, evaluation-outdated (manifest), uncertainty (unverified/unresolved), unrecognized activity, working order, worker, operation state; lane/severity mapping; suppression when the calendar is unknown | Enumeration test (each rule → one lane + severity); 29 Sep fixture → `calendar_out_of_range`, `worker_unknown`, `no_active_paper_strategy`, `outcome_uncertain_unverified` |
| **P21-04 Sessions & operations** (OPR-03) | P21-02 | Pipeline per evaluation session (stages, attempts, records); execution operation (state, reason, next action, intent states); never one "current session" | Historical sessions are never marked executable; a paused operation shows preserved unsent intents; bounded queries |
| **P21-05 Coverage + reconciliation detail** (OPR-04, OPR-05) | P21-02 | Per-symbol coverage for the evaluation session; calendar horizon/runway; reconciliation list/detail with scope (account/owner), classes, origin tags, unexplained exposure, recorded external items | The 29 Sep coverage fixture; the 15:46:50 detail shows buying-power divergence; bounded queries |
| **P21-06 Activity** (OPR-06, **AUD-01**) | P21-02 | One stream: operations, control changes (incl. seeding/handover, external recording), session outcomes, reconciliations; keyset pagination; control rows translated (S8) | Allowlist walk: every mutating route → exactly one Activity item; `operator_control` never appears as a run |
| **P21-07 Catalog & job list** (OPR-07) | P21-02 | `broker_effect`, `session_scoped`, machine-readable prerequisites (owner, recovery predicate, execution window), job-list `outcome` + truncated failure message | Every registered type declares its operator mapping (a new type without one fails the test); the list avoids N+1 (query count) |

---

## 3. v1.4 Operator Console (outline only; detailed after Phase 21)

1. **22 Design** (Paper; no code): inspiration → design system → representative screens (Overview, Next execution with a paused operation) → iteration → finalized language → remaining screens incl. mobile.
2. **23 Shell + Overview** (NOTIF-02 attention badge).
3. **24 Trading:** the pipeline; the execution operation with Continue / End; permission & controls incl. seeding/handover; recovery screens incl. escalation evidence.
4. **25 Portfolio · Market data · Research.**
5. **26 Activity (AUD-02) · System › Technical;** retire old routes against 02 §13.
6. **27 Mobile:** monitoring + emergency controls, per O-7.

The temporary limitations TL-1..TL-8 must be visible in the UI copy where they bite: pause reasons, "Continue session", the working-order block, historical rejection.

---

## 4. Traceability

| Requirement (draft) | Plan |
|---|---|
| PAPER-01 | P20.1-01 |
| PAPER-02 | P20.1-12 |
| COR-01 | P20.1-03 |
| COR-03 | P20.1-04 |
| COR-04 (+ PROV-01) | P20.1-05, P20.1-06 |
| COR-05 | P20.1-07 |
| ACCT-01 | P20.1-08 |
| EXT-01 | P20.1-09 |
| COR-06 | P20.1-02 |
| REC-01 (COR-02 pt 1) | P20.1-10 |
| REC-02 (COR-02 pt 2) | P20.1-11, P20.1-15, P20.1-16 |
| Legacy-console truthfulness (H-0) | P20.1-14 |
| WRK-01/02 | P21-01 |
| OPR-01, OPR-08 | P21-02, P21-08 |
| OPR-02 | P21-03 |
| OPR-03 | P21-04 |
| OPR-04/05 | P21-05 |
| OPR-06, AUD-01 | P21-06 |
| OPR-07 | P21-07 |
| AUD-02, NOTIF-02 | v1.4 (26, 23) |

## Plan-drafting resolutions (2026-10-03)

The formal plans resolve these spec points. Each resolution is written into the plan text; none changes scope. Items marked **review** are planning choices the owner may want to confirm before execution.

| # | Spec point | Resolution in plan | Review? |
|---|---|---|---|
| 1 | 03 §3.6 said a definitive 4xx rejection ends in `submission_failed` | 20.1-02 ends it in `REJECTED` (the existing legal transition), as 20.1-CONTEXT D-12 already specifies ("Other 4xx → rejected"). 03 §3.6 is corrected to match | |
| 2 | 03 §3.4 evidence rule 5 ("owner at registration") has no ownership history before 0022 | 20.1-07 applies rule 5 only to orders registered after the earliest recorded ownership period; earlier orders use rules 1–4 | |
| 3 | 05 E1 used `strategy_not_active_paper_strategy` for the no-owner case | 20.1-13 asserts `no_active_paper_strategy` (CONTEXT D-03) for no owner, and the non-owner code in a second step | |
| 4 | 05 E3 expected `awaiting_reconciliation` for a fill that hasn't been synced yet | 20.1-13 asserts `working_order_commitments_unaccounted` before the sync, then `awaiting_reconciliation` after the sync and before a clean reconciliation | |
| 5 | D-15 says orchestration calls the recovery predicate, but `test_orchestration_boundaries.py` forbids orchestration from importing services | 20.1-10 places the gate in the spec's `validate_payload`, the same pattern 20.1-01 and 20.1-05 use, so submit and retry are both gated, and a replayed idempotency key re-evaluates it | |
| 6 | 03 §3.3 A6 ("after the latest broker-touching Job") is self-referential for the recording Job | 20.1-09 uses the recorded rows' creation time as that Job's effect time | |
| 7 | 05 E15 is a console-rendering scenario, but 20.1-13 is HTTP-only | 20.1-13 asserts the API half and runs 20.1-14's vitest suite as a named verify step | |
| 8 | Interim truthful "trading blocked" reason predates the Phase 21 overview | 20.1-14 adds `trading_blocked_reasons` to the existing `GET /controls/active-paper-strategy` | |
| 9 | 05 M15 cited "M13/M14"; M13 is intentionally absent | M14 is the only broker-evidence control | |
| 10 | 21-02 needs `services/worker_health.py` from 21-01 | 21-02 depends on 21-01 (W2), and 21-03..07 move to W3 | |
| 11 | 02 §10 R1–R6 query-count targets (≤15/12/10/5/1·3/4) | **All targets restored** as total request cost, reused components included; met by planned consolidation and the approved single-statement gate loader R-Q1 (round 3). No exception bounds remain. Totals are planning estimates, verified by measurement during implementation (stop and report if exceeded) | |
| 12 | 02 §3 next-action precedence omits newer rules and verdict precedence isn't defined | **Resolved (round 2):** one decision table (21-02 D-02a) feeds verdict and next action; approved rules 1–7 keep their order; inserted rows are labelled corrections; rule 5 restored to its stage form (covers paused operations); rule-7 contradiction corrected | |
| 13 | `handover_blocked` (02 §5.2) is triggered by "a handover was requested", which nothing stores | **Approved (round 2):** rule removed (23 rules); handover eligibility and reasons stay on the 20.1-12 control read; no placeholder rule, no persistent issue, no request storage | |
| 14 | 02 §10 R1 listed a compact `current_session` and `recent_activity` in the overview | **Restored (round 2):** `evaluation_session_pipeline` (21-02; the compact R3 for the evaluation session, never named current_session) and `recent_activity` (21-08, wave 4) | |
| 15 | List `outcome` vocabulary in 02 vs 20.1 | 21-07 keeps the 20.1 values (20.1-CONTEXT D-28 and the metadata-outcome decision H-3) | |
| 16 | R-8 three-surface status agreement crossed wave-1 plans 01 and 03 | Each plan tests only its own surfaces; 20.1-13 asserts the three-surface agreement | |
| 17 | 20.1-01's GET route would import `services.active_paper_strategy`, which the controls adapter-import test forbids | The GET goes through `OperatorControlService.get_active_paper_strategy_view()`; the test stays unedited | |
| 18 | 02 §10 R1 and R2 carried `?strategy_id=` | **Contract change, documented (round 2):** removed from overview and issues and **rejected** with HTTP 422 `strategy_filter_not_supported`. Strategy filtering is kept on sessions (research-only, never executable), coverage, reconciliations and activity, and on existing `/runs` and analytics | |

### Planning review resolutions (2026-10-03)

Tags: **canonical restored** = brought back to the approved spec; **correction** = internal contradiction or approved rule not met, fixed in plan text; **pending** = a new behavioural rule or contract change awaiting owner confirmation.

| # | Plan | Finding | Resolution | Tag |
|---|---|---|---|---|
| V1 | 20.1-11 | Intent identity has no risk run, but `execution_operation_intents.client_order_id` was globally UNIQUE | Uniqueness per operation; **superseded by round 2 S3** (decision-key guard covering filled and possibly-sent orders) | correction |
| V2 | 20.1-11 | A crash-left `running` operation could not be continued | **Superseded by round 2 S1:** fencing token + advisory lock + attempt-before-POST guard; a running operation is never resumable by state alone | correction |
| V3 | 20.1-11 | Start path did not touch an expired open operation before `create_operation` | touch first in the same transaction; test | correction |
| V4 | 20.1-11 | Continue preconditions (sync + standalone check after terminal orders) were applied to every intent, including a fresh start | checks 2–4 of 03 §3.7 A2 apply only with `continuation=True` | canonical restored |
| V5 | 20.1-11 | `revalidate_pinned_intent` had no price source | **Superseded by round 2 S2:** fresh broker-observed price with freshness and deviation rules; `reference_price` is audit and baseline only | correction |
| V6 | 20.1-11 | End missed queued Continue Jobs (linked only at run time) | also detect queued/running Jobs by payload `operation_id`; row lock | correction |
| V7 | 20.1-11 | The generic exception pause could overwrite `outcome_unresolved` | the more specific pause wins | correction |
| V8 | 20.1-02/10/11 | The single statement-backed resend was unreachable (pre-send guard refuses any log with an ambiguous attempt) | resend authorization bound only by 20.1-11 after `resubmission_permitted`; guard classifies attempts after the statement; one POST; a non-pre_connection result reopens unresolved, no second statement | correction *(superseded 2026-10-04, round 5: no resend path)* |
| V9 | 20.1-10 | `resubmission_permitted` ignored the a–d absence evidence that 03 §3.7 B requires for resubmission; a later appearance vs a statement was undefined | absence evidence gates resubmission (not recording); found_verified supersedes a statement; fresh 404 lookup before the send | canonical restored *(superseded 2026-10-04, round 5: no resend path)* |
| V10 | 20.1-02×10 | `not_sent` / `rejected` intents inside an uncertain-flagged Job would classify `not_found` and stay unresolved forever | recovery consumes the 20.1-02 submission class first: established `not_sent`, `rejected_at_submission`; crash-left PENDING_SUBMISSION is a member | correction |
| V11 | 20.1-10 | Linked run with zero orders undefined; `id_mismatch` had no trigger | `unresolved(execution_path_unproven)`; id_mismatch defined per D-07 | correction |
| V12 | 20.1-02 | 401/403 on the order POST raised AlpacaAuthError → retryable SUBMISSION_FAILED (D-12: other 4xx → rejected) | every 4xx class `rejected` → REJECTED | canonical restored |
| V13 | 20.1-02 | Unbound attempt log fell back to a silent no-op; duplicate-lookup "found" skipped D-07 field checks | fail closed (`AttemptLogNotBoundError`); found requires matching symbol/side/qty/type and created_at ≥ registration, else UNKNOWN `id_mismatch` | correction |
| V14 | 20.1-09 | `order_not_unrecognized` rejected already-recorded orders, contradicting the plan's own idempotent re-record and retry rules; the name is a double negative | renamed `order_owned_by_strategy`, fires only for owned orders; matching recorded items are idempotent no-ops; spec delta to 03 §3.5 / 05 M9 | correction (refusal reason; S4 catalog) |
| V15 | 20.1-11/15 | `risk_run_already_operated` replaced the Phase 20 `noop_existing_orders` for completed evaluations | fires only after `terminated`; completed → `noop_existing_orders`, zero order submissions (agreed); categorized in the S4 code catalog | canonical restored |
| V16 | 20.1-11/15 | Ownership-loss pause reason had a dead-end next action and could look like a bypass | integrity-only pause reason (D-18 amendment); next action End then re-evaluate; race closed by FOR SHARE on submit (20.1-01); open operation keeps A1 blocking handover, End resolves nothing, A5 still applies | correction |
| V17 | 20.1-01/12 | Run-time ownership loss presented as reachable by handover; 20.1-12 test contradicted its own A1 | documented as race/direct-write defense in depth; test reframed; A1 refusal tested | correction |
| V18 | 20.1-11 | `operation_not_paused` (Continue on a non-paused operation; 409 at submit with the current state) | kept; distinct from End's `operation_not_open` (End accepts paused and requires_reevaluation); now evaluated on the effective state (V2) | verified |

**Split applied (2026-10-03).** The former 20.1-11 (33 files, 5 tasks) is split by responsibility into three plans, all REC-02: **11** persistence, state machine, S1 fencing primitives (`acquire_execution`, `authorize_send`), the real OperationView, R2 reads and End (owns migration 0027 and the 6→7 allowlist; wave 7, depends 05/06/10); **15** per-intent permission, `revalidate_pinned_intent`, S2 fresh pricing, S3 executed-key guard, the sequential submission loop with operation creation, the S1 guard call in the single send path and the start-mode submit-time gates (wave 8, depends 11); **16** the Continue mode, run-time continuation with S1 takeover, the no-resend proof (round 5), OPS-07 retry resolution and the S1 concurrency acceptance scenario (wave 9, depends 15). REC-02 maps to all three; 12 stays at wave 8 (needs only 11's effective state; verified, it uses nothing moved); 14 depends on 15 and 16 (wave 10); 13 depends on 15 and 16 (wave 11). 12–14 are not renumbered. 20.1-10 could split into Tasks 1–2 (migration 0026) and 3–4, at the cost of one more serial wave; not recommended unless executor context is a concern. 20.1-02 stays whole. Additions to the original 20.1-11 file list: 15 also lists `services/alpaca.py` and `core/settings.py` (S2 price source and settings); every other file was already in the former list.

### Planning review, round 2 (2026-10-03)

Owner directions applied: all 02 §10 query targets restored as total request cost; Overview content restored; `handover_blocked` removal approved; `?strategy_id=` on Overview and Issues rejected with 422; 20.1-11 split; one verdict/next-action decision table; three safety gaps closed; code catalog finalized.

**Query budgets (total statements per request on the shared engine, reused components included).**

| Read | Target (02 §10) | Planned statements | Depends on |
|---|---|---|---|
| Issues | ≤ 12 | F1 gate 1 · F2 recovery ≤ 2 · F3 reconciliation 1 · F4 calendar 1 · F5 readiness 1 · F6 manifest ≤ 3 · F7 jobs 1 · F8 orders/account 1 · F9 heartbeats 1 = 12 | R-Q1; 20.1-05, 20.1-10, 21-01 pinned component bounds |
| Overview (complete, 21-08) | ≤ 15 | Issues facts 12 · O1 evaluation-session stage inputs 1 · O2 recent activity 2 = 15 | R-Q1 |
| Sessions list/detail | ≤ 10 | S1 window 1 · S2 coverage 1 · F4 1 · F1 1 · F2 ≤ 2 · F3 1 · F6 ≤ 3 = 10 | R-Q1 |
| Coverage | ≤ 5 | C1 calendar 1 · C2 universe 1 · C3 ingestions 1 = 3 | — |
| Reconciliation list / detail | ≤ 1 / ≤ 3 | one UNION statement / D1–D3 = 3 | — |
| Activity | ≤ 4 | A1 union keyset 1 · A2 page enrichment 1 = 2 | — |

**R-Q1 (approved in round 3):** one engine gate loader (`load_trading_gate_state`: kill switch, owner, owner status in one statement) used by `run_paper_session` itself and by the reads, so parity holds by construction. Without it, parity needs the three separate gate callables (+2 statements). (The round-2 exception proposal is withdrawn now that R-Q1 is approved.) Executors stop and report if a bound is exceeded; no bound is raised and no response content is removed.

**Decision table (21-02 D-02a).** Verdict and next action come from one first-matching row. Approved rules 1–7 keep their order; inserted rows are corrections with their reasons stated (unresolved outcome, unknown order state, unrecognized activity, ownership anomaly, no owner, working orders, worker unavailable, operation in progress, other open rules, unknown lanes). Rule 5 is restored to its stage form, which covers paused operations. Rule 7's contradiction is corrected: `all_clear` only when no non-info rule is open.

**Safety gaps.**
- S1 exclusive execution (20.1-11, used by 20.1-15/16): fencing token + advisory lock + attempt-before-POST guard; concurrency acceptance scenario with a reclaimed lease and a terminated lock connection; at most one POST per intent.
- S2 fresh pricing (20.1-15): fresh broker-observed price within a freshness window for every continuation send; pause `price_unavailable` or re-evaluate `price_moved_beyond_tolerance` *(round-2/3 design; superseded: PD-1 approved the pause on 2026-10-04)*; identities unchanged.
- S3 duplicate prevention (20.1-15): decision key (strategy, evaluation session, symbol, side); executed keys (including filled, partially filled and possibly-sent) are never sent again under any operation or evaluation; new intents need an established-unfilled key, a newer risk run and a new client_order_id version; no global uniqueness constraint.

**Storage and scope recommendations (round 2; all approved in round 3 — see below):**
- R-Q1 engine gate loader (code path change in `run_paper_session`, no storage).
- S1 columns `executor_job_id`, `execution_epoch`, `last_guarded_at` on the planned `execution_operations` table (migration 0027, not yet applied anywhere).
- S2 broker latest-trade read (`get_latest_trade`), a new read-only broker dependency, behind a setting that stays off until approved. Without it, prices come only from a live read-only `list_positions()` (held symbols), so **Continue cannot buy a symbol the account does not already hold** (those intents pause `price_unavailable`). *(Superseded in round 3: the latest-trade read is approved and prices every symbol, S2-R3.)*
- R-OWN *(approved in round 3 as SER, with handover and operation creation on the same lock)*: re-check ownership with FOR SHARE inside the Job-insert transaction (`orchestration/job_mutations.submit` validates in a separate session today, verified). Until approved, the run-time ownership gate guarantees no order is sent after a racing handover.
- S3 versioned identity: `build_client_order_id` / `build_intent_hash` gain `intent_version` (version 1 byte-identical to today) so a same-quantity new decision gets a new id (20.1-15; code change, no storage).

**Budget risks (planned, not yet measured; zero slack on overview/issues/sessions):** a `SET TRANSACTION READ ONLY` fallback in `read_only_session_scope` would add one statement per opened session; 20.1-06's ≤ 3 is stated for the bar window, and the whole manifest re-check (calendar and valuation re-reads) is unverified. Either would trigger the stop-and-report rule.

**S1 additions after self-review:** every executor write carries the epoch CAS (a stale executor's late transition affects 0 rows); takeover reads attempts only after its CAS commits; reclaimed Jobs are FAILED and never requeued (verified in `jobs/queue.py`), so every later execution is a new Job that must take over.
- Plan 21-08 (new, wave 4) completes the Overview after the Activity read exists; no scope change beyond the restored content.

### Planning review, round 3 (2026-10-03)

**Approved:** R-Q1 shared gate loader; the read-only broker latest-trade price for every pre-send risk check; executor identity/epoch fields; ownership re-checked at Job insertion with a shared serialization lock. These approve planning choices only; implementation still needs separate authorization.

**Corrections made in this round.**
- **S1-R3, honest fencing (20.1-11 <interfaces>, 20.1-15, 20.1-16).**
  - The pre-POST guard and the attempt row are one committed transaction T1 (operation row locked; epoch, Job, lease and state checked; intent sendable with no outcome-less attempt). The POST happens only after T1 commits.
  - Takeover commits the new epoch first and only then reads attempts. Every outcome-less attempt makes its intent in doubt (UNKNOWN; operation paused/outcome_unresolved).
  - No executor can authorize another submission of an in-doubt intent. ~~The only resend path is the statement path, which now also requires the bounded HTTP request lifetime to have elapsed.~~ *(superseded 2026-10-04, round 5: no resend path)*
  - A stale executor may complete only its own attempt row and record a `late_attempt_outcome` event; every other write it makes is epoch-fenced and affects 0 rows. Order state comes from sync and recovery.
  - New targeted scenario `test_s1_r3_lock_lost_between_attempt_record_and_post`, with a timeout variant.
- **S3-R3, intent identity (20.1-15)** *(superseded by S3-R4 in "Final correction" below)*. The round-2 decision-key rule is withdrawn: it would have silently imposed one order per (strategy, session, symbol, side).
  - **Same logical intent:** one operation intent row; retries and Continue never repeat its executed effect.
  - **Genuinely new intent:** only from an evaluation whose broker-observed portfolio basis postdates the strategy's execution watermark (otherwise `evaluation_predates_executions`). It records `prior_execution_refs` and passes the normal pre-send price and risk checks.
  - A newer evaluation, an ended operation or a new order-ID version is not sufficient on its own.
  - Existing risk codes (`duplicate_open_position`, `no_open_position`, caps) decide what may be added. No temporary restriction is proposed.
- **S2-R3 price (20.1-15):** `GET /v2/stocks/{symbol}/trades/latest`, fields `trade.p` and `trade.t`.
  - Fresh means within `pre_send_price_max_age_seconds` (120) and today's session. The tolerance is `pre_send_max_price_deviation` (0.05) against the evaluation price.
  - Failures pause with a detail code. Quantity and identity never change. It applies to start and Continue, held and not-held symbols.
- **SER (20.1-01, 09, 12, 15):** admission of every broker-touching Job (FOR SHARE), handover (FOR UPDATE) and operation creation (FOR SHARE) serialize on the active_paper_strategy singleton row, locked first.

**Resulting guarantees.**
- **G1:** *(corrected in "Final correction")* no executor can authorize another submission of an intent while its broker outcome is uncertain, whether the attempt's outcome is empty or records a timeout or other ambiguous result.
- **G2:** *(corrected in "Final correction")* given a separate T1 for every HTTP attempt, an executor that lost authority cannot start a new attempt; it has at most the one request it already recorded, which may leave the process arbitrarily late.
- **G3:** every POST has a durable attempt row before it is sent.
- **G4:** a retry or Continue never repeats an executed or possibly executed effect of the same intent.
- **G5:** a new intent exists only from an evaluation that saw every earlier execution, and its pre-send check includes those executions at a fresh price.
- **G6:** admission and handover cannot interleave; ownership changes only with no broker-touching Job queued or running and no open operation. This relies on the singleton row always existing: migration 0022 seeds `id = 1, strategy_id = NULL`, nothing deletes or get-or-creates it, and admission and handover fail closed if it is missing.

**Limitations (stated).**
- **L1:** the one late POST of a stale executor can still reach the broker after takeover and create exposure; the platform has no cancel path.
- **L2:** until recovery classifies it, the strategy is blocked, but the late order itself is not stopped.
- **L3:** Alpaca does not enforce the fencing token and rejects a duplicate `client_order_id` only against an active order, so it is not a second fence.
- **L4:** a kill-switch trip does not stop an in-flight POST.
- **L5:** S2 depends on market-data availability; without a fresh trade, sending pauses. On the `iex` feed a 120 s freshness window can fail for less liquid symbols; the window is a configuration default to check against the strategy universe.
- **L6:** *(corrected in "Final correction")* a process can be suspended for an unbounded time between the final deadline check and the request leaving the socket, so the one recorded request can reach the broker arbitrarily late; the deadline assumes worker and database clocks agree within `clock_skew_margin_seconds`.

**Defence order in the crash scenario.** First line: the recovery gate refuses a product-level Continue while an attempt has no outcome (409 `outcome_unresolved`), so no takeover Job is admitted. Second line: fencing (T1, epoch, deadline) for any path that reaches the send code anyway. The acceptance scenario tests both.

**Consequences surfaced for the owner (consistent with approved decisions, not new decisions):**
- After any execution, a broker sync must record a snapshot before the next evaluation that should trade (`evaluation_predates_executions` otherwise). This is the normal Trade → Sync → next Decide order, not the rejected Sync-before-Trade workaround inside one session. Phase 21 shows it as Decide `outdated(basis_predates_executions)` and as a paper-session prerequisite, so the console never offers a run the backend would refuse.
- A same-identity intent from a NEW evaluation is versioned (new client_order_id) and sent if the pre-send checks pass, instead of Phase 20's cross-evaluation `reuse_existing`. A re-run of the same completed evaluation is still `noop_existing_orders`.
- A recovered intent established as not sent (UNKNOWN after a takeover) becomes retryable again through recovery (UNKNOWN → SUBMISSION_FAILED); End and lazy expiry bump the epoch so no stale write reaches a final operation.

**Storage added by these approvals (all within the planned migration 0027):**
- `execution_operations.executor_job_id`, `execution_epoch`, `last_guarded_at`;
- `order_submission_attempts.execution_epoch`, `executor_job_id`;
- `execution_operation_intents.prior_execution_refs`.

**Open product decisions:** none (both consequences accepted or refined in the final correction below).

### Final correction (2026-10-03)

> *Partly superseded by "Safety correction, round 5" below: the statement-backed resend and `previous_executor_terminated`, the new-intent criterion (a changed fingerprint alone), the L-S3 wording and the owner-visible limitations list.*

**1. Post-execution synchronization (accepted).** A new evaluation authorizes trading only with a verified basis (`verify_evaluation_basis`, 20.1-15 S3-R4). A later snapshot timestamp alone is not enough. The basis snapshot must come from a broker sync that:
- completed after the strategy's execution watermark;
- applied every earlier order's final broker state and ingested its full filled quantity;
- was followed by a clean standalone reconciliation before the evaluation;
- produced basis positions equal to the positions derived from those fills.

Data sources: account snapshots have no Job link, so each broker-order-sync Job records `snapshot_id` and `applied_orders` (broker status, broker filled quantity, applied time) in its result_summary (20.1-08, JSON only); order terminal times come from order_events, ingested quantities from paper_fills. Empty history (no earlier order for the strategy, including a `configured_starting_cash` basis) verifies trivially. The verification result is stored on the operation. On failure the submission is refused with 409 `evaluation_basis_unverified`, with one of these details:

| Detail | Meaning |
|---|---|
| `predates_executions` | the sync did not complete after the execution watermark |
| `executions_not_synced` | an earlier order's final broker state was not applied |
| `fills_not_ingested` | the ingested fill quantity differs from the broker's |
| `reconciliation_missing` | no clean standalone reconciliation between the sync and the evaluation |
| `basis_positions_mismatch` | the basis positions differ from the positions derived from the fills |

Phase 21 shows this as Decide `outdated(basis_not_verified)`, a paper-session prerequisite and an `evaluation_outdated` issue, and recommends the next missing step.

**2. Distinguishing a new intent (concrete criterion).** A new intent requires all three:
- a verified basis;
- risk approval on that basis;
- a `decision_fingerprint` that differs from every earlier intent of the strategy that reached or may have reached the broker (accepted in any later state, broker-rejected, ambiguous). Intents never submitted (planned, registered_unsent, not_sent, cancelled_unsent, expired_unsent) do not block, so the approved End → re-evaluate → new session path stays achievable. The fingerprint is SHA-256 over strategy, session, symbol, side, quantity, a timestamp-free decision-inputs digest (manifest result digests with as-of bounds and time-derived parameters removed, the strategy settings digest, and the current risk-limit configuration digest) and the portfolio-state digest (positions and working orders; cash and timestamps excluded).

A matching fingerprint is `replay_of_earlier_decision`: not registered, not sent. A run in which every candidate is a replay returns `noop_existing_orders`. Run IDs, timestamps, new evaluations and new order IDs never make a decision new.

The other two cases are unchanged:
- **Same intent:** retry and Continue keep the order identity and never repeat its executed effect.
- **Completed replay:** still returns `noop_existing_orders`.

Acceptance tests A1–A5 in 20.1-15 cover:
- unchanged re-evaluation;
- post-fill evaluation, including the unverified-basis refusals;
- partial fills and remaining exposure;
- End followed by re-evaluation;
- completed replay.

Limitations of the current strategy semantics (entry/exit signals, one position per symbol), stated rather than worked around:
- A held symbol is not bought again.
- The unfilled remainder of a partially filled order is never pursued; that would need target-position semantics.
- *(Price part superseded by round 5 item 5 and PD-1, approved 2026-10-04; the risk-failure part is superseded by round 5 item 5's End → fresh evaluation rule.)* ~~Sizing uses the evaluation session's closing data, so after `price_moved_beyond_tolerance` or a price-dependent risk failure an unchanged re-evaluation is refused again at the pre-send check (no sends); trading resumes when data, settings, risk policy or portfolio change, or on the next evaluation session.~~ (See L-S3 corrected in round 5.)

**3. Deadline and uncertainty (corrected).**
- T1 authorizes a send only when the intent's broker outcome is certainly "not sent". An empty outcome, a recorded timeout or any other ambiguous result is uncertain. Recovery must not report the intent unresolved.
- The deadline check stops a request only if the process notices the expiry before handing the request to the HTTP client. A suspension after the final check can last any length of time, so the one recorded request may still leave late. No sub-millisecond claim remains.
- Guarantees:
  - every attempt is durably recorded before its request can leave the process;
  - no executor can authorize another submission of an intent while its broker outcome is uncertain;
  - a stale executor cannot start a new attempt.
- Limitations:
  - the one recorded request can reach the broker arbitrarily late and cannot be cancelled by the platform;
  - ~~a statement-backed resend therefore also requires the operator's recorded confirmation that the previous executor's process is terminated (optional boolean `previous_executor_terminated` on the broker-statement body, default false, required true for a resend; 05 M14);~~ *(superseded round 5: no resend)*
  - ~~if the old request still arrives after an accepted resend, the result is a duplicate the platform cannot prevent.~~ *(superseded round 5: no resend exists, so this case cannot arise)*
- New acceptance tests:
  - `test_s1_suspension_after_final_deadline_check`;
  - `test_s1_recorded_timeout_then_takeover`.
  Neither authorizes a second submission of the uncertain intent.

**Budget risk:** the read path's call of `verify_evaluation_basis` must be fed by the shared statements F3/F7/F8 (21-02 D-08); if it cannot, the executor stops and reports.

**Tests adjusted:** 20.1-13 scenarios that start after earlier orders (E5, E6, E9, E11, E12) perform sync + clean reconciliation before the new evaluation; existing Phase 20 suites keep same-risk-run behaviour and add the verification step for new evaluations (20.1-15).

**Storage added (planned migration 0027 only):**
- `execution_operations.basis_verification`;
- `execution_operation_intents.decision_fingerprint`;
- `order_submission_attempts.authorization_deadline`, alongside the round-3 fields.

**Unresolved blockers:** none in the plans.

~~**Owner-visible limitations to accept with execution authorization:**~~ *(superseded: see the explicit accepted limitations in round 5)*

### Safety correction, round 5 (2026-10-04) — accepted; propagated (item 5's price pause was held as PD-1 in round 6 and approved 2026-10-04)

Supersedes the conflicting parts of "Final correction" items 2 and 3:
- the optional `previous_executor_terminated` flag;
- the statement-backed resend;
- "a risk-limit change alone → new intent";
- "corrected bars → new intents";
- the "duplicate after an accepted resend" limitation;
- the L-S3 wording.

It also supersedes the approved non-receipt release rule (D-14, REC-01, TL-4 and ROADMAP 20.1 criterion 4).

Propagated to:
- plans 20.1-02, 10, 11, 12, 13, 15 and 16;
- 20.1-CONTEXT;
- 03 (annotations) and the 05 runbook;
- REQUIREMENTS REC-01/REC-02;
- ROADMAP criteria 4–5, TL-4/10/11 and the 20.1-16 title;
- Phase 21 plans 21-02, 21-03, 21-04 and 21-CONTEXT.

**1. Unresolved submissions stay blocked (accepted temporary limitation TL-4, amended).**
- **Guarantees, stated separately:**
  - **G-late:** one durably recorded original request may reach the broker late. That is a platform boundary, not a duplicate.
  - **G-no-second:** while that original may still produce an execution, no other request is authorized for the strategy. That covers a retry, a new intent and a new order version, under any operation, evaluation or owner.
- **Evidence that would authorize a resend:** proof that the already-sent request can no longer reach the broker and produce an execution.
- **What does not count, alone or together:**
  - an operator flag;
  - verified executor termination, which does not retract a request that has already left the process;
  - a `not_received` broker statement, which shows only that the broker has not (yet) seen the request;
  - absence evidence a–d;
  - elapsed time and deadlines.
- **No mechanism available today provides that proof.** L3: client_order_id uniqueness is enforced only against active orders, so it is not a fence. Resubmission is therefore unavailable in the initial scope:
  - `previous_executor_terminated` is removed, and the broker-statement body is `{statement, reference, reason}`;
  - `resubmission_permitted` is always False with reason `resubmission_unavailable`;
  - the 20.1-02 resend authorization is removed;
  - the settings `submit_request_max_lifetime_seconds` and `clock_skew_margin_seconds` are dropped;
  - the intent state `broker_confirmed_not_received` is removed (8 intent states).
- **Broker statements** (both kinds) remain as audited evidence: an operator_control run, append-only recovery records, listed in R3 and in Activity. They resolve nothing.
- **Resolution comes only from:**
  - broker evidence: found and verified by client_order_id in any state (H-1), or a recorded broker 4xx rejection;
  - the intent's own attempt history proving it not sent.
- **Without that evidence:**
  - the strategy stays blocked with no product-level release;
  - End, expiry, re-evaluation, a new operation, a new order version (`version_bypass_refused`) and ownership seeding/handover/release (A5) never bypass the block;
  - recovery stays available after End.
- **Proven not sent (precise):** positive evidence that the request never left the process, recorded by the executor that created the attempt:
  - `pre_connection`: httpx ConnectError/ConnectTimeout/PoolTimeout, raised before a connection exists and so before any request byte is written;
  - `deadline_expired`: the wall-clock check refused to hand the request to the HTTP client.

  Further rules:
  - A NULL outcome (crash, suspension, lost process) is uncertainty, not proof.
  - A timeout or error after connect, a 5xx/429, `exists_reported`, accepted or rejected are not proof either.
  - Every attempt in the intent's whole history must be proven not sent. Zero attempts counts only for an intent registered under the attempt-log invariant (operation-bound or with at least one attempt row), because only there does G3 make a POST without an earlier committed attempt row impossible. A legacy order (no attempt rows, no operation; e.g. Phase 20 orders) is never proven not sent: it is established by broker evidence, otherwise UNKNOWN/PENDING_SUBMISSION is unestablished and SUBMISSION_FAILED counts as 'may have reached the broker' for replay, TL-10 and version + 1 (`test_legacy_order_without_attempts_is_never_proven_not_sent`).
  - "Reached or may have reached the broker" = an attempt not proven not sent, broker evidence on the order (broker_order_id, a broker-applied status, fills), or a legacy order without proof.
  - One proven-not-sent attempt never resolves an earlier or later ambiguous attempt; `classify_submission` precedence is ambiguous first.
- **New acceptance tests:**
  - 20.1-16 S-c `test_s1_delayed_request_executor_terminated_statement_recorded_no_resend`:
    1. The request has left the process and the broker holds it.
    2. The executor is terminated, complete absence evidence is collected, and `not_received` is recorded.
    3. Every send path is refused: Continue, a new evaluation, End followed by a new evaluation, handover (A5), version + 1 and T1. POST count stays 1.
    4. The held request is released, sync finds it, and the outcome resolves through the original only.
  - 20.1-13 E8 variants A–C.
  - 20.1-12 `test_a5_not_received_statement_does_not_unblock_handover`.
  - 20.1-10 `test_one_proven_not_sent_attempt_never_resolves_an_earlier_ambiguous_attempt`.

**2. A changed fingerprint is not a new intent.** The fingerprint is replay detection only. A new intent must pass, in order (20.1-15 S3-R4), with the first failing check deciding the outcome:

| # | Check | Failing outcome |
|---|---|---|
| 1 | no unresolved submission of the strategy | 409 `outcome_unresolved` / `blocked_outcome_unresolved`, zero POST |
| 2 | verified basis | 409 `evaluation_basis_unverified(<detail>)` |
| 3 | no working order on the symbol | `working_order_commitments_unaccounted` |
| 4 | fingerprint differs from every broker-reaching intent | `replay_of_earlier_decision` |
| 5 | action justified by the strategy's semantics against the verified portfolio (entry only when flat, exit only when held, exit quantity = the whole verified position) | `duplicate_open_position` / `no_open_position` (an approved exit's quantity is the whole verified position) |
| 6 | session allowance not consumed (TL-10) | `action_already_submitted` |
| 7 | risk approval, revalidated at the fresh price | risk codes / `risk_limit_failed:<code>` |

- A justified candidate becomes exactly one new intent.
- A new order version is created only when every earlier version of that identity is proven not sent or never reached the broker.
- Removed expectations: "risk-policy change alone → new order" and "corrected bars → new orders for an already-submitted action".
- **New scenarios:**
  - **N1:** the fingerprint changes (risk config, corrected bars, an unrelated portfolio change) but no action is justified → zero POST.
  - **N2:** a justified later-session exit → exactly one POST; a re-run → replay.
  - **N3:** an unresolved earlier submission blocks despite a changed fingerprint, including on another symbol; no version + 1.
  - **N4:** pricing.

**3. Session allowance (accepted temporary product limitation TL-10; initial scope, not a permanent platform invariant).** At most one broker-reaching action per (strategy, evaluation session, symbol, side).
- **Consumed by** any earlier intent with that key, under any operation, risk run, owner period or intent version, that reached or may have reached the broker (an attempt not proven not sent, broker evidence, or a legacy order without proof):
  - broker-accepted in any later state: working, partially filled, filled, canceled, expired, replaced;
  - broker-rejected at submission (4xx);
  - ambiguous. Unresolved intents are also under the stronger TL-4 block.
- **Not consumed by:** planned, registered_unsent, proven-not-sent, cancelled_unsent and expired_unsent intents. The approved End → re-evaluate → new session path therefore stays available for orders never sent.
- **Partially filled exit:** another sell of that symbol in that evaluation session is unavailable even though a position remains.

**4. Partial-fill remainders are not pursued automatically (accepted temporary limitation TL-11).**
- The remaining position is:
  - preserved in the broker snapshot and the verified basis;
  - exposed: 21-04 Sessions shows requested, filled and remaining quantity per intent, and the Overview account shows positions;
  - included in every current risk check (position count, allocation, cash).
- Verified from the code: `services/risk.py` plus each strategy's signal rule. The signals are stateless level conditions of the bars, independent of the position.

| Strategy | Partial buy remainder | Partial sell remainder (later evaluation sessions) |
|---|---|---|
| trend_following_daily | never topped up (`duplicate_open_position`); the held part exits normally | EXIT whenever close < SMA_exit (checked before entry) → sold on the next session where that holds |
| time_series_momentum_daily | same | EXIT in every session that is not momentum-positive → sold on the next such session; while positive, LONG is rejected and the remainder is held |
| donchian_breakout_daily | same | EXIT only while close < exit-channel low; inside the channel FLAT → held until a later breakdown |
| rsi_mean_reversion_daily | same | EXIT only while RSI > overbought; neutral FLAT, oversold LONG rejected → can be held for many sessions |

The per-strategy behaviour is asserted by `test_partial_exit_remainder_per_strategy` (20.1-15 A3).

**5. Pricing (corrected).**
- A settings change or a changed fingerprint alone does not prove a new intent. It can still enable valid trading when checks 1–7 hold.
- **Approved 2026-10-04 (PD-1, canonical record in "Planning correction, round 6"):** `price_moved_beyond_tolerance` becomes a pause reason; it was a re-evaluation reason. Its next action is `wait_for_price_then_continue`. Continue re-runs the fresh-price check and sends the same pinned intent unchanged once the price is back within tolerance and the revalidation at that price passes. Nothing is re-planned and no identity changes.
- After `risk_limit_failed:<code>` at the fresh price (requires_reevaluation), End followed by a fresh evaluation may authorize a never-sent action with no data, settings or portfolio change. A never-sent intent is neither a replay nor consumes the allowance, and the fresh pre-send check decides.
- Remaining strategy limitation L-S1: a held symbol is never bought again (no pyramiding).
- **L-S3 (corrected):** quantity and the deviation baseline come from the evaluation session's close. If the fresh price stays outside tolerance for the whole execution window, the action is not taken in that session. Re-evaluating the same session reuses the same close, so it helps only once the fresh price passes again; no data, settings or portfolio change is required, and none by itself overrides the fresh-price check.
- ~~*Interpretation for review:*~~ *(Resolved 2026-10-04: PD-1 approved the pause; the alternative below is superseded history.)* Moving `price_moved_beyond_tolerance` from a re-evaluation reason to a pause reason is this round's reading of "continuation may become eligible under the existing checks". It changes the state machine and the planned 0027 CHECK sets (not yet applied). ~~The alternative keeps it a re-evaluation reason, and recovery then goes through End plus an unchanged re-evaluation, which this round also permits.~~ *(superseded by the PD-1 approval)*

**Storage:** none added. The new codes and checks use the planned migration-0027 columns:
- `decision_fingerprint`, `prior_execution_refs`, `basis_verification`;
- the attempt log.

The CHECK sets change inside the not-yet-applied 0027/0026 designs:
- paused reasons 12, re-evaluation reasons 3 families (PD-1 approved 2026-10-04);
- intent states 8;
- recovery classifications without `broker_confirmed_not_received`.

**Accepted temporary limitations for the initial scope (each stated; execution authorization covers nothing unstated):**
- G-late;
- TL-4 (amended);
- TL-10;
- TL-11;
- L-S1;
- L-S3 (corrected).

~~**Unresolved blockers:** none.~~ *(Corrected 2026-10-04, round 6: that statement was premature.)* ~~**Unresolved blockers:** PD-1 (below) blocks execution of 20.1-11 and every plan that depends on it (20.1-12, 13, 14, 15, 16) and of 21-02 onward.~~ *(Resolved 2026-10-04: PD-1 approved.)* **Unresolved product blockers:** none. Implementation still needs separate authorization.

### Planning correction, round 6 (2026-10-04) — review findings applied to planning documents only

A read-only review of all 24 formal plans (20.1-01..16, 21-01..08) confirmed the dependency graph, wave order, declared same-wave file ownership, migration sequence and query budgets, but found active instructions that contradicted round 5. The round-5 "Unresolved blockers: none" was premature. This round changes planning documents and planned acceptance criteria only: no production code, tests, schema, migrations or UI; nothing is executed and the broker is not contacted. It is **not** execution authorization.

Round-5 rules preserved unchanged: G-late and G-no-second; a `not_received` statement is audited evidence only; complete absence evidence, executor termination, elapsed time, End, expiry, re-evaluation, new versions and ownership changes never release a never-found order; no in-doubt submission is resent while the original may still execute; "proven not sent" needs positive evidence over the whole attempt history; TL-10, TL-11, L-S1, L-S3 (corrected).

1. **No active non-receipt release or resend instruction remains.** Corrected: 20.1-10 Task 4 route tests (`test_not_received_statement_route_is_evidence_only`), threat T-20.1-10-01 and success criteria; 20.1-16 success criteria; this document's P20.1-10 acceptance bullets, the P20.1-11 split note (20.1-16 has no resend work) and P20.1-13 summary item 3; 03 J-1, R-28 and §3.7 B escalation step 5 (whole step struck); 05 R3 row, M15 and the final-correction amendment. Historical text is kept only struck through and marked superseded with a pointer to round 5 item 1. The proven-not-sent sends (20.1-10 liveness rule, 20.1-16 variant v2) are not resends and stay.
2. **`deadline_expired` is consistent.** 20.1-02 (owner of migration 0023) defines six attempt outcome classes, the 0023 CHECK, `classify_submission` and `derive_intent_state` with it, plus exact-set, migration and precedence tests (`[deadline_expired]` → not_sent; `[ambiguous, deadline_expired]` and `[NULL, deadline_expired]` → ambiguous). Only the send path of the executor that created the attempt row writes it (20.1-15; if that executor has lost authority, through 20.1-11's `complete_attempt_late`, which accepts it only from that executor, 20.1-16 v3); recovery, takeover and other executors never do. No additional migration; 0027 does not touch the outcome CHECK. 03 §3.6/§3.7 A2 and P20.1-02 above list it.
3. **Ambiguous-submission runbook** (05 §2 procedure 3): explicit branches. While any intent is unresolved, a clean reconciliation does not release the block. Trading becomes eligible only after every intent is established under REC-01, then a fresh clean standalone reconciliation, then every other applicable gate. Broker sync stays available throughout, including after End.
4. **Price pause** — recorded as PD-1 below (labelled proposed in round 6; approved 2026-10-04 and propagated as approved).
5. **Human UAT** (20.1-13): the generated `20.1-HUMAN-UAT.md` covers TL-1..TL-11, with operator-visible expectations for TL-10 and TL-11 through Phase 20.1 surfaces, including a partially filled exit whose remainder cannot be sold again in the same evaluation session.
6. **Phase 21 Sessions** (21-04): planned read-model and HTTP scenarios for a consumed allowance (broker-rejected consumes it; proven-not-sent does not; a changed fingerprint does not bypass it; filled and remaining quantities for partial fills), read from persisted write-side evidence only; the read model computes no allowance or fingerprint. Query budget unchanged (≤ 10). 20.1-15 records per-candidate dispositions in every run summary, not only no-op runs.

#### PD-1 — `price_moved_beyond_tolerance`: pause (APPROVED 2026-10-04)

- **Status: approved by the user on 2026-10-04 (planning decision only; implementation authorization remains separate).** This entry is the canonical decision record.
- **Approved behaviour:**
  - If the fresh pre-send price deviates from `reference_price` beyond the configured tolerance, the operation pauses with `paused/price_moved_beyond_tolerance` and next action `wait_for_price_then_continue`.
  - Nothing is sent, and unsent intents are preserved with their identity unchanged.
  - An explicit Continue may send **the same pinned intent**, once the price is back within tolerance and every applicable check passes again. The first failing check decides the outcome.
- **What the send keeps:** the same intent row, `client_order_id`, symbol, side, quantity and identity. There is no replanning, no resizing and no new order version.
- **Checks rerun by Continue:**
  - **freshness and price:** a fresh latest-trade price within the freshness window, within tolerance;
  - **permission:** owner and enabled, kill switch armed, execution window open, per-intent `check_intent_permission`;
  - **provenance:** the evaluation manifest matches;
  - **recovery:** no unresolved earlier submission for the strategy, and the intent is unsent or proven not sent;
  - **reconciliation:** submitted orders are terminal, a sync follows them, and a fresh clean standalone reconciliation follows that;
  - **risk:** `revalidate_pinned_intent` passes at the fresh price;
  - **allowance:** the TL-10 allowance for (strategy, evaluation session, symbol, side) is not consumed.
- **Limits:**
  - **Re-evaluation still required:** changed evaluation inputs or strategy settings still lead to `requires_reevaluation` (`evaluation_data_changed` / `strategy_settings_changed`), never to a Continue send.
  - **Window expiry:** expiry terminates the operation under the existing lazy-expiry rules (D-21; `execution_window_elapsed` / `evaluation_superseded`, unsent intents → `expired_unsent`). A price that recovers after the window does not override expiry.
  - **No resend:** any unresolved earlier submission remains blocking (`outcome_unresolved`). Continue never authorizes resending an in-doubt intent (round 5 item 1, G-no-second).
- **Consequences:** the planned (not yet applied) 0027 CHECK sets keep 12 paused reasons and 3 re-evaluation reason families. `NextAction` keeps `wait_for_price_then_continue` for `price_unavailable` and `price_moved_beyond_tolerance`. 21-02 maps the pause to that next action.
- ~~**Alternative (the round-3 design):** `price_moved_beyond_tolerance` stays a re-evaluation reason (`requires_reevaluation`, next action `end_operation_then_reevaluate`)…~~ *(Superseded 2026-10-04 by this approval; kept as history.)*
- ~~**Execution gate:** before Task 1 of 20.1-11, 20.1-15, 20.1-16 or 21-02, the executor checks that PD-1 is recorded here as decided…~~ *(Removed 2026-10-04: PD-1 is decided; the STOP gates are removed from the plans.)*
- **Accepted supporting corrections (2026-10-04):**
  1. Every paper-session run summary persists the per-candidate dispositions (20.1-15; read by 21-04).
  2. `deadline_expired` may be recorded only by the executor that created the attempt, including through the guarded late-completion path `complete_attempt_late` (20.1-02 / 20.1-11 / 20.1-16 v3).
- **Readiness:** no open product decision remains in the Phase 20.1/21 planning set. Implementation still needs separate authorization.

#### Validation of this round (documents only)

These are **document** checks only. None of the planned implementation tests named in the plans has been written or run, and no code, schema or migration exists for them.

- **Plan parsing:** `gsd-tools verify plan-structure` passes 24/24 plans (no errors, no warnings). Frontmatter parses for all 24.
- **Dependencies and waves:** every `depends_on` resolves; there are no cycles; every plan's wave ≥ 1 + its in-phase dependencies' maximum wave. Unchanged: no frontmatter dependency, wave or `files_modified` entry was edited, apart from the inserted must_have truths.
- **Declared same-wave file overlap:** none.
- **Migration ownership:** 0022 → 20.1-01, 0023 → 20.1-02, 0024 → 20.1-08, 0025 → 20.1-09, 0026 → 20.1-10, 0027 → 20.1-11, 0028 → 20.1-18, 0029 → 20.1-27, 0030 → 21-01 (renumbered 2026-10-05 from 0028 → 21-01, which collided with 20.1-18's attempt-log migration). Each migration has exactly one owner, and the chain 0021 → 0030 is linear. `deadline_expired` adds no migration.
- **Requirements traceability:** every plan requirement exists in REQUIREMENTS.md and its traceability table. All 13 Phase 20.1 and 11 Phase 21 roadmap requirements are covered inside their phase.
- **`gsd-tools validate consistency`:** passed. Its 6 warnings ("Phase 17–21 exists on disk but not in ROADMAP.md") affect Phases 17–20 equally and predate this round.
- **Stale release/resend scan:** windows of ±110 characters around non-receipt, statement, resend, resubmit, re-POST and withdraw terms that also contain a release term (resolve, release, unblock, lift, resume, eligible, sendable, permit, once, again, clear, authorize). There were 236 distinct windows across ROADMAP, REQUIREMENTS, PROJECT, STATE, 20-CONTEXT, every 20.1/21 plan and context file, and research 01–05. Each was inspected. Every one is a negation, an evidence-only statement, a struck-through superseded passage with its pointer, a test name asserting no resend, a found-order resolution, or the proven-not-sent path. No active release or resend instruction remains.
- **Price-pause label scan (at round 6, before approval):** every line that mentioned `price_moved_beyond_tolerance`, `wait_for_price_then_continue` or the pause wording carried a PD-1 / proposed / pending / alternative label. *(Superseded by the PD-1 approval on 2026-10-04; those labels now read approved.)*
- **Query budgets:** unchanged by construction. No edit changed a budget figure or added a statement: 21-04 still totals 1+1+1+1+2+1+3 = 10, and the new C1–C4 data rides in the existing S1 lateral JSON. There is no pre-round baseline diff, because the phase directories are untracked in git.

### PD-1 approval, propagation (2026-10-04)

The user approved PD-1's pause behaviour and both supporting corrections. This round records approvals and edits planning documents only. Implementation authorization remains separate: no production code, tests, schema, migrations or UI, and no broker contact.

Propagated as approved:
- this record (PD-1 above; round 5 item 5 and the round-6 blocker line);
- 20.1-CONTEXT and 21-CONTEXT;
- ROADMAP 20.1 criterion 5;
- REQUIREMENTS REC-02 and its footer;
- 03 §0.3 and its amendment notes;
- the 05 runbook amendment and the M11 row;
- PROJECT.md and STATE.md;
- the S2-R3 definitions, code catalogs, closed sets and tests in 20.1-11/15/16;
- 21-02.

The PD-1 STOP gates are removed from 20.1-11, 20.1-15, 20.1-16 and 21-02. Earlier alternatives remain only as struck-through, superseded history. The validation results are in the PD-1 approval report.

