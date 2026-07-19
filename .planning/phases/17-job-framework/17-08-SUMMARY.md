---
phase: 17-job-framework
plan: 08
subsystem: api
tags: [fastapi, sqlalchemy, postgresql, read-only-api, observability]

# Dependency graph
requires:
  - phase: 17-01
    provides: "Job/JobDependency/JobEvent/JobLog ORM models, closed JobStatus/JobFailureReason/JobCancellationCause vocabulary"
  - phase: 17-04
    provides: "DatabaseJobContext write path for progress/logs, MAX_LOG_MESSAGE_CHARS/MAX_LOG_CONTEXT_BYTES caps"
  - phase: 17-05
    provides: "JobDependency edge shape used by the detail route's dependency/blocking-dependency lists"
provides:
  - "JobReadService -- transport-agnostic, boundary-respecting (JOB-04) read layer over Job/JobDependency/JobEvent/JobLog"
  - "Five read-only /api/v1/jobs HTTP routes: list, detail, progress, logs, events"
  - "Cursor-based log pagination (sequence-ordered, D-13) that provably neither skips nor duplicates rows under paging"
  - "JOB-07 closed end-to-end: progress/logs recorded by 17-04 are now observable via the API during and after execution"
affects: [17-09, 18]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Overfetch-by-one (limit+1) cursor pagination to compute has_more without a false positive on an exact-size final page"
    - "build_collection_response generalized with a serializer parameter so a second read-surface family (jobs) reuses the same envelope builder as the operator-reads family (runs/operations) without a second envelope shape"
    - "Boundary-sensitive predicates (here: 'a dependency is blocking') reimplemented inline in services/ rather than imported from jobs/, when the same predicate already exists in the jobs package -- required by the JOB-04 import-boundary test"

key-files:
  created:
    - src/trading_platform/services/job_reads.py
    - src/trading_platform/api/routes/jobs.py
    - tests/test_job_api.py
  modified:
    - src/trading_platform/api/dependencies.py
    - src/trading_platform/api/app.py

key-decisions:
  - "JOB-07 marked Complete: its literal text ('records progress and structured logs observable via the API during and after execution') is now fully satisfiable -- 17-04 ships the recording, this plan ships API observability, and test_progress_is_readable_while_job_is_running / test_progress_of_failed_job_preserves_last_value pin the during/after halves respectively. JOB-07 does not require a live worker executing a handler (that lands in 17-09); it requires that a Job's RUNNING-state progress and its terminal-state progress are both readable over HTTP, which this plan proves against a real Postgres database."
  - "JOB-05 and JOB-06 deliberately left Pending despite appearing in this plan's frontmatter -- this plan only reads dependency/cancellation state; it ships no dependency-gated start (JOB-05) or operator cancel action (JOB-06). Both remain blocked on Phase 18/19 wiring per the existing STATE.md blocker notes from 17-05/17-06."
  - "'Blocking dependency' (a dependency whose target Job has not reached SUCCEEDED) is reimplemented as an inline Python filter in job_reads.py rather than importing jobs/dependencies.py's unsatisfied_dependency_exists SQL predicate -- services/ must not import trading_platform.jobs (JOB-04), even though the two predicates express the same idea and could otherwise be seen as duplicated logic."
  - "Log-pagination has_more is computed by fetching capped_limit+1 rows and trimming, not by comparing len(items) to the requested limit -- the naive comparison produces a false positive when the database holds exactly limit remaining rows, which would break the D-13 cursor-safety guarantee on the last page of a set whose final page size equals the limit exactly."

requirements-completed: [JOB-07]

# Metrics
duration: ~25min
completed: 2026-07-20
---

# Phase 17 Plan 08: Generic Job Read Surface (JOB-07 API Observation) Summary

**JobReadService + five read-only `/api/v1/jobs` routes expose Job lifecycle, terminal outcome, dependency causal chain, cancellation audit, live progress, and sequence-ordered paginated logs over HTTP, closing JOB-07 end-to-end.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 3 completed
- **Files modified:** 5 (3 created, 2 modified)

## Accomplishments

