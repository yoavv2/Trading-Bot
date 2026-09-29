---
status: complete
phase: 20-complete-operation-migration-safety-controls
source: [20-VERIFICATION.md]
started: 2026-09-28T23:15:00Z
updated: 2026-09-29T10:26:00Z
---

## Current Test

[testing complete]

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

## Environment notes

- Check `lsof -nP -iTCP:8000 -sTCP:LISTEN` first — a host uvicorn and the compose API can both bind :8000.
- Verified 2026-09-29, before the automated run: host `make dev` uvicorn owned 127.0.0.1:8000 (no compose API); the API and the UAT DB helper both hit Homebrew Postgres ::1:5432/trading_platform, already at `0021_phase20_operations_safety (head)`, so no upgrade was needed; the Alpaca endpoint is the paper endpoint.
- Restore: kill switch re-armed through the API (run c6a7503b), strategy active, zero queued/running Jobs, `make dev` relaunched, and the openapi Phase 20 routes, worker and console were re-probed. The audit rows written during UAT are kept (reasons are prefixed "UAT-").
- The persistent local Postgres must be upgraded to migration 0021 (`make migrate` / `scripts/migrate.py upgrade head`) before a live run; phase tests used throwaway DBs only.

## Summary

total: 5
passed: 4
issues: 1
pending: 0
skipped: 0
blocked: 0

## Gaps

- truth: "A failing ingest-bars Job (bad Polygon key or unreachable) shows its ingestion run as FAILED in Job resources"
  status: failed
  reason: "Automated UAT: with an invalid key or an unreachable Polygon, every symbol fails inside the per-symbol try in services/ingestion.py. The run finalizes as PARTIAL and the Job SUCCEEDS, so no failing Job exists to show FAILED. Only run-level errors, such as a missing key raising in PolygonClient.__init__, give a FAILED run and Job, and that path renders correctly."
  severity: major
  test: 5
  root_cause: "Known: WR-A-02 was skipped as a product decision (should ingest-bars FAIL when all symbols fail?). D-08/D-09 say the handler never reinterprets partial symbol failures. The user must decide."
  artifacts: [src/trading_platform/services/ingestion.py, src/trading_platform/jobs/handlers/ingest_bars.py]
  missing: []
  debug_session: ""
- truth: "reconciliation, broker-order-sync and paper-session Jobs can complete against the live Alpaca paper broker"
  status: failed
  reason: "Found during test 3; that test's literal criteria still pass. Jobs dbf6a335, 302020fc and 45b09f0d, and the earlier paper-session 52468936, all failed with 'AlpacaClientError: Alpaca request failed with status 422: tried to set the page size to 500, but the maximum is 100'."
  severity: major
  test: 3
  root_cause: "services/alpaca.py:279 list_fills(page_size=500) calls GET /v2/account/activities/FILL, whose page_size is capped at 100; the error text names page_size. list_orders(limit=500) on /v2/orders uses a different parameter; Alpaca documents its max as 500, so leave it. This predates Phase 20; the E2E tests use a fake broker."
  artifacts: [src/trading_platform/services/alpaca.py]
  missing: []
  debug_session: ""
- truth: "A re-opened control confirmation dialog starts clean: prompt body, empty reason, focus on the reason field (WR-C-05)"
  status: failed
  reason: "Found during tests 2 and 5a; the literal criteria still pass. On re-open the first rendered frame shows the previous opening's state, such as the old reason with confirm enabled or 'Already ENABLED — no change (recorded)', until the reset effect runs. After an unchanged notice -> Close -> re-open, focus lands on 'Keep Current State' instead of the reason field, and it stays there."
  severity: minor
  test: 5
  root_cause: "Likely: ControlConfirmDialog resets its state in a useEffect after the render, so the first commit renders stale state. useDialogFocus runs in that same commit, while the reason textarea is not rendered because unchanged=true, so it falls back to the dismiss button."
  artifacts: [console/src/components/controls/ControlConfirmDialog.tsx, console/src/lib/useDialogFocus.ts]
  missing: []
  debug_session: ""

## Observations (not gaps; for the user to triage)

- `kill-switch-trip` prints two structured JSON log lines on stdout before the report, so stdout is not pure JSON.
- Control-run reports have completed_at earlier than started_at by about 25ms. completed_at uses Python time taken at method entry; started_at uses the DB default at insert.
- An ARCHIVED strategy is shown as DISABLED with an "Enable Strategy" trigger (GET maps anything non-active to disabled). The archived notice appears only after the attempt.
- The sync-market-sessions form defaults to_date to today (2026-09-29), while local market_sessions data ends 2026-03-13. Not exercised.
