---
phase: 20-complete-operation-migration-safety-controls
verified: 2026-09-29T15:10:00Z
status: human_needed
score: 7/7 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: human_needed
  previous_score: 7/7 must-haves verified
  trigger: "UAT diagnosed 4 gaps in 20-HUMAN-UAT.md; gap-closure plans 20-25..20-28 executed"
  gaps_closed:
    - "UAT gap 1 - ingest-bars all-symbols-failed reported SUCCEEDED (D-08a, plan 20-25)"
    - "UAT gap 2 - Alpaca fills page_size 500 -> 422 and single-page orders/fills (plan 20-27)"
    - "UAT gap 3 - dialog re-open rendered stale state / wrong focus (plan 20-26)"
    - "UAT gap 4 - OPERATOR_CONTROL completed_at < started_at (plan 20-28)"
  gaps_remaining: []
  note: "4/4 UAT gaps closed in code with regression tests; live re-runs pending (see human_verification)"
  regressions: []
deferred_decisions:
  - decision: "Optional migration 0022: CHECK (completed_at IS NULL OR completed_at >= started_at) NOT VALID on strategy_runs, and what to do with the 25 legacy inverted OPERATOR_CONTROL rows (currently left as-is)"
    status: "open user decision (20-28-SUMMARY); not a gap - no must-have requires DB-level enforcement, and no migration 0022 exists (verified: ls alembic/versions | grep -c '^0022' = 0; no alembic diff since 38806d2)"
human_verification:
  - test: "Re-run 20-HUMAN-UAT test 3 against live Alpaca paper: submit reconciliation, broker-order-sync and paper-session Jobs from the console"
    expected: "All three Jobs no longer fail with AlpacaClientError 422 (page size); they reach a terminal state through the pagination path. NOTE: the paper account has 0 orders and 0 fills, so this proves only that the 422 is gone; completeness/multi-page behavior is proven by MockTransport tests only and cursor semantics (page_token / before_order_id, direction=desc, short-page termination) remain unverified against real Alpaca (code review IN-03)"
    why_human: "Requires live Alpaca paper credentials and a running stack; unit tests fake the HTTP transport"
  - test: "Re-run 20-HUMAN-UAT test 5(c): ingest-bars with an invalid Polygon key and with an unreachable Polygon base URL on a live stack"
    expected: "Job FAILED / handler_error, failure_message 'IngestionAllSymbolsFailedError: Ingestion run <id> failed: 0 of N symbols succeeded; failed: SYM (PolygonAuthError)...', Job resources show the ingestion run as FAILED; Retry is offered. A 1-ok + 1-fail run still ends Job SUCCEEDED with run PARTIAL"
    why_human: "Needs the live worker + Polygon fault injection; automated tests use fakes"
  - test: "Re-open every confirmation dialog in a real browser (kill-switch trip/reset from /controls and the banner, strategy enable/disable, Cancel Job, Retry Job): after keep, error, and changed:false 'Already ...' notice then Close then re-open"
    expected: "First rendered frame shows body text, empty reason, disabled confirm, no stale alert; focus lands in the Reason textarea (Control dialog) or Close (Retry dialog) on every opening; Tab stays trapped"
    why_human: "jsdom FrameProbe tests prove the first committed frame, but real focus and paint behavior in Chromium (the original UAT defect) was found only by browser automation"
  - test: "Trip/reset kill switch and enable/disable strategy on the live stack, then read strategy_runs for the new OPERATOR_CONTROL rows"
    expected: "Each new row has completed_at >= started_at, event_at == completed_at, result_summary.changed_at ends +00:00. (25 legacy rows written before the fix remain inverted by design - deferred decision above)"
    why_human: "Postgres tests cover this on a throwaway DB; the live persistent DB and remote-latency behavior were where the defect was observed"
---

# Phase 20: Complete Operation Migration & Safety Controls - Verification Report

