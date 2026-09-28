---
phase: 20-complete-operation-migration-safety-controls
plan: 04
subsystem: jobs
tags: [job-framework, cancellation, domain-conflict, retry, concurrency-guard]

# Dependency graph
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-01: migration 0021 (JobFailureReason.DOMAIN_CONFLICT enum value, jobs.retry_of_job_id UNIQUE) this plan builds on"
provides:
  - "registry.py: JobCancellationMode.QUEUED_ONLY (D-01) -- future paper-session/broker-sync Job types can declare cancellable-only-while-QUEUED semantics"
  - "registry.py: retry_prerequisite_for(spec) + register()-time validation of the optional retry_prerequisite_job_type spec attribute (D-19) -- future retry-gating plans read this instead of re-deriving it"
  - "contracts.py: JobDomainConflictError -- the framework-level signal every domain-conflict-capable handler raises"
  - "jobs/handlers/domain_conflicts.py: DOMAIN_CONFLICT_EXCEPTIONS closed tuple + translate_domain_conflicts() context manager -- future handlers (paper-session) wrap their broker-submission call in this to get the domain_conflict outcome for free"
  - "runner.py: except JobDomainConflictError branch landing FAILED/domain_conflict with outcome_uncertain pinned False, cascading to dependents exactly like handler_error"
affects: [20-16-job-type-registrations, 20-*-remaining-plans]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Framework-level typed-exception translation at the handler boundary: a handler-layer module (jobs/handlers/domain_conflicts.py) owns the closed set of domain exceptions it may translate and does the translation via a context manager, so jobs/runner.py and jobs/contracts.py never import a concrete domain exception (JOB-04) while still getting a dedicated outcome branch."
    - "Optional, non-Protocol spec attributes: retry_prerequisite_job_type follows the same pattern JobSubmissionSpec already established -- read via getattr with a None default in a module function, validated inside JobRegistry.register() when present, without adding a new Protocol member (avoids breaking every existing fake spec in prior-phase tests)."

key-files:
  created:
    - src/trading_platform/jobs/handlers/domain_conflicts.py
  modified:
    - src/trading_platform/jobs/registry.py
    - src/trading_platform/jobs/contracts.py
    - src/trading_platform/jobs/runner.py
    - tests/test_job_registry.py
    - tests/test_job_runner.py
    - tests/test_job_catalog.py

key-decisions:
  - "outcome_uncertain is hardcoded False on the domain_conflict path (not derived from _job_emitted_external_side_effect_log like handler_error) -- justified in a runner.py comment: the only translated conflict (ConcurrentRunLockedError) is raised when the paper-session advisory lock is denied, which structurally precedes any broker order submission (LOCK-01), so no domain_conflict outcome can ever follow a real broker call."
  - "retry_prerequisite_job_type validation lives in JobRegistry.register(), not in JobSubmissionSpec's Protocol definition, consistent with the plan's explicit instruction not to add a new Protocol member (would break six existing fake specs in earlier-phase tests)."
  - "Fixed a pre-existing exact-set pin on JobCancellationMode in tests/test_job_catalog.py (test_cancellation_mode_enum_is_closed) to the 2-value set -- in scope of Task 1's own verify command, not a new file added by this plan's files_modified list, but required for that file to stay green."

patterns-established:
  - "Any future Phase 20 handler that needs to translate a typed domain exception into a Job outcome follows domain_conflics.py's shape: closed tuple + @contextmanager translator, imported only from jobs/handlers/, never from a queue-framework module."

requirements-completed: []  # OPS-08 mechanism now exists (JobDomainConflictError + runner branch) but no handler raises it yet -- no Job type calls translate_domain_conflicts() until a Phase 20 handler (paper-session, Plan 16) wraps run_paper_order_submission. OPS-03/OPS-07 similarly: retry-prerequisite declaration exists but nothing reads it yet (no retry-gating service/route). All three left Pending per the 19-01/20-01/20-02/20-03 precedent of not overclaiming end-to-end behavior at the mechanism/schema layer.

# Metrics
duration: ~20min
completed: 2026-09-28
---

# Phase 20 Plan 04: Job Framework Mechanisms (Cancellation Mode, Domain Conflict, Retry Prerequisite) Summary

**QUEUED_ONLY cancellation mode, a typed JobDomainConflictError translated from ConcurrentRunLockedError by a new jobs/handlers/domain_conflicts.py module, a runner.py outcome branch landing FAILED/domain_conflict with outcome_uncertain pinned False, and registry.retry_prerequisite_for/register() validation for the optional D-19 retry-prerequisite spec attribute.**

## Performance

- **Duration:** ~20 min (commit-to-commit)
- **Completed:** 2026-09-28
- **Tasks:** 2
- **Files modified:** 1 created, 5 modified

## Accomplishments
- `JobCancellationMode` gained `QUEUED_ONLY = "queued_only"` (D-01), with its docstring documenting both members' semantics; the closed 2-value set is pinned in both `tests/test_job_registry.py` and `tests/test_job_catalog.py`.
- `registry.retry_prerequisite_for(spec)` reads the optional `retry_prerequisite_job_type` spec attribute (D-19); `JobRegistry.register()` validates it (None or nonblank string) before either registry mapping changes, without adding a new `JobSubmissionSpec` Protocol member.
- `JobDomainConflictError(message)` added to `jobs/contracts.py` as the framework-level domain-conflict signal.
- New `jobs/handlers/domain_conflicts.py` holds the closed `DOMAIN_CONFLICT_EXCEPTIONS = (ConcurrentRunLockedError,)` tuple and a `translate_domain_conflicts()` context manager that catches it and re-raises `JobDomainConflictError` with `__cause__` preserved; this keeps `ConcurrentRunLockedError` out of `jobs/runner.py` and `jobs/contracts.py` (JOB-04) — it is referenced only under `jobs/handlers/`.
- `jobs/runner.py` gained an `except JobDomainConflictError` branch (before the generic `except Exception`) and a `domain_conflict` terminal-write block that lands the Job FAILED with `failure_reason=JobFailureReason.DOMAIN_CONFLICT`, `outcome_uncertain=False` (always, even after an `external_*` log), and cascades to unstarted dependents exactly like the `handler_error` path.

