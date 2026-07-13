---
phase: 10-startup-hardening
plan: 06
subsystem: infra
tags: [logging, structured-logging, security, sanitization, ast, pytest]

# Dependency graph
requires:
  - phase: 10-startup-hardening (10-02)
    provides: single sanitization chokepoint (sanitize(), emit_structured_log, get_logger factory)
  - phase: 10-startup-hardening (10-04, 10-05)
    provides: final versions of paper_execution.py, worker/__main__.py, bootstrap.py, api/app.py this plan migrates
provides:
  - Every execution/reconciliation/config/startup/control module obtains loggers exclusively through get_logger (LOG-01)
  - Formatter-level sanitize() backstop in JsonLogFormatter closing the message-string gap 10-02 left open
  - AST-based import-boundary enforcement test (tests/test_log_enforcement.py) forbidding logging.getLogger in 12 named modules
  - Emitted-line enforcement test proving no default-config log line leaks credentials or a full broker order ID (LOG-06)
affects: [any future module added to the execution/reconciliation/config/startup/control paths, future log-schema changes]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Formatter-level sanitize() backstop: JsonLogFormatter.format() sanitizes the whole assembled payload (message + context), not just context, as defense-in-depth against callers that bypass emit_structured_log"
    - "AST-based (not substring-grep) import-boundary test for logger-acquisition discipline, avoiding false positives on comments/docstrings"

key-files:
  created:
    - tests/test_log_enforcement.py
  modified:
    - src/trading_platform/services/paper_execution.py
    - src/trading_platform/services/alpaca.py
    - src/trading_platform/services/concurrency_guard.py
    - src/trading_platform/services/operator_status.py
    - src/trading_platform/services/operator_controls.py
    - src/trading_platform/services/reconciliation.py
    - src/trading_platform/worker/__main__.py
    - src/trading_platform/services/bootstrap.py
    - src/trading_platform/api/app.py
    - src/trading_platform/core/logging.py

key-decisions:
  - "order_state_machine.py required zero changes (Task 1's files_modified list included it, but the module never called logging.getLogger at all) — treated as a verified no-op, not skipped silently"
  - "Removed now-unused `import logging` from alpaca.py, bootstrap.py, and api/app.py where get_logger fully replaced the module's only logging.* reference; kept `import logging` in modules still using logging.WARNING/INFO level constants or logging.Logger type hints"
  - "Import-boundary test uses an AST walk (not grep) to detect logging.getLogger call expressions specifically, so a mention inside a comment/docstring/string literal never produces a false positive"
  - "Split the plan's Task 3 (tdd=true) into a practical two-step flow rather than literal RED-before-GREEN: since Task 2 already delivered the formatter backstop, the emitted-line tests were GREEN on first write; verified this wasn't vacuous by round-tripping (temporarily removing sanitize() from the formatter, confirming the dedicated message-string test goes RED, then restoring it)"
  - "Added a dedicated test-in-message-string regression test beyond the plan's literal ask, after advisor review found the two emitted-line tests as originally written seed secrets only via context/extra dict fields — they would have stayed green even if the message-string gap (the exact thing deferred-items.md flagged from 10-02) had never been closed"

patterns-established:
  - "Formatter backstop pattern: any future structured-logging change should sanitize the fully-assembled JSON payload at the formatter, not just at the emit_structured_log chokepoint, so direct/legacy logger calls remain safe by default"

requirements-completed: [LOG-01, LOG-06]

duration: ~20min
completed: 2026-07-13
---

# Phase 10 Plan 06: Logger Migration + Formatter Backstop + Enforcement Tests Summary

**Migrated 9 execution/reconciliation/config/startup/control modules off direct `logging.getLogger` onto the approved `get_logger` wrapper, added a formatter-level `sanitize()` backstop closing the message-string leak gap left by 10-02, and shipped AST-based import-boundary plus emitted-line enforcement tests (16 total) proving LOG-01 and LOG-06 end-to-end.**

## Performance

- **Duration:** ~20 min
- **Completed:** 2026-07-13
- **Tasks:** 3
- **Files modified:** 10 (9 source + 1 new test file)