**Phase Goal:** Every remaining existing long-running manual operation executes as a registered Job, immediate safety controls work from the console through synchronous HTTP endpoints that never depend on the worker, the operator can explicitly retry a failed or cancelled Job with lineage, and every mutation bypass is removed and prevented from returning.
**Verified:** 2026-09-29 (re-verification; initial verification 2026-09-28)
**Status:** human_needed (all automated checks pass; no blocking gaps; four live re-runs remain after gap closure 20-25..20-28)
**Re-verification:** Yes - after gap closure. Initial verification (2026-09-28) was human_needed; 20-HUMAN-UAT executed those 4 items (all passed) and diagnosed 4 gaps, closed by plans 20-25..20-28.

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Risk evaluation, paper session, reconciliation, ingest-bars, sync-symbol-metadata, sync-market-sessions and broker order-lifecycle sync are each a separately registered, independently validated Job type with API -> worker -> service E2E test and a console submission form | VERIFIED | `jobs/registry.py:227-252` registers 8 handlers (backtest + the 7), each with its own `*SubmissionSpec`. Each handler in `jobs/handlers/` imports and calls exactly one existing service (grep confirmed: `reconcile_paper_execution`, `sync_paper_state`, `sync_symbol_metadata`, `sync_market_sessions`, `ingest_daily_bars`, `run_risk_evaluation`, `run_paper_session`). No composite handler. E2E: `tests/test_strategy_job_types_e2e.py` (risk, recon, broker sync), `tests/test_paper_session_job_e2e.py`, `tests/test_market_data_job_types_e2e.py` (three market-data types). Console: `console/src/lib/jobTypeForms.ts` maps all 8 job types to form components under `components/jobs/new/`. Full pytest 956 passed; vitest 262 passed. **Amendment 2026-09-29 (UAT gap 2):** the cited E2E tests replace AlpacaClient with a fake broker, so they never exercised Alpaca HTTP parameters. Against live Alpaca paper, the reconciliation, broker-order-sync and paper-session Jobs failed with 422 (FILL page_size 500 > max 100), and both list calls were single-page. Plan 20-27 fixed this: documented limits (100/500), cursor pagination, and typed cap/stall errors, pinned by tests/test_alpaca_pagination.py. The live re-run of 20-HUMAN-UAT test 3 is pending. |
| 2 | Paper-session Job cancellable only before broker submission; catalog states this; test proves cancel after submission starts does not interrupt broker submission | VERIFIED | `PaperSessionSubmissionSpec.cancellation_mode = QUEUED_ONLY` and description "Cancellable only while queued; once running, the session runs to completion." (`paper_session_submission.py:122-126`); exposed in catalog via `api/routes/job_types.py:46`. `JobOrchestrationService.cancel` raises `JobNotCancellableRunningError` for RUNNING queued-only types under the row lock, before any write (`job_mutations.py:376-391`); route maps to 409 `job_not_cancellable_running`. `test_cancel_while_running_is_rejected_and_submission_completes` fires the cancel from inside the real `run_paper_session` call and asserts 409, final status SUCCEEDED, no cancellation fields/events, and 2 intents submitted. Queued cancel test asserts the service never runs. |
| 3 | Domain concurrency conflict lands as distinct closed `failure_reason`, not `handler_error` | VERIFIED | `JobFailureReason.DOMAIN_CONFLICT` (`db/models/job.py:57`), enum value added in migration 0021, runner branch (`jobs/runner.py:243,315-330`), handler translation of `ConcurrentRunLockedError` (`handlers/domain_conflicts.py`). `test_lock_conflict_lands_as_domain_conflict` holds the real `session_run_lock` and asserts `failure_reason == "domain_conflict"`, no broker submission, `outcome_uncertain False`. |
| 4 | Retry of FAILED/CANCELLED creates new Job with same type+payload and `retry_of_job_id`; same-key replay returns same retry Job; non-terminal/SUCCEEDED rejected; no automatic retry | VERIFIED | `JobOrchestrationService.retry` (`job_mutations.py:481-572`): status gate (FAILED/CANCELLED only -> `JobNotRetryableError`/409), verbatim payload copy after revalidation, `submit_job(..., retry_of_job_id=job_id)` plus `JobMutation` row in one savepoint, replay via `_existing_outcome`, UNIQUE `uq_jobs_retry_of_job_id` (migration 0021) backing one-retry-per-Job. `retry_of_job_id=` is passed only at this one call site in `src/` (grep). Route `POST /api/v1/jobs/{id}/retry` returns 202 new / 200 replay with `Idempotency-Replayed`. Tests: `test_job_orchestration.py::test_retry_*` (created/linked, chain, replay, non-terminal rejection param, concurrent fresh keys -> exactly one), `test_job_mutation_api.py::test_retry_*`, `test_strategy_job_types_e2e.py::test_retry_failed_risk_evaluation_end_to_end`. No retry policy/backoff/counter code found. |
| 5 | Kill-switch trip/reset and strategy enable/disable succeed with no worker; Console -> HTTP -> `OperatorControlService` (no Job); idempotent by target state, no Idempotency-Key; write existing OPERATOR_CONTROL run + ExecutionEvent; console requires confirmation + reason | VERIFIED (automated); live-stack confirmation routed to human | `api/routes/controls.py`: `PUT /kill-switch`, `PUT /strategies/{id}` call `OperatorControlService` via `run_in_threadpool`, no Job import, `require_mutations_enabled` guard, no Idempotency-Key. `OperatorControlService._set_*` computes `changed = previous != target`, always writes an `OPERATOR_CONTROL` `StrategyRun` + `ExecutionEvent` (`operator_controls.py:229-340, 378-495`). `tests/test_control_routes.py` asserts repeat "trip" returns `changed False` with audit counts still +1 per call and Job count unchanged; TestClient runs with no worker. Console: `ControlConfirmDialog` requires a trimmed non-empty reason (<=500), typed RESET for reset, "Already X - no change (recorded)" on unchanged; mounted via `KillSwitchControlTrigger`/`StrategyControlTrigger` on `/controls`, `KillSwitchBanner`, and strategy panel; `api.ts` calls `PUT /api/v1/controls/*` only. |
| 6 | Boundary test fails if any script/worker command/Makefile target invokes a mutating service outside a Job handler or `OperatorControlService`; exemptions pinned; mutating scripts/dead commands/targets deleted; dry_run.py and generate_signals.py classified | VERIFIED | `tests/test_orchestration_boundaries.py:560-859`: `_SCRIPT_EXEMPTIONS` (6 scripts, each with a reason), `test_scripts_directory_is_exactly_the_exempt_set`, `test_makefile_targets_are_exactly_the_kept_set` (+ PHONY), Makefile recipes reference only exempt scripts/DISPATCH commands, AST scan of `scripts/` + `worker/` for a pinned set of 24 mutating entry points with a single allowed exception (`worker/commands/operator.py: trip_kill_switch`), non-vacuity test, thin-wrapper top-level-def pin, and worktree exclusion. Filesystem: `scripts/` = `export_backtest_report, generate_signals, migrate, operator_status, report_strategy_analytics, seed_phase1`; Makefile has exactly 10 targets; worker DISPATCH = report-backtest, report-strategy-analytics, operator-status, run-jobs, kill-switch-trip only. **dry_run.py:** retired (deleted, with `run_dry_bootstrap` and `tests/test_dry_run.py`) because it wrote a StrategyRun. **generate_signals.py:** classified read-only and exempted with recorded reason ("evaluates the strategy against persisted bars, writes nothing"); I independently confirmed read-only by code inspection - script, `TrendFollowingDailyStrategy.generate_signals`, `bars_for_sessions`, `latest_completed_session` contain no add/insert/update/delete/flush; the only commit is the empty `session_scope` exit. Exemption reason lives in the boundary test and `20-CONTEXT.md` D-29 (see note below). `DISPATCH` key set and parser subcommands are pinned (`test_orchestration_boundaries.py:47,87`). **Limitation (WARNING):** the mutation scan is a name denylist (`_MUTATING_ENTRY_POINTS`); lower-level mutators such as `upsert_daily_bars` / `upsert_symbol` are not listed, so a new bypass calling an unlisted mutator directly would not trip it (new scripts/worker commands would still trip the closed-world set/DISPATCH pins). Protection is strong for the enumerated bypasses, not exhaustive. |
| 7 | "Exactly two mutating routes" test replaced by explicit mutating-route allowlist covering job submit/cancel/retry and the control endpoints, all under ORCH-07 guard | VERIFIED | `tests/test_orchestration_boundaries.py:186-276`: AST decorator scan and runtime-app route scan both assert the exact 5-route set (POST /jobs, /jobs/{id}/cancel, /jobs/{id}/retry, PUT /controls/kill-switch, PUT /controls/strategies/{id}); `test_every_allowlisted_route_declares_the_mutation_guard` asserts `require_mutations_enabled` on each. Route source confirms the five decorators carry the dependency (`jobs.py:83,126,174`, `controls.py:70,91`). |

