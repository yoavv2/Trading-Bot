# Phase 21: Operator Read-Model Foundation - Context

**Gathered:** 2026-09-30
**Status:** Planning review rounds 1–6 applied (2026-10-03/04; round 6 adds explicit Sessions coverage for consumed allowances in 21-04 and records decision PD-1, approved 2026-10-04; round 5 vocabulary: `broker_confirmed_not_received` removed from intent states, broker statements are evidence only and never clear `outcome_uncertain_unresolved`, `price_moved_beyond_tolerance` as a pause reason with next action `wait_for_price_then_continue` (approved as PD-1 on 2026-10-04: 04 "Planning correction, round 6"), new candidate dispositions `replay_of_earlier_decision` / `action_already_submitted`, Sessions shows filled and remaining quantity per intent; see 04 "Safety correction, round 5"); round 3 approved R-Q1, the broker latest-trade read, executor fencing and transactional ownership checks. Decisions and guarantees: `04-IMPLEMENTATION-PLANS.md` "Planning review, round 3" and "Final correction". Implementation still requires separate authorization. Previously: Ready for execution after Phase 20.1 (plans drafted; implementation requires separate authorization)
**Source:** `.planning/research/operator-console-ia/02-HYBRID-IA-PROPOSAL.md` (§3 overview hierarchy, §5 issues, §8 activity, §9 backend vs frontend, §10 read models), `03-PLANNING-CHANGES.md` revision 8 (§2 Phase 21, §6 deltas, §3.11 S-8), `04-IMPLEMENTATION-PLANS.md` §2

<domain>
## Phase Boundary

Re-planned Phase 21. The previous scope ("Operations History & Polish", a UI history page and failure indicator) was replaced by settled decision S2, option (b).

The backend exposes every operator-facing fact the v1.4 Operator Console needs as **read-only, bounded, tested APIs** whose meaning is carried in **closed enums**, so the console rebuild does no domain interpretation:
- trading permission;
- posture lanes;
- verdict and next action;
- issues;
- session pipeline and execution operations;
- market-data coverage;
- reconciliation detail (account and owner scope);
- activity;
- worker health, backed by a persisted heartbeat;
- job catalog and list extensions.

v1.3 closes after this phase.

**Out of scope:**
- any console UI: AUD-02 and NOTIF-02 moved to v1.4;
- issue persistence (issues are projections);
- auth;
- storage separation of control changes;
- scheduling;
- concurrent paper trading.
</domain>

<decisions>
## Implementation Decisions (locked)

