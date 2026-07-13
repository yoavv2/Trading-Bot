# Requirements: Trading Strategy Platform — Milestone v1.2 Operator Console v0

**Defined:** 2026-07-07
**Core Value:** Build a trustworthy, auditable trading platform that can reproducibly validate a strategy, run it in daily paper trading, and explain every action or blocked action without ambiguity.

**Milestone scope rule:** Every screen that increases inspectability — yes. Every screen that adds a new capability — no. Read-only Next.js console over existing FastAPI read endpoints. No new backend capabilities.

## v1.2 Requirements

Requirements for this milestone. Each maps to roadmap phases.

### Console Foundation

- [x] **CONS-01**: Operator can start the console locally with a single documented command; it reads the FastAPI base URL from local env config
- [x] **CONS-02**: When the API is unreachable or an endpoint returns an error, the affected screen shows an explicit error state with the failing endpoint and status — never an empty or fake-success render
- [x] **CONS-03**: Every screen shows when its data was fetched (as-of timestamp) with manual refresh

### System Status

- [x] **STAT-01**: Operator can view health, environment name, and DB connection state from the health/system endpoints
- [x] **STAT-02**: Operator can view the latest run (any type) with its status and errors
- [x] **STAT-03**: Operator can view current kill-switch state on the system status screen

### Strategy Overview

- [x] **STRA-01**: Operator can view `TrendFollowingDailyV1` with its enabled/disabled status
- [x] **STRA-02**: Operator can view the strategy's config summary (universe, entry/exit rules, risk params)

### Runs

- [x] **RUNS-01**: Operator can view a runs table across backtest/risk/paper types with status, session date, created_at, and error indication
- [x] **RUNS-02**: Operator can filter the runs table by run type and status
- [x] **RUNS-03**: Operator can open a run detail page showing its signals
- [x] **RUNS-04**: Run detail shows risk decisions including blocked trades with human-readable blocked reasons
- [x] **RUNS-05**: Run detail shows orders and fills, including intent lineage (client_order_id)
- [x] **RUNS-06**: Run detail shows the run's persisted metrics

### Paper Trading Status

- [x] **PAPR-01**: Operator can view current positions
- [x] **PAPR-02**: Operator can view open orders
- [x] **PAPR-03**: Operator can view the latest reconciliation result and its findings
- [x] **PAPR-04**: Operator can view the latest account snapshot (equity, cash, buying power)

### Analytics

- [x] **ANLX-01**: Operator can view an equity curve chart for a selected backtest run
- [x] **ANLX-02**: Operator can view summary metrics for a run: Sharpe, max drawdown, win rate, P&L, trade count

### Kill Switch

- [x] **KILL-01**: A tripped kill switch is visibly indicated on every console screen (global banner), not only on the status page

## v1.1 Resumed — Concurrency Guard (Phase 8)

Migrated from `.planning/milestones/v1.1-paused/REQUIREMENTS.md` on 2026-07-12 after v1.2 shipped and the `00-VERIFY` gate went green. These are the v1.1 Tier-0 concurrency requirements now active for Phase 8 planning. (RECON, CFG, LOG, DB, PERF, STRUCT, TOOL remain paused in the archive until their phases resume.)

### Concurrency (LOCK)

- [x] **LOCK-01**: At most one active run per `(strategy_id, session_date)` can execute, enforced by a PostgreSQL advisory lock keyed on that tuple
- [x] **LOCK-02**: The advisory lock is acquired BEFORE any side effect of the run (no broker calls, no order persistence, no state-affecting DB writes happen without the lock)
- [x] **LOCK-03**: Every run writes `run_started_at` and `run_status=running` as its first persisted action after lock acquisition
- [x] **LOCK-04**: A run with `status=running` past a declared heartbeat/timeout is detectable via a single query (stale-run detection)
- [x] **LOCK-05**: New run attempt — if the advisory lock is held by another session, exit cleanly with a typed message; if the lock is free but a stale `running` row exists, mark that row `stale` and continue
- [x] **LOCK-06**: Lock release is guaranteed on normal exit, crash (via session-scoped lock), and kill-switch trigger — verified by a restart/crash test

## v1.1 Resumed — Reconciliation Rewrite (Phase 9)

Migrated from `.planning/milestones/v1.1-paused/REQUIREMENTS.md` on 2026-07-13 after Phase 8 (Concurrency Guard) completed. These are the v1.1 reconciliation requirements now active for Phase 9 planning. (CFG, LOG, DB, PERF, STRUCT, TOOL remain paused in the archive until their phases resume.)

### Reconciliation (RECON)

