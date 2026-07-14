---
phase: 12-structural-refactor-and-tooling
plan: 01
subsystem: refactoring
tags: [reconciliation, tolerances, config, decimal, pytest-baseline]

# Dependency graph
requires:
  - phase: 11-query-performance
    provides: "Completed Tier-0 correctness kernel + green full suite that STRUCT-01 gates on"
provides:
  - "12-BASELINE.md — the immutable Phase-12 pass-count invariant (306 passed / 0 failed) every later plan must reproduce unchanged"
  - "services/config/ package with tolerances.py as the single typed source of MONEY_TOLERANCE and QUANTITY_TOLERANCE"
  - "Duplicated _MONEY_TOLERANCE/_QUANTITY_TOLERANCE constants retired from reconciliation.py and reconciliation_matcher.py"
affects: [12-02, 12-03, 12-04, 12-05, 12-06, 12-07]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Centralized typed tolerance module (Final[Decimal]) as single source of truth for reconciliation comparison tolerances"
    - "Phase-wide pytest pass-count baseline as the zero-behavior-change proof mechanism for Tier-3 refactors"

key-files:
  created:
    - .planning/phases/12-structural-refactor-and-tooling/12-BASELINE.md
    - src/trading_platform/services/config/__init__.py
    - src/trading_platform/services/config/tolerances.py
  modified:
    - src/trading_platform/services/reconciliation.py
    - src/trading_platform/services/reconciliation_matcher.py

key-decisions:
  - "Baseline invariant is defined as the passed-count (306) AND failed-count (0); the pg_terminate_backend teardown-error tally is documented as variable environmental noise to be ignored in later-plan comparisons"
  - "STRUCT-01 gate honored against real artifacts (00-VERIFY GREEN + live green suite), NOT the stale REQUIREMENTS.md Tier-0 checkboxes"
  - "MONEY_SCALE (quantization scale in backtest/portfolio reporting) left untouched — it is not a comparison tolerance and is out of STRUCT-07 scope"

patterns-established:
  - "Pattern 1: reconciliation comparison tolerances live in services/config/tolerances.py; no per-file duplicated tolerance constants"
  - "Pattern 2: no-behavior-change refactors are proven by an unchanged full-suite pass count with zero assertion edits"

requirements-completed: [STRUCT-01, STRUCT-07]

# Metrics
duration: ~15min
completed: 2026-07-14
---

# Phase 12 Plan 01: Refactor Preconditions + Tolerance Consolidation Summary

**Pinned the Phase-12 zero-behavior-change baseline (306 passed / 0 failed) as the STRUCT-01 gate, then retired the first duplication by moving the reconciliation money/quantity tolerances into one typed `services/config/tolerances.py` module — full suite still 306 passed / 0 failed with zero assertion changes.**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-07-14
- **Tasks:** 2/2
- **Files created:** 3
- **Files modified:** 2

## Accomplishments
- STRUCT-01 gate proven: `00-VERIFY.md` confirmed GREEN and the full suite runs green (`306 passed, 0 failed`) BEFORE any Tier-3 refactor code landed.
- Captured `12-BASELINE.md` recording `306 passed` as the immutable invariant every subsequent Phase-12 plan must reproduce unchanged.
- STRUCT-07 complete: `MONEY_TOLERANCE` and `QUANTITY_TOLERANCE` now live in one typed module; the three duplicated private constants across `reconciliation.py` and `reconciliation_matcher.py` are deleted.
- Verified zero-behavior-change: reconciliation tests 54/54 green, full suite unchanged at `306 passed / 0 failed`, no assertion added/removed/modified.

## Task Commits

Each task was committed atomically:

1. **Task 1: STRUCT-01 gate — verify Tier-0 green and capture baseline** - `676b813` (docs)
2. **Task 2: STRUCT-07 — extract reconciliation tolerances into one typed module** - `0d090d2` (refactor)

**Plan metadata:** committed with SUMMARY/STATE/ROADMAP/REQUIREMENTS update.

## Files Created/Modified
- `.planning/phases/12-structural-refactor-and-tooling/12-BASELINE.md` - Records the 306-passed / 0-failed Phase-12 invariant, the exact command, and the documented teardown-privilege flake.
- `src/trading_platform/services/config/__init__.py` - New `services/config` package marker (later plans add validation.py/secrets.py here).
- `src/trading_platform/services/config/tolerances.py` - Single typed source: `MONEY_TOLERANCE: Final[Decimal] = Decimal("0.01")`, `QUANTITY_TOLERANCE: Final[Decimal] = Decimal("0.000001")`.
- `src/trading_platform/services/reconciliation.py` - Deleted local `_MONEY_TOLERANCE`; imports `MONEY_TOLERANCE`; 4 usages updated.
- `src/trading_platform/services/reconciliation_matcher.py` - Deleted local `_MONEY_TOLERANCE`/`_QUANTITY_TOLERANCE`; imports both; usages updated.

## Verification

- **STRUCT-01:** `00-VERIFY.md` Status `✅ GREEN` confirmed by read; full suite `306 passed, 0 failed`; baseline captured.
- **STRUCT-07:** `grep -rn "_MONEY_TOLERANCE\|_QUANTITY_TOLERANCE" src/` returns nothing (grep clean); both consumers import from `services/config/tolerances.py`.
- **Zero-behavior-change (STRUCT-02 property):** full-suite pass count equals the captured baseline (306 passed / 0 failed) after the move; reconciliation tests 54/54 green; no assertions changed.

## Deviations from Plan

None — plan executed as written. Tolerance values relocated byte-for-byte with no value change.

## Known Stubs

None.

## Notes / Environmental Observations

The full suite reports a **variable** number of `ERROR at teardown` entries per run (observed 3, 6, 7, and 8 across four runs) — every one is the documented `pg_terminate_backend` `InsufficientPrivilege` teardown-privilege flake. The test functions themselves all pass; pytest counts these as `errors`, distinct from `failed`. The stable `306 passed` / `0 failed` across all runs while the error tally varies is direct evidence the flake is decoupled from test outcomes. Honesty flag (recorded in 12-BASELINE.md): the STATE.md blocker and plan NOTE described this as affecting "one unrelated" test under *parallel* load, but it surfaced across 3–8 DB-backed tests under **sequential** execution here — same root cause, broader in scope than previously documented. Root cause not investigated (out of STRUCT-01 scope).

## Self-Check: PASSED
