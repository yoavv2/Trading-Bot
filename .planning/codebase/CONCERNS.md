# Codebase Concerns

**Analysis Date:** 2026-09-23

## Tech Debt

**Monolithic Service Layer — Execution:**
- Issue: `submit_orders.py` contains 1732 lines (70KB) handling paper order submission, session orchestration, and intent-decision logic. This single file bundles broker-state sync prep, candidate validation, submission loops, and reconciliation triggering.
- Files: `src/trading_platform/services/execution/submit_orders.py`
- Impact: Difficult to test specific submission scenarios; cognitive load makes bug-fixing risky; changes to one concern (e.g., intent logic) risk unintended changes to another (e.g., broker sync).
- Fix approach: Split into focused modules: `_intent_evaluator.py` (signal→decision logic), `_submission_orchestrator.py` (session loop), `_state_sync_prep.py` (broker state prep). Each module should be <400 lines.

**Complex Reconciliation Report:**
- Issue: `reconciliation/report.py` at 814 lines combines broker-to-local matching, safety-finding detection, and persistence in a single module.
- Files: `src/trading_platform/services/reconciliation/report.py`
- Impact: Hard to extend matching rules without touching persistence logic; findings detection is intertwined with broker state snapshots.
- Fix approach: Extract a `_findings_detector.py` module focused solely on SafeFinding→ReconciliationFinding mapping. Decouple finding rules from snapshot schema.

**Large Risk Service:**
- Issue: `risk.py` at 661 lines contains approval rules, position checks, and cash validation all in one module.
- Files: `src/trading_platform/services/risk.py`
- Impact: Adding a new risk check (e.g., correlation limits) requires careful insertion in the existing call chain; testing one rule often exercises unrelated rules.
- Fix approach: Refactor toward a rule-registry pattern (similar to job registry): each RiskRule is a small class with a single `evaluate()` method. Compose them in `RiskValidator`.

**Incomplete Type Coverage:**
- Issue: mypy configured only for `src/trading_platform/services/execution`, `src/trading_platform/services/reconciliation`, and `src/trading_platform/services/config` in `pyproject.toml` (lines 74-78). Rest of codebase (`strategies/`, `jobs/`, `core/`, `db/`, `worker/`, `api/`) is untyped.
- Files: `pyproject.toml`, `src/trading_platform/`
- Impact: Type-unsafe refactoring in untyped modules (e.g., job runner, API routes) risks silent bugs at runtime; IDE tooling cannot help in large files like `operator_controls.py` (612 lines).
- Fix approach: Gradually expand mypy coverage: add `jobs/` and `core/` modules in Phase N; deprecate `# type: ignore` comments. Use `reveal_type()` in tests to catch regressions.

**E501 (Line-Too-Long) Intentionally Ignored:**
- Issue: `pyproject.toml` (lines 51-56) explicitly disables ruff's E501 check due to ~200+ pre-existing long lines, mostly in comments/strings/test assertions.
- Files: `pyproject.toml`
- Impact: Long lines reduce readability, especially in critical paths like order submission and reconciliation; inconsistent formatting makes review harder.
- Fix approach: Separate concern: Phase N refactor to wrap comments and strings systematically. Do NOT combine with functional changes.

## Known Bugs

**Database Connection Check Untested:**
- Symptoms: `check_database_connection()` is marked `pragma: no cover` — readiness integration tests exercise it, but unit path is unverified.
- Files: `src/trading_platform/db/session.py:114`
- Trigger: Call `check_database_connection()` with a misconfigured or unreachable database.
- Workaround: Startup relies on manual database readiness checks; connection failures may not surface until first query inside `session_scope()`.
- Fix: Add explicit unit tests for connection failure scenarios (bad host, wrong port, auth failure) without relying on integration test.

**Job Constraint Error Handling Untested:**
- Symptoms: Two locations in `job_mutations.py` (lines 259, 330) guard against "malformed constraint error" but are marked `pragma: no cover`.
- Files: `src/trading_platform/orchestration/job_mutations.py`
- Trigger: Attempt to create a Job with a duplicate `(worker_id, session_date, strategy_id, symbol)` tuple.
- Workaround: Constraint violations fail silently or raise generic DB exceptions; the protection code is never exercised.
- Fix: Write unit test that triggers duplicate constraint; verify the guard catches it before it propagates.

