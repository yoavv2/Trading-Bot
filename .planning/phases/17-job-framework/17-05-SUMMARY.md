---
phase: 17-job-framework
plan: 05
subsystem: infra
tags: [sqlalchemy, postgresql, dag, cycle-detection, python]

# Dependency graph
requires:
  - phase: 17-01
    provides: "Job/JobDependency/JobEvent ORM models and the closed JobStatus/JobCancellationCause vocabulary"
  - phase: 17-03
    provides: "apply_job_transition -- the single guarded writer of Job.status"
provides:
  - "validate_dependency_set + submit_job -- submission-time self-dependency/unknown-dependency/cycle rejection before any row is inserted (D-06)"
  - "find_ready_job_ids + unsatisfied_dependency_exists -- single-query readiness predicate for plan 17-07's claim loop"
  - "cascade_dependency_outcome -- transitive dependency-outcome cancellation with full causal chain (D-04, D-05)"
affects: [17-06, 17-07, 17-08, 17-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Iterative (explicit-stack) three-color DFS cycle detection rooted at the new Job id -- unbounded dependency chains are not a Python recursion-limit concern"
    - "Reusable SQLAlchemy expression-builder function (unsatisfied_dependency_exists) returning a correlated EXISTS, so readiness has exactly one SQL definition shared by this module and the future claim loop"
    - "BFS cascade over reverse dependency edges, filtered to QUEUED before calling apply_job_transition, mirroring reclaim_stale_runs's idempotency-via-status-filter convention"

key-files:
  created:
    - src/trading_platform/jobs/dependencies.py
    - tests/test_job_dependencies.py
  modified: []

key-decisions:
  - "Self-dependency and cycle rejection are tested by driving submit_job through a mocked uuid.uuid4() that returns an already-existing Job id, not by literally forcing a caller-supplied cycle -- submit_job always generates its own id internally, so a caller can never reference the about-to-be-created Job's id, and that impossibility IS the D-06 'unrepresentable' guarantee, not a test gap"
  - "validate_dependency_set gained an optional job_type: str | None = None keyword (not in the plan's literal signature) purely to make SelfDependencyError messages informative when called from submit_job; it does not affect validation logic or any acceptance-criteria grep"
  - "cascade_dependency_outcome queries (Job.id, Job.status) per BFS level in one un-locked SELECT rather than re-fetching each candidate with with_for_update -- the row lock and the actual status re-check happen once, inside apply_job_transition itself, avoiding a redundant double-lock per descendant"
  - "unsatisfied_dependency_exists's job_id_column parameter is typed Any (return type stays ColumnElement[bool]) because mypy does not treat SQLAlchemy's InstrumentedAttribute as a ColumnElement subtype even though it behaves as one at runtime via __clause_element__"

requirements-completed: []  # JOB-05 left Pending -- see 'Requirements Note' below

# Metrics
duration: ~25min
completed: 2026-07-19
---

# Phase 17 Plan 05: Explicit Job Dependencies Summary

**`jobs/dependencies.py` ships submission-time cycle/self-dependency rejection via iterative three-color DFS, a single-query readiness predicate, and a BFS transitive dependency-outcome cascade that cancels every unstarted descendant with a full causal chain -- pinned by 15 tests against a real Postgres database.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 3 completed
- **Files modified:** 2 (both created)

## Accomplishments

- `validate_dependency_set` rejects self-dependencies, unknown dependency IDs, and any cycle (proven for both a two-node and a genuine three-hop cycle) before a single row is written; the cycle check is an iterative three-color DFS with an explicit stack (`_detect_cycle`), never recursive.
- `submit_job` inserts the Job, its deduplicated `JobDependency` edges, and a `SUBMITTED` `JobEvent` in one transaction; validation failure raises before any insert, so the whole submission rolls back cleanly, and there is deliberately no `add_dependency`/`remove_dependency` function anywhere -- D-06 immutability is structural, confirmed by a zero-hit grep.
- `find_ready_job_ids` returns QUEUED Job IDs oldest-first via exactly one SQL statement (a correlated NOT-EXISTS over `JobDependency`/`Job`), gated behind `unsatisfied_dependency_exists`, a standalone reusable expression builder plan 17-07's `claim_next_job` will call verbatim rather than re-deriving the SQL.
- `cascade_dependency_outcome` performs a BFS over the reverse dependency edges from a Job that just reached FAILED or CANCELLED, transitioning every still-QUEUED descendant to CANCELLED through `apply_job_transition` (never a direct status write) with `blocking_job_id`/`blocking_job_status`/`root_cause_job_id` recording the exact causal chain, `cancellation_cause` correctly split between `DEPENDENCY_FAILED`/`DEPENDENCY_CANCELLED`, and `failure_reason` always `None`. A `RUNNING` descendant is left untouched (cooperative cancellation is plan 17-06's path). The traversal is idempotent (a second call over an already-cascaded subgraph transitions nothing) and cycle-safe (bounded via a `visited` set even though cycles are structurally unrepresentable).
- `tests/test_job_dependencies.py` (15 tests, all green against a real migrated Postgres database): self-dependency, two-node cycle, three-node cycle, unknown-dependency, dependency dedup, `SUBMITTED` event, zero-dependency readiness, partial-success non-readiness, oldest-first+limit ordering, failed-dependency causal-chain cancellation, three-level transitivity, `DEPENDENCY_CANCELLED` cause, `RUNNING`-descendant preservation, cascade idempotency, and the D-04 anti-stranding invariant (a direct query proving no QUEUED Job remains behind a FAILED/CANCELLED dependency after the cascade).
- Verified `grep -rn "JobDependency(" src/trading_platform/ | grep -v "jobs/dependencies.py"` returns only the `class JobDependency(` definition line in `db/models/job_dependency.py` -- no other module ever instantiates a dependency edge.
- Full suite verified green: 384 passed (369 baseline + 15 new), zero regressions. `ruff check`, `ruff format --check`, and `mypy` all pass clean on both new files.

## Task Commits

Each task was committed atomically (Tasks 1 and 2 both edit the same new file, `dependencies.py`, and were written together, then committed in a single combined commit -- see Deviations):

1. **Tasks 1+2: Submission-time validation, readiness predicate, and cascade** - `da8f6d8` (feat)
2. **Task 3: Dependency gating and cascade tests** - `f26c218` (test)

## Files Created/Modified

- `src/trading_platform/jobs/dependencies.py` - `SelfDependencyError`/`DependencyCycleError`/`UnknownDependencyError`, `_detect_cycle`, `validate_dependency_set`, `submit_job`, `unsatisfied_dependency_exists`, `find_ready_job_ids`, `cascade_dependency_outcome`
- `tests/test_job_dependencies.py` - 15 JOB-05/D-04/D-05/D-06 tests + local `migrated_job_dependencies_db` fixture + `_seed_job`/`_fail_job`/`_cancel_queued_job` helpers

## Decisions Made

- Self-dependency and cycle tests drive `submit_job` through `unittest.mock.patch("trading_platform.jobs.dependencies.uuid.uuid4", return_value=<existing_job_id>)` rather than attempting to force a self/cycle scenario through a caller-supplied ID directly -- `submit_job` generates its own UUID internally, so a real caller can never reference the about-to-be-created Job's own ID in `depends_on`. That impossibility is the literal proof of D-06's "cyclic graphs must never be representable," not a gap the tests need to route around differently.
- `cascade_dependency_outcome`'s BFS queries `(Job.id, Job.status)` per level in a single un-locked `SELECT` joined against `JobDependency`, rather than re-fetching and row-locking each candidate before deciding whether to cascade into it. The authoritative row lock and re-check happen exactly once, inside `apply_job_transition` itself, when a QUEUED descendant is actually being transitioned -- avoiding a redundant double-lock per descendant while still being safe, since `apply_job_transition`'s own `with_for_update=True` is the true serialization point.
- `unsatisfied_dependency_exists` is a pure expression-builder function (never executes a query itself), returning a correlated `EXISTS(...)` built with `sqlalchemy.orm.aliased(Job)` for the dependency-target join -- this is what makes it safe to embed inside both this module's `find_ready_job_ids` and, unmodified, inside plan 17-07's future claim-loop query.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] mypy type mismatch on `unsatisfied_dependency_exists`'s parameter**
- **Found during:** Task 1 (post-implementation `mypy` verification)
- **Issue:** The plan's literal signature (`job_id_column: ColumnElement[uuid.UUID]`) fails mypy when called with `Job.id`, because mypy does not treat SQLAlchemy's `InstrumentedAttribute[UUID]` as a `ColumnElement[UUID]` subtype, even though it satisfies the runtime protocol via `__clause_element__`.
- **Fix:** Changed the parameter type to `Any` (return type remains `ColumnElement[bool]`, preserving the caller-facing contract).
- **Files modified:** `src/trading_platform/jobs/dependencies.py`
- **Verification:** `mypy src/trading_platform/jobs/dependencies.py` → `Success: no issues found in 1 source file`
- **Committed in:** `da8f6d8` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug/typing)
**Impact on plan:** Purely a type-annotation correction with no behavioral change; no scope creep. Additionally, Tasks 1 and 2 were combined into a single commit rather than two separate ones, since both were implemented together in the same new file before either was committed -- no functional deviation, just commit granularity (noted for transparency, not a Rule 1-3 fix).

