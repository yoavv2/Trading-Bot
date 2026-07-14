# Requirements: Trading Strategy Platform

**Defined:** 2026-04-18
**Milestone:** v1.1 — Execution Correctness & Hardening
**Core Value:** Build a trustworthy, auditable trading platform that can reproducibly validate a strategy, run it in daily paper trading, and explain every action or blocked action without ambiguity.

**Milestone thesis:** Prove every order intent has exactly one legal lifecycle, one broker identity, and one audit trace before extending platform capabilities. Every requirement below is written as a testable, falsifiable invariant — "direction" language is not accepted.

## v1.1 Requirements

Requirements grouped by tier. Each is an enforceable invariant. Each maps to one roadmap phase.

### Tier 0 — Correctness Kernel

#### Order Lifecycle (ORDER)

- [ ] **ORDER-01**: `OrderState` and `OrderEvent` are closed enums — every legal state and every legal event is enumerated, and adding one requires a code change and a migration
- [ ] **ORDER-02**: A closed transition map declares every legal `(from_state, event) → to_state` tuple; any tuple not in the map is illegal by definition
- [ ] **ORDER-03**: All order-state mutations flow through a single entry point `apply_order_transition(order_id, event)`; direct mutation of order state elsewhere is forbidden and fails code review / module-boundary check
- [ ] **ORDER-04**: An illegal transition raises a typed exception (`IllegalOrderTransition`) — it is never a silent no-op, never a warning-only path
- [ ] **ORDER-05**: Every accepted or rejected transition persists a new `OrderEvent` row (append-only audit trail); updating the order row alone is insufficient
- [ ] **ORDER-06**: `apply_order_transition` contains no I/O — broker calls and other side effects occur only inside `services/execution/*`, never inside the transition function
- [ ] **ORDER-07**: Resubmission creates a new `OrderEvent`, never mutates prior events; the same `client_order_id` is reused or an explicit new version is emitted with a recorded predecessor link

#### Idempotency (IDEM)

- [ ] **IDEM-01**: `client_order_id` is deterministic, derived from `(strategy_id, session_date, symbol, side, intent_hash)` — given identical inputs, the output ID is byte-for-byte identical across processes and restarts
- [ ] **IDEM-02**: A DB-level `UNIQUE` constraint on `client_order_id` enforces one persisted row per intent; constraint violation is a caught, typed error
- [ ] **IDEM-03**: Retry for the same intent does not create a new order row when one already exists with the matching `client_order_id`; the existing row is returned instead
- [ ] **IDEM-04**: Broker-response reconciliation matches by `client_order_id` first, then falls back to `broker_order_id` only if `client_order_id` is unset

#### Concurrency (LOCK)

- [ ] **LOCK-01**: At most one active run per `(strategy_id, session_date)` can execute, enforced by a PostgreSQL advisory lock keyed on that tuple
- [ ] **LOCK-02**: The advisory lock is acquired BEFORE any side effect of the run (no broker calls, no order persistence, no state-affecting DB writes happen without the lock)
- [ ] **LOCK-03**: Every run writes `run_started_at` and `run_status=running` as its first persisted action after lock acquisition
- [ ] **LOCK-04**: A run with `status=running` past a declared heartbeat/timeout is detectable via a single query (stale-run detection)
- [ ] **LOCK-05**: New run attempt — if the advisory lock is held by another session, exit cleanly with a typed message; if the lock is free but a stale `running` row exists, mark that row `stale` and continue
- [ ] **LOCK-06**: Lock release is guaranteed on normal exit, crash (via session-scoped lock), and kill-switch trigger — verified by a restart/crash test

#### Reconciliation (RECON)

- [ ] **RECON-01**: The broker snapshot is source of truth for current quantities, positions, and fills
- [ ] **RECON-02**: Local DB is source of truth for intent and history (signals, orders, state events)
- [ ] **RECON-03**: Reconciliation is read-only — it emits findings and never mutates execution state (order rows, positions, account snapshots)
- [ ] **RECON-04**: Corrective action is a separate explicit step invoked after findings are reviewed; reconciliation and correction never share a code path
- [ ] **RECON-05**: Broker and local snapshots are loaded as typed values (dataclass/typed dict) — no dict-of-strings passes the snapshot boundary
- [ ] **RECON-06**: Matcher uses an indexed map keyed by typed identity `(symbol, account, side)`; runtime is O(n) over entity count (no nested scans)
- [ ] **RECON-07**: Findings are typed enum values from a closed set: `MISSING_LOCAL`, `MISSING_BROKER`, `QUANTITY_MISMATCH`, `PRICE_MISMATCH`, `STATE_MISMATCH`
- [ ] **RECON-08**: Flat positions (zero quantity on both sides) produce zero findings
- [ ] **RECON-09**: Reconciliation emits one materialized report; every finding is tied to its category and the source snapshots that produced it

#### Safety (SAFE)