- [x] **RECON-01**: The broker snapshot is source of truth for current quantities, positions, and fills
- [x] **RECON-02**: Local DB is source of truth for intent and history (signals, orders, state events)
- [x] **RECON-03**: Reconciliation is read-only — it emits findings and never mutates execution state (order rows, positions, account snapshots)
- [x] **RECON-04**: Corrective action is a separate explicit step invoked after findings are reviewed; reconciliation and correction never share a code path
- [ ] **RECON-05**: Broker and local snapshots are loaded as typed values (dataclass/typed dict) — no dict-of-strings passes the snapshot boundary
- [x] **RECON-06**: Matcher uses an indexed map keyed by typed identity `(symbol, account, side)`; runtime is O(n) over entity count (no nested scans)
- [ ] **RECON-07**: Findings are typed enum values from a closed set: `MISSING_LOCAL`, `MISSING_BROKER`, `QUANTITY_MISMATCH`, `PRICE_MISMATCH`, `STATE_MISMATCH`
- [x] **RECON-08**: Flat positions (zero quantity on both sides) produce zero findings
- [x] **RECON-09**: Reconciliation emits one materialized report; every finding is tied to its category and the source snapshots that produced it

## v1.1 Resumed — Startup Hardening (Phase 10)

Migrated from `.planning/milestones/v1.1-paused/REQUIREMENTS.md` on 2026-07-13 after Phase 9 (Reconciliation Rewrite) completed. These are the v1.1 startup-hardening requirements now active for Phase 10 planning. (PERF, STRUCT, TOOL remain paused in the archive until their phases resume.)

### Config Validation (CFG)

- [x] **CFG-01**: Startup validates that all required secrets for the active execution mode (backtest / paper / live) are present; missing secret = process exit with non-zero code
- [x] **CFG-02**: Startup validates cross-field constraints — e.g., `broker=alpaca` requires `alpaca_api_key` + `alpaca_secret`; `mode=paper` forbids configuring live endpoints
- [x] **CFG-03**: Startup validates mutually exclusive config flags (paper vs live, broker mode combinations) — conflict = process exit
- [x] **CFG-04**: Startup validates DB connection settings — unreachable DB = process exit before any domain code runs
- [x] **CFG-05**: Startup validates tolerance ranges against declared typed bounds (type + min/max) — out-of-range = process exit
- [x] **CFG-06**: All config validation runs BEFORE any service init; a single failure prevents service init entirely
- [x] **CFG-07**: Validation failure produces a single, actionable error message naming the failed field and the expected shape — no generic "config invalid"

### Log Sanitization (LOG)

- [ ] **LOG-01**: Application code uses one logger wrapper only; direct `logging.getLogger` use is forbidden in execution, reconciliation, and config paths (enforced by a lint rule / import boundary)
- [x] **LOG-02**: Every logged payload passes through the sanitizer before reaching the logger
- [x] **LOG-03**: The sanitizer redacts credentials, API keys, tokens, and connection URLs containing passwords
- [x] **LOG-04**: The sanitizer redacts `Authorization` and equivalent auth headers from request/response logs
- [x] **LOG-05**: Broker order IDs are hashed or truncated to last-6 by default; full ID appears only when an explicit debug flag is set
- [ ] **LOG-06**: Enforcement tests assert no emitted log line contains `password=`, `api_key=`, `Authorization:` header values, or a full broker order ID under default config

### DB Lifecycle (DB)

- [x] **DB-01**: One documented model governs engine and session lifecycle — process-level immutable OR explicit reloadable manager; the choice is recorded in a Key Decision entry
- [x] **DB-02**: The `@lru_cache` vs `_ENGINE_CACHE` / `_SESSION_FACTORY_CACHE` duality is resolved — only the chosen mechanism remains in code
- [x] **DB-03**: Engine and session access goes through one canonical import path; other paths are removed
- [x] **DB-04**: Every execution flow runs within an explicit transaction boundary (`with session.begin():` or equivalent)
- [x] **DB-05**: A transaction commits only after BOTH the broker call succeeds AND the state transition is persisted — partial success does not commit
- [x] **DB-06**: When rollback occurs after a broker side effect has already happened, a reconciliation task is scheduled/enqueued (rollback cannot undo broker effect; reconciliation must follow)

## Future Requirements

Deferred. Tracked but not in current roadmap.

### Console Controls (post-v1.2, after backend verification)

- **CTRL-01**: Operator can trip/reset the kill switch from the console behind a very explicit local-only confirmation flow
- **CTRL-02**: Operator can enable/disable a strategy from the console

### v1.1 Remaining Hardening (paused milestone)