**Score:** 7/7 truths verified

### Gap-Closure Re-Verification (plans 20-25..20-28, UAT gaps 1-4)

Each row was checked against source and tests, not SUMMARY claims. Regression check on the 7 truths above: the files changed by gap closure (`ingestion.py`, `data.py`, `ingest_bars.py`, `alpaca.py`, `operator_controls.py`, three console dialogs) do not touch the registry, retry/cancel orchestration, control routes, boundary test or Makefile/scripts; `tests/test_orchestration_boundaries.py` still passes (47).

| UAT gap | Plan | Must-have checked | Status | Evidence |
|---------|------|-------------------|--------|----------|
| 1. ingest-bars all-symbols-failed shows Job SUCCEEDED | 20-25 (OPS-05) | Service owns the rule; zero succeeded -> run FAILED; handler propagates typed error before the cancel checkpoint; partial stays SUCCEEDED; no new enum/migration | VERIFIED (code + tests); live re-run pending | `services/ingestion.py:153-170` pure `_derive_run_status` (failed on run_error or succeeded_count==0, partial on >=1 ok and >=1 fail, ValueError on negatives); `ingestion.py:277-348` counts a symbol succeeded only after its `session_scope` exits, passes the derived status to `_finalize_run`, class-name-only `error_message`; `services/data.py:46-99` closed `IngestionRunStatus` Literal, required kw-only `run_status`, `IngestionAllSymbolsFailedError`, `raise_for_all_symbols_failed()`; `jobs/handlers/ingest_bars.py:70-95` log with `run_status`, then `raise_for_all_symbols_failed()`, then `raise_if_cancelled()`. D-08a and the D-05 scope note are in 20-CONTEXT.md:54,77. No `0022` migration, no alembic diff. Tests: truth table (10 rows), all-fail/empty-bars/partial service tests, handler all-fail and cancel-during-all-fail, E2E all-fail (Job failed / handler_error, run FAILED, retry 202) and 1ok+1fail (Job succeeded, run partial). |
| 2. Alpaca fills page_size 500 -> 422; no pagination | 20-27 (OPS-03/04/06) | Documented limits; complete-set-or-typed-error; no date bounding; callers unchanged | VERIFIED (MockTransport); live re-run pending | `services/alpaca.py:44-51` constants 100/500/100 pages/20 pages; `:313-346` `list_orders(*, status)` (limit 500, `before_order_id`) and `list_fills(self)` (page_size 100, `page_token`), both `direction=desc`, no date/after/until; `:348-411` `_paginate` (cursor key absent on request 1, duplicate id / missing id / non-list payload -> `AlpacaPaginationStalledError`, cap -> `AlpacaPaginationCapExceededError`, never a partial list; both subclass `AlpacaClientError` so the Job handler_error path is unchanged). `tests/test_alpaca_pagination.py` (23 cases incl. 237-item fills chain, 1012-item orders chain, `load_broker_state` 503 orders / 105 fills) pass. SC1 amendment and OPS-03/04/06 traceability annotations are present (REQUIREMENTS.md:142-146). Caveat: cursor semantics and short-page termination are unverified against real Alpaca (paper account has 0 orders / 0 fills). |
| 3. Dialog re-open renders stale state; focus on dismiss button | 20-26 (CTRL-01/02, OPS-07) | Persistent shell + per-opening body; no reset-in-effect; WR-C-01 guard preserved; idempotency key per opening | VERIFIED (jsdom); browser re-run pending | `ControlConfirmDialog.tsx:60-78` shell owns `wasOpenRef`/`openingRef` and mounts `ControlConfirmDialogBody` only while open; body owns all five state values (`:96-100`); `:141-148` continuation compares captured opening to `openingRef.current`; `setReason("")`/`setUnchanged(false)` calls are gone. `CancelJobDialog.tsx:32-49` and `RetryJobDialog.tsx:42-58` use shell/body with `useState(() => newIdempotencyKey())`. Console dialog suites (22 files / 207 tests in controls+jobs) and the full vitest run (38 files / 346) pass, including FrameProbe first-frame tests and the existing WR-C-01 test. |
| 4. Control audit `completed_at < started_at` | 20-28 (CTRL-01/02) | One DB clock read inside the transaction after the row lock | VERIFIED (Postgres tests); live-DB confirmation pending | `services/operator_controls.py:296-299` and `:475-478` call `_db_clock_now(session)` immediately after the row lock; `:669-677` reads `clock_timestamp()`, rejects a naive value, normalizes to UTC; grep finds no `datetime.now` in the file. `tests/test_operator_control_timestamps.py` (13 Postgres cases: 8 action x changed matrix, `event_at == completed_at`, `changed_at` `+00:00`, `last_changed_at`, two lock-wait cases asserting >= 0.3 s, break-glass report) passes. D-11a is in 20-CONTEXT.md:97. No migration and no CHECK constraint added (the CHECK is a deferred user decision). |

