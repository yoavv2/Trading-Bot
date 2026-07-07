# Codebase Concerns

**Analysis Date:** 2026-07-07

## Tech Debt

### File Complexity — Large Single Services

**Paper Execution Service:**
- Issue: `paper_execution.py` is 1,671 lines with multiple concerns bundled: state machine orchestration, broker submission, kill-switch checking, error recovery, and detailed result reporting
- Files: `src/trading_platform/services/paper_execution.py`
- Impact: Makes testing individual submission flows difficult; changes to one concern risk cascading failures; hard to reason about error paths
- Fix approach: Extract broker submission logic into `BrokerSubmissionOrchestrator`, kill-switch checking into `KillSwitchGuard`, and result reporting into `ExecutionResultBuilder`; use composition instead of monolithic function

**Reconciliation Service:**
- Issue: `reconciliation.py` is 840 lines handling broker-state sync, drift detection, recovery logic, and safety validation in one module
- Files: `src/trading_platform/services/reconciliation.py`
- Impact: Drift recovery logic is not isolated from sync logic; hard to test state-recovery paths independently
- Fix approach: Extract `BrokerDriftRecovery` and `SafetyValidation` as separate internal classes with explicit boundaries

**Operator Reads Service:**
- Issue: `operator_reads.py` is 642 lines with 15+ serialization methods returning dict payloads for different read types
- Files: `src/trading_platform/services/operator_reads.py`
- Impact: Serialization logic is scattered; schema changes ripple across multiple methods; no centralized payload builder
- Fix approach: Create `OperatorReadPayload` dataclass hierarchy with `.to_dict()` on each read result type; use factory method for construction

**Risk Pipeline:**
- Issue: `risk.py` is 655 lines bundling signal validation, portfolio checks, sizing calculation, and event persistence
- Files: `src/trading_platform/services/risk.py`
- Impact: Sizing algorithm is hard to test in isolation; changes to risk rules affect multiple decision codes
- Fix approach: Extract `RiskSizingEngine` and `RiskValidationChain` as separate concerns with explicit rule ordering

**Worker CLI:**
- Issue: `worker/__main__.py` is 751 lines of monolithic CLI entry point with 10+ subcommand handlers inline
- Files: `src/trading_platform/worker/__main__.py`
- Impact: Each handler references its own set of imports and state; changes to any workflow can affect CLI bootstrap time
- Fix approach: Move subcommand handlers to `worker/commands/*.py` with explicit handler interface

### Configuration and Settings Hardcoding

**Default Database Credentials in Code:**
- Issue: `settings.py` defines default database password as `"trading_platform"` (lines 44-45); visible in `.env.example`
- Files: `src/trading_platform/core/settings.py`, `.env.example`
- Impact: Reduces friction for local development but bleeds into production checklist; easy to forget overriding credentials
- Fix approach: Move password default to environment variable only; raise validation error if database password is the default in non-local environments

**Alpaca Credentials Not Documented in .env.example:**
- Issue: Alpaca API key/secret (lines 211-212) default to empty strings with no `.env.example` entry documenting their requirement
- Files: `src/trading_platform/core/settings.py`, `.env.example`
- Impact: Alpaca integration is silently unavailable without explicit env var setup; no startup validation catches this
- Fix approach: Add documented `.env.example` entries; add startup validation in `AlpacaBrokerSettings` that raises if credentials are empty in non-test environments

**Polygon API Key Required But Startup Validation Missing:**
- Issue: Market-data ingestion requires Polygon API key, but no startup check validates it before attempting reads
- Files: `src/trading_platform/services/polygon.py`, `src/trading_platform/services/ingestion.py`
- Impact: Ingestion commands fail with opaque Polygon API errors instead of clear configuration errors
- Fix approach: Add `PolygonProviderSettings.validate()` that checks API key is non-empty on first client initialization; raise `ConfigurationError` with guidance

## Known Bugs and Runtime Issues

### Environment Override Contamination

**Test Environment Overridden by Operator .env:**
- Symptoms: `test_app_bootstrap_serves_foundation_endpoints` expects `environment=test` but receives `environment=local` because operator `.env` is read by all test runs
- Files: `.env`, test configuration in `tests/test_app_boot.py`
- Trigger: Running any test with `.env` present in project root
- Workaround: Temporarily rename `.env` during test runs
- Status: Identified in `.planning/00-VERIFY.md` — focused baseline not green

