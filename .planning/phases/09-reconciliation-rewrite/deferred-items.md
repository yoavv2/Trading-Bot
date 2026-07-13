# Deferred Items — Phase 9 (Reconciliation Rewrite)

## 09-03

- **`tests/test_market_data_ingestion.py::TestIngestionPipeline::test_upsert_symbol_creates_new_record`** errors during
  teardown with `psycopg.errors.InsufficientPrivilege: must be a superuser to terminate superuser process`. This is a
  pre-existing environmental issue in the test DB's `pg_terminate_backend` teardown helper, unrelated to
  `reconciliation.py` or any file this plan touches — out of scope per the plan's scope boundary. Not fixed.
  (Did not reproduce during 09-04's full-suite run; still unresolved, may be flaky/environment-dependent.)

## 09-04

- **`worker/__main__.py`'s standalone `reconcile-paper-execution` CLI command** calls only
  `reconcile_paper_execution` (read-only after 09-03) and was never wired to
  `apply_reconciliation_corrections`. Before 09-03, this CLI path's single `reconcile_paper_execution`
  call also incremented/reset `PaperOrder.sync_failure_count` as a side effect; that capability
  disappeared silently in 09-03 (not introduced by 09-04) and this plan's `files_modified` scope
  (`reconciliation.py`, `paper_execution.py`, `analytics.py`/`operator_status.py`/`operator_reads.py`)
  does not include `worker/__main__.py`. If operators rely on this CLI command (e.g. via cron) to
  auto-heal sync-failure counters outside of a full paper-session run, a follow-up plan should add a
  new `apply-reconciliation-corrections` (or similar) CLI subcommand that explicitly invokes the new
  corrective entrypoint. No test currently exercises or pins this CLI command's reconcile-mutation
  behavior, so nothing is failing today — this is a silent capability gap, not a broken test.
- **`REQUIREMENTS.md` marks RECON-05 and RECON-07 as `Pending`** even though both were implemented by
  09-01 (`LocalOrderSnapshot`/`LocalFillSnapshot`/`LocalPositionSnapshot`/`LocalAccountSnapshot` typed
  dataclasses for RECON-05; the closed 5-member `ReconciliationFinding` enum for RECON-07 — see
  `src/trading_platform/services/reconciliation_types.py`). This plan's frontmatter only declares
  `requirements: [RECON-04]`, so `requirements mark-complete` was run for RECON-04 only, per the
  documented instruction to extract IDs strictly from the current plan's frontmatter. Flagging here
  rather than silently checking boxes outside this plan's declared scope; a follow-up should confirm
  and mark RECON-05/RECON-07 complete (likely a 09-01 execution oversight).