**Broad Exception Handlers Mask Root Causes:**
- Symptoms: 86 instances of `except Exception:` or `except Exception as exc:` across the codebase hide the true error type.
- Files: Multiple locations including `db/session.py:101`, `jobs/runner.py:157,199,384`, `services/backtesting.py:148`, etc.
- Trigger: Any unexpected error in a try-block (e.g., missing database field, import error, network timeout).
- Workaround: Logs contain full exception traceback but not the specific exception type, making grep-based debugging harder.
- Fix: Audit each `except Exception` block; replace with specific exception types (`psycopg.Error`, `StrategyInitError`, etc.) or document why broad catch is necessary.

## Security Considerations

**Hardcoded Database Credentials in Settings:**
- Risk: `core/settings.py` defines `DatabaseSettings` with default password `"trading_platform"` (line 46). While overridable via environment, the default is weak and the pattern encourages checking defaults into version control.
- Files: `src/trading_platform/core/settings.py`
- Current mitigation: `.env` file present (not version-controlled); environment variables override defaults at runtime.
- Recommendations:
  1. Require `DATABASE_PASSWORD` environment variable; remove default from code.
  2. Add startup validation: `if settings.database.password == "trading_platform": raise ConfigError("Production password detected")`.
  3. Audit all secret-like fields (API keys, tokens) for similar patterns.

**Log Sanitization Dependency on Global State:**
- Risk: `_DEBUG_UNMASK_IDS` global flag in `core/logging.py` (line 18) controls whether broker order IDs are masked in logs. If set to True in production by mistake, logs leak sensitive IDs.
- Files: `src/trading_platform/core/logging.py`
- Current mitigation: Default is False (safe); test helpers can toggle it explicitly.
- Recommendations:
  1. Make `_DEBUG_UNMASK_IDS` read-only after `configure_logging()` is called.
  2. Add a startup check that raises an error if `debug_unmask_ids=True` in production environment.
  3. Consider using context-based sanitization (thread-local or context var) instead of module-level global.

**Credentials in .env Files (Not Directly Readable):**
- Risk: `.env`, `.env.local` files exist but not shown here (forbidden by policy). These likely contain API keys, database passwords, broker credentials.
- Files: `.env`, `.env.local` (not analyzed)
- Current mitigation: Listed in `.gitignore`; example files (`.env.example`) commit safe defaults.
- Recommendations:
  1. Verify all secret-bearing .env files are in .gitignore.
  2. Use separate .env files for local, staging, production; never commit production secrets.
  3. Consider using a secrets manager (AWS Secrets Manager, HashiCorp Vault) for production.

## Performance Bottlenecks

**109 Explicit Session Scope Usages — Transaction Complexity:**
- Problem: The codebase explicitly opens/closes database sessions 109 times across services, runners, and API endpoints. Each session_scope creates a new connection, runs a query, and commits/rolls back. No connection pooling or batch operations.
- Files: Throughout `src/trading_platform/` (identified via grep)
- Cause: Single-purpose sessions for isolated operations (e.g., "load one order", "record one log event"). No attempt to amortize query cost across multiple operations.
- Improvement path:
  1. Profile hot paths: identify which sessions are called >1000 times per day.
  2. Batch operations where possible: merge 5 "load order" calls into one multi-order query with joins.
  3. Consider a session-per-request pattern in API layer (dependency injection via FastAPI) instead of ad-hoc session_scope() calls.
  4. Add connection pooling tuning: adjust `pool_size` and `max_overflow` in `build_engine()`.

**Large Files = Slow IDE Navigation and Tests:**
- Problem: `submit_orders.py` (1732 lines) takes >5 seconds to load in some IDEs; running its full test suite (`test_paper_execution.py`, 2168 lines) takes >30 seconds.
- Files: `src/trading_platform/services/execution/submit_orders.py`, `tests/test_paper_execution.py`
- Cause: Monolithic module with many functions and complex control flow; test file mirrors the size and has overlapping coverage.
- Improvement path: (See Tech Debt section: split submit_orders.py into focused modules; refactor test file to mirror the new module structure.)

**No Query Index Coverage Analysis:**
- Problem: No systematic audit of database queries vs. indexes. Risk: common queries (e.g., "find all paper orders for strategy X") may do full table scans.
- Files: Database migrations in `alembic/versions/`, queries in `services/`
- Cause: Ad-hoc index creation during feature development; no performance testing against large tables (millions of rows).
- Improvement path:
  1. Run EXPLAIN ANALYZE on top 10 frequent queries (identify via logs or APM).
  2. Create missing indexes for WHERE/JOIN/ORDER BY columns.
  3. Add performance regression tests: e.g., "fetching 1M paper orders for a strategy completes in <2s".

## Fragile Areas