**Inconsistent Session Factory Cache Cleanup:**
- Symptoms: `session_scope()` commits and closes but engine cache persists across test suite; subsequent tests may inherit connection state
- Files: `src/trading_platform/db/session.py` (lines 65-75), `src/trading_platform/tests/conftest.py` (if present)
- Trigger: Running multiple database-backed tests in sequence without explicit cache clearing
- Workaround: Explicit `clear_engine_cache()` call in test teardown
- Status: Unfixed — test isolation depends on conftest fixtures, not automatic cleanup

### Unverified External Integrations

**Polygon Read-Only Not Authorized/Tested:**
- Symptoms: API credential configured but no successful authorized read in verification pass
- Files: `src/trading_platform/services/polygon.py`, `src/trading_platform/services/ingestion.py`
- Trigger: Running `market-data ingest` command or `run-backtest` with symbol refresh
- Status: Blocker — `00-VERIFY` gate lists as UNVERIFIED (STATE.md line 18)

**Alpaca Paper Credentials Absent:**
- Symptoms: `TRADING_PLATFORM_BROKER__ALPACA__API_KEY` and `__API_SECRET` not configured; all Alpaca operations fail silently or return dummy results
- Files: `src/trading_platform/services/alpaca.py`, `src/trading_platform/services/paper_execution.py`
- Trigger: Attempting paper submission via CLI or API
- Status: Blocker — `00-VERIFY` gate lists as BLOCKED (STATE.md line 19)

### Kill Switch Runtime Blocking Not Fully Tested

**Kill Switch Integration Test Incomplete:**
- Symptoms: Kill-switch code paths and unit tests exist, but PostgreSQL-backed integration test was not authorized/completed in last verification pass
- Files: `src/trading_platform/services/operator_controls.py`, `tests/test_operator_controls.py`
- Trigger: Running full integration suite with live database
- Status: Blocker — `00-VERIFY` gate lists as UNVERIFIED (STATE.md line 20)

## Security Considerations

### Database Credentials Exposed in URL Format

**Risk:** Database URL is constructed with credentials embedded as plaintext (line 50-53 in `settings.py`)
- Files: `src/trading_platform/core/settings.py`
- Current mitigation: URL is only used in SQLAlchemy's `create_engine()` call; not logged or exposed in logs
- Recommendations: 
  1. Use SQLAlchemy's `URL()` object with separate user/password parameters instead of string interpolation
  2. Audit logging to ensure database URLs never appear in structured logs
  3. Consider raising log level on SQLAlchemy echo to SENSITIVE and filtering from output

### Operator API No Authentication Layer

**Risk:** All operator-read endpoints accept strategy_id from query parameter with no authentication or authorization
- Files: `src/trading_platform/api/routes/operations.py` (all endpoints), `src/trading_platform/api/dependencies.py`
- Current mitigation: Reads are read-only; strategy validation only checks registry knows strategy exists
- Recommendations:
  1. Implement operator authentication before Phase 8 (multi-user or session-based)
  2. Add authorization checks to enforce strategy ownership per operator
  3. Document that current implementation is single-user only (line 25 in `settings.py` enforces `operator_mode=single_user`)

### No Input Validation on Filter Ranges

**Risk:** Date range filters (`session_start`, `session_end`) are not validated for logical ordering or reasonable bounds
- Files: `src/trading_platform/api/dependencies.py` (lines 42-46)
- Current mitigation: SQLAlchemy queries are parameterized; not vulnerable to injection
- Recommendations:
  1. Add `session_start < session_end` validation in `get_operator_read_filters()`
  2. Add max date range (e.g., 2 years) to prevent OOM from large result sets
  3. Add audit logging for unusual query patterns (very wide date ranges, high limits)

### Uncaught Broad Exceptions in Paper Execution

**Risk:** Paper execution catches `Exception` (line 579) and logs without distinguishing between recoverable errors (network, broker) and unrecoverable ones (logic bugs)
- Files: `src/trading_platform/services/paper_execution.py` (lines 579-607)
- Current mitigation: All exceptions re-raise after logging; execution halts and operator is notified
- Recommendations:
  1. Distinguish `BrokerError`, `ValidationError`, and `InternalError` categories
  2. Log internal errors with full traceback; log broker errors with minimal context
  3. Implement exponential backoff only for broker transient errors, not for logic failures

## Performance Bottlenecks

### Kill Switch Checked Inside Order Submission Loop