- **D-01 Worker heartbeat (WRK-01/02).**
  - `worker_heartbeats` is **the only new table** in this phase (migration `0031`, parent `0030_phase20_1_evidence_update_guards`). *(2026-10-05: renumbered 0028→0030, parent 0029; 2026-10-06: renumbered 0030→0031, parent 0030_phase20_1_evidence_update_guards, to follow Phase 20.1's 0028/0029/0030 migrations.)*
  - The run-jobs loop upserts its row, throttled (default 10 s), records a graceful `stopped`, and prunes stale rows.
  - The writer lives in `jobs/` (worker infrastructure self-state, not an operator mutation; ORCH-08 unchanged).
  - `GET /api/v1/system/worker-health` → `idle | busy | unavailable | unknown` (+ reason code; the public read-only deploy uses `unknown` with `no_worker_expected` when `worker_expected=false`).
  - No active Job is never evidence of health. Worker health is not part of `/ready`.
- **D-02 Overview (OPR-01).**
  - Trading permission is `allowed | blocked` with closed blockers: `kill_switch_tripped`, `no_active_paper_strategy`, `strategy_disabled`, `reconciliation_blocking`, `unrecognized_broker_activity`, `outcome_unresolved`, `working_order_commitments_unaccounted`.
  - Kill-switch, owner and enabled gates use the **same functions** as `run_paper_session`. The books gate uses the latest persisted reconciliation (either scope), with its time; it's not a prediction.
  - Also returned:
    - four posture lanes;
    - `verdict_code` (`all_clear`, `trading_blocked`, `will_not_trade`, `needs_verification`, `operation_in_progress`, `attention_needed`, `unknown_state`);
    - `next_action` by closed precedence;
    - environment (mode, broker, `mutations_enabled`, active paper strategy or none);
    - the three calendar facts, separately;
    - account with source and as-of.
  - **Scope (contract change, documented 2026-10-03):** the overview is account-level and has no `?strategy_id=` parameter (02 §10 R1 listed one). A request carrying it is rejected with HTTP 422 `strategy_filter_not_supported` (never silently ignored). The single active paper strategy (or none) is reported in `environment`; strategy filtering stays on the research-scoped reads (sessions, coverage, reconciliations, activity).
  - **Content (restored, review round 2):** the response also carries `evaluation_session_pipeline` (compact pipeline of the evaluation session; not a "current session" field) and `recent_activity` (last 5 Activity items, added by 21-08).
- **D-02a Verdict and next action (review round 2, 2026-10-03).** One ordered decision table produces both, so they always agree; every other open condition stays visible (blockers, lanes, issue counts, `verdict.params.other_conditions`). Approved 02 §3 rules 1–7 keep their order; inserted rows are labelled corrections: unresolved outcome, unknown order state, unrecognized activity and the ownership anomaly after rule 2; no owner after rule 3; working orders, worker unavailable and in-flight operations after rule 4; rule 5 in its stage form (a paused operation yields the operation's own next action); other open rules → `attention_needed` with their first recommended action, unknown lanes → `unknown_state`, and `all_clear` only when no non-info rule is open. Full table: 21-02 D-02a.
- **D-03 Issues (OPR-02).**
  - Projection only, never persisted. A closed rule enum; each rule maps to exactly one lane, one severity and one resolution code.
  - Session-dependent rules are suppressed when the evaluation session is `unknown`.
  - The uncertainty rules use the Phase 20.1 recovery predicate.
  - **Scope (contract change, documented 2026-10-03):** issues are account-level; the `?strategy_id=` parameter of 02 §10 R2 is removed (an unresolved outcome of any strategy blocks seeding/handover, A5) and rejected with HTTP 422 `strategy_filter_not_supported` if sent.
  - Rule set (02 §5.2 + 03 §6 deltas):
    - trading permission: `kill_switch_tripped`, `no_active_paper_strategy`, `strategy_disabled`, `working_order_commitments_unaccounted` (`handover_blocked` removed by D-03a);
    - books vs broker: `outcome_uncertain_unverified`, `outcome_uncertain_unresolved`, `reconciliation_blocking`, `order_state_unknown`, `broker_sync_stale`, `unrecognized_broker_activity`, `owned_order_outside_ownership_period`;
    - market data: `market_data_incomplete`, `market_data_stale`, `calendar_out_of_range`, `calendar_runway_low`, `ingestion_partial`, `symbol_metadata_missing`, `evaluation_outdated`;
    - worker/operations: `operation_failed_repeatedly`, `queue_stalled`, `worker_unavailable`, `worker_unknown`, `mutations_disabled`.
- **D-03a Handover availability is not an issue (contract change APPROVED 2026-10-03).**
  - 02 §5.2 `handover_blocked` ("a handover was requested, but preconditions are unmet") is removed from the rule set: there is no handover request to observe, and request storage is not introduced to make the rule testable.
  - Handover and seeding availability, with reasons, is derived from persisted evidence by 20.1-12 (checks A1–A7 → `seeding_available`, `handover_available`, `checks[]` on `GET /api/v1/controls/active-paper-strategy`).
  - A non-flat account, working orders or an open operation are normal during trading: they make handover unavailable and never open an issue by that fact. Conditions that need the operator keep their own rules.
  - No never-firing placeholder rule, no persistent issue entity and no handover-request storage.
- **D-04 Sessions and operations (OPR-03).**
  - Pipeline per **evaluation session** (Data → Decide → Trade → Sync → Verify; closed stage statuses) for the active paper strategy.
  - Execution operations with state, reason, next action and intent states.
  - Never one "current session" field; historical sessions are never executable.
- **D-05 Coverage and reconciliation (OPR-04/05).**
  - Per-symbol readiness: bars, history, metadata.
  - Calendar horizon and runway.
  - Reconciliation list and detail across `strategy_runs` (owner scope) **and** `account_reconciliation_runs` (account scope), with classes, origin tags, unexplained exposure and recorded external items.
- **D-06 Activity (OPR-06, AUD-01).**
  - One stream of operations, control changes (incl. seeding/handover, operation End, broker statements), external recordings, session outcomes and reconciliations (both scopes).
  - Closed `kind` / `domain` / `outcome`; keyset pagination.
  - `operator_control` rows are translated into Control Changes, never runs.
  - An allowlist walk proves every mutating route yields exactly one item.
- **D-07 Catalog and job list (OPR-07).**
  - `broker_effect`, `session_scoped`, machine-readable prerequisites, and `console_submission` are exposed.
  - The job list carries `outcome` and `operation` (introduced in 20.1) plus a truncated `failure_message`, with no N+1.
  - Every job type must declare its operator mapping (test).
- **D-08 Cross-cutting (OPR-08).**
  - Every read returns server `as_of`, is read-only (zero writes asserted), and has an asserted query-count bound (Phase 11 style).
  - Bounds are the 02 §10 targets, restored 2026-10-03: overview ≤ 15, issues ≤ 12, sessions ≤ 10, coverage ≤ 5, reconciliation list ≤ 1 / detail ≤ 3, activity ≤ 4. Each bound is the TOTAL statement count of the request on the shared engine, including reused components (gates, recovery predicate, calendar facts, manifest verification, worker health). They are met through planned consolidation (21-02 D-08); overview, issues and sessions also rely on the approved single-statement engine gate loader (R-Q1). The totals are planning estimates, verified by measurement during implementation. An executor that cannot meet a bound stops and reports; it never raises a bound or drops response content.
  - Schema-delta test: exactly one new table (`worker_heartbeats`).
  - No files under `console/` change.

### Claude's Discretion
- Route paths beyond those named, and response field naming, within the closed-enum contracts above.
- Heartbeat interval, stale threshold and retention window, as tested configurable constants.
</decisions>

<canonical_refs>
## Canonical References

- `.planning/research/operator-console-ia/02-HYBRID-IA-PROPOSAL.md`: §1 lanes, §3 overview + next-action precedence, §4 pipeline, §5 issues, §7 run translation, §8 activity, §9 interpretation split, §10 read models R1–R9
- `.planning/research/operator-console-ia/03-PLANNING-CHANGES.md`: revision 8 §2, §6, §3.11 (S-8), §8 conflicts
- `.planning/research/operator-console-ia/04-IMPLEMENTATION-PLANS.md`: §2 plan table
- `.planning/phases/20.1-operator-state-correctness/20.1-CONTEXT.md`: semantics this phase reads (ownership, recovery predicate, operation states, calendar facts, outcomes)
- `services/operator_status.py`: existing CLI-only aggregate (starting point for the overview)
- `services/operator_reads.py`, `services/job_reads.py`, `services/analytics.py`, `api/routes/*`
- `jobs/runner.py`, `jobs/queue.py`: worker loop and per-Job heartbeat
</canonical_refs>

<deferred>
## Deferred Ideas

- Console UI for every read above: v1.4 (Phases 22–27)
- Persisted issue history / acknowledge / snooze
- Push transport (SSE/WebSocket)
- Attribution ledger / watermark (LEDGER-01)
</deferred>

---

*Phase: 21-operator-read-model-foundation*
*Context gathered: 2026-09-30*
