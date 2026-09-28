---
status: partial
phase: 20-complete-operation-migration-safety-controls
source: [20-VERIFICATION.md]
started: 2026-09-28T23:15:00Z
updated: 2026-09-29T00:00:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Kill-switch trip/reset from /controls and the KillSwitchBanner with NO worker running
expected: Dialog requires a non-blank reason (<=500 chars) and typed RESET for reset; trip succeeds; banner flips to tripped; a second trip shows "Already tripped - no change (recorded)"; a new OPERATOR_CONTROL run + ExecutionEvent row exists each time
result: [pending]

### 2. Strategy enable/disable from /strategy and /controls on a live stack
expected: Disable then enable round-trips with a reason; status badge updates; unchanged notice on repeat
result: [pending]

### 3. Submit each of the 7 new Job types from its console form; Retry a failed Job; open a RUNNING paper-session Job
expected: Forms validate and submit; Retry creates a linked Job with lineage shown on both the original and the retry; paper-session shows queued-only cancel gating (no Cancel while RUNNING)
result: [pending]

### 4. Break-glass CLI `python -m trading_platform.worker kill-switch-trip --reason 'x'` with the API stopped
expected: Kill switch trips, JSON report printed, OPERATOR_CONTROL run written; no reset/enable/disable subcommand exists
result: [pending]

### 5. Code-review fixes that need a live stack (see 20-REVIEW-FIX.md)
expected: (a) Control dialogs: Escape/Keep Current State disabled while a PUT is in flight; focus moves into the dialog, Tab is trapped, focus returns on close; an API outage (stop the API mid-trip) shows the outage message, not "Request rejected"; banner resyncs after a transport failure. (b) Enable/disable on an archived strategy returns the archived notice. (c) A failing ingest-bars Job (bad Polygon key or unreachable) shows its ingestion run as FAILED in Job resources. (d) Kill-switch trip works even if the strategy registry fails to load. (e) Two concurrent same-key retries both return the same retry Job.
result: [pending]

## Environment notes

- Check `lsof -nP -iTCP:8000 -sTCP:LISTEN` first — a host uvicorn and the compose API can both bind :8000.
- The persistent local Postgres must be upgraded to migration 0021 (`make migrate` / `scripts/migrate.py upgrade head`) before a live run; phase tests used throwaway DBs only.

## Summary

total: 5
passed: 0
issues: 0
pending: 5
skipped: 0
blocked: 0

## Gaps
