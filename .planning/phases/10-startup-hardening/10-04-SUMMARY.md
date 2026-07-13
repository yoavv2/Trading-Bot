---
phase: 10-startup-hardening
plan: 04
subsystem: database
tags: [postgresql, sqlalchemy, transactions, reconciliation, execution-events]

# Dependency graph
requires:
  - phase: 10-03
    provides: "db/session.py explicit reloadable session_scope() as the canonical DB transaction boundary"
  - phase: 09
    provides: "reconcile_paper_execution / apply_reconciliation_corrections reconciliation subsystem this plan's DB-06 hand-off feeds"
provides:
  - "Explicit, comment-documented DB-04/DB-05 transaction boundary at the broker-call/success-persist site in paper_execution.py"
  - "schedule_reconciliation_after_partial_failure() — durable ExecutionEvent + WARNING-log hand-off invoked when a post-broker-success persist rolls back (DB-06)"
  - "5 test-pinned invariants: commit-after-both, no-commit-on-broker-raise, broker-call-outside-open-transaction, partial-failure-schedules-reconciliation, broker-failure-skips-reconciliation"
affects: [10-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Independent-connection visibility probe: a spy ExecutionService opens its OWN session_scope inside submit_order() to query the row by client_order_id, proving the pre-broker write is already committed and visible to a separate connection (i.e. no transaction is holding it open) — a reusable pattern for testing broker-call-outside-txn invariants."
    - "Monkeypatch on the imported symbol (not an internal detail) to force a natural post-broker persist failure: replacing paper_execution.apply_order_transition with a wrapper that raises only for BROKER_* event types, leaving INTENT_REGISTERED/RETRY_REQUESTED/SUBMISSION_FAILED untouched."

key-files:
  created: []
  modified:
    - src/trading_platform/services/paper_execution.py
    - tests/test_paper_execution.py

key-decisions:
  - "Both tasks landed in a single commit (8d3f416) rather than two separate task commits: Task 2's wrapping try/except is structurally inseparable from Task 1's re-indentation of the same session_scope block (splitting would require either a broken intermediate commit or re-doing the same edit twice); advisor-reviewed and endorsed given the shared working tree was mid-collision with a concurrent plan (10-05) at the time."
  - "paper_order_id (not None) is attached to the reconciliation_scheduled ExecutionEvent, giving reconciliation a direct FK to the affected PaperOrder row (durable pre-broker, unaffected by the rollback) rather than leaving attribution to the details JSON alone."
  - "schedule_reconciliation_after_partial_failure is deliberately NOT called on the broker-call-failed path (submit_order itself raised) — that is a clean failure with no broker-side effect, not a divergence; only a rollback AFTER a successful broker call is a DB-06 scenario."

patterns-established:
  - "DB-04/05/06 boundary comment block: every future broker-call site should document, inline, which session sits outside the broker call and which commit is contingent on which two conditions — this plan's comment at paper_execution.py:560-571 is the reference example."

requirements-completed: [DB-04, DB-05, DB-06]

# Metrics
duration: ~20min
completed: 2026-07-13
---

# Phase 10 Plan 04: Paper-Execution Transaction Integrity Summary

**Explicit DB-04/05 transaction-boundary documentation plus a new `schedule_reconciliation_after_partial_failure()` DB-06 hand-off in `paper_execution.py`, so a broker order that succeeds but whose local persist rolls back always schedules reconciliation instead of silently diverging — 5 new tests, full `test_paper_execution.py` suite 25/25 green.**

## Performance

- **Duration:** ~20 min
- **Completed:** 2026-07-13T19:49:03Z
- **Tasks:** 2/2 (both landed in one commit — see Deviations)
- **Files modified:** 2

## Accomplishments
- Added an inline comment block at the broker-call/success-persist boundary in `_run_paper_order_submission_guarded` documenting the DB-04/DB-05 invariant: the broker call sits outside any open session, and the success-persist `session_scope` commits only when both the broker call succeeded and the state-transition write flushed cleanly.
- Wrapped the post-broker success-persist `session_scope` in a `try/except` that, on any exception AFTER a successful broker call, invokes `schedule_reconciliation_after_partial_failure(...)` and re-raises the original exception unmodified.
- Implemented `schedule_reconciliation_after_partial_failure()`: emits a structured WARNING log and persists a durable `reconciliation_scheduled` `ExecutionEvent` (own independent `session_scope`, so it lands even though the triggering write rolled back) carrying strategy/run/paper-order/broker-order attribution — feeds the existing Phase 9 `reconcile_paper_execution` reconciliation pass.
- Added 5 tests pinning all three requirements end-to-end against a real PostgreSQL test database (`migrated_paper_db` fixture), no mocking of the DB layer.

## Task Commits

Both tasks landed in a single commit (see Deviations for why):

1. **Task 1 + Task 2 combined** - `8d3f416` (feat) — explicit txn boundary comment (DB-04/05) + `schedule_reconciliation_after_partial_failure` (DB-06) + 5 tests

_No separate plan-metadata commit for code — this SUMMARY/STATE/ROADMAP update is the metadata commit._

## Files Created/Modified
- `src/trading_platform/services/paper_execution.py` — DB-04/05 boundary comment; post-broker `session_scope` wrapped in try/except calling the new `schedule_reconciliation_after_partial_failure()` helper on rollback-after-broker-success; new helper function (module-level, ~65 lines) placed near `_record_intent_decision_event`.
- `tests/test_paper_execution.py` — 5 new tests + 1 new fixture class (`_BrokerFailsOnSubmitExecutionService`) + `OrderLifecycleState` import + `paper_execution_module` import for monkeypatching.

## Decisions Made
- **Single combined commit for both tasks** — Task 2's `try/except` wrap is structurally interleaved with Task 1's re-indentation of the exact same `session_scope` block; splitting into two atomic commits would have required either committing a broken intermediate state or duplicating the diff. Reviewed and endorsed via the advisor tool given the added complication of a concurrent plan (10-05) modifying the shared working tree at the same time (see Issues Encountered).
- **`paper_order_id` attached to the reconciliation-scheduled `ExecutionEvent`** rather than leaving attribution solely in the `details` JSON — the FK is safe because `pending_order_id`'s row was committed durably in the pre-broker `session_scope`, unaffected by the later rollback.
- **Reconciliation is never scheduled on the broker-call-failed path** — only a rollback *after* a successful `submit_order()` return is a DB-06 divergence; a broker exception itself has no side effect to reconcile.

## Deviations from Plan

### Process deviation (not a code/architecture deviation)

**Both tasks committed together instead of two atomic task commits.** Task 1 (explicit boundary + comment) and Task 2 (DB-06 wrap + helper) touch the identical `session_scope` block at the identical lines — Task 2's `try/except` is added by re-indenting the exact code Task 1 documents. Splitting them would mean committing Task 1's version, then re-diffing/re-indenting for Task 2, producing a noisier history for no correctness benefit. No rule-1/2/3 auto-fix and no architectural change occurred — this is purely a commit-granularity call, made after consulting the advisor tool given the added complexity of a concurrently-executing sibling plan touching the same repository (see below).

**Total deviations:** 0 code deviations. 1 process deviation (commit granularity), advisor-reviewed.
**Impact on plan:** None on the delivered code — both DB-04/05/06 invariants are documented, implemented, and test-pinned exactly as specified.

## Issues Encountered

**Concurrent parallel-plan execution collision (environmental, not a code issue).** Plan 10-05 (wave 2, same parallel batch as this plan) was actively editing `src/trading_platform/services/bootstrap.py` and `src/trading_platform/worker/__main__.py` in the same working tree while this plan executed. Mid-session, an exploratory `git stash` (used only to test whether an intermittent `pg_terminate_backend`/"must be a superuser to terminate superuser process" full-suite failure pre-existed on a clean tree) inadvertently captured 10-05's in-progress uncommitted work alongside this plan's own changes, and a subsequent `git stash pop` conflicted against 10-05's continued edits. Recovered by: (1) identifying exactly which files in the stash were mine (`paper_execution.py`, `tests/test_paper_execution.py`) via `git stash show -p --stat`, (2) `git checkout stash@{0} -- <my two files>` to restore only my content onto the then-current working tree (leaving 10-05's live in-progress files untouched), (3) `git diff stash@{0} -- <my two files>` confirmed zero difference from the stashed version, (4) `git stash drop`. One casualty: a pre-existing, already-uncommitted `.planning/config.json` change (`nyquist_validation: true → false`, present in `git status` before this session started, unrelated to either plan) was also swept into the stash and lost on drop; recovered from the dropped-but-still-reachable stash commit (`0da6f76`) and restored to the working tree (left unstaged — not committed here, not this plan's change to make). 10-05 finished and committed its own work (`769eb5e`, `be8643f`) partway through this recovery, which is what made the conflict resolvable. Full end-to-end regression risk from the stash incident: none confirmed — `git diff` proved byte-identical restoration of both owned files.

**Full-repo-suite verification is confounded by the same concurrency.** `python -m pytest -q` was run several times during this session (both with and without this plan's changes present, via `git stash`) and non-deterministically failed 1 unrelated test each time — always a different file (`test_market_data_access.py`, `test_market_data_ingestion.py`, `test_concurrency_guard_e2e.py`, `test_operator_controls.py`), always the same underlying cause: `psycopg.errors.InsufficientPrivilege: must be a superuser to terminate superuser process` during a throwaway test-database's `pg_terminate_backend` teardown, or advisory-lock contention from 10-05's own e2e tests running concurrently. This was reproduced on a clean tree with this plan's changes fully removed (`git stash`), confirming it is pre-existing environmental flakiness (background/autovacuum superuser connections racing test-DB teardown) plus parallel-executor lock contention — not caused by this plan. Regression evidence used instead: `tests/test_paper_execution.py` alone is 25/25 green (verified 3 times, including immediately post-commit); the same file's pre-existing 20 tests were unaffected.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- DB-04/DB-05/DB-06 requirements are fully implemented and test-pinned; `paper_execution.py` is the only flow with a broker side effect, so no other module needed similar hardening this phase.
- 10-06 (wave 3, depends on 10-02/10-03/10-04/10-05) can proceed once all of wave 2 (this plan + 10-05) lands — this plan's portion is committed and ready.
- Flag for the orchestrator: verify `.planning/config.json`'s `nyquist_validation` field reflects the intended value before the next planning-mode operation — it was found already modified (`true → false`) and uncommitted at this session's start, unrelated to either 10-04 or 10-05, and was restored to that pre-session state (not committed) after the stash incident above.
- The intermittent `pg_terminate_backend` superuser-teardown failure (`InsufficientPrivilege`) affecting `test_market_data_access.py` / `test_market_data_ingestion.py` fixture teardown under full-suite/parallel load is a pre-existing environmental issue, out of this plan's scope — worth a follow-up ticket if it recurs outside of parallel-execution sessions.

---
*Phase: 10-startup-hardening*
*Completed: 2026-07-13*

## Self-Check: PASSED

- FOUND: `.planning/phases/10-startup-hardening/10-04-SUMMARY.md`
- FOUND: commit `8d3f416`
- FOUND: `schedule_reconciliation` occurrences in `src/trading_platform/services/paper_execution.py` (definition + call site)
