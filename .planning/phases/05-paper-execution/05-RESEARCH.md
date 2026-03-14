# Phase 5: Paper Execution - Research

**Researched:** 2026-03-14
**Domain:** Alpaca paper-order submission, lifecycle syncing, scheduling, and reconciliation on top of the existing deterministic strategy and planned Phase 4 risk pipeline
**Confidence:** HIGH

<planning_inputs>
## Planning Inputs

### Available Context
- No `05-CONTEXT.md` exists for this phase. Planning uses `.planning/PROJECT.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`, the completed Phase 3 summary, the Phase 4 research and plan files, and the current codebase.
- `.planning/REQUIREMENTS.md` is not present in this repo, so requirement mapping must be derived from `.planning/ROADMAP.md` and `.planning/PROJECT.md`.
- Phase 5 depends on Phase 4 execution, but only the Phase 4 research and plan files exist today. The Paper Execution plans must therefore be explicit about consuming the Phase 4 portfolio and risk artifacts once `04-01` and `04-02` are completed.
- The current codebase already has the core upstream seams this phase should compose on: typed settings, a strategy registry, deterministic signal generation, persisted market sessions and daily bars, `strategy_runs` as the canonical run root, and a worker or script pattern for operator-facing CLI flows.

### Locked Decisions From Project State
- The platform remains local-first, PostgreSQL-backed, and CLI-first in v1.
- Alpaca is the only v1 paper broker. Broker state is the source of truth and internal state must reconcile to it.
- Every signal must pass through the Phase 4 risk pipeline before it can become an executable broker order. Phase 5 must consume approved execution candidates, not recreate risk logic.
- The first implementation remains single-user, one broker account, one portfolio, and one strategy.
- Daily execution must be auditable the next day: submitted orders, fills, blocked trades, retries, and current state all need durable persistence.

### Claude's Discretion
- Whether to implement Alpaca via a thin `httpx` client or a broker SDK. The current codebase strongly favors thin `httpx` clients with explicit response mapping.
- Whether scheduling is implemented as an internal worker loop, an idempotent command that can be called by a scheduler, or both.
- The exact normalization shape for order status history and reconciliation findings, as long as broker state can be replayed and compared against local state.

</planning_inputs>

<research_summary>
## Summary

Phase 5 should stay narrowly execution-focused and preserve the current architecture:

1. Keep broker-specific HTTP concerns isolated behind a real execution service and Alpaca client. Do not let strategy generation, portfolio math, or risk validation import Alpaca semantics.
2. Treat `strategy_runs` as the execution batch root for paper-trading sessions, then hang durable `paper_orders`, `paper_fills`, and execution or reconciliation events off that root.
3. Keep the paper-trading runner idempotent and session-aware. Scheduling should call a deterministic session runner rather than embedding business logic inside a long-lived loop.
4. Update positions and account snapshots from broker reads, not only from local assumptions. The platform direction explicitly says broker state is the source of truth.
5. Split the phase into three sequential plans that mirror the roadmap and keep verification tight:
   - `05-01`: typed Alpaca broker adapter, order-intent submission, and persisted paper-order foundation
   - `05-02`: scheduled paper-session runner, order lifecycle syncing, fill ingestion, and broker-derived live-state updates
   - `05-03`: reconciliation, restart-safe idempotency, and execution stop conditions for unsafe broker state

**Primary recommendation:** Build a thin `httpx`-based `AlpacaClient`, extend `ExecutionService` into a real paper-execution seam, persist each broker order under a `strategy_runs` batch root with durable local idempotency keys, then layer scheduling and reconciliation around that runner instead of mixing those concerns together.

</research_summary>

<codebase_findings>
## Codebase Findings

### Existing Reusable Assets
- `src/trading_platform/services/execution.py` is still a placeholder contract, which gives Phase 5 a clean seam to replace with real execution behavior.
- `src/trading_platform/services/polygon.py` shows the project's preferred integration style: a thin `httpx` client, typed settings, explicit retries, and response normalization without a heavy SDK abstraction.
- `src/trading_platform/worker/__main__.py` already defines the repo's operator-facing pattern: scriptable subcommands that load settings, configure logging, call a service, and print JSON or markdown summaries.
- `scripts/run_backtest.py`, `scripts/generate_signals.py`, and `scripts/export_backtest_report.py` show the expected standalone CLI wrapper pattern for service orchestration.
- `src/trading_platform/db/models/strategy_run.py` already acts as the canonical run root for dry runs and backtests. Extending that enum and table is lower risk than inventing a separate execution-batch root.
- `src/trading_platform/strategies/signals.py` and `src/trading_platform/strategies/trend_following_daily/strategy.py` explicitly keep strategy output free of broker, portfolio, and risk concerns. Phase 5 should preserve that boundary.
- `src/trading_platform/services/market_data_access.py` already provides latest-session resolution helpers the scheduled runner can reuse to avoid wall-clock-only logic.