**Problem:** Kill-switch state is reloaded from database for every candidate order (line 332-335 in `paper_execution.py`)
- Files: `src/trading_platform/services/paper_execution.py`
- Cause: Safety design to catch mid-run trip; trades latency for safety
- Improvement path:
  1. Cache kill-switch state on entry with versioning timestamp
  2. Check only once per batch instead of per-candidate
  3. Accept brief delay in operator response (next candidate submission) as acceptable vs. 10x latency multiplier

### In-Memory Engine/Session Cache No TTL

**Problem:** Database engines and session factories are cached globally with no expiration; connections may go stale during long-running processes
- Files: `src/trading_platform/db/session.py` (lines 14-15, 44-47, 54-62)
- Cause: Unbounded cache for process lifetime; never pruned
- Improvement path:
  1. Add optional TTL to engine cache (e.g., 30 minutes for long-running workers)
  2. Add pool recycle settings to `create_engine()` to force fresh connections periodically
  3. Implement explicit cache invalidation hooks for deployment scenarios

### Order State Machine No Transition Index

**Problem:** `order_events` are appended but no index on (paper_order_id, created_at) makes latest-status queries full table scan
- Files: `src/trading_platform/db/models/order_event.py`
- Cause: State projection from event log without materialized view
- Improvement path:
  1. Add (paper_order_id, created_at DESC) index
  2. Consider materializing latest status in `paper_orders.current_lifecycle_state` column (asynchronously updated)
  3. Measure impact on reconciliation latency once indexed

## Fragile Areas

### Paper Order State Machine Backward Compatibility

**Files:** `src/trading_platform/services/order_state_machine.py`, `src/trading_platform/db/models/order_event.py`
- Why fragile: `OrderTransitionEventType` and `OrderLifecycleState` are enums with limited extensibility; adding a new state requires changes to all validation logic scattered across `order_state_machine.py` and `paper_execution.py`
- Safe modification: 
  1. Add new state to `OrderLifecycleState` enum
  2. Add transition rules in `resolve_transition_target()` for new state
  3. Write tests for all legal transitions TO and FROM the new state before deploying
  4. Verify no code assumes finite state set (e.g., if-else chains instead of pattern matching)
- Test coverage: `tests/test_order_state_machine.py` covers state transitions; add tests for new state before merging

### Risk Decision Audit Trail Immutability

**Files:** `src/trading_platform/services/risk.py`, `src/trading_platform/db/models/risk_event.py`
- Why fragile: Risk decisions are persisted once; no versioning if decision code definitions change later
- Safe modification:
  1. Treat `RiskDecisionCode` enum as append-only (never delete or rename codes)
  2. Add migration if new decision code required
  3. Add audit table to track `RiskDecisionCode` definition changes
- Test coverage: No test for decision code immutability; add snapshot test for all decision codes

### Broker Order Mapping Assumes Deterministic Client Order ID

**Files:** `src/trading_platform/services/paper_execution.py` (lines 49-53), `src/trading_platform/services/reconciliation.py`
- Why fragile: Paper-to-broker mapping relies on deterministic `client_order_id` format (`tp-{hash}`); any change breaks existing order recovery
- Safe modification:
  1. Never change client_order_id generation algorithm
  2. If format must change, implement migration that rewrites all pending orders' IDs before broker submission resumes
  3. Add test that verifies `_build_client_order_id()` output is stable across runs
- Test coverage: `tests/test_paper_execution.py` should validate client_order_id determinism

## Scaling Limits

### Single-User Operator Mode

**Current capacity:** Single concurrent user/session enforced by `operator_mode=single_user` (line 25 in `settings.py`)
- Limit: Any second user request blocks on database locks or session conflicts
- Scaling path:
  1. Introduce operator identity and session tracking
  2. Add user-specific run filtering in OperatorReadService
  3. Implement advisory locks for strategy control changes (Phase 8 scope)
  4. Phase 12 refactor includes multi-user support

### Database Connection Pool Not Configured

**Current capacity:** Default SQLAlchemy pool size (5 connections) with no explicit limit
- Limit: Peak paper execution batch (20+ concurrent order submissions) may exhaust pool
- Scaling path:
  1. Configure `pool_size` and `max_overflow` in `build_engine()` based on concurrency profile
  2. Monitor pool saturation metrics
  3. Consider async SQLAlchemy if submission latency becomes bottleneck (Phase 12)

### Backtest Reporting CSV Export Unbounded

