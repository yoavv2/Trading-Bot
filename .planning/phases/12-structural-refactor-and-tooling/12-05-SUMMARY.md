---
phase: 12-structural-refactor-and-tooling
plan: 05
subsystem: reconciliation
tags: [python, refactor, structural-split, reconciliation]

# Dependency graph
requires:
  - phase: 12-01
    provides: services/config/tolerances.py (MONEY_TOLERANCE, QUANTITY_TOLERANCE) consumed by the matcher
  - phase: 12-04
    provides: services/execution package (final import paths) — reconciliation report imports execution-side symbols
provides:
  - services/reconciliation/snapshot.py (typed Local*Snapshot dataclasses + ReconciliationIdentity)
  - services/reconciliation/findings.py (closed ReconciliationFinding enum + Finding value type)
  - services/reconciliation/matcher.py (pure indexed match_snapshots)
  - services/reconciliation/report.py (read-only reconcile_paper_execution orchestrator + ReconciliationReport)
  - old standalone reconciliation.py / reconciliation_matcher.py / reconciliation_types.py fully deleted; consumers repoint through services.reconciliation
affects: [structural-refactor-and-tooling, worker split (12-06)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Package split along the Phase-9 boundary already latent in the code: typed snapshots, closed-enum findings, pure matcher, read-only orchestrator/report"
    - "Intentional same-name-different-role: the closed-enum finding CATEGORY (findings.ReconciliationFinding) and the report-level ReconciliationFinding dataclass (report) share a name across roles; the package __init__ re-exports the enum, the dataclass is imported from report directly where needed"
    - "Temporary re-export shim during Task 1 (reconciliation_matcher/reconciliation_types reduced to shims), deleted-last in Task 2 after every real consumer + test was repointed and the targeted suites were green"

key-files:
  created:
    - src/trading_platform/services/reconciliation/__init__.py
    - src/trading_platform/services/reconciliation/snapshot.py
    - src/trading_platform/services/reconciliation/findings.py
    - src/trading_platform/services/reconciliation/matcher.py
    - src/trading_platform/services/reconciliation/report.py
  modified:
    - src/trading_platform/worker/__main__.py
    - tests/test_execution_reconciliation.py
    - tests/test_log_enforcement.py
    - tests/test_reconciliation_matcher.py
    - tests/test_reconciliation_types.py
  deleted:
    - src/trading_platform/services/reconciliation.py
    - src/trading_platform/services/reconciliation_matcher.py
    - src/trading_platform/services/reconciliation_types.py

requirements:
  - id: STRUCT-05
    status: complete

commits:
  - 6d62922 refactor(12-05): build reconciliation package (snapshot, findings, matcher, report)
  - 36e2f1f refactor(12-05): repoint reconciliation consumers, delete temporary shims
---

# Plan 12-05 Summary — Reconciliation Package Reorg (STRUCT-05)

## What Landed

Reorganized the reconciliation subsystem into the four role-named modules declared by
STRUCT-05, under `services/reconciliation/`:

- **`snapshot.py`** — the typed `Local*Snapshot` dataclasses and `ReconciliationIdentity`.
- **`findings.py`** — the closed `ReconciliationFinding` enum + the `Finding` value type.
- **`matcher.py`** — the pure, indexed `match_snapshots` / `match_snapshots_with_comparisons`.
- **`report.py`** — the read-only `reconcile_paper_execution` orchestrator, the
  separately-invoked `apply_reconciliation_corrections` corrective entrypoint, and the
  materialized `ReconciliationReport`.

The three pre-package modules (`reconciliation.py`, `reconciliation_matcher.py`,
`reconciliation_types.py`) are deleted. Every consumer (`worker/__main__.py`, the test
files) resolves the public surface through `trading_platform.services.reconciliation`.

## How

- **Task 1** (`6d62922`): built the package — moved code verbatim, renamed the orchestrator
  module into `report.py`, and split matcher/types into `snapshot`/`findings`/`matcher`,
  leaving the old `reconciliation_matcher`/`reconciliation_types` modules as temporary
  re-export shims so the tree stayed buildable.
- **Task 2** (`36e2f1f`): repointed every real consumer and test import (import lines and
  `mock.patch` target strings only — assertion bodies frozen), then deleted the temporary
  shims once the targeted suites were green.

## Deviations / Notes

- **Same-name-different-role** (`ReconciliationFinding`): the closed-enum finding CATEGORY
  and the report-level finding dataclass intentionally share a name across different roles.
  The package `__init__` re-exports the enum; the dataclass is imported from `report`
  directly where it is used. Documented so the plan-checker does not read it as a collision.
- The matcher now imports `MONEY_TOLERANCE` / `QUANTITY_TOLERANCE` from the
  `services/config/tolerances.py` module introduced in 12-01.

## Verification

- Full suite: **306 passed, 0 failed** — matches the immutable 12-BASELINE.md invariant
  with zero assertion changes (documented `pg_terminate_backend` teardown-ERROR noise only).
- `PYTHONPATH=src .venv/bin/python -m trading_platform.worker --help` succeeds — CLI wiring intact.
- Grep for the old module import paths is clean.

## Next Phase Readiness

- STRUCT-05 is Complete. Reconciliation now presents a single package surface, so 12-06's
  worker split can import the FINAL reconciliation paths.
- No blockers for 12-06 (worker split) or 12-07 (pre-commit gates).

---
*Phase: 12-structural-refactor-and-tooling*
*Completed: 2026-07-15*

## Self-Check: PASSED
- FOUND: .planning/phases/12-structural-refactor-and-tooling/12-05-SUMMARY.md
- FOUND: services/reconciliation/{snapshot,findings,matcher,report}.py
- REMOVED: reconciliation.py / reconciliation_matcher.py / reconciliation_types.py
- FOUND: commit 6d62922 (Task 1), commit 36e2f1f (Task 2)
- SUITE: 306 passed / 0 failed (baseline held)
