---
phase: 12-structural-refactor-and-tooling
plan: 02
subsystem: config
tags: [config-validation, pydantic, structural-refactor, no-behavior-change]

# Dependency graph
requires:
  - phase: 12-structural-refactor-and-tooling
    plan: 01
    provides: "12-BASELINE.md zero-behavior-change invariant (306 passed / 0 failed) and the services/config/ package this plan adds validation.py + secrets.py into"
provides:
  - "services/config/validation.py — ExecutionMode, ConfigValidationError, validate_config (moved verbatim from core/config_validation.py)"
  - "services/config/secrets.py — semantic_failures (CFG-01/02/03 per-mode/endpoint checks), the newly-extracted, zero-I/O semantic layer"
  - "Confirmation record that trading_platform.core.settings is the sole canonical settings surface (no competing BaseSettings/Settings module, no duplicate load_settings-style loader)"
affects: [12-03, 12-04, 12-05, 12-06, 12-07]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Config validation lives under services/config/ (shape/orchestration in validation.py, semantic per-mode checks in secrets.py), not core/"
    - "Deferred (call-time) intra-package import used to break a circular import introduced by a two-module split where each module needs a symbol from the other"

key-files:
  created:
    - src/trading_platform/services/config/validation.py
    - src/trading_platform/services/config/secrets.py
    - .planning/phases/12-structural-refactor-and-tooling/deferred-items.md
  modified:
    - src/trading_platform/core/startup.py
    - src/trading_platform/api/app.py
    - src/trading_platform/worker/__main__.py
    - src/trading_platform/services/bootstrap.py
    - tests/test_config_validation.py
    - tests/test_startup_validation.py
    - tests/test_log_enforcement.py

key-decisions:
  - "validate_config's import of services.config.secrets is deferred to call-time (inside the function body) rather than a top-of-file import, to break a module-load-time circular import: secrets.py imports ExecutionMode from validation.py at its own top level, and validation.py needs secrets.semantic_failures — an eager two-way top-level import would fail on first load."
  - "tests/test_log_enforcement.py's IN_SCOPE_MODULES LOG-01 enforcement list was repointed 1:1 (core/config_validation.py -> services/config/validation.py) with its length-guard assertion left at 12, deliberately NOT adding services/config/secrets.py (would bump the count to 13, an assertion-body edit forbidden by the zero-behavior-change contract) — secrets.py has zero logging calls today, so the coverage gap carries no live risk; logged as a follow-up in deferred-items.md."
  - "STRUCT-08 confirmed by direct grep evidence (no code change): core/settings.py is the only module defining BaseSettings/Settings-family classes, and load_settings/build_settings_payload/get_strategy_config/clear_settings_cache are the sole, non-duplicated settings-loading API."

requirements-completed: [STRUCT-06, STRUCT-08]

# Metrics
duration: ~20min
completed: 2026-07-14
---

# Phase 12 Plan 02: Config Validation Reorganization + Settings Surface Confirmation Summary

**Split `core/config_validation.py` into `services/config/validation.py` (shape/orchestration) and `services/config/secrets.py` (CFG-01/02/03 semantic checks), deleted the old module, repointed all six importers, and confirmed `core/settings.py` is the sole canonical settings surface — zero behavior change, full suite still 306 passed / 0 failed.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-07-14T16:07:00Z
- **Completed:** 2026-07-14T16:27:14Z
- **Tasks:** 2/2
- **Files modified:** 10 (2 created, 1 deleted+renamed, 7 modified)

## Accomplishments
- STRUCT-06 complete: config validation now lives under `services/config/{validation,secrets}.py` per its declared service boundary; `core/config_validation.py` is deleted with no backward-compat shim.
- All six importers repointed to the new path: `core/startup.py`, `api/app.py`, `services/bootstrap.py`, `worker/__main__.py`, `tests/test_config_validation.py`, `tests/test_startup_validation.py`.
- STRUCT-08 confirmed: `core/settings.py` is the one and only module defining `BaseSettings`/`Settings`-family classes across `src/`; no competing settings module or duplicate `load_settings()`-style loader exists. No code change was needed or fabricated.
- Full suite verified at exactly the Phase-12 baseline: `306 passed, 0 failed` (one `pg_terminate_backend` teardown ERROR present, matching the documented environmental flake in `12-BASELINE.md`).

## Task Commits

Each task was committed atomically:

