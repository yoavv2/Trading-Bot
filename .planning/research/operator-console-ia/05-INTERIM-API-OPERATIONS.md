# Interim Operating Path — HTTP API until the v1.4 Operator Console

_Date: 2026-09-30 · Status: **planning draft** (describes the API that Phase 20.1 will deliver; none of the new routes exist yet) · Decision H-0 (option a) · Specs: [03-PLANNING-CHANGES.md](03-PLANNING-CHANGES.md) · Plans: [04-IMPLEMENTATION-PLANS.md](04-IMPLEMENTATION-PLANS.md)_

Between Phase 20.1 and the v1.4 console, the new paper-trading controls are operated **only through the HTTP API**. The existing console stays truthful but read-mostly for paper trading (§4).

**Common rules:**
- Every mutation is gated by `orchestration.mutations_enabled` (ORCH-07; 403 `mutations_disabled` otherwise).
- Job submissions need an `Idempotency-Key`.
- Controls are idempotent by target state and require a `reason`.
- Every response carries typed error codes (closed enums).

Paths are proposed. Existing routes keep their behavior; changes are additive.

---

## 1. Actions

### 1.1 Inspect state (read-only; always available)

| # | Action | Route (proposed) | Returns |
|---|---|---|---|
| R1 | Active paper strategy + seeding/handover checks | `GET /api/v1/controls/active-paper-strategy` | owner or `null`, `since`, and the check list A1–A7 with pass/fail and evidence refs |
| R2 | Execution operation | `GET /api/v1/execution-operations?strategy_id=` · `GET /api/v1/execution-operations/{id}` | state (`running`, `paused`, `requires_reevaluation`, `terminated`, `completed`), closed reason, **next action**, intents with states (`planned`, `registered_unsent`, `not_sent`, `submitted`, `ambiguous`, `rejected`, `expired_unsent`, `cancelled_unsent`; round 5 removed `broker_confirmed_not_received`); after End/expiry, `ambiguous` intents and working orders remain listed with their blocking effect, linked Jobs |
| R3 | Recovery status of an uncertain Job | `GET /api/v1/jobs/{job_id}/recovery` | per-intent classification, absence-evidence items a–d with timestamps, `resubmission_permitted` (always `false` with reason `resubmission_unavailable` in this version; *amended 2026-10-04: the earlier "only after a broker statement, while the operation is open" is superseded — no statement, evidence, elapsed time or executor termination permits a resend*), any recorded broker statements (evidence only), and the evidence package for a broker inquiry. Available before and **after** the operation ends |
| R4 | Calendar facts | `GET /api/v1/market-data/calendar-state` | trading day, evaluation session (readiness per symbol incl. metadata), execution window; each with `unknown(calendar_data_unavailable)` |
| R5 | Latest reconciliation (account or strategy scope) | existing `GET /api/v1/runs?run_type=reconciliation`, plus `scope` and classification fields (additive) | findings, unexplained exposure, unrecognized items with origin tags |
| R6 | Jobs | existing `GET /api/v1/jobs[/{id}]`, plus additive `outcome` (batch: `complete/partial/failed`) and `operation` (`{id, state, reason}` for paper sessions) | lifecycle status **and** outcome, kept distinct |

### 1.2 Mutations

