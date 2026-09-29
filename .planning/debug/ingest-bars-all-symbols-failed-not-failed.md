---
status: diagnosed
trigger: "ingest-bars-all-symbols-failed-not-failed: An ingest-bars Job where EVERY symbol fails (bad Polygon key or unreachable Polygon) finishes SUCCEEDED, and its MarketDataIngestionRun is PARTIAL rather than FAILED."
created: 2026-09-29
updated: 2026-09-29
goal: find_root_cause_only
---

## Current Focus

hypothesis: CONFIRMED -- ingestion.py _finish_run derives status as `failed if error_message else (partial if failed_symbols else succeeded)`; the per-symbol `except Exception` in ingest_daily_bars never sets error_message and never raises, so all-fail == partial and the handler returns normally -> runner lands SUCCEEDED.
test: scratch E2E repro (API -> run-jobs --once -> handler -> service, throwaway DB) with a fake PolygonClient raising PolygonAuthError per symbol
expecting: all-fail => Job succeeded, run partial (bug); 1ok+1fail => Job succeeded, run partial (baseline to keep)
next_action: return ROOT CAUSE FOUND to orchestrator (diagnose-only); planner writes fix per Resolution.fix

## Symptoms

expected: A failing ingest-bars Job (bad Polygon key or unreachable Polygon) shows its ingestion run as FAILED in the Job resources panel (Phase 20 UAT test 5c).
actual: bad key -> Job a62ba406 SUCCEEDED, run 109b19fb PARTIAL symbols_failed [SPY] (worker log 401). BASE_URL=http://127.0.0.1:9 -> Job a9d3fec6 SUCCEEDED, run 4d73c649 PARTIAL symbols_failed [QQQ]. Empty key -> PolygonAuthError in PolygonClient.__init__ -> FAILED run + FAILED Job (b120d584, run 91727cac).
errors: none raised; per-symbol exceptions swallowed into failed_symbols.
reproduction: Test 5 in .planning/phases/20-complete-operation-migration-safety-controls/20-HUMAN-UAT.md
started: discovered during UAT; WR-A-02 review finding skipped as product decision.

## Eliminated

- hypothesis: the console/job_reads renders the wrong run status
  evidence: job_reads.py:141-145 derives resources[] from market_data_ingestion_runs.job_id FK and passes run.status through; repro shows resources[0].status == run.status == 'partial'. The UI is faithful; the persisted status is the problem.
  timestamp: 2026-09-29

## Evidence

- timestamp: 2026-09-29
  checked: src/trading_platform/services/ingestion.py:148-166 (_finish_run)
  found: run.status = "failed" if error_message else ("partial" if failed_symbols else "succeeded"); error_message only set when passed.
  implication: no branch distinguishes "zero succeeded" from "some succeeded".

- timestamp: 2026-09-29
  checked: ingestion.py:255-313 (ingest_daily_bars)
  found: per-symbol try/except Exception appends ticker to failed_symbols (line 284-289) and continues; _finalize_run(no error_message) at 291; outer except (297-313) only fires on run-level exceptions (PolygonClient.__init__ / _finalize_run) and finalizes FAILED with error_message=str(exc) then re-raises.
  implication: every per-symbol failure (401 PolygonAuthError, transport PolygonClientError) is swallowed; only run-level exceptions produce FAILED.

- timestamp: 2026-09-29
  checked: src/trading_platform/jobs/handlers/ingest_bars.py:61-98
  found: handler returns result_summary with ingestion_succeeded=result.succeeded (== failed_count==0) and never raises on failures.
  implication: runner.execute_job outcome_kind="success" -> SUCCEEDED.

- timestamp: 2026-09-29
  checked: src/trading_platform/jobs/runner.py:233-351
  found: handler exceptions other than JobCancelledError/JobDomainConflictError -> FAILED / HANDLER_ERROR, failure_message=f"{type(exc).__name__}: {exc}"[:2000], outcome_uncertain = any job_logs.event_code startswith "external_". result_summary persisted ONLY on SUCCEEDED.
  implication: raising a typed exception from the handler lands FAILED/handler_error, outcome_uncertain=False (ingest-bars logs no external_* codes). result_summary would be null/empty on the FAILED Job; linkage survives via FK.

- timestamp: 2026-09-29
  checked: scratch E2E repro (scratchpad/repro/test_repro_all_fail.py; throwaway DB via market_jobs_env)
  found: all_fail (AAPL,SPY raise PolygonAuthError): job_status=succeeded, failure_reason=None, run_status=partial, run_error_message=None, run_bars=0, symbols_failed=[AAPL,SPY], result_summary.ingestion_succeeded=False, log_codes=[ingest_bars_started, ingest_bars_completed], run.job_id==job.id. one_ok_one_fail (SPY fails): job_status=succeeded, failure_reason=None, run_status=partial, run_bars=2, symbols_failed=[SPY], ingestion_succeeded=False.
  implication: bug reproduced deterministically without network. Partial-case Job outcome TODAY = SUCCEEDED (failure_reason None, outcome_uncertain False, result_summary.ingestion_succeeded False).

