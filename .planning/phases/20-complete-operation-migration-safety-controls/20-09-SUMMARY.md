---
phase: 20-complete-operation-migration-safety-controls
plan: 09
subsystem: jobs
tags: [jobs, paper-execution, pydantic, nextjs, console]

# Dependency graph
requires:
  - phase: 20-02
    provides: submit_orders.py ensure_strategy_control_state / ensure_strategy_state pattern
  - phase: 20-03
    provides: shared payload_fields.py validators (parse_iso_date, map_validation_error, require_registered_strategy, require_trading_session_not_future, latest_completed_session_default)
  - phase: 20-04
    provides: JobCancellationMode.QUEUED_ONLY, jobs/handlers/domain_conflicts.py::translate_domain_conflicts, registry.retry_prerequisite_for
  - phase: 20-05
    provides: services/reconciliation/report.py::reconcile_paper_execution(..., job_id=) job_id threading; services/risk.py::is_eligible_risk_run
  - phase: 20-06
    provides: console/src/components/jobs/new/jobFormKit.tsx (useJobFormSubmission, JobFormFooter, StrategySelectField)
  - phase: 20-08
    provides: ReconciliationSubmissionSpec/ReconciliationJobHandler/ReconciliationJobForm as the direct queued-only structural precedent for this plan's paper-session Job type
provides:
  - "run_paper_session(..., job_id=) threads job_id to BOTH runs it creates (internal reconciliation StrategyRun and paper_execution StrategyRun, including the blocked_strategy_disabled path); PaperSessionRunReport.reconciliation_run_id names the internal reconciliation run"
  - "PaperSessionSubmissionSpec + PaperSessionPayloadRejection (strict {strategy_id, as_of_session, risk_run_id} public Job contract with an optional pinned risk run, UUID + SUCCEEDED-risk-evaluation eligibility check; QUEUED_ONLY cancellation, retry_prerequisite_job_type = \"reconciliation\", D-01/D-19/D-23)"
  - "PaperSessionJobHandler (wraps run_paper_session in translate_domain_conflicts(), external_broker_session_started marker before the call, zero cancellation checkpoints, never branches on report.action, PAPER execution mode, D-02..D-05/D-19)"
  - "PaperSessionJobForm.tsx (unwired form, ready for a later registry-registration plan)"
affects: [20-16, 20-19]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Phase 20 queued-only Job-type vertical slice with a real domain-conflict path: spec (payload_fields.py helpers + JobCancellationMode.QUEUED_ONLY + optional pinned-reference field with its own eligibility check) + handler (zero cancellation checkpoints, external_* pre-call marker, translate_domain_conflicts() wrap, never reinterprets domain outcomes) + unit tests + unwired console form -- the queued-only, retry-gated, domain-conflict-translating sibling of the 20-08 reconciliation precedent"

key-files:
  created:
    - src/trading_platform/jobs/handlers/paper_session_submission.py
    - src/trading_platform/jobs/handlers/paper_session.py
    - tests/test_paper_session_job_type.py
    - console/src/components/jobs/new/PaperSessionJobForm.tsx
    - console/src/components/jobs/new/PaperSessionJobForm.test.tsx
  modified:
    - src/trading_platform/services/execution/submit_orders.py
    - src/trading_platform/services/execution/_paper_common.py

key-decisions:
  - "recover_inflight_paper_orders (read_first-listed for confirmation) creates no StrategyRun -- it only reads/writes PaperOrder rows via _apply_broker_order_snapshot -- so it takes no job_id parameter, exactly as the plan's action text implied without stating outright. Confirmed by direct read before writing any code, not assumed."
  - "A bare uuid.uuid4() cannot stand in for a job_id in DB-backed tests: strategy_runs.job_id carries an FK to jobs.id (migration 0021), so every job_id-threading test seeds a real (minimal) Job row first via a local _seed_job() helper, mirroring tests/test_phase20_operations_migration.py's _create_job precedent."
  - "PaperSessionSubmissionSpec.retry_prerequisite_job_type is written as the literal string \"reconciliation\" (not an indirection constant) so the plan's own pinned acceptance-criteria grep (`retry_prerequisite_job_type = \"reconciliation\"`) holds against the source line itself."
  - "paper_session.py's docstring paraphrases the external_broker_session_started marker in prose instead of naming it verbatim, and the Cancellable-only-while-queued sentence in paper_session_submission.py's description is kept on one unbroken source line -- both changes needed only to satisfy the plan's own pinned single-line/single-occurrence greps (external_broker_session_started count==1; the sentence substring count==1), not a design change. Same class of grep-vs-prose conflict as 20-03/20-08."
  - "Left OPS-03/OPS-08 Pending in REQUIREMENTS.md (spec/handler/service-threading/form ship here; build_default_registry registration and JOB_TYPE_FORMS console wiring are a later plan's scope, per registry.py's own docstring naming that later plan) -- see requirements-completed note below."

