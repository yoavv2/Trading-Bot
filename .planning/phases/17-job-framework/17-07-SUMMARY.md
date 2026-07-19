---
phase: 17-job-framework
plan: 07
subsystem: infra
tags: [sqlalchemy, postgresql, skip-locked, lease-expiry, python]

# Dependency graph
requires:
  - phase: 17-03
    provides: "apply_job_transition -- the single guarded writer of Job.status"
  - phase: 17-05
    provides: "unsatisfied_dependency_exists / cascade_dependency_outcome -- the readiness predicate and dependency-outcome cascade this plan's claim/reclaim loop reuses verbatim"
provides:
  - "claim_next_job -- SKIP LOCKED claim query making concurrent double-claim structurally impossible, gated on readiness (unsatisfied_dependency_exists) and D-07 (cancellation_requested_at IS NULL)"
  - "renew_lease -- owner-scoped lease extension returning False once a sweep has reclaimed the lease out from under a worker"
  - "find_lost_job_ids / reclaim_lost_jobs -- lease-expiry crash detection and idempotent, audited, never-requeuing reclaim with dependency cascade"
affects: [17-08, 17-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "SELECT ... FOR UPDATE SKIP LOCKED as the concurrent-claim serialization primitive, composed directly with the existing unsatisfied_dependency_exists correlated-EXISTS expression in one locked statement"
    - "threading.Thread + join(timeout=...) + is_alive() as the non-blocking regression-guard shape for proving SKIP LOCKED never blocks, instead of an elapsed-wall-clock assertion"
    - "Direct-seed of a lost RUNNING Job (bypassing the CLAIMED transition) in tests, keeping audit-event-count assertions unambiguous"

key-files:
  created:
    - src/trading_platform/jobs/queue.py
    - tests/test_stale_job_reclaim.py
  modified: []

key-decisions:
  - "claim_next_job locks the outer Job row (correlated-EXISTS readiness subquery is not itself locked -- FOR UPDATE only ever applies to the driving table), matching the precedent that FOR UPDATE against a query with a joined/nullable side is unsafe in Postgres; readiness stays a single reusable expression-builder call rather than an inline join"
  - "reclaim_lost_jobs re-fetches each lost Job under with_for_update=True and re-checks status==RUNNING and lease_expires_at < now before transitioning it, even though the plan's literal task text only required find_lost_job_ids-driven iteration -- a defensive re-check under the lock (Rule 2) so two concurrent reclaim sweeps racing the same lost Job cannot both attempt the transition against a stale read"
  - "Crash-recovery tests seed a lost Job directly with status=RUNNING and an expired lease rather than driving it there via apply_job_transition(CLAIMED), so the only JobEvent a reclaim produces is LEASE_EXPIRED itself -- keeping 'exactly one audit event' and 'idempotent reclaim leaves exactly one event' assertions unambiguous"
  - "The concurrency-safety test opens session_one directly from get_session_factory() (not session_scope) so its transaction can be held open across the thread boundary and rolled back deliberately afterward, proving the skipped row becomes claimable again once the first transaction ends"

requirements-completed: [JOB-02]

# Metrics
duration: ~30min
completed: 2026-07-19
---

# Phase 17 Plan 07: Restart-Safe Job Claim/Lease Queue Summary

**`jobs/queue.py` ships JOB-02's persistence half -- a `SELECT ... FOR UPDATE SKIP LOCKED` claim query that makes double-claiming structurally impossible, plus lease-expiry crash detection and an idempotent, audited, never-requeuing reclaim path that cascades to unstarted dependents -- pinned by 14 tests against a real Postgres database, including a two-connection concurrency proof.**

## Performance

- **Duration:** ~30 min
- **Tasks:** 2 completed
- **Files modified:** 2 (both created)

## Accomplishments

- `claim_next_job` selects the oldest ready QUEUED Job under `SELECT ... FOR UPDATE SKIP LOCKED`, filtered on `cancellation_requested_at IS NULL` (D-07) and `~unsatisfied_dependency_exists(Job.id)` reused verbatim from `jobs/dependencies.py` -- readiness has exactly one SQL definition in the codebase, now actually driving the claim path plan 17-05 anticipated. On a hit it sets `lease_owner`/`lease_expires_at`/`heartbeat_at` and calls `apply_job_transition(CLAIMED)`, landing the Job on RUNNING with `started_at` stamped.
- `renew_lease` extends a RUNNING Job's lease only when the caller's `worker_id` still matches `lease_owner`, returning `False` (never raising) once a sweep has already reclaimed the lease -- the exact signal plan 17-09's runner needs to stop working on a Job it no longer owns.
- `find_lost_job_ids` is a single-query detector mirroring `find_stale_runs`: RUNNING Jobs whose `lease_expires_at` has lapsed, with no separate liveness protocol needed since a dead worker simply cannot renew.
- `reclaim_lost_jobs` transitions each lost Job via `apply_job_transition(LEASE_EXPIRED)` -- landing on FAILED, never CANCELLED (D-01) -- with `outcome_uncertain` forced `True` by `lifecycle.py` itself (D-03), clears the lease fields, and calls `cascade_dependency_outcome` so unstarted dependents are never stranded behind a crashed ancestor (D-04). Idempotent by construction: once a Job leaves RUNNING it no longer matches the detector, so a second pass is a safe no-op. There is no code path back to QUEUED anywhere in this module (D-02).
- `tests/test_stale_job_reclaim.py` (14 tests, all green against a real migrated Postgres database): oldest-first claim ordering, RUNNING+lease+CLAIMED-audit assertions, the JOB-02 no-duplication proof (two genuinely separate connections, a held-open transaction, a `threading.Thread` + `join(timeout=10)` + `is_alive()` non-blocking assertion -- no elapsed-wall-clock check anywhere), dependency-gated and cancellation-gated claim skipping, lease-renewal ownership (including the already-reclaimed case), lease-expiry detection, FAILED-with-audit reclaim (explicit `is not JobStatus.CANCELLED` assertion), `outcome_uncertain=True`, idempotent reclaim (empty second pass, exactly one JobEvent), never-requeues (table-wide QUEUED count unchanged), dependency cascade to CANCELLED with `root_cause_job_id` and `DEPENDENCY_FAILED` cause, and D-12 progress preservation (`progress_percent == 60` survives reclaim).
- Verified `grep -c "\.status = " src/trading_platform/jobs/queue.py` returns 0 (every status change routes through `apply_job_transition`), `grep -c "skip_locked=True"` returns 1, `grep -c "session.commit()"` returns 0, `grep -c "get_engine\|create_engine\|get_session_factory"` returns 0, `grep -c "unsatisfied_dependency_exists"` returns 3 with zero `"not exists"` hits (readiness SQL not duplicated), and `grep -rn "lease_owner = " src/trading_platform/ | grep -v "jobs/queue.py"` returns nothing -- leases are managed in exactly one module.
- Full suite verified green: 413 passed (399 baseline + 14 new). One unrelated `test_market_data_ingestion.py` error surfaced only under full-suite parallel load (the documented pre-existing `pg_terminate_backend`/`InsufficientPrivilege` flake from STATE.md's Blockers section) and passed cleanly in isolation -- not a regression from this plan.

## Task Commits

Each task was committed atomically:

1. **Task 1: Claim/lease primitives and lost-worker reclaim** - `a231ca1` (feat)
2. **Task 2: Claim-safety and crash-recovery tests** - `ef12e87` (test)

## Files Created/Modified

- `src/trading_platform/jobs/queue.py` - `LEASE_SECONDS`/`HEARTBEAT_SECONDS`/`POLL_INTERVAL_SECONDS`, `claim_next_job`, `renew_lease`, `find_lost_job_ids`, `reclaim_lost_jobs`
- `tests/test_stale_job_reclaim.py` - 14 JOB-02/D-01/D-02/D-03/D-04/D-07/D-12 tests + local `migrated_job_queue_db` fixture + `_seed_job`/`_seed_lost_job` helpers

## Decisions Made

- `claim_next_job` locks only the outer `Job` row via `.with_for_update(skip_locked=True)` on a `select(Job)` statement whose `WHERE` clause embeds `unsatisfied_dependency_exists` as a correlated `EXISTS` subquery (not a join) -- this is what keeps `FOR UPDATE` valid and scoped to the single row a worker is claiming, per the plan's explicit "readiness is a correlated subquery, so FOR UPDATE locks only the outer jobs row" guidance.
- `reclaim_lost_jobs` re-fetches each candidate under `with_for_update=True` and re-validates `status == RUNNING` and `lease_expires_at < now` before transitioning it, beyond the plan's literal text -- a small Rule 2 defensive addition so two reclaim sweeps racing the same lost Job (a realistic scenario once multiple workers/schedulers exist) cannot both attempt a transition against a stale unlocked read. This does not change any tested behavior (all 14 tests pass whether or not the re-check is present) but closes a latent race.
- The concurrency-safety test rolls back (never commits) both the first worker's held-open transaction and the thread's own transaction, since committing the first claim would leave the Job genuinely RUNNING and falsify the "same Job becomes claimable again" assertion -- only rollback demonstrates the row was *skipped*, not durably claimed, by session two.

## Deviations from Plan

None - plan executed exactly as written. The one addition beyond the plan's literal task text (the defensive re-check inside `reclaim_lost_jobs`, noted above under Decisions Made) is a Rule 2 correctness hardening, not a scope or behavior change relative to what the plan's acceptance criteria and tests require.

## Issues Encountered

None. Both tasks passed all acceptance-criteria greps and the full test run on the first clean pass.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `claim_next_job`, `renew_lease`, `find_lost_job_ids`, `reclaim_lost_jobs`, and the `LEASE_SECONDS`/`HEARTBEAT_SECONDS`/`POLL_INTERVAL_SECONDS` constants are all importable from `trading_platform.jobs.queue` for plan 17-09's worker runner: the runner loop calls `claim_next_job` on its poll cadence, `renew_lease` on the heartbeat cadence (stopping work when it returns `False`), and `reclaim_lost_jobs`/`sweep_cancellation_timeouts` (from 17-06) together on the same sweep cadence.
- JOB-02 is marked Complete in `REQUIREMENTS.md` (this plan's sole frontmatter requirement) -- both halves of its literal claim are now code-real and tested: "a queued job submitted before a worker restart executes after it" (claim survives process restart because it is pure PostgreSQL row state, no in-memory queue) and "a running job interrupted by crash is detected and moved to a terminal state, never silently lost or duplicated" (lease-expiry detection + `reclaim_lost_jobs`, plus the two-connection `SKIP LOCKED` proof for the duplication half).
- JOB-05's previously-noted gap ("the readiness predicate is unit-tested but not yet called by any real claim/execution loop") is now closed in code and test (`test_claim_skips_job_with_unsatisfied_dependency` exercises `claim_next_job` -> `unsatisfied_dependency_exists` end-to-end), but `requirements mark-complete` was intentionally NOT run for JOB-05 here since this plan's frontmatter declares only `[JOB-02]` -- flagging this as a tracking note for the orchestrator/`/gsd-transition` rather than overclaiming outside this plan's declared scope, consistent with the 16-02/11-03 precedent.
- JOB-06 remains Pending: `acknowledge_cancellation`/`sweep_cancellation_timeouts` (from 17-06) are still only exercised by unit tests -- no production runner calls them yet. That wiring is plan 17-09's scope.
- No code blockers identified for 17-08/17-09. Full suite holds at 413/414 (one documented pre-existing environmental flake, confirmed to pass in isolation).

---
*Phase: 17-job-framework*
*Completed: 2026-07-19*

## Self-Check: PASSED

Both created files verified present on disk (`src/trading_platform/jobs/queue.py`, `tests/test_stale_job_reclaim.py`); all three commit hashes (a231ca1, ef12e87, a14d3c3) verified present in git log.
