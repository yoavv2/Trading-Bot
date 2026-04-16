# Technical Concerns

**Analysis Date:** 2026-04-16

## Tech Debt

### Monolithic Service Files
- `src/trading_platform/worker/__main__.py` (673 lines) — command dispatch growing unbounded
- Large service files (reconciliation, paper execution) approaching 500+ lines
- **Impact:** Hard to navigate, test in isolation, or extend without conflicts

### Mutable State in Reconciliation
- Reconciliation service accumulates findings in mutable lists during processing
- **Impact:** Harder to test individual reconciliation steps, state leaks between runs possible

### Session Scope Management
- `session_scope()` context manager used everywhere but engine cached via `@lru_cache`
- **Impact:** Cache invalidation issues if connection parameters change at runtime

### Scattered Tolerance Constants
- Numeric tolerances (price comparison, position matching) defined inline in service modules
- **Impact:** Inconsistent thresholds across services, hard to audit or adjust globally

### Configuration Complexity
- Settings model in `src/trading_platform/core/settings.py` (385 lines) growing with each feature
- YAML + env var merge logic adds cognitive overhead
- **Impact:** New features require touching settings model, YAML schema, and env var handling

## Known Bugs / Edge Cases

### Order Resubmission After Broker Rejection
- When broker rejects order, resubmission path may not properly reset order state
- **Impact:** Stale order state could prevent retry or cause duplicate submissions

### Unperched Reconciliation Findings for Flat Positions
- Reconciliation may flag findings for positions that have been fully closed
- **Impact:** Noise in reconciliation reports, operator confusion

### Partial Broker Failure Handling
- If broker API fails mid-batch (e.g., 3 of 5 orders placed), rollback is incomplete
- **Impact:** Inconsistent position state between local DB and broker

## Security Considerations

### Broker Credentials in Memory
- API keys/secrets loaded into Settings object and held in memory for process lifetime
- `@lru_cache` on settings means credentials persist even if env vars change
- **Impact:** Memory dump or debug logging could expose credentials

### Database Password in Connection URL
- Connection string assembled with password inline
- **Impact:** URL may appear in logs or error tracebacks

### Missing Config Validation
- No validation that required secrets are present before attempting broker connections
- **Impact:** Cryptic errors at runtime instead of clear startup failures

### Broker Order IDs in Logs
- Structured logs include broker-side order identifiers
- **Impact:** Log aggregation systems may expose order activity to unintended audiences

## Performance Bottlenecks

### N+1 Queries in Paper Session Preflight
- Paper session preflight checks may issue separate queries per position/order
- **Impact:** Slow preflight with many open positions

### O(n^2) Reconciliation Iteration
- Reconciliation matches local vs broker positions with nested loops
- **Impact:** Degraded performance as position count grows

### Missing Database Indices
- Some query patterns (filter by strategy_id + session_date) may lack covering indices
- **Impact:** Full table scans on growing tables

## Fragile Areas

### Implicit Paper Order State Machine
- Order state transitions (pending -> placed -> filled -> reconciled) not enforced by formal state machine
- String/enum comparisons scattered across services
- **Impact:** Invalid state transitions possible, hard to add new states

### String-Based Finding Classification
- Reconciliation findings classified by string comparison rather than typed enum
- **Impact:** Typos or inconsistencies in classification silently pass

### StrEnum Decision Encoding Without Versioning
- Strategy decisions and run statuses use StrEnum persisted to DB
- No migration path if enum values need to change
- **Impact:** DB data becomes inconsistent if enum values are renamed

## Scaling Limits

### Single Strategy Instance
- System assumes one instance of each strategy runs at a time
- No distributed locking or concurrency control
- **Impact:** Running multiple workers could cause duplicate orders

### Database Connection Pool
- Connection pool size not explicitly configured
- **Impact:** Under load, pool exhaustion causes request failures

### In-Memory Reconciliation
- Full reconciliation data loaded into memory
- **Impact:** Memory pressure with large position/order histories

## At-Risk Dependencies

| Dependency | Risk |
|-----------|------|
| `httpx` | Pre-1.0, API may change |
| `exchange-calendars` | Niche library, maintenance risk |
| `PyYAML` | Stable but no schema validation built-in |
| `psycopg` | v3 relatively new, ecosystem still catching up |

## Missing Features

- **Credential rotation** — no mechanism to rotate broker keys without restart
- **Event sourcing** — no audit trail beyond StrategyRun status changes
- **Rate-limit backoff** — broker/data API calls lack retry with exponential backoff
- **Analytics caching** — analytics queries recalculate on every request
- **Emergency halt** — no kill switch to immediately stop all trading activity

## Test Coverage Gaps

- Paper session idempotency — no tests for re-running same session
- Broker API failures — limited mocking of partial failure scenarios
- Risk edge cases — boundary conditions in risk thresholds untested
- Config validation — no tests for invalid/missing config combinations
- Operator control transitions — state machine transitions not fully covered

---

*Concerns analysis: 2026-04-16*
