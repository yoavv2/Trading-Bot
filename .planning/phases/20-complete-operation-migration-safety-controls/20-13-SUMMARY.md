---
phase: 20-complete-operation-migration-safety-controls
plan: 13
subsystem: api
tags: [fastapi, job-orchestration, operator-controls, idempotency, postgresql]

# Dependency graph
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-02: pure load_strategy_control_state read; 20-05: job_reads payload/retry lineage; 20-10: JobOrchestrationService.retry()/retry_block()/JobNotCancellableRunningError; 20-12: worker CLI surface"
provides:
  - "PUT /api/v1/controls/kill-switch and PUT /api/v1/controls/strategies/{id}: synchronous, no-Job, no-worker, no-Idempotency-Key safety-control routes (CTRL-01/02 mechanism), idempotent by target state, audited via OPERATOR_CONTROL StrategyRun + ExecutionEvent (D-10/D-11)"
  - "GET /api/v1/controls/strategies/{id}: pure DB control-status read distinct from the static config flag on GET /api/v1/strategies/{id} (D-13/D-31)"
  - "POST /api/v1/jobs/{job_id}/retry: idempotent operator retry HTTP route over JobOrchestrationService.retry() (OPS-07 mechanism)"
  - "cancel_job now maps JobNotCancellableRunningError to 409 job_not_cancellable_running (OPS-03 mechanism), closing the gap 20-10's summary flagged"
  - "GET /api/v1/jobs/{id} adds cancellation_mode and retry_blocked, composed at read time from JobRegistry/JobOrchestrationService"
  - "D-12 five-route mutating allowlist replacing both P18/P19 exactly-two tests"
affects: [20-16-remaining-job-type-registrations, 20-17-retry-console, 20-18-controls-console, 20-19-remaining-job-type-console-wiring, 20-20-paper-session-retry-wiring, 20-22-controls-console-live-verify, 20-23-operations-console-live-verify]

tech-stack:
  added: []
  patterns:
    - "Manual JSON body parsing for mutating routes with a typed 422 dict: controls.py declares no pydantic body model / Literal fields because FastAPI's default validation-error body is a list, which the console's error-detail contract (always a dict) cannot parse -- same constraint jobs.py's submit/cancel routes already satisfy structurally, controls.py satisfies it explicitly via _read_body/_validate_reason."
    - "Route-layer composition over two independently-owned read models: job_detail composes JobReadService's DB-only detail dict with JobRegistry.resolve_submission_spec (cancellation_mode) and JobOrchestrationService.retry_block (retry_blocked) after the read returns, keeping services/job_reads.py jobs-package-free (PATTERNS constraint 1) while giving the console both fields in one response."

key-files:
  created:
    - src/trading_platform/api/routes/controls.py
    - tests/test_control_routes.py
  modified:
    - src/trading_platform/api/dependencies.py
    - src/trading_platform/api/app.py
    - src/trading_platform/api/routes/jobs.py
    - tests/test_job_mutation_api.py
    - tests/test_job_api.py
    - tests/test_orchestration_boundaries.py
    - tests/test_mutation_guard.py

key-decisions:
  - "CTRL-01, CTRL-02, OPS-03, OPS-07 all stay Pending in REQUIREMENTS.md -- this plan ships only the HTTP mechanism layer (control routes, retry route, queued-only cancel mapping, job-detail composition). Every one of these requirement's literal text (\"from the UI\", \"registered Job\" in the production registry) needs console wiring and/or production Job-type registration that later plans explicitly own: 20-16/20-17/20-19/20-20 (registration + retry/operations console) and 20-18/20-22/20-23 (controls console + live-verify). Confirmed by grepping every 20-*-PLAN.md frontmatter requirements field before leaving these Pending -- all four IDs appear in at least one plan numbered above 13, so none is orphaned by this decision."
  - "Verified the console wire contract before finalizing: console/src/lib/api.ts (20-06) already ships typed clients and a MUTATION_ERROR_COPY map keyed by the exact 18 error codes this plan's routes raise (job_not_cancellable_running, reconciliation_required, retry_exists, job_not_retryable, strategy_not_found, invalid_control_target/reason/request) and typed response shapes (state/changed/run_id for kill-switch; strategy_id/status/changed/run_id for strategy PUT; strategy_id/status/updated_at for the GET read) -- the server implementation matches this pre-existing console contract exactly, with zero server-side changes needed to align."
  - "A code-review pass (before declaring done) found and fixed two latent 500s: (1) `target in {allowed strings}` on a non-string JSON value (list/dict) raises TypeError, not a typed 422 -- fixed with an isinstance(str) guard; (2) _read_body only caught json.JSONDecodeError, so invalid-UTF-8 body bytes raised an uncaught UnicodeDecodeError -- fixed by catching both. Both are D-11 correctness requirements (every rejection must be a typed 422 dict), not scope creep."

patterns-established:
  - "Reason validation as a shared two-step gate: strict allowed-keys JSON object parse first (invalid_control_request), then string-typed comparison against a closed target set (invalid_control_target) before the reason is even inspected (invalid_control_reason) -- fixed order per D-11, reusable by any future control route."

