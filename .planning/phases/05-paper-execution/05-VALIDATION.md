---
phase: 05
slug: paper-execution
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-14
---

# Phase 05 - Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `PYTHONPATH=src .venv/bin/pytest tests/test_alpaca_execution.py tests/test_paper_execution.py tests/test_execution_reconciliation.py tests/test_db_migrations.py -q` |
| **Full suite command** | `PYTHONPATH=src .venv/bin/pytest tests -q` |
| **Estimated runtime** | ~120 seconds |

---

## Sampling Rate

- **After every task commit:** Run `PYTHONPATH=src .venv/bin/pytest <task-specific test files> -q`
- **After every plan wave:** Run `PYTHONPATH=src .venv/bin/pytest tests -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 120 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | REQ-08 | unit | `PYTHONPATH=src .venv/bin/pytest tests/test_alpaca_execution.py -q` | created in 05-01 | ✅ green |
| 05-01-02 | 01 | 1 | REQ-06 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_db_migrations.py -q` | existing | ✅ green |
| 05-01-03 | 01 | 1 | REQ-08 | integration | `PYTHONPATH=src .venv/bin/python scripts/submit_paper_orders.py --help` and `PYTHONPATH=src .venv/bin/python -m trading_platform.worker submit-paper-orders --help` and `PYTHONPATH=src .venv/bin/pytest tests/test_alpaca_execution.py tests/test_db_migrations.py -q` | created in 05-01 | ✅ green |
| 05-02-01 | 02 | 2 | REQ-08 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_paper_execution.py -q` | created in 05-02 | ✅ green |
| 05-02-02 | 02 | 2 | REQ-06 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_paper_execution.py tests/test_db_migrations.py -q` | existing | ✅ green |
| 05-02-03 | 02 | 2 | REQ-08 | integration | `PYTHONPATH=src .venv/bin/python scripts/run_paper_session.py --help` and `PYTHONPATH=src .venv/bin/python -m trading_platform.worker run-paper-session --help` and `PYTHONPATH=src .venv/bin/pytest tests/test_paper_execution.py tests/test_alpaca_execution.py tests/test_db_migrations.py -q` | created in 05-02 | ✅ green |
| 05-03-01 | 03 | 3 | REQ-08 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_execution_reconciliation.py -q` | created in 05-03 | ✅ green |
| 05-03-02 | 03 | 3 | REQ-08 | regression | `PYTHONPATH=src .venv/bin/pytest tests/test_execution_reconciliation.py tests/test_paper_execution.py -q` | created in 05-03 | ✅ green |
| 05-03-03 | 03 | 3 | REQ-08 | integration | `PYTHONPATH=src .venv/bin/python scripts/reconcile_paper_execution.py --help` and `PYTHONPATH=src .venv/bin/python -m trading_platform.worker reconcile-paper-execution --help` and `PYTHONPATH=src .venv/bin/pytest tests/test_execution_reconciliation.py tests/test_paper_execution.py tests/test_alpaca_execution.py tests/test_db_migrations.py -q` | created in 05-03 | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. `pytest`, the temporary PostgreSQL integration-test pattern, Alembic migration checks, and worker CLI conventions are already in place; the new broker-facing test files and commands are created inside the Phase 5 plan tasks that immediately verify them.

---

## Manual-Only Verifications

All planned Phase 5 behaviors have automated verification. A live Alpaca paper-account smoke check is optional after the user provides credentials, but it is not required for plan completion.

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 120s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated 2026-03-14

## Validation Audit 2026-03-14

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |

**Full suite:** 22 passed in 4.58s
**CLI entrypoints:** 6/6 confirmed (3 scripts + 3 worker commands)
