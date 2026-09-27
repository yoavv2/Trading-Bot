# Phase 20: Complete Operation Migration & Safety Controls - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-27
**Phase:** 20-complete-operation-migration-safety-controls
**Areas discussed:** Paper-session cancel, Multi-run linkage, Safety controls, Bypass classification, Retry semantics, Payload explicitness

---

## Paper-session cancel

**RUNNING cancel on a paper-session Job**

| Option | Description | Selected |
|--------|-------------|----------|
| Reject 409 | Typed 409, nothing recorded; the sweeper never engages; framework unchanged | ✓ |
| Accept, then ignore | Record the request, the handler never acks; needs a sweeper exemption | |
| Checkpoint before submit | Honor cancel only before the service call (a tiny window) | |

**OPS-08 conflict reason**

| Option | Description | Selected |
|--------|-------------|----------|
| Generic domain_conflict | One closed value; failure_message names the conflict | ✓ |
| Specific per conflict | e.g. run_lock_held; a migration per new conflict | |

**Which types get the queued-only cancel mode**

| Option | Description | Selected |
|--------|-------------|----------|
| Broker-touching types | paper-session, broker order sync, reconciliation | ✓ |
| Paper session only | Only the type OPS-03 names | |
| All new types | Every Phase 20 type | |

**Blocked paper-session status**

| Option | Description | Selected |
|--------|-------------|----------|
| SUCCEEDED + action | Domain decision in result_summary.action | ✓ |
| FAILED domain_conflict | Handler interprets domain outcomes | |

**Reconciliation Job scope**

| Option | Description | Selected |
|--------|-------------|----------|
| Report only | Matches today's CLI; RECON-04 preserved | ✓ |
| Report + corrections | Adds a corrective step outside the paper session | |
| Two Job types | A separate corrections type | |

---

## Multi-run linkage

**Linking multiple or non-StrategyRun outputs**

| Option | Description | Selected |
|--------|-------------|----------|
| Nullable FKs per table | Drop UNIQUE on strategy_runs.job_id; add market_data_ingestion_runs.job_id | ✓ |
| job_resources table | The generic link table deferred in P19 | |
| Primary run only | Keep UNIQUE; recon run unlinked | |

**Types with no run record**

| Option | Description | Selected |
|--------|-------------|----------|
| result_summary only | Empty resources[]; counts in result_summary | ✓ |
| Add audit records | New run-like records | |

---

## Safety controls

**Route shape**

| Option | Description | Selected |
|--------|-------------|----------|
| PUT target state | PUT /controls/kill-switch, PUT /controls/strategies/{id} | ✓ |
| POST action verbs | 4 verb routes | |

**Reason enforcement**

| Option | Description | Selected |
|--------|-------------|----------|
| API-enforced, all 4 | Typed 422 on blank reason | ✓ |
| Console-only | API accepts null | |
| Required for trip/disable only | Protective actions only | |

**Placement**

| Option | Description | Selected |
|--------|-------------|----------|
| /controls + inline | New page + banner + /strategy, shared dialog | ✓ |
| /controls page only | | |
| Inline only | | |

**Break-glass gap**

| Option | Description | Selected |
|--------|-------------|----------|
| Accept, document risk | No CLI exemption | |
| Keep trip-only CLI | Pinned exemption; requires amending ORCH-01 | ✓ |

**Break-glass shape**

| Option | Description | Selected |
|--------|-------------|----------|
| Worker subcommand, trip-only | kill-switch-trip, reason required, ignores ORCH-07 | ✓ |
| Standalone script, trip-only | | |
| Trip + reset CLI | | |

**Amend requirements**

| Option | Description | Selected |
|--------|-------------|----------|
| Amend now | Edit ORCH-01/08 + ROADMAP SC6 during discussion | ✓ |
| Leave to planner | | |

**Notes:** REQUIREMENTS.md ORCH-01, ORCH-08 and ROADMAP Phase 20 SC6 were amended on 2026-09-27.

**Confirmation strength**

| Option | Description | Selected |
|--------|-------------|----------|
| Dialog + reason | One shared dialog | |
| Typed confirm for reset | Same dialog; kill-switch reset also requires typing RESET | ✓ |
| Typed confirm for all | | |

---

## Bypass classification

| Script | Options | Selected |
|--------|---------|----------|
| dry_run.py (writes StrategyRun) | Retire / Migrate to Job | Retire |
| generate_signals.py (read-only) | Exempt as read-only / Retire | Exempt |
| submit_paper_orders (standalone) | Retire standalone path / Own Job type | Retire |
| export_backtest_report.py (files only) | Exempt as report / Retire | Exempt |

---

## Retry semantics

**Lineage**

| Option | Description | Selected |
|--------|-------------|----------|
| Parent; one retry per Job | UNIQUE(retry_of_job_id); linear chain | ✓ |
| Parent; many retries | Tree | |
| Root; one retry per Job | Flat grouping | |

**Retry with outcome_uncertain**

| Option | Description | Selected |
|--------|-------------|----------|
| Allow + warn | Dialog warns to reconcile first | |
| Block until reconciled | Typed 409 until a reconciliation succeeded after the failure | ✓ |
| Allow, no warning | | |

**Block scope:** paper-session + broker sync (selected) / paper-session only / every type.
**Predicate:** SUCCEEDED reconciliation Job for the same strategy, finished after the original (selected) / reconciliation run with no blocking findings.

**Revalidate payload**

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, 422 if invalid now | Same submit-time validation | ✓ |
| No, copy blindly | | |

**Retry UI**

| Option | Description | Selected |
|--------|-------------|----------|
| Button + lineage links | Retry of / Retried as links | ✓ |
| Button only | | |

---

## Payload explicitness

| Question | Options | Selected |
|----------|---------|----------|
| paper-session risk_run_id | Optional (null = latest) / Required, pre-filled | Optional, null = latest |
| ingest-bars symbols | Required list, pre-filled / Optional (empty = universe) | Required list, pre-filled |
| as_of_session | Required, pre-filled / Optional (null = latest) | Required, pre-filled |

**Notes:** The remaining types follow the same pattern: strategy_id required, sync-market-sessions dates required, sync-symbol-metadata symbols required and pre-filled.

---

## Claude's Discretion

- Type, enum and error-code names
- The domain_conflict signalling mechanism
- Per-type ExecutionMode
- The closed-world boundary-test implementation
- risk_run_id submit-time check
- Symbols validation rules
- Dropping the metadata --dry-run flag
- Console deep-link shortcuts
- Control actor/trigger_source values
- Break-glass documentation location

## Deferred Ideas

- Standalone reconciliation-corrections Job type
- Dependency-chained submission UI
- Stricter retry predicate (no blocking findings)
- ExecutionMode in the catalog