patterns-established: []

requirements-completed: []  # OPS-03/OPS-08 stay Pending: this plan ships the job_id-threading spine, spec+handler+unit tests, and an unwired form only -- neither OPS-03's "registered Job"/"operator can run...from the UI" text nor OPS-08's literal end-to-end-reachable domain_conflict path is true until a later plan registers PaperSessionJobHandler in build_default_registry and wires PaperSessionJobForm into JOB_TYPE_FORMS (20-01/20-04/20-05/20-08 precedent).

# Metrics
duration: ~35min
completed: 2026-09-28
---

# Phase 20 Plan 09: paper-session Job Type Summary

**run_paper_session job_id threading to both runs it creates + PaperSessionSubmissionSpec/PaperSessionJobHandler (strict {strategy_id, as_of_session, risk_run_id|null} payload with UUID + SUCCEEDED-risk-evaluation eligibility check, QUEUED_ONLY cancellation, D-19 reconcile-first retry prerequisite, translate_domain_conflicts() wrap, external_broker_session_started outcome-uncertain marker) plus an unwired PaperSessionJobForm console component -- the only broker-submission path after Phase 20 (D-28).**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-09-28T13:05:00Z (approx)
- **Completed:** 2026-09-28T13:40:00Z
- **Tasks:** 3 completed
- **Files modified:** 7 (5 new, 2 modified)

