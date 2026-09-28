---
phase: 20-complete-operation-migration-safety-controls
verified: 2026-09-28T23:10:00Z
status: human_needed
score: 7/7 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Kill-switch trip and reset from /controls and from the KillSwitchBanner in a real browser against a running API with NO worker process running"
    expected: "Dialog requires a non-blank reason (<=500 chars) and typed RESET for reset; trip succeeds; banner flips to tripped; second trip shows 'Already tripped - no change (recorded)'; a new OPERATOR_CONTROL run + ExecutionEvent row exists each time"
    why_human: "Component tests (262 vitest) prove dialog behavior and HTTP contract tests prove the route, but the combined browser -> proxy -> API -> DB path on a live stack with worker stopped cannot be exercised here (no dev server / browser allowed)"
  - test: "Strategy enable/disable from /strategy and /controls on a live stack"
    expected: "Disable then enable round-trips with reason; status badge updates; unchanged notice on repeat"
    why_human: "Visual/interaction confirmation on a real browser"
  - test: "Submit each of the 7 new Job types from its console form; open the detail page of a failed Job and click Retry; open a RUNNING paper-session Job"
    expected: "Forms validate and submit; Retry dialog creates a linked Job with lineage shown on both original and retry; paper-session shows queued-only cancel gating (no Cancel while RUNNING)"
    why_human: "Real end-to-end browser flow, visual layout, lineage rendering"
  - test: "Break-glass CLI: `python -m trading_platform.worker kill-switch-trip --reason 'x'` with the API stopped"
    expected: "Kill switch trips, JSON report printed, OPERATOR_CONTROL run written; no reset/enable/disable subcommand exists"
    why_human: "Requires a live database and process-level check; code path verified statically and by boundary test only"
---

# Phase 20: Complete Operation Migration & Safety Controls - Verification Report