**Deferred decision (not a gap):** optional migration 0022 `CHECK (completed_at IS NULL OR completed_at >= started_at) NOT VALID` and the 25 legacy inverted OPERATOR_CONTROL rows left as-is. No roadmap success criterion or plan must-have requires DB-level enforcement; 20-28 explicitly claims none. Awaiting the user's choice (rewrite history, NOT VALID constraint, or none).

**Gap-closure code review (20-REVIEW-gap-closure.md; 0 critical, 2 warnings, 4 info) - advisory, no must-have broken:**
- WR-01 (Cancel/Retry dialogs: stale continuation can call `onClose`/`onNavigate` after re-open; dismiss/Escape not gated while submitting). Pre-existing, narrowed by the split; the 20-26 must-haves scope the `openingRef` guard to `ControlConfirmDialog`, and server-side idempotency still prevents a double write. WARNING.
- WR-02 (pagination caps 10,000 fills / 10,000 orders are unconfigurable module constants; past that ceiling reconcile/sync fails permanently with a typed error). The must-have is "complete set or typed error", which holds; the ceiling is an operational limit. WARNING; recommend making it a setting or documenting it.
- IN-01 (unreachable `next_cursor == cursor` branch), IN-02 (cap error is a false positive at an exact multiple of `max_pages * page_size`; fails closed), IN-03 (live cursor semantics unverified; covered by the human item above), IN-04 (`IngestionResult.succeeded` is `failed_count == 0`, independent of `run_status`, and `IngestionAllSymbolsFailedError` is not pickle/copy-safe; latent only). INFO.