**Operator Controls Global State:**
- Files: `src/trading_platform/services/operator_controls.py`, `src/trading_platform/services/operator_reads.py`, `src/trading_platform/services/concurrency_guard.py`, `src/trading_platform/db/models/system_control.py`
- Why fragile: Multiple modules use global flags and module-level state to coordinate kill-switch and strategy control. Changes to one module (e.g., adding a new control state) ripple through all consumers.
- Safe modification:
  1. All control-state changes must go through `load_kill_switch_state()` and `load_strategy_control_state()` (enforce in tests).
  2. Add an invariant test: "Control state read twice in succession is identical" (no hidden mutations).
  3. Add a "control state audit log" that records every `kill_switch.trip()` call and its caller.
- Test coverage: `test_operator_controls.py` (320 lines) covers trips and reads, but not concurrent read-modify-write scenarios.

**Reconciliation Matcher with Assertions:**
- Files: `src/trading_platform/services/reconciliation/matcher.py:167` (`assert broker_position is not None`)
- Why fragile: Assertions can be disabled with Python's `-O` flag; the code assumes `broker_position` is present but may silently fail in production if assumptions break.
- Safe modification:
  1. Replace assertion with explicit check: `if broker_position is None: raise MissingBrokerPositionError(...)`.
  2. Add integration test that exercises the exact snapshot combination that triggered the assert.
  3. Document the invariant: "Every broker order must have a corresponding broker position" (or explain the exception).
- Test coverage: `test_reconciliation_matcher.py` (520 lines) covers happy path; edge case (missing broker position) is not tested.

**Job Runner Exception Handling:**
- Files: `src/trading_platform/jobs/runner.py:157,199,384`
- Why fragile: Three locations catch broad `Exception`, making it hard to distinguish between handler errors, lease-loss, and infrastructure failures.
- Safe modification:
  1. Define specific exception types: `HandlerError`, `LeaseLossError`, `JobCancelledError` (already exists).
  2. Catch each explicitly; log the type; transition job to appropriate terminal state.
  3. Add test: run a job handler that raises a custom exception; verify it lands in FAILED state with the right failure_reason.
- Test coverage: `test_job_runner.py` (529 lines) covers nominal paths; exception routing is partially tested.

## Scaling Limits

**Paper Order Submission Session Loop — Linear Scaling:**
- Current capacity: Tested with ~50 paper orders per strategy run (typical backtest).
- Limit: At ~500+ orders, the session loop in `submit_orders.py` (lines 300+) performs O(n) database queries, one per order. Each query is wrapped in a session_scope, causing network latency to multiply.
- Scaling path:
  1. Batch orders: load all candidate orders in one query, validate, submit in a single transaction.
  2. Use `insertmanyvalues` for bulk PaperOrder inserts (SQLAlchemy 2.0+ feature).
  3. Add benchmarks: "Submitting 1000 orders to paper broker completes in <30 seconds".

**Database Connection Pool — Unbounded Checkout:**
- Current capacity: SQLAlchemy default pool size is 5 connections; max_overflow is 10.
- Limit: Under load (e.g., 20 concurrent strategy runs), pool may exhaust, causing checkout to block or fail.
- Scaling path:
  1. Profile: measure concurrent session_scope() calls during peak times (e.g., market open).
  2. Adjust pool_size and max_overflow in `build_engine()` based on observed concurrency.
  3. Consider async/await pattern (FastAPI + asyncpg) for I/O-bound operations, but this is a major refactor.

**Market Data Ingestion — No Batch Insert:**
- Current capacity: `services/ingestion.py` inserts daily bars one at a time (Session.add, Session.commit per bar).
- Limit: Ingesting 10 years of data for 100 symbols = ~250k inserts, each taking ~100ms in current approach → >6 hours.
- Scaling path:
  1. Batch inserts: collect bars in a list, insert in chunks of 1000.
  2. Use `bulk_insert_mappings()` or `bulk_save_objects()` from SQLAlchemy.
  3. Add benchmarks: "Ingesting 250k bars completes in <2 minutes".

## Dependencies at Risk

**Version Ranges Allow Major Changes (pyproject.toml):**
- Risk: Dependencies specified with `>=` lower bounds and `<2.0.0` upper bounds, e.g., `fastapi>=0.131.0,<1.0.0`. If a dependency released a minor version with breaking changes (e.g., a different return type for a helper function), builds might fail silently or at runtime.
- Files: `pyproject.toml` (lines 11-21)
- Impact: CI may pass on main but fail when a new developer pulls the repo and installs latest compatible versions.
- Migration plan:
  1. Use a `requirements-lock.txt` or `pyproject.toml` with exact pinned versions (e.g., `fastapi==0.131.0`).
  2. Set up Dependabot or Renovate to auto-test and PR each dependency update.
  3. Schedule monthly dependency audits to check for security patches.