## Issues Encountered

None. All 15 tests, plus the full 384-test suite, passed on the first clean run after the mypy fix above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `submit_job`, `find_ready_job_ids`, `unsatisfied_dependency_exists`, and `cascade_dependency_outcome` are all importable from `trading_platform.jobs.dependencies` for the remaining Phase 17 plans: plan 17-06 (cancellation) will call `cascade_dependency_outcome` after any FAILED/CANCELLED transition it drives cooperatively; plan 17-07 (queue/claim loop) will call `find_ready_job_ids`/`unsatisfied_dependency_exists` on every poll and `submit_job` from wherever Jobs are externally created; plan 17-08 (read routes) may build a `describe_blocking_dependencies` read helper in `services/job_reads.py`, deliberately NOT added here per the JOB-04 service/jobs import-boundary constraint.
- No code blockers identified for 17-06 onward. Full suite holds at 384/0.

## Requirements Note

This plan's frontmatter lists `requirements: [JOB-05]`, and this plan implements the full mechanism JOB-05 describes -- explicit dependency declaration (`submit_job`/`JobDependency`), a "starts only after all dependencies succeed" readiness predicate (`find_ready_job_ids`), and "a failed dependency moves dependents to a terminal non-executed state" (`cascade_dependency_outcome`, fully tested for causal chain, transitivity, idempotency, and the anti-stranding invariant).

However, per the 17-01/17-03/17-04 precedent (do not mark a requirement Complete until its behavior is verifiable end-to-end, not just unit-tested in isolation), `requirements mark-complete` was deliberately skipped for JOB-05 in this plan. The literal claim "a dependent Job starts only after all dependencies succeed" requires a real claim/execution loop that actually calls `find_ready_job_ids` before claiming a Job -- that loop is plan 17-07's scope (explicitly named in this module's own docstrings: "the queue claim path in plan 17-07 calls it on every poll"). No queue or worker exists yet in this codebase, so today nothing actually enforces dependency-gated claiming end-to-end; only the tested, importable predicate exists. JOB-05 remains `Pending` in `REQUIREMENTS.md`; it should be marked Complete once plan 17-07 wires `find_ready_job_ids` into the real claim path and that path is verified.

---
*Phase: 17-job-framework*
*Completed: 2026-07-19*