| # | Action | Route / Job | Prerequisites | Outcomes (typed) | Recovery / next step |
|---|---|---|---|---|---|
| M1 | **Sync calendar ahead** | Job `sync-market-sessions` `{from_date, to_date ≤ today + horizon}` | none | `complete` / `failed`; `date_range_out_of_calendar_range` | Re-run with a range inside the library bounds |
| M2 | **Ingest bars** | Job `ingest-bars` | none | `complete` / `partial` (per-symbol failures) / `failed` | Re-ingest the failed symbols; check R4 readiness |
| M3 | **Sync symbol metadata** | Job `sync-symbol-metadata` | none | `complete` / `partial` (per-symbol reasons `not_found`, `missing_required_fields`, `invalid_response`, `fetch_error`) / `failed` (operation-level: auth, config, database, provider unavailable) | Fix provider or config for operation-level failures; symbol-level failures leave that symbol `not_ready(missing_metadata)` |
| M4 | **Account-level broker sync** | Job `broker-order-sync` `{scope: "account"}` | none (works with no owner and while trading is blocked) | `complete`; updates only known orders and fills and records an account snapshot; never adopts | Follow with M5 |
| M5 | **Account-level reconciliation** | Job `reconciliation` `{scope: "account"}` | none | clean / blocking with findings: `unrecognized` (origin tags), anomalies, unexplained exposure, divergence | Unrecognized → M9; anomalies → investigate (the Technical record) |
| M6 | **Seed / change the active paper strategy** | `PUT /api/v1/controls/active-paper-strategy` `{strategy_id \| null, reason}` | A1 no broker-touching Job queued or running, no open operation · A2 all broker orders terminal · A3 flat, 0 unexplained exposure · A4 no unrecognized items · A5 no unresolved outcomes · A6 a fresh clean account-level reconciliation after the latest broker-touching Job · A7 (handover) the outgoing owner is disabled | `changed` / `unchanged`; refusal names the failing check (`check_failed:A3`…) | Satisfy the named check (M4/M5; for A5 follow procedure 3 — a recorded M14 statement never satisfies A5; wait for orders) and retry. **The new owner starts disabled** |
| M7 | **Enable / disable a strategy** | existing `PUT /api/v1/controls/strategies/{id}` | — | `changed` / `unchanged` | Enabling matters only for the active paper strategy |
| M8 | **Evaluate** | Job `risk-evaluation` `{strategy_id, as_of_session}` | none (research allowed for any strategy) | run with decisions and an **input manifest** | — |
| M9 | **Record external activity** | Job `record-external-activity` `{order_ids[], reason}` | listed orders are unrecognized; each is **terminal** at the broker; net external exposure is **zero** | recorded **and** fresh account-level reconciliation result, in the same Job; or `external_order_not_terminal` / `external_exposure_nonzero` / `broker_record_unavailable` / `order_owned_by_strategy` (added 2026-10-03, 04 V14; an already-recorded order is an idempotent no-op) | Neutralize at the broker first (itself external activity), then record everything. Recording never clears a finding by itself |
| M10 | **Start paper session** | Job `paper-session` `{strategy_id, as_of_session (= evaluation session), risk_run_id}` | owner = strategy, enabled, kill switch armed · trading day `open` · evaluation session `ready` and equal to the previous session · inside the execution window · manifest matches · no open operation · no unresolved outcome · no unrecognized activity · clean reconciliation | Operation `completed` / `paused(reason)` / `requires_reevaluation(reason)`; submit-time rejections `historical_execution_rejected`, `outside_execution_window`, `evaluation_data_not_ready`, `evaluation_data_changed`, `calendar_data_unavailable`, `operation_open`, `outcome_unresolved`, `strategy_not_active_paper_strategy` | Per the operation's `next_action` (R2) |
| M11 | **Continue session** | Job `paper-session` `{mode: "continue", operation_id}` (own Idempotency-Key; **not** OPS-07 retry) | operation `paused` · every submitted order terminal · owner-level sync after that · fresh clean standalone reconciliation · manifest matches · per-intent permission and fresh risk check | Proceeds; pauses again at the next unaccounted order or price check (`price_unavailable`, `price_moved_beyond_tolerance`: the same pinned intent is sent only once a fresh price is back within tolerance and every check passes; PD-1 approved 2026-10-04); or `requires_reevaluation` / `terminated` | May need repeating within one session (TL-1). A price recovery never overrides window expiry, changed inputs or an unresolved earlier submission |
| M12 | **End operation** | `POST /api/v1/execution-operations/{id}/end` `{reason}` (synchronous control) | operation open; no Job of the operation running (`operation_running` otherwise) | `terminated/cancelled_by_operator`; **unsent only** → `cancelled_unsent`; the response lists remaining working orders and unresolved intents | **Does not** cancel broker orders, resolve uncertainty or unblock trading (J-2). If intents are unresolved, follow procedure 3 (R3, M4 — available after End —, M14 only as audited evidence); a new session is possible only after procedure 3 branch B (every intent established, then a fresh clean standalone M5). Wait for working orders; then Evaluate (M8) and start a new session (M10), subject to every other gate |
| M13 | _(intentionally absent: withdrawal declined, J-1. The number is kept so the other references stay stable)_ | — | — | — | — |
| M14 | **Record broker statement** | `POST /api/v1/recovery/intents/{id}/broker-statement` `{statement: "not_received" \| "order_record", reference, reason}` (control) | intent unresolved on the missing-order path; the broker's written response is in hand; allowed before **or after** the operation ends | *(amended 2026-10-04)* Both statements are **audited evidence only**: the intent stays unresolved and nothing is resent (a non-receipt statement cannot show that a request already sent will not arrive later). `order_record` helps the next sync/lookup find and verify the order | Keep syncing (M4/M5). The intent resolves only when the broker shows the order (found and verified) or its attempt history proves it not sent. Until then the strategy stays blocked — no Continue, no new session, no handover — with no product-level release (TL-4). Recovery stays available after End |
| M15 | **Retry a failed Job** | existing `POST /api/v1/jobs/{id}/retry` | OPS-07 unchanged for non-paper types. For a failed uncertain `paper-session`: blocked until resolved; then it resumes original intents only | typed 409 `outcome_unresolved` / `reconciliation_required` / `reconciliation_not_clean` | Recovery steps R3 → M4/M5 (M13 does not exist; M14 only records audited evidence and never resolves). The retry becomes possible only after the uncertainty is resolved (procedure 3, branch B) |
| M16 | **Kill switch trip / reset** | existing `PUT /api/v1/controls/kill-switch` | — | `changed` / `unchanged` | A trip pauses a running operation at its next order; reset + checks → M11 |

