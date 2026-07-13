---
phase: 10-startup-hardening
plan: 01
subsystem: config
tags: [pydantic, config-validation, startup-hardening]

# Dependency graph
requires: []
provides:
  - "core/config_validation.py: ExecutionMode(str, Enum) closed BACKTEST/PAPER/LIVE"
  - "ConfigValidationError(Exception): typed, carries failures: list[(field, expected)], renders one multi-line actionable message"
  - "validate_config(payload, *, mode) -> Settings: pure validator over the raw pre-pydantic payload; owns Settings.model_validate and translates pydantic.ValidationError into ConfigValidationError; runs CFG-01/02/03 semantic checks against the constructed Settings"
affects: [10-05, 10-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure validators own the pydantic model_validate() call themselves and translate ValidationError into a typed, field-named error — never validate a fields already-constructed model whose bounds have already silently rejected bad values."
    - "Mode/environment selection passed as an explicit function parameter rather than a config field, to avoid a config value contradicting the command actually invoked."

key-files:
  created:
    - src/trading_platform/core/config_validation.py
    - tests/test_config_validation.py
  modified: []

key-decisions:
  - "CFG-02 and CFG-03 are satisfied by a single check (broker.alpaca.base_url must match the active mode's endpoint pattern), not two independent checks — there is no separate mode field or live-broker settings block in this codebase today, so base_url is the only signal of which environment a set of credentials targets. Both directions (live endpoint while mode=paper; paper endpoint while mode=live) are tested."
  - "requirements marking: CFG-01, CFG-02, CFG-03, CFG-05, CFG-07 are all marked Complete by this plan despite their REQUIREMENTS.md text mentioning 'process exit' — verified that 10-01 is the sole plan in Phase 10 whose frontmatter lists these five IDs (10-05 lists only CFG-04, CFG-06), confirming the phase author's intended split: 10-01 delivers the pure validation decision, CFG-06 (10-05) separately tracks wiring-before-service-init + the actual non-zero exit. Marking all five here reflects that split, not a shortcut."

patterns-established:
  - "Field-path translation of pydantic ValidationError.errors() (loc tuple -> dotted string) as the mechanism for turning Field(ge/le) bound violations into named application errors, without duplicating bounds in a second table."

requirements-completed: [CFG-01, CFG-02, CFG-03, CFG-05, CFG-07]

# Metrics
duration: ~15min
completed: 2026-07-13
---

# Phase 10 Plan 01: Config Validation Core Summary

**Pure `validate_config(payload, *, mode)` core: closed `ExecutionMode` enum, field-named `ConfigValidationError`, and pydantic-`ValidationError`-translation that makes CFG-05 tolerance-bound violations reachable as a named failure instead of a raw pydantic error — no entrypoint wiring yet (that's 10-05).**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-07-13T19:16:34Z
- **Tasks:** 2 (both TDD: RED/GREEN cycles)
- **Files modified:** 2 (1 created source, 1 created test)

## Accomplishments
- `ExecutionMode(str, Enum)` closed to exactly BACKTEST/PAPER/LIVE, passed by the caller — no corresponding `Settings` field.
- `ConfigValidationError` is a typed `Exception` (not a bare `ValueError`) carrying `failures: list[tuple[field, expected_shape]]`, rendering `"Configuration invalid:\n - {field}: expected {shape}"` per failure (CFG-07).
- `validate_config` owns `Settings.model_validate(payload)` and translates any raw `pydantic.ValidationError` into a field-named `ConfigValidationError` — proven on the real path: an out-of-range `risk_per_trade` or `stale_run_timeout_minutes` in the raw payload raises `ConfigValidationError` naming that dotted field path, not an unguarded pydantic error (CFG-05).
- Semantic per-mode checks against the constructed `Settings`: paper/live mode with empty `broker.alpaca.api_key`/`api_secret` raises naming the empty field (CFG-01); backtest mode with empty keys still returns a valid `Settings` (empty-keys-still-boots invariant); a mismatched `broker.alpaca.base_url` for the active mode (live endpoint while paper, or paper endpoint while live) raises naming `base_url` (CFG-02/CFG-03).
- A fully-valid paper payload (non-empty keys, paper `base_url`, in-range tolerances) returns a constructed `Settings` with no error.
- Verified zero I/O: `grep -n "session_scope\|getLogger\|open("` against the module returns nothing.

## Task Commits

Each task was committed atomically (TDD RED -> GREEN):

1. **Task 1: ExecutionMode + ConfigValidationError + pydantic-translation (CFG-05, CFG-07)**
   - `1bf4048` test: failing tests for enum/error/tolerance-bounds/valid-payload
   - `a486450` feat: implementation — all filtered task-1 tests pass
2. **Task 2: Per-mode required-secret, cross-field, mutual-exclusion checks (CFG-01, CFG-02, CFG-03)**
   - `d8fa104` test: failing tests for paper/live secret + base_url checks
   - `8b0acbb` feat: semantic checks implemented — full suite (12 tests) passes

**Plan metadata:** (this commit)

## Files Created/Modified
- `src/trading_platform/core/config_validation.py` - `ExecutionMode`, `ConfigValidationError`, `validate_config(payload, *, mode) -> Settings`, `_translate_pydantic_errors`, `_semantic_failures`
- `tests/test_config_validation.py` - 12 unit tests covering CFG-01/02/03/05/07 against raw `build_settings_payload()` dicts (deep-copied and mutated per test)

## Decisions Made
- CFG-02/CFG-03 collapse onto a single `broker.alpaca.base_url`-vs-mode check rather than two independently implemented checks, per the plan's own `key_facts` (no live-broker config block exists today). Both directions are tested (`test_live_endpoint_configured_while_mode_paper_raises_naming_base_url`, `test_paper_endpoint_configured_while_mode_live_raises_naming_base_url`).
- Requirements marking for CFG-01/02/03/05 (whose REQUIREMENTS.md text references "process exit") was resolved by confirming 10-01 is the sole Phase-10 plan listing these IDs in its frontmatter — 10-05 lists only CFG-04/CFG-06 — so the phase author's intended split assigns the pure validation decision to this plan and the wiring/exit-code behavior to CFG-06 (10-05). All five requirements in this plan's frontmatter (CFG-01, CFG-02, CFG-03, CFG-05, CFG-07) are marked Complete on that basis.

## Deviations from Plan

None - plan executed exactly as written. Two additional tests beyond the plan's named 4-scenario list for Task 2 (`test_live_mode_with_empty_alpaca_keys_raises_naming_api_key`, `test_paper_endpoint_configured_while_mode_live_raises_naming_base_url`) were added to exercise the LIVE-mode branch explicitly named in the plan's `key_facts` ("the LIVE enum member exists per the closed-set spec... write it to the spec") — this is coverage depth within the same task's declared scope, not a scope change.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `validate_config(payload, *, mode)` is ready for 10-05 to wire into entrypoints via `validate_config(build_settings_payload(), mode=...)`, catching `ConfigValidationError` and mapping it to a non-zero process exit (CFG-06), plus adding the DB-reachability check (CFG-04) this plan explicitly left out of scope.
- No blockers.

---
*Phase: 10-startup-hardening*
*Completed: 2026-07-13*

## Self-Check: PASSED

- FOUND: src/trading_platform/core/config_validation.py
- FOUND: tests/test_config_validation.py
- FOUND: .planning/phases/10-startup-hardening/10-01-SUMMARY.md
- FOUND commit: 1bf4048 (test: task 1 RED)
- FOUND commit: a486450 (feat: task 1 GREEN)
- FOUND commit: d8fa104 (test: task 2 RED)
- FOUND commit: 8b0acbb (feat: task 2 GREEN)
