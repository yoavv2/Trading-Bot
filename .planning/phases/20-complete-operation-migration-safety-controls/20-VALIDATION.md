---
phase: 20
slug: complete-operation-migration-safety-controls
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-09-29
reconstructed: true
---

# Phase 20 — Validation Strategy

> Reconstructed after execution (State B) from 24 PLAN/SUMMARY files, 20-VERIFICATION.md and 20-REVIEW-FIX.md. Audit run 2026-09-29.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (backend) · vitest 4 + tsc (console) |
| **Config file** | `pyproject.toml` · `console/vitest.config.*`, `console/tsconfig.json` |
| **Quick run command** | `PYTHONPATH=src .venv/bin/python -m pytest <touched test files> -q` / `cd console && npx vitest run <touched dir>` |
| **Full suite command** | `PYTHONPATH=src .venv/bin/python -m pytest -q` and `cd console && npx vitest run && npx tsc --noEmit` |
| **Estimated runtime** | ~160 s backend · ~7 s console |

---

## Sampling Rate

- **After every task commit:** the task's `<automated>` command (see map)
- **After every plan wave:** full backend + console suites
- **Before `/gsd:verify-work`:** full suites green
- **Max feedback latency:** ~160 s

---

## Requirement Coverage

| Requirement | Status | Primary automated evidence |
|-------------|--------|----------------------------|
| OPS-02 risk-evaluation Job | COVERED | `test_risk_evaluation_job_type.py`, `test_strategy_job_types_e2e.py`, `RiskEvaluationJobForm.test.tsx` |
| OPS-03 paper-session Job, queued-only cancel | COVERED | `test_paper_session_job_type.py`, `test_paper_session_job_e2e.py` (cancel-while-running), `test_job_orchestration.py` |
| OPS-04 reconciliation Job | COVERED | `test_reconciliation_job_type.py`, `test_strategy_job_types_e2e.py` |
| OPS-05 three market-data Job types | COVERED | `test_ingest_bars_job_type.py`, `test_sync_symbol_metadata_job_type.py`, `test_sync_market_sessions_job_type.py`, `test_market_data_job_types_e2e.py` |
| OPS-06 broker order-sync Job | COVERED | `test_broker_order_sync_job_type.py`, `test_strategy_job_types_e2e.py` |
| OPS-07 retry with lineage | COVERED | `test_job_orchestration.py::test_retry_*`, `test_job_mutation_api.py`, `test_phase20_operations_migration.py`, `RetryJobDialog.test.tsx` |
| OPS-08 typed domain conflict | COVERED | `test_job_runner.py`, `test_paper_session_job_e2e.py::test_lock_conflict_lands_as_domain_conflict` |
| CTRL-01 strategy enable/disable | COVERED | `test_control_routes.py`, `test_operator_controls.py`, `controlTriggers.test.tsx`, `StrategyControlSection.test.tsx` |
| CTRL-02 kill-switch trip/reset | COVERED | `test_control_routes.py`, `ControlConfirmDialog.test.tsx`, `KillSwitchBanner.test.tsx`, `test_kill_switch_trip_cli.py` |
| ORCH-01 HTTP-only surfaces (+ break-glass) | COVERED | `test_orchestration_boundaries.py` (DISPATCH/parser pins, closed-world scripts/Makefile) |
| ORCH-02 thin wrappers | COVERED | `test_orchestration_boundaries.py` (script top-level-def pin) |
| ORCH-08 one mutation path | COVERED (gaps filled this audit) | `test_orchestration_boundaries.py`, `test_read_path_purity.py`, **`test_derived_mutation_boundary.py`** |

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 20-01-01 | 01 | 1 | OPS-07,OPS-08,OPS-05 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/python -c "from trading_platform.db.models import Job, StrategyRun, MarketDataIngestionRun, JobFailureReason; from sqlalchemy.orm import configure_mappers; configure_mappers(); assert JobFailureReason.DOMAIN_CONFLICT.value=='domain_conflict'; assert Job.__table__.c.retry_of_job_id.unique; assert not StrategyRun.__table__.c.job_id.unique; assert MarketDataIngestionRun.__table__.c.job_id.index"` | ✅ | ✅ green |
| 20-01-02 | 01 | 1 | OPS-07,OPS-08,OPS-05 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_phase20_operations_migration.py tests/test_phase19_job_operations_migration.py tests/test_db_migrations.py tests/test_job_dependencies.py -q` | ✅ | ✅ green |
| 20-02-01 | 02 | 1 | ORCH-08 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_backtest_reporting.py tests/test_backtest_runner.py tests/test_analytics_service.py tests/test_backtest_job_type.py -q` | ✅ | ✅ green |
| 20-02-02 | 02 | 1 | ORCH-08 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_read_path_purity.py tests/test_operator_controls.py tests/test_paper_execution.py tests/test_api_reads.py -q` | ✅ | ✅ green |
| 20-03-01 | 03 | 1 | ORCH-02,OPS-05 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_job_payload_fields.py -q` | ✅ | ✅ green |
| 20-03-02 | 03 | 1 | ORCH-02,OPS-05 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_symbol_metadata_sync.py tests/test_market_data_ingestion.py -q` | ✅ | ✅ green |
| 20-04-01 | 04 | 2 | OPS-08,OPS-03,OPS-07 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_job_registry.py tests/test_job_import_boundary.py tests/test_job_catalog.py -q` | ✅ | ✅ green |
| 20-04-02 | 04 | 2 | OPS-08,OPS-03,OPS-07 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_job_runner.py tests/test_job_runner_preflight.py tests/test_orchestration_boundaries.py -q` | ✅ | ✅ green |
| 20-05-01 | 05 | 2 | OPS-02,OPS-04,OPS-05,OPS-07 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_job_resources_read.py tests/test_job_api.py tests/test_backtest_job_link.py -q` | ✅ | ✅ green |
| 20-05-02 | 05 | 2 | OPS-02,OPS-04,OPS-05,OPS-07 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_phase20_service_job_links.py tests/test_risk_pipeline.py tests/test_execution_reconciliation.py tests/test_market_data_ingestion.py -q` | ✅ | ✅ green |
| 20-06-01 | 06 | 2 | OPS-07,CTRL-01,CTRL-02,OPS-03 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/lib/api.test.ts && npx tsc --noEmit` | ✅ | ✅ green |
| 20-06-02 | 06 | 2 | OPS-07,CTRL-01,CTRL-02,OPS-03 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/lib/api.test.ts src/lib/consoleBoundaries.test.ts` | ✅ | ✅ green |
| 20-06-03 | 06 | 2 | OPS-07,CTRL-01,CTRL-02,OPS-03 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/jobs/new/jobFormKit.test.tsx src/components/jobs/new/BacktestJobForm.test.tsx src/lib/consoleBoundaries.test.ts && npx tsc --noEmit` | ✅ | ✅ green |
| 20-07-01 | 07 | 3 | OPS-02 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_risk_evaluation_job_type.py -q` | ✅ | ✅ green |
| 20-07-02 | 07 | 3 | OPS-02 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/jobs/new/RiskEvaluationJobForm.test.tsx src/lib/consoleBoundaries.test.ts && npx tsc --noEmit` | ✅ | ✅ green |
| 20-08-01 | 08 | 3 | OPS-04 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_reconciliation_job_type.py -q` | ✅ | ✅ green |
| 20-08-02 | 08 | 3 | OPS-04 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/jobs/new/ReconciliationJobForm.test.tsx src/lib/consoleBoundaries.test.ts && npx tsc --noEmit` | ✅ | ✅ green |
| 20-09-01 | 09 | 3 | OPS-03,OPS-08 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_paper_session_job_type.py tests/test_paper_execution.py tests/test_concurrency_guard.py -q` | ✅ | ✅ green |
| 20-09-02 | 09 | 3 | OPS-03,OPS-08 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_paper_session_job_type.py -q` | ✅ | ✅ green |
| 20-09-03 | 09 | 3 | OPS-03,OPS-08 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/jobs/new/PaperSessionJobForm.test.tsx src/lib/consoleBoundaries.test.ts && npx tsc --noEmit` | ✅ | ✅ green |
| 20-10-01 | 10 | 4 | OPS-03,OPS-07 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_job_orchestration.py tests/test_job_cancellation.py -q` | ✅ | ✅ green |
| 20-10-02 | 10 | 4 | OPS-03,OPS-07 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_job_orchestration.py tests/test_job_mutation_api.py tests/test_orchestration_boundaries.py -q` | ✅ | ✅ green |
| 20-11-01 | 11 | 4 | OPS-06 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_broker_order_sync_job_type.py -q` | ✅ | ✅ green |
| 20-11-02 | 11 | 4 | OPS-06 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/jobs/new/BrokerOrderSyncJobForm.test.tsx src/lib/consoleBoundaries.test.ts && npx tsc --noEmit` | ✅ | ✅ green |
| 20-12-01 | 12 | 4 | ORCH-01,ORCH-08 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_kill_switch_trip_cli.py tests/test_operator_controls.py -q` | ✅ | ✅ green |
| 20-12-02 | 12 | 4 | ORCH-01,ORCH-08 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_orchestration_boundaries.py tests/test_startup_validation.py tests/test_deploy_config.py tests/test_kill_switch_trip_cli.py tests/test_job_runner_preflight.py -q && .venv/bin/python -m trading_platform.worker --help` | ✅ | ✅ green |
| 20-13-01 | 13 | 5 | CTRL-01,CTRL-02,OPS-07,OPS-03 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_control_routes.py tests/test_operator_controls.py tests/test_app_boot.py -q` | ✅ | ✅ green |
| 20-13-02 | 13 | 5 | CTRL-01,CTRL-02,OPS-07,OPS-03 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_job_mutation_api.py tests/test_job_api.py tests/test_job_resources_read.py -q` | ✅ | ✅ green |
| 20-13-03 | 13 | 5 | CTRL-01,CTRL-02,OPS-07,OPS-03 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_orchestration_boundaries.py tests/test_mutation_guard.py -q` | ✅ | ✅ green |
| 20-14-01 | 14 | 5 | OPS-05 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_ingest_bars_job_type.py tests/test_market_data_ingestion.py tests/test_phase20_service_job_links.py -q` | ✅ | ✅ green |
| 20-14-02 | 14 | 5 | OPS-05 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/jobs/new/IngestBarsJobForm.test.tsx src/lib/consoleBoundaries.test.ts && npx tsc --noEmit` | ✅ | ✅ green |
| 20-15-01 | 15 | 5 | OPS-05,ORCH-02 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_sync_symbol_metadata_job_type.py -q` | ✅ | ✅ green |
| 20-15-02 | 15 | 5 | OPS-05,ORCH-02 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_sync_market_sessions_job_type.py -q` | ✅ | ✅ green |
| 20-15-03 | 15 | 5 | OPS-05,ORCH-02 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/jobs/new/SyncSymbolMetadataJobForm.test.tsx src/components/jobs/new/SyncMarketSessionsJobForm.test.tsx src/lib/consoleBoundaries.test.ts && npx tsc --noEmit` | ✅ | ✅ green |
| 20-16-01 | 16 | 6 | OPS-02,OPS-03,OPS-04,OPS-05,OPS-06 | — | see 20-SECURITY.md | e2e | `.venv/bin/pytest tests/test_orchestration_boundaries.py tests/test_job_catalog.py tests/test_job_registry.py tests/test_job_operations_e2e.py -q` | ✅ | ✅ green |
| 20-16-02 | 16 | 6 | OPS-02,OPS-03,OPS-04,OPS-05,OPS-06 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/lib/jobTypeForms.test.ts src/lib/consoleBoundaries.test.ts src/components/jobs/new && npx tsc --noEmit` | ✅ | ✅ green |
| 20-17-01 | 17 | 6 | OPS-07,OPS-03 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/jobs/RetryJobDialog.test.tsx src/lib/consoleBoundaries.test.ts` | ✅ | ✅ green |
| 20-17-02 | 17 | 6 | OPS-07,OPS-03 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/jobs src/lib/consoleBoundaries.test.ts && npx tsc --noEmit` | ✅ | ✅ green |
| 20-18-01 | 18 | 6 | CTRL-01,CTRL-02 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/controls/ControlConfirmDialog.test.tsx && npx tsc --noEmit` | ✅ | ✅ green |
| 20-18-02 | 18 | 6 | CTRL-01,CTRL-02 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/controls && npx tsc --noEmit` | ✅ | ✅ green |
| 20-19-01 | 19 | 7 | OPS-02,OPS-04,OPS-06,OPS-07 | — | see 20-SECURITY.md | e2e | `.venv/bin/pytest tests/test_strategy_job_types_e2e.py -q -k "not retry"` | ✅ | ✅ green |
| 20-19-02 | 19 | 7 | OPS-02,OPS-04,OPS-06,OPS-07 | — | see 20-SECURITY.md | e2e | `.venv/bin/pytest tests/test_strategy_job_types_e2e.py -q` | ✅ | ✅ green |
| 20-20-01 | 20 | 7 | OPS-03,OPS-08,OPS-07 | — | see 20-SECURITY.md | e2e | `.venv/bin/pytest tests/test_paper_session_job_e2e.py -q` | ✅ | ✅ green |
| 20-20-02 | 20 | 7 | OPS-03,OPS-08,OPS-07 | — | see 20-SECURITY.md | e2e | `.venv/bin/pytest tests/test_paper_session_job_e2e.py -q` | ✅ | ✅ green |
| 20-21-01 | 21 | 7 | OPS-05 | — | see 20-SECURITY.md | e2e | `.venv/bin/pytest tests/test_market_data_job_types_e2e.py -q` | ✅ | ✅ green |
| 20-21-02 | 21 | 7 | OPS-05 | — | see 20-SECURITY.md | e2e | `.venv/bin/pytest tests/test_market_data_job_types_e2e.py -q` | ✅ | ✅ green |
| 20-22-01 | 22 | 8 | CTRL-01,CTRL-02 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/status src/components/controls && npx tsc --noEmit` | ✅ | ✅ green |
| 20-22-02 | 22 | 8 | CTRL-01,CTRL-02 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx tsc --noEmit && npx vitest run && npm run build` | ✅ | ✅ green (build step not re-run in audit — `next dev` live on :3000) |
| 20-23-01 | 23 | 8 | CTRL-01,CTRL-02,OPS-02,OPS-03,OPS-04,OPS-06 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run src/components/KillSwitchBanner.test.tsx && npx tsc --noEmit` | ✅ | ✅ green |
| 20-23-02 | 23 | 8 | CTRL-01,CTRL-02,OPS-02,OPS-03,OPS-04,OPS-06 | — | see 20-SECURITY.md | component (vitest) | `cd console && npx vitest run && npx tsc --noEmit` | ✅ | ✅ green |
| 20-24-01 | 24 | 8 | ORCH-01,ORCH-02,ORCH-08 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_startup_validation.py tests/test_operator_controls.py tests/test_backtest_runner.py tests/test_risk_pipeline.py -q && make -n test >/dev/null` | ✅ | ✅ green |
| 20-24-02 | 24 | 8 | ORCH-01,ORCH-02,ORCH-08 | — | see 20-SECURITY.md | unit/integration | `.venv/bin/pytest tests/test_orchestration_boundaries.py tests/test_read_path_purity.py -q && .venv/bin/pytest -q` | ✅ | ✅ green |
| 20-AUDIT-G1 | audit | — | ORCH-08 (SIG-01) | — | `generate_signals.py` exemption proven write-free on the real evaluation path | integration (write spy) | `PYTHONPATH=src .venv/bin/python -m pytest tests/test_read_path_purity.py -q` | ✅ | ✅ green |
| 20-AUDIT-G2 | audit | — | ORCH-08 (BND-01) | — | scripts/worker call no service-layer writer, incl. unlisted low-level upserts | static AST | `PYTHONPATH=src .venv/bin/python -m pytest tests/test_derived_mutation_boundary.py -q` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

Notes:
- `tests/test_dry_run.py` (referenced by 20-24 `files_modified`) was deleted by design when `scripts/dry_run.py` and `run_dry_bootstrap` were retired (D-29).
- 20-24-01 `make -n test` and 20-12-02 `python -m trading_platform.worker --help` re-run in audit: OK (worker surface = report-backtest, report-strategy-analytics, operator-status, run-jobs, kill-switch-trip).
- 20-22-02 `npm run build` not re-run: a `next dev` server was live on :3000 and a concurrent build would clobber `.next`. vitest + tsc portions green.
- Audit suite counts (1015 pytest / 328 vitest) include the user's uncommitted `tests/test_dev_workflow.py` and WIP edit to `tests/test_orchestration_boundaries.py` (Makefile dev targets); both were green.

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Kill-switch trip/reset from `/controls` and `KillSwitchBanner` with no worker running | CTRL-02 | Browser → proxy → API → DB on a live stack | `20-VERIFICATION.md` human item 1 / `20-HUMAN-UAT.md` |
| Strategy enable/disable from `/strategy` and `/controls` | CTRL-01 | Visual/interaction on live stack | `20-VERIFICATION.md` human item 2 / `20-HUMAN-UAT.md` |
| Submit all 7 new Job forms, Retry a failed Job, queued-only cancel gating on RUNNING paper session | OPS-02..07 | End-to-end browser flow, lineage rendering | `20-VERIFICATION.md` human item 3 / `20-HUMAN-UAT.md` |
| Break-glass `kill-switch-trip` CLI with API stopped | ORCH-01, CTRL-02 | Live DB + process-level check | `20-VERIFICATION.md` human item 4 / `20-HUMAN-UAT.md` |
| Review-fix concurrency/migration/async behaviors (WR-A-01/04/05, CR-B-01, WR-B-02/05/06, WR-C-01..04/06/07) | OPS-05, OPS-07, OPS-08, CTRL-01/02 | Unit/E2E cover them only in simulated form | `20-HUMAN-UAT.md` test 5 |

Out of scope for validation (not test gaps): WR-A-02 ingest-bars all-symbols-fail semantics (open product decision), CR-B-01 stale `running` ingestion-run residual, DOC-01 REQUIREMENTS.md traceability drift.

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 160s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-09-29

---

## Validation Audit 2026-09-29

| Metric | Count |
|--------|-------|
| Gaps found | 2 |
| Resolved | 2 |
| Escalated | 0 |

- G1 (SIG-01, MISSING → green): `test_generate_signals_script_writes_no_rows` drives `generate_signals.main()` against seeded bars (`--as-of 2024-01-08`), asserts real signals emitted and zero writes. Mutation-checked: an injected `ensure_strategy_control_state` call makes it fail.
- G2 (BND-01, PARTIAL → green): `tests/test_derived_mutation_boundary.py` derives 35 session writers from `services/` by AST, unions with `_MUTATING_ENTRY_POINTS`, scans scripts + worker. Non-vacuity: `upsert_daily_bars`/`upsert_symbol` discovered and absent from the hand list; synthetic bypass caught by derived scan, missed by hand-listed scan. Known limits (name-based, non-transitive, no ORM attribute-mutation detection) documented in module docstring.
