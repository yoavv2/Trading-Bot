# Phase 7: Correctness Kernel - Context

**Gathered:** 2026-04-18
**Status:** Ready for planning

<domain>
## Phase Boundary

Enforce a closed order state machine with one legal transition boundary, deterministic intent identity for `client_order_id`, and a persistent kill switch that blocks new broker submissions without stopping read-only safety flows.

Phase 7 is about correctness inside the existing paper-execution path. It does not add concurrency control, reconciliation rewrite, startup hardening, or broader product features from later phases.

</domain>

<decisions>
## Implementation Decisions

### Intent identity
- Two submissions are the same intent only if `strategy_id`, `session_date`, `symbol`, `side`, and intended quantity are unchanged.
- Deterministic `client_order_id` should be derived from the material intent fields, not from `risk_event_id` or any other ephemeral row identity.
- If broker outcome is uncertain, retry must first reuse the same `client_order_id` and resolve against persisted/broker state before any new submission is attempted.
- Once the broker has definitely seen an order, that intent is never reused. Any later attempt for the same trade idea becomes a new version / new intent chain.
- When a rerun finds an already-known intent and no new submission is needed, the operator must see an explicit reuse note rather than a silent skip.

### Kill switch scope
- Phase 7 kill switch is a global submission halt for the platform. It is separate from the existing per-strategy active/disabled control.
- Trip and reset stay CLI-first in Phase 7, with a durable audit trail for both actions.
- While tripped, scheduled paper sessions should still run preflight, reconciliation, and read-only safety checks, then record that submissions were blocked by the kill switch.
- The kill switch remains tripped across restarts and future sessions until the operator manually resets it.

### Lifecycle visibility
- Default operator inspection should stay condensed: latest canonical lifecycle state and key blocking reason first, with full timeline available on drilldown.
- Canonical local lifecycle state is the primary operator-facing label. Raw broker status/details are secondary drilldown data.
- If code hits an illegal transition during a batch, the current run fails hard, highlights the offending order/event, and performs no further submissions in that run.
- Duplicate/reuse outcomes should appear in both the run summary and the affected order timeline.

### Claude's Discretion
- Exact enum names, event names, and predecessor-link field names for order version chains.
- Exact CLI flag naming and report JSON/markdown formatting.
- Exact severity levels for non-blocking reuse notes versus blocking transition failures.
- Exact schema shape for storing append-only order-transition events, as long as accepted and rejected transitions are both durable and operator-readable.

</decisions>

<specifics>
## Specific Ideas

- Replace the current `risk_event_id`-derived `client_order_id` scheme with a material-intent identity that survives reruns and process restarts.
- Keep the existing operator-control pattern, but add a true global submission halt instead of overloading per-strategy disable for emergency-stop behavior.
- Preserve the current condensed operator-status surface from Phases 5-6, but make every order drilldown show the full transition timeline.
- Prefer explicit reuse notes over silent no-ops so idempotent reruns stay understandable to the operator.

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/trading_platform/services/operator_controls.py`: already persists CLI-first control actions as `StrategyRun(run_type=operator_control)` plus durable `ExecutionEvent` audit rows; strong pattern for kill-switch trip/reset flows.
- `src/trading_platform/services/paper_execution.py`: already owns paper-session orchestration, duplicate detection, run summaries, and broker submission entrypoints; this is the main boundary Phase 7 must harden.
- `src/trading_platform/services/reconciliation.py`: already performs read-only broker/local checks and persists blocking findings; good existing path for “kill switch tripped but safety flows still run.”
- `src/trading_platform/services/operator_reads.py` and `src/trading_platform/services/operator_status.py`: already implement condensed operator reads with drilldown-friendly event payloads.
- `src/trading_platform/db/models/paper_order.py`: already has DB `UNIQUE` constraints on `client_order_id` and `broker_order_id`, giving Phase 7 a natural idempotency anchor.

### Established Patterns
- CLI-first operator control is an intentional project pattern from Phase 1 and Phase 6; Phase 7 should preserve it.
- Durable auditability already uses `StrategyRun` roots plus `ExecutionEvent` rows for operator-visible failures and blocking conditions.
- Broker matching already checks `broker_order_id` and `client_order_id`; Phase 7 should strengthen `client_order_id` into the primary stable intent identity.
- Current code mutates `PaperOrder.status` directly in multiple places using freeform strings. Phase 7 exists to replace that with a closed transition boundary.

### Integration Points
- Worker commands already in place: `submit-paper-orders`, `run-paper-session`, `reconcile-paper-execution`, `operator-control`, `operator-status`.
- Core persistence touchpoints: `PaperOrder`, `ExecutionEvent`, `StrategyRun`, and the existing operator-read services.
- Main code seams to harden: submission flow in `services/paper_execution.py`, broker-response sync/recovery in `services/reconciliation.py`, and provider-facing contracts in `services/execution.py`.

</code_context>

<deferred>
## Deferred Ideas

- Per-strategy kill switch in addition to the global switch.
- API write surface for kill-switch trip/reset.
- Auto-expiring kill switch or next-session auto-reset behavior.
- Promoting every duplicate/reuse note into the global recent-blocking-events feed by default.

</deferred>

---

*Phase: 07-correctness-kernel*
*Context gathered: 2026-04-18*
