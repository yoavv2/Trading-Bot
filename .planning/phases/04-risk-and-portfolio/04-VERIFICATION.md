---
phase: 04-risk-and-portfolio
verified: 2026-03-14T17:20:00Z
status: verified
score: pass
re_verification: false
---

# Phase 4: Risk and Portfolio Verification

**Status:** Verified against the implemented codebase

## Goal Check

Phase goal: add deterministic sizing, portfolio state, and hard execution guardrails so no signal can bypass risk controls.

Verified outcomes:

- Every generated signal now flows through `PortfolioRiskService` before it can become an execution candidate.
- Live portfolio state is persisted in `positions` and `account_snapshots`, separate from Phase 3 backtest artifacts.
- Approved and rejected decisions persist in `risk_events` under `strategy_runs` with machine-readable codes and human-readable reasons.
- The operator can run the full signal-to-risk flow through `scripts/evaluate_risk.py` or `trading-platform-worker evaluate-risk`.

## Evidence

- Portfolio foundation: `src/trading_platform/services/portfolio.py`, `src/trading_platform/db/models/position.py`, `src/trading_platform/db/models/account_snapshot.py`
- Risk gate and audit trail: `src/trading_platform/services/risk.py`, `src/trading_platform/db/models/risk_event.py`, `src/trading_platform/db/models/strategy_run.py`
- Operator surface: `scripts/evaluate_risk.py`, `src/trading_platform/worker/__main__.py`
- Verification command passed:
  - `PYTHONPATH=src .venv/bin/python scripts/evaluate_risk.py --help`
  - `PYTHONPATH=src .venv/bin/python -m trading_platform.worker evaluate-risk --help`
  - `PYTHONPATH=src .venv/bin/pytest tests/test_risk_pipeline.py tests/test_portfolio_service.py tests/test_db_migrations.py tests/test_backtest_runner.py -q`

## Requirement Coverage

- `REQ-07`: satisfied by deterministic signal gating, stale-data blocking, duplicate prevention, max-position enforcement, allocation caps, and persisted decisions.
- `REQ-06`: satisfied for Phase 4 scope by persisting positions, account snapshots, and risk events in PostgreSQL.
- `REQ-11`: satisfied by typed portfolio runtime settings used by the live portfolio and risk services.

## Residual Risks

- Broker submission, lifecycle syncing, and reconciliation are intentionally deferred to Phase 5.
- The risk-evaluation verification slice depends on a reachable local PostgreSQL instance in this environment.