**Initial human-verification items:** the four items from the 2026-09-28 verification were executed in 20-HUMAN-UAT (tests 1-4, all pass, automated Playwright/shell against a live stack). They are superseded by the four re-run items in the frontmatter, which exist because the gap-closure code has only been exercised by unit/integration tests.

### Required Artifacts (aggregate)

`gsd-sdk query verify.artifacts` on all 28 PLAN files (24 original + gap-closure 20-25..20-28): every declared must_have artifact passes exists/substantive checks (0 failed artifacts). For 20-25..20-28 specifically: 12/12 artifacts pass (20-25 4/4, 20-26 4/4, 20-27 2/2, 20-28 2/2); `min_lines` confirmed directly: `tests/test_alpaca_pagination.py` 436 lines (>=150), `tests/test_operator_control_timestamps.py` 266 lines (>=120). `verify.key-links` reported many pattern misses because the plans express links in prose rather than greppable patterns, so I confirmed each miss manually against source:

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
| Gap-closure key links (20-25..20-28): `verify.key-links` reported 0/8 verified, all tool artifacts (double-escaped regex in plan patterns such as `raise_for_all_symbols_failed\\(\\)`, and prose `from:` values that are not file paths); each confirmed manually | `ingest_bars.py:89` `result.raise_for_all_symbols_failed()` between the completion log (:76) and `raise_if_cancelled()` (:95); `ingestion.py:332-348` `_derive_run_status` -> `_finalize_run(status=...)` -> `IngestionResult.run_status`; `ControlConfirmDialog.tsx:66,141-148` `openingRef` prop compared after `await`; exactly 1 dialog tag and no `key=` on it in `KillSwitchControlTrigger.tsx`, `StrategyControlTrigger.tsx` (1 each) and `jobs/detail/JobHeaderPanel.tsx` (Cancel + Retry; the only `key=` is on a dependency list item); `report.py:127-128` and `sync_orders.py:69-70` still call `client.list_orders()` / `client.list_fills()` with no arguments (callers unchanged); `alpaca.py:_paginate` calls `_request_with_retry("GET", ...)` once per page; `operator_controls.py:296-299` and `:474-478` `_db_clock_now(session)` directly after the locked-record / `for_update=True` load |
| `KillSwitchControlTrigger` -> api, `RetryJobDialog` -> `retryJob`, `JOB_TYPE_FORMS` -> 8 forms | `KillSwitchControlTrigger.tsx:69`, `RetryJobDialog.tsx:87`, `jobTypeForms.ts` |