## Accomplishments
- Every logger acquisition in the 12 in-scope modules (execution, reconciliation, config/startup, control) now goes through `get_logger`, with logger names preserved exactly
- `JsonLogFormatter.format()` now sanitizes the whole assembled JSON payload (message + context), not just `context`, closing the exact gap `deferred-items.md` flagged from 10-02: a secret embedded in a raw message string (e.g. `f"... password={x}"`) is now scrubbed even for callers that bypass `emit_structured_log`
- `tests/test_log_enforcement.py` (16 tests): a parametrized AST-based import-boundary test over the 12 named in-scope modules (LOG-01), and 4 emitted-line tests proving no default-config log line leaks credentials or a full broker order ID, that debug-unmask reveals the full ID while credentials stay redacted, and — added after advisor review — a dedicated test proving the message-string sanitization path specifically (not just context-dict sanitization)

## Task Commits

Each task was committed atomically:

1. **Task 1: Migrate execution + reconciliation path modules onto get_logger** - `d5579f8` (feat)
2. **Task 2: Migrate config/startup modules + formatter sanitize backstop + import-boundary test (LOG-01)** - `ee6f8d9` (feat)
3. **Task 3: LOG-06 emitted-line enforcement test** - `65a5b09` (test), extended by `2581bbf` (test) after advisor review

**Plan metadata:** (this commit)

_Note: Task 3 was TDD-tagged in the plan, but since Task 2 had already delivered the formatter backstop, its tests were GREEN on first write rather than following a literal RED-before-GREEN sequence; the RED phase was instead verified retroactively (see Decisions)._

## Files Created/Modified
- `tests/test_log_enforcement.py` - AST import-boundary test (12 modules) + 4 emitted-line/message-string enforcement tests
- `src/trading_platform/services/paper_execution.py` - 2x `logging.getLogger("trading_platform.paper_execution")` → `get_logger(...)`
- `src/trading_platform/services/alpaca.py` - `logging.getLogger(__name__)` → `get_logger(__name__)`; dropped unused `import logging`
- `src/trading_platform/services/concurrency_guard.py` - `logging.getLogger(__name__)` → `get_logger(__name__)`
- `src/trading_platform/services/operator_status.py` - `self._logger = logging.getLogger(...)` → `get_logger(...)`
- `src/trading_platform/services/operator_controls.py` - `self._logger = logging.getLogger(...)` → `get_logger(...)`
- `src/trading_platform/services/reconciliation.py` - `logging.getLogger("trading_platform.reconciliation")` → `get_logger(...)`
- `src/trading_platform/worker/__main__.py` - 14x `logging.getLogger(...)` acquisitions (13 `"trading_platform.worker"`, 1 `"trading_platform.analytics.report.worker"`) → `get_logger(...)`
- `src/trading_platform/services/bootstrap.py` - `logging.getLogger("trading_platform.dry_run")` → `get_logger(...)`; dropped unused `import logging`
- `src/trading_platform/api/app.py` - `logging.getLogger("trading_platform.bootstrap")` → `get_logger(...)`; dropped unused `import logging`
- `src/trading_platform/core/logging.py` - `JsonLogFormatter.format()` now calls `sanitize(payload, unmask_ids=_DEBUG_UNMASK_IDS)` on the fully assembled payload before `json.dumps`

