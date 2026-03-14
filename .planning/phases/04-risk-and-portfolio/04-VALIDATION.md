---
phase: 04
slug: risk-and-portfolio
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-14
---

# Phase 04 - Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `PYTHONPATH=src .venv/bin/pytest tests/test_portfolio_service.py tests/test_risk_pipeline.py tests/test_db_migrations.py -q` |
| **Full suite command** | `PYTHONPATH=src .venv/bin/pytest tests -q` |
| **Estimated runtime** | ~90 seconds |

---

## Sampling Rate

- **After every task commit:** Run `PYTHONPATH=src .venv/bin/pytest <task-specific test files> -q`
- **After every plan wave:** Run `PYTHONPATH=src .venv/bin/pytest tests -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | REQ-11 | unit | `PYTHONPATH=src .venv/bin/pytest tests/test_portfolio_service.py -q` | created in 04-01 | ⬜ pending |
| 04-01-02 | 01 | 1 | REQ-06 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_db_migrations.py -q` | existing | ⬜ pending |
| 04-01-03 | 01 | 1 | REQ-07 | regression | `PYTHONPATH=src .venv/bin/pytest tests/test_portfolio_service.py tests/test_db_migrations.py tests/test_backtest_runner.py -q` | created in 04-01 | ⬜ pending |
| 04-02-01 | 02 | 2 | REQ-07 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_risk_pipeline.py -q` | created in 04-02 | ⬜ pending |
| 04-02-02 | 02 | 2 | REQ-06 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_db_migrations.py tests/test_risk_pipeline.py -q` | existing | ⬜ pending |
| 04-02-03 | 02 | 2 | REQ-07 | integration | `PYTHONPATH=src .venv/bin/python scripts/evaluate_risk.py --help` and `PYTHONPATH=src .venv/bin/python -m trading_platform.worker evaluate-risk --help` and `PYTHONPATH=src .venv/bin/pytest tests/test_risk_pipeline.py tests/test_portfolio_service.py tests/test_db_migrations.py tests/test_backtest_runner.py -q` | created in 04-02 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. `pytest`, the temp-PostgreSQL integration-test pattern, Alembic migration checks, and worker CLI conventions are already in place; the new test files and CLI entrypoint are created inside the Phase 4 plan tasks that immediately verify them.

---

## Manual-Only Verifications

All phase behaviors have automated verification.

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
