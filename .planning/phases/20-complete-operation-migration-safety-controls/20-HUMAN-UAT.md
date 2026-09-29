---
status: partial
phase: 20-complete-operation-migration-safety-controls
source: [20-VERIFICATION.md]
started: 2026-09-28T23:15:00Z
updated: 2026-09-29T12:20:00Z
---

## Current Test

[awaiting human re-test of gap-closure fixes 20-25..20-28 — tests 6-9]

## Tests

### 1. Kill-switch trip/reset from /controls and the KillSwitchBanner with NO worker running
expected: Dialog requires a non-blank reason (<=500 chars) and typed RESET for reset; trip succeeds; banner flips to tripped; a second trip shows "Already tripped - no change (recorded)"; a new OPERATOR_CONTROL run + ExecutionEvent row exists each time
result: pass
verified_by: automated (Playwright headless Chromium against the running console on :3000 -> host API :8000 -> Homebrew Postgres ::1:5432 at 0021 head; DB read in READ ONLY transactions)
evidence:
  - precondition: worker (watchfiles + run-jobs) stopped and confirmed absent via ps; no compose worker; kill switch ARMED at start
  - dialog validation (both /controls panel and KillSwitchBanner on /jobs): confirm disabled for empty, whitespace-only and 501-char reasons; enabled at 500 chars
  - reset dialog shows the typed field; confirm disabled for "", "reset", "RESE", "Reset" and for RESET without a reason; enabled only for RESET plus a valid reason. Trip dialog has no typed field
  - trip from /controls panel: dialog closed on changed:true; banner flipped to "KILL SWITCH TRIPPED — order submission halted — ... (UAT-T1 trip via /controls panel)"; panel shows TRIPPED plus the Reset trigger. DB: +1 operator_control run 1aae76fc (trigger_source api_control, changed true), +1 kill_switch_trip event, system_controls.last_change_run_id = 1aae76fc
  - second trip from a page opened while ARMED: dialog stayed open with alert "Already TRIPPED — no change (recorded)"; confirm hidden; Close shown and focused. DB: +1 run 85662053 (changed false), +1 kill_switch_trip event, state still tripped
  - reset from banner on /jobs: banner back to "Kill switch: ARMED". DB: +1 run 17a99974, +1 kill_switch_reset event. Stale second reset: "Already ARMED — no change (recorded)", +1 run 1c1ff8d5 (changed false), +1 event
  - trip from banner (run d978ee22) and reset from /controls panel (run 1035e018): each +1 run, +1 event
  - cancel paths (Escape, Keep Current State) wrote zero rows. Total: 6 PUTs, 6 runs, 6 events (3 trip, 3 reset). Final state ARMED

### 2. Strategy enable/disable from /strategy and /controls on a live stack
expected: Disable then enable round-trips with a reason; status badge updates; unchanged notice on repeat
result: pass
verified_by: automated (Playwright against the running console; DB read-only)
evidence:
  - disable from /strategy: dialog "Current state: ENABLED. This will change it to: DISABLED.", confirm disabled without a reason, no RESET field; after confirm the badge showed DISABLED and the trigger became Enable Strategy. DB: strategies.status=disabled, +1 operator_control run 9dfbcf5d (api_control, changed true), +1 strategy_disabled event
  - repeat disable from a stale /controls dialog: "Already DISABLED — no change (recorded)"; +1 run 7228190b (changed false), +1 strategy_disabled event; the /controls badge resynced to DISABLED
  - enable from /controls: badge ENABLED; DB active; +1 run 6e05bcf8, +1 strategy_enabled event. Repeat enable from a stale /strategy dialog: "Already ENABLED — no change (recorded)", +1 run 0fb484ac (changed false)
  - reverse round trip (disable from /controls, then enable from /strategy): each +1 run and +1 event; after the /controls disable, /strategy showed DISABLED (it reads DB control state, not the static config flag)
  - final state: GET /api/v1/controls/strategies/trend_following_daily -> enabled
note: While running this test I found a dialog re-open defect that the literal criteria above don't cover. It is logged under Gaps (test 5).