1. **Task 1: STRUCT-06 — create services/config/validation.py + secrets.py, delete core/config_validation.py, repoint importers** - `8c55ee5` (refactor)
2. **Task 2: STRUCT-08 — confirm single canonical settings surface** - no commit (confirmation only, no code change per plan's explicit instruction not to fabricate a deletion with no target)

**Plan metadata:** committed with SUMMARY/STATE/ROADMAP/REQUIREMENTS update.

## Files Created/Modified
- `src/trading_platform/services/config/validation.py` - `ExecutionMode`, `ConfigValidationError`, `_dotted_path`, `_translate_pydantic_errors`, `validate_config` — moved verbatim from `core/config_validation.py`; `validate_config`'s semantic-check call to `services.config.secrets.semantic_failures` is a deferred (call-time) import to avoid a circular import.
- `src/trading_platform/services/config/secrets.py` - `semantic_failures` (renamed from private `_semantic_failures`) and `_PAPER_BASE_URL_MARKER` — the CFG-01/02/03 semantic layer, moved byte-for-byte in logic.
- `src/trading_platform/core/config_validation.py` - deleted (git recorded as a rename to `services/config/validation.py`, 66% similarity).
- `src/trading_platform/core/startup.py` - import repointed to `services.config.validation`.
- `src/trading_platform/api/app.py` - import repointed to `services.config.validation`.
- `src/trading_platform/services/bootstrap.py` - import repointed to `services.config.validation`.
- `src/trading_platform/worker/__main__.py` - import repointed to `services.config.validation`.
- `tests/test_config_validation.py` - import repointed; assertions unchanged.
- `tests/test_startup_validation.py` - import repointed; docstring updated to reference the new path; assertions unchanged.
- `tests/test_log_enforcement.py` - `IN_SCOPE_MODULES` list entry repointed 1:1 to `services/config/validation.py`; length-guard assertion left at 12 (unchanged); `secrets.py` coverage gap documented in `deferred-items.md`.
- `.planning/phases/12-structural-refactor-and-tooling/deferred-items.md` - new file recording the `secrets.py` LOG-01 coverage follow-up.

## Decisions Made
- Deferred `secrets.py` import to call-time inside `validate_config` (see key-decisions above) — the cleanest fix for the two-way symbol dependency created by the STRUCT-06 split, with zero behavior change to the two-pass validation contract.
- Left `tests/test_log_enforcement.py`'s length-guard assertion at 12 rather than bumping to 13, prioritizing the zero-behavior-change contract's "assertion bodies are frozen" rule over completeness of LOG-01 coverage for the newly-extracted `secrets.py` (which has no logging calls today, so there is no live enforcement gap).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed circular import between validation.py and secrets.py**
- **Found during:** Task 1 (writing services/config/secrets.py)
- **Issue:** `secrets.py` needs `ExecutionMode` from `validation.py` (for the `mode` parameter's runtime `is` comparisons, not just type annotations), while `validation.py` needs `semantic_failures` from `secrets.py`. A top-level two-way import would raise `ImportError` on first module load.
- **Fix:** Kept `secrets.py`'s top-level `from trading_platform.services.config.validation import ExecutionMode` (needed at runtime for the `mode is ExecutionMode.X` checks), and moved `validation.py`'s `from trading_platform.services.config.secrets import semantic_failures` inside the `validate_config` function body (deferred/call-time import). By the time `validate_config` is ever called, `validation.py` has fully finished loading, so `secrets.py`'s top-level import succeeds without ordering issues.
- **Files modified:** `src/trading_platform/services/config/validation.py`, `src/trading_platform/services/config/secrets.py`
- **Verification:** `PYTHONPATH=src .venv/bin/pytest tests/test_config_validation.py tests/test_startup_validation.py tests/test_app_boot.py tests/test_dry_run.py tests/test_log_enforcement.py -q` — 41 passed.
- **Committed in:** `8c55ee5` (Task 1 commit)

**2. [Rule 3 - Blocking] Repointed test_log_enforcement.py's stale path reference after deleting core/config_validation.py**
- **Found during:** Task 1 (running the targeted verification suite)
- **Issue:** `tests/test_log_enforcement.py`'s `IN_SCOPE_MODULES` list (not in this plan's declared `files_modified`) hardcoded the path `core/config_validation.py` for LOG-01 AST-scan enforcement. Deleting the old module without updating this reference would have broken the test at `path.read_text()` (file not found).
- **Fix:** Repointed the single list entry to `services/config/validation.py`, a 1:1 path swap that keeps the list length (and its `== 12` length-guard assertion) unchanged. Did NOT add `services/config/secrets.py` as a second entry, since that would bump the assertion to 13 — an assertion-body edit forbidden by the zero-behavior-change contract. Logged the resulting `secrets.py` coverage gap in `deferred-items.md` as a follow-up (no live risk: `secrets.py` has zero logging calls).
- **Files modified:** `tests/test_log_enforcement.py`, `.planning/phases/12-structural-refactor-and-tooling/deferred-items.md`
- **Verification:** `PYTHONPATH=src .venv/bin/pytest tests/test_log_enforcement.py -q` — 4 passed, including the length-guard assertion at its original value.
- **Committed in:** `8c55ee5` (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 3 - blocking issues surfaced by the module move, neither changed any assertion body or production behavior)
**Impact on plan:** Both fixes were mechanical consequences of moving/splitting one module into two and were required to keep the suite green. No scope creep — no new capability, no assertion changed.

## Issues Encountered
None beyond the two deviations documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `services/config/` now holds tolerances.py (12-01), validation.py, and secrets.py (12-02) — the config service boundary is fully consolidated for anything 12-03 onward touches.
- STRUCT-08 is closed; no further settings-surface work is needed in later Phase-12 plans.
- Full suite confirmed at the exact Phase-12 baseline (306 passed / 0 failed) with zero assertion changes — 12-03 can proceed on a clean, verified base.

---
*Phase: 12-structural-refactor-and-tooling*
*Completed: 2026-07-14*

## Self-Check: PASSED

- FOUND: src/trading_platform/services/config/validation.py
- FOUND: src/trading_platform/services/config/secrets.py
- FOUND: .planning/phases/12-structural-refactor-and-tooling/deferred-items.md
- FOUND: .planning/phases/12-structural-refactor-and-tooling/12-02-SUMMARY.md
- CONFIRMED DELETED: src/trading_platform/core/config_validation.py
- FOUND commit: 8c55ee5
- Full suite re-verified: 306 passed, 0 failed (1 documented pg_terminate_backend teardown ERROR, matching 12-BASELINE.md)
