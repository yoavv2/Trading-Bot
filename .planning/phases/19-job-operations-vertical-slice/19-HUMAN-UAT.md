---
status: partial
phase: 19-job-operations-vertical-slice
source: [19-VERIFICATION.md]
started: 2026-09-24T20:33:18Z
updated: 2026-09-25T15:05:00Z
---

## Current Test

Test 3 — queued cancellation (Tests 1–2 passed after the dev-cache fix below)

## Tests

### 1. Live submit from the console (closes OPS-01)
Steps: `docker compose up -d` (worker runs `run-jobs`, mutations enabled in compose), open the console against the running API, go to `/jobs/new?type=backtest`, confirm from_date/to_date are pre-filled from catalog submission_defaults, submit.
expected: 202 navigates the browser to /jobs/{job_id}; the new Job appears in the /jobs list.
result: pass — run locally (host processes; the compose image has a separate config-packaging bug). Job 5b86f5f3… QUEUED → RUNNING → SUCCEEDED.

### 2. Live Job detail through queued → running → succeeded (settles WR-02)
Steps: stay on /jobs/{id} without reloading.
expected: Progress updates, log tail appends, Events panel shows the terminal 'succeeded' event without reload, auto-refresh indicator shows 'Auto-refresh stopped — Job finished', Result summary shows run_id, strategy_run resource links to /runs/{id}, which shows a 'Created by Job …' back-link.
result: pass — Job detail behaviour all as expected. Clicking the strategy_run link first looped (see Gaps G-01); resolved (environmental), then passed: /runs/2b260e0d… renders once with 'Created by Job 5b86f5f3…'.

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
passed: 2
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps

### G-01 — /runs/[runId] reload loop after following the strategy_run link (resolved, environmental)
status: resolved
found_in: Test 2
symptom: clicking the Job's strategy_run resource sent the dev server into a loop of repeated `GET /runs/{id}` 200s plus `FATAL: An unexpected Turbopack error occurred`, until the server was stopped.
root_cause: a corrupted persistent Turbopack dev cache in `console/.next/dev/cache` (dating from Jul 8). Every panic log reads `Failed to write app endpoint /runs/[runId]/page`, caused by `Next.js package not found`, raised in `Project::hmr_version_state`. The HMR update for that route cannot be computed, so the browser full-reloads, which requests the route again: a loop. The Turbopack panic is the loop mechanism; the cause is the cache state.
evidence: A/B/A in the real console dir with the same code: old cache → 21 panics and 176 repeat GETs in seconds; fresh cache → 1 GET, 0 panics, backlink renders; old cache restored → loop returns. An isolated copy (same source and node_modules, clean cache) never reproduced it. No router.refresh/replace/redirect or location reloads exist in the run-detail graph, and the page's useApiQuery endpoints are stable strings without polling.
fix: replaced the active `.next/dev` with a freshly built one (the old cache is kept at `console/.next/dev.poisoned-20260925` for reference; safe to delete). No code change was needed; the Created-by-Job backlink is unchanged.
regression: `console/src/app/runs/[runId]/page.test.tsx` renders the page for a Job-created run, asserts the backlink, and asserts exactly one run fetch with zero additional requests over 60s of fake time. Checked against a deliberate polling mutation: it fails with 31 fetches.