**Score scope:** 7/7 counts the ROADMAP success criteria (the non-negotiable contract). The plans' own must_have truths are folded into those criteria; they are covered by the artifact check above, the key-link table, and the 956 pytest / 262 vitest runs, but I did not enumerate all 24 plans' truths one by one.

### Behavioral Spot-Checks / Test Runs

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full backend suite | `PYTHONPATH=src .venv/bin/python -m pytest -q` | 956 passed on 2026-09-28; 1076 passed on 2026-09-29 after gap closure (orchestrator run) | PASS |
| Gap-closure backend suites (own run) | `pytest -q tests/test_alpaca_pagination.py tests/test_operator_control_timestamps.py tests/test_market_data_ingestion.py tests/test_ingest_bars_job_type.py tests/test_market_data_job_types_e2e.py tests/test_orchestration_boundaries.py` | 159 passed | PASS |
| Console tests | `cd console && npx vitest run` | 262 passed (2026-09-28); 38 files / 346 passed (2026-09-29, orchestrator run); own run of controls+jobs component dirs: 22 files / 207 passed | PASS |
| No migration added by gap closure | `ls alembic/versions \| grep -c '^0022'`; `git diff 38806d2 HEAD --stat -- alembic` | 0; empty | PASS |
| Schema drift | orchestrator run | none | PASS |
| Console typecheck | `npx tsc --noEmit` | exit 0, no output | PASS |
| CR-A-01 repro (historical, 2026-09-28; since fixed by review-fix, typed 422, confirmed live in UAT test 3) | `is_trading_session(date(2000,1,3),"XNYS")` | raised `DateOutOfBounds` (uncaught path confirmed at that time) | superseded |

### Requirements Coverage

All 12 IDs appear in PLAN frontmatter and in REQUIREMENTS.md; no orphaned Phase-20 IDs (REQUIREMENTS.md maps exactly OPS-02..08, CTRL-01/02, ORCH-08 to Phase 20, plus ORCH-01/02 "closes Phase 20").

| Requirement | Source Plans | Status | Evidence |
|-------------|-------------|--------|----------|
| OPS-02 risk evaluation Job | 05, 07, 16, 19, 23 | SATISFIED | SC1 |
| OPS-03 paper session Job, queued-only cancel, catalog states it | 04, 06, 09, 10, 13, 16, 17, 20, 23, 27 | SATISFIED | SC1, SC2 (live Alpaca re-run pending, see SC1 amendment) |
| OPS-04 reconciliation Job | 05, 08, 16, 19, 23, 27 | SATISFIED | SC1 (live Alpaca re-run pending, see SC1 amendment) |
| OPS-05 three separate market-data Job types | 01, 03, 05, 14, 15, 16, 21, 25 | SATISFIED | SC1 (three distinct handlers/specs, no composite); D-08a all-fail semantics (20-25) |
| OPS-06 broker order sync Job | 11, 16, 19, 23, 27 | SATISFIED | SC1 (live Alpaca re-run pending, see SC1 amendment) |
| OPS-07 operator retry with lineage | 01, 04, 05, 06, 10, 13, 17, 19, 20, 26 | SATISFIED | SC4 |
| OPS-08 typed domain conflict reason | 01, 04, 09, 20 | SATISFIED | SC3 |
| CTRL-01 strategy enable/disable control | 06, 13, 18, 22, 23, 26, 28 | SATISFIED | SC5 |
| CTRL-02 kill-switch trip/reset control | 06, 13, 18, 22, 23, 26, 28 | SATISFIED | SC5 |
| ORCH-01 only HTTP surfaces (+ break-glass trip) | 12, 24 | SATISFIED | SC6; worker DISPATCH + boundary test |
| ORCH-02 thin wrappers extended to scripts/Makefile | 03, 15, 24 | SATISFIED | SC6 thin-wrapper pin |
| ORCH-08 one mutation path per operation class | 02, 12, 24 | SATISFIED | SC6, SC7 |

### Anti-Patterns Found

Debt-marker scan (`grep -E "(TBD|FIXME|XXX)"`, stderr not suppressed, over files changed since phase start `961cdab` with deleted paths excluded via `--diff-filter=d`): no matches. Re-verification scan over gap-closure changes (`git diff 38806d2 HEAD --name-only -- src tests console/src`, 16 files): no matches. No stub handlers or empty implementations observed in the handlers/routes/components read.

### Warnings (non-blocking)

