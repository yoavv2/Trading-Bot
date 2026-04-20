---
phase: 07-correctness-kernel
verified: 2026-04-20T08:08:11Z
status: passed
score: 5/5 success criteria verified
test_suite:
  command: "PYTHONPATH=src .venv/bin/pytest tests/test_order_state_machine.py tests/test_operator_controls.py tests/test_paper_execution.py tests/test_execution_reconciliation.py tests/test_db_migrations.py tests/test_alpaca_execution.py -q"
  result: "50 passed in 12.54s"
  passed: true
requirements_coverage:
  completed_in_plans: 16
  total_expected: 16
  documentation_staleness:
    note: "REQUIREMENTS.md ledger rows for ORDER-01..07 and IDEM-01..04 still display `[ ]` / `Pending` even though plans 07-01 and 07-02 landed and the code/tests satisfy the invariants. SAFE-01..05 were updated to `[x]` / `Complete` by plan 07-03. This is a documentation bookkeeping miss, not a code-level gap."
    affected_ids:
      - ORDER-01
      - ORDER-02
      - ORDER-03
      - ORDER-04
      - ORDER-05
      - ORDER-06
      - ORDER-07
      - IDEM-01
      - IDEM-02
      - IDEM-03
      - IDEM-04
---

# Phase 7: Correctness Kernel Verification Report

**Phase Goal:** Every order state transition is governed by a closed enum state machine with a single entry point, every submission carries a deterministic broker identity, and the kill switch is a durable persisted invariant — not an in-process flag.

**Verified:** 2026-04-20T08:08:11Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Test Suite Result

Command:

```
PYTHONPATH=src .venv/bin/pytest tests/test_order_state_machine.py tests/test_operator_controls.py \
    tests/test_paper_execution.py tests/test_execution_reconciliation.py tests/test_db_migrations.py \
    tests/test_alpaca_execution.py -q
```

Result: **50 passed in 12.54s**

---

## Goal Achievement — Success Criteria

| #   | Success Criterion                                                                 | Status     | Evidence |
| --- | ---------------------------------------------------------------------------------- | ---------- | -------- |
| 1   | `apply_order_transition(order_id, event)` with an illegal `(from_state, event)` pair raises `IllegalOrderTransition`; no other path mutates order state; module-boundary test asserts this | VERIFIED   | `src/trading_platform/services/order_state_machine.py:112-130` defines typed `IllegalOrderTransition`; `:167-175` raises on illegal target; `:199-213` persists rejected `OrderEvent` row. Module boundary regression: `tests/test_paper_execution.py:1203-1207` and `tests/test_execution_reconciliation.py:629-633` assert no `<var>.status =` mutation remains in those services and both import `apply_order_transition`. Manual scan: only one `.status =` write in `services/` is at `order_state_machine.py:281` inside the kernel; other `.status` writes in `paper_execution.py:1623,1630` and `backtesting.py:326` target `Position.status` / `Trade.status`, not `PaperOrder`. |
| 2   | Every accepted or rejected transition appends a new `OrderEvent` row; the `orders` row is never the sole record | VERIFIED   | `order_state_machine.py:249-291` always emits an `OrderEvent` (flush at `:282`) before touching `paper_order.status`; rejected path (`:199-213`) also persists an event with `outcome=REJECTED` and preserves the original state. `src/trading_platform/db/models/order_event.py:54-112` defines the append-only table with closed-enum `from_state`/`event_type`/`to_state`/`outcome`. Tests: `tests/test_order_state_machine.py:195,254` cover accepted and rejected persistence. Migration `alembic/versions/0013_phase7_order_state_kernel.py` backfills `order_events` for legacy rows. |
| 3   | Identical `(strategy_id, session_date, symbol, side, intent_hash)` inputs produce byte-for-byte identical `client_order_id` across processes/restarts; DB `UNIQUE` constraint enforces one row per intent | VERIFIED   | `src/trading_platform/services/order_identity.py:30-128` derives identity only from material fields via `hashlib.sha256` over a canonical JSON (`sort_keys=True, separators=(",", ":")`) — no clock, UUID, or `risk_event_id`. `build_client_order_id` formats `{prefix}-{YYYYMMDD}-{symbol}-{hash[:18]}` deterministically. DB uniqueness: `src/trading_platform/db/models/paper_order.py:28-33` declares `uq_paper_orders_client_order_id` and `uq_paper_orders_intent_hash`. Migration `alembic/versions/0014_phase7_idempotent_intents.py:49-63` backfills and adds the `UNIQUE` constraint with a guard against pre-existing duplicates. Tests: `tests/test_db_migrations.py:737` and `tests/test_paper_execution.py:625,683`. |
| 4   | Retry of an existing intent returns the persisted row instead of inserting a duplicate; broker-response matching resolves by `client_order_id` first | VERIFIED   | `tests/test_paper_execution.py:625-681` (`test_run_paper_order_submission_retries_same_intent_across_followup_risk_runs`) asserts same-intent retry reuses the persisted row. Broker matching: `src/trading_platform/services/reconciliation.py:144-167` and `:369-379` build a `broker_by_client_id` dict first and only fall back to `broker_order_id` when the client-order lookup misses. Test `tests/test_execution_reconciliation.py:503` (`test_reconciliation_prefers_client_order_id_when_version_chain_exists`) locks in the priority. Version chain: `paper_order.supersedes_paper_order_id` plus `tests/test_paper_execution.py:683` asserts broker-touched material changes become new versions with predecessor links. |
| 5   | Kill-switch state is persisted in the DB, checked before every broker submission, survives restarts, and when tripped allows reconciliation/logging to continue while blocking only new submissions | VERIFIED   | Durable persistence: `src/trading_platform/db/models/system_control.py:22-60` defines `KillSwitchState` enum + `SystemControl` table; migration `alembic/versions/0015_phase7_global_kill_switch.py` creates and seeds the row. Batch-entry check: `src/trading_platform/services/paper_execution.py:226-253` (`load_kill_switch_state` then halt + record failed `StrategyRun`). Mid-run check: `paper_execution.py:332-354` re-reads the switch before each candidate. Restart safety: `tests/test_operator_controls.py:227` (`test_kill_switch_tripped_state_is_restart_safe`). Reconciliation/read continuity while tripped: `tests/test_paper_execution.py:1360` and `:1443` (session reconciliation and `sync_paper_state` continue; blocked runs are persisted, not silent). Manual-reset-only: `tests/test_paper_execution.py:1255`. CLI: `tests/test_operator_controls.py:247,269` cover `trip-kill-switch`, `reset-kill-switch`, `show-kill-switch`. |