## Task Commits

Each task was committed atomically:

1. **Task 1: QUEUED_ONLY mode, retry-prerequisite declaration, JobDomainConflictError + handler translation module** - `fb92b5e` (feat)
2. **Task 2: Runner domain_conflict outcome branch** - `e28d0dc` (feat)

**Plan metadata:** (this commit, to follow)

## Files Created/Modified
- `src/trading_platform/jobs/registry.py` - `JobCancellationMode.QUEUED_ONLY`; `retry_prerequisite_for(spec)`; `register()` validation of `retry_prerequisite_job_type`; `build_default_registry` docstring updated to point at Plan 16
- `src/trading_platform/jobs/contracts.py` - `JobDomainConflictError(message)`
- `src/trading_platform/jobs/handlers/domain_conflicts.py` (new) - `DOMAIN_CONFLICT_EXCEPTIONS`, `translate_domain_conflicts()`
- `src/trading_platform/jobs/runner.py` - imports `JobDomainConflictError`; new `except JobDomainConflictError` branch in the handler try-block; new `domain_conflict` terminal-write block
- `tests/test_job_registry.py` - 11 new tests: closed cancellation-mode set, `retry_prerequisite_for` (absent/declared), `register()` accept/reject matrix for `retry_prerequisite_job_type`, `JobDomainConflictError` message/str, `DOMAIN_CONFLICT_EXCEPTIONS` exact tuple, `translate_domain_conflicts` translate/pass-through
- `tests/test_job_runner.py` - 3 new tests: domain_conflict failure_reason/message/outcome_uncertain + single terminal event, outcome_uncertain still False after an `external_*` log, cascade to an unstarted dependent
- `tests/test_job_catalog.py` - `test_cancellation_mode_enum_is_closed` updated to the 2-value set (pre-existing exact-set pin, in scope of Task 1's own verify command)

## Decisions Made
- `outcome_uncertain` is pinned `False` unconditionally on the `domain_conflict` path rather than derived from `_job_emitted_external_side_effect_log` (the `handler_error` convention) — documented inline in `runner.py`: the only currently-translated conflict is raised when the paper-session advisory lock (`session_run_lock`) is denied, which happens before `run_paper_order_submission` ever calls the broker, so a domain conflict structurally cannot follow a real broker order submission (LOCK-01), and D-19's reconcile-first retry block is never falsely triggered.
- `retry_prerequisite_job_type` validation was added to `JobRegistry.register()` rather than the `JobSubmissionSpec` Protocol, per the plan's explicit instruction — a new Protocol member would break every existing fake submission spec across prior-phase tests that don't declare it.
- Fixed `tests/test_job_catalog.py::test_cancellation_mode_enum_is_closed`'s pre-existing 1-value exact-set pin to the new 2-value set — required for Task 1's own verify command (`tests/test_job_catalog.py`) to pass; not a new capability, a necessary consequence of D-01.

## Deviations from Plan

None - plan executed exactly as written. The `test_job_catalog.py` fix above was anticipated by the plan's `<read_first>` instruction to check "any exact-set assertion on JobCancellationMode" and is a mechanical consequence of the closed-enum change, not a new decision.

## Issues Encountered
None. No auth gates, no checkpoints (plan is fully autonomous, no `checkpoint:*` tasks).

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- The three framework mechanisms (`QUEUED_ONLY`, `JobDomainConflictError`/`translate_domain_conflicts`, `retry_prerequisite_for`) are ready for the Job types that need them: a future paper-session handler (Plan 16) can wrap its `run_paper_order_submission` call in `translate_domain_conflicts()` to get the domain_conflict outcome automatically; a future retry-gating service/route can call `retry_prerequisite_for(spec)` to decide whether a FAILED/outcome_uncertain Job of a given type needs a successful reconciliation first before it may be retried.
- OPS-08, OPS-03, OPS-07 correctly stay `Pending` in REQUIREMENTS.md — this plan ships only the framework-level mechanism (typed signal, runner branch, declaration API); no handler raises `JobDomainConflictError` yet and no retry-gating consumer reads `retry_prerequisite_for` yet. Mark each requirement Complete at the plan that wires the actual behavior (handler registration is explicitly Plan 16's scope per `build_default_registry`'s updated docstring).
- Full suite verified green at 656 passed (0 failed), up from the 642-pass pre-plan baseline (14 new tests: 11 in `test_job_registry.py`, 3 in `test_job_runner.py`).

## Self-Check: PASSED

- FOUND: src/trading_platform/jobs/handlers/domain_conflicts.py
- FOUND (modified): src/trading_platform/jobs/registry.py
- FOUND (modified): src/trading_platform/jobs/contracts.py
- FOUND (modified): src/trading_platform/jobs/runner.py
- FOUND (modified): tests/test_job_registry.py
- FOUND (modified): tests/test_job_runner.py
- FOUND (modified): tests/test_job_catalog.py
- FOUND commit fb92b5e (Task 1)
- FOUND commit e28d0dc (Task 2)

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*
