---
phase: 10-startup-hardening
plan: 02
subsystem: logging
tags: [python, logging, security, redaction, stdlib-re]

# Dependency graph
requires: []
provides:
  - "core/log_sanitizer.py: sanitize(payload, *, unmask_ids=False) — pure, recursive, dependency-free (stdlib re) redaction of credential/token keys, password-bearing connection URLs, embedded key=value secrets, and Authorization/Bearer header values"
  - "core/log_sanitizer.py: mask_order_id(value, *, unmask=False) — last-6 masking for broker order IDs, full value when unmask=True"
  - "core/logging.py: emit_structured_log routes its assembled context dict through sanitize() before logger.log — single sanitization chokepoint (LOG-02)"
  - "core/logging.py: get_logger(name) — the approved logger factory 10-06 will migrate execution/reconciliation/config callers onto"
  - "core/settings.py: LoggingSettings.debug_unmask_ids (default False) — flows through existing YAML/env config plumbing to unmask broker order IDs"
affects: [10-06-startup-hardening]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Single sanitization chokepoint: every emit_structured_log payload passes through sanitize() before reaching the logger; module-level _DEBUG_UNMASK_IDS flag defaults to masked (safe-by-default) even if configure_logging never ran"
    - "sanitize() is a pure recursive function over dict/list/tuple/str — never mutates its input, always returns a new structure"

key-files:
  created:
    - src/trading_platform/core/log_sanitizer.py
    - tests/test_log_sanitizer.py
  modified:
    - src/trading_platform/core/logging.py
    - src/trading_platform/core/settings.py

key-decisions:
  - "debug_unmask_ids reaches emit_structured_log via a module-level global (_DEBUG_UNMASK_IDS) set by configure_logging() from LoggingSettings, not by calling load_settings() inside emit_structured_log itself — avoids repeated YAML loads / FileNotFoundError risk on every log call, and keeps the log path safe-by-default (masked) before configure_logging runs"
  - "settings.py was not listed in this plan's files_modified frontmatter, but the plan body's key_facts explicitly directs adding LoggingSettings.debug_unmask_ids — treated as a plan-body-wins-over-frontmatter-omission deviation (Rule 3, blocking: the debug-unmask flag has no other config surface to live on)"
  - "The JsonLogFormatter defense-in-depth backstop mentioned in the plan's key_facts was NOT added — neither task's done-criteria requires it, and the single required chokepoint (emit_structured_log) is fully covered; keeping formatter changes out avoids unrequested scope"
  - "Test/implementation for each task were committed together (not as separate RED/GREEN commits) — tests and implementation for Task 1 were authored together this session since the file didn't exist yet; both were verified passing before commit"

requirements-completed: [LOG-02, LOG-03, LOG-04, LOG-05]

# Metrics
duration: 12min
completed: 2026-07-13
---

# Phase 10 Plan 02: Log Sanitization Core Summary

**Pure `sanitize()`/`mask_order_id()` redaction core wired as the single chokepoint inside `emit_structured_log`, with a `get_logger()` API contract and a config-driven debug-unmask flag for broker order IDs**

## Performance

- **Duration:** 12 min
- **Started:** 2026-07-13T19:18:50Z (previous commit) / work began ~19:20Z
- **Completed:** 2026-07-13T19:24:44Z
- **Tasks:** 2
- **Files modified:** 4 (2 created, 2 modified)

## Accomplishments
- `core/log_sanitizer.py`: recursive, pure, stdlib-only `sanitize()` redacting credential/token dict keys (password/api_key/api_secret/secret/token/authorization, case-insensitive, any nesting depth), password-bearing connection URLs, embedded `key=value` secrets in free-text strings, and Bearer/Token/Basic auth header values
- `mask_order_id()` masks broker order IDs to a last-6 form by default; full value only when `unmask=True`; short/non-string values pass through unchanged
- `emit_structured_log` now routes its assembled context dict through `sanitize(context, unmask_ids=_DEBUG_UNMASK_IDS)` before `logger.log` — one sanitization chokepoint, verified via an end-to-end `caplog`-style handler-capture test
- `LoggingSettings.debug_unmask_ids` (default `False`) added to `core/settings.py`; `configure_logging()` sets the module-level `_DEBUG_UNMASK_IDS` flag from it, so logs emitted before `configure_logging` runs are still safe-by-default
- `get_logger(name)` exported with a docstring establishing it as the approved logger factory 10-06 will migrate callers onto
- 32 unit tests in `tests/test_log_sanitizer.py` covering every redaction rule, nesting, no-mutation-of-input, order-id masking/unmasking, and the end-to-end emit_structured_log sanitization path

