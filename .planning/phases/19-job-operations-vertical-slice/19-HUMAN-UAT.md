---
status: partial
phase: 19-job-operations-vertical-slice
source: [19-VERIFICATION.md]
started: 2026-09-24T20:33:18Z
updated: 2026-09-24T20:33:18Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Live submit from the console (closes OPS-01)
Steps: `docker compose up -d` (worker runs `run-jobs`, mutations enabled in compose), open the console against the running API, go to `/jobs/new?type=backtest`, confirm from_date/to_date are pre-filled from catalog submission_defaults, submit.
expected: 202 navigates the browser to /jobs/{job_id}; the new Job appears in the /jobs list.
result: [pending]

### 2. Live Job detail through queued → running → succeeded (settles WR-02)
Steps: stay on /jobs/{id} without reloading.
expected: Progress updates, log tail appends, Events panel shows the terminal 'succeeded' event without reload, auto-refresh indicator shows 'Auto-refresh stopped — Job finished', Result summary shows run_id, strategy_run resource links to /runs/{id}, which shows a 'Created by Job …' back-link.
result: [pending]

### 3. Queued cancellation via the console
Steps: `docker compose stop worker`, submit a new backtest, click "Cancel Job…" and confirm, then restart the worker.
expected: Job lands CANCELLED with header 'Cancelled before start — never executed'; Result summary panel blank or empty-state (WR-01, cosmetic); after worker restart the Job stays CANCELLED and no strategy_run is created.
result: [pending]

### 4. Mutations-disabled posture
Steps: set TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=false, restart the API, open /jobs/new and a non-terminal Job's detail page.
expected: Submit and Cancel controls disabled with 'Mutations disabled on this deployment' visible; direct POST /api/v1/jobs returns 403 {"detail": {"code": "mutations_disabled"}}.
result: [pending]

## Summary

total: 4
passed: 0
issues: 0
pending: 4
skipped: 0
blocked: 0

## Gaps