- timestamp: 2026-09-29
  checked: callers of ingest_daily_bars (grep src scripts tests alembic)
  found: production caller = jobs/handlers/ingest_bars.py:61 only. scripts/ingest_polygon_bars.py was deleted in Phase 20 (D-26). Tests: test_market_data_ingestion.py (455,485,548), test_phase20_service_job_links.py (398,421,441,469), test_ingest_bars_job_type.py (handler fakes 273-393; service 424), test_market_data_job_types_e2e.py (172), test_orchestration_boundaries.py:712 (name in mutator list).
  implication: change surface is small; no CLI path to keep consistent.

- timestamp: 2026-09-29
  checked: sync-symbol-metadata precedent (services/symbol_metadata_sync.py:120-157, jobs/handlers/sync_symbol_metadata.py:57-73, tests/test_market_data_job_types_e2e.py:304-326)
  found: service defines typed SymbolMetadataSyncFailedError(RuntimeError) + MetadataSyncResult.raise_for_failures(); handler logs completion then calls raise_for_failures() -> FAILED / handler_error, failure_message names failed tickers.
  implication: an in-repo typed-exception + handler_error precedent exists for "per-item failures fail the Job"; no new JobFailureReason needed.

- timestamp: 2026-09-29
  checked: 20-CONTEXT.md D-05, D-08, D-09 text
  found: D-08 = resources[] per type table (ingest-bars: 1 market_data_ingestion_run). D-09 = job_id same-txn, back-link, trigger_source=job, produced-run-ids test. Neither says anything about partial symbol failures. "Jobs never reinterpret domain outcomes (invariant 2)" is a bullet under D-05 (paper-session blocked/noop). The "handler never reinterprets partial symbol failures" wording exists only in ingest_bars.py docstring and 20-REVIEW-FIX*.md.
  implication: the amendment should be a new decision (D-08a) amending D-05's invariant-2 bullet scope + D-08 ingest-bars row, and the handler docstring citation must be corrected.

- timestamp: 2026-09-29
  checked: retry gating (orchestration/job_mutations.py:439-453, ingest_bars_submission.py:94, test_spec_declares_no_retry_prerequisite)
  found: ingest-bars is STEP_BOUNDARY, retry_prerequisite None; FAILED jobs retry normally (UAT b120d584 -> 02df4a94). D-19 block needs FAILED+outcome_uncertain+prerequisite type; none apply.
  implication: new FAILED all-fail Jobs become retryable (previously SUCCEEDED => 409 not retryable). No gating code change.

- timestamp: 2026-09-29
  checked: alembic job_failure_reason enum (0018, 0020:21, 0021:31), lifecycle.py:224, job.py:129
  found: job_failure_reason is a Postgres ENUM; new values need ALTER TYPE ... ADD VALUE migration. On FAILED the runner passes no result_summary, so jobs.result_summary stays {} (NOT NULL default dict).
  implication: handler_error needs no migration. A FAILED all-fail Job exposes its run only via resources[] (FK), so failure_message should carry the run_id.

- timestamp: 2026-09-29
  checked: produced_run_ids generalized test (tests/test_strategy_job_types_e2e.py:145-321), test_job_resources_read.py:168-335, console/src grep for partial/ingestion_succeeded/symbols_failed
  found: the generalized D-09 test covers strategy types only; resources-read tests seed rows directly; console has no ingest-specific status logic (only OpenOrdersPanel partially_filled, unrelated).
  implication: no console change; no generalized test breaks.

- timestamp: 2026-09-29
  checked: PROJECT.md invariants 2 and 8
  found: inv 2 "Jobs are orchestration-only ... never domain semantics"; inv 8 "services never depend on Jobs".
  implication: the zero-succeeded rule is domain semantics -> service owns predicate + typed exception (defined in services/, no jobs import); handler only propagates.

## Resolution

root_cause: services/ingestion.py _finish_run (lines 159-163) derives run status as `failed if error_message else (partial if failed_symbols else succeeded)` with no zero-succeeded branch, and ingest_daily_bars (lines 257-289) swallows every per-symbol exception into failed_symbols without error_message and without raising. The handler (ingest_bars.py:61-98) returns normally, so runner.execute_job takes the success path and writes SUCCEEDED. Only run-level exceptions (PolygonClient.__init__) reach the outer except and yield FAILED.
fix: (diagnose-only) service-owned predicate _derive_run_status(succeeded_count, failed_count, run_error); all-fail => run FAILED + deterministic error_message; IngestionResult.run_status + raise_for_all_symbols_failed() raising typed IngestionAllSymbolsFailedError (services/data.py); handler calls it after the completion log and BEFORE the post-call cancel checkpoint -> runner FAILED/handler_error.
verification:
files_changed: []