- [x] **SAFE-01**: Kill-switch state is persisted in the DB (or a central config row) — never in-process memory only
- [x] **SAFE-02**: Kill-switch state is checked before every broker submission AND before every batch-execution entry point
- [x] **SAFE-03**: When tripped, the kill switch halts only new submissions; reconciliation, state transitions, lifecycle sync, and logging continue running
- [x] **SAFE-04**: A change to kill-switch state takes effect on the next submission check without a worker restart
- [x] **SAFE-05**: Kill-switch state is restart-safe — a tripped switch stays tripped across worker restart and is re-read at boot

### Tier 1 — Operational Hardening

#### Startup Config (CFG)

- [ ] **CFG-01**: Startup validates that all required secrets for the active execution mode (backtest / paper / live) are present; missing secret = process exit with non-zero code
- [ ] **CFG-02**: Startup validates cross-field constraints — e.g., `broker=alpaca` requires `alpaca_api_key` + `alpaca_secret`; `mode=paper` forbids configuring live endpoints
- [ ] **CFG-03**: Startup validates mutually exclusive config flags (paper vs live, broker mode combinations) — conflict = process exit
- [ ] **CFG-04**: Startup validates DB connection settings — unreachable DB = process exit before any domain code runs
- [ ] **CFG-05**: Startup validates tolerance ranges against declared typed bounds (type + min/max) — out-of-range = process exit
- [ ] **CFG-06**: All config validation runs BEFORE any service init; a single failure prevents service init entirely
- [ ] **CFG-07**: Validation failure produces a single, actionable error message naming the failed field and the expected shape — no generic "config invalid"

#### Log Sanitization (LOG)

- [ ] **LOG-01**: Application code uses one logger wrapper only; direct `logging.getLogger` use is forbidden in execution, reconciliation, and config paths (enforced by a lint rule / import boundary)
- [ ] **LOG-02**: Every logged payload passes through the sanitizer before reaching the logger
- [ ] **LOG-03**: The sanitizer redacts credentials, API keys, tokens, and connection URLs containing passwords
- [ ] **LOG-04**: The sanitizer redacts `Authorization` and equivalent auth headers from request/response logs
- [ ] **LOG-05**: Broker order IDs are hashed or truncated to last-6 by default; full ID appears only when an explicit debug flag is set
- [ ] **LOG-06**: Enforcement tests assert no emitted log line contains `password=`, `api_key=`, `Authorization:` header values, or a full broker order ID under default config

#### DB Lifecycle (DB)

- [ ] **DB-01**: One documented model governs engine and session lifecycle — process-level immutable OR explicit reloadable manager; the choice is recorded in a Key Decision entry
- [ ] **DB-02**: The `@lru_cache` vs `_ENGINE_CACHE` / `_SESSION_FACTORY_CACHE` duality is resolved — only the chosen mechanism remains in code
- [ ] **DB-03**: Engine and session access goes through one canonical import path; other paths are removed
- [ ] **DB-04**: Every execution flow runs within an explicit transaction boundary (`with session.begin():` or equivalent)
- [ ] **DB-05**: A transaction commits only after BOTH the broker call succeeds AND the state transition is persisted — partial success does not commit
- [ ] **DB-06**: When rollback occurs after a broker side effect has already happened, a reconciliation task is scheduled/enqueued (rollback cannot undo broker effect; reconciliation must follow)

### Tier 2 — Performance (only after correctness)

#### Query Performance (PERF)

- [ ] **PERF-01**: Paper-preflight issues at most 2 queries total, regardless of the number of positions or approved candidates (measured by query-count assertion in an integration test)
- [ ] **PERF-02**: Reconciliation runtime is O(n) over entity count — asserted by a benchmark test that scales linearly (not quadratically) with input size
- [ ] **PERF-03**: Every critical query (operator reads, reconciliation, order lifecycle sync) has an explicit named index; `EXPLAIN` output shows the index is used

### Tier 3 — Maintainability

#### Structural Refactor (STRUCT)

- [x] **STRUCT-01**: No structural refactor (Tier 3) lands before all Tier 0 requirements are verified complete
- [x] **STRUCT-02**: Every refactor change is no-behavior-change — full existing test suite passes before the change and after with zero new or modified assertions
- [x] **STRUCT-03**: `worker/__main__.py` is split into `worker/commands/{bootstrap,ingest,backtest,risk_check,paper_execute,reconcile}.py`; the entrypoint contains only routing (< ~100 lines)
- [x] **STRUCT-04**: Execution logic is reorganized under `services/execution/{submit_orders,sync_orders,transition,idempotency}.py`
- [x] **STRUCT-05**: Reconciliation logic is reorganized under `services/reconciliation/{snapshot,matcher,findings,report}.py`
- [x] **STRUCT-06**: Config logic is reorganized under `services/config/{validation,secrets}.py`
- [x] **STRUCT-07**: Scattered tolerance constants are extracted into a single typed config module; old scattered definitions are deleted
- [x] **STRUCT-08**: Settings are consolidated to one canonical settings surface; duplicate or competing settings modules are removed

#### Tooling (TOOL)

- [x] **TOOL-01**: Lint and format tooling (ruff or equivalent) is wired with a pre-commit or CI gate that blocks merge on failure
- [x] **TOOL-02**: Static type checking (mypy or pyright) is wired for execution, reconciliation, and config modules; CI gate blocks merge on type errors in those modules