- `src/trading_platform/services/job_reads.py` ships `JobReadService` with five methods (`list_jobs`, `get_job_detail`, `get_job_progress`, `list_job_logs`, `list_job_events`), all opening their own `session_scope`, returning plain JSON-serializable dicts, and respecting the JOB-04 import boundary (verified: zero `trading_platform.jobs` imports, zero write operations, `tests/test_job_import_boundary.py` still 36/36 green with the new module in scope).
- `get_job_detail` exposes the full D-05 dependency causal chain (`blocking_job_id`/`blocking_job_status`/`root_cause_job_id`), the D-10 cancellation audit record (requester, reason, `requested_at`, `acknowledged_at`, cause), and a `blocking_dependencies` list that explains exactly why a QUEUED Job hasn't started -- the "blocking" predicate is reimplemented inline (not imported from `jobs/dependencies.py`) to respect the boundary.
- `list_job_logs` orders strictly by `sequence` (D-13, never `logged_at`) and implements safe cursor pagination: fetching `limit+1` rows and trimming makes `has_more` correct even when the database holds exactly `limit` remaining rows, avoiding a false-negative on the final page.
- `src/trading_platform/api/routes/jobs.py` registers five GET-only routes (`""`, `/{job_id}`, `/{job_id}/progress`, `/{job_id}/logs`, `/{job_id}/events`) under `/api/v1/jobs`, translating every `LookupError` to a 404, with `status` typed as the `JobStatus` enum (422 on an unknown value) and `job_id` typed as `UUID` (422 on a malformed path segment) at the transport layer.
- `src/trading_platform/api/dependencies.py`'s `build_collection_response` gained a `serializer` parameter (default `serialize_operator_filters`, unchanged for `runs`/`operations`) so the jobs list route reuses one envelope builder rather than a second one; `build_operator_read_catalog` gained a `jobs` section with all five sub-paths.
- `tests/test_job_api.py` (19 tests, all green against a real migrated Postgres database via `TestClient`): seeded-ordering, status/type filtering, 422 on out-of-enum status and oversized limits, full detail lifecycle/outcome, dependency causal chain, blocking-dependencies filtering, cancellation audit, 404-vs-422 discrimination, progress readable while RUNNING and preserved after FAILED (D-12, literal value 60), deterministic log ordering under colliding timestamps, literal `[1, 2, 3, 4, 5]` cursor-pagination round trip across three pages, empty-vs-missing-Job log distinction, rejected/accepted event exposure, and a runtime route-introspection test proving no mutating verb exists on the jobs router.
- Full suite verified green: 433 passed, 0 failed (up from the 413-pass pre-plan baseline plus 19 new tests plus one previously-uncounted pass), no regressions.

## Task Commits

Each task was committed atomically:

1. **Task 1: JobReadService -- the transport-agnostic read layer** - `4688c14` (feat)
2. **Task 2: Read-only jobs routes, dependency providers, and router registration** - `442abdc` (feat)
3. **Task 3: API observability tests** - `9173c32` (test)

## Files Created/Modified

- `src/trading_platform/services/job_reads.py` - `JobReadService`, `JobReadFilters`, pagination/limit constants
- `src/trading_platform/api/routes/jobs.py` - five read-only `/api/v1/jobs` routes
- `src/trading_platform/api/dependencies.py` - `get_job_read_service`, `get_job_read_filters`, `serialize_job_filters`, generalized `build_collection_response`, extended `build_operator_read_catalog`
- `src/trading_platform/api/app.py` - `jobs_router` import + `include_router` call
- `tests/test_job_api.py` - 19 JOB-07 API observability tests + local `migrated_job_api_db` fixture + `_seed_job` helper

## Decisions Made

See `key-decisions` in frontmatter: JOB-07 marked Complete with rationale; JOB-05/JOB-06 deliberately left Pending; the blocking-dependency predicate reimplemented inline per the JOB-04 boundary; `has_more` computed via overfetch-by-one rather than a `len(items) == limit` comparison.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Acceptance-criteria greps flagged docstring/comment text, not real imports**
- **Found during:** Task 1, running the `grep -c "from trading_platform.jobs\|import trading_platform.jobs"` acceptance check
- **Issue:** The module docstring and an inline comment explaining the JOB-04 boundary literally contained the substring `trading_platform.jobs` (e.g. "must never import from `trading_platform.jobs`"), so the acceptance grep matched prose, not an actual import statement, and returned 1 instead of the required 0.
- **Fix:** Reworded the docstring and comment to describe the same constraint without ever writing `trading_platform.jobs`/`import trading_platform.jobs` as contiguous text (e.g. "must never import the `jobs`, `api`, or `worker` top-level packages of this project").
- **Files modified:** `src/trading_platform/services/job_reads.py` (comment/docstring wording only, no logic change)
- **Verification:** `grep -c "from trading_platform.jobs\|import trading_platform.jobs" src/trading_platform/services/job_reads.py` returns 0; `pytest tests/test_job_import_boundary.py -q` 36/36 green
- **Committed in:** `4688c14` (part of the Task 1 commit)