---

## 2. Standard procedures

1. **First start (no owner):**
   1. M1 calendar to today + horizon.
   2. M2 bars and M3 metadata.
   3. R4 until evaluation readiness is `ready`.
   4. M4 → M5 (clean).
   5. Resolve any outcome listed by R1/A5 (the 29 Sep jobs: `nothing_submitted` evidence, then the fresh M5 resolves them).
   6. M6 seed.
   7. M7 enable.
2. **Daily session:**
   1. M1 if the runway is low.
   2. M2, M3.
   3. M8 on the evaluation session.
   4. Inside the window: M10.
   5. On `paused`: wait for orders to finish → M4 (owner or account) → M5 → M11. Repeat as needed (TL-1).
3. **Ambiguous submission** *(corrected 2026-10-04; the earlier unconditional "M5 clean → trading resumes" is superseded)*:
   1. R3 (shows each intent's classification, evidence, `resubmission_permitted: false`).
   2. M4 (account or owner scope). Broker sync stays available throughout recovery, before and **after** End (M12), and is never gated.
   3. Branch on what the broker and the attempt log show for **every** intent of the uncertain Job:
      - **A — still unresolved** (any intent not found, or found evidence incomplete, and its attempt history does not prove it was never sent): the strategy stays blocked (`outcome_unresolved`). Wait for the grace period and repeat M4. Broker inquiry with the R3 evidence package → M14 records the answer as audited evidence only; it does not resolve the intent. A clean M5 does **not** release the block while any intent is unresolved, and neither do complete absence evidence, executor termination, elapsed time, End/expiry (M12), a new evaluation (M8), a new order version or an ownership change (M6 refuses with A5). No intent is resent. Keep repeating M4 (also after End); the case leaves branch A only when the broker shows the order or the attempt history proves non-sending.
      - **B — every intent established** under the REC-01 predicate (found and verified at the broker in any state, `nothing_submitted` with execution-path evidence, or proven not sent by its whole attempt history): run a **fresh** M5 (standalone, account or owner level; the in-session check never counts) completed after the latest broker-touching Job. Only a clean result clears the uncertainty (`reconciliation_required` / `reconciliation_not_clean` otherwise).
   4. After branch B and a clean fresh M5, trading becomes **eligible** again — not automatic: M10/M11 still require every other applicable gate (owner and enabled, kill switch armed, no open/working orders unaccounted, verified evaluation basis, execution window, manifest, fresh price and risk checks, TL-10 allowance). A found order is never re-POSTed; a found working order still pauses/blocks per TL-2.
4. **Unrecognized broker activity:** M5 lists the items → ensure they're terminal and net zero (at the broker if necessary) → M9 (recording + fresh check).
5. **Data corrected after evaluation:** the operation shows `requires_reevaluation/evaluation_data_changed` → M12 → M8 → M10.
6. **Handover:**
   1. M7 disable the outgoing strategy.
   2. Reach flat (TL-6).
   3. M4 → M5.
   4. M6 to the new strategy, which starts disabled.
   5. M7 enable it.

---

## 3. API end-to-end acceptance scenarios (Phase 20.1 gate, P20.1-13)

These run over HTTP (FastAPI TestClient) with a scripted fake broker and a real database. Each asserts status codes, typed bodies and fake-broker POST counts.

| # | Scenario | Key assertions |
|---|---|---|
| E1 | First start from no owner | M10 before seeding → `strategy_not_active_paper_strategy`; M6 before M5 → `check_failed:A6`; after M4+M5 → seeded, owner **disabled**; M7 → enabled |
| E2 | 29 Sep carry-over | R1 shows A5 failing for the three jobs; R3 shows `nothing_submitted` (no linked `paper_execution` run); after a fresh M5 → A5 passes |
| E3 | Session with one working order | M10 → `paused/working_order_commitments_unaccounted`, intents 2..n `registered_unsent`/`planned`; M11 early → refused (order not terminal); order fills → M11 without sync → refused (`awaiting_reconciliation`); M4 + M5 → M11 sends intent 2 with its **original** id; intent 1 POST count stays 1 |
| E4 | Immediately filled order | M10 → `paused` (not synced) → M4 + M5 → M11 → continues (TL-1 documented) |
| E5 | Earlier fill changes the portfolio | After intent 1 fills, M11 re-checks risk on intent 2: pass → sent unchanged; forced cash shortfall → `requires_reevaluation/risk_limit_failed:insufficient_cash`; **no re-evaluation is demanded because of the fill itself** |
| E6 | Data correction | Corrected close for an evaluated symbol → M11 → `requires_reevaluation/evaluation_data_changed`; identical re-ingest → no change |
| E7 | Ambiguous submission, order found | Read timeout on POST → Job uncertain; M10/M15 → 409; M4 finds the order `filled` → resolved with no broker statement; the intent is never re-POSTed |
| E8 | Ambiguous submission, order never found | Absence evidence during validity and after close → **still unresolved**; M10 → 409 `outcome_unresolved`. M12 End → operation `terminated`, the intent stays `ambiguous`, R3 still lists it, M10 still 409. *(amended 2026-10-04)* M14 `not_received` with the operation open → still unresolved; M11 and M10 → 409 `outcome_unresolved`; M6 handover → 409 with `A5` among the failed checks (A1 is named first while the operation is open); no resend. M14 after End → still unresolved, M10 still 409. The fake broker later shows the order → sync classifies it found-verified, a clean standalone reconciliation resolves it, and only then is M10 allowed (other checks passing). Zero broker cancel calls throughout |
| E9 | External activity | Scripted manual buy + sell → M5 unrecognized → M10 refused; M9 → recorded + fresh check clean → M10 allowed; M9 with an open external order → `external_order_not_terminal` |
| E10 | Historical execution | M10 for an old evaluation session → `historical_execution_rejected`; M8 (research) for the same date → succeeds |
| E11 | Window expiry | Paused operation, clock past the cutoff → the next touch (M11) → `terminated/execution_window_elapsed`, **unsent only** → `expired_unsent`; a working order from it still blocks until terminal and synced; then a new session is allowed (other checks passing) |
| E12 | Symbol metadata | M3 with one unknown ticker → `partial` with a per-symbol reason; R4 shows that symbol `not_ready(missing_metadata)`; M10 → its intent rejected `symbol_not_ready`; the other symbols trade; auth failure → the whole operation `failed` |
| E13 | Handover | M6 with a position → `check_failed:A3`; after flat + M4 + M5 → succeeds, new owner disabled |
| E14 | Mutations disabled | Every new mutating route → 403 `mutations_disabled`, zero writes |
| E15 | Legacy console truthfulness | Contract tests: a paused operation's Job renders "Job: Succeeded · Outcome: Paused" (never plain success); no paper-session start or Retry controls; the account-scope shortcuts **and** `/jobs/new` reconciliation/broker-sync forms work with no owner; the reconciliation panel never says "does not block execution" while trading is blocked; the active-strategy line shows `none` |

---

## 4. Existing console during the interim (smallest truthful behavior)

| Surface | Change (P20.1-14) | Why |
|---|---|---|
| `/jobs/new` picker + `PaperSessionJobForm` | `paper-session`, `record-external-activity` and the `continue` mode are marked **API only** (from an additive catalog field `console_submission: "api_only"`); selecting one shows "Operated through the API in this version" instead of a form | No unsupported initiation |
| `/paper` "Run paper session" shortcut | Removed; replaced by the same notice | Same |
| `/paper` "Run reconciliation" / "Sync broker orders" shortcuts **and** the `/jobs/new` reconciliation and broker-sync forms | Submit `scope: "account"` (forms default to it; strategy scope stays API-only) | Always supported, even with no owner |
| `/paper` reconciliation panel ("does not block execution") | Show the latest reconciliation of **either** scope with its time; when trading is blocked for another reason (no owner, unresolved outcome), state "Trading blocked: <reason>" instead of implying readiness | It would otherwise show the 29 Sep strategy run while trading is blocked |
| Job detail **Retry** for `paper-session` | Hidden, with the API-only notice (other job types unchanged) | Retry now has resume semantics the old console can't explain |
| Jobs list + Job detail header | Show **Outcome** next to the Job status, from the additive `outcome`/`operation` fields: "Succeeded · Paused: working order", "Succeeded · Partial (1 symbol failed)". A non-final operation never gets success styling | Lifecycle ≠ outcome |
| `/controls` and `/strategy` | One read-only line: "Active paper strategy: none (managed through the API)" | "Enabled" no longer implies "trading" |
| Everything else | Unchanged; `tests/test_console_api_contract.py` stays green | Additive API |

**Fallback:** if the Outcome display can't be done with these small changes, paper-trading initiation stays disabled (already planned) and every paper-session Job row is labelled "Outcome shown via API only". A success label is never shown for a paper session.

---

## 5. Temporary limitations visible to the operator

- **TL-1:** repeated Continue actions within one session.
- **TL-2:** a working order blocks further orders.
- **TL-3:** single execution policy; no pre-open or after-hours submission.
- **TL-4:** an ambiguous order that is **found** resolves normally. One that is **never found** blocks the strategy's new submissions and every ownership change until the order appears at the broker or its attempt history proves it was never sent. *(Amended 2026-10-04: Alpaca's written non-receipt is recorded as audited evidence only and no longer clears the block or permits a resend; there is no product-level release in the initial scope.)* Elapsed time, absence evidence, executor termination, ending the operation, re-evaluation and new order versions never clear it.
- **TL-10 (2026-10-04):** at most one broker-reaching action per strategy, evaluation session, symbol and side (initial product limitation, not a permanent invariant). An earlier order with that key that reached or may have reached the broker (accepted, partially filled, filled, expired, canceled, broker-rejected or uncertain) consumes it; orders never sent do not. While an order is still uncertain, TL-4's block applies first (new sessions are refused `outcome_unresolved`). A partially filled exit leaves no second sell in that evaluation session.
- **TL-11 (2026-10-04):** partial-fill remainders are not pursued automatically; the remaining position is kept, shown and included in risk checks, and a later evaluation session's exit handles a sell remainder according to the strategy's own exit rule.
- **TL-5:** external activity must be terminal and net zero.
- **TL-6:** handover only when flat.
- **TL-7:** full history re-read.
- **TL-8:** lazy window expiry.
- **TL-9:** operation through the API until v1.4.

