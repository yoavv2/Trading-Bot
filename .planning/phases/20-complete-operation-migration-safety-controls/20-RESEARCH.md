# Phase 20: Complete Operation Migration & Safety Controls - Research

**Researched:** 2026-09-27
**Domain:** Job-framework operation migration (backend orchestration + Next.js console), safety-control HTTP endpoints, closed-world boundary enforcement
**Confidence:** HIGH

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions

**Phase Boundary:** Every remaining long-running manual operation becomes a registered, independently validated Job type: `risk-evaluation`, `paper-session`, `reconciliation`, `ingest-bars`, `sync-symbol-metadata`, `sync-market-sessions` and `broker-order-sync`. Final type names are Claude's choice, except the three market-data names, which are roadmap-fixed. Each type gets an API → worker → service E2E test and an explicit console form. Kill-switch trip/reset and strategy enable/disable become synchronous HTTP control endpoints (Console → HTTP → `OperatorControlService`). They do not depend on the worker, are idempotent by target state and are audited. The operator can explicitly retry a `FAILED`/`CANCELLED` Job with lineage (`retry_of_job_id`). Every mutation bypass in `scripts/`, `worker/commands/` and the Makefile is deleted. A closed-world boundary test prevents their return. The "exactly two mutating routes" test is replaced by an explicit mutating-route allowlist.

Out of scope (roadmap-fixed): new Job types beyond existing operations; a composite "sync everything" handler; retry policies, backoff, counters or automatic retry; cancellation/progress abstractions inside domain services; scheduling; auth/identity fields; the history view and global failure indicator (Phase 21).

### Cancellation contract for broker-touching types (OPS-03)
- **D-01:** Add a new closed `JobCancellationMode` value meaning "cancellable only while queued" (name is Claude's). It applies to `paper-session`, `broker-order-sync` and `reconciliation`. These three types call the broker or write broker-derived state inside one opaque service call. `risk-evaluation`, `ingest-bars`, `sync-symbol-metadata` and `sync-market-sessions` keep `step_boundary`, with pre/post-call checkpoints as in P19 D-12.
- **D-02:** Cancelling a **RUNNING** Job whose type uses the queued-only mode is **rejected** by `JobOrchestrationService` with a typed HTTP `409` and a stable code (e.g. `job_not_cancellable_running`). Nothing is recorded: no `cancellation_requested_at`, no event. As a result, neither the cooperative path nor the 300s cancellation-timeout sweeper ever engages for these Jobs. The catalog statement is true by construction. The `jobs/` framework (`cancellation.py`, `runner.py`) is unchanged **with respect to the cancellation mechanism itself** — this scoping matters; see "Verification of CONTEXT.md Decisions" below for a `runner.py` change this phase does need for a different reason (OPS-08). Cancelling a QUEUED Job keeps working as before: atomic → `CANCELLED`, handler never invoked. The QUEUED-vs-RUNNING check must happen inside the same transaction and row lock as the transition, so a cancel racing a claim resolves to exactly one outcome. Pin this with a test.
- **D-03:** "Before broker submission begins" is interpreted as "before the Job starts running". `run_paper_session` performs reconciliation, corrections and submission inside one call, so no meaningful pre-submission checkpoint exists without entering the domain service, which invariant 6 forbids. The catalog description states: cancellable only while queued; once running, the session runs to completion. The OPS-03 test proves that a cancel request after the Job starts running is rejected and does not interrupt submission.
- **D-03a:** Console: on a RUNNING Job whose type's catalog `cancellation_mode` is queued-only, the Cancel control is **disabled** with the inline reason "Not cancellable once running". The check is driven by the catalog `cancellation_mode`, never by `job_type`, so P19 D-17's map discipline holds. The P19 D-14 label function stays generic. If a stale view still sends the cancel, the API's 409 is surfaced as that same message.

### Domain conflicts (OPS-08)
- **D-04:** Add one closed `JobFailureReason` value `domain_conflict`. Handlers translate a small, explicit set of typed domain exceptions into it; today that set is only `ConcurrentRunLockedError`. `failure_message` names the specific conflict (e.g. strategy + session holding the lock). `jobs/` must not import domain exceptions (JOB-04). The handler translates into a framework-level typed signal, and the mechanism is Claude's choice. Needs a migration if the enum is DB-constrained.
- **D-05:** A paper session that returns a **blocked** report (`blocked_strategy_disabled`, `blocked_global_kill_switch`, `blocked_reconciliation`) or a no-op (`noop_*`) is a **SUCCEEDED** Job. The domain decision is surfaced through `result_summary.action`, rendered by the generic key/value view. Jobs never reinterpret domain outcomes (invariant 2).

### Reconciliation Job scope (OPS-04)
- **D-06:** The `reconciliation` Job is **report-only**. It calls `reconcile_paper_execution` exactly as the CLI does today. It does not call `apply_reconciliation_corrections`. Corrections still run only inside the paper session (RECON-04: correction is a separate explicit step). No behavior flag.

### Job → output linkage
- **D-07:** Linkage stays **FK-derived at read time** (P19 D-04 holds): drop the UNIQUE constraint on `strategy_runs.job_id` and keep it indexed; add nullable `market_data_ingestion_runs.job_id` (FK → `jobs.id`, indexed); add the closed `resources[].kind` value `market_data_ingestion_run`.
- **D-08:** Expected `resources[]` per type: `risk-evaluation` → 1 `strategy_run` (risk evaluation); `reconciliation` → 1 `strategy_run` (reconciliation); `paper-session` → up to 2 `strategy_run`: the internal reconciliation run and the execution run, both linked; `ingest-bars` → 1 `market_data_ingestion_run`; `sync-symbol-metadata`, `sync-market-sessions`, `broker-order-sync` → empty. The three types with no run record report counts in `result_summary` (e.g. synced/failed, sessions upserted, orders_synced/fills_ingested). No new audit tables.
- **D-09:** The following carry forward to every new type that creates a run: P19 D-02: `job_id` is written in the same transaction that creates the run, and services accept an opaque originating `job_id`. P19 D-07: "Created by Job" back-link. P19 D-11: `trigger_source = "job"`. The paper session's internal reconciliation run may derive its trigger_source as today. The P19 D-06 test generalizes: every run this Job **created**, as reported by the handler under an explicit produced-run-ids key in `result_summary`, appears in `resources[]`. Referenced inputs are not resources — the paper session's `source_risk_run_id` belongs to a risk-evaluation run whose `job_id` points at a different Job.

### Safety controls API (CTRL-01/02)
- **D-10:** Endpoints use **PUT by target state**: `PUT /api/v1/controls/kill-switch` `{state: "tripped"|"armed", reason}`; `PUT /api/v1/controls/strategies/{strategy_id}` `{status: "enabled"|"disabled", reason}`. Both call `OperatorControlService` synchronously, no Job, no worker, no `Idempotency-Key`. Response carries resulting state plus `changed: bool`. Requesting the current state returns it and still writes the existing unchanged audit rows (`OPERATOR_CONTROL` run + `ExecutionEvent`). Unknown strategy → `404` (`resolve_strategy_metadata` pattern). Invalid target → `422`. Both routes subject to ORCH-07 guard (403 first).
- **D-11:** **Reason is required by the API** for all four actions. Trimmed; blank/missing or >500 chars → typed `422`, zero writes. Audit row always carries a reason.
- **D-12:** The mutating-route allowlist test pins **exactly five** routes, replacing both "exactly two" tests in `tests/test_orchestration_boundaries.py`: `POST /api/v1/jobs`, `POST /api/v1/jobs/{job_id}/cancel`, `POST /api/v1/jobs/{job_id}/retry`, `PUT /api/v1/controls/kill-switch`, `PUT /api/v1/controls/strategies/{strategy_id}`. All five sit behind the ORCH-07 guard.

### Safety controls console
- **D-13:** Controls appear in three places: a new `/controls` page with a "Controls" nav link; inline Trip/Reset on `KillSwitchBanner`; inline Enable/Disable on `/strategy`. All three use one shared confirmation-dialog component and the single `console/src/lib/api.ts` client.
- **D-14:** Dialog shows current→target state and a required reason field; submit stays disabled until non-blank. Kill-switch reset additionally requires typing `RESET`. Trip/enable/disable are one step plus reason. A `changed: false` response shows "Already <state> — no change (recorded)". When mutations are disabled, controls are visible but disabled with the inline reason (P19 D-21).

### Break-glass kill-switch trip (ORCH-01 exception, requirements amended)
- **D-15:** One **trip-only worker subcommand** `kill-switch-trip` with required `--reason`: calls `OperatorControlService.trip_kill_switch` with `trigger_source="break_glass_cli"`, writes the same audit rows; exists so the kill switch can be tripped when the API is down; ignores ORCH-07 (shell access already privileged); needs DB-only config (no broker creds); no reset/enable/disable CLI. Pinned by name in the boundary exemption list; test asserts no other script/worker command/Makefile target calls an `OperatorControlService` mutator, and that no reset CLI exists. REQUIREMENTS.md ORCH-01/ORCH-08/milestone scope rule, ROADMAP SC6, and PROJECT.md invariant 1/milestone goal were already amended for this — no planner action needed on wording.

### Operator retry (OPS-07)
- **D-16:** `POST /api/v1/jobs/{job_id}/retry`, idempotent by `Idempotency-Key` under the P18 contract: key scoped per endpoint, canonical identity = target Job id; exact replay → `200` + `Idempotency-Replayed: true` + same retry Job; key reused for a different target → `409`. Retry creates a new Job with same `job_type` + same normalized payload, `retry_of_job_id` = the **immediate parent** (chain A←B←C). Dependencies are **not** copied.
- **D-17:** Add a **UNIQUE** constraint on `retry_of_job_id` — each Job has at most one retry. A fresh-key retry of an already-retried Job → typed `409` carrying the existing retry's id. Retrying a non-terminal or `SUCCEEDED` Job also → typed `409`. No automatic retry path exists anywhere.
- **D-18:** Retry **re-runs `validate_payload`** on the copied payload. If no longer valid → typed `422`, zero rows. Payload never modified.
- **D-19:** A **reconcile-first block** applies to `paper-session` and `broker-order-sync` only. When the original Job is `FAILED` with `outcome_uncertain=true`, retry is rejected with typed `409` `reconciliation_required`. The block lifts once a `reconciliation` Job exists that meets ALL of: status `SUCCEEDED`, same `strategy_id`, `finished_at` later than the original's `finished_at`. The check reads only the jobs table in the orchestration layer, no domain-report coupling. All other types retry normally regardless of `outcome_uncertain`. Which types need the block is declared per type (mechanism is Claude's). **Note (verified this session): the `Job` model's terminal timestamp column is named `completed_at`, not `finished_at` — CONTEXT.md's prose uses `finished_at` informally; the actual comparison must use `Job.completed_at`.**
- **D-20:** Retry UI: "Retry" button on `FAILED`/`CANCELLED` Job detail; disabled with a reason when a retry already exists (links to it), mutations off, or D-19 blocks (links to `/jobs/new?type=reconciliation&strategy_id=…`); confirm dialog shows type+payload; detail shows "Retry of Job X"/"Retried as Job Y" links; successful retry navigates to the new Job. Lineage exposed via `retry_of_job_id` + reverse `retried_as_job_id`, derived at read time.