**2. [Rule 3 - Blocking, tooling] ruff format reformatted dependencies.py and test_job_api.py**
- **Found during:** Task 3, running the project's pre-commit format gate before the Task 3 commit
- **Issue:** `ruff format --check` flagged a line-wrap in `dependencies.py`'s `serialize_operator_filters` (pre-existing code, untouched by this plan's logic but adjacent to the new `serialize_job_filters`) and a parameter-call wrap in the new test file; the project's pre-commit hook is a merge-blocking gate.
- **Fix:** Ran `ruff format` on both files; re-verified all Task 1/2 acceptance-criteria greps and the full `tests/test_job_api.py` suite green after reformatting.
- **Files modified:** `src/trading_platform/api/dependencies.py` (whitespace/line-wrap only), `tests/test_job_api.py` (whitespace only)
- **Verification:** `ruff format --check` clean on all five plan files; `pytest tests/test_job_api.py -x -q` 19/19 green; full suite 433/0
- **Committed in:** `9173c32` (bundled with the Task 3 test commit, since it was discovered while preparing that commit)

---

**Total deviations:** 2 auto-fixed (1 bug/wording, 1 blocking/tooling)
**Impact on plan:** No behavior change from either fix. No scope creep.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Requirements Frontmatter Discrepancy

This plan's frontmatter lists `requirements: [JOB-07, JOB-06, JOB-05]`. Only **JOB-07** is marked Complete by this plan.

- **JOB-07** ("Every Job records progress and structured logs observable via the API during and after execution") is Complete: "records" was shipped by 17-04 (the write-side `DatabaseJobContext`), and "observable via the API during and after execution" is what this plan ships and tests end-to-end -- `test_progress_is_readable_while_job_is_running` proves the during-execution half (a RUNNING Job's live `progress_percent` is readable over HTTP) and `test_progress_of_failed_job_preserves_last_value` proves the after-execution half (D-12's preserved-progress guarantee is readable over HTTP). This reading does not require a live worker actually executing a handler (that lands in 17-09) -- JOB-07's text is about progress/logs being observable, not about execution itself being orchestrated.
- **JOB-05** ("a dependent Job starts only after all dependencies succeed... a failed dependency moves dependents to a terminal non-executed state") remains Pending. This plan only *reads* dependency state (`dependencies`/`blocking_dependencies` on the detail route); it ships no start-gating or cascade-cancellation behavior of its own (that mechanism already exists in 17-05's `jobs/dependencies.py`, but 17-05 itself was left Pending because no claim loop called it end-to-end until 17-07, and 17-07 also left it Pending because its own frontmatter declared only JOB-02). This plan does not change that state; it is out of scope to mark JOB-05 Complete here.
- **JOB-06** ("Operator can cancel a queued or running Job; cancellation transitions it to `CANCELLED` and is audited") remains Pending. This plan only *reads* the cancellation audit trail (the `cancellation_*` fields on the detail route, and `JobEvent` rows via the events route); it ships no operator-facing cancel action. 17-06 already shipped the full mechanism (`request_cancellation`/`acknowledge_cancellation`/`sweep_cancellation_timeouts`) but left JOB-06 Pending because no operator-facing surface calls `request_cancellation` yet (Phase 18/19 scope). This plan does not change that.

Per the 17-01/17-04/17-05/17-06/17-07 precedent in STATE.md (do not mark a requirement Complete until the behavior it describes is actually verifiable end-to-end), `requirements mark-complete` was run for **JOB-07 only**. JOB-05 and JOB-06 remain `Pending` in `REQUIREMENTS.md`, consistent with the existing blocker notes from 17-05/17-06/17-07.

## Next Phase Readiness

- `JobReadService` and the five `/api/v1/jobs` routes are the complete D-15 generic read surface Phase 18's orchestration layer (ORCH-04: "the console observes Job state, progress, and logs via API reads") can build on directly -- no further read-surface work is required before Phase 18 adds submission/cancellation write routes.
- The `blocking_dependencies` field on the detail route is ready for a future console screen to explain, without additional backend work, exactly why a QUEUED Job hasn't started.
- No code blockers identified for 17-09 (the worker execution loop) or Phase 18. Full test suite holds at 433/0 passing.

---
*Phase: 17-job-framework*
*Completed: 2026-07-20*

## Self-Check: PASSED

All three created files (`src/trading_platform/services/job_reads.py`, `src/trading_platform/api/routes/jobs.py`, `tests/test_job_api.py`) verified present on disk; all three task commit hashes (4688c14, 442abdc, 9173c32) verified present in git log.
