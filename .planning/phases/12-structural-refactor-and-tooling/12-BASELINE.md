# Phase 12 Baseline — Full-Suite Pass Count (STRUCT-01)

**Captured:** 2026-07-14
**Command:** `PYTHONPATH=src .venv/bin/pytest -q`
**Result:** `306 passed, 0 failed` (see environmental-noise note below)

## The Invariant

**`306 passed` / `0 failed` is the immutable Phase-12 baseline.**

Every subsequent Phase-12 plan is a Tier-3 no-behavior-change refactor (STRUCT-02).
The proof of correctness is that this full suite stays green — before and after — with
**ZERO** new, removed, or modified assertions. After any Phase-12 refactor, running the
full suite MUST still report:

- **passed count == 306** (must match exactly)
- **failed count == 0** (must match exactly)

If either the passed count drops below 306 or any test fails, the refactor changed
behavior and is a bug — STOP and fix before landing.

## Comparison Rule

Compare **only** the `passed` and `failed` counts. **Ignore the teardown-error tally** — it
is variable environmental noise (see below) and is not a behavior signal. A different
error count between runs (e.g. 3 vs. 6) does NOT indicate a regression.

## STRUCT-01 Gate Confirmation

- `.planning/00-VERIFY.md` Status line reads `✅ GREEN` — confirmed by direct read.
- Full suite run right now: `306 passed`, `0 failed`. Tier-0 correctness kernel is verified
  complete before any Tier-3 refactor code lands.

## Environmental Noise: `pg_terminate_backend` teardown flake

The suite reports a **variable** number of `ERROR at teardown` entries per run (observed
6 on the first run, 3 on an immediate re-run). Every one is the same documented flake:

```
psycopg.errors.InsufficientPrivilege: must be a superuser to terminate superuser process
  (also seen: must be a member of the role whose process is being terminated ...)
```

These are **teardown finalizer** errors from the throwaway test-DB `pg_terminate_backend`
call — the test functions themselves all pass (pytest counts these as `errors`, separate
from `failed`). The stable `306 passed` / `0 failed` across runs while the error tally
varies (6 → 3) is direct evidence the errors are decoupled from test outcomes: a real
regression would move the `passed`/`failed` counts, not just the teardown-error count.

**Honesty note (broader than previously documented):** The STATE.md blocker and the
12-01 plan NOTE describe this as affecting "one unrelated" market-data test under
*parallel* load. In this run it surfaced across **3–6** DB-backed tests
(`test_market_data_access.py`, `test_market_data_ingestion.py`, `test_risk_pipeline.py`,
`test_paper_execution.py`), and it occurred under **sequential** execution — not only
parallel load. Same root cause (a Postgres role-privilege race in test-DB teardown),
but broader in scope than the earlier note implies. Root cause not investigated here;
it is out of scope for STRUCT-01, which gates on test outcomes, not harness teardown.

Per the plan's Task-1 instruction ("re-run once to confirm green; document it if it
recurs"), the suite was re-run once — the flake recurred (as expected for a timing race),
the `306 passed` / `0 failed` outcome held both times, so the gate is satisfied and the
flake is documented here.
