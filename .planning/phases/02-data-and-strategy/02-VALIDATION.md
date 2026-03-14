---
phase: 02
slug: data-and-strategy
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-13
---

# Phase 02 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `PYTHONPATH=src .venv/bin/pytest tests/test_market_data_ingestion.py tests/test_market_data_access.py tests/test_trend_following_strategy.py -q` |
| **Full suite command** | `PYTHONPATH=src .venv/bin/pytest tests -q` |
| **Estimated runtime** | ~45 seconds |

---

## Sampling Rate

- **After every task commit:** Run `PYTHONPATH=src .venv/bin/pytest <task-specific test files> -q`
- **After every plan wave:** Run `PYTHONPATH=src .venv/bin/pytest tests -q`
- **Before `$gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | REQ-04 | unit | `PYTHONPATH=src .venv/bin/pytest tests/test_app_boot.py -q` | ✅ | ⬜ pending |
| 02-01-02 | 01 | 1 | REQ-04 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_market_data_ingestion.py tests/test_db_migrations.py -q` | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | REQ-04 | integration | `PYTHONPATH=src .venv/bin/python scripts/ingest_polygon_bars.py --help` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 2 | REQ-04 | integration | `PYTHONPATH=src .venv/bin/pytest tests/test_market_data_access.py tests/test_db_migrations.py -q` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 2 | REQ-04 | unit | `PYTHONPATH=src .venv/bin/pytest tests/test_market_data_access.py -q` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 2 | REQ-04 | integration | `PYTHONPATH=src .venv/bin/python scripts/sync_symbol_metadata.py --help` | ❌ W0 | ⬜ pending |
| 02-03-01 | 03 | 3 | REQ-03 | unit | `PYTHONPATH=src .venv/bin/pytest tests/test_trend_following_strategy.py -q` | ❌ W0 | ⬜ pending |
| 02-03-02 | 03 | 3 | REQ-03 | unit | `PYTHONPATH=src .venv/bin/pytest tests/test_trend_following_strategy.py tests/test_strategy_registry.py -q` | ❌ W0 | ⬜ pending |
| 02-03-03 | 03 | 3 | REQ-03 | integration | `PYTHONPATH=src .venv/bin/python scripts/generate_signals.py --help` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_market_data_ingestion.py` — mocked provider ingestion and upsert coverage for REQ-04
- [ ] `tests/test_market_data_access.py` — session-aware read and metadata-sync coverage for REQ-04
- [ ] `tests/test_trend_following_strategy.py` — deterministic SMA entry/exit coverage for REQ-03
- [ ] `tests/fixtures/polygon_daily_bars.json` — stable provider fixtures for ingestion tests

---

## Manual-Only Verifications

All phase behaviors have automated verification.

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending

---