**Phase Goal:** Every remaining existing long-running manual operation executes as a registered Job, immediate safety controls work from the console through synchronous HTTP endpoints that never depend on the worker, the operator can explicitly retry a failed or cancelled Job with lineage, and every mutation bypass is removed and prevented from returning.
**Verified:** 2026-09-28
**Status:** human_needed (all automated checks pass; no blocking gaps; browser/live-stack items remain)
**Re-verification:** No - initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Risk evaluation, paper session, reconciliation, ingest-bars, sync-symbol-metadata, sync-market-sessions and broker order-lifecycle sync are each a separately registered, independently validated Job type with API -> worker -> service E2E test and a console submission form | VERIFIED | `jobs/registry.py:227-252` registers 8 handlers (backtest + the 7), each with its own `*SubmissionSpec`. Each handler in `jobs/handlers/` imports and calls exactly one existing service (grep confirmed: `reconcile_paper_execution`, `sync_paper_state`, `sync_symbol_metadata`, `sync_market_sessions`, `ingest_daily_bars`, `run_risk_evaluation`, `run_paper_session`). No composite handler. E2E: `tests/test_strategy_job_types_e2e.py` (risk, recon, broker sync), `tests/test_paper_session_job_e2e.py`, `tests/test_market_data_job_types_e2e.py` (three market-data types). Console: `console/src/lib/jobTypeForms.ts` maps all 8 job types to form components under `components/jobs/new/`. Full pytest 956 passed; vitest 262 passed. |
| 2 | Paper-session Job cancellable only before broker submission; catalog states this; test proves cancel after submission starts does not interrupt broker submission | VERIFIED | `PaperSessionSubmissionSpec.cancellation_mode = QUEUED_ONLY` and description "Cancellable only while queued; once running, the session runs to completion." (`paper_session_submission.py:122-126`); exposed in catalog via `api/routes/job_types.py:46`. `JobOrchestrationService.cancel` raises `JobNotCancellableRunningError` for RUNNING queued-only types under the row lock, before any write (`job_mutations.py:376-391`); route maps to 409 `job_not_cancellable_running`. `test_cancel_while_running_is_rejected_and_submission_completes` fires the cancel from inside the real `run_paper_session` call and asserts 409, final status SUCCEEDED, no cancellation fields/events, and 2 intents submitted. Queued cancel test asserts the service never runs. |
| 3 | Domain concurrency conflict lands as distinct closed `failure_reason`, not `handler_error` | VERIFIED | `JobFailureReason.DOMAIN_CONFLICT` (`db/models/job.py:57`), enum value added in migration 0021, runner branch (`jobs/runner.py:243,315-330`), handler translation of `ConcurrentRunLockedError` (`handlers/domain_conflicts.py`). `test_lock_conflict_lands_as_domain_conflict` holds the real `session_run_lock` and asserts `failure_reason == "domain_conflict"`, no broker submission, `outcome_uncertain False`. |
| 4 | Retry of FAILED/CANCELLED creates new Job with same type+payload and `retry_of_job_id`; same-key replay returns same retry Job; non-terminal/SUCCEEDED rejected; no automatic retry | VERIFIED | `JobOrchestrationService.retry` (`job_mutations.py:481-572`): status gate (FAILED/CANCELLED only -> `JobNotRetryableError`/409), verbatim payload copy after revalidation, `submit_job(..., retry_of_job_id=job_id)` plus `JobMutation` row in one savepoint, replay via `_existing_outcome`, UNIQUE `uq_jobs_retry_of_job_id` (migration 0021) backing one-retry-per-Job. `retry_of_job_id=` is passed only at this one call site in `src/` (grep). Route `POST /api/v1/jobs/{id}/retry` returns 202 new / 200 replay with `Idempotency-Replayed`. Tests: `test_job_orchestration.py::test_retry_*` (created/linked, chain, replay, non-terminal rejection param, concurrent fresh keys -> exactly one), `test_job_mutation_api.py::test_retry_*`, `test_strategy_job_types_e2e.py::test_retry_failed_risk_evaluation_end_to_end`. No retry policy/backoff/counter code found. |
| 5 | Kill-switch trip/reset and strategy enable/disable succeed with no worker; Console -> HTTP -> `OperatorControlService` (no Job); idempotent by target state, no Idempotency-Key; write existing OPERATOR_CONTROL run + ExecutionEvent; console requires confirmation + reason | VERIFIED (automated); live-stack confirmation routed to human | `api/routes/controls.py`: `PUT /kill-switch`, `PUT /strategies/{id}` call `OperatorControlService` via `run_in_threadpool`, no Job import, `require_mutations_enabled` guard, no Idempotency-Key. `OperatorControlService._set_*` computes `changed = previous != target`, always writes an `OPERATOR_CONTROL` `StrategyRun` + `ExecutionEvent` (`operator_controls.py:229-340, 378-495`). `tests/test_control_routes.py` asserts repeat "trip" returns `changed False` with audit counts still +1 per call and Job count unchanged; TestClient runs with no worker. Console: `ControlConfirmDialog` requires a trimmed non-empty reason (<=500), typed RESET for reset, "Already X - no change (recorded)" on unchanged; mounted via `KillSwitchControlTrigger`/`StrategyControlTrigger` on `/controls`, `KillSwitchBanner`, and strategy panel; `api.ts` calls `PUT /api/v1/controls/*` only. |
| 6 | Boundary test fails if any script/worker command/Makefile target invokes a mutating service outside a Job handler or `OperatorControlService`; exemptions pinned; mutating scripts/dead commands/targets deleted; dry_run.py and generate_signals.py classified | VERIFIED | `tests/test_orchestration_boundaries.py:560-859`: `_SCRIPT_EXEMPTIONS` (6 scripts, each with a reason), `test_scripts_directory_is_exactly_the_exempt_set`, `test_makefile_targets_are_exactly_the_kept_set` (+ PHONY), Makefile recipes reference only exempt scripts/DISPATCH commands, AST scan of `scripts/` + `worker/` for a pinned set of 24 mutating entry points with a single allowed exception (`worker/commands/operator.py: trip_kill_switch`), non-vacuity test, thin-wrapper top-level-def pin, and worktree exclusion. Filesystem: `scripts/` = `export_backtest_report, generate_signals, migrate, operator_status, report_strategy_analytics, seed_phase1`; Makefile has exactly 10 targets; worker DISPATCH = report-backtest, report-strategy-analytics, operator-status, run-jobs, kill-switch-trip only. **dry_run.py:** retired (deleted, with `run_dry_bootstrap` and `tests/test_dry_run.py`) because it wrote a StrategyRun. **generate_signals.py:** classified read-only and exempted with recorded reason ("evaluates the strategy against persisted bars, writes nothing"); I independently confirmed read-only by code inspection - script, `TrendFollowingDailyStrategy.generate_signals`, `bars_for_sessions`, `latest_completed_session` contain no add/insert/update/delete/flush; the only commit is the empty `session_scope` exit. Exemption reason lives in the boundary test and `20-CONTEXT.md` D-29 (see note below). `DISPATCH` key set and parser subcommands are pinned (`test_orchestration_boundaries.py:47,87`). **Limitation (WARNING):** the mutation scan is a name denylist (`_MUTATING_ENTRY_POINTS`); lower-level mutators such as `upsert_daily_bars` / `upsert_symbol` are not listed, so a new bypass calling an unlisted mutator directly would not trip it (new scripts/worker commands would still trip the closed-world set/DISPATCH pins). Protection is strong for the enumerated bypasses, not exhaustive. |
| 7 | "Exactly two mutating routes" test replaced by explicit mutating-route allowlist covering job submit/cancel/retry and the control endpoints, all under ORCH-07 guard | VERIFIED | `tests/test_orchestration_boundaries.py:186-276`: AST decorator scan and runtime-app route scan both assert the exact 5-route set (POST /jobs, /jobs/{id}/cancel, /jobs/{id}/retry, PUT /controls/kill-switch, PUT /controls/strategies/{id}); `test_every_allowlisted_route_declares_the_mutation_guard` asserts `require_mutations_enabled` on each. Route source confirms the five decorators carry the dependency (`jobs.py:83,126,174`, `controls.py:70,91`). |

