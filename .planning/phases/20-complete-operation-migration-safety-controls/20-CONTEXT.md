# Phase 20: Complete Operation Migration & Safety Controls - Context

**Gathered:** 2026-09-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Every remaining long-running manual operation becomes a registered, independently validated Job type: `risk-evaluation`, `paper-session`, `reconciliation`, `ingest-bars`, `sync-symbol-metadata`, `sync-market-sessions` and `broker-order-sync`. Final type names are Claude's choice, except the three market-data names, which are roadmap-fixed. Each type gets an API → worker → service E2E test and an explicit console form.

Kill-switch trip/reset and strategy enable/disable become synchronous HTTP control endpoints (Console → HTTP → `OperatorControlService`). They do not depend on the worker, are idempotent by target state and are audited. The operator can explicitly retry a `FAILED`/`CANCELLED` Job with lineage (`retry_of_job_id`).

Every mutation bypass in `scripts/`, `worker/commands/` and the Makefile is deleted. A closed-world boundary test prevents their return. The "exactly two mutating routes" test is replaced by an explicit mutating-route allowlist.

Out of scope (roadmap-fixed):
- new Job types beyond existing operations;
- a composite "sync everything" handler;
- retry policies, backoff, counters or automatic retry;
- cancellation/progress abstractions inside domain services;
- scheduling;
- auth/identity fields;
- the history view and global failure indicator (Phase 21).

</domain>

<decisions>
## Implementation Decisions