## Future Requirements

Deferred to later milestones.

### Event Sourcing (EVT)

- **EVT-01**: Full historical replay of order lifecycle from persisted events — deferred; kill switch is the near-term primitive
- **EVT-02**: Event store separate from relational state — deferred until replay is needed operationally

### Product Expansion (PROD)

- **PROD-01**: Second strategy implementation beyond `TrendFollowingDailyV1`
- **PROD-02**: Multi-broker adapter (IBKR)
- **PROD-03**: Dashboard UI consuming operator-read APIs
- **PROD-04**: Live-mode enablement (only after v1.1 correctness kernel is validated in paper for a sustained period)

## Out of Scope

Explicitly excluded from v1.1.

| Feature | Reason |
|---------|--------|
| Full event sourcing / historical replay | Kill switch is the needed primitive; replay adds weight without current operational value |
| New strategies / intraday / multi-broker / dashboard | v1.1 is hardening only; product expansion waits on proven correctness kernel |
| Architecture redesign beyond orchestration split | Layering is sound; restructure is bounded to moving code between existing boundaries |
| Performance-first work ahead of correctness | Speed without state integrity is negative value |
| Live-trading enablement | v1.0 non-goal still holds; live mode requires sustained paper validation after v1.1 ships |
| Multi-user / auth / RBAC | Single-operator scope unchanged from v1.0 |
| Cosmetic-only refactor | Structural refactor in v1.1 is justified by orchestration bloat, not aesthetics |
| Silent-warning state transitions | Every illegal or ambiguous path is a typed error, never a log-and-continue |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| ORDER-01 | Phase 7 | Pending |
| ORDER-02 | Phase 7 | Pending |
| ORDER-03 | Phase 7 | Pending |
| ORDER-04 | Phase 7 | Pending |
| ORDER-05 | Phase 7 | Pending |
| ORDER-06 | Phase 7 | Pending |
| ORDER-07 | Phase 7 | Pending |
| IDEM-01 | Phase 7 | Pending |
| IDEM-02 | Phase 7 | Pending |
| IDEM-03 | Phase 7 | Pending |
| IDEM-04 | Phase 7 | Pending |
| LOCK-01 | Phase 8 | Pending |
| LOCK-02 | Phase 8 | Pending |
| LOCK-03 | Phase 8 | Pending |
| LOCK-04 | Phase 8 | Pending |
| LOCK-05 | Phase 8 | Pending |
| LOCK-06 | Phase 8 | Pending |
| RECON-01 | Phase 9 | Pending |
| RECON-02 | Phase 9 | Pending |
| RECON-03 | Phase 9 | Pending |
| RECON-04 | Phase 9 | Pending |
| RECON-05 | Phase 9 | Pending |
| RECON-06 | Phase 9 | Pending |
| RECON-07 | Phase 9 | Pending |
| RECON-08 | Phase 9 | Pending |
| RECON-09 | Phase 9 | Pending |
| SAFE-01 | Phase 7 | Complete |
| SAFE-02 | Phase 7 | Complete |
| SAFE-03 | Phase 7 | Complete |
| SAFE-04 | Phase 7 | Complete |
| SAFE-05 | Phase 7 | Complete |
| CFG-01 | Phase 10 | Pending |
| CFG-02 | Phase 10 | Pending |
| CFG-03 | Phase 10 | Pending |
| CFG-04 | Phase 10 | Pending |
| CFG-05 | Phase 10 | Pending |
| CFG-06 | Phase 10 | Pending |
| CFG-07 | Phase 10 | Pending |
| LOG-01 | Phase 10 | Pending |
| LOG-02 | Phase 10 | Pending |
| LOG-03 | Phase 10 | Pending |
| LOG-04 | Phase 10 | Pending |
| LOG-05 | Phase 10 | Pending |
| LOG-06 | Phase 10 | Pending |
| DB-01 | Phase 10 | Pending |
| DB-02 | Phase 10 | Pending |
| DB-03 | Phase 10 | Pending |
| DB-04 | Phase 10 | Pending |
| DB-05 | Phase 10 | Pending |
| DB-06 | Phase 10 | Pending |
| PERF-01 | Phase 11 | Pending |
| PERF-02 | Phase 11 | Pending |
| PERF-03 | Phase 11 | Pending |
| STRUCT-01 | Phase 12 | Complete |
| STRUCT-02 | Phase 12 | Complete |
| STRUCT-03 | Phase 12 | Complete |
| STRUCT-04 | Phase 12 | Complete |
| STRUCT-05 | Phase 12 | Complete |
| STRUCT-06 | Phase 12 | Complete |
| STRUCT-07 | Phase 12 | Complete |
| STRUCT-08 | Phase 12 | Complete |
| TOOL-01 | Phase 12 | Complete |
| TOOL-02 | Phase 12 | Complete |

**Coverage:**
- v1.1 requirements: 63 total
- Mapped to phases: 63
- Unmapped: 0

---
*Requirements defined: 2026-04-18*
*Last updated: 2026-04-18 after v1.1 roadmap creation (all 63 requirements mapped)*