LOCK (Concurrency Guard, Phase 8) resumed 2026-07-12, RECON (Reconciliation Rewrite, Phase 9) resumed 2026-07-13, and CFG/LOG/DB (Startup Hardening, Phase 10) resumed 2026-07-13 — all now active above under "v1.1 Resumed". The rest remain paused: see `.planning/milestones/v1.1-paused/REQUIREMENTS.md` — PERF, STRUCT, TOOL requirements resume with their respective phases.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Real-time websocket dashboard | Polling/manual refresh sufficient for a verification instrument; realtime adds weight without inspectability gain |
| Mobile app | Local-first single-operator tool |
| Multi-user auth/RBAC | One operator; auth complexity has no product value in v1.x |
| SaaS-style onboarding | Not a public product |
| Strategy builder | Strategies remain code-defined |
| Multi-strategy comparison beyond existing API | No new backend capability in this milestone |
| Live trading controls | No live trading exists; console is read-only |
| Any write/mutation from the UI (incl. kill switch) | Console v0 is inspection-only; mutations deferred to CTRL-01/02 |
| Polished visuals hiding backend uncertainty | UI must expose system state honestly, including errors and unverified areas |
| New FastAPI endpoints beyond the existing read surface | Milestone rule: no new backend capabilities. Approved narrow exceptions (2026-07-07): one GET route exposing existing `get_kill_switch_state()` (Phase 13) and `equity_curve` field added to existing analytics response (Phase 16) — read-only exposure of already-computed state only |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CONS-01 | Phase 13 | Complete |
| CONS-02 | Phase 13 | Complete |
| CONS-03 | Phase 13 | Complete |
| STAT-01 | Phase 13 | Complete |
| STAT-02 | Phase 13 | Complete |
| STAT-03 | Phase 13 | Complete |
| STRA-01 | Phase 14 | Complete |
| STRA-02 | Phase 14 | Complete |
| RUNS-01 | Phase 14 | Complete |
| RUNS-02 | Phase 14 | Complete |
| RUNS-03 | Phase 14 | Complete |
| RUNS-04 | Phase 14 | Complete |
| RUNS-05 | Phase 14 | Complete |
| RUNS-06 | Phase 14 | Complete |
| PAPR-01 | Phase 15 | Complete |
| PAPR-02 | Phase 15 | Complete |
| PAPR-03 | Phase 15 | Complete |
| PAPR-04 | Phase 15 | Complete |
| ANLX-01 | Phase 16 | Complete (16-02 frontend + 16-01 backend equity_curve exposure delivered; 16-03 operator live-verify confirmed the Recharts equity curve renders real data end-to-end, incl. the dcd4232 YAxis auto-scale fix) |
| ANLX-02 | Phase 16 | Complete |
| KILL-01 | Phase 13 | Complete |
| LOCK-01 | Phase 8 | Complete |
| LOCK-02 | Phase 8 | Complete |
| LOCK-03 | Phase 8 | Complete |
| LOCK-04 | Phase 8 | Complete |
| LOCK-05 | Phase 8 | Complete |
| LOCK-06 | Phase 8 | Complete |
| RECON-01 | Phase 9 | Complete |
| RECON-02 | Phase 9 | Complete |
| RECON-03 | Phase 9 | Complete |
| RECON-04 | Phase 9 | Complete |
| RECON-05 | Phase 9 | Pending |
| RECON-06 | Phase 9 | Complete |
| RECON-07 | Phase 9 | Pending |
| RECON-08 | Phase 9 | Complete |
| RECON-09 | Phase 9 | Complete |
| CFG-01 | Phase 10 | Complete |
| CFG-02 | Phase 10 | Complete |
| CFG-03 | Phase 10 | Complete |
| CFG-04 | Phase 10 | Complete |
| CFG-05 | Phase 10 | Complete |
| CFG-06 | Phase 10 | Complete |
| CFG-07 | Phase 10 | Complete |
| LOG-01 | Phase 10 | Pending |
| LOG-02 | Phase 10 | Complete |
| LOG-03 | Phase 10 | Complete |
| LOG-04 | Phase 10 | Complete |
| LOG-05 | Phase 10 | Complete |
| LOG-06 | Phase 10 | Pending |
| DB-01 | Phase 10 | Complete |
| DB-02 | Phase 10 | Complete |
| DB-03 | Phase 10 | Complete |
| DB-04 | Phase 10 | Complete |
| DB-05 | Phase 10 | Complete |
| DB-06 | Phase 10 | Complete |

**Coverage:**
- v1.2 requirements: 21 total
- Mapped to phases: 21
- Unmapped: 0 ✓

**Known gaps (resolved):** STAT-03 and KILL-01 (Phase 13) and ANLX-01 (Phase 16) originally depended on unwired backend read surfaces (see ROADMAP.md "Known Gaps (Backend Read-Surface)"). All three narrow backend exceptions have shipped: Phase 13 added the thin GET kill-switch route (STAT-03, KILL-01) and Phase 16-01 added the `equity_curve` passthrough (ANLX-01). ANLX-01 is now Complete — 16-03 operator live-verify confirmed the equity curve renders real data end-to-end. No open coverage gaps remain.

---
*Requirements defined: 2026-07-07*
*Last updated: 2026-07-07 after roadmap creation — all 21 v1.2 requirements mapped to phases 13-16; 3 known backend read-surface gaps flagged in ROADMAP.md*
