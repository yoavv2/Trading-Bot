---
phase: 20-complete-operation-migration-safety-controls
fixed_at: 2026-09-29
review_path: 20-REVIEW.md
fix_scope: critical_warning
iteration: 1
findings_in_scope: 22
fixed: 21
skipped: 1
status: partial
parts:
  - 20-REVIEW-FIX-part-A-jobs.md
  - 20-REVIEW-FIX-part-B-services-api.md
  - 20-REVIEW-FIX-part-C-console.md
verification: "Full gate after all fixes: 997 pytest, 328 vitest, tsc + eslint clean"
---

# Phase 20 Code Review Fix (merged)

Scope was Critical + Warning: 21 review findings plus WR-C-09, which the orchestrator added to give console copy to the new backend error codes. Each fix has its own `fix(20): <ID>` commit and a regression test that fails before the fix and passes after it.

| Part | In scope | Fixed | Skipped | Status |
|------|----------|-------|---------|--------|
| A: jobs, orchestration, migration | 6 | 5 | 1 | partial |
| B: services, API, worker | 7 | 7 | 0 | all_fixed |
| C: console (+WR-C-09) | 9 | 9 | 0 | all_fixed |
| **Total** | **22** | **21** | **1** | **partial** |

## Critical findings (both fixed)
- **CR-A-01:** an `as_of_session` outside the calendar now returns a typed 422 (`as_of_session_out_of_calendar_range`) on both submit and retry, and writes zero rows.
- **CR-B-01:** the ingestion run row, with its `job_id`, is committed before any work starts and finalized in its own transaction. A failed ingest keeps a FAILED run linked to its Job, and each symbol runs in its own transaction.

## Skipped
- **WR-A-02 (product decision):** should `ingest-bars` FAIL when every symbol fails? D-08/D-09 say the handler never reinterprets partial symbol failures, and changing that reverses a documented design. The user needs to decide. **Decided 2026-09-29 (UAT gap 1):** zero succeeded symbols means run FAILED and Job FAILED/handler_error; partial stays SUCCEEDED. See 20-CONTEXT.md D-08a, implemented in plan 20-25. The earlier claim that D-08/D-09 contain the 'never reinterprets partial symbol failures' rule was incorrect: that wording existed only in the handler docstring.

## Partial or residual
- **WR-A-03:** `sync-market-sessions` ranges are now calendar-bounded at submit. The lookback cap for `ingest-bars` is not implemented because it needs a config value.
- **CR-B-01 residual:** a crashed ingest now leaves a visible `running` row, and nothing reclaims stale ingestion runs.
- **WR-B-06:** for a strategy with no DB row, analytics now reports `active`, matching the control read. The alternative, where `ensure_strategy_record` honours `metadata.enabled`, would change gating and was not applied.
- **WR-C-04:** 5xx and transport control failures now show a separate outage message. `20-UI-SPEC.md` needs its "Request rejected" row scoped to 4xx. Part C lists the spec deviations.

## Needs human verification
The part reports mark these as changing concurrency, migration, safety or async behavior. Unit and E2E tests cover them only in simulated form:
- Part A: WR-A-01 (retry replay after the lock), WR-A-04 (downgrade guard), WR-A-05 (`outcome_uncertain` carried on the conflict).
- Part B: CR-B-01, WR-B-02 (FOR UPDATE and first-use race), WR-B-05 (kill switch without the registry), WR-B-06.
- Part C: WR-C-01, WR-C-02, WR-C-03, WR-C-04, WR-C-06, WR-C-07.

These are folded into `20-HUMAN-UAT.md` (test 5).