## Task Commits

Each task was committed atomically:

1. **Task 1: sanitize() redaction rules for credentials, tokens, conn-URLs, and auth headers** - `79b021a` (feat)
2. **Task 2: broker-order-id masking + wire sanitize into emit_structured_log + get_logger wrapper** - `9907aac` (feat)

**Plan metadata:** (this commit)

## Files Created/Modified
- `src/trading_platform/core/log_sanitizer.py` - `sanitize()`, `mask_order_id()`, `SENSITIVE_KEY_PATTERN`, `ORDER_ID_KEY_PATTERN`, `REDACTION` constant
- `tests/test_log_sanitizer.py` - 32 unit tests for both tasks
- `src/trading_platform/core/logging.py` - `emit_structured_log` sanitizes context; `configure_logging` sets `_DEBUG_UNMASK_IDS`; `get_logger()` added
- `src/trading_platform/core/settings.py` - `LoggingSettings.debug_unmask_ids: bool = False`

## Decisions Made
See `key-decisions` in frontmatter. Summary: module-level flag (not per-call settings lookup) carries the debug-unmask state into the hot log path; `settings.py` was edited despite not being listed in the plan's `files_modified` frontmatter because the plan body explicitly required it; the optional formatter-level defense-in-depth backstop was intentionally left out of scope.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added `LoggingSettings.debug_unmask_ids` in `settings.py`, which was not listed in this plan's `files_modified` frontmatter**
- **Found during:** Task 2 (wiring `emit_structured_log`)
- **Issue:** The plan's `files_modified` frontmatter lists only `log_sanitizer.py`, `logging.py`, and the test file, but the plan body's `key_facts` explicitly instructs adding a `LoggingSettings.debug_unmask_ids` field to `settings.py` (lines 34-37) as the preferred way to plumb the debug-unmask flag through existing config. Without it, "explicit debug-unmask flag" (a `must_haves` truth) has no config surface.
- **Fix:** Added `debug_unmask_ids: bool = False` to `LoggingSettings`; flows automatically into both `Settings` and `EnvironmentOverrides` (both already reference `LoggingSettings`).
- **Files modified:** `src/trading_platform/core/settings.py`
- **Verification:** `python -m pytest tests/test_log_sanitizer.py -x -q` (32 passed); full suite (`python -m pytest -q`, 262 passed) shows no regression.
- **Committed in:** `9907aac` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking — no architectural change, single boolean field on an existing settings model)
**Impact on plan:** Necessary to satisfy the plan's own explicit key_facts instruction and the must_haves debug-unmask truth. No scope creep — no new settings surfaces beyond the one field the plan named.

## Issues Encountered
- Full-suite run (`python -m pytest -q`) intermittently showed one unrelated `psycopg.errors.InsufficientPrivilege` error in `tests/test_market_data_ingestion.py::TestIngestionPipeline::test_upsert_daily_bars_is_idempotent` ("must be a superuser to terminate superuser process"). Confirmed pre-existing and unrelated to this plan's changes: the same test passes in isolation, and re-running the full suite (both with and without this plan's changes stashed) reproduced it inconsistently — an order/timing-dependent DB-fixture flake, not a code regression. Final full-suite run (262 passed) was clean. Out of this plan's declared scope; not fixed.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- The `sanitize()`/`mask_order_id()` core and the `emit_structured_log` chokepoint are ready for 10-06, which migrates all execution/reconciliation/config logging callers off `logging.getLogger` onto `get_logger()` and adds the import-boundary/enforcement tests (LOG-01, LOG-06).
- LOG-01 (caller migration) and LOG-06 (emitted-line enforcement test) remain explicitly out of scope here, as declared in this plan's `<verification>` section — no blocker, by design.
- Deferred: the intermittent `test_market_data_ingestion.py` DB-privilege flake (see Issues Encountered) is unrelated to this plan and was not investigated further; worth a look if it recurs during 10-06 execution.

---
*Phase: 10-startup-hardening*
*Completed: 2026-07-13*

## Self-Check: PASSED

- FOUND: src/trading_platform/core/log_sanitizer.py
- FOUND: tests/test_log_sanitizer.py
- FOUND: .planning/phases/10-startup-hardening/10-02-SUMMARY.md
- FOUND commit: 79b021a
- FOUND commit: 9907aac
- FOUND: "sanitize" reference in src/trading_platform/core/logging.py