### Current Gaps Blocking Phase 5
- There is no real execution service, no broker settings surface, and no Alpaca client.
- There are no normalized order, fill, or reconciliation tables yet.
- The worker has no paper-execution or broker-sync command surface.
- The project has no mechanism yet for idempotent order submission, restart-safe resumption, or broker-to-local reconciliation.
- Current tests cover PostgreSQL-backed flows well, but there is no broker-focused test slice yet.

### Planning Implications
- Phase 5 should add broker integration by following the current `httpx` service pattern rather than bringing in a second style or a hidden SDK-driven control flow.
- The paper-execution runner should be deterministic and callable both manually and on a schedule.
- Broker-derived positions and account snapshots should be updated by sync or reconciliation flows, not by local assumptions alone.
- Reconciliation and stop conditions must be explicit Phase 5 work rather than an afterthought in Phase 6, because the product principles already define broker alignment and restart safety as execution requirements.

</codebase_findings>

<implementation_edge_cases>
## Implementation Edge Cases

### Idempotent Order Submission
- A restart or retry must not create duplicate broker orders for the same approved execution candidate.
- Phase 5 should assign and persist stable `client_order_id` values before submission so retries can detect or recover the prior broker order instead of blindly re-sending.

### Session Boundary and Scheduling
- Daily execution should resolve the target trading session from persisted sessions and the latest completed bar coverage, not from naive wall-clock time alone.
- The runner must record which session it executed so the same session is not re-run accidentally after a restart or a second scheduler tick.

### Exit-First Ordering
- The backtest runner already processes exits before entries on a fill session to preserve deterministic slot rotation.
- The paper-execution flow should preserve that principle when turning approved risk decisions into broker orders so it does not diverge from the documented strategy behavior.

### Broker State Mapping
- Alpaca order statuses, partial fills, cancels, rejects, and replace-like behavior must map into a stable local status model instead of leaking raw provider strings throughout the codebase.
- Positions and account snapshots should be refreshed from broker reads so local state can detect drift rather than assuming submissions always succeed exactly once.

### Unsafe State Detection
- Repeated broker submission failures, missing order acknowledgements, or mismatches between broker positions and local open orders should block new execution until reconciled.
- These stop conditions need durable persistence so the operator can inspect why execution halted on the next day.

</implementation_edge_cases>

<recommendations>
## Recommended Architecture

### Configuration Shape
- Add a typed `broker.alpaca` or equivalent block in `config/app.yaml` and `src/trading_platform/core/settings.py` for:
  - `base_url`
  - `api_key`
  - `api_secret`
  - `paper` or environment selector
  - timeout and retry settings
- Add typed execution-runtime settings for:
  - default order time-in-force and side constraints
  - session scheduling parameters
  - broker sync polling interval
  - repeated-failure thresholds and unsafe-state stop policy

### Persistence Shape
- Extend `strategy_runs` with a Phase 5 run type such as `paper_execution`, and add later sync or reconciliation run types only if they materially improve auditability.
- Add normalized execution tables for:
  - `paper_orders`
  - `paper_fills`
  - `execution_events` or `reconciliation_events`
- Reuse the Phase 4 `positions` and `account_snapshots` tables as the durable live-state objects that broker sync updates.

### Broker Integration
- Introduce `src/trading_platform/services/alpaca.py` as a thin `httpx` client with:
  - typed request or response mapping
  - explicit auth-header handling
  - timeout and retry logic aligned with `PolygonClient`
  - narrow methods for submit order, fetch orders, fetch fills or activities, fetch positions, and fetch account
- Keep provider-specific payload translation inside this module, not in orchestration or strategy code.

### Execution Orchestration
- Replace the placeholder `ExecutionService` with a real service layer that:
  - accepts approved execution candidates from the Phase 4 risk pipeline
  - assigns deterministic local idempotency keys and batch metadata
  - submits orders via the Alpaca client
  - persists order state before and after broker acknowledgement
- Add a session runner that:
  - resolves the target execution session
  - loads approved decisions for that session
  - submits missing orders exactly once
  - prints a concise summary for operator inspection