## Accomplishments
- `run_paper_session(..., job_id=)`: threads `job_id` to both `run_paper_order_submission` calls (the `blocked_strategy_disabled` path and the normal submission path) and to `reconcile_paper_execution`, so a paper-session Job's internal reconciliation run and its execution run are both FK-linked from creation (D-08/D-09). `run_paper_order_submission`/`_run_paper_order_submission_guarded`/`_create_paper_execution_run` gain a pass-through `job_id` keyword (D-28: `run_paper_order_submission` stays because the paper session uses it). `PaperSessionRunReport` gains a `reconciliation_run_id: str | None = None` last field (+ `to_dict()` entry) naming the internal reconciliation run, `None` on the `blocked_strategy_disabled` path and when `job_id` is omitted.
- `PaperSessionSubmissionSpec`: strict `extra="forbid"` pydantic validation delegating to `payload_fields.py`; `risk_run_id` has no pydantic default (the key must always be present, value may be `null`); a non-null `risk_run_id` is UUID-parsed then checked via `is_eligible_risk_run` (SUCCEEDED, same strategy, same session) before being normalized to its canonical lowercase string. Closed 9-value `PaperSessionPayloadRejection` enum. `cancellation_mode = JobCancellationMode.QUEUED_ONLY` with the exact D-03 sentence in `description`; `retry_prerequisite_job_type = "reconciliation"` (D-19). `submission_defaults()` never includes `risk_run_id` (D-23 -- the form sends an explicit `null`).
- `PaperSessionJobHandler`: `report_progress`/`log` steps exactly `["resolving strategy", "running paper session", "recording result"]`; logs `event_code="external_broker_session_started"` immediately before the one `run_paper_session` call, wrapped in `translate_domain_conflicts()` so a `ConcurrentRunLockedError` surfaces as `JobDomainConflictError` naming the strategy and session (D-04); zero cancellation checkpoints anywhere in the module (D-02/D-03: a RUNNING queued-only Job is never cancelled); never branches on `report.action` -- a blocked/no-op report returns as an ordinary successful `result_summary` (D-05); `required_execution_mode = ExecutionMode.PAPER`; `result_summary` carries `action`, `strategy_id`, `as_of_session`, `source_risk_run_id`, `execution_run_id`, `execution_status`, `reconciliation_run_id`, and `produced_run_ids` (the non-null subset of `[reconciliation_run_id, execution_run_id]`).
- `tests/test_paper_session_job_type.py`: 32 tests total -- 3 service-level `job_id`-threading tests against a real Postgres DB (both created runs carry `job_id`; blocked path links only the execution run; omitted `job_id` links nothing), plus 29 spec/handler unit tests (one parametrized case per rejection enum value including a DB-backed eligible/ineligible pinned-risk-run pair, normalization, both `submission_defaults` paths, registry contract, retry-prerequisite/cancellation-mode pinning, and the full handler ordering/no-checkpoint/domain-conflict-translation/D-05-blocked-report/produced-run-ids/execution-mode/never-writes-status matrix).
- `PaperSessionJobForm.tsx`: Strategy `<select>` + `as_of_session` date input (required) + a third, genuinely optional `risk_run_id` text field (label "Risk run ID (optional)", helper "Leave blank to use the latest succeeded risk evaluation.") composed over the Plan 06 `jobFormKit`; submits `{job_type: "paper-session", payload: {strategy_id, as_of_session, risk_run_id}}` where `risk_run_id` is always present as either the trimmed string or JSON `null`; button reads "Submit Paper Session"; deliberately not registered in `JOB_TYPE_FORMS` (a later plan's scope).

## Task Commits

1. **Task 1: Thread job_id through run_paper_session to both created runs**
   - `31007e9` (feat) -- service threading + 3 new job_id-threading tests, full `tests/test_paper_session_job_type.py tests/test_paper_execution.py tests/test_concurrency_guard.py` green (42/42), both pinned acceptance-criteria greps (`job_id=job_id` count 6 ≥ 5, `reconciliation_run_id` count 2 ≥ 2) verified
2. **Task 2: PaperSessionSubmissionSpec + PaperSessionJobHandler + unit tests**
   - `029b009` (feat) -- spec + handler + 29 unit tests, full file 32/32 green (≥ 22 required), all four pinned acceptance-criteria greps verified (`raise_if_cancelled` → 0, `external_broker_session_started` → 1, `retry_prerequisite_job_type = "reconciliation"` → 1, the D-03 sentence → 1)
3. **Task 3: PaperSessionJobForm**
   - `9ca4cb6` (feat) -- form + test, `npx vitest run src/components/jobs/new/PaperSessionJobForm.test.tsx src/lib/consoleBoundaries.test.ts` (18/18) + `npx tsc --noEmit` clean, both pinned greps (`Submit Paper Session` → 1, the helper sentence → 1) verified

**Plan metadata:** (this commit, to follow, using plain `git commit` with the Co-Authored-By trailer per this session's instructions)

## Files Created/Modified
- `src/trading_platform/services/execution/submit_orders.py` - `job_id` threading through `run_paper_order_submission`/`_run_paper_order_submission_guarded`/`_create_paper_execution_run`/`run_paper_session`
- `src/trading_platform/services/execution/_paper_common.py` - `PaperSessionRunReport.reconciliation_run_id`
- `tests/test_paper_session_job_type.py` - 32 tests: 3 service-level job_id-threading + 29 spec/handler unit tests
- `src/trading_platform/jobs/handlers/paper_session_submission.py` - `PaperSessionSubmissionSpec`, `PaperSessionPayloadRejection`, `PAPER_SESSION_JOB_TYPE`
- `src/trading_platform/jobs/handlers/paper_session.py` - `PaperSessionJobHandler`
- `console/src/components/jobs/new/PaperSessionJobForm.tsx` - paper-session submission form
- `console/src/components/jobs/new/PaperSessionJobForm.test.tsx` - 5 vitest cases (pre-fill, disabled-until-filled, null risk_run_id on submit, trimmed risk_run_id on submit, mutations-disabled)

## Decisions Made
- Confirmed by direct read (not assumed) that `recover_inflight_paper_orders` creates no `StrategyRun`, so it correctly takes no `job_id` parameter -- see key-decisions.
- Added a local `_seed_job()` test helper (mirroring `tests/test_phase20_operations_migration.py::_create_job`) since `strategy_runs.job_id` carries a real FK to `jobs.id` -- see key-decisions.
- Wrote `retry_prerequisite_job_type = "reconciliation"` as a literal string rather than an indirection constant, and kept the D-03 description sentence on one source line, both purely to satisfy the plan's own pinned single-line/exact-substring greps -- see key-decisions.
- Reworded `paper_session.py`'s docstring to paraphrase the `external_broker_session_started` marker instead of naming it verbatim, so the plan's pinned "count == 1" grep (log call only) holds -- see key-decisions.
- Left OPS-03/OPS-08 Pending in REQUIREMENTS.md (registration and console wiring are a later plan's scope) -- see requirements-completed note above.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Pinned acceptance-criteria greps initially failed on docstring/description prose**
- **Found during:** Task 2, immediately after first green test run
- **Issue:** The plan pins `grep -c "external_broker_session_started" ... paper_session.py` at exactly 1, but the module docstring also named the marker verbatim (count 2). Separately, `retry_prerequisite_job_type = RECONCILE_FIRST_PREREQUISITE_JOB_TYPE` (an indirection constant) did not match the plan's literal `retry_prerequisite_job_type = "reconciliation"` grep, and the D-03 description sentence was split across two Python string-literal lines, so it never appeared as one contiguous substring in the source file even though the runtime string was correct.
- **Fix:** Reworded the docstring sentence to paraphrase the marker concept without repeating the literal event code; replaced the indirection constant with the literal string assignment; reflowed the description's two string-literal lines so the D-03 sentence sits on a single source line.
- **Files modified:** `src/trading_platform/jobs/handlers/paper_session.py`, `src/trading_platform/jobs/handlers/paper_session_submission.py`
- **Verification:** All four pinned greps now return their required counts; 32/32 tests still green.
- **Committed in:** `029b009` (caught before the Task 2 commit landed, not a follow-up fix commit)

**2. [Rule 1 - Bug] Test-time `IntegrityError` on a bare `uuid.uuid4()` used as `job_id`**
- **Found during:** Task 1, first pytest run of the two new job_id-threading tests
- **Issue:** `strategy_runs.job_id` carries `fk_strategy_runs_job_id_jobs` (migration 0021, FK to `jobs.id`), so passing a freshly generated UUID with no backing `jobs` row violated the foreign key on insert.
- **Fix:** Added a local `_seed_job()` helper that inserts a minimal real `Job` row and returns its id, used everywhere a real `job_id` is needed in a DB-backed test (mirrors the existing `tests/test_phase20_operations_migration.py::_create_job` precedent).
- **Files modified:** `tests/test_paper_session_job_type.py`
- **Verification:** Both job_id-threading tests pass; `tests/test_paper_execution.py`/`tests/test_concurrency_guard.py` unaffected (40/40 still green in the same run).
- **Committed in:** `31007e9` (caught before the Task 1 commit landed, not a follow-up fix commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1, both caught and folded into their task's commit before it landed -- no separate fix commits needed)
**Impact on plan:** Both fixes tighten conformance to the plan's own literal acceptance criteria and test-database FK reality; no scope creep, no architectural change.

## Issues Encountered
None beyond the deviations above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `paper-session` is fully unit-tested (spec, handler, and service-level job_id threading) and ready for `build_default_registry` registration and `JOB_TYPE_FORMS` console wiring in a later plan, following the `backtest`/`risk-evaluation`/`reconciliation` precedent exactly.
- The D-19 reconcile-first retry block is declared (`retry_prerequisite_job_type = "reconciliation"`) but not yet enforced by any retry-gating service/route -- that consumer is a later plan's scope (mirrors 20-04's original note for this same mechanism).
- No blockers.

## Self-Check: PASSED

- FOUND: src/trading_platform/services/execution/submit_orders.py
- FOUND: src/trading_platform/services/execution/_paper_common.py
- FOUND: tests/test_paper_session_job_type.py
- FOUND: src/trading_platform/jobs/handlers/paper_session_submission.py
- FOUND: src/trading_platform/jobs/handlers/paper_session.py
- FOUND: console/src/components/jobs/new/PaperSessionJobForm.tsx
- FOUND: console/src/components/jobs/new/PaperSessionJobForm.test.tsx
- FOUND commit 31007e9 (Task 1 feat)
- FOUND commit 029b009 (Task 2 feat)
- FOUND commit 9ca4cb6 (Task 3 feat)
- Full backend suite: 746 passed (0 failed), up from the 714-pass 20-08 baseline (+32 new tests)
- Full console verify: `npx vitest run` (172 passed, all files) + `npx tsc --noEmit` (clean)
- `.venv/bin/mypy src/trading_platform/services/execution src/trading_platform/services/reconciliation src/trading_platform/services/config` -- clean (16 source files)

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*