**Score:** 7/7 truths verified

### Required Artifacts (aggregate)

`gsd-sdk query verify.artifacts` on all 24 PLAN files: every declared must_have artifact passes exists/substantive checks (0 failed artifacts). `verify.key-links` reported many pattern misses because the plans express links in prose rather than greppable patterns, so I confirmed each miss manually against source:

| Link | Evidence |
|------|----------|
| handler -> service (recon, broker sync, symbol metadata, market sessions, ingest, risk, paper session) | each `jobs/handlers/*.py` imports and calls the one existing service (`reconciliation.py:30,63`, `broker_order_sync.py:34,67`, etc.) |
| `submit_job` -> `jobs.retry_of_job_id` | `jobs/dependencies.py:180,201,268` |
| `retry` -> `submit_job(retry_of_job_id=...)` | `orchestration/job_mutations.py:531-535` |
| backtest completion -> `persist_backtest_metrics` | `services/backtesting.py:25,254` |
| `submit_orders.py` -> `ensure_strategy_control_state` | `submit_orders.py:63,216,727` |
| `sync_market_sessions` -> `upsert_market_sessions` | `services/calendar.py:121,227` |
| `job_reads` -> `StrategyRun.job_id` / `MarketDataIngestionRun.job_id` | `services/job_reads.py:133,142` |
| `run_risk_evaluation` threads `job_id` | `services/risk.py:476,495,595,603` |
| `run_paper_session` -> reconcile + submission | `submit_orders.py:690,750,796,895` |
| `job_detail` -> `retry_block` | `api/routes/jobs.py:253` |
| `putJson` -> `/backend/api/v1/controls/*` | `console/src/lib/api.ts:365` uses the same `/backend${endpoint}` proxy prefix as `getJson` (:33) and `postJson` (:197); `next.config.ts:16` rewrites `/backend/:path*` |
| `DISPATCH["kill-switch-trip"]` | `worker/commands/__init__.py:28`; `DISPATCH` key set pinned by `tests/test_orchestration_boundaries.py:47,87` |
| `KillSwitchControlTrigger` -> api, `RetryJobDialog` -> `retryJob`, `JOB_TYPE_FORMS` -> 8 forms | `KillSwitchControlTrigger.tsx:69`, `RetryJobDialog.tsx:87`, `jobTypeForms.ts` |

**Score scope:** 7/7 counts the ROADMAP success criteria (the non-negotiable contract). The plans' own must_have truths are folded into those criteria; they are covered by the artifact check above, the key-link table, and the 956 pytest / 262 vitest runs, but I did not enumerate all 24 plans' truths one by one.

### Behavioral Spot-Checks / Test Runs

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full backend suite | `PYTHONPATH=src .venv/bin/python -m pytest -q` | 956 passed, 1 warning, 147s | PASS |
| Console tests | `cd console && npx vitest run` | 36 files, 262 passed | PASS |
| Console typecheck | `npx tsc --noEmit` | exit 0, no output | PASS |
| CR-A-01 repro | `is_trading_session(date(2000,1,3),"XNYS")` | raises `DateOutOfBounds` (uncaught path confirmed) | reproduced |

### Requirements Coverage

All 12 IDs appear in PLAN frontmatter and in REQUIREMENTS.md; no orphaned Phase-20 IDs (REQUIREMENTS.md maps exactly OPS-02..08, CTRL-01/02, ORCH-08 to Phase 20, plus ORCH-01/02 "closes Phase 20").

