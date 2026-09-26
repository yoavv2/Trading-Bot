---
phase: 19-job-operations-vertical-slice
verified: 2026-09-24T19:09:43Z
status: passed
score: 9/9 roadmap success criteria verified; human UAT 4/4 passed (19-HUMAN-UAT.md), which closes OPS-01's console-inclusive claim
overrides_applied: 0
human_verification:
  - test: "Live submit: docker compose up -d (worker command run-jobs, mutations_enabled=true per compose), open console against the running API, navigate to /jobs/new?type=backtest, confirm from_date/to_date are pre-filled from catalog submission_defaults, submit the form."
    expected: "202 response navigates the browser to /jobs/{job_id}; the new Job appears in /jobs list."
    why_human: "No automated test drives a real browser/console dev server against a real running API + worker. Backend E2E (tests/test_job_operations_e2e.py) calls the HTTP API directly, bypassing the actual UI; console component tests mock fetchApi. OPS-01's literal text is 'Operator can run a backtest from the UI' — the UI-to-live-backend leg is unverified by any automated test."
  - test: "Watch the submitted Job's detail page (/jobs/{id}) through queued -> running -> succeeded without manual reload."
    expected: "Progress panel updates, log tail appends new lines, Events panel shows the terminal ('succeeded') event without needing a page reload, the auto-refresh indicator changes to 'Auto-refresh stopped — Job finished', Result summary panel shows run_id, and the linked strategy_run resource links to /runs/{id} (which shows a 'Created by Job ...' back-link)."
    why_human: "Confirms in a live setting that WR-02 (JobEventsPanel in-flight-tick race) does not manifest on a typical run; the race is timing-dependent and was not reliably reproducible/observable via static analysis alone."
  - test: "Queued cancellation: docker compose stop worker, submit a new backtest, then Cancel Job... from the console before restarting the worker."
    expected: "Job lands CANCELLED with header copy 'Cancelled before start — never executed'; Result summary panel is either blank or shows empty-state copy (informational only — see WR-01 below, non-blocking); restarting the worker afterward leaves the Job CANCELLED with no strategy_run created."
    why_human: "Exercises the JOBUI-04 cancellation confirmation dialog and honest-outcome labeling end-to-end through the real console UI; also visually confirms the WR-01 blank-panel cosmetic gap in context."
  - test: "With TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=false (restart API), open /jobs/new and a Job detail page for a non-terminal Job."
    expected: "Submit and Cancel controls render disabled with the capability reason text ('Mutations disabled on this deployment') visible; a direct POST to /api/v1/jobs returns 403 {\"detail\": {\"code\": \"mutations_disabled\"}}."
    why_human: "Confirms ORCH-07's console-facing gating (useMutationCapability) renders correctly against a live disabled deployment, matching render.yaml's production posture."
---

# Phase 19: Job Operations Vertical Slice — Verification Report

**Phase Goal:** A backtest submitted from the console travels the full production path — `POST /api/v1/jobs` → `JobOrchestrationService` → registered `backtest` handler → production worker (`run-jobs`) → existing backtest service — and its progress, logs, events, result, failure state, and cancellation are observable in generic, job-type-agnostic Job UI.

