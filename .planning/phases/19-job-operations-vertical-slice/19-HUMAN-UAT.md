---
status: complete
phase: 19-job-operations-vertical-slice
source: [19-VERIFICATION.md]
started: 2026-09-24T20:33:18Z
updated: 2026-09-26T10:30:00Z
---

## Current Test

[testing complete — 4/4 passed; G-01..G-03 resolved]

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
result: pass — human UAT, Job f530a8c0-9965-437e-b57c-754f39ee1ada. Submitted via the console and stayed QUEUED; Cancel Job… was confirmed → CANCELLED with 'Cancelled before start — never executed'. Started empty, no linked resources, empty result summary, no logs, and still CANCELLED with nothing executed after the worker restart. DB record (read-only check): status=cancelled, cancellation_cause=operator_request, started_at=NULL, result_summary={}, 0 strategy_runs, 0 job_logs.
note: the console proxy reached the host API on 127.0.0.1:8000 (same environment as G-03), so this job lives in the host/Homebrew DB. The compose `stop worker`/`start worker` therefore didn't touch its DB, and no host worker was running. The "worker restart leaves it CANCELLED" leg rests on the automated E2E, tests/test_job_operations_e2e.py (SC7 queued cancellation runs the worker after cancel and asserts the Job stays CANCELLED with no strategy_run).

### 4. Mutations-disabled posture
Steps: set TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=false, restart the API, open /jobs/new and a non-terminal Job's detail page.
expected: Submit and Cancel controls disabled with 'Mutations disabled on this deployment' visible; direct POST /api/v1/jobs returns 403 {"detail": {"code": "mutations_disabled"}}.
result: pass — verified against the compose API (MUTATIONS_ENABLED=false), after G-03 showed the first attempt hit a different API. /jobs/new?type=backtest: Submit Backtest disabled=true (opacity 0.5, not-allowed cursor), "Mutations disabled on this deployment" visible. QUEUED Job 42d7a874…: Cancel Job… disabled=true with the same reason. Direct POST /api/v1/jobs → 403 {"detail":{"code":"mutations_disabled"}}; direct POST /api/v1/jobs/{id}/cancel → 403 mutations_disabled, job stayed queued. The fixture job was created via JobOrchestrationService in-container with the compose worker stopped, then cancelled; the worker was restarted.

## Summary

total: 4
passed: 4
issues: 0
pending: 0
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

### G-02 — Docker Compose api/worker crash-loop on startup: config path (resolved)
status: resolved
found_in: pre-Test 3 setup (Test 3 not started; not a Test 3 failure)
symptom: `docker compose up -d db api` → db healthy, api restart loop with `FileNotFoundError: Configuration file not found: /usr/local/lib/python3.13/config/app.yaml` (lifespan → enforce_startup_config → build_settings_payload → _resolve_config_locations). The worker fails the same way.
root_cause: `settings.py` derives `PROJECT_ROOT = Path(__file__).resolve().parents[3]`, which is the repo root in a checkout. The Dockerfile does a non-editable `pip install .`, so the imported module lives in site-packages and `parents[3]` = `/usr/local/lib/python3.13`. `config/` is copied to `/app/config`, but nothing pointed there. Render worked only because `render.yaml` sets TRADING_PLATFORM_CONFIG_FILE / TRADING_PLATFORM_STRATEGY_CONFIG_DIR explicitly; compose never did. Pre-existing (the compose api was already crash-looping before Phase 19), but Phase 19 (ORCH-05) makes the compose worker the production Job path.
fix: commit 066c365 (from the parallel fix session's 9cfedbb). The Dockerfile ENV pins both variables to /app/config/... for every image entrypoint; compose api/worker set them too; a one-shot `migrate` service (alembic upgrade head) gates api/worker, because their `command` overrides skip the image CMD's migration. The db host port is overridable via POSTGRES_HOST_PORT. Settings resolution is unchanged.
verification: `docker compose down` → rebuild → `docker compose up -d db api` → migrate Exited (0) after 0018→0020, db healthy, api Up with 0 restarts, /health 200, /ready 200 (configuration check ok); `up -d worker` → Up, 0 restarts, `run-jobs` process alive; in-container resolution returns /app/config/app.yaml and /app/config/strategies. (The Docker VM disk was 100% full and first blocked db; cleared by pruning 15 GB of build cache, with the user's approval.)
regression: tests/test_deploy_config.py adds 6 contract tests (Dockerfile ENV pins, config COPY into WORKDIR, compose api/worker env, render.yaml parity, pinned paths exist in repo, migrate gates api/worker). Against the pre-fix Dockerfile/compose, 3 fail. Full suite: 595 passed.
why_missed: no test built or ran the image. test_deploy_config.py only parsed compose/render YAML for commands. Phase 19 UAT ran host processes, where `parents[3]` is the repo root. Render sets the variables explicitly.
note: the compose DB has no seeded strategy row or bars (seed/ingest scripts are not in the image). That's enough for Test 3 (queued cancel); a successful backtest under compose would need seeding.

### G-03 — Test 4 first attempt showed Submit enabled with MUTATIONS_ENABLED=false (resolved: test setup, no defect)
status: resolved
found_in: Test 4
symptom: after `docker compose up -d --force-recreate api` with MUTATIONS_ENABLED=false (confirmed via `docker compose exec api env`), /jobs/new?type=backtest still showed Submit Backtest enabled and no "Mutations disabled on this deployment".
root_cause: two APIs were listening on port 8000. A host `uvicorn` process (started 2026-09-26 12:04 local, mutations enabled, Homebrew DB) held 127.0.0.1:8000, and the compose API was published on *:8000 (IPv6). The console proxy target `TRADING_CONSOLE_API_BASE_URL=http://127.0.0.1:8000` and host curl requests went to the host process, which correctly reported `mutations_enabled: true`. Not a console or API bug: the console reads `/api/v1/job-types` live on every mount (`cache: "no-store"`, no build-time state), so a hard reload could not change the result.
evidence: the same moment, three views: via 127.0.0.1:8000 (host API) → mutations_enabled true, and POST accepted 202; inside the container → mutations_enabled false and POST 403 with 0 rows; console pointed at the container → correct disabled posture for both Submit and Cancel.
side_effect: the 202 probe created job 4a792bc2… in the Homebrew DB via the host API; it was cancelled immediately (never ran).
fix: none in code. Setup rule for compose-based UAT: stop any host `uvicorn`/API on :8000 before testing the compose stack (check with `lsof -nP -iTCP:8000 -sTCP:LISTEN`).
regression: existing component tests already pin the posture (JobHeaderPanel "disables the Cancel Job… trigger with the D-21 reason when mutations are disabled"; NewJobView/StrategyOverviewPanel disabled-reason tests; tests/test_mutation_guard.py for the 403). No new test, since there's no code defect to pin.
