---
phase: 06
slug: analytics-and-apis
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-14
---

# Phase 06 - Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `PYTHONPATH=src .venv/bin/pytest tests/test_analytics_service.py tests/test_api_reads.py tests/test_operator_controls.py tests/test_backtest_reporting.py tests/test_paper_execution.py tests/test_app_boot.py tests/test_db_migrations.py -q` |
| **Full suite command** | `PYTHONPATH=src .venv/bin/pytest tests -q` |
| **Estimated runtime** | ~150 seconds |

---

## Sampling Rate

- **After every task commit:** Run `PYTHONPATH=src .venv/bin/pytest <task-specific test files> -q`
- **After every plan wave:** Run `PYTHONPATH=src .venv/bin/pytest tests -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 150 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | REQ-09 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_analytics_service.py tests/test_backtest_reporting.py tests/test_db_migrations.py -q` | created in 06-01 | ✅ green |
| 06-01-02 | 01 | 1 | REQ-09 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_analytics_service.py -q` | created in 06-01 | ✅ green |
| 06-01-03 | 01 | 1 | REQ-09 | integration | `PYTHONPATH=src .venv/bin/python scripts/report_strategy_analytics.py --help` and `PYTHONPATH=src .venv/bin/python -m trading_platform.worker report-strategy-analytics --help` and `PYTHONPATH=src .venv/bin/pytest tests/test_analytics_service.py tests/test_backtest_reporting.py -q` | created in 06-01 | ✅ green |
| 06-02-01 | 02 | 2 | REQ-09 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_api_reads.py -q` | created in 06-02 | ✅ green |
| 06-02-02 | 02 | 2 | REQ-09 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_api_reads.py -q` | created in 06-02 | ✅ green |
| 06-02-03 | 02 | 2 | REQ-10 | regression | `PYTHONPATH=src .venv/bin/pytest tests/test_api_reads.py tests/test_app_boot.py -q` | existing | ✅ green |
| 06-03-01 | 03 | 3 | REQ-10 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_operator_controls.py tests/test_db_migrations.py -q` | created in 06-03 | ✅ green |
| 06-03-02 | 03 | 3 | REQ-10 | regression | `PYTHONPATH=src .venv/bin/pytest tests/test_operator_controls.py tests/test_paper_execution.py -q` | existing | ✅ green |
| 06-03-03 | 03 | 3 | REQ-10 | integration | `PYTHONPATH=src .venv/bin/python scripts/operator_control.py --help` and `PYTHONPATH=src .venv/bin/python scripts/operator_status.py --help` and `PYTHONPATH=src .venv/bin/python -m trading_platform.worker operator-status --help` and `PYTHONPATH=src .venv/bin/pytest tests/test_operator_controls.py tests/test_paper_execution.py tests/test_api_reads.py -q` | created in 06-03 | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure already covers the execution preconditions for this phase:

- `pytest` is present and already used for unit and PostgreSQL-backed integration tests.
- The repository already has Alembic migration verification, FastAPI boot tests, and CLI help-surface verification patterns.
- The Phase 6 plans add new analytics, API, and operator-control test files directly inside the plan tasks, so no external validation tooling is missing before execution begins.

---

## Manual-Only Verifications

All planned Phase 6 behaviors have automated verification. Optional manual curl or browser checks against the FastAPI routes can happen after implementation, but they are not required for plan completion.

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 150s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated 2026-03-14

---

## Validation Audit 2026-03-15

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

All 9 task verification commands ran green (32 tests, 5 CLI help surfaces). No gaps to fill.