**Score:** 5/5 Success Criteria verified.

---

## Must-Haves from Plan Frontmatter (three plans)

### 07-01 (Order State Kernel)

| Truth / Artifact / Link                                                                                   | Status    | Evidence |
| --------------------------------------------------------------------------------------------------------- | --------- | -------- |
| Paper-order lifecycle is a closed enum state machine, not freeform strings                                 | VERIFIED  | `OrderLifecycleState` StrEnum at `src/trading_platform/db/models/order_event.py:19-28`; column typed `Enum(OrderLifecycleState)` at `paper_order.py:68-77`. |
| All accepted/rejected transitions flow through one `apply_order_transition(order_id, event)` with no broker I/O | VERIFIED  | Single function at `order_state_machine.py:143-183`; test `tests/test_order_state_machine.py:292-317` monkeypatches `socket.create_connection` to prove no network I/O. |
| Every lifecycle change leaves an append-only durable event trail                                          | VERIFIED  | `order_events` table definition `order_event.py:54-112`; every path in `_persist_transition_event` appends a row before mutating `paper_order.status`. |
| `order_events` model/table exists with typed event and state fields                                        | VERIFIED  | `alembic/versions/0013_phase7_order_state_kernel.py` + `tests/test_db_migrations.py:445`. |
| Dedicated order-state-machine service owns legal transition mapping and typed illegal errors              | VERIFIED  | `_LEGAL_TRANSITIONS` closed map + `IllegalOrderTransition` dataclass (`order_state_machine.py:23-130`). |
| Existing submission and reconciliation flows call the boundary instead of mutating `PaperOrder.status`     | VERIFIED  | Regression tests `tests/test_paper_execution.py:1203` and `tests/test_execution_reconciliation.py:629` assert no direct `.status =` mutation remains in either service file. |

### 07-02 (Idempotent Intents)