| Requirement | Source Plans | Status | Evidence |
|-------------|-------------|--------|----------|
| OPS-02 risk evaluation Job | 05, 07, 16, 19, 23 | SATISFIED | SC1 |
| OPS-03 paper session Job, queued-only cancel, catalog states it | 04, 06, 09, 10, 13, 16, 17, 20, 23 | SATISFIED | SC1, SC2 |
| OPS-04 reconciliation Job | 05, 08, 16, 19, 23 | SATISFIED | SC1 |
| OPS-05 three separate market-data Job types | 01, 03, 05, 14, 15, 16, 21 | SATISFIED | SC1 (three distinct handlers/specs, no composite) |
| OPS-06 broker order sync Job | 11, 16, 19, 23 | SATISFIED | SC1 |
| OPS-07 operator retry with lineage | 01, 04, 05, 06, 10, 13, 17, 19, 20 | SATISFIED | SC4 |
| OPS-08 typed domain conflict reason | 01, 04, 09, 20 | SATISFIED | SC3 |
| CTRL-01 strategy enable/disable control | 06, 13, 18, 22, 23 | SATISFIED | SC5 |
| CTRL-02 kill-switch trip/reset control | 06, 13, 18, 22, 23 | SATISFIED | SC5 |
| ORCH-01 only HTTP surfaces (+ break-glass trip) | 12, 24 | SATISFIED | SC6; worker DISPATCH + boundary test |
| ORCH-02 thin wrappers extended to scripts/Makefile | 03, 15, 24 | SATISFIED | SC6 thin-wrapper pin |
| ORCH-08 one mutation path per operation class | 02, 12, 24 | SATISFIED | SC6, SC7 |

### Anti-Patterns Found

Debt-marker scan (`grep -E "(TBD|FIXME|XXX)"`, stderr not suppressed, over files changed since phase start `961cdab` with deleted paths excluded via `--diff-filter=d`): no matches. No stub handlers or empty implementations observed in the handlers/routes/components read.

### Warnings (non-blocking)

- **CR-A-01** (as_of_session outside calendar window -> 500 not 422; reproduced) - see below.
- **CR-B-01** (failed ingest run row rolled back) - see below.
- **DOC-01** REQUIREMENTS.md traceability rows for ORCH-01 and ORCH-02 (lines 128-129) still read "Partial" though checkboxes are [x] and the code closes them; documentation drift.
- **BND-01** boundary-test scan is a name denylist (see SC6 row).
- **SIG-01** `generate_signals.py` exemption has no runtime write-spy test (see note below).

### Code-Review Findings vs. Success Criteria (reported honestly)

- **CR-A-01 (500 instead of 422 for pre-2006 `as_of_session`)** - confirmed by reproduction. It is a real validation-robustness defect on risk-evaluation, paper-session, reconciliation and broker-order-sync payloads. It does **not** falsify SC1: bad input is still rejected, fail-closed, with no Job or run created; only the HTTP status/typed reason is wrong. SC1 does not specify status codes. Classified WARNING; recommend fixing before Phase 21 (translate calendar bound errors into a closed rejection reason).
- **CR-B-01 (failed ingest run row rolled back)** - confirmed by reading `services/ingestion.py`. It weakens audit completeness for FAILED `ingest-bars` Jobs (empty `resources[]`, no ingestion audit row), a plan-level D-08/D-09 intent, but no roadmap success criterion requires the failure-path audit row, and SC1's E2E (success path, link to ingestion run) passes. Classified WARNING; worth fixing since Phase 21 history relies on this audit data.
- Other review warnings (WR-A-02: ingest-bars SUCCEEDED when every symbol fails; WR-B-02: no `FOR UPDATE` on control paths so concurrent PUTs can both report `changed:true`; WR-C-* console a11y/stale-state items) do not contradict any success criterion; final control state is still correct under concurrency, and idempotency-by-target-state holds.

### Notes on SC6 classification of generate_signals.py

The exemption is legitimate on the merits (verified read-only above; README describes it as "Read-only signal evaluation"). Two small weaknesses, neither blocking: (1) the recorded reason says "verified transitively" but, unlike `export_backtest_report`, `operator_status` and `report_strategy_analytics`, there is no runtime write-spy test in `tests/test_read_path_purity.py` covering it, so a future write added inside `strategy.generate_signals` would not trip the boundary test; (2) the 20-24 SUMMARY does not restate the classification (it lives in the test and 20-CONTEXT D-29). Suggest adding a write-spy case for it.

### Human Verification Required

See frontmatter `human_verification` (4 items): live-stack kill-switch/strategy controls with no worker running; the seven Job forms + Retry + queued-only cancel gating in a real browser; and the break-glass CLI against a live database.

### Gaps Summary

No blocking gaps. All seven roadmap success criteria are backed by code and passing tests (956 pytest / 262 vitest / tsc clean). Non-blocking follow-ups: CR-A-01, CR-B-01, the REQUIREMENTS.md traceability rows for ORCH-01/02 still reading "Partial", and a write-spy test for `generate_signals.py`. Status is `human_needed` solely because browser/live-stack confirmation cannot be performed in this environment.

---

_Verified: 2026-09-28_
_Verifier: Claude (gsd-verifier)_