### Lifecycle Sync and Reconciliation
- Add a dedicated sync path that pulls broker order states, fills, positions, and account data into PostgreSQL.
- Add a reconciliation service that compares:
  - broker open orders vs local `paper_orders`
  - broker fills vs local `paper_fills`
  - broker positions and account state vs local `positions` and `account_snapshots`
- Persist mismatch findings and execution-stop decisions so the next session runner can block safely when drift is unresolved.

### Testing Strategy
- Add `tests/test_alpaca_execution.py` for request mapping, auth headers, retry behavior, client-order-id persistence, and order-response normalization.
- Add `tests/test_paper_execution.py` for session-resolution, idempotent runner behavior, order submission, lifecycle syncing, and live-state updates using mocked broker responses.
- Add `tests/test_execution_reconciliation.py` for mismatch detection, repeated-failure stop conditions, and restart-safe resume behavior.
- Extend `tests/test_db_migrations.py` to verify the new Phase 5 tables and any new `strategy_run_type` enum values.
- Use built-in `httpx` transport mocking or monkeypatching rather than introducing a new network-test dependency unless truly necessary.

</recommendations>

## Validation Architecture

- `tests/test_alpaca_execution.py`
  - verify typed Alpaca request mapping, auth, retries, and broker response normalization
- `tests/test_paper_execution.py`
  - verify session runner idempotency, daily scheduling behavior, order submission, lifecycle sync, and broker-derived position or account updates
- `tests/test_execution_reconciliation.py`
  - verify mismatch detection, repeated-failure blocking, and restart-safe execution resumption
- Extend `tests/test_db_migrations.py`
  - verify `paper_orders`, `paper_fills`, and `execution_events` plus any new `strategy_run_type` labels land correctly at Alembic head
- CLI checks
  - verify `scripts/submit_paper_orders.py --help`, `scripts/run_paper_session.py --help`, and `scripts/reconcile_paper_execution.py --help`
  - verify the matching worker subcommands also render help successfully

**Quick command:** `PYTHONPATH=src .venv/bin/pytest tests/test_alpaca_execution.py tests/test_paper_execution.py tests/test_execution_reconciliation.py tests/test_db_migrations.py -q`

**Full command:** `PYTHONPATH=src .venv/bin/pytest tests -q`

## Plan Split Recommendation

### 05-01: Implement the Alpaca broker adapter and paper-order submission flow
- Own typed broker settings, the thin Alpaca client, the real execution-service seam, and persisted paper-order records anchored to `strategy_runs`.
- Keep the scope limited to turning approved execution candidates into durable submitted-order records. Do not mix scheduling, polling, or reconciliation into this first slice.

### 05-02: Add scheduled daily execution, order lifecycle updates, and fill ingestion
- Own the idempotent paper-session runner, worker or script scheduling surface, broker-order status syncing, fill ingestion, and broker-derived updates to positions and account snapshots.
- Keep the work focused on the daily operating loop and lifecycle state, not on mismatch resolution policy.

### 05-03: Build reconciliation, restart safety, and execution-stop conditions for unsafe broker state
- Own mismatch detection, repeated-failure guards, restart-safe replay logic, and operator-visible stop reasons.
- Make the execution runner refuse new submissions when broker alignment is unsafe instead of allowing silent drift.

## Sources

No external sources were required for this phase beyond the current project planning docs and codebase.

Current project sources:
- `.planning/PROJECT.md`
- `.planning/ROADMAP.md`
- `.planning/STATE.md`
- `.planning/phases/04-risk-and-portfolio/04-RESEARCH.md`
- `.planning/phases/04-risk-and-portfolio/04-01-PLAN.md`
- `.planning/phases/04-risk-and-portfolio/04-02-PLAN.md`
- `.planning/phases/03-backtest-and-reporting/03-03-SUMMARY.md`
- `src/trading_platform/services/execution.py`
- `src/trading_platform/services/polygon.py`
- `src/trading_platform/worker/__main__.py`
- `src/trading_platform/core/settings.py`
- `src/trading_platform/db/models/strategy_run.py`
- `src/trading_platform/strategies/signals.py`
- `src/trading_platform/services/market_data_access.py`
- `scripts/generate_signals.py`
- `tests/test_backtest_runner.py`
- `tests/test_db_migrations.py`
- `tests/test_dry_run.py`

---
*Phase: 05-paper-execution*
*Research completed: 2026-03-14*