### Cancellation contract for broker-touching types (OPS-03)
- **D-01:** Add a new closed `JobCancellationMode` value meaning "cancellable only while queued" (name is Claude's). It applies to `paper-session`, `broker-order-sync` and `reconciliation`.
  - These three types call the broker or write broker-derived state inside one opaque service call.
  - `risk-evaluation`, `ingest-bars`, `sync-symbol-metadata` and `sync-market-sessions` keep `step_boundary`, with pre/post-call checkpoints as in P19 D-12.
- **D-02:** Cancelling a **RUNNING** Job whose type uses the queued-only mode is **rejected** by `JobOrchestrationService` with a typed HTTP `409` and a stable code (e.g. `job_not_cancellable_running`).
  - Nothing is recorded: no `cancellation_requested_at`, no event.
  - As a result, neither the cooperative path nor the 300s cancellation-timeout sweeper ever engages for these Jobs. The catalog statement is true by construction.
  - The `jobs/` framework (`cancellation.py`, `runner.py`) is unchanged.
  - Cancelling a QUEUED Job keeps working as before: atomic → `CANCELLED`, handler never invoked.
  - The QUEUED-vs-RUNNING check must happen inside the same transaction and row lock as the transition, so a cancel racing a claim resolves to exactly one outcome. Pin this with a test.
- **D-03:** "Before broker submission begins" is interpreted as "before the Job starts running". `run_paper_session` performs reconciliation, corrections and submission inside one call, so no meaningful pre-submission checkpoint exists without entering the domain service, which invariant 6 forbids.
  - The catalog description states: cancellable only while queued; once running, the session runs to completion.
  - The OPS-03 test proves that a cancel request after the Job starts running is rejected and does not interrupt submission.
- **D-03a:** Console: on a RUNNING Job whose type's catalog `cancellation_mode` is queued-only, the Cancel control is **disabled** with the inline reason "Not cancellable once running".
  - The check is driven by the catalog `cancellation_mode`, never by `job_type`, so P19 D-17's map discipline holds.
  - The P19 D-14 label function stays generic.
  - If a stale view still sends the cancel, the API's 409 is surfaced as that same message.

### Domain conflicts (OPS-08)
- **D-04:** Add one closed `JobFailureReason` value `domain_conflict`. Handlers translate a small, explicit set of typed domain exceptions into it; today that set is only `ConcurrentRunLockedError`.
  - `failure_message` names the specific conflict (e.g. strategy + session holding the lock).
  - `jobs/` must not import domain exceptions (JOB-04). The handler translates into a framework-level typed signal, and the mechanism is Claude's choice.
  - Needs a migration if the enum is DB-constrained.
- **D-05:** A paper session that returns a **blocked** report (`blocked_strategy_disabled`, `blocked_global_kill_switch`, `blocked_reconciliation`) or a no-op (`noop_*`) is a **SUCCEEDED** Job.
  - The domain decision is surfaced through `result_summary.action`, rendered by the generic key/value view.
  - Jobs never reinterpret domain outcomes (invariant 2). Scope (amended 2026-09-29, see D-08a): this governs domain decisions a service returns as a normal outcome (blocked or no-op paper sessions). It does not forbid a service-owned predicate that classifies a run FAILED and a service-defined typed error that the handler propagates unchanged.

### Reconciliation Job scope (OPS-04)
- **D-06:** The `reconciliation` Job is **report-only**. It calls `reconcile_paper_execution` exactly as the CLI does today.
  - It does not call `apply_reconciliation_corrections`. Corrections still run only inside the paper session (RECON-04: correction is a separate explicit step).
  - No behavior flag.

### Job → output linkage
- **D-07:** Linkage stays **FK-derived at read time** (P19 D-04 holds):
  - drop the UNIQUE constraint on `strategy_runs.job_id` and keep it indexed;
  - add nullable `market_data_ingestion_runs.job_id` (FK → `jobs.id`, indexed);
  - add the closed `resources[].kind` value `market_data_ingestion_run`.
- **D-08:** Expected `resources[]` per type:

  | Type | `resources[]` |
  |------|---------------|
  | `risk-evaluation` | 1 `strategy_run` (risk evaluation) |
  | `reconciliation` | 1 `strategy_run` (reconciliation) |
  | `paper-session` | up to 2 `strategy_run`: the internal reconciliation run and the execution run, both linked |
  | `ingest-bars` | 1 `market_data_ingestion_run` |
  | `sync-symbol-metadata`, `sync-market-sessions`, `broker-order-sync` | empty |

  The three types with no run record report counts in `result_summary` (e.g. synced/failed, sessions upserted, orders_synced/fills_ingested). No new audit tables.
- **D-08a (amended 2026-09-29, UAT gap 1, user decision):** `ingest-bars` failure semantics. The ingestion service (not the handler; invariant 2) derives the run status with the pure predicate `_derive_run_status(succeeded_count, failed_count, run_error)`, where a symbol succeeds when its fetch and upsert complete without raising, including zero bars. The result is `failed` if a run-level error occurred or if zero symbols succeeded (this includes 0 requested, i.e. 0 succeeded and 0 failed, which follows the literal user decision; that row is unreachable through the Job path because `validate_payload` rejects an empty symbols list with `empty_symbols`); `partial` if at least one succeeded and at least one failed; `succeeded` otherwise. On an all-fail run the service writes a deterministic `error_message` built from exception class names only ('0 of N symbols succeeded; failed: T (Class), ...'), and `IngestionResult.raise_for_all_symbols_failed()` raises `IngestionAllSymbolsFailedError` (defined in `services/data.py`). The handler calls it after its completion log and before the post-call cancel checkpoint, so the Job lands FAILED with failure_reason `handler_error` (no new `JobFailureReason` value, no migration) and `failure_message` naming the run id. A cancel requested during an all-fail call therefore also ends FAILED, not CANCELLED. A partial run (at least 1 ok, at least 1 failed) keeps the Job SUCCEEDED, unchanged. Consequence: all-fail Jobs are FAILED and therefore retryable (ingest-bars declares no retry prerequisite). Implemented by plan 20-25.
- **D-09:** The following carry forward to every new type that creates a run:
  - P19 D-02: `job_id` is written in the same transaction that creates the run, and services accept an opaque originating `job_id`.
  - P19 D-07: "Created by Job" back-link.
  - P19 D-11: `trigger_source = "job"`. The paper session's internal reconciliation run may derive its trigger_source as today.
  - The P19 D-06 test generalizes: every run this Job **created**, as reported by the handler under an explicit produced-run-ids key in `result_summary`, appears in `resources[]`.
  - Referenced inputs are not resources. For example, the paper session's `source_risk_run_id` belongs to a risk-evaluation run whose `job_id` points at a different Job.

### Safety controls API (CTRL-01/02)
- **D-10:** Endpoints use **PUT by target state**:
  - `PUT /api/v1/controls/kill-switch` with body `{state: "tripped" | "armed", reason}`;
  - `PUT /api/v1/controls/strategies/{strategy_id}` with body `{status: "enabled" | "disabled", reason}`.

  Behavior:
  - Both call `OperatorControlService` synchronously, with no Job, no worker and no `Idempotency-Key`.
  - The response carries the resulting state plus `changed: bool`.
  - Requesting the current state returns it and still writes the existing unchanged audit rows (`OPERATOR_CONTROL` run + `ExecutionEvent`).
  - An unknown strategy returns `404`, following the existing `resolve_strategy_metadata` pattern. An invalid target returns `422`.
  - Both routes are subject to the ORCH-07 guard (403 first, per P19 ordering).
- **D-11:** **Reason is required by the API** for all four actions. It is trimmed, and a blank/missing value or one over 500 chars returns a typed `422` with zero writes. The audit row therefore always carries a reason.
- **D-11a (amended 2026-09-29, UAT gap 4):** Control audit timestamps come from a single DB clock read (`clock_timestamp()`) taken inside the mutating transaction, after the row lock. That one value, normalized to UTC, is written to `StrategyRun.completed_at`, `ExecutionEvent.event_at`, `SystemControl.last_changed_at` (on a change only) and `result_summary.changed_at`, so every OPERATOR_CONTROL run satisfies `completed_at >= started_at`, including under lock waits. There is no DB CHECK constraint; that is an open user decision, deferred. **Decided 2026-09-29 (user): no constraint and no migration 0022; the 25 pre-fix inverted rows stay as historical records.** Implemented by plan 20-28.
- **D-12:** The mutating-route allowlist test pins **exactly five** routes, replacing both "exactly two" tests in `tests/test_orchestration_boundaries.py`:
  - `POST /api/v1/jobs`
  - `POST /api/v1/jobs/{job_id}/cancel`
  - `POST /api/v1/jobs/{job_id}/retry`
  - `PUT /api/v1/controls/kill-switch`
  - `PUT /api/v1/controls/strategies/{strategy_id}`

  All five sit behind the ORCH-07 guard.

### Safety controls console
- **D-13:** Controls appear in three places:
  - a new **`/controls` page** with a "Controls" nav link, holding kill-switch and strategy controls;
  - inline **Trip/Reset** on `KillSwitchBanner`;
  - inline **Enable/Disable** on `/strategy`.

  All three use one shared confirmation-dialog component and the single `console/src/lib/api.ts` client.
- **D-14:** Dialog behavior:
  - It shows current state → target state and a required reason field. Submit stays disabled until the reason is non-blank.
  - **Kill-switch reset additionally requires typing `RESET`**, because re-arming allows trading.
  - Trip, enable and disable are one step plus reason, so an emergency trip stays fast.
  - A `changed: false` response shows "Already <state> — no change (recorded)".
  - When mutations are disabled, the controls are visible but disabled with the inline reason (P19 D-21).

### Break-glass kill-switch trip (ORCH-01 exception, requirements amended)
- **D-15:** One **trip-only worker subcommand** `kill-switch-trip` with a required `--reason`:
  - It calls `OperatorControlService.trip_kill_switch` with `trigger_source="break_glass_cli"` and writes the same audit rows.
  - It exists so the kill switch can be tripped when the API is down.
  - It ignores ORCH-07: that flag guards HTTP, and shell access is already privileged.
  - It needs DB-only config (no broker creds).
  - There is **no** reset, enable or disable CLI.
  - It is pinned by name in the boundary exemption list. The test asserts no other script, worker command or Makefile target calls an `OperatorControlService` mutator, and that no reset CLI exists.
  - These docs were amended during this discussion to name this exemption. No planner action is needed on the wording:
    - REQUIREMENTS.md: ORCH-01, ORCH-08 and the milestone scope rule;
    - ROADMAP Phase 20 SC6;
    - PROJECT.md: invariant 1 and the milestone goal.

### Operator retry (OPS-07)
- **D-16:** `POST /api/v1/jobs/{job_id}/retry`, idempotent by `Idempotency-Key` under the P18 contract:
  - key scoped per endpoint, canonical identity = target Job id;
  - exact replay → `200` + `Idempotency-Replayed: true` + the same retry Job;
  - a key reused for a different target → `409`.
  - The retry creates a new Job with the same `job_type` and the same normalized payload, with `retry_of_job_id` = the **immediate parent** (chain A←B←C).
  - Dependencies are **not** copied.
- **D-17:** Add a **UNIQUE** constraint on `retry_of_job_id`, so each Job has at most one retry. A fresh-key retry of a Job that already has one returns a typed `409` carrying the existing retry's id.
  - Retrying a non-terminal or `SUCCEEDED` Job also returns a typed `409`.
  - No automatic retry path exists anywhere.
- **D-18:** Retry **re-runs `validate_payload`** on the copied payload. If it is no longer valid (e.g. the strategy is unregistered) the result is a typed `422` with zero rows. The payload is never modified.
- **D-19 — SUPERSEDED 2026-09-30 by Phase 20.1 D-15 / REQUIREMENTS REC-01** (see `.planning/phases/20.1-operator-state-correctness/20.1-CONTEXT.md` and `research/operator-console-ia/03-PLANNING-CHANGES.md` §3.7 D). The replacement rule:
  - every `paper-session` submission, fresh or retry, is gated while an uncertain outcome for the strategy is unresolved;
  - resolution requires per-intent broker-state classification plus a fresh clean standalone reconciliation; a `SUCCEEDED` reconciliation whose result blocks no longer lifts the block;
  - `broker-order-sync` is never gated;
  - the orchestration layer calls a domain read predicate, reversing the "jobs table only" clause.

  Original text kept for history:
- **D-19 (original):** A **reconcile-first block** applies to `paper-session` and `broker-order-sync` only.
  - When the original Job is `FAILED` with `outcome_uncertain=true`, retry is rejected with a typed `409` `reconciliation_required`.
  - The block lifts once a `reconciliation` Job exists that meets all of: status `SUCCEEDED`, same `strategy_id`, `finished_at` later than the original's `finished_at`.
  - The check reads only the jobs table in the orchestration layer, with no domain-report coupling. A reconciliation that found blocking findings still counts, because the paper session itself blocks on those findings.
  - All other types retry normally regardless of `outcome_uncertain`.
  - Which types need the block is declared per type (e.g. a submission-spec attribute; mechanism is Claude's).
- **D-20** _(amended 2026-09-30: during the API-only interim, paper-session Retry is hidden in the legacy console (Phase 20.1 COMPAT-01); retry-block reasons gain `outcome_unresolved` and `reconciliation_not_clean`)_: Retry UI:
  - a "Retry" button on `FAILED`/`CANCELLED` Job detail;
  - the button is disabled with a reason when a retry already exists (links to it), when mutations are off, or when D-19 blocks. The blocked message links to `/jobs/new?type=reconciliation&strategy_id=…` pre-filled;
  - the confirm dialog shows type and payload;
  - detail shows "Retry of Job X" and "Retried as Job Y" links;
  - a successful retry navigates to the new Job.
  - Lineage is exposed through the Job detail API: `retry_of_job_id` plus reverse `retried_as_job_id`, derived at read time.

### Payloads (P19 D-08 carried forward: explicit, never defaulted in `validate_payload`, pre-filled via `submission_defaults`)
- **D-21:** `as_of_session` is **required** for `risk-evaluation`, `paper-session`, `reconciliation` and `broker-order-sync`.
  - It is pre-filled with the latest completed session.
  - It is validated as an exchange trading session and not in the future, using the injectable exchange clock.
- **D-22:** `strategy_id` is **required** for every strategy-scoped type and checked against the strategy registry. The console pre-fills it.
- **D-23** _(amended 2026-09-30 by Phase 20.1 D-16/D-19/D-23: `as_of_session` is the **evaluation (data) session**, and execution must fall inside its execution window; a `null` `risk_run_id` is resolved to a concrete id at the first submission, so retries and continuations replay the same intents; a new `mode: continue` with `operation_id` resumes a paused execution operation)_: Paper session payload is `{strategy_id, as_of_session, risk_run_id | null}`.
  - `null` means the service picks the latest succeeded risk run at run time, as today.
  - The consumed risk run stays recorded in the domain (`source_risk_run_id`).
  - A retry of a `null` payload may consume a newer risk run, which is intended.
- **D-24:** `ingest-bars` payload is `{from_date, to_date, symbols: [...]}`, all required.
  - `symbols` is pre-filled with the configured universe and normalized (upper-case, de-duplicated, sorted) so the fingerprint is stable.
- **D-25:** Payloads for the remaining types:
  - `sync-symbol-metadata`: `{symbols: [...]}`, required and pre-filled/normalized the same way;
  - `sync-market-sessions`: `{from_date, to_date}`, required.
  - All payloads use strict schemas (`extra="forbid"`), and every rejection has a stable machine-readable reason and a test.

### Bypass retirement (ORCH-01/02/08)
- **D-26:** Delete these scripts together with their Makefile targets:
  - `scripts/evaluate_risk.py`
  - `scripts/run_paper_session.py`
  - `scripts/reconcile_paper_execution.py`
  - `scripts/ingest_polygon_bars.py`
  - `scripts/sync_symbol_metadata.py`
  - `scripts/sync_paper_state.py`
  - `scripts/run_backtest.py`
  - `scripts/operator_control.py`
  - `scripts/submit_paper_orders.py`
  - `scripts/dry_run.py`
- **D-27:** `dry_run.py` is **retired**. It writes a StrategyRun via `run_dry_bootstrap`, so it is mutating and gets no exemption.
  - Remove the `dry-run` Makefile target, `run_dry_bootstrap` and the worker `bootstrap` command.
  - Remove the `bootstrap` service where it is otherwise unused; the planner verifies usage.
  - Update `tests/test_dry_run.py` and the Makefile `test` list.
- **D-28:** The standalone `submit_paper_orders` path is **retired**, including the script and Makefile target. `run_paper_order_submission` stays because the paper session uses it. There is exactly one path to broker submission: the `paper-session` Job.
- **D-29:** Pinned exemptions, each with a recorded reason:
  - `migrate` and `seed_phase1` (deployment tooling);
  - `generate_signals.py` (read-only: evaluates the strategy against persisted bars and writes nothing, verified transitively);
  - `export_backtest_report.py` (report: reads the DB, writes local files only);
  - `operator_status.py` and `report_strategy_analytics.py` (read/report). The planner verifies each is read-only; if one writes, reclassify it.
  - the `kill-switch-trip` worker subcommand (D-15).

  Kept Makefile targets: `up`, `down`, `logs`, `migrate`, `seed`, `export-backtest-report`, `generate-signals`, `test`, `console`, `console-install`.
- **D-30:** Remove dead code:
  - the dead worker functions (`paper_execute.py`, `reconcile.py`, `risk_check.py`, `ingest.py`, the `backtest.py` run function, the `operator.py` control action);
  - the `serve` / `run_placeholder_worker` path, which is dead after ORCH-05;
  - the Makefile targets calling non-existent worker subcommands (`dry-run`, `sync-sessions`).

  The final worker DISPATCH is `report-backtest`, `report-strategy-analytics`, `operator-status`, `run-jobs`, `kill-switch-trip`.

### Read-path purity for D-29 exemptions (amended 2026-09-27, during plan-phase)
- **D-31:** The D-29 read-only verification found writes. `export_backtest_report.py` and `report_strategy_analytics.py` both reach `materialize_backtest_report` → `_upsert_backtest_metric`. `operator_status.py` reaches `OperatorControlService.get_strategy_state` → `ensure_strategy_record` (upsert + flush). The same upsert also runs under `GET /api/v1/analytics/strategies/{id}`. The operator chose to make these read paths genuinely read-only rather than retire the scripts or loosen the exemption:
  - Split `materialize_backtest_report` into a pure compute/serialize read function, which never calls `session.add`, `flush` or `commit`, and a separate persist step. The `BacktestMetric` upsert moves to backtest Job completion (inside the backtest Job handler/service path), so the metric row is still written exactly once per successful backtest run. Report scripts, `StrategyAnalyticsService` and the analytics GET route use only the pure function.
  - `get_strategy_state` (and `load_strategy_control_state` where it serves read/report callers) uses a plain `select`. When no `Strategy` row exists, it returns registry-default control state and does not insert. Mutating callers such as enable/disable and the paper submission path keep using `ensure_strategy_record`.
  - D-29's dispositions and kept Makefile targets are unchanged, and the stated reasons are now literally true. The boundary test's pinned mutating-entry-point set includes `_upsert_backtest_metric`/the persist step and `ensure_strategy_record`. A test proves the three exempt scripts and the analytics GET route perform zero DB writes, for example via a session flush/commit spy or a row-count/`updated_at` invariance check.
  - Any repo-wide AST/boundary scan excludes `.claude/worktrees/`, which holds a stale copy of `src/`.

### Claude's Discretion
- Exact Job type names (market-data names are fixed), the new cancellation-mode enum name, and the typed error code strings (`job_not_cancellable_running`, `reconciliation_required`, retry-exists, etc.).
- The mechanism by which a handler signals `domain_conflict` to the runner without `jobs/` importing domain code.
- Per-type `ExecutionMode` declarations (P19 D-22): the paper-family types (`paper-session`, `reconciliation`, `broker-order-sync`) declare `PAPER`. For `risk-evaluation` and the market-data types, match what their CLIs validated (BACKTEST) plus whatever the Polygon key requirement needs.
- Boundary-test implementation. Strongly preferred: **closed-world**. Every file in `scripts/`, every worker DISPATCH entry and every Makefile target must appear in either the "allowed" list (with reason) or be absent, so a new script fails the test until classified. Detect mutating calls via an explicit pinned set of mutating service entry points.
- Submit-time validation of a non-null `risk_run_id`: recommended check is that it references a SUCCEEDED risk-evaluation run for the same strategy and session, with a typed 422 otherwise.
- `symbols` validation rules (ticker format, max count). Restricting to the configured universe is not required.
- The `sync-symbol-metadata` CLI `--dry-run` flag is dropped: a Job is never a dry run, and OPS-05 forbids behavior flags.
- Console shortcuts that deep-link into forms (e.g. "Run paper session" on `/paper`, "Evaluate risk" on `/strategy`), following the P19 D-18 pattern.
- The actor/trigger_source values the control endpoints pass to `OperatorControlService` (single operator; no identity schema).
- Where the break-glass command is documented (README/runbook note). Whether to add a Makefile target for it is optional; if added, it must be the one pinned exemption.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Milestone contracts
- `.planning/ROADMAP.md` § Phase 20: goal, 7 success criteria, out-of-scope list. SC6 was amended 2026-09-27 for the break-glass exemption.
- `.planning/REQUIREMENTS.md`: OPS-02..08, CTRL-01/02, ORCH-01/02/08 (ORCH-01/08 amended 2026-09-27), ORCH-03 (the retry idempotency extension).
- `.planning/PROJECT.md`: architecture invariants 1–9 (two mutation paths; Jobs orchestration-only; cooperative cancellation limitation; audit from existing records).

### Prior phase decisions
- `.planning/phases/17-job-framework/17-CONTEXT.md`: D-01..D-03 (crash → FAILED, outcome_uncertain, no auto-retry) and D-07..D-10 (cancellation semantics).
- `.planning/phases/18-orchestration-surface/18-CONTEXT.md`: D-06..D-10 (idempotency contract that retry extends), D-11..D-15 (cancel API), D-16..D-20 (Job reference shape).
- `.planning/phases/19-job-operations-vertical-slice/19-CONTEXT.md`: D-01..D-07 (FK linkage, resources[]), D-08..D-11 (payload explicitness), D-12..D-16 (cancel honesty), D-17..D-21 (forms, map discipline, mutation guard UX), D-22 (per-type config mode).

### Implementation anchors
- `src/trading_platform/jobs/registry.py`: `JobCancellationMode` (add the queued-only value), `JobSubmissionSpec`, `build_default_registry` (append 7 registrations).
- `src/trading_platform/jobs/handlers/backtest.py`, `backtest_submission.py`: the pattern for every new handler and spec.
- `src/trading_platform/jobs/cancellation.py`: `CANCELLATION_GRACE_SECONDS` = 300 and the sweeper, which D-02 keeps from engaging.
- `src/trading_platform/orchestration/job_mutations.py`: cancel (add the queued-only rejection) and retry (new).
- `src/trading_platform/db/models/job.py`: `JobFailureReason` (add `domain_conflict`) and `retry_of_job_id`.
- `src/trading_platform/db/models/strategy_run.py`: drop UNIQUE on `job_id`.
- `src/trading_platform/services/job_reads.py`: `resources[]` (multi-run, new kind) and retry lineage fields.
- `src/trading_platform/services/operator_controls.py`: `OperatorControlService` enable/disable/trip/reset, with changed/unchanged audit semantics.
- `src/trading_platform/services/execution/submit_orders.py`: `run_paper_session` (one opaque call: recon → corrections → submission) and `run_paper_order_submission`.
- `src/trading_platform/services/execution/sync_orders.py`: `sync_paper_state` (broker-order-sync).
- `src/trading_platform/services/reconciliation/report.py`: `reconcile_paper_execution` and `_create_reconciliation_run`.
- `src/trading_platform/services/risk.py`: `run_risk_evaluation`, `resolve_evaluation_session`, `_create_risk_run`.
- `src/trading_platform/services/ingestion.py`: `ingest_daily_bars` and `_start_run` (`MarketDataIngestionRun`).
- `src/trading_platform/services/calendar.py`: `upsert_market_sessions` (sync-market-sessions).
- `scripts/sync_symbol_metadata.py`: contains the metadata-sync business logic (`_fetch_ticker_overview`, `_upsert_symbol_metadata`), which must move into `services/` before deletion.
- `src/trading_platform/services/concurrency_guard.py`: `ConcurrentRunLockedError` (→ `domain_conflict`).
- `src/trading_platform/worker/commands/__init__.py` (DISPATCH), `worker/parser.py`, `worker/commands/*`: remove dead commands and add `kill-switch-trip`.
- `src/trading_platform/api/routes/jobs.py`: the retry route. New control router (e.g. `api/routes/controls.py`).
- `tests/test_orchestration_boundaries.py`: replace the "exactly two" tests (lines ~140–172) with the five-route allowlist, and extend the boundary test to `scripts/` and the Makefile.
- `Makefile`: target retirement (D-26..D-30).
- `console/src/lib/api.ts`: the sole fetch site; add retry and control mutations.
- The console form map module (P19 D-17): add 7 forms.
- `console/src/components/KillSwitchBanner.tsx`, `console/src/app/strategy/`, `console/src/app/layout.tsx` (nav), and the new `console/src/app/controls/`.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `JobRegistry.register(handler, submission_spec=...)` with `submission_defaults()`: every new type follows the backtest handler/spec pair.
- `JobOrchestrationService`: submit/cancel/idempotency are complete. Retry reuses the same key/fingerprint machinery, and cancel gains one mode check.
- `OperatorControlService`: all four control operations already exist, with changed/unchanged audit semantics. The HTTP routes are thin adapters.
- Existing kill-switch GET route and `KillSwitchBanner`: the read side of controls exists.
- `resolve_submission_session` / `resolve_evaluation_session`: the logic behind "latest completed session" for `submission_defaults`. It must not be used inside `validate_payload` (P19 D-08).
- P19 console primitives: `useApiQuery` polling, the generic Job detail, the cancel dialog (the pattern for the control and retry dialogs), the resource-link component (kind → route map), and the generic key/value `result_summary` view.

### Established Patterns
- Handlers call `services.*` only. They never write `jobs` lifecycle rows, and no DB transaction stays open across `handler.run`.
- Closed enums, typed exceptions and DB constraints are preferred over convention. Every rejection has a stable code and a test.
- The console has exactly two lookup maps (job_type → form, resource kind → route). List/detail components never branch on job_type.
- The ORCH-07 guard runs first (403) on every mutating route.

### Integration Points
- New migration(s):
  - drop UNIQUE on `strategy_runs.job_id`;
  - add `market_data_ingestion_runs.job_id` FK;
  - add `jobs.retry_of_job_id` (nullable FK → `jobs.id`, UNIQUE);
  - add `JobFailureReason.domain_conflict`.
- Services gain an opaque `job_id` parameter where they create runs: `run_risk_evaluation`, `reconcile_paper_execution`, `run_paper_session` (threaded to its internal recon and execution runs), and `ingest_daily_bars`.
- The metadata-sync logic is extracted from `scripts/sync_symbol_metadata.py` into a service module. The worker's importlib hack goes away.
- The paper-session internal flow calls `reconcile_paper_execution` itself. `job_id` threading must reach that internal reconciliation run.
- Existing tests that exercise deleted scripts or CLI commands must be migrated to service-level or Job-level tests, not dropped. Tests of the `worker` subcommands that are removed get deleted along with those subcommands.
- D-19 (mutations disabled by default) still applies. The new E2E and route tests must enable the flag explicitly, and the disabled-default test must cover the control and retry routes.

</code_context>

<specifics>
## Specific Ideas

- Honesty over convenience:
  - never label a paper session CANCELLED after it submitted orders;
  - never let the sweeper mark a non-interruptible submission FAILED/`cancellation_timeout`;
  - blocked sessions are SUCCEEDED with the domain decision visible, never re-interpreted.
- The user wants a break-glass path to trip the kill switch when the API is down, and accepted a narrow, pinned, trip-only exception to ORCH-01 in exchange.
- Re-arming the kill switch is deliberately higher-friction (typed `RESET`) than tripping it.
- Retrying a broker-touching Job with an uncertain outcome must be preceded by a reconciliation. The retry block is enforced server-side, not just warned about.

</specifics>

<deferred>
## Deferred Ideas

- A standalone "apply reconciliation corrections" Job type. Rejected for Phase 20 (D-06); revisit if operators need corrections outside a paper session.
- Dependency-chained submission from the console (e.g. risk-evaluation → paper-session via Job dependencies). The framework supports it (invariant 7), but a UI for composing chains is a new capability.
- A stricter retry predicate that requires a reconciliation report with no blocking findings. Rejected in favor of the jobs-table-only check (D-19).
- Exposing a Job type's required `ExecutionMode` in the catalog. Still deferred (P19 D-23); revisit if paper-family `config_invalid` failures become common.

</deferred>

---

*Phase: 20-complete-operation-migration-safety-controls*
*Context gathered: 2026-09-27*