| Truth / Artifact / Link                                                                                   | Status    | Evidence |
| --------------------------------------------------------------------------------------------------------- | --------- | -------- |
| Intent identity derived from material fields, not `risk_event_id`                                          | VERIFIED  | `order_identity.py:30-128` uses only `strategy_id`, `session_date`, `symbol`, `side`, `quantity`. |
| Retry of the same intent reuses persisted row and `client_order_id` before any new submission             | VERIFIED  | `tests/test_paper_execution.py:625-681`. |
| Broker-response recovery resolves by `client_order_id` first; explicit version chaining for broker-touched supersedes | VERIFIED  | `reconciliation.py:144-167,369-379`; `tests/test_execution_reconciliation.py:503`; `paper_order.supersedes_paper_order_id` column + `tests/test_paper_execution.py:683`. |
| Dedicated order-identity helper                                                                            | VERIFIED  | `src/trading_platform/services/order_identity.py` present and imported by `paper_execution.py`. |
| Paper-order model stores retry-vs-version metadata                                                         | VERIFIED  | `intent_hash`, `intent_version`, `supersedes_paper_order_id` columns on `paper_orders`. |
| Operator reads show explicit reuse notes                                                                   | VERIFIED  | Summary payload from `paper_execution.py` emits reuse/version context; covered by `tests/test_execution_reconciliation.py` and operator-reads tests. |

### 07-03 (Global Kill Switch)

| Truth / Artifact / Link                                                                                   | Status    | Evidence |
| --------------------------------------------------------------------------------------------------------- | --------- | -------- |
| Durable global kill-switch, not in-process flag                                                            | VERIFIED  | `SystemControl` row persisted in `system_controls`; `load_kill_switch_state` reads DB on every check. |
| Batch-entry and pre-submit paths check the persisted switch; reconciliation/logging continue              | VERIFIED  | `paper_execution.py:226-249` (batch entry) and `:332-354` (per-candidate); `tests/test_paper_execution.py:1360,1443` cover continuity. |
| Trip/reset are explicit audited operator events with restart-safe state                                    | VERIFIED  | `operator_controls.py` `trip_kill_switch` / `reset_kill_switch` create `OPERATOR_CONTROL` `StrategyRun` + typed `ExecutionEvent`; `tests/test_operator_controls.py:167,227`. |
| Persisted kill-switch table exists                                                                         | VERIFIED  | `src/trading_platform/db/models/system_control.py`; migration `alembic/versions/0015_phase7_global_kill_switch.py`; `tests/test_db_migrations.py:876`. |
| Worker CLI commands for trip/reset/show                                                                    | VERIFIED  | `src/trading_platform/worker/__main__.py` `operator-control {trip-kill-switch|reset-kill-switch|show-kill-switch}`; `tests/test_operator_controls.py:247,269`. |
| Operator status shows switch state and blocked executions                                                  | VERIFIED  | `operator_reads.py` `get_kill_switch_state` / `list_blocked_paper_executions`; `operator_status.py` adds fields to `OperatorStatusReport`; `tests/test_operator_controls.py:341,373`. |

---

## Requirement Traceability (16 IDs)

| Req ID   | Source Plan | Description (short)                                   | Status     | Evidence |
| -------- | ----------- | ----------------------------------------------------- | ---------- | -------- |
| ORDER-01 | 07-01       | Closed enums for states and events                    | SATISFIED  | `order_event.py:19-47` `OrderLifecycleState`, `OrderTransitionEventType`, `OrderTransitionOutcome`. |
| ORDER-02 | 07-01       | Closed legal `(from_state, event) → to_state` map     | SATISFIED  | `order_state_machine.py:23-91` `_LEGAL_TRANSITIONS`. |
| ORDER-03 | 07-01       | Single-entry mutation via `apply_order_transition`    | SATISFIED  | `order_state_machine.py:143`; boundary tests `tests/test_paper_execution.py:1203`, `tests/test_execution_reconciliation.py:629`. |
| ORDER-04 | 07-01       | Typed `IllegalOrderTransition` on illegal event       | SATISFIED  | `order_state_machine.py:112-130,167-175`. |
| ORDER-05 | 07-01       | Every accepted or rejected transition appends an `OrderEvent` | SATISFIED  | `_persist_transition_event` always writes an `OrderEvent`; `tests/test_order_state_machine.py:195,254`. |
| ORDER-06 | 07-01       | `apply_order_transition` contains no I/O              | SATISFIED  | `tests/test_order_state_machine.py:292-317` (socket monkeypatch). |
| ORDER-07 | 07-02       | Resubmission reuses `client_order_id` or emits new version with predecessor link | SATISFIED  | `paper_order.supersedes_paper_order_id` + `tests/test_paper_execution.py:625,683`. |
| IDEM-01  | 07-02       | Deterministic `client_order_id` from material fields  | SATISFIED  | `order_identity.py:47-91`. |
| IDEM-02  | 07-02       | DB `UNIQUE` on `client_order_id` + intent_hash        | SATISFIED  | `paper_order.py:28-33`; migration `0014_phase7_idempotent_intents.py:49-63`. |
| IDEM-03  | 07-02       | Retry returns existing row, no duplicate              | SATISFIED  | `tests/test_paper_execution.py:625-681`. |
| IDEM-04  | 07-02       | Reconciliation matches by `client_order_id` first     | SATISFIED  | `reconciliation.py:144-167,369-379`; `tests/test_execution_reconciliation.py:503`. |
| SAFE-01  | 07-03       | Kill-switch persisted in DB                           | SATISFIED  | `SystemControl` table; migration `0015_phase7_global_kill_switch.py`. |
| SAFE-02  | 07-03       | Checked before every submission and every batch entry | SATISFIED  | `paper_execution.py:226,332`. |
| SAFE-03  | 07-03       | Tripped halts only new submissions; reconciliation/logging continue | SATISFIED  | `tests/test_paper_execution.py:1360,1443`. |
| SAFE-04  | 07-03       | State change takes effect on next check without restart | SATISFIED  | `tests/test_paper_execution.py:1307` (`_halts_mid_run_when_kill_switch_trips_between_submissions`). |
| SAFE-05  | 07-03       | Tripped state survives restart and is re-read at boot | SATISFIED  | `tests/test_operator_controls.py:227`. |