requirements-completed: []  # CTRL-01/CTRL-02/OPS-03/OPS-07 all left Pending -- see key-decisions; this plan ships the HTTP mechanism only, console UI + production registration are later plans' scope (20-16..20-23)

# Metrics
duration: ~35min
completed: 2026-09-28
---

# Phase 20 Plan 13: Safety Controls API + Retry Route + Queued-Only Cancel + Job Detail Composition Summary

**Five new/changed HTTP routes in one plan: two synchronous no-worker safety-control PUTs (CTRL-01/02 mechanism), a pure control-status GET, an idempotent retry POST (OPS-07 mechanism), and the queued-only cancel 409 (OPS-03 mechanism) -- pinned together by a single D-12 five-route mutating allowlist.**

## Performance

- **Duration:** ~35 min
- **Tasks:** 3 completed
- **Files modified:** 9 (2 created, 7 modified)

## Accomplishments
- `PUT /api/v1/controls/kill-switch` and `PUT /api/v1/controls/strategies/{id}` call `OperatorControlService` synchronously (no Job, no worker, no `Idempotency-Key`), are idempotent by target state, and always write one `OPERATOR_CONTROL` `StrategyRun` + one `ExecutionEvent` per accepted call (changed or not). Reason is required, trimmed, and capped at 500 chars; an unparseable/non-object body or unknown keys is `invalid_control_request`; a non-string or out-of-set target is `invalid_control_target`; every rejection writes zero rows (D-10/D-11).
- `GET /api/v1/controls/strategies/{id}` exposes the true DB control status via the pure `load_strategy_control_state` read, distinct from the static config `enabled` flag `GET /api/v1/strategies/{id}` still reports (D-13/D-31) -- proven both by a live before/after-disable comparison test and a zero-write `before_cursor_execute` spy test.
- `POST /api/v1/jobs/{job_id}/retry` maps `JobOrchestrationService.retry()`'s typed errors to `job_not_found` 404, `job_not_retryable`/`retry_exists`/`reconciliation_required`/`idempotency_key_conflict` 409, `invalid_retry_payload`/`unknown_job_type` 422, `missing_idempotency_key`/`invalid_idempotency_key` 400 -- exact field shapes per the plan's must_haves (D-16..D-19).
- `cancel_job` now catches `JobNotCancellableRunningError` and returns 409 `job_not_cancellable_running`, closing the specific gap 20-10's summary named: a `QUEUED_ONLY` job type's RUNNING cancel would otherwise surface as an unhandled 500.
- `job_detail` composes `cancellation_mode` (the registered spec's value, or `null` for an unregistered type) and `retry_blocked` (`RetryBlock.to_dict()` or `null`) at read time from `JobRegistry`/`JobOrchestrationService`, without touching `services/job_reads.py` (kept jobs-package-free per PATTERNS constraint 1).
- D-12: replaced both P18/P19 "exactly two mutating job routes" tests with a five-route allowlist pin (`test_api_route_modules_declare_only_allowlisted_mutation_decorators`, `test_runtime_application_mutating_routes_are_exactly_the_allowlist`), added a route-by-route guard proof (`test_every_allowlisted_route_declares_the_mutation_guard`) and a `controls.py` import-boundary test, and extended `test_mutation_guard.py`'s disabled-default coverage to all five routes.
- Verified the console wire contract (`console/src/lib/api.ts`, 20-06) matches this plan's server implementation exactly -- same error codes, same response field names -- with zero server-side changes needed for alignment.
- Found and fixed two latent 500s via self-review before declaring done: non-string JSON targets (list/dict) causing `TypeError` on set-membership, and invalid-UTF-8 body bytes causing an uncaught `UnicodeDecodeError`. Both violated D-11's "every rejection is a typed 422 dict" invariant.

## Task Commits

1. **Task 1: Synchronous control routes (CTRL-01/02)** - RED `48ef340` (test) / GREEN `98ba44f` (feat)
2. **Task 2: Retry route, queued-only cancel 409, Job detail composition** - RED `f9bd403` (test) / GREEN `4ed104e` (feat)
3. **Task 3: D-12 five-route allowlist + disabled-default guard coverage** - `8b27872` (test)
4. **Post-review fix: guard control routes against non-string targets and undecodable bodies** - `7e3cf42` (fix)

## Files Created/Modified
- `src/trading_platform/api/routes/controls.py` (new) - `router`, `_error`, `_read_body`, `_validate_reason`, `set_kill_switch`, `set_strategy_status`, `get_strategy_control_status`
- `tests/test_control_routes.py` (new) - 31 tests: idempotent trip/reset/enable/disable + audit-row counts, 404 unknown strategy, 22-case rejection matrix (invalid target/reason/request incl. non-string and undecodable-body cases), no-Idempotency-Key-required, GET control-status vs. config-flag divergence, zero-write GET proof
- `src/trading_platform/api/dependencies.py` - added `get_operator_control_service`
- `src/trading_platform/api/app.py` - registered `controls_router`
- `src/trading_platform/api/routes/jobs.py` - `retry_job` route, `cancel_job`'s new `JobNotCancellableRunningError` mapping, `job_detail`'s `cancellation_mode`/`retry_blocked` composition
- `tests/test_job_mutation_api.py` - 15 new tests: retry create/replay, queued-only cancel 409, each Plan-10 error mapping, job-detail composition (cancellation_mode per spec, retry_blocked object/null)
- `tests/test_job_api.py` - updated the pre-existing `test_jobs_router_exposes_exact_allowed_methods` route pin for the new retry route (Rule 1, direct consequence)
- `tests/test_orchestration_boundaries.py` - D-12 five-route allowlist tests, route-by-route guard proof, `controls.py` import-boundary test, extended `jobs.py`'s import-boundary test to pin `trading_platform.jobs.registry`
- `tests/test_mutation_guard.py` - route-walk bound tightened `>=2` to `==5`, new parametrized disabled-default test over retry + both control routes

## Decisions Made
- CTRL-01/CTRL-02/OPS-03/OPS-07 left Pending -- documented in frontmatter `key-decisions` above.
- Verified the console contract before finalizing rather than assuming it -- documented in frontmatter `key-decisions` above.
- Fixed two latent 500s found during self-review before declaring done -- documented in frontmatter `key-decisions` above.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `test_jobs_router_exposes_exact_allowed_methods` pin needed updating for the new retry route**
- **Found during:** Task 2 verification
- **Issue:** `tests/test_job_api.py` (outside this plan's `files_modified`) pins the exact set of `/api/v1/jobs*` routes; adding `POST /{job_id}/retry` made this pre-existing test fail.
- **Fix:** Added the new route to the pinned dict.
- **Files modified:** `tests/test_job_api.py`
- **Committed in:** `4ed104e` (Task 2 GREEN commit)

**2. [Rule 1 - Bug] Non-string control targets and undecodable body bytes returned 500 instead of a typed 422**
- **Found during:** Post-implementation self-review (before declaring done)
- **Issue:** `target in {"tripped", "armed"}` raises `TypeError` for an unhashable value (list/dict); `_read_body` caught only `json.JSONDecodeError`, missing `UnicodeDecodeError` on invalid-UTF-8 bytes. Both are D-11 correctness requirements (every rejection is a typed 422 dict, zero writes).
- **Fix:** Added `isinstance(str)` guards before both membership checks; widened the except clause to `(json.JSONDecodeError, UnicodeDecodeError)`.
- **Files modified:** `src/trading_platform/api/routes/controls.py`, `tests/test_control_routes.py` (added list/dict-target and invalid-UTF-8 cases to both rejection matrices, plus an enable-after-disable test since only the disable path had been exercised)
- **Verification:** `.venv/bin/pytest tests/test_control_routes.py -q` (31 passed)
- **Committed in:** `7e3cf42` (fix)

---

**Total deviations:** 2 auto-fixed (both Rule 1 bugs)
**Impact on plan:** Both fixes necessary for correctness (D-11's typed-422 invariant, and keeping a pre-existing structural pin accurate). No scope creep.

## Issues Encountered

None beyond the two auto-fixed bugs above. No auth gates, no checkpoints (plan is fully autonomous).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- The console (20-06) already has typed clients (`tripKillSwitch`, `resetKillSwitch`, `enableStrategy`, `disableStrategy`, `retryJob`) and error-copy tables built against this exact wire contract -- verified to match with zero server-side changes needed. The `/controls` page (20-18/20-22) and remaining registration/console-wiring plans (20-16/20-17/20-19/20-20/20-23) can proceed without further backend changes for CTRL-01/02/OPS-03/OPS-07's mechanism.
- Full suite verified green at 850 passed (0 failed), up from the 806-pass pre-plan baseline. `.venv/bin/ruff check src/trading_platform/api tests/test_control_routes.py` clean.
- `paper-session`/`broker-order-sync`/`reconciliation` are still not registered in `build_default_registry` (20-16's scope) -- the queued-only cancel 409 and D-19 reconcile-first block are only reachable through the production API once that registration lands; both are fully mechanism-tested against test-local registries here.

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

- FOUND: src/trading_platform/api/routes/controls.py
- FOUND: tests/test_control_routes.py
- FOUND (modified): src/trading_platform/api/dependencies.py
- FOUND (modified): src/trading_platform/api/app.py
- FOUND (modified): src/trading_platform/api/routes/jobs.py
- FOUND (modified): tests/test_job_mutation_api.py
- FOUND (modified): tests/test_job_api.py
- FOUND (modified): tests/test_orchestration_boundaries.py
- FOUND (modified): tests/test_mutation_guard.py
- FOUND commit: 48ef340 (Task 1 RED)
- FOUND commit: 98ba44f (Task 1 GREEN)
- FOUND commit: f9bd403 (Task 2 RED)
- FOUND commit: 4ed104e (Task 2 GREEN)
- FOUND commit: 8b27872 (Task 3)
- FOUND commit: 7e3cf42 (post-review fix)