**Current capacity:** No pagination or result-size limits on backtest export
- Limit: Large backtests (5+ years of daily data) generate multi-MB CSV files in memory
- Scaling path:
  1. Implement streaming CSV writer instead of building list in memory
  2. Add result-size limit to export endpoint (e.g., max 1M rows)
  3. Implement pagination for large exports

## Dependencies at Risk

### Polygon.io Integration Not Verified in Production Path

**Risk:** Market-data pipeline depends on Polygon API; no fallback data source
- Current impact: If Polygon credential is invalid, all strategies with symbol refresh fail
- Migration plan:
  1. Add abstract `MarketDataProvider` interface
  2. Implement `PolygonMarketDataProvider` and `CachedFallbackProvider`
  3. Allow strategies to specify data source per symbol
  4. Phase 12 refactor includes multi-provider support

### Alpaca Integration Not Configured/Verified

**Risk:** Paper submission depends on Alpaca; no dry-run fallback configured
- Current impact: Alpaca credentials must be manually configured; execution path untested
- Migration plan:
  1. Add `PaperExecutionProvider` interface
  2. Implement `AlpacaPaperProvider` and `MockPaperProvider` (for testing)
  3. Allow deployment to choose provider via config
  4. Phase 8+ includes local advisory-lock testing without broker submission

### Alembic Migration Versioning

**Risk:** Migration IDs are short strings (e.g., `0015_phase7_kill_switch`); unique constraint enforces `varchar(32)` (line 32 in alembic.ini)
- Current impact: Long migration IDs (>32 chars) fail on apply
- Migration plan:
  1. Document max ID length in DEVELOPMENT.md
  2. Add pre-merge check that rejects migration files with IDs >25 chars
  3. Phase 10 refactor includes migration consolidation to reset ID sequence

## Test Coverage Gaps

### Paper Execution Retry Logic Under-tested

**Untested area:** Retry paths for broker submission failures (lines 484-510 in `paper_execution.py`)
- What's not tested: How retries interact with mid-run kill-switch trip; recovery when Alpaca is temporarily down
- Files: `src/trading_platform/services/paper_execution.py`, `tests/test_paper_execution.py`
- Risk: Retry loop could spiral if broker is down; kill switch mid-retry could leak half-submitted orders
- Priority: HIGH — Phase 7 critical path

### Reconciliation State Recovery Under-tested

**Untested area:** Recovery of in-flight orders from broker state when local database is stale (lines 150-200 in `reconciliation.py`)
- What's not tested: Broker has new order that local database knows nothing about; recovery creates new paper_order row
- Files: `src/trading_platform/services/reconciliation.py`, `tests/test_execution_reconciliation.py`
- Risk: Under-recovery could leave open broker positions without local tracking
- Priority: HIGH — Phase 9 critical path

### Risk Pipeline Edge Cases Under-tested

**Untested area:** Risk decision when portfolio state is ambiguous (stale account snapshots, missing market data)
- What's not tested: Decision rejection when latest quote is >1 session old; handling when symbol has no bars yet
- Files: `src/trading_platform/services/risk.py`, `tests/test_risk_pipeline.py`
- Risk: Risk engine could approve trades on stale data or with zero reference price
- Priority: HIGH — Phase 4 critical path

### Backtest Equity Snapshot Edge Cases

**Untested area:** Equity snapshots with zero trades or all-loss scenarios
- What's not tested: Reports with only losing trades; edge case formatting in `backtest_reporting.py`
- Files: `src/trading_platform/services/backtest_reporting.py`, `tests/test_backtest_reporting.py`
- Risk: Report export could divide by zero or return NaN metrics
- Priority: MEDIUM — Phase 3 reporting path

### Order State Machine Illegal Transitions

**Untested area:** Illegal state transitions (e.g., SUBMITTED → PENDING_SUBMISSION) log rejected audit events but don't test the log content
- What's not tested: Audit event structure and persistence for rejected transitions
- Files: `src/trading_platform/services/order_state_machine.py`, `tests/test_order_state_machine.py`
- Risk: Audit trail could be incomplete if persistence fails silently
- Priority: MEDIUM — Phase 7 correctness path

### Operator Status Rendering With Missing Data

**Untested area:** Status rendering when strategy has no recent runs or account snapshot is missing
- What's not tested: Fallback rendering; graceful null handling in `operator_status.py`
- Files: `src/trading_platform/services/operator_status.py`
- Risk: Status endpoint could return 500 when expected to return empty summary
- Priority: LOW — Phase 6 reporting path

---

*Concerns audit: 2026-07-07*
