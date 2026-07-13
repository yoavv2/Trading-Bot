# Deferred Items — Phase 9 (Reconciliation Rewrite)

## 09-03

- **`tests/test_market_data_ingestion.py::TestIngestionPipeline::test_upsert_symbol_creates_new_record`** errors during
  teardown with `psycopg.errors.InsufficientPrivilege: must be a superuser to terminate superuser process`. This is a
  pre-existing environmental issue in the test DB's `pg_terminate_backend` teardown helper, unrelated to
  `reconciliation.py` or any file this plan touches — out of scope per the plan's scope boundary. Not fixed.
