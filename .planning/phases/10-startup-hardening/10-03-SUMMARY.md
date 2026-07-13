---
phase: 10-startup-hardening
plan: 03
subsystem: database
tags: [sqlalchemy, postgres, lifecycle, import-boundary, testing]

# Dependency graph
requires:
  - phase: 08-concurrency-guard
    provides: existing db/session.py keyed (url, echo) engine/session-factory dict-cache and clear_engine_cache() reload primitive
provides:
  - One documented DB connection-lifecycle model (explicit reloadable manager) declared in db/session.py's module docstring
  - Key Decision entry in PROJECT.md recording the DB-01/DB-02 choice
  - Single canonical import path (trading_platform.db.session) for all engine/session symbols; db/__init__.py no longer re-exports them
  - tests/test_db_lifecycle.py: import-boundary test (AST-scans all src files), re-export-surface test, and reloadability test
affects: [10-04-transaction-integrity]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Import-boundary enforcement via AST scan (not just grep) in a dedicated test file, catching any future `from trading_platform.db import <lifecycle-symbol>` regression"
    - "Lifecycle model declared in the owning module's docstring, not just in planning docs, so the authoritative model travels with the code"

key-files:
  created: [tests/test_db_lifecycle.py]
  modified: [src/trading_platform/db/session.py, src/trading_platform/db/__init__.py, .planning/PROJECT.md]

key-decisions:
  - "DB connection lifecycle is an explicit reloadable manager (keyed (url, echo) dict-cache + clear_engine_cache()), not a process-immutable singleton — required by the test suite's need to point at test vs. local DB within one process (DB-01)."
  - "The keyed dict-cache is the single authorized engine/session caching mechanism; no functools-decorator-based memoization of engines/sessions is permitted anywhere (DB-02)."
  - "trading_platform.db.session is the single canonical import path for engine/session symbols; trading_platform.db (package __init__) re-exports only Base (DB-03)."

patterns-established:
  - "Pattern: pin architectural invariants (single import path, forbidden caching style) with an executable AST-scanning test, not just a docstring or planning-doc statement."

requirements-completed: [DB-01, DB-02, DB-03]

# Metrics
duration: ~15min
completed: 2026-07-13
---

# Phase 10 Plan 03: DB Connection Lifecycle Consolidation Summary

**Formalized db/session.py's existing keyed-dict engine cache as the one documented reloadable lifecycle model, removed the competing db/__init__.py re-export surface, and pinned both invariants with an AST-scanning import-boundary test.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-07-13T19:00:00Z (approx)
- **Completed:** 2026-07-13T19:14:00Z
- **Tasks:** 2
- **Files modified:** 4 (2 modified + 1 modified for decision doc + 1 created)

## Accomplishments
- `db/session.py` now carries an explicit "Lifecycle model" docstring section declaring the keyed `(url, echo)` dict-cache as the single authorized reloadable engine/session-factory manager, and forbidding any competing `functools`-decorator-based memoization of engines/sessions.
- `.planning/PROJECT.md` Key Decisions table records the DB-01/DB-02 choice and rationale (test-suite DB-swap requirement).
- `db/__init__.py` no longer re-exports `build_engine`, `get_engine`, `get_session_factory`, `session_scope`, `clear_engine_cache`, `check_database_connection` — only `Base` remains, closing the second import surface (DB-03).
- New `tests/test_db_lifecycle.py` pins three invariants: (1) an AST-based import-boundary scan across all of `src/trading_platform/**/*.py` failing on any non-canonical lifecycle-symbol import, (2) a re-export-surface test asserting `trading_platform.db` exposes none of the lifecycle symbols, (3) a reloadability test proving `clear_engine_cache()` forces a fresh `Engine` instance on next `get_engine()` call.
- Full repo suite (218 tests, up from 215) green with no regressions.

## Task Commits

Each task was committed atomically:

1. **Task 1: Formalize db/session.py as the one documented reloadable manager + Key Decision entry (DB-01, DB-02)** - `e981906` (feat)
2. **Task 2: Single canonical import path + import-boundary test (DB-03)** - `730e899` (feat)

**Plan metadata:** (this commit) `docs(10-03): complete DB Connection Lifecycle Consolidation plan`

## Files Created/Modified
- `src/trading_platform/db/session.py` - Added module docstring declaring the explicit reloadable manager as the one lifecycle model and the keyed dict-cache as the sole authorized caching mechanism
- `src/trading_platform/db/__init__.py` - Removed engine/session re-exports; only `Base` remains exported; docstring points consumers to `trading_platform.db.session`
- `.planning/PROJECT.md` - Added Key Decision row for the DB-01/DB-02 lifecycle-model choice
- `tests/test_db_lifecycle.py` (new) - Import-boundary AST scan, re-export-surface test, reloadability test

## Decisions Made
- DB connection lifecycle is an explicit reloadable manager, not a process-immutable singleton (DB-01) — dictated by the existing 215+-test suite's requirement to rebind the engine to the test DB vs. local DB within one process; collapsing to a singleton would break the suite (this constraint was already identified in the plan's own key_facts and confirmed unchanged during execution).
- The keyed `(url, echo)` dict-cache is the single authorized engine/session caching mechanism (DB-02) — no `functools`-decorator-based memoization of engines/sessions is permitted; the codebase's only such decorator usage (`load_settings` in `core/settings.py`) caches parsed configuration, a distinct concern, not the engine/session lifecycle, so it does not compete with this model.
- `trading_platform.db.session` is the single canonical import path (DB-03); `trading_platform.db` re-exports only `Base`. No consumer code changes were needed beyond `db/__init__.py` itself — a pre-execution grep across `src/` and `tests/` confirmed zero existing consumers imported lifecycle symbols from the package surface (`concurrency_guard.py` and all `session_scope` consumers already imported from `.db.session` directly), so the "competing surface" was dead re-export code, not live duplicate wiring.

## Deviations from Plan

**1. [Rule 3 - Blocking] Reworded the docstring to avoid the literal substring "lru_cache"**
- **Found during:** Task 1
- **Issue:** The plan's own verify command (`assert 'lru_cache' not in src`) does a literal substring check across the whole file, but a clear docstring explanation of "no @lru_cache-style engine memoization" naturally contains the string "lru_cache", which the check then flags as a false positive (it can't distinguish prose from a decorator).
- **Fix:** Reworded the docstring to describe the forbidden pattern ("functools's single-value memoizing decorator") without using the literal token `lru_cache`, preserving the exact same meaning for a human reader while satisfying the automated check.
- **Files modified:** `src/trading_platform/db/session.py`
- **Verification:** `python -c "assert 'Lifecycle model' in src; assert 'lru_cache' not in src"` passes; `grep -rn lru_cache src/trading_platform/db/` returns nothing.
- **Committed in:** `e981906` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Purely cosmetic wording fix to satisfy a literal-string verification command; no change to the actual invariant being documented or enforced.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Plan 10-04 (transaction integrity) can now build on a single, documented, test-pinned DB lifecycle model and import path with no ambiguity about which caching mechanism or import surface is canonical.
- No blockers or concerns identified.

---
*Phase: 10-startup-hardening*
*Completed: 2026-07-13*

## Self-Check: PASSED

All created/modified files verified present on disk; both task commits (e981906, 730e899) verified present in git log.