## Decisions Made
- `order_state_machine.py` (named in Task 1's `files_modified`) required zero changes — grepped and confirmed it has no `logging` import or usage at all; documented as a verified no-op rather than silently skipped.
- Dropped the now-unused `import logging` statement in `alpaca.py`, `bootstrap.py`, and `api/app.py` where `get_logger` was the module's only `logging.*` reference; left `import logging` intact in modules that still use `logging.WARNING`/`logging.INFO` level constants or `logging.Logger` type hints (`paper_execution.py`, `concurrency_guard.py`, `operator_status.py`, `operator_controls.py`, `worker/__main__.py`).
- Import-boundary test implemented via `ast.walk` matching `Attribute(attr="getLogger", value=Name(id="logging"))` call nodes, not a text/grep scan — avoids false positives from `logging.getLogger` appearing in a comment or docstring, and would still catch it if written as `logging . getLogger(...)` or across formatting variants a naive grep might miss.
- Task 3 was tagged `tdd="true"` in the plan, but Task 2's formatter backstop already implemented the mechanism the emitted-line test needed to prove — so the test was GREEN immediately on write, not RED-then-GREEN. To confirm this wasn't a vacuously-passing test, the `sanitize()` call was temporarily removed from the formatter and the full test file re-run to confirm RED, then restored (verified via `git diff`/`git checkout`) and re-confirmed GREEN before committing.
- After an advisor review, added a dedicated `test_default_config_scrubs_secret_embedded_in_message_string` test: the two emitted-line tests as originally written seed secrets only through `context`/`extra` dict fields (kwargs to `emit_structured_log`, or a nested `headers` dict), so neither would have failed even if the formatter backstop sanitized `context` alone (the pre-10-06 state) rather than the whole payload. The new test emits a secret directly in the `message` positional argument and asserts on the secret **value** (not the `"password="` substring, since `_scrub_string` preserves the `key=` prefix — `password=hunter2` becomes `password=[REDACTED]`, so that substring legitimately survives). Verified this test independently goes RED without the backstop and GREEN with it.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking/hygiene] Removed unused `import logging` after get_logger migration**
- **Found during:** Task 1 (alpaca.py) and Task 2 (bootstrap.py, api/app.py)
- **Issue:** After swapping the sole `logging.getLogger(...)` call for `get_logger(...)`, `import logging` became a dead import in these three files (nothing else in them referenced `logging.*`)
- **Fix:** Removed the unused import
- **Files modified:** `src/trading_platform/services/alpaca.py`, `src/trading_platform/services/bootstrap.py`, `src/trading_platform/api/app.py`
- **Verification:** `grep -n "logging\." <file>` confirmed no remaining references; full test suite green
- **Committed in:** `d5579f8` (alpaca.py), `ee6f8d9` (bootstrap.py, api/app.py)

**2. [Rule 1 - Bug/test-gap] Added message-string-specific regression test**
- **Found during:** Task 3, via advisor review after initial write
- **Issue:** The originally-written emitted-line tests would have stayed green even if the message-string sanitization gap (deferred-items.md's specific flag from 10-02) had never actually been closed, since both seeded secrets via `context`/`extra` dict fields only
- **Fix:** Added `test_default_config_scrubs_secret_embedded_in_message_string`, which emits a secret in the message argument itself and asserts on the secret value
- **Files modified:** `tests/test_log_enforcement.py`
- **Verification:** Manually toggled the formatter backstop off/on and confirmed RED/GREEN transitions for this specific test
- **Committed in:** `2581bbf`

---

**Total deviations:** 2 auto-fixed (1 hygiene/blocking, 1 bug/test-gap)
**Impact on plan:** Both were necessary for correctness (clean imports) and for the plan's own stated LOG-06 guarantee to be genuinely test-proven rather than merely claimed. No scope creep — no new production capability was added beyond what the plan specified.

## Issues Encountered
None beyond the deviations above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All of LOG-01 through LOG-06 (the full LOG requirement track for Phase 10) are now Complete.
- Phase 10 (Startup Hardening) is now fully complete: all 6 plans (10-01 through 10-06) executed, all CFG-01/02/03/04/05/06/07, DB-01/02/03/04/05/06, and LOG-01/02/03/04/05/06 requirements satisfied.
- The pre-existing, documented `pg_terminate_backend` teardown flake (`InsufficientPrivilege`, non-deterministic across `test_market_data_access.py`/`test_market_data_ingestion.py`/`test_risk_pipeline.py`/etc.) remains unresolved and out of this plan's scope — reproduced again during this plan's full-suite runs (different test each time), consistent with prior phase-10 findings. Still recommended as a follow-up (see `deferred-items.md`).

---
*Phase: 10-startup-hardening*
*Completed: 2026-07-13*

## Self-Check: PASSED

All 12 claimed created/modified files verified present on disk; all 4 claimed commit hashes (`d5579f8`, `ee6f8d9`, `65a5b09`, `2581bbf`) verified present in `git log --oneline --all`.