- **CR-A-01** (as_of_session outside calendar window -> 500 not 422; reproduced 2026-09-28) - RESOLVED by review-fix (typed 422), confirmed live in UAT test 3.
- **CR-B-01** (failed ingest run row rolled back) - RESOLVED for run-level failures, confirmed live in UAT 5c.
- **DOC-01** (resolved) REQUIREMENTS.md traceability rows for ORCH-01 and ORCH-02 (lines 128-129) now read "Complete".
- **BND-01** boundary-test scan is a name denylist (see SC6 row).
- **SIG-01** `generate_signals.py` exemption has no runtime write-spy test (see note below).

### Code-Review Findings vs. Success Criteria (reported honestly)

- **CR-A-01 (500 instead of 422 for pre-2006 `as_of_session`)** - HISTORICAL as of 2026-09-28 (since fixed: typed 422 `as_of_session_out_of_calendar_range`, live-confirmed in UAT test 3). Original assessment follows. It was confirmed by reproduction. It is a real validation-robustness defect on risk-evaluation, paper-session, reconciliation and broker-order-sync payloads. It does **not** falsify SC1: bad input is still rejected, fail-closed, with no Job or run created; only the HTTP status/typed reason is wrong. SC1 does not specify status codes. Classified WARNING; the recommendation to translate calendar bound errors into a closed rejection reason has been carried out.
- **CR-B-01 (failed ingest run row rolled back)** - HISTORICAL; fixed for run-level failures (live-confirmed in UAT 5c: Job FAILED with the FAILED run in resources). Original assessment: confirmed by reading `services/ingestion.py`. It weakens audit completeness for FAILED `ingest-bars` Jobs (empty `resources[]`, no ingestion audit row), a plan-level D-08/D-09 intent, but no roadmap success criterion requires the failure-path audit row, and SC1's E2E (success path, link to ingestion run) passes. Classified WARNING; worth fixing since Phase 21 history relies on this audit data.
- Other review warnings (WR-A-02: ingest-bars SUCCEEDED when every symbol fails, resolved 2026-09-29 by D-08a / plan 20-25; WR-B-02: no `FOR UPDATE` on control paths so concurrent PUTs can both report `changed:true`; WR-C-* console a11y/stale-state items) do not contradict any success criterion; final control state is still correct under concurrency, and idempotency-by-target-state holds.

### Notes on SC6 classification of generate_signals.py

The exemption is legitimate on the merits (verified read-only above; README describes it as "Read-only signal evaluation"). Two small weaknesses, neither blocking: (1) the recorded reason says "verified transitively" but, unlike `export_backtest_report`, `operator_status` and `report_strategy_analytics`, there is no runtime write-spy test in `tests/test_read_path_purity.py` covering it, so a future write added inside `strategy.generate_signals` would not trip the boundary test; (2) the 20-24 SUMMARY does not restate the classification (it lives in the test and 20-CONTEXT D-29). Suggest adding a write-spy case for it.

### Human Verification Required

See frontmatter `human_verification` (4 live re-run items after gap closure): (1) UAT test 3 against live Alpaca paper (reconciliation, broker-order-sync, paper-session) to confirm the 422 is gone; (2) UAT test 5(c) ingest-bars all-symbols-failed on a live stack; (3) real-browser dialog re-open and focus for all control/Cancel/Retry dialogs; (4) live-DB check that new OPERATOR_CONTROL rows satisfy `completed_at >= started_at`.

### Gaps Summary

No blocking gaps. All seven roadmap success criteria remain backed by code and passing tests (1076 pytest / 346 vitest / no schema drift), and all four UAT gaps (20-HUMAN-UAT gaps 1-4) are closed in code with regression tests. Non-blocking follow-ups: a write-spy test for `generate_signals.py`, and gap-closure review WR-01/WR-02. Since the initial verification, CR-A-01 is fixed (typed 422 `as_of_session_out_of_calendar_range`, `payload_fields.py`; 20-REVIEW-FIX; confirmed live in UAT test 3: zero rows written) and CR-B-01 is fixed for run-level failures (confirmed live in UAT 5c). One open user decision is recorded (migration 0022 CHECK / legacy rows). Status is `human_needed` because the gap-closure fixes have only been exercised by fakes/throwaway databases and jsdom; the four live re-runs in the frontmatter must be done before treating UAT gaps 1-4 as closed end to end.

---

_Verified: 2026-09-29 (re-verification; initial 2026-09-28)_
_Verifier: Claude (gsd-verifier)_