### Payloads (P19 D-08 carried forward)
- **D-21:** `as_of_session` **required** for `risk-evaluation`, `paper-session`, `reconciliation`, `broker-order-sync`. Pre-filled with latest completed session. Validated as an exchange trading session, not in the future, via injectable exchange clock.
- **D-22:** `strategy_id` **required** for every strategy-scoped type, checked against strategy registry. Console pre-fills it.
- **D-23:** Paper session payload `{strategy_id, as_of_session, risk_run_id | null}`. `null` = service picks latest succeeded risk run at run time (recorded in domain as `source_risk_run_id`). A retry of a `null` payload may consume a newer risk run — intended.
- **D-24:** `ingest-bars` payload `{from_date, to_date, symbols: [...]}`, all required. `symbols` pre-filled with configured universe, normalized (upper-case, de-duplicated, sorted) for a stable fingerprint.
- **D-25:** `sync-symbol-metadata`: `{symbols: [...]}`, required, same normalization. `sync-market-sessions`: `{from_date, to_date}`, required. All payloads use strict schemas (`extra="forbid"`); every rejection has a stable machine-readable reason and a test.

### Bypass retirement (ORCH-01/02/08)
- **D-26:** Delete these scripts together with their Makefile targets: `scripts/evaluate_risk.py`, `scripts/run_paper_session.py`, `scripts/reconcile_paper_execution.py`, `scripts/ingest_polygon_bars.py`, `scripts/sync_symbol_metadata.py`, `scripts/sync_paper_state.py`, `scripts/run_backtest.py`, `scripts/operator_control.py`, `scripts/submit_paper_orders.py`, `scripts/dry_run.py`.
- **D-27:** `dry_run.py` is **retired**. It writes a StrategyRun via `run_dry_bootstrap`, so it is mutating and gets no exemption. Remove the `dry-run` Makefile target, `run_dry_bootstrap` and the worker `bootstrap` command. Remove the `bootstrap` service **where it is otherwise unused; the planner verifies usage** (verified this session: `services/bootstrap.py`'s `ensure_strategy_record`/`create_strategy_run` are used by 6+ other modules and MUST stay — only the `run_dry_bootstrap` function, and `worker/commands/bootstrap.py` in its entirety, are removable). Update `tests/test_dry_run.py` and the Makefile `test` list.
- **D-28:** The standalone `submit_paper_orders` path is **retired**, including the script and Makefile target. `run_paper_order_submission` stays because the paper session uses it. There is exactly one path to broker submission: the `paper-session` Job.
- **D-29:** Pinned exemptions, each with a recorded reason: `migrate` and `seed_phase1` (deployment tooling); `generate_signals.py` (read-only: evaluates the strategy against persisted bars and writes nothing, verified transitively); `export_backtest_report.py` (report: reads the DB, writes local files only — **verified this session to be inaccurate as stated; see "Verification" below, flagged as an open item for the planner, not silently corrected**); `operator_status.py` and `report_strategy_analytics.py` (read/report — **`operator_status.py` also has a caveat, see below**). The planner verifies each is read-only; if one writes, reclassify it. Kept Makefile targets: `up`, `down`, `logs`, `migrate`, `seed`, `export-backtest-report`, `generate-signals`, `test`, `console`, `console-install`.
- **D-30:** Remove dead code: the dead worker functions (`paper_execute.py`, `reconcile.py`, `risk_check.py`, `ingest.py`, the `backtest.py` run function, the `operator.py` control action); the `serve` / `run_placeholder_worker` path, which is dead after ORCH-05; the Makefile targets calling non-existent worker subcommands (`dry-run`, `sync-sessions`). **The final worker DISPATCH is `report-backtest`, `report-strategy-analytics`, `operator-status`, `run-jobs`, `kill-switch-trip` — `serve` is NOT in the final set.**

### Claude's Discretion

- Exact Job type names (market-data names are fixed), the new cancellation-mode enum name, and the typed error code strings (`job_not_cancellable_running`, `reconciliation_required`, retry-exists, etc.).
- The mechanism by which a handler signals `domain_conflict` to the runner without `jobs/` importing domain code.
- Per-type `ExecutionMode` declarations (P19 D-22): paper-family types (`paper-session`, `reconciliation`, `broker-order-sync`) declare `PAPER`. For `risk-evaluation` and market-data types, match what their CLIs validated (BACKTEST) plus whatever the Polygon key requirement needs. **Verified this session (`services/config/secrets.py::semantic_failures`): BACKTEST-mode config validation requires NO broker secrets and has NO separate Polygon-key check at all — Polygon key absence is not a boot-time/`config_invalid` failure for any mode; it only surfaces as a runtime `PolygonAuthError` inside the actual Polygon HTTP call. So "whatever the Polygon key requirement needs" resolves to: nothing extra — BACKTEST is sufficient for all four non-paper-family types, and a missing Polygon key for `ingest-bars`/`sync-symbol-metadata` is a `handler_error`-style runtime failure, not `config_invalid`.**
- Submit-time validation of a non-null `risk_run_id`: recommended check is that it references a SUCCEEDED risk-evaluation run for the same strategy and session, typed 422 otherwise.
- `symbols` validation rules (ticker format, max count). Restricting to the configured universe is not required.
- The `sync-symbol-metadata` CLI `--dry-run` flag is dropped: a Job is never a dry run, OPS-05 forbids behavior flags.
- Console shortcuts that deep-link into forms (P19 D-18 pattern).
- Actor/trigger_source values control endpoints pass to `OperatorControlService`.
- Where the break-glass command is documented; whether to add a Makefile target for it (if added, must be the one pinned exemption).

### Deferred Ideas (OUT OF SCOPE)

- A standalone "apply reconciliation corrections" Job type. Rejected for Phase 20 (D-06); revisit if operators need corrections outside a paper session.
- Dependency-chained submission from the console (e.g. risk-evaluation → paper-session via Job dependencies). Framework supports it (invariant 7); a UI for composing chains is a new capability.
- A stricter retry predicate requiring a reconciliation report with no blocking findings. Rejected in favor of the jobs-table-only check (D-19).
- Exposing a Job type's required `ExecutionMode` in the catalog. Still deferred (P19 D-23); revisit if paper-family `config_invalid` failures become common.

**Canonical implementation anchors** (all verified this session, see below): `jobs/registry.py` (`JobCancellationMode`, `JobSubmissionSpec`, `build_default_registry`), `jobs/handlers/backtest.py`+`backtest_submission.py` (the template), `jobs/cancellation.py` (`CANCELLATION_GRACE_SECONDS=300`), `orchestration/job_mutations.py` (cancel + new retry), `db/models/job.py` (`JobFailureReason`, needs `retry_of_job_id`), `db/models/strategy_run.py` (drop UNIQUE on `job_id`), `services/job_reads.py` (`resources[]`, retry lineage — **needs a real code fix, not just an addition, see Pitfall 1 below**), `services/operator_controls.py` (`OperatorControlService`, already complete), `services/execution/submit_orders.py` (`run_paper_session`, `run_paper_order_submission`), `services/execution/sync_orders.py` (`sync_paper_state`), `services/reconciliation/report.py` (`reconcile_paper_execution`, `_create_reconciliation_run`), `services/risk.py` (`run_risk_evaluation`, `resolve_evaluation_session`, `_create_risk_run`), `services/ingestion.py` (`ingest_daily_bars`, `_start_run`), `services/calendar.py` (`upsert_market_sessions`), `scripts/sync_symbol_metadata.py` (business logic must move to `services/` first), `services/concurrency_guard.py` (`ConcurrentRunLockedError`), `worker/commands/__init__.py` (DISPATCH), `worker/parser.py`, `worker/commands/*`, `api/routes/jobs.py` (retry route), new `api/routes/controls.py`, `tests/test_orchestration_boundaries.py`, `Makefile`, `console/src/lib/api.ts`, the console form map module, `console/src/components/KillSwitchBanner.tsx`, `console/src/app/strategy/`, `console/src/app/layout.tsx`, new `console/src/app/controls/`.

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| OPS-02 | Risk evaluation as a registered Job | `RiskEvaluationJobHandler`/`Spec` mirror `BacktestJobHandler`/`Spec`; `run_risk_evaluation` (`risk.py:467`) needs a `job_id` kwarg threaded to `_create_risk_run` (verified absent today) |
| OPS-03 | Paper session Job, cancellable only pre-broker-submission | `run_paper_session` (`submit_orders.py:680`) performs recon+corrections+submission in one opaque call, no internal checkpoint (verified) — new `queued_only` `JobCancellationMode` member is the only honest implementation (currently only `STEP_BOUNDARY` exists) |
| OPS-04 | Reconciliation as a registered Job, report-only | `reconcile_paper_execution` (`reconciliation/report.py:277`) is report-only re: PaperOrder mutation (Phase 9) but DOES create a `StrategyRun`+`ExecutionEvent` rows (verified — see D-29 note above); handler needs `job_id` kwarg threaded to `_create_reconciliation_run` |
| OPS-05 | Three separate market-data Job types, no composite flag handler | `ingest_daily_bars` (`ingestion.py:183`, needs `job_id` kwarg → `_start_run`), symbol-metadata logic (currently only in `scripts/sync_symbol_metadata.py`, must be extracted to a new service module — prerequisite task), `upsert_market_sessions` (`calendar.py:116`, no run record per D-08) |
| OPS-06 | Broker order-lifecycle sync as a registered Job | `sync_paper_state` (`sync_orders.py:49`) — empty `resources[]` per D-08 |
| OPS-07 | Operator retry with lineage | New `JobOrchestrationService.retry()`; `submit_job` (`jobs/dependencies.py:261`) currently has NO `retry_of_job_id` parameter — must be added; new `jobs.retry_of_job_id` UNIQUE FK column (migration, needs an explicit constraint name, e.g. `uq_jobs_retry_of_job_id`, so `_is_named_uniqueness_error` can distinguish it from `uq_job_mutations_endpoint_key` when both could fire in the same flush) |
| OPS-08 | Domain conflict as distinct `failure_reason` | `ConcurrentRunLockedError` exists (`concurrency_guard.py`); needs new `JobFailureReason.domain_conflict` (migration) + handler-level translation + **a real `runner.py` code change** (see Pitfall/Pattern 2 below — `runner.py`'s single `except Exception` at line 242 currently collapses everything to `HANDLER_ERROR` at line 316; D-02's "runner.py unchanged" claim is scoped to cancellation only) |
| CTRL-01 | Strategy enable/disable via sync HTTP control endpoint | `OperatorControlService.enable_strategy`/`disable_strategy` fully implemented — needs only `PUT /api/v1/controls/strategies/{strategy_id}` route |
| CTRL-02 | Kill-switch trip/reset via sync HTTP control endpoint | `OperatorControlService.trip_kill_switch`/`reset_kill_switch` fully implemented — needs only `PUT /api/v1/controls/kill-switch` route |
| ORCH-01 | HTTP-only mutation surface + break-glass exception | Need to ADD `kill-switch-trip` subcommand (doesn't exist today) and delete `scripts/*.py` bypass; **also need to REMOVE `serve`/`run_placeholder_worker` per D-30** (verified: these currently still exist and are reachable via CLI, unlike the other dead commands) |
| ORCH-02 | CLI/scripts are thin wrappers, no business logic | `scripts/sync_symbol_metadata.py` contains business logic that must move to a service module before deletion |
| ORCH-08 | Exactly one mutation path; boundary test; classify `dry_run.py`/`generate_signals.py` | Full inventory + classification below; `generate_signals.py` verified read-only; `dry_run.py` verified mutating |

</phase_requirements>

## Summary

Phase 20 arrives with an unusually thorough `20-CONTEXT.md` (30 locked decisions with file:line anchors) and a fully pinned `20-UI-SPEC.md`. This research verified every anchor against the live codebase via direct `Read`/`grep`/`Bash` — most check out exactly, but this pass also surfaced several **concrete, previously-unflagged discrepancies** that the planner must account for (not just "confirmed correct" findings):

1. **D-30's `serve`/`run_placeholder_worker` retirement is real and currently reachable** — unlike the other five dead worker-command modules (already unreachable via `DISPATCH`), `serve` IS still a live, parser-recognized, dispatchable CLI command today. Deleting it changes `worker/__main__.py::main()`'s special-case structure and three existing passing tests.
2. **`services/bootstrap.py` must NOT be deleted** — only its `run_dry_bootstrap` function. `ensure_strategy_record`/`create_strategy_run` from the same module are imported by `risk.py`, `operator_controls.py`, `reconciliation/report.py`, `sync_orders.py`, `submit_orders.py`, and `backtesting.py`.
3. **`job_reads.py`'s `resources[]` query will break for `paper-session`** once the `strategy_runs.job_id` UNIQUE constraint is dropped (D-07/D-08 explicitly requires up to 2 linked runs per Job) — the current query uses `.scalar_one_or_none()`, which raises `MultipleResultsFound` for 2 rows. This is a required code fix, not an addition.
4. **Four scripts (`run_paper_session.py`, `submit_paper_orders.py`, `sync_paper_state.py`, `reconcile_paper_execution.py`) already fail to import** — they reference `trading_platform.services.paper_execution`, a module that no longer exists after the Phase 12 STRUCT-04 split into `services/execution/{submit_orders,sync_orders}.py`. They are already dead, not merely "live bypasses."
5. **`export_backtest_report.py`'s D-29 exemption reasoning ("writes local files only") is not fully accurate** — `export_backtest_report()` calls `materialize_backtest_report()`, which calls `_upsert_backtest_metric()`, an idempotent-by-`strategy_run_id` DB upsert of a `BacktestMetric` row. This is flagged as an open item for the planner to explicitly re-confirm or reclassify, not silently overridden (CONTEXT.md's decisions are locked; this is new evidence the planner should weigh).
6. **`reconcile_paper_execution.py`'s "reconciliation is read-only" framing (from Phase 9) does not mean the script is non-mutating** — `reconcile_paper_execution()` still creates a `StrategyRun` + `ExecutionEvent` rows via `_create_reconciliation_run`; "read-only" there means it never mutates `PaperOrder`/broker state, not that it performs zero writes.

None of these six findings contradict CONTEXT.md's locked decisions or roadmap success criteria — they are additional ground-truth detail the planner needs to sequence tasks correctly and avoid two classes of bugs: (a) deleting shared code that's still needed, (b) shipping a resources[] query that crashes on the very feature (D-08's 2-run paper-session linkage) this phase is building.

**Primary recommendation:** Build the 7 new Job types as a mechanical repeat of the `backtest`/`BacktestJobHandler`/`BacktestSubmissionSpec` pattern, reuse `OperatorControlService` as-is behind two thin route adapters, and extend the existing AST-scan pattern in `tests/test_orchestration_boundaries.py` for the new boundary test — but sequence a "shared spine" wave first (migration, model changes, `job_reads.py` fix, `runner.py` domain_conflict branch, service `job_id` kwargs, `symbol_metadata_sync` extraction) before the 7 parallel per-type plans, per the Wave Decomposition section below.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| 7 new Job submission forms | Browser/Client (console) | API/Backend (validation) | Forms are thin; all validation lives server-side in each `JobSubmissionSpec` |
| Job type registration/handlers | API/Backend (worker process) | Database | `jobs/registry.py` + `worker run-jobs` process; handlers call existing domain services only |
| Cancellation-mode enforcement (OPS-03) | API/Backend (`JobOrchestrationService.cancel`) | Database (row lock) | Must happen in the same transaction/row-lock as the QUEUED→claim race (D-02) |
| Domain-conflict translation (OPS-08) | API/Backend (Job handler + `jobs/runner.py`) | — | Handler translates `ConcurrentRunLockedError`; `runner.py` needs one new outcome branch (see Pitfall below); `jobs/` stays free of domain imports (JOB-04) |
| Retry lineage (OPS-07) | API/Backend (`JobOrchestrationService.retry`, new) | Database (`retry_of_job_id` FK) | Same idempotency machinery as submit; console only renders server-derived fields |
| Kill-switch / strategy control (CTRL-01/02) | API/Backend (`OperatorControlService`, existing) | Database (`SystemControl`, `StrategyRun` OPERATOR_CONTROL) | Synchronous, no worker |
| Boundary enforcement (ORCH-01/02/08) | Backend tooling (pytest AST scan) | — | No browser UI; static test walking `scripts/`, `worker/commands/*`, Makefile |
| `/controls` page, control dialogs | Browser/Client (console) | API/Backend | UI-SPEC fully pins this |

## Standard Stack

No new external dependencies. Verified: `pyproject.toml` pins `alembic>=1.18.0,<2.0.0`, `fastapi>=0.131.0,<1.0.0`, requires Python `>=3.12` (local interpreter 3.14.6, `.venv` interpreter 3.13.15 — both satisfy). `console/package.json` pins Next.js `16.2.10`, React `19.2.4`, Vitest `^4.1.10` (locally resolves to `4.1.10`). `console/AGENTS.md` warns this Next.js version has breaking API changes vs. training data — read `console/node_modules/next/dist/docs/` before writing new App Router code.

**Installation:** none — no new packages.

## Package Legitimacy Audit

Not applicable — zero new external packages this phase (backend: existing FastAPI/SQLAlchemy/Alembic/pydantic; frontend: existing Next.js/React/Vitest, UI-SPEC explicitly reconfirms zero new npm dependencies).

## Architecture Patterns

### System Architecture Diagram

```
Console (Next.js)                     API (FastAPI)                    Worker (run-jobs loop)
──────────────────                    ──────────────                   ──────────────────────
7 new JobSubmissionForms   ──POST──▶  POST /api/v1/jobs        ──▶  JobOrchestrationService.submit()
  (jobTypeForms.ts map)                 (idempotency key)              → registry.resolve_submission_spec()
                                                                        → spec.validate_payload() [strict schema]
Job detail "Retry" button  ──POST──▶  POST /jobs/{id}/retry    ──▶  JobOrchestrationService.retry() [NEW]
  (retry_blocked, retried_as_job_id)     (idempotency key)              → re-validate_payload, D-19 reconcile-first
                                                                         → retry_of_job_id lineage (UNIQUE-constraint backed)

Cancel control (queued-only  ──POST──▶ POST /jobs/{id}/cancel  ──▶  JobOrchestrationService.cancel()
  types: disabled if RUNNING)                                          → D-02: reject RUNNING+queued_only w/ 409, same row lock

Control dialogs (Trip/Reset, ──PUT───▶ PUT /controls/kill-switch ──▶ OperatorControlService
  Enable/Disable, no key)               PUT /controls/strategies/{id}  .trip_kill_switch()/.reset_kill_switch()
                                                                        .enable_strategy()/.disable_strategy()
                                                                        [already fully implemented, no worker dep]
                                                                             │
                                                                             ▼
                                                                    StrategyRun(OPERATOR_CONTROL) +
                                                                    ExecutionEvent (audit, always written)

                                                                        Worker "run-jobs" loop (unchanged claim logic)
                                                                             │  handler.run(context) raises:
                                                                             ▼
                                                          runner.py: except JobCancelledError -> cancelled
                                                                     except JobDomainConflictError -> NEW branch,
                                                                       failure_reason=domain_conflict [code change]
                                                                     except Exception -> handler_error (unchanged)
                                                                             │
                                                                             ▼
                                                                    7 new JobHandlers each call an existing
                                                                    domain service with a NEW job_id kwarg:
                                                                    run_risk_evaluation / run_paper_session /
                                                                    reconcile_paper_execution / ingest_daily_bars /
                                                                    sync_symbol_metadata [NEW service fn] /
                                                                    upsert_market_sessions / sync_paper_state
                                                                             │
                                                                             ▼
                                                              StrategyRun / MarketDataIngestionRun (job_id FK,
                                                              same-transaction write, D-02/D-07)
                                                                             │
                                                                             ▼
                                                    job_reads.py resources[]: MUST become a .scalars().all() loop,
                                                    not .scalar_one_or_none() (breaks today for 2-run paper-session)

Break-glass (API down):    `trading-platform-worker kill-switch-trip --reason "..."` (DB-only boot, ignores ORCH-07,
                            calls OperatorControlService.trip_kill_switch directly — sole CLI exemption; NOTE `serve`
                            is being DELETED this phase, not retained alongside it)

Boundary test (pytest, backend-only, no UI):
  scripts/ + worker/commands/* + Makefile  ──AST-scan (exact-set closed-world)──▶  pinned allowlist
    (migrate, seed, generate_signals, export_backtest_report [flagged], operator_status [flagged],
     report_strategy_analytics, kill-switch-trip) — every other script/command/target must be ABSENT, not merely
     "not calling a pinned function" (see Boundary Test section for why call-matching alone is insufficient)
```

### Pattern 1: Job Handler + Submission Spec pair (verified template — `backtest`)

```python
# Source: src/trading_platform/jobs/handlers/backtest.py (verified live code)
class BacktestJobHandler:
    job_type = "backtest"
    required_execution_mode = ExecutionMode.BACKTEST

    def run(self, context: JobContext) -> dict[str, Any]:
        context.report_progress(step="resolving strategy")
        strategy_id = context.payload["strategy_id"]
        context.raise_if_cancelled()  # pre-call checkpoint (STEP_BOUNDARY types only)
        context.report_progress(step="running backtest")
        report = run_backtest(strategy_id, ..., job_id=context.job_id, settings=self._settings)
        context.raise_if_cancelled()  # post-call checkpoint
        return {"run_id": report.run_id, ...}
```
For the three **queued-only** types (`paper-session`, `broker-order-sync`, `reconciliation`), D-02 means NO `raise_if_cancelled()` calls inside the handler at all — the cancellation-mode enum value alone makes RUNNING-cancel impossible, rejected earlier at `JobOrchestrationService.cancel()`, before the handler ever runs.

### Pattern 2: Domain Conflict Signaling Mechanism (Claude's-discretion — concrete recommendation, with an honest scope note)

Recommended: define `class JobDomainConflictError(Exception): message: str` in `jobs/contracts.py` (the one file already a shared frozen contract, home to `JobCancelledError`). The handler catches `ConcurrentRunLockedError` and re-raises `JobDomainConflictError`. **`jobs/runner.py` genuinely needs a code change here** — verified at `runner.py:232-244`: today there are exactly two outcome branches, `except JobCancelledError: outcome_kind = "cancelled"` and a catch-all `except Exception as exc: outcome_kind = "error"`, and the `"error"` branch at line 308-320 hardcodes `failure_reason=JobFailureReason.HANDLER_ERROR`. Add a third branch: `except JobDomainConflictError as exc: outcome_kind = "domain_conflict"; failure_message = exc.message`, and a matching terminal-write branch setting `failure_reason=JobFailureReason.domain_conflict`. This does not violate `test_job_framework_modules_import_no_domain_layers` (the new exception lives in `jobs/contracts.py`, not a domain module) or JOB-03 (JOB-03 is about *adding a Job type* touching zero queue modules — this is a one-time Phase 20 framework change, not a per-type one). D-02's statement that "the `jobs/` framework (`cancellation.py`, `runner.py`) is unchanged" is scoped specifically to the *cancellation* mechanism it's discussing — it is not a blanket statement that `runner.py` needs zero changes anywhere in Phase 20; OPS-08 is a separate decision (D-04) with its own, different runner.py touch-point.

### Pattern 3: Retry as a structural copy of Submit's idempotency machinery

`JobOrchestrationService.submit()` (`orchestration/job_mutations.py`) has the exact shape `retry()` needs: `_validate_idempotency_key` → `_request_fingerprint` → `_existing_outcome` lookup against `JobMutation` (add `RETRY_ENDPOINT_ID = "POST:/api/v1/jobs/{job_id}/retry"`) → `session.begin_nested()` → create the new Job. New pieces: (1) load+lock the original Job, verify `FAILED`/`CANCELLED` (else `409`); (2) re-run `spec.validate_payload()` on the copied payload (D-18); (3) for `paper-session`/`broker-order-sync`, check D-19's reconcile-first predicate via a plain jobs-table query using `Job.completed_at` (not `finished_at` — verify the column name at implementation time); (4) `submit_job()` (`jobs/dependencies.py:261`) currently has NO `retry_of_job_id` parameter and must be extended to accept and persist it; (5) name the new UNIQUE constraint explicitly (e.g. `op.f("uq_jobs_retry_of_job_id")`, mirroring 0020's `op.f("uq_strategy_runs_job_id")` convention) so `_is_named_uniqueness_error`-style dispatch can distinguish a retry-uniqueness violation from a `JobMutation`-uniqueness violation if both could raise `IntegrityError` in the same flush.

### Anti-Patterns to Avoid
- Re-adding a pre-submission cancellation checkpoint inside `run_paper_session` — invariant 6/D-03 forbids entering the domain service for this.
- Branching the console UI on `job_type` — `consoleBoundaries.test.ts` already enforces zero tolerance for this in the Job-UI scope (verified: 4 existing assertions, `JobHeaderPanel.tsx` is in scope).
- A composite market-data Job handler with a mode flag — explicitly out of scope (OPS-05).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Idempotent retry semantics | A new idempotency table/mechanism | The existing `JobMutation` table + `_request_fingerprint`/`_existing_outcome`, add a third `endpoint_id` | Already proven correct under concurrent-submission races (Phase 18) |
| Domain-conflict detection | New locking/detection logic | The existing `ConcurrentRunLockedError`/`session_run_lock` advisory-lock primitive | Already correct/tested since Phase 8 |
| Kill-switch / strategy audit trail | New audit tables | Existing `StrategyRun(OPERATOR_CONTROL)` + `ExecutionEvent`, already written by `OperatorControlService` for both changed/unchanged | AUD-01 requires "no new audit tables"; service already satisfies it |
| Boundary/closed-world enforcement | A new static-analysis tool | Extend `tests/test_orchestration_boundaries.py`'s existing AST scanner (`_schema_mutation_offenders`, `_module_imports`, `_dotted_name`, `_resolve_import_alias`) | Working, precise scanner already exists — but see the Boundary Test section for a required generalization (terminal-name matching for instance-method calls) |

**Key insight:** Nearly everything this phase needs already exists in a proven, tested form. The remaining work is mechanical repetition plus two small structural additions (retry, domain_conflict) that are themselves near-copies of existing code paths — with the caveat that "near-copy" still requires real, specific code edits in `runner.py`, `job_reads.py`, and `jobs/dependencies.py::submit_job`, not zero-touch.

## Full Inventory: `scripts/` (16 files — corrected count), `worker/commands/*`, Makefile

### scripts/ (16 files, all read in full and classified from actual behavior)

| Script | Calls | Mutating? | Disposition |
|--------|-------|-----------|-------------|
| `dry_run.py` | `run_dry_bootstrap` (writes `StrategyRun`) | YES | DELETE (D-27) |
| `evaluate_risk.py` | `run_risk_evaluation` (writes RiskRun) | YES | DELETE (D-26) |
| `generate_signals.py` | `strategy.generate_signals()` (reads bars only) | **NO** — verified: zero `session.add`/`commit`/`flush` in `strategies/` (checked `trend_following_daily/strategy.py` and `strategies/base.py`) or in `services/market_data_access.py` (the read helper it calls) | EXEMPT (D-29) |
| `ingest_polygon_bars.py` | `ingest_daily_bars` (writes bars) | YES | DELETE (D-26) |
| `operator_control.py` | `OperatorControlService.enable_strategy`/`disable_strategy` directly | YES — bypasses CTRL-01 entirely | DELETE (D-26) |
| `operator_status.py` | `build_operator_status_report` → `load_strategy_control_state` → `OperatorControlService.get_strategy_state()` → `ensure_strategy_record()` (get-or-create: **INSERTs on first call, UPDATEs-in-place + `session.flush()` on every subsequent call even when nothing changed**) | **Technically writes on every invocation** (a no-op-value UPDATE + flush is still a DB write) — verified via direct read of `bootstrap.py::ensure_strategy_record` | D-29 exempts this with the "read/report" reason; **flagged as an open item** — the exemption is defensible in practice (idempotent value-preserving upsert of the strategy catalog row, not a state-changing operation) but is not literally "zero writes." Planner should note this explicitly rather than assert pure read-only. |
| `reconcile_paper_execution.py` | `reconcile_paper_execution()` — **imports from `trading_platform.services.paper_execution`, a module that does not exist** (verified: only a stale `.pyc` remains; the real module is `services/execution/{submit_orders,sync_orders}.py` since the Phase 12 STRUCT-04 split) — **this script is already import-broken, cannot run today** | YES when it could run: `_create_reconciliation_run` creates a `StrategyRun` + the findings-persistence loop writes `ExecutionEvent` rows | DELETE (D-26) — already dead code, deletion is pure cleanup |
| `report_strategy_analytics.py` | `build_strategy_analytics_report` (read-only) | NO | EXEMPT (D-29) |
| `run_backtest.py` | `run_backtest` (writes BacktestRun) | YES | DELETE (D-26) |
| `run_paper_session.py` | **Same broken import as `reconcile_paper_execution.py`** (`services.paper_execution`) — already dead | YES when it could run | DELETE (D-26) |
| `submit_paper_orders.py` | **Same broken import** — already dead | YES when it could run | DELETE (D-28) |
| `sync_paper_state.py` | **Same broken import** — already dead | YES when it could run | DELETE (D-26) |
| `sync_symbol_metadata.py` | `_upsert_symbol_metadata` (writes `Symbol` rows); **contains the only live copy of the metadata-sync business logic** (`_fetch_ticker_overview`, `_upsert_symbol_metadata`), imported at runtime by `worker/commands/ingest.py::run_sync_metadata` via a documented pre-existing broken `importlib`/`sys.path` hack (`parents[5]` resolves one level above project root — already non-functional for the non-dry-run path per the code's own comment) | YES | DELETE the script, but extract logic into a new `services/` module FIRST (prerequisite task, ORCH-02) |
| `export_backtest_report.py` | `export_backtest_report()` → `materialize_backtest_report()` → `_upsert_backtest_metric()` — **verified: this DOES write a `BacktestMetric` row to the DB** (idempotent select-then-upsert keyed on `strategy_run_id`), in addition to writing local summary/CSV files | **Partially YES** — not purely "writes local files only" as D-29's stated reason claims | D-29 exempts this as "report... writes local files only"; **flagged as an open item for the planner** — the write is a derived-metric upsert of data already computed by a prior real backtest run (not a new manual operation, no new state machine transition), so the spirit of the exemption likely still holds, but the reason text as written is not literally accurate and the planner should either re-confirm the exemption with corrected reasoning or reclassify |
| `migrate.py` | Alembic commands | Schema-only | EXEMPT (D-29, deployment tooling) |
| `seed_phase1.py` | Writes `Strategy` catalog row | YES, deployment-scoped | EXEMPT (D-29, deployment tooling) |

**ORCH-08 unresolved classification items, resolved:**
- `dry_run.py` → **mutating, retire** (D-27). Verified: `run_dry_bootstrap` creates a `StrategyRun`.
- `generate_signals.py` → **read-only, exempt** (D-29). Verified across both the strategy layer and the market-data-access read helper it calls.

### worker/commands/* — DISPATCH reachability, corrected

`worker/parser.py` today defines 5 subparsers: `serve`, `report-backtest`, `report-strategy-analytics`, `operator-status`, `run-jobs`. `DISPATCH` maps 4 of them (`serve` is special-cased in `__main__.py::main()`, NOT in `DISPATCH`). **`serve` is currently live and reachable** — this is a key correction from an earlier draft of this research: `serve` is not in the same "already dead" category as `paper_execute.py` etc.

| Module | Status | Disposition |
|--------|--------|-------------|
| `bootstrap.py::run_placeholder_worker` (the `serve` handler) | **Currently live/reachable** via `worker/__main__.py`'s `if args.command == "serve":` special case | **DELETE** (D-30: "the `serve`/`run_placeholder_worker` path, which is dead after ORCH-05" — dead in the sense of *unused in deploy config*, not unreachable via CLI; still must be removed) |
| `bootstrap.py::run_dry_bootstrap` | Dead (no subparser calls it directly; only `scripts/dry_run.py` calls it) | DELETE (D-27) |
| `paper_execute.py` (whole file: `run_submit_paper_orders_command`, `run_paper_session_command`, `run_sync_paper_state_command`) | Dead — no subparsers exist for these commands | DELETE (D-30) |
| `reconcile.py` (whole file) | Dead | DELETE (D-30) |
| `risk_check.py` (whole file) | Dead | DELETE (D-30) |
| `ingest.py` (whole file: `run_ingest_bars`, `run_sync_metadata`, `run_sync_sessions`) | Dead | DELETE (D-30) |
| `backtest.py::run_backtest_command` | Dead (`run_report_backtest_command`/`run_report_strategy_analytics_command` stay — both in DISPATCH) | DELETE only this function |
| `operator.py::run_operator_control_command` + `_run_kill_switch_action` | Dead (`run_operator_status_command` stays — in DISPATCH) | DELETE; **ADD** new `kill-switch-trip` handler (D-15) here or a new module |
| `run_jobs.py` | Live, keep as-is | Also add `kill-switch-trip` dispatch + subparser |

**Corrected final worker surface** (per D-30's literal text, verified against `worker/parser.py`): `parser.py` subparsers become exactly `{report-backtest, report-strategy-analytics, operator-status, run-jobs, kill-switch-trip}` — **`serve` is REMOVED, not retained**. `worker/__main__.py::main()`'s current structure (`if args.command == "serve": run_placeholder_worker(...); return` + `DISPATCH.get(args.command)`) collapses to just the `DISPATCH.get(args.command)` lookup with no special case at all, once `serve` is gone — this is a structural simplification the planner should call out explicitly, since `tests/test_orchestration_boundaries.py::test_worker_entrypoint_has_only_serve_special_case_and_dispatch_lookup` (asserts exactly 2 `if` statements, the first `== 'serve'`) will need rewriting to assert 0 special-case `if`s (or however the planner restructures `main()`), not just relaxed.

### Existing tests that must change because of this inventory

| Test | Current assertion | Required change |
|------|-------------------|------------------|
| `test_parser_exposes_only_retained_cli_surface` | `_RETAINED_CLI_COMMANDS = {serve, report-backtest, report-strategy-analytics, operator-status, run-jobs}` | Drop `serve`, add `kill-switch-trip` |
| `test_dispatch_exposes_only_retained_non_serve_commands` | `_RETAINED_DISPATCH_COMMANDS` = same 4, minus `serve` | Add `kill-switch-trip` |
| `_REMOVED_CLI_COMMANDS` (parametrized rejection test) | Does not include `serve` | Add `serve` |
| `test_worker_entrypoint_has_only_serve_special_case_and_dispatch_lookup` | Asserts 2 `if`s, first `== 'serve'` | Rewrite for the post-`serve` `main()` structure |
| `test_api_route_modules_have_only_the_two_job_mutation_decorators` (line 140) | `{("POST",""), ("POST","/{job_id}/cancel")}` | Add `("POST","/{job_id}/retry")`, plus new `controls.py` PUT decorators if scanned by this test's glob (it globs `routes/*.py`, so `controls.py` will be picked up — decide whether PUT belongs in this same assertion or a parallel one) |
| `test_runtime_application_has_exactly_two_mutating_job_routes` (line 162) | Exactly 2 routes | Replace with the 5-route allowlist (D-12) |
| `test_default_registry_registers_exactly_the_phase19_job_types` | `registry.list_job_types() == ["backtest"]` | Update to the 8-type sorted list |
| `tests/test_dry_run.py` | Imports `from scripts.dry_run import main` and exercises it directly | **Delete or fully rewrite** — its only subject is being deleted (D-27) |
| `tests/test_startup_validation.py` | References `worker_bootstrap_commands.run_placeholder_worker` (verified, line 250) | Update once `run_placeholder_worker`/`serve` is removed |

**Verified via anchored grep (`^from scripts\.`/`^import scripts\.`) that no other test file imports a to-be-deleted script** — the ~30 other hits from a looser grep were all `from scripts.migrate import build_alembic_config` (test-DB setup helper; `migrate.py` is kept, unaffected). This fully resolves the original "which tests reference deleted scripts" open question: only the two files above need attention.

### Makefile targets

Kept (per D-29): `up, down, logs, migrate, seed, export-backtest-report, generate-signals, test, console, console-install`. Deleted: `dry-run` and `sync-sessions` (already call nonexistent worker subcommands — verified neither `dry-run` nor `sync-sessions` has a `parser.py` subparser today), `backtest`, `ingest-bars`, `sync-metadata`, `submit-paper-orders`, `run-paper-session`, `sync-paper-state`, `reconcile-paper-execution` (all call scripts being deleted).

**Pre-existing gap (not this phase's to fix, but worth flagging):** the Makefile `test` target's hardcoded pytest file list predates Phase 17 and does not include `tests/test_orchestration_boundaries.py`, any Job-framework test, or `tests/test_job_operations_e2e.py`. `make test` alone will not validate this phase's own boundary tests; use `pytest` directly against the specific files, or the full suite.

## Boundary Test Implementation Recommendation

**The scanner must assert exact-set equality on enumerated inventories, not just "no forbidden call found."** A pure call-matching scan (checking whether each file calls a pinned mutating function) is NOT closed-world: a brand-new, unclassified script that happens to call nothing on the pinned list would pass silently. To be genuinely closed-world, combine two checks:

1. **Exact-set membership tests** (cheap, closed-world by construction):
   - `set(f.name for f in Path("scripts").glob("*.py"))` must equal exactly the pinned exempt set (`migrate.py`, `seed_phase1.py`, `generate_signals.py`, `export_backtest_report.py`, `operator_status.py`, `report_strategy_analytics.py`, `sync_symbol_metadata.py` if not yet extracted/deleted) — any new file not on the list fails immediately, regardless of what it calls.
   - `set(DISPATCH)` must equal exactly `{report-backtest, report-strategy-analytics, operator-status, run-jobs, kill-switch-trip}` (reuses the existing `test_dispatch_exposes_only_retained_non_serve_commands` pattern, generalized).
   - Parser subcommands (`_parser_commands(build_parser())`, already defined in the test file) must equal the same 5-set.
   - Makefile target names (simple line-parsing, e.g. regex `^([a-zA-Z0-9_-]+):`) must equal exactly the kept 10-set.
2. **AST call-target scan on the exempted files themselves**, extending `_schema_mutation_offenders`'s existing `_dotted_name`/`_import_aliases`/`_resolve_import_alias` helpers, to catch a *future* edit to an already-exempted file that adds a mutating call it didn't have before. Two verified gotchas the scanner must handle:
   - **`OperatorControlService` mutators are called as instance methods** (`service.trip_kill_switch(...)`) — `_resolve_import_alias` resolves *module-level* import aliases, not instance attribute types, so it will NOT resolve `service.trip_kill_switch` to `OperatorControlService.trip_kill_switch` via import-alias substitution. Match on the **terminal attribute name** (`node.func.attr` for `ast.Attribute` call targets) against a pinned set of method names (`trip_kill_switch`, `reset_kill_switch`, `enable_strategy`, `disable_strategy`, `run_dry_bootstrap`, etc.) rather than requiring a fully-resolved dotted path — the existing `_schema_mutation_offenders` normalizes to `".".join(resolved.split(".")[-2:])` for exactly this reason (`metadata.create_all`), so extending that same last-two-segments normalization to also match bare method names is a natural generalization.
   - **Some scripts import from `services.paper_execution`, a module that no longer exists** — an AST import-based scanner does not need the module to actually exist to walk its `ast.ImportFrom` node, but be aware these four scripts are already broken in a way any import-boundary test might separately flag; don't be surprised if a naive "does the import resolve" check also fails these files for an unrelated reason.

## Common Pitfalls

### Pitfall 1: `job_reads.py`'s resources[] query breaks for paper-session (verified, concrete)
**What goes wrong:** `services/job_reads.py:124-126` does `session.execute(select(StrategyRun).where(StrategyRun.job_id == job_uuid)).scalar_one_or_none()`. Once the `strategy_runs.job_id` UNIQUE constraint is dropped (D-07) and `paper-session` legitimately links 2 `StrategyRun` rows to one Job (D-08), this exact line raises `sqlalchemy.exc.MultipleResultsFound` for any such Job.
**How to avoid:** Change to `.scalars().all()` and append one `resources[]` entry per row (loop), for `StrategyRun` AND analogously for the new `MarketDataIngestionRun` lookup (D-07's `market_data_ingestion_runs.job_id`). This is a required code change in the "shared spine" wave, not an optional hardening — writing the `paper-session` handler without it will produce a Job detail read that 500s.

### Pitfall 2: Forgetting `job_id` threading breaks D-07/D-09 resource linkage
**What goes wrong:** `run_risk_evaluation`, `reconcile_paper_execution`, `ingest_daily_bars` do not currently accept a `job_id` kwarg (verified absent) — unlike `run_backtest`, which already has it and threads it to `_create_backtest_run`.
**How to avoid:** Add `job_id: uuid.UUID | None = None` to each and thread to `_create_risk_run`/`_create_reconciliation_run`/`_start_run`, using `backtesting.py::run_backtest`'s pattern as the literal template. Also thread it to `run_paper_session`'s *internal* call to `reconcile_paper_execution` (`submit_orders.py:775`) so both linked runs (D-08's "up to 2") share the same Job id — easy to add to the outer call and forget the nested one.

### Pitfall 3: `sync-symbol-metadata`'s only business-logic copy lives in the script being deleted
**What goes wrong:** Deleting `scripts/sync_symbol_metadata.py` before extracting `_fetch_ticker_overview`/`_upsert_symbol_metadata` removes the only implementation — breaking the new Job handler and the already-broken `worker/commands/ingest.py::run_sync_metadata` importlib hack simultaneously.
**How to avoid:** Sequence: extract to a new `services/` module FIRST, repoint the new handler at it, THEN delete the script and the dead worker functions.

### Pitfall 4: The QUEUED-vs-RUNNING cancel race for queued-only types (D-02)
**What goes wrong:** A cancel request and a worker's claim (QUEUED→RUNNING) race; if the queued-only mode-check isn't inside the same row lock as the cancel transition, the DB end-state could be ambiguous.
**How to avoid:** `JobOrchestrationService.cancel()` already does `self._require_job(session, job_id, lock=True)` (`job_mutations.py`) before deciding — the new mode check must use this SAME locked `job` object, inside the same `session.begin_nested()` block. Pin with a real two-connection concurrent-DB test, following the `08-02`/`17-07` advisory-lock/`SELECT...FOR UPDATE SKIP LOCKED` race-test precedent.

### Pitfall 5: Overclaiming requirement completion mid-phase (established project norm)
**What goes wrong:** Per this project's STATE.md history (repeated across Phases 17-19), marking a requirement `Complete` when only part of its literal text is satisfied by a given plan.
**How to avoid:** Follow the established convention — declare only the requirement IDs a plan actually completes end-to-end in its frontmatter; leave partial ones `Pending` with a STATE.md note. Especially relevant here since OPS-02..08/CTRL-01/02 each need BOTH backend wiring AND a console form/control before "operator can run X from the UI" is literally true.

### Pitfall 6: Deleting `worker/commands/bootstrap.py` wholesale before checking both its functions
**What goes wrong:** The file contains both `run_placeholder_worker` (the `serve` handler — being deleted per D-30) and `run_dry_bootstrap` (being deleted per D-27). Both are going, so the whole file becomes deletable — but only after confirming (as this research did) that neither function is imported anywhere else. Verified: `run_dry_bootstrap` (worker-layer function, not `services/bootstrap.py`'s copy) is only imported by `worker/commands/__init__.py`'s historical wiring path and nowhere else; safe to delete the whole file.

## Code Examples

### Existing `JobSubmissionSpec` template
```python
# Source: src/trading_platform/jobs/handlers/backtest_submission.py (verified live code)
class _BacktestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: StrictStr = Field(min_length=1, max_length=64)
    from_date: date
    to_date: date

class BacktestSubmissionSpec:
    job_type = BACKTEST_JOB_TYPE
    cancellation_mode = JobCancellationMode.STEP_BOUNDARY
    def validate_payload(self, payload): ...  # pydantic parse -> typed rejection -> semantic checks
    def submission_defaults(self) -> dict[str, str] | None: ...  # read-only, catalog-read time
```

### Existing migration naming convention (verified, `0020_phase19_job_operations.py`)
```python
op.add_column("strategy_runs", sa.Column("job_id", sa.Uuid(), nullable=True))
op.create_foreign_key(op.f("fk_strategy_runs_job_id_jobs"), "strategy_runs", "jobs", ["job_id"], ["id"])
op.create_unique_constraint(op.f("uq_strategy_runs_job_id"), "strategy_runs", ["job_id"])
# downgrade reverses in exact opposite order, using the same op.f(...) names
```
The Phase 20 migration's downgrade for D-07 must drop the constraint by this exact name: `op.drop_constraint(op.f("uq_strategy_runs_job_id"), "strategy_runs", type_="unique")` — reversing 0020's own upgrade.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `scripts/*.py` + Makefile calling domain services directly (4 of the 13 mutating scripts are already import-broken, silently) | Console → HTTP → Job → worker → domain service | Phase 20 | Closes the last live bypass; also cleans up dead-but-undetected breakage |
| "Exactly two mutating routes" test | Explicit five-route allowlist | Phase 20 | Accommodates retry + 2 control routes |
| `JobCancellationMode.STEP_BOUNDARY` only | + `queued_only` | Phase 20 | First Job types honestly stating in-flight cancellation is impossible |
| `worker/__main__.py::main()` has a `serve` special case | Pure `DISPATCH.get()` lookup, no special case | Phase 20 (D-30) | Simplifies the entrypoint further after ORCH-05 already stopped deploying `serve` |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The new `JobCancellationMode` value should be named `queued_only` (matching UI-SPEC's pinned console string) | Pattern 1 | Low — UI-SPEC pins this string for console rendering; a different backend value would need a translation layer for no benefit |
| A2 | `services/symbol_metadata_sync.py` (or similarly named) is an acceptable extraction target | Pitfall 3 | Low — CONTEXT.md only requires "moves into `services/`," not an exact filename |
| A3 | The `JobDomainConflictError`-in-`contracts.py` mechanism (Pattern 2) is the best of several valid designs | Pattern 2 | Medium — explicit Claude's-discretion item; an alternative (e.g. a runner-side exception-type classification hook) could also satisfy JOB-04. Recommended because it structurally mirrors the existing `JobCancelledError` precedent in the same file. |
| A4 | `export_backtest_report.py`'s DB write (`_upsert_backtest_metric`) doesn't disqualify it from D-29's exemption | scripts/ inventory table | Medium — this is presented as an open item, not a silent override, precisely because reasonable people could reclassify it; flagged for explicit planner/user re-confirmation given the project's stated preference for testable, precise classification over "direction" language |

## Open Questions

1. **~~Whether existing tests reference deleted scripts~~ — RESOLVED this session.** Only `tests/test_dry_run.py` (direct import of `scripts.dry_run`) and `tests/test_startup_validation.py` (references `run_placeholder_worker`) need updates. Verified via anchored grep; all other `from scripts.` hits are `scripts.migrate` (kept).

2. **Whether `export_backtest_report.py` and `operator_status.py`'s D-29 exemptions should be re-confirmed with corrected reasoning, or reclassified**
   - What we know: both perform a DB write as a side effect (verified this session — `_upsert_backtest_metric`, `ensure_strategy_record`'s flush respectively), contradicting the literal "writes local files only"/"read/report" framing in D-29's stated reasons.
   - What's unclear: whether this is material enough to change the boundary-test's exemption reasoning text, or purely cosmetic (both writes are idempotent, derived, and don't constitute a new manual operation).
   - Recommendation: keep both exempt (their writes are non-state-changing upserts of already-computed data, not new orchestrated operations), but correct D-29's exemption *reason* text in the actual boundary test's comments/docstring to be accurate, per this project's stated preference for precise, testable, non-"direction" language.

3. **Whether `apply_reconciliation_corrections` needs a `job_id` param**
   - What we know: D-06 says the `reconciliation` Job does NOT call it (report-only). It's called only from inside `run_paper_session`.
   - What's unclear: whether it creates any new run record itself (if it only updates existing `PaperOrder` fields, no `job_id` threading is needed there).
   - Recommendation: verify its body directly during implementation; not read in full this session due to time-boxing.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PostgreSQL | All persistence, E2E tests | ✓ (`pg_isready` → "accepting connections") | not queried further | — |
| Python (`.venv`) | Backend | ✓ — **STATE.md's 2026-09-23 blocker note ("`.venv/bin` contains no python interpreter") is STALE; verified this session `.venv/bin/python --version` → 3.13.15 and `.venv/bin/pytest --version` → 9.1.1 both work** | 3.13.15 | — |
| Alembic | Migrations | ✓ | `>=1.18.0,<2.0.0` pin | — |
| Node/Vitest | Console tests | ✓ (`npx vitest --version` → 4.1.10) | Next.js 16.2.10, React 19.2.4 | — |
| Alpaca paper broker creds | Real broker calls in `run_paper_order_submission`/`sync_paper_state` | Not verified this session | — | Job E2E tests can follow `test_job_operations_e2e.py`'s no-broker-creds pattern; D-22 validates required `ExecutionMode` per-handler at dispatch, not at worker boot |
| Polygon API key | `ingest_daily_bars`/symbol-metadata sync real calls | Not verified this session | — | Not required for BACKTEST-mode boot (verified: `secrets.py::semantic_failures` has no Polygon check at all, any mode); absence surfaces as a runtime `PolygonAuthError` inside the actual call, not a boot-time failure |

**Missing dependencies with no fallback:** none identified as blocking.

**Correction to STATE.md:** the `.venv` breakage documented as an active blocker (2026-09-23) appears resolved — both `python` and `pytest` are present and functional in `.venv/bin` as of this research session. The planner should re-verify at execution time but should not assume the old blocker still applies.

## Validation Architecture

Skipped — `.planning/config.json` sets `workflow.nyquist_validation: false` explicitly (`[VERIFIED: .planning/config.json]`).

**Test commands for the planner's own use** (not a formal Validation Architecture section, since config disables it): backend — `cd` to repo root, `.venv/bin/pytest tests/test_orchestration_boundaries.py tests/test_job_operations_e2e.py -q` (fast, targeted); full backend suite — `.venv/bin/pytest -q` (NOT `make test`, which uses a stale hardcoded subset predating Phase 17). Frontend — `cd console && npx vitest run` or `npm run test`.

## Security Domain

`security_enforcement` absent from `.planning/config.json` — treated as enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Out of scope for the whole v1.3 milestone (single-operator) |
| V3 Session Management | No | Same |
| V4 Access Control | Partial | `ORCH-07`'s `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED` flag is the sole exposure control; new control/retry routes must sit behind the same `require_mutations_enabled` dependency |
| V5 Input Validation | Yes | pydantic `extra="forbid"` for every new payload; control routes must self-validate `state`/`status`/`reason` and raise a structured `{"code": ...}` 422 (UI-SPEC requirement — FastAPI's default array-shaped 422 can't be parsed by the console's error dispatch) |
| V6 Cryptography | No | Not applicable |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Retry idempotency-key replay against a different target Job | Tampering | `_existing_outcome`'s fingerprint check already raises `IdempotencyConflictError` → 409 for mismatched fingerprints under the same key |
| Break-glass `kill-switch-trip` CLI abuse | Elevation of Privilege | Explicitly accepted per D-15 — shell access is already privileged; no additional mitigation in scope |
| Control route reason-field injection into audit/log lines | Tampering (log injection) | `reason` trimmed/length-capped (500 chars, D-11), stored via SQLAlchemy parameter binding; structured logging's existing `sanitize()` pipeline (Phase 10 LOG-06) already redacts the whole payload |

## Suggested Wave Decomposition (non-binding)

Given `config.json`'s `parallelization.plan_level: true` and STATE.md's documented history of real shared-working-tree collisions (11-01/11-03, 10-04/10-05, 19-08), minimize concurrent plans touching the same "spine" files.

**Wave 0 (single plan, sequential, must land first):**
- Migration: drop `uq_strategy_runs_job_id`; add `market_data_ingestion_runs.job_id` FK+index; add `JobFailureReason.domain_conflict`; add `jobs.retry_of_job_id` (nullable FK, named UNIQUE constraint).
- `jobs/registry.py`: add the new `JobCancellationMode` member.
- `jobs/contracts.py`: add `JobDomainConflictError`.
- `jobs/runner.py`: add the domain_conflict outcome branch.
- `jobs/dependencies.py::submit_job`: add `retry_of_job_id` parameter.
- `services/job_reads.py`: fix `resources[]` to `.scalars().all()` loop (both StrategyRun and new MarketDataIngestionRun kind).
- `job_id` kwarg additions: `risk.py::run_risk_evaluation`, `reconciliation/report.py::reconcile_paper_execution`, `ingestion.py::ingest_daily_bars`, and threading through `submit_orders.py::run_paper_session`'s internal reconciliation call.
- Extract `services/symbol_metadata_sync.py` (or similar) from `scripts/sync_symbol_metadata.py`.
- `orchestration/job_mutations.py::retry()` — the new method itself (depends on the migration + `submit_job` change above).

**Wave 1 (parallel, one plan per Job type — each owns only NET-NEW files):**
- 7 plans, each: one handler module, one submission-spec module, one E2E test file (mirroring `test_job_operations_e2e.py`), one console form component.
- Each plan makes exactly ONE line's worth of edit to each shared file: `registry.py::build_default_registry` (register call), `console/src/lib/jobTypeForms.ts` (map entry), `console/src/lib/api.ts` (only if a type needs a bespoke client helper — most don't). **Flag this explicitly to the planner:** these small shared-file edits are the one real collision risk in this wave; consider serializing just those single-line edits (e.g., a fast-follow micro-plan) rather than relying on 7 concurrent agents merging cleanly into the same function body.

**Wave 2 (parallel-safe, independent of Wave 1's specific types):**
- CTRL-01/02: new `api/routes/controls.py`, `console/src/lib/api.ts` control-mutation functions, `ControlConfirmDialog.tsx`, `/controls` page, inline controls on `KillSwitchBanner`/`StrategyOverviewPanel`, `StrategyStatusBadge` extraction.
- Retry UI: Job detail Retry button + lineage fields (`JobHeaderPanel.tsx`, `jobs/types.ts` additions: `payload`, `retry_blocked`, `retried_as_job_id`, `retry_of_job_id`). **Collision note:** both this and CTRL-01/02 touch `console/src/lib/api.ts` — sequence or coordinate these two plans' edits to that one file.

**Wave 3 (single plan, last, after all 7 types are registered and all scripts have replacement Jobs):**
- Extend `tests/test_orchestration_boundaries.py` with the closed-world scanner (exact-set + AST call-target checks).
- Delete all scripts/worker-commands/Makefile-targets per the inventory table.
- Update `tests/test_dry_run.py`, `tests/test_startup_validation.py`.
- Add `kill-switch-trip` worker subcommand.
- This must be last because the boundary test's exact-set assertions need the FINAL state of `scripts/`, `DISPATCH`, and the Makefile to write correct expected-set literals against.

## Sources

### Primary (HIGH confidence — direct codebase reads this session)
`.planning/phases/20-.../20-CONTEXT.md`, `20-UI-SPEC.md`; `.planning/REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md`, `config.json`; `src/trading_platform/jobs/{registry,contracts,cancellation,runner,dependencies}.py`, `jobs/handlers/{backtest,backtest_submission}.py`; `src/trading_platform/orchestration/job_mutations.py`; `src/trading_platform/db/models/{job,strategy_run,market_data_ingestion_run}.py`; `src/trading_platform/services/{operator_controls,concurrency_guard,job_reads,risk,ingestion,calendar,bootstrap,backtest_reporting}.py`, `services/execution/submit_orders.py`, `services/config/{validation,secrets}.py`, `services/reconciliation/report.py`; `src/trading_platform/worker/{parser,__main__}.py`, `worker/commands/{__init__,bootstrap,paper_execute,reconcile,risk_check,ingest,backtest,operator,run_jobs}.py`; all 16 `scripts/*.py` (read in full); `Makefile`, `pyproject.toml`, `console/package.json`, `console/AGENTS.md`; `tests/test_orchestration_boundaries.py`, `test_job_operations_e2e.py`, `test_dry_run.py`, `test_startup_validation.py`; `console/src/lib/{api.ts,jobTypeForms.ts,consoleBoundaries.test.ts}`, `console/src/components/jobs/types.ts`; `alembic/versions/0020_phase19_job_operations.py`; live probes: `pg_isready`, `.venv/bin/python --version`, `.venv/bin/pytest --version`, `npx vitest --version`.

### Secondary / Tertiary
None — entirely primary-source codebase inspection; no external library research was needed.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies, versions verified directly
- Architecture: HIGH — every pattern is a verified, working existing implementation, including the corrections found this session
- Pitfalls: HIGH — each is grounded in a specific verified code gap or contradiction, not speculation

**Research date:** 2026-09-27
**Valid until:** 30 days (stable internal codebase, no new dependencies)