> **Amendments (2026-10-03, round 3):** M10/M11 sends need a fresh Alpaca latest-trade price per order (otherwise the operation pauses `price_unavailable`). A new start on a fresh evaluation is refused `evaluation_predates_executions` until the evaluation's portfolio basis postdates the strategy's earlier executions (sync after they are terminal). Job admission and M6 handover serialize on the active-paper-strategy row. After a crash, an order whose attempt has no outcome is in doubt and is recovered through R3/M4/M5/M14, never resent by Continue.

> **Amendments (2026-10-03, final correction; resend part superseded 2026-10-04):** ~~M14 may lead to a resend when the statement carries `previous_executor_terminated: true`~~ — superseded by round 5 below. A recorded timeout is an uncertain outcome like an empty one: Continue stays refused until recovery resolves it (and there is no resend at all; round 5). A new start on a new evaluation sends only candidates that are risk-approved on a verified basis (sync of every earlier execution + clean standalone reconciliation) and are not replays of an earlier SUBMITTED decision (`evaluation_basis_unverified`, `replay_of_earlier_decision`); never-submitted intents do not block a re-evaluation after End.

> **Amendments (2026-10-04, round 5):** No order request is resent while the original may still produce an execution. M14 records audited evidence only; `previous_executor_terminated` is removed (an unknown key → 422). "Proven not sent" needs positive evidence over the intent's whole attempt history (`pre_connection` or `deadline_expired` recorded by the sending executor, or zero attempts); a missing outcome is uncertainty. A new evaluation sends only actions justified by verified state and strategy rules: a changed fingerprint alone proves nothing (`replay_of_earlier_decision`, `action_already_submitted`, risk codes otherwise). *Approved 2026-10-04 (PD-1; 04 "Planning correction, round 6"):* a price deviation beyond tolerance pauses (`price_moved_beyond_tolerance`, next action `wait_for_price_then_continue`) and sends nothing. M11 may send the same pinned intent (same identity, client_order_id and quantity; no replanning, resizing or new version) only after rerunning every M11 check: fresh price and tolerance, permission, provenance, recovery, reconciliation, risk and the TL-10 allowance. Changed evaluation inputs or strategy settings still yield `requires_reevaluation` (M12 → M8 → M10). Window expiry still terminates the operation (E11) even if the price recovers. An unresolved earlier submission still blocks, and M11 never resends an in-doubt intent. ~~Under the alternative it stays `requires_reevaluation`.~~ *(superseded)* After a fresh-price risk failure, M12 End then an unchanged new evaluation can send a never-sent action without any data or settings change.
