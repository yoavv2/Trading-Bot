---
phase: 20-complete-operation-migration-safety-controls
plan: 21
subsystem: testing
tags: [e2e, job-framework, market-data, pytest, fastapi, polygon-fake]

# Dependency graph
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-14 (ingest-bars), 20-15 (sync-symbol-metadata, sync-market-sessions), 20-16 (production registry registration of all 8 Job types)"
provides:
  - "tests/test_market_data_job_types_e2e.py: API -> worker -> service E2E for the three separate market-data Job types (SC1)"
  - "Pinned proof that ingest-bars links exactly one market_data_ingestion_run (id == result_summary.run_id, row job_id == Job id)"
  - "Pinned proof of symbols normalization and fingerprint-stable idempotent replay for ingest-bars (D-24/D-25)"
  - "Pinned proof that all three types reject unknown payload keys with 422 unknown_payload_keys and zero rows written"
affects: [20-22-controls-console-live-verify, 20-23-operations-console-live-verify, 20-24-orchestration-closure]

tech-stack:
  added: []
  patterns:
    - "Polygon faked at the service seam only: FakePolygonClient replaces services.ingestion.PolygonClient (generates deterministic bars locally) and services.symbol_metadata_sync.fetch_ticker_overview is monkeypatched; handlers and services are otherwise the production code"

key-files:
  created:
    - tests/test_market_data_job_types_e2e.py
  modified: []

key-decisions:
  - "Ingest window 2024-01-11..2024-01-12 (past the shared fixture's last seeded bar, 2024-01-10) so the fake bars never overwrite seeded AAPL bars."
  - "sync-market-sessions uses Feb 2024 (unseeded, contains Presidents Day) so the expected count comes from sessions_in_range (20 sessions vs 21 weekdays) and the pre-count of persisted rows is provably 0."
  - "Both tasks landed in one commit: Task 2 only appends to the single new test file created in Task 1 and the file was authored and verified as a unit."
  - "Closes OPS-05: the three separate, independently validated types are now each proven through create_app() -> POST /api/v1/jobs -> real run-jobs --once. ORCH-02 is left to 20-24."

patterns-established:
  - "Market-data E2E fixture market_jobs_env layers the two Polygon fakes on job_operations_env; no broker credentials are configured (all three types are BACKTEST mode)."

requirements-completed: [OPS-05]

duration: ~25min
completed: 2026-09-28
---

# Phase 20 Plan 21: Market-Data Job Types E2E Summary

**Production-path E2E (create_app -> POST /api/v1/jobs -> real `run-jobs --once`) proving ingest-bars, sync-symbol-metadata and sync-market-sessions as three separate Job types, with Polygon faked only at the service seam.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 (delivered in one commit, see Deviations)
- **Files modified:** 1 (new test file, 394 lines, 9 test cases)

## Accomplishments
- `ingest-bars` runs to SUCCEEDED with exactly one `resources[]` entry `{kind: market_data_ingestion_run, links: {}}`; its id equals `result_summary.run_id` and `produced_run_ids`, and the `MarketDataIngestionRun` row carries `job_id == Job id`, `trigger_source == "job"`; 4 bars upserted (2 symbols x 2 sessions).
- Symbol normalization pinned: `[" spy", "aapl", "SPY"]` persists as `["AAPL", "SPY"]`; resubmitting `["SPY", "AAPL"]` with the same Idempotency-Key replays (200, `Idempotency-Replayed: true`) with exactly one ingest-bars Job.
- `sync-symbol-metadata` runs to SUCCEEDED with `resources == []`, symbols rows upserted (name/exchange/provider asserted) and `synced_count == 2`; when the faked fetch raises for SPY the Job ends FAILED / `handler_error` with SPY (and not QQQ) in `failure_message`.
- `sync-market-sessions` runs to SUCCEEDED with `resources == []`, persisted `market_sessions` dates exactly equal to `sessions_in_range` for the range, and `sessions_upserted == 20`.
- The three types appear as separate `GET /api/v1/job-types` entries, each `step_boundary` with distinct descriptions.
- An unknown payload key (`dry_run`) on each of the three types returns 422 `invalid_job_payload` / `unknown_payload_keys` with `_counts()` unchanged.

## Task Commits

1. **Tasks 1+2: market-data Job types E2E** - `7ae1670` (test)

**Plan metadata:** (this docs commit)

## Files Created/Modified
- `tests/test_market_data_job_types_e2e.py` - new; `market_jobs_env` fixture, `FakePolygonClient`, 9 test cases (3 ingest/catalog, 3 sync, 3 parametrized unknown-key rejections).

## Decisions Made
See key-decisions in frontmatter.

## Deviations from Plan

### Process

**1. Tasks 1 and 2 committed together**
- The plan describes two tasks that both edit only the one new test file. The file was written and verified as a whole (9 passed, ruff clean) and committed once (`7ae1670`) rather than as two intermediate states.

No code deviations - no production code changed; the handlers/services from 20-14/20-15/20-16 behaved as specified. One self-caught test arithmetic error (expected Feb 2024 session count is 20, not 19) was fixed before the commit.

## Issues Encountered
None. Full Python suite: 949 passed (baseline ~940 + 9 new), no flake this run.

## Known Stubs
None.

## Threat Flags
None - test-only change; T-20-21-01 (normalization drift) mitigated by the pinned normalized-symbols and identical-fingerprint replay tests; T-20-21-02 (Polygon key disclosure) mitigated by the faked seams (no key configured, no network).

## Next Phase Readiness
- OPS-05 is closed. Remaining Phase 20 plans: 20-22, 20-23 (console live verification) and 20-24 (ORCH-02 ownership).

## Self-Check: PASSED
- tests/test_market_data_job_types_e2e.py exists; commit 7ae1670 exists; 9 tests pass.
