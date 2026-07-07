# Requirements: Trading Strategy Platform — Milestone v1.2 Operator Console v0

**Defined:** 2026-07-07
**Core Value:** Build a trustworthy, auditable trading platform that can reproducibly validate a strategy, run it in daily paper trading, and explain every action or blocked action without ambiguity.

**Milestone scope rule:** Every screen that increases inspectability — yes. Every screen that adds a new capability — no. Read-only Next.js console over existing FastAPI read endpoints. No new backend capabilities.

## v1.2 Requirements

Requirements for this milestone. Each maps to roadmap phases.

### Console Foundation

- [ ] **CONS-01**: Operator can start the console locally with a single documented command; it reads the FastAPI base URL from local env config
- [ ] **CONS-02**: When the API is unreachable or an endpoint returns an error, the affected screen shows an explicit error state with the failing endpoint and status — never an empty or fake-success render
- [ ] **CONS-03**: Every screen shows when its data was fetched (as-of timestamp) with manual refresh

### System Status

- [ ] **STAT-01**: Operator can view health, environment name, and DB connection state from the health/system endpoints
- [ ] **STAT-02**: Operator can view the latest run (any type) with its status and errors
- [ ] **STAT-03**: Operator can view current kill-switch state on the system status screen

### Strategy Overview

- [ ] **STRA-01**: Operator can view `TrendFollowingDailyV1` with its enabled/disabled status
- [ ] **STRA-02**: Operator can view the strategy's config summary (universe, entry/exit rules, risk params)

### Runs

- [ ] **RUNS-01**: Operator can view a runs table across backtest/risk/paper types with status, session date, created_at, and error indication
- [ ] **RUNS-02**: Operator can filter the runs table by run type and status
- [ ] **RUNS-03**: Operator can open a run detail page showing its signals
- [ ] **RUNS-04**: Run detail shows risk decisions including blocked trades with human-readable blocked reasons
- [ ] **RUNS-05**: Run detail shows orders and fills, including intent lineage (client_order_id)
- [ ] **RUNS-06**: Run detail shows the run's persisted metrics

### Paper Trading Status

- [ ] **PAPR-01**: Operator can view current positions
- [ ] **PAPR-02**: Operator can view open orders
- [ ] **PAPR-03**: Operator can view the latest reconciliation result and its findings
- [ ] **PAPR-04**: Operator can view the latest account snapshot (equity, cash, buying power)

### Analytics

- [ ] **ANLX-01**: Operator can view an equity curve chart for a selected backtest run
- [ ] **ANLX-02**: Operator can view summary metrics for a run: Sharpe, max drawdown, win rate, P&L, trade count

### Kill Switch

- [ ] **KILL-01**: A tripped kill switch is visibly indicated on every console screen (global banner), not only on the status page

## Future Requirements

Deferred. Tracked but not in current roadmap.

### Console Controls (post-v1.2, after backend verification)

- **CTRL-01**: Operator can trip/reset the kill switch from the console behind a very explicit local-only confirmation flow
- **CTRL-02**: Operator can enable/disable a strategy from the console

### v1.1 Remaining Hardening (paused milestone)

See `.planning/milestones/v1.1-paused/REQUIREMENTS.md` — LOCK, RECON, CFG, LOG, DB, PERF, STRUCT, TOOL requirements resume after v1.2.

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
| New FastAPI endpoints beyond the existing read surface | Milestone rule: no new backend capabilities |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| — | — | — |

**Coverage:**
- v1.2 requirements: 21 total
- Mapped to phases: 0
- Unmapped: 21 ⚠️ (roadmap pending)

---
*Requirements defined: 2026-07-07*
*Last updated: 2026-07-07 after initial definition*
