---
phase: 03
slug: backtest-and-reporting
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-14
---

# Phase 03 - Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `PYTHONPATH=src .venv/bin/pytest tests/test_backtest_runner.py tests/test_backtest_reporting.py tests/test_db_migrations.py -q` |
| **Full suite command** | `PYTHONPATH=src .venv/bin/pytest tests -q` |
| **Estimated runtime** | ~75 seconds |

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
| 03-01-01 | 01 | 1 | REQ-11 | regression | `PYTHONPATH=src .venv/bin/pytest tests/test_dry_run.py -q` | existing | ⬜ pending |
| 03-01-02 | 01 | 1 | REQ-06 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_db_migrations.py -q` | existing | ⬜ pending |
| 03-01-03 | 01 | 1 | REQ-05 | regression | `PYTHONPATH=src .venv/bin/pytest tests/test_db_migrations.py tests/test_dry_run.py -q` | existing | ⬜ pending |
| 03-02-01 | 02 | 2 | REQ-05 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_backtest_runner.py -q` | created in 03-02 | ⬜ pending |
| 03-02-02 | 02 | 2 | REQ-05 | integration | `PYTHONPATH=src .venv/bin/python scripts/run_backtest.py --help` and `PYTHONPATH=src .venv/bin/python -m trading_platform.worker backtest --help` | created in 03-02 | ⬜ pending |
| 03-02-03 | 02 | 2 | REQ-06 | regression | `PYTHONPATH=src .venv/bin/pytest tests/test_backtest_runner.py tests/test_dry_run.py -q` | created in 03-02 | ⬜ pending |
| 03-03-01 | 03 | 3 | REQ-05 | unit | `PYTHONPATH=src .venv/bin/pytest tests/test_backtest_reporting.py tests/test_db_migrations.py -q` | created in 03-03 | ⬜ pending |
| 03-03-02 | 03 | 3 | REQ-05 | integration | `PYTHONPATH=src .venv/bin/python scripts/export_backtest_report.py --help` and `PYTHONPATH=src .venv/bin/python -m trading_platform.worker report-backtest --help` | created in 03-03 | ⬜ pending |
| 03-03-03 | 03 | 3 | REQ-06 | regression | `PYTHONPATH=src .venv/bin/pytest tests/test_backtest_runner.py tests/test_backtest_reporting.py tests/test_db_migrations.py tests/test_dry_run.py -q` | created in 03-03 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. `pytest`, the temp-PostgreSQL test pattern, Alembic migration tests, and the worker CLI already exist; the new test files and scripts are created within the plan tasks that immediately verify them, so no separate Wave 0 dependency is required.

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
