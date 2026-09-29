---
phase: 20-complete-operation-migration-safety-controls
part: A-jobs
fixed_at: 2026-09-28T00:00:00Z
review_path: .planning/phases/20-complete-operation-migration-safety-controls/20-REVIEW-part-A-jobs.md
iteration: 1
findings_in_scope: 6
fixed: 5
skipped: 1
status: partial
---

# Phase 20 (Part A: Jobs): Code Review Fix Report

**Fixed at:** 2026-09-28
**Source review:** .planning/phases/20-complete-operation-migration-safety-controls/20-REVIEW-part-A-jobs.md
**Iteration:** 1

**Summary:**
- Findings in scope: 6 (CR-A-01, WR-A-01..05; Info out of scope)
- Fixed: 5 (WR-A-03 only partially, see below)
- Skipped: 1 (WR-A-02; amended 2026-09-29: decided by the user as D-08a and implemented in plan 20-25)

Each fix has its own commit on `main` and a regression test that failed before the fix and passes after it.

## Fixed Issues

### CR-A-01: Out-of-calendar `as_of_session` escapes `validate_payload` as an untyped exception

**Files modified:** `src/trading_platform/jobs/handlers/payload_fields.py`, `src/trading_platform/jobs/handlers/risk_evaluation_submission.py`, `src/trading_platform/jobs/handlers/paper_session_submission.py`, `src/trading_platform/jobs/handlers/reconciliation_submission.py`, `src/trading_platform/jobs/handlers/broker_order_sync_submission.py`, plus tests (`test_job_payload_fields.py`, `test_job_orchestration.py`, and the four per-type job tests)
**Commit:** 84e1b85
**Applied fix:** `require_trading_session_not_future` now catches `ValueError`/`OverflowError` (which cover `DateOutOfBounds`) around `is_trading_session` and raises `InvalidJobPayloadError` with a new closed value `as_of_session_out_of_calendar_range`. The value was added to `PayloadFieldRejection` and to the four session-scoped spec enums. The console renders the reason generically, so no console change was needed. Tests use `2000-01-03` and `0001-01-01` at helper level, at spec level for all four types, and through `JobOrchestrationService.submit` and `.retry` with the default registry (typed rejection, zero rows written).
**Status:** fixed (a new closed rejection code was chosen over reusing `as_of_session_not_trading_session`, so operators can tell "outside the calendar window" from "not a session").

### WR-A-01: Concurrent same-key retry returns `retry_exists` instead of an idempotent replay

**Files modified:** `src/trading_platform/orchestration/job_mutations.py`, `tests/test_job_orchestration.py`
**Commit:** 3ee81a9
**Applied fix:** `retry()` re-runs `_existing_outcome` right after taking the row lock on the original Job, before the status and existing-retry checks. The test runs retry A to completion, blinds the first replay lookup of retry B (simulating the pre-lock check missing A's commit), and asserts B replays instead of raising `RetryAlreadyExistsError`. It fails without the fix.
**Status:** fixed: requires human verification (concurrency logic. The regression test simulates the race deterministically rather than running two real connections.)

### WR-A-03: Date ranges unbounded, pre-window dates fail at run time (PARTIAL)

**Files modified:** `src/trading_platform/jobs/handlers/payload_fields.py`, `src/trading_platform/jobs/handlers/sync_market_sessions_submission.py`, `tests/test_job_payload_fields.py`, `tests/test_sync_market_sessions_job_type.py`
**Commit:** f902aa5
**Applied fix:** Added `require_date_range_within_calendar` and a closed `date_range_out_of_calendar_range` rejection. `sync-market-sessions` now rejects, at submit, a range whose `from_date` precedes the calendar's `first_session` (or whose `to_date` exceeds `last_session`), instead of landing FAILED with a raw `DateOutOfBounds`/`OverflowError` at run time. Tests cover the window edge, a pre-window date, and year 1.
**Not done:** the `ingest-bars` lookback/span cap. It needs a config value and a product decision on the maximum, which is outside a conservative fixer's remit. `ingest-bars` still accepts arbitrarily old `from_date` values. Recommend a follow-up decision.

### WR-A-04: Migration `downgrade()` fails once any paper-session Job has run

**Files modified:** `alembic/versions/0021_phase20_operations_safety.py`, `tests/test_phase20_operations_migration.py`
**Commit:** 65a964e
**Applied fix:** `upgrade()` is untouched. `downgrade()` now begins with a guard (skipped in offline mode) that raises a clear `RuntimeError` when any `job_id` links more than one `strategy_runs` row, before any DDL runs. The module docstring documents that dropping `market_data_ingestion_runs.job_id` is lossy. The test seeds a Job with two runs, asserts the refusal, and checks that the schema, `alembic_version` and rows are unchanged.
**Status:** fixed: requires human verification (the guard refuses rather than auto-NULLing links, which is the conservative choice. Operators must resolve the shared links manually before downgrading.)

### WR-A-05: Runner hard-codes `outcome_uncertain=False` for every `domain_conflict`

**Files modified:** `src/trading_platform/jobs/contracts.py`, `src/trading_platform/jobs/handlers/domain_conflicts.py`, `src/trading_platform/jobs/runner.py`, `tests/test_job_runner.py`, `tests/test_job_registry.py`
**Commit:** 036084f
**Applied fix:** `JobDomainConflictError` gained a keyword `outcome_uncertain` (default `True`, the safe direction). `domain_conflicts.py` now owns a `DOMAIN_CONFLICT_OUTCOME_UNCERTAIN` mapping (`ConcurrentRunLockedError: False`, with the LOCK-01 rationale). `DOMAIN_CONFLICT_EXCEPTIONS` is derived from it, so adding a member forces an explicit certainty decision. The runner records `exc.outcome_uncertain` and no longer assumes one. The existing probe handlers now pass `outcome_uncertain=False` explicitly. New tests cover the default-uncertain landing, the mapping/tuple parity, and the translator asserting `False`.
**Status:** fixed: requires human verification (safety-semantics change. Behavior for the only current translated exception is unchanged.)

## Skipped Issues

### WR-A-02: `ingest-bars` records a fully or partially failed ingestion as SUCCEEDED

**File:** `src/trading_platform/jobs/handlers/ingest_bars.py:89-96`
**Reason:** Requires a product/design decision. The handler's documented design, referencing D-08/D-09, is that it "never reinterprets a partial symbol failure" and mirrors `IngestionResult.succeeded` in `ingestion_succeeded`, with per-symbol failure detail kept on the linked `MarketDataIngestionRun`. It is consistent with invariant 2 and D-05 ("Jobs never reinterpret domain outcomes"). Failing the Job on an all- or partial-symbols-failed ingest would reverse that design and would change what the Phase 21 failure indicator sees. There is no safe minimal change. Whether `sync-symbol-metadata` and `ingest-bars` should share failure semantics needs a human call, recorded in CONTEXT.
**Original issue:** A bad or expired Polygon key yields a SUCCEEDED Job with `ingestion_succeeded: false` and `bars_upserted: 0`, unlike the retired CLI's `sys.exit(1)` and unlike `sync-symbol-metadata`.

**Amendment 2026-09-29:** decided by the user after UAT. See 20-CONTEXT.md D-08a. The all-fail case is now FAILED (plan 20-25). The partial case stays SUCCEEDED. D-08/D-09 never contained the cited rule.

---

_Fixed: 2026-09-28_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