**Verified:** 2026-09-24T19:09:43Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (ROADMAP.md Phase 19 Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | E2E test using production registry proves submit → worker → SUCCEEDED, progress/logs/events resolve via reference links, `resources[]` contains the linked `strategy_run` (FK + `result_summary.run_id` match) (OPS-01) | ✓ VERIFIED (automated, backend-only) | `tests/test_job_operations_e2e.py::test_backtest_job_runs_through_production_path` uses `create_app()` with **no** registry override (production `build_default_registry`), asserts `status=='succeeded'`, `progress.percent==100`, self/progress/logs/events links all 200, log codes `backtest_run_started`/`backtest_run_completed`, event_types `submitted`/`succeeded`, `resources==[{kind:'strategy_run', id:R, status:'succeeded', links:{self:'/api/v1/runs/'+R}}]`, `result_summary.run_id==R`. Included in the 589-test passing suite run this session. **Does not** exercise the actual console UI — see human_verification #1. |
| 2 | Resubmitting with the same `Idempotency-Key` returns the same `job_id`; exactly one backtest run exists (OPS-01, ORCH-03 regression) | ✓ VERIFIED | `test_idempotent_resubmission_creates_one_run` asserts `Idempotency-Replayed: true`, same `job_id`, and exactly one `strategy_runs` row / one run in total after a second worker pass. |
| 3 | A test parsing `docker-compose.yml` asserts the worker service command is `run-jobs`; no deploy config starts `serve` (ORCH-05) | ✓ VERIFIED | `tests/test_deploy_config.py` (part of the 589-pass suite) plus direct read: `docker-compose.yml:48` → `command: ["python", "-m", "trading_platform.worker", "run-jobs"]`. `Dockerfile` CMD is `alembic upgrade head && exec uvicorn ...` — no `serve` reference anywhere in `docker-compose.yml`/`render.yaml`/`Dockerfile`. |
| 4 | `GET /api/v1/job-types` lists exactly the registered types with description + cancellation mode; enforcement test asserts every registered type appears (ORCH-06) | ✓ VERIFIED | `src/trading_platform/api/routes/job_types.py` implements the route; `tests/test_job_catalog.py` (≥100 lines, in passing suite) asserts `set(item.job_type) == set(build_default_registry().list_job_types())`. `JobCancellationMode` is a closed `StrEnum` with member set `{step_boundary}` (`src/trading_platform/jobs/registry.py:19`). |
| 5 | With mutations disabled, every mutating route returns typed 403 and writes zero rows; `render.yaml` sets it disabled (ORCH-07) | ✓ VERIFIED | `tests/test_mutation_guard.py` (route-walk + zero-row + guard-ordering tests, in passing suite). `render.yaml:66-67` sets `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED: "false"` explicitly with an inline rationale comment. |
| 6 | Console enforcement: no `fetch(` outside `api.ts`; Job list/detail/log/event components carry no job-type-specific branches; a component test renders a test-only Job type through list+detail with zero UI changes (JOBUI-01..03) | ✓ VERIFIED | `grep -rn "fetch(" console/src --include=*.ts --include=*.tsx` (excluding tests) returns only `refetch()` call sites and `api.ts` itself — zero violations. `console/src/lib/consoleBoundaries.test.ts` (in the 129-test passing suite) enforces the fetch-boundary, `JOB_TYPE_FORMS`/`RESOURCE_ROUTES` single-declaration, and `dangerouslySetInnerHTML` absence. `JobDetailView.test.tsx` contains the SC6 test-only-`job_type`/`unknown_kind`-resource render test. |
| 7 | Cancelling queued backtest lands CANCELLED and never executes; cancelling running backtest lands CANCELLED at next step boundary or FAILED/cancellation_timeout, UI labels the actual outcome (JOBUI-04) | ✓ VERIFIED | `test_cancel_queued_backtest_never_executes` and `test_cancel_running_backtest_acknowledged_after_service` (both in passing suite) prove both backend outcomes. `console/src/lib/cancellationLabel.ts:70` handles the `status==='failed' && failure_reason==='cancellation_timeout'` case; `cancellationLabel.test.ts` has 4 dedicated `cancellation_timeout` test cases (lines 109-148+), all passing. |
| 8 | Job list and detail refresh automatically while non-terminal, stop polling at terminal state (JOBUI-05) | ✓ VERIFIED | `console/src/lib/useApiQuery.ts` implements the shared poll-chain (`scheduleNextTick` re-evaluates `shouldPoll()` after every resolved fetch and stops arming the next timer once it returns false); `JobsTable.tsx` (`pollIntervalMs: 5000`) and `JobDetailView.tsx` (`pollIntervalMs: 3000`, `shouldPoll: (data) => !isTerminalJobStatus(data.status)`) both wire it correctly. Covered by `JobsTable.test.tsx`/`JobDetailView.test.tsx` polling-stop assertions, all passing. |
| 9 | Phase 18 registry tripwires are replaced by a test pinning the exact registered Job-type set | ✓ VERIFIED | `grep -rn` for `test_default_registry_remains_empty_until_phase_19`, `_PHASE19_OPERATION_TYPES`, `test_phase18_diff_excludes_console_and_phase19_handler_registrations`, `test_build_default_registry_is_empty_in_phase_17` across `tests/` and `src/` returns **zero matches** — all four tripwires are gone. Replacement pinning assertions found at `tests/test_backtest_job_type.py:201`, `tests/test_job_registry.py:91`, `tests/test_orchestration_boundaries.py:246`: `registry.list_job_types() == ["backtest"]`. |

**Score:** 9/9 roadmap success criteria VERIFIED by automated tests and direct code/config inspection. OPS-01's own broader wording ("Operator can run a backtest **from the UI**") is only partially covered by SC1's automated E2E (which proves the HTTP→worker→service leg with the production registry, but does not drive a browser against a live console+API+worker) — this residual gap is listed under human_verification, matching the executors' own decision to leave `OPS-01` `Pending` in REQUIREMENTS.md rather than mark it Complete.

### Required Artifacts (spot sample across the 12 plans)

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `alembic/versions/0020_phase19_job_operations.py` | job_id FK+UNIQUE, config_invalid enum | ✓ VERIFIED | Matches must_haves exactly: `ADD VALUE IF NOT EXISTS 'config_invalid'`, FK `fk_strategy_runs_job_id_jobs` ON DELETE SET NULL, UNIQUE `uq_strategy_runs_job_id`; downgrade documented as intentional enum no-op (see WR-04, non-blocking). |
| `src/trading_platform/api/routes/job_types.py` | Read-only job-type catalog route | ✓ VERIFIED | `APIRouter(prefix="/api/v1/job-types")`, resilient `submission_defaults()` failure handling, catalog keys limited to job_type/description/cancellation_mode/submission_defaults. |
| `docker-compose.yml` worker service | `run-jobs` command | ✓ VERIFIED | Confirmed by direct read. |
| `render.yaml` | mutations disabled | ✓ VERIFIED | Confirmed by direct read, explicit `"false"` with rationale comment. |
| `console/src/lib/api.ts` | submitJob/cancelJob/mutationErrorMessage, sole fetch() site | ✓ VERIFIED / WIRED | grep confirms sole non-test `fetch(` site; consoleBoundaries.test.ts passing. |
| `console/src/components/jobs/detail/JobDetailView.tsx` | Generic composition, polling | ✓ VERIFIED / WIRED | Composes Header/Progress/Resources/ResultSummary/Logs/Events panels off a single polled `GET /api/v1/jobs/{id}`; zero job_type conditionals observed. |
| `console/src/components/jobs/detail/JobResultSummaryPanel.tsx` | Generic result_summary rendering with honest empty state | ⚠️ VERIFIED but STUB-EMPTY-STATE (WR-01) | Renders real data correctly for SUCCEEDED Jobs (`resultSummary.run_id` etc. shown generically). Empty-state branch (`resultSummary === null`) is unreachable in production because the backend's `Job.result_summary` column is `nullable=False, default=dict` and is only ever non-empty after a SUCCEEDED transition (`src/trading_platform/db/models/job.py:127`, `src/trading_platform/jobs/lifecycle.py:224-225`) — confirmed directly against test evidence: `tests/test_job_operations_e2e.py:311,379` assert `detail["result_summary"] == {}` for cancelled Jobs, not `null`. For QUEUED/RUNNING/FAILED/CANCELLED Jobs the panel therefore renders an empty, unlabeled `<dl>` instead of the intended empty-state copy. **Classified non-blocking (see Anti-Patterns / WR-01 discussion)** — no false data is shown, and failure/cancellation state remains fully observable via `JobHeaderPanel` (failure_reason, failure_message, outcome_uncertain, cancellation fields, `cancellationOutcomeLabel`) independent of this panel. |
| `console/src/components/jobs/detail/JobEventsPanel.tsx` | Generic lifecycle events list | ⚠️ VERIFIED but narrow polling race (WR-02) | Renders event_type/from_status/to_status/outcome/event_at/requested_by/reason generically; "No events recorded yet." empty state. Traced `useApiQuery.ts`'s poll scheduler directly: `scheduleNextTick()` re-evaluates `shouldPoll()` only when a fetch's `.then()` resolves (not at timer-fire time — the armed `setTimeout` callback calls `tickRef.current()` unconditionally). This means an **already-armed** timer still fires and fetches even after the Job goes terminal, and normally does pick up the terminal event. The narrower race the code review (WR-02) describes only occurs when a tick's `fetchApi` call is *in flight* at the exact moment the Job transitions to terminal — that in-flight response (issued before the terminal write) resolves without the terminal event, and the subsequent `scheduleNextTick()` now sees `shouldPoll() === false` and stops the chain permanently, with no manual-refresh control on this panel to recover short of a full page reload. **Classified non-blocking** — this is a timing-window race, not a systematic failure; a full reload recovers the missing event; see human_verification #2 to confirm typical-case behavior live. |

### Key Link Verification (spot sample)

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `console/src/components/jobs/new/BacktestJobForm.tsx` | `submitJob` (`api.ts`) | `NewJobView.tsx` dispatch via `JOB_TYPE_FORMS` map | ✓ WIRED | `console/src/lib/jobTypeForms.ts` exports `JOB_TYPE_FORMS = {backtest: BacktestJobForm}`, imported only by `NewJobView.tsx` (enforced by `consoleBoundaries.test.ts`). |
| `console/src/components/jobs/detail/JobHeaderPanel.tsx` | `CancelJobDialog` → `cancelJob` (`api.ts`) | `onCancelled` → `onChanged` → `refetch()` | ✓ WIRED | Cancel trigger gated by `useMutationCapability`; dialog only renders for `queued`/`running`; successful cancel calls `onChanged()` which is `JobDetailView`'s `refetch`. |
| `src/trading_platform/jobs/handlers/backtest.py` | `src/trading_platform/services/backtesting.py::run_backtest` | `job_id=context.job_id`, `trigger_source='job'` | ✓ WIRED | Confirmed by `tests/test_backtest_job_type.py` and the E2E test's `job_id`/`trigger_source` assertions. |
| `src/trading_platform/worker/commands/run_jobs.py` | Production registry / preflight | `build_default_registry`, `required_mode_preflight` | ✓ WIRED | `test_job_runner_preflight.py`, `test_deploy_config.py` confirm BACKTEST-mode boot without broker credentials and `config_invalid` failure path. |

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|---|---|---|---|---|
| OPS-01 | 19-01,03,06,07,11 | Operator can run a backtest from the UI, proven end-to-end Console → HTTP → Job → worker → backtest service | ? NEEDS HUMAN (backend leg SATISFIED) | Backend chain proven by `test_job_operations_e2e.py` with production registry. Console-to-live-backend leg has no automated coverage (component tests mock `fetchApi`). REQUIREMENTS.md correctly still shows this `Pending` — this verifier agrees that is the correct classification, not an error. |
| ORCH-05 | 19-05 | Production worker command is `run-jobs`; no `serve` in deploy config | ✓ SATISFIED | `test_deploy_config.py` + direct read of `docker-compose.yml`/`render.yaml`/`Dockerfile`. |
| ORCH-06 | 19-04 | Read-only job-type catalog listing every registered type | ✓ SATISFIED | `test_job_catalog.py`, `job_types.py`, `registry.py`. |
| ORCH-07 | 19-02, 19-05 | Mutation flag disables mutating routes with typed 403, zero rows; render.yaml disabled | ✓ SATISFIED | `test_mutation_guard.py`, `render.yaml`. |
| JOBUI-01 | 19-09 | Job-type-agnostic list, filtered by status/type | ✓ SATISFIED | `JobsTable.tsx`/`JobFilters.tsx`, `JobsTable.test.tsx`, `consoleBoundaries.test.ts`. |
| JOBUI-02 | 19-01,03,12 | Generic Job detail: status/progress/failure/cancellation/dependencies/resources/result_summary | ✓ SATISFIED (with non-blocking WR-01 empty-state cosmetic gap) | `JobDetailView.tsx`, `JobHeaderPanel.tsx`, `JobResourcesPanel.tsx`, `JobResultSummaryPanel.tsx`, and their tests. |
| JOBUI-03 | 19-10 | Structured logs (cursor tail) and lifecycle events | ✓ SATISFIED (with non-blocking WR-02 narrow polling race) | `JobLogsPanel.tsx` (has its own terminal-edge final-fetch handling, tested), `JobEventsPanel.tsx` (lacks the same defense but the timer-fire behavior traced above means the terminal event is picked up in the overwhelmingly common case). |
| JOBUI-04 | 19-07,10,12 | Cancel non-terminal Job behind confirmation; honest outcome labeling | ✓ SATISFIED | `CancelJobDialog.tsx`, `cancellationLabel.ts` (confirmed `cancellation_timeout` row present and tested), backend cancel-outcome E2E tests. |
| JOBUI-05 | 19-08,09,12 | List/detail auto-refresh while non-terminal, stop at terminal | ✓ SATISFIED | `useApiQuery.ts`, `AutoRefreshIndicator.tsx`, polling-stop tests in `JobsTable.test.tsx`/`JobDetailView.test.tsx`. |

**Orphaned requirements:** None. All 9 phase-declared requirement IDs (OPS-01, ORCH-05, ORCH-06, ORCH-07, JOBUI-01..05) appear in REQUIREMENTS.md's Phase 19 mapping table and are covered by at least one plan's `requirements:` frontmatter.

**REQUIREMENTS.md entries this verifier believes are mis-marked:** None. `OPS-01` is correctly `Pending` (agrees with executors' own assessment). `ORCH-05..07` and `JOBUI-01..05` are correctly `Complete` — WR-01 and WR-02 are real but non-blocking defects within already-Complete requirements (JOBUI-02 and JOBUI-03 respectively), not requirement-level failures; see justification below.

### Anti-Patterns Found (from 19-REVIEW.md, independently re-verified)

| File | Line | Pattern | Severity | Impact |
|---|---|---|---|---|
| `console/src/components/jobs/detail/JobResultSummaryPanel.tsx` | 41-42 | `resultSummary === null` check never true against real API responses (`{}` default) | ⚠️ WARNING (non-blocking) | Empty/non-succeeded Jobs show a blank `<dl>` instead of intended empty-state copy. Does not hide or falsify data — failure/cancellation state remains observable elsewhere (JobHeaderPanel). Fix is a one-line `Object.keys(...).length === 0` check per REVIEW.md. |
| `console/src/components/jobs/detail/JobEventsPanel.tsx` | 21-26 | No terminal-edge final fetch or manual-refresh control, unlike `JobLogsPanel` | ⚠️ WARNING (non-blocking) | A narrow in-flight-tick race can permanently stop polling one fetch short of the terminal event, recoverable only by full page reload. Traced the scheduler directly (`useApiQuery.ts`): the already-armed timer, in the common case, still fires and does pick up the terminal event — this is a timing-window edge case, not systematic. |
| `console/src/components/jobs/new/BacktestJobForm.tsx` | 52-76 | Failed `/api/v1/strategies` fetch silently collapses to empty `<select>` | ℹ️ INFO / WARNING (non-blocking, cosmetic) | Inconsistent with the codebase's "honest unknown" pattern used elsewhere; does not block goal achievement. |
| `alembic/versions/0020_phase19_job_operations.py` | 40-51 | Downgrade cannot remove `config_invalid` enum value; docstring understates the read-crash hazard on a coordinated rollback | ℹ️ INFO (non-blocking) | Operational documentation gap only; no current-state defect. |
| `console/src/lib/useApiQuery.ts` | 141-159 | `JobsTable`'s filter-change does not clear stale `result` before the new filtered response lands | ⚠️ WARNING (non-blocking) | Transient stale-row display during a filter change; does not affect Job detail (confirmed remounts on navigation per this Next.js version's routing cache-key behavior, verified in REVIEW.md against `node_modules/next`). |

No `TBD`/`FIXME`/`XXX` debt markers found in any Phase 19-modified file (backend or console). No `TODO`/`HACK`/`PLACEHOLDER` markers found either.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Full backend test suite | `PYTHONPATH=src .venv/bin/pytest -q` | `589 passed, 1 warning in 99.68s` | ✓ PASS (matches expected 589) |
| Console test suite | `cd console && npx vitest run` | `Test Files 16 passed (16); Tests 129 passed (129)` | ✓ PASS (matches expected 129) |
| Console type-check | `npx tsc --noEmit` | Clean, no output | ✓ PASS |
| Console lint | `npm run lint` | Clean, no output | ✓ PASS |
| `docker-compose.yml` worker command | direct read | `["python", "-m", "trading_platform.worker", "run-jobs"]` | ✓ PASS |
| `render.yaml` mutations flag | direct read | `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED: "false"` | ✓ PASS |
| No stray `fetch(` outside `api.ts` | `grep -rn "fetch(" console/src` (excl. tests) | Only `refetch()` call sites and `api.ts` itself | ✓ PASS |
| Old Phase 18 registry tripwires removed | `grep -rn` across `tests/`, `src/` | Zero matches | ✓ PASS |
| SC9 registry pinning test present | `grep -rn 'list_job_types() =='` | `tests/test_backtest_job_type.py:201`, `tests/test_job_registry.py:91`, `tests/test_orchestration_boundaries.py:246` | ✓ PASS |

### Probe Execution

No `scripts/*/tests/probe-*.sh` probes declared or found for this phase. Skipped — this phase relies on `pytest`/`vitest` suites and the E2E test module above, all executed directly.

### Human Verification Required

See YAML frontmatter `human_verification` for the full list (4 items). Summary:

1. **Live console → real API → worker submission of a backtest from `/jobs/new?type=backtest`** — closes OPS-01's literal "from the UI" clause, which no automated test spans.
2. **Live observation of a Job through queued → running → succeeded**, specifically confirming the Events panel shows the terminal event without a reload (settles WR-02 in the common case) and the Result summary/resources render correctly.
3. **Live queued-cancellation walkthrough** — exercises JOBUI-04's confirmation dialog end-to-end and visually confirms the WR-01 blank-panel behavior in context (non-blocking either way).
4. **Live mutations-disabled walkthrough** — confirms ORCH-07's console-facing capability gating against a real disabled deployment.

### Gaps Summary

No must-have truth FAILED. All 9 ROADMAP.md Phase 19 success criteria are backed by passing automated tests and/or direct, independently-verified code/config inspection (589/589 backend tests, 129/129 console tests, clean `tsc`/`lint`). The two code-review Warnings re-verified here (WR-01: `JobResultSummaryPanel`'s unreachable null-check; WR-02: `JobEventsPanel`'s narrow terminal-edge polling race) are confirmed real but judged non-blocking: neither displays false information, and failure/cancellation state remains independently observable via `JobHeaderPanel` regardless of either defect. `df60600` (post-19-12 test deflake of `JobHeaderPanel` cancel-trigger tests) is informational — it fixed a real intermittent-failure/vacuous-pass pair and is included in the current 129-test passing console suite.

The phase is withheld from a clean `passed` only because `OPS-01`'s own requirement text ("Operator can run a backtest **from the UI**") is broader than what any automated test proves: the backend vertical slice (HTTP → JobOrchestrationService → handler → `run-jobs` worker → backtest service) is proven end-to-end with the production registry, but no automated test drives an actual browser/console instance against a live API + worker. This matches the executors' own judgment (OPS-01 left `Pending` in REQUIREMENTS.md with the same rationale) — this verifier concurs with that classification rather than overriding it in either direction.

---

_Verified: 2026-09-24T19:09:43Z_
_Verifier: Claude (gsd-verifier)_

## Human Verification Resolution (2026-09-26)

Human UAT is complete in `19-HUMAN-UAT.md`: **4/4 passed**, and gaps G-01..G-03 are resolved. Status moved from `human_needed` to `passed`.

| Test | Result | Evidence |
|------|--------|----------|
| 1. Live console submit | PASS | Job 5b86f5f3… submitted from /jobs/new?type=backtest → QUEUED → RUNNING → SUCCEEDED via `run-jobs` |
| 2. Live Job detail / terminal refresh | PASS | Progress, logs, and events (incl. `succeeded`) updated without reload; auto-refresh stopped at terminal; result_summary has run_id; strategy_run 2b260e0d… links to /runs/{id} with the "Created by Job" back-link (after G-01) |
| 3. Queued cancellation | PASS | Job f530a8c0…: 'Cancelled before start — never executed'; started_at NULL, 0 runs, 0 logs, result_summary {} |
| 4. Mutations-disabled posture | PASS | Against the compose API: Submit and Cancel disabled with the reason; POST submit/cancel → 403 `mutations_disabled`, zero writes |

Gaps found during UAT (details in 19-HUMAN-UAT.md):
- **G-01** /runs/[runId] reload loop: a corrupted persistent Turbopack dev cache (environmental), proven by A/B/A cache swap. Page-level regression test added (80ad973).
- **G-02** compose api/worker config-path crash: `PROJECT_ROOT` resolved into site-packages inside the image. Fixed in 066c365 (Dockerfile/compose config pins + migrate service), with 6 contract tests.
- **G-03** Test 4 first attempt hit a host API on 127.0.0.1:8000 instead of the compose API: test setup, no defect.

**Scope caveat.** Tests 1–3 ran against host processes (host API + `run-jobs` + Homebrew Postgres), because a host uvicorn held 127.0.0.1:8000. The compose stack was verified at boot level (migrate → 0020, api /health + /ready 200, worker `run-jobs` up, 0 restarts) and for Test 4 enforcement, but no backtest was executed inside compose (its DB is unseeded). OPS-01's literal text (Console → HTTP → Job → worker → backtest service) is met by the host-process run. ORCH-05's compose-worker command is pinned by tests/test_deploy_config.py.

**Code review follow-through.** WR-01 (blank result-summary panel for non-succeeded Jobs) was observed as cosmetic in Test 3. WR-02 (events-panel terminal-edge race) did not show up in Test 2. WR-01..05 remain open and advisory (19-REVIEW.md).

**Final gate on main @ post-UAT:** backend `pytest` 595 passed; console `vitest` 130 passed; `tsc --noEmit` and lint clean.