### 3. Submit each of the 7 new Job types from its console form; Retry a failed Job; open a RUNNING paper-session Job
expected: Forms validate and submit; Retry creates a linked Job with lineage shown on both the original and the retry; paper-session shows queued-only cancel gating (no Cancel while RUNNING)
result: pass
verified_by: automated (Playwright + worker run-jobs --once + DB read-only)
evidence:
  - all 7 forms validate:
    - ingest-bars, sync-market-sessions and sync-symbol-metadata open prefilled with defaults. Clearing the fields disables submit, and so does leaving only from_date filled. A symbols value of " , " parses to [] and stays disabled
    - risk-evaluation, reconciliation, broker-order-sync and paper-session: submit disabled with neither field, with the date only, and with the strategy only; enabled with both
  - server-side rejection: risk-evaluation as_of_session=2000-01-03 stayed on the form, showed "This submission was rejected: the as-of session date is outside the exchange calendar's supported range.", and wrote zero jobs rows
  - all 7 submitted from their forms and navigated to /jobs/{id}, each with a DB row of the right job_type and exact payload:
    - ingest-bars a62ba406, a9d3fec6, b120d584
    - sync-market-sessions 27f2b40c
    - sync-symbol-metadata e55684dd (payload symbols ["QQQ","SPY"] from "spy, qqq, spy")
    - risk-evaluation ade07e3d
    - reconciliation dbf6a335
    - broker-order-sync 302020fc
    - paper-session 45b09f0d (payload carries the optional risk_run_id as null)
  - execution outcomes (outside this item's criteria): ingest-bars (retry), sync-market-sessions, sync-symbol-metadata and risk-evaluation SUCCEEDED. reconciliation, broker-order-sync and paper-session FAILED against live Alpaca paper; see Gaps
  - Retry: the failed ingest-bars Job b120d584 got Retry -> "Retry Job ingest-bars · b120d584" -> Retry Job, which navigated to new Job 02df4a94 (DB retry_of_job_id=b120d584, same payload)
    - 02df4a94 shows "Retry of Job b120d584" linking /jobs/b120d584
    - b120d584 shows "Retried as Job 02df4a94" linking /jobs/02df4a94, with Retry disabled and "Already retried — see the linked retry Job below."
    - the retry Job then SUCCEEDED (ingestion run 44255641 succeeded)
  - paper-session cancel gating (kill switch TRIPPED throughout, so no broker order submission was possible; broker endpoint is Alpaca paper):
    - while QUEUED: Cancel Job… enabled, no gating text
    - catalog: cancellation_mode queued_only, with "Cancellable only while queued; once running, the session runs to completion."
    - held RUNNING by SIGSTOPping the --once worker right after its claim. The detail page showed a running badge, Cancel Job… disabled with "Not cancellable once running", and clicking it opened no dialog
    - POST /jobs/45b09f0d/cancel -> 409 job_not_cancellable_running with cancellation_requested_at still null. After SIGCONT the Job ran to its own terminal state with no cancellation fields
note: The Cancel button is present but disabled while RUNNING, with the reason shown, rather than absent. That matches D-03a and the UI code.

### 4. Break-glass CLI `python -m trading_platform.worker kill-switch-trip --reason 'x'` with the API stopped
expected: Kill switch trips, JSON report printed, OPERATOR_CONTROL run written; no reset/enable/disable subcommand exists
result: pass
verified_by: automated (shell + DB read-only)
evidence:
  - API stopped: nothing listening on :8000 (lsof), checked before each CLI run
  - run 1 (right after the 5a outage): committed operator_control run c47af8af (trigger_source break_glass_cli, changed true, +1 kill_switch_trip event). My JSON extraction failed on that run's stdout, so I re-armed the switch through the API and ran it again
  - clean run 2 (`PYTHONPATH=src .venv/bin/python -m trading_platform.worker kill-switch-trip --reason 'UAT-T4 break-glass rerun, API stopped'`): exit 0, empty stderr. stdout ends with a JSON report: run_id 06f2f707, action trip, previous_state armed, current_state tripped, changed true, trigger_source break_glass_cli. DB: +1 operator_control run 06f2f707 (+1 kill_switch_trip event); system_controls state=tripped, last_change_run_id=06f2f707
  - `--help` lists exactly {report-backtest, report-strategy-analytics, operator-status, run-jobs, kill-switch-trip}. kill-switch-reset, reset-kill-switch, enable-strategy, disable-strategy, strategy-enable, strategy-disable and operator-control each exit 2 with argparse "invalid choice"
  - a blank `--reason '   '` exits 2 ("--reason must be 1-500 characters after trimming ...") with zero rows written
observation: The JSON report is not the only thing on stdout. Two structured log lines (kill_switch_applied, worker_kill_switch_break_glass_trip) come before it, so `... | jq` on the raw stdout fails. The report also has completed_at earlier than started_at, which all control runs share (see Observations).

### 5. Code-review fixes that need a live stack (see 20-REVIEW-FIX.md)
expected: (a) Control dialogs: Escape/Keep Current State disabled while a PUT is in flight; focus moves into the dialog, Tab is trapped, focus returns on close; an API outage (stop the API mid-trip) shows the outage message, not "Request rejected"; banner resyncs after a transport failure. (b) Enable/disable on an archived strategy returns the archived notice. (c) A failing ingest-bars Job (bad Polygon key or unreachable) shows its ingestion run as FAILED in Job resources. (d) Kill-switch trip works even if the strategy registry fails to load. (e) Two concurrent same-key retries both return the same retry Job.
result: issue
reported: "(c) not met as literally written: with a bad Polygon key or an unreachable Polygon, the ingest-bars Job SUCCEEDS and its ingestion run is PARTIAL, not FAILED. Only a missing key (a run-level error) produces a failing Job, and that one does show FAILED in Job resources. (a), (b), (d) and (e) pass."
severity: major
verified_by: automated (Playwright, API fault injection, row locks via SELECT ... FOR UPDATE then ROLLBACK, DB read-only)
evidence:
  - (a) pass, in-flight: held the system_controls row lock and clicked Trip. While the PUT was pending, Keep Current State and confirm were disabled, Escape was ignored, a forced click did not close the dialog, and nothing was committed. After release it committed run fb3bc3ed and the dialog closed
  - (a) pass, focus: on open, focus is in the reason textarea; 16 Tab/Shift+Tab presses stayed inside the dialog; on close, focus returns to the trigger (both kill-switch entry points, see Test 1)
  - (a) pass, outage: held the lock, clicked Trip, then kill -9'd the API mid-request. The dialog showed "Operation failed (HTTP 500). The change may not have been applied — reload to verify the current state, then try again; if it persists, check the API logs." (not "Request rejected") and the form stayed editable. The banner re-read and showed "Kill-switch state UNKNOWN — GET /api/v1/system/kill-switch failed (500)" with no trigger, while the dialog stayed mounted. A second click with the API fully down showed the same outage copy. DB unchanged, because the aborted transaction rolled back
  - (a) pass, banner resync after a transport failure: a Playwright route forwarded the reset PUT (upstream 200, committed) and then dropped the response. The dialog showed "/api/v1/controls/kill-switch is unreachable (network or proxy failure)" and the banner flipped to ARMED with no reload. After the API outage and restart, the banner's Refresh showed the CLI trip ("... (UAT-T4 break-glass with API stopped)")
  - (b) pass: set strategies.status=archived as a fixture write, then restored to active with the original updated_at. Enable from /controls and from /strategy both showed "Strategy trend_following_daily is archived and cannot be enabled or disabled." and the dialog stayed open. PUT enabled and PUT disabled both returned 409 strategy_archived. Zero operator_control runs and zero events were written
  - (c) literal scenario: run as a --once worker with each fault injected through an env override
    - invalid key "uat-invalid-key": Job a62ba406 SUCCEEDED; run 109b19fb PARTIAL with symbols_failed [SPY] (worker log shows 401); Job resources shows "market_data_ingestion_run: 109b19fb… partial"
    - unreachable (BASE_URL http://127.0.0.1:9): Job a9d3fec6 SUCCEEDED; run 4d73c649 PARTIAL with symbols_failed [QQQ]
  - (c) missing key (run-level failure, separate evidence): Job b120d584 FAILED (handler_error: PolygonAuthError); run 91727cac is FAILED, linked by job_id, with its error_message; Job resources shows "market_data_ingestion_run: 91727cac… failed". The CR-B-01 fix holds for run-level failures
  - (d) pass: served the real app on :8000 with every strategy-registry builder patched to raise. The fault was confirmed live: GET/PUT /controls/strategies/* returned 500 internal_error, and the /controls strategy section showed the failure. Reset (run 729c7186) and Trip (run af3d7f1e) through the console both succeeded, with +1 run and +1 event each
  - (e) pass:
    - 4 concurrent same-key POST /jobs/f530a8c0/retry (overlapping in flight) returned one 202 and three 200 Idempotency-Replayed, all job b8090498; exactly 1 row with retry_of_job_id=f530a8c0
    - 2 concurrent requests on 4a792bc2 returned 202 + 200 replay, same job 3bcf395d, 1 row
    - a different key afterwards returned 409 retry_exists. Both queued retries were cancelled as cleanup

### 6. Re-test gap 2 (fixed by 20-27): reconciliation, broker-order-sync and paper-session Jobs against live Alpaca paper
expected: All three Jobs complete with no 422 "maximum is 100" error. list_fills sends page_size=100 and follows page_token; list_orders uses limit=500 + before_order_id. Note: the paper account has 0 orders / 0 fills, so this proves the 422 is gone only; multi-page cursor behaviour is covered by mock-transport tests (review IN-03).
result: [pending]
source: 20-VERIFICATION.md human_verification (re-verification 2026-09-29)

### 7. Re-test gap 1 (fixed by 20-25): ingest-bars all-fail vs partial
expected: ingest-bars with an invalid Polygon key, and with Polygon unreachable, ends with run FAILED and Job FAILED (handler_error, IngestionAllSymbolsFailedError; run error_message "0 of N symbols succeeded; failed: ..." with exception class names only). A run with >=1 ok and >=1 failed symbol still ends run PARTIAL / Job SUCCEEDED. Retry of the FAILED Job returns 202.
result: [pending]
source: 20-VERIFICATION.md human_verification (re-verification 2026-09-29)

### 8. Re-test gap 3 (fixed by 20-26): real-browser re-open of every control, Cancel and Retry dialog
expected: Every re-opening shows a clean first frame (prompt body, empty reason, confirm disabled, no stale role=alert or "Already X" notice). After unchanged -> Close -> re-open, focus lands on the Reason field (ControlConfirmDialog); RetryJobDialog still focuses Close. Check in Chromium (where the original focus bug reproduced).
result: [pending]
source: 20-VERIFICATION.md human_verification (re-verification 2026-09-29)

### 9. Re-test gap 4 (fixed by 20-28): new OPERATOR_CONTROL rows are temporally consistent on the live DB
expected: After a few kill-switch / strategy control actions, every NEW OPERATOR_CONTROL StrategyRun has completed_at >= started_at (READ ONLY query). The 25 legacy inverted rows remain as-is pending the open decision on optional migration 0022 CHECK ... NOT VALID.
result: [pending]
source: 20-VERIFICATION.md human_verification (re-verification 2026-09-29)

## Environment notes

- Check `lsof -nP -iTCP:8000 -sTCP:LISTEN` first — a host uvicorn and the compose API can both bind :8000.
- Verified 2026-09-29, before the automated run: host `make dev` uvicorn owned 127.0.0.1:8000 (no compose API); the API and the UAT DB helper both hit Homebrew Postgres ::1:5432/trading_platform, already at `0021_phase20_operations_safety (head)`, so no upgrade was needed; the Alpaca endpoint is the paper endpoint.
- Restore: kill switch re-armed through the API (run c6a7503b), strategy active, zero queued/running Jobs, `make dev` relaunched, and the openapi Phase 20 routes, worker and console were re-probed. The audit rows written during UAT are kept (reasons are prefixed "UAT-").
- The persistent local Postgres must be upgraded to migration 0021 (`make migrate` / `scripts/migrate.py upgrade head`) before a live run; phase tests used throwaway DBs only.

## Summary

total: 9
passed: 4
issues: 1
pending: 4
skipped: 0
blocked: 0

## Gaps

- truth: "A failing ingest-bars Job (bad Polygon key or unreachable) shows its ingestion run as FAILED in Job resources"
  status: fix_applied
  fixed_by: 20-25 (awaiting live re-test 7)
  reason: "Automated UAT: with an invalid key or an unreachable Polygon, every symbol fails inside the per-symbol try, the run finalizes PARTIAL and the Job SUCCEEDS. Only run-level errors (a missing key) give FAILED."
  severity: major
  test: 5
  user_decision: "2026-09-29: zero symbols succeeded => run FAILED and Job FAILED; >=1 succeeded and >=1 failed => run PARTIAL, Job SUCCEEDED (unchanged); explicit amendment (new D-08a) rather than an undocumented change"
  root_cause: "services/ingestion.py _finish_run derives status as failed-if-error_message / partial-if-any-failed / succeeded, with no zero-succeeded branch. The per-symbol except swallows PolygonAuthError/PolygonClientError into failed_symbols without error_message, so the handler returns normally and the runner writes SUCCEEDED. The 'never reinterpret partial failures' rule is not in D-08/D-09; it exists only in the ingest_bars.py docstring, 20-14-SUMMARY and WR-A-02."
  artifacts:
    - path: src/trading_platform/services/ingestion.py
      issue: "status derivation has no zero-succeeded branch; no succeeded_count; all-fail sets no error_message"
    - path: src/trading_platform/services/data.py
      issue: "IngestionResult carries no run_status and no raise method"
    - path: src/trading_platform/jobs/handlers/ingest_bars.py
      issue: "returns normally on all-fail; docstring misattributes the rule to D-08/D-09"
  missing:
    - "pure _derive_run_status(succeeded_count, failed_count, run_error) -> succeeded|partial|failed in the service (invariant 2: the service decides, not the handler)"
    - "typed IngestionAllSymbolsFailedError in services/data.py; IngestionResult.run_status + raise_for_all_symbols_failed(); the handler calls it after the completion log and before the post-call cancel checkpoint; Job FAILED/handler_error (no new enum value)"
    - "all-fail run error_message: deterministic, exception class names only"
    - "amend 20-CONTEXT.md with D-08a plus a D-05 scope note; update the handler docstring, 20-SECURITY/VALIDATION/VERIFICATION/REVIEW-FIX lines; amendment notes on 20-14-SUMMARY and WR-A-02"
    - "tests: predicate truth table; service all-fail/empty-bars/partial; handler all-fail and cancel-during-all-fail; E2E all-fail (job failed, handler_error, resources[0].status failed, run.job_id==job.id) and 1ok+1fail (job succeeded, run partial)"
  debug_session: .planning/debug/ingest-bars-all-symbols-failed-not-failed.md
- truth: "reconciliation, broker-order-sync and paper-session Jobs complete against the live Alpaca paper broker and see the COMPLETE broker order and fill sets"
  status: fix_applied
  fixed_by: 20-27 (awaiting live re-test 6)
  reason: "Found during test 3 (whose literal criteria passed): Jobs dbf6a335, 302020fc and 45b09f0d (and the earlier 52468936) FAILED with AlpacaClientError 422 'tried to set the page size to 500, but the maximum is 100'"
  severity: major
  test: 3
  user_decision: "2026-09-29: fix in Phase 20; verify the documented limits; pagination must retrieve the complete set, with no truncation"
  root_cause: "alpaca.py list_fills sends page_size=500 to GET /v2/account/activities/FILL (documented max 100, confirmed live: 101 and 500 give 422). Neither list_fills nor list_orders paginates. list_orders(limit=500) is legal (max 500), but it is single-page and silently drops older orders beyond 500 (the server does not enforce limit). load_broker_state (reconciliation, paper-session) and sync_paper_state (broker-order-sync) need the complete sets: the matchers compare them against undated local history, so truncation gives false MISSING_BROKER blocking findings and silently missing fills. It predates Phase 20 (d5579f8, phase 10); all E2E tests fake AlpacaClient."
  artifacts:
    - path: src/trading_platform/services/alpaca.py
      issue: "page_size over the max; no pagination in list_fills or list_orders"
    - path: src/trading_platform/services/reconciliation/report.py
      issue: "consumer requiring the complete sets (no change expected)"
    - path: src/trading_platform/services/execution/sync_orders.py
      issue: "consumer requiring the complete sets (no change expected)"
  missing:
    - "Final constants: ALPACA_ACTIVITIES_MAX_PAGE_SIZE=100, ALPACA_ORDERS_MAX_LIMIT=500, and max-page caps"
    - "cursor pagination: page_token for fills (the last id, direction desc) and before_order_id for orders (never combined with after/until); terminate on a short or empty page"
    - "typed AlpacaPaginationCapExceededError and AlpacaPaginationStalledError (duplicate id or non-advancing cursor), both AlpacaClientError subclasses; never return a partial list"
    - "no date bounding (the local side is undated; bounding only the broker side would create false findings)"
    - "tests/test_alpaca_pagination.py with httpx.MockTransport: page_size<=100, limit<=500, multi-page concatenation, cursor==last id, termination on short/empty page, cap raises, stall raises, before_order_id never combined with after/until"
    - "annotate 20-VERIFICATION SC1 (the E2E evidence used a fake broker) and REQUIREMENTS OPS-03/04/06 traceability until the fix lands and UAT test 3 re-runs"
  debug_session: .planning/debug/alpaca-fills-page-size-422.md
- truth: "Every opening of a control or job confirmation dialog is clean on its first committed frame (prompt body, empty reason, confirm disabled, no stale alert), and focus goes to the reason field on every opening (WR-C-05)"
  status: fix_applied
  fixed_by: 20-26 (awaiting live re-test 8)
  reason: "Found during tests 2 and 5a (whose literal criteria passed): a re-opened dialog first renders the previous opening's state; after an unchanged notice, Close and re-open, focus stays on 'Keep Current State'"
  severity: minor
  test: 5
  root_cause: "Confirmed. ControlConfirmDialog stays mounted between openings and resets per-opening state with setState in a post-commit useEffect, so the first commit shows stale state, including stale role=alert nodes. useDialogFocus and the [open, unchanged] effect run in that same flush against the stale DOM, where the textarea is absent after an unchanged notice, and both focus the dismiss button; nothing re-focuses after the reset. CancelJobDialog and RetryJobDialog share the reset-in-effect pattern (stale first frame, no persistent focus bug). useLayoutEffect does not fix it (tested)."
  artifacts:
    - path: console/src/components/controls/ControlConfirmDialog.tsx
      issue: "reset-in-effect; the [open, unchanged] focus effect fires on the stale state"
    - path: console/src/components/jobs/CancelJobDialog.tsx
      issue: "same reset-in-effect pattern (stale first frame)"
    - path: console/src/components/jobs/RetryJobDialog.tsx
      issue: "same pattern (stale role=alert on re-open); idempotency key per opening"
  missing:
    - "split each dialog: a persistent outer part keeps openingRef/wasOpenRef (the WR-C-01 guard) and renders an inner body only while open; all per-opening state, useDialogFocus and the handlers live in the body; delete the reset effect. Do NOT key the outer dialog (that would regress WR-C-01)"
    - "job dialogs: idempotency key generated once per body mount (useState initializer), reused across attempts within an opening"
    - "tests using a useLayoutEffect FrameProbe (first commit): clean first frame after keep, error and changed:true re-open; no 'Already' text; focus on the textarea after unchanged, Close, re-open (with and without StrictMode); no stale alert; Cancel and Retry equivalents; key differs per opening; the existing WR-C-01 and WR-C-06 tests stay green"
    - "amend 20-UI-SPEC shared-dialog and Retry mechanics sections, and the WR-C-05 text: clean first frame, focus on reason on every opening"
  debug_session: .planning/debug/control-dialog-stale-state-on-reopen.md
- truth: "Every completed StrategyRun satisfies completed_at >= started_at (control audit rows are temporally consistent)"
  status: fix_applied
  fixed_by: 20-28 (awaiting live re-test 9)
  reason: "UAT observation promoted after diagnosis: 25 of 25 operator_control runs in the live DB have completed_at < started_at (as much as -93ms); every other run type has 0 violations"
  severity: minor
  test: 4
  root_cause: "operator_controls.py takes changed_at = datetime.now(UTC) in Python BEFORE the transaction opens (lines 293, 468) and writes it to completed_at, event_at, system_controls.last_changed_at and result_summary.changed_at. started_at is server_default now() = the Postgres transaction start, which is always later. There are two clocks and one ordering, and the skew grows with a remote DB. It predates Phase 20 (06-03/07-03), but Phase 20 made it the primary HTTP and break-glass control path. No duration consumer exists; the impact is audit integrity."
  artifacts:
    - path: src/trading_platform/services/operator_controls.py
      issue: "pre-transaction Python timestamp used as the completion time"
    - path: src/trading_platform/db/models/strategy_run.py
      issue: "started_at uses the DB transaction-start clock; there is no CHECK on the ordering"
  missing:
    - "read one DB clock inside the transaction after the row lock (clock_timestamp()) and use it for completed_at, event_at, last_changed_at and result_summary.changed_at"
    - "Postgres tests for trip/reset/enable/disable, changed and unchanged: completed_at >= started_at; event_at == completed_at; last_changed_at == completed_at on a change; a lock-wait variant"
    - "OPTIONAL (user decision): migration 0022 CHECK (completed_at IS NULL OR completed_at >= started_at) NOT VALID (enforces new rows; legacy audit rows left unrewritten)"
    - "add a one-line timestamp-source invariant to the 20-CONTEXT control-path decisions"
  debug_session: .planning/debug/operator-control-run-completed-before-started.md

## Observations (not gaps; for the user to triage)

- `kill-switch-trip` prints two structured JSON log lines on stdout before the report, so stdout is not pure JSON.
- (Promoted to gap 4 after diagnosis) control runs have completed_at < started_at.
- An ARCHIVED strategy is shown as DISABLED with an "Enable Strategy" trigger (GET maps anything non-active to disabled). The archived notice appears only after the attempt.
- The sync-market-sessions form defaults to_date to today (2026-09-29), while local market_sessions data ends 2026-03-13. Not exercised.