**SQLAlchemy 2.0 with Non-Standard Session Config:**
- Risk: `session_factory` in `db/session.py:85-91` uses `autoflush=False` and `expire_on_commit=False`, which are non-defaults. If SQLAlchemy 3.0 changes defaults, this code may behave unexpectedly.
- Files: `src/trading_platform/db/session.py`
- Impact: Silent data inconsistency bugs (e.g., stale object attributes after commit) if the settings change.
- Migration plan:
  1. Document the exact reason for each non-default setting (add a comment explaining why expire_on_commit=False is needed, for example).
  2. Add a test that verifies the settings are correctly applied (e.g., "session.autoflush is False").
  3. When upgrading SQLAlchemy, run the full test suite and pay special attention to any data-mutation tests.

**Alpaca SDK Update Risk:**
- Risk: `alpaca.py` wraps the Alpaca broker API. If the SDK's version is bumped (e.g., from 0.x to 1.0), response schema or method signatures may change without warning.
- Files: `src/trading_platform/services/alpaca.py`
- Impact: Paper trading and live trading may silently fail if the SDK is upgraded without testing.
- Migration plan:
  1. Pin Alpaca SDK version: add it to `pyproject.toml` if not already there.
  2. Add integration tests that connect to Alpaca's paper trading environment and verify method signatures.
  3. Create a changelog for each Alpaca SDK version that documents schema changes.

## Missing Critical Features

**Risk Validation Deferred:**
- Problem: `services/risk.py:446` raises `NotImplementedError("Risk validation is deferred to Phase 4.")` for unspecified risk checks.
- Blocks: Any risk check beyond max_positions, allocation caps, and cash checks cannot be added without hitting this error.
- Blocks: Correlation limits, VaR-based limits, drawdown limits.
- Roadmap: Phase 4 (TBD); depends on analytics service maturity.

**Market-Data Integration Deferred:**
- Problem: `services/data.py:102` raises `NotImplementedError("Market-data integration is deferred to Phase 2.")`.
- Blocks: Direct market data queries (e.g., "fetch latest close for SPY") are not implemented; strategies must rely on pre-loaded daily bars.
- Blocks: Intraday strategies, tick-level analytics.
- Roadmap: Phase 2 (TBD).

**Strategy Execution Deferred:**
- Problem: `services/execution/contracts.py:87` raises `NotImplementedError("Execution is deferred to Phase 5.")` in the base ExecutionService.
- Blocks: Any new execution backend (e.g., Interactive Brokers, crypto exchange) requires implementing the full ExecutionService interface.
- Blocks: Multi-broker execution, DMA (direct market access).
- Roadmap: Phase 5 (TBD); depends on execution framework stabilization.

## Test Coverage Gaps

**Database Connection Failures:**
- What's not tested: `check_database_connection()` when database is unreachable, credentials are wrong, or network is down.
- Files: `src/trading_platform/db/session.py`
- Risk: Startup readiness checks pass (because they're integration-tested) but unit-level connection errors are never caught.
- Priority: High (affects operational reliability).

**Job Constraint Violations:**
- What's not tested: Attempting to create a Job with duplicate `(worker_id, session_date, strategy_id, symbol)`.
- Files: `src/trading_platform/orchestration/job_mutations.py`
- Risk: Silent constraint violation, incomplete error handling, or duplicate job execution.
- Priority: High (affects job correctness).

**Operator Control State Concurrency:**
- What's not tested: Two threads reading kill-switch state concurrently while a third thread updates it.
- Files: `src/trading_platform/services/operator_controls.py`, tests in `test_operator_controls.py`
- Risk: Race condition where kill-switch is read as OFF but trips immediately after; execution proceeds and should have been blocked.
- Priority: High (affects safety).

**Reconciliation Matcher Edge Cases:**
- What's not tested: Broker state contains orders/fills/positions with nulls, mismatched currencies, or extreme quantities.
- Files: `src/trading_platform/services/reconciliation/matcher.py`
- Risk: Silent data corruption if matcher assumptions break.
- Priority: Medium (depends on data source quality).

**Strategy Registry Dynamic Loading:**
- What's not tested: Loading a strategy from `config/strategies/` that has a syntax error, circular import, or missing dependency.
- Files: `src/trading_platform/strategies/registry.py`
- Risk: Strategy load fails silently; execution proceeds with stale strategy list.
- Priority: Medium (affects strategy hot-reload).

---

*Concerns audit: 2026-09-23*