All 16 requirement IDs appear in at least one plan's `requirements:` frontmatter (07-01: ORDER-01..06; 07-02: ORDER-07, IDEM-01..04; 07-03: SAFE-01..05) and all 16 have supporting code + test evidence.

**Documentation staleness (non-blocking):** REQUIREMENTS.md still shows `[ ]` / `Pending` for ORDER-01..07 and IDEM-01..04. SAFE-01..05 were updated to `[x]` / `Complete`. Recommend a follow-up commit that flips ORDER-01..07 and IDEM-01..04 to `[x]` / `Complete` so the ledger matches reality.

---

## Key Link Verification (Wiring)

| From                                    | To                                      | Via                                                 | Status |
| --------------------------------------- | --------------------------------------- | --------------------------------------------------- | ------ |
| `services/paper_execution.py`           | `services/order_state_machine.py`       | `apply_order_transition(...)` imports and call sites | WIRED  |
| `services/reconciliation.py`            | `services/order_state_machine.py`       | `apply_order_transition(...)`                       | WIRED  |
| `services/paper_execution.py`           | `services/order_identity.py`            | `build_client_order_id`, `derive_order_identity`    | WIRED  |
| `services/reconciliation.py`            | `client_order_id`-first matching        | `broker_by_client_id` then `broker_by_broker_id` fallback | WIRED  |
| `services/paper_execution.py`           | `services/operator_controls.load_kill_switch_state` | batch entry + per-candidate re-read                | WIRED  |
| `services/operator_status.py`           | `system_controls` via operator-reads    | `get_kill_switch_state`, `list_blocked_paper_executions` | WIRED  |
| `worker/__main__.py`                    | `services/operator_controls.trip_kill_switch` / `reset_kill_switch` | `operator-control {trip|reset|show}-kill-switch` CLI | WIRED  |

---

## Anti-Patterns Found

None. Spot checks:

- `grep -E "(pending_order|persisted_order|existing_order|local_order|paper_order)\.status\s*="` against `src/trading_platform/services/*.py` returns only the kernel's own write at `order_state_machine.py:281`. The other `.status =` writes are on `Position` / `Trade`, not `PaperOrder`.
- No `TODO`, `FIXME`, `placeholder`, or `return null` stubs in the Phase 7 files.
- No empty test functions; all 50 collected tests exercise real DB + service code paths.

---

## Human Verification Required

None. The phase goal is a correctness/invariant goal expressible in automated tests, and every Success Criterion has a passing test in the 50-test slice.

---

## Gaps Summary

No gaps blocking goal achievement. One documentation-only follow-up: update `.planning/REQUIREMENTS.md` rows for ORDER-01..07 and IDEM-01..04 from `[ ]` / `Pending` to `[x]` / `Complete` to match the code and tests (SAFE-01..05 were already updated by plan 07-03).

---

*Verified: 2026-04-20T08:08:11Z*
*Verifier: Claude (gsd-verifier)*
