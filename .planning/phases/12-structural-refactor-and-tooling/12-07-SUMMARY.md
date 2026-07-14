---
phase: 12-structural-refactor-and-tooling
plan: 07
subsystem: infra
tags: [ruff, mypy, pre-commit, tooling, ci-gate]

# Dependency graph
requires:
  - phase: 12-structural-refactor-and-tooling
    provides: "Final Phase-12 module structure (services/execution, services/reconciliation, services/config, worker/commands) that this plan's tooling targets"
provides:
  - "Blocking local pre-commit gate: ruff lint (repo-wide) + ruff format (Phase-12 surface only)"
  - "Blocking local pre-commit gate: mypy scoped to services/execution, services/reconciliation, services/config"
  - "[tool.ruff] and [tool.mypy] pyproject.toml config sections"
  - ".pre-commit-config.yaml wiring ruff/ruff-format/mypy hooks"
affects: [future-phases-touching-execution-reconciliation-config, ci-tooling]

# Tech tracking
tech-stack:
  added: ["ruff>=0.8,<1.0 (0.15.21 installed)", "mypy>=1.13,<2.0 (1.20.2 installed)", "pre-commit>=4.0,<5.0 (4.6.0 installed)"]
  patterns:
    - "ruff-format hook restricted via `files:` regex to the Phase-12 structural surface only, so any future commit staging an out-of-scope file can never trigger an accidental repo-wide reformatting diff"
    - "mypy wired as a local pre-commit hook (language: system, pass_filenames: false) with static `args` pointing at exactly the three in-scope package dirs, gated by a `files:` trigger regex over the same three packages"
    - "E501 (line-too-long) deliberately excluded from the blocking ruff lint rule set — pre-existing repo-wide long lines are out of Phase-12 scope; ruff format still wraps code where safe"
    - "tests/* per-file-ignore for ruff F811, since pytest's import-fixture-then-reuse-as-parameter-name idiom is a false positive for that rule"

key-files:
  created:
    - .pre-commit-config.yaml
  modified:
    - pyproject.toml
    - src/trading_platform/services/execution/submit_orders.py
    - src/trading_platform/services/reconciliation/matcher.py
    - src/trading_platform/services/reconciliation/report.py
    - src/trading_platform/services/risk.py
    - tests/test_api_reads.py
    - tests/test_operator_controls.py
    - tests/test_db_lifecycle.py
    - tests/test_market_data_ingestion.py
    - "(~35 additional files via repo-wide `ruff check --fix` isort/unused-import cleanup — see Files Created/Modified)"

key-decisions:
  - "E501 excluded from the blocking lint rule set rather than fixed repo-wide (~200+ pre-existing long lines, mostly comments/strings/test assertions, unrelated to Phase-12's scope) — enforcing it would have forced a large reformatting-only diff the plan explicitly scopes away from"
  - "ruff-format pre-commit hook given an explicit `files:` regex scoping it to the Phase-12 structural surface, beyond what the plan's key_facts described (relying only on 'hooks only run on staged files') — added as a Rule-2 correctness safeguard after an initial commit attempt proved the un-scoped hook reformats ANY staged Python file, not just ones the operator intends to touch"
  - "Task 1 and Task 2's code changes to submit_orders.py/matcher.py/report.py were committed together in the Task 2 commit rather than split further, since ruff-format's whitespace reflow and the mypy annotation-only fixes touch overlapping lines in the same files"

requirements-completed: [TOOL-01, TOOL-02]

duration: 45min
completed: 2026-07-15
---

# Phase 12 Plan 07: Ruff + mypy Blocking Pre-commit Gates Summary

**Local pre-commit hooks now block commits on ruff lint/format failures (repo-wide lint, Phase-12-scoped format) and on mypy type errors in services/execution, services/reconciliation, and services/config — closing out TOOL-01 and TOOL-02 and completing Phase 12 (7/7 plans).**

## Performance

- **Duration:** ~45 min
- **Tasks:** 2
- **Files modified:** 62 (2 tooling-config files + ~4 files with mypy annotation fixes + ~35 files touched only by repo-wide `ruff check --fix` isort/unused-import cleanup + 21 files reformatted within the declared Phase-12 surface by 12-02..12-06's own prior work being re-verified against ruff-format, actually 8 files reformatted by the initial `ruff format` pass)

## Accomplishments

- `.pre-commit-config.yaml` created: `astral-sh/ruff-pre-commit` (ruff lint repo-wide, ruff-format scoped to the Phase-12 structural surface) + a local mypy hook scoped to exactly `services/execution`, `services/reconciliation`, `services/config`. All three block the commit (non-zero exit) on failure. Git hook installed via `pre-commit install`.
- `pyproject.toml` gained `[tool.ruff]` (target-version py312, line-length 100, `select = ["E","F","I","W"]`, E501 ignored, isort first-party config, a `tests/*` F811 per-file-ignore) and `[tool.mypy]` (python 3.12, `mypy_path = "src"`, `explicit_package_bases`, scoped `files` list) config sections, plus `ruff`/`mypy`/`pre-commit` added to `[project.optional-dependencies] dev`.
- Repo-wide `ruff check --fix` landed (isort reordering + unused-import removal across ~35 files) with zero behavior change, confirmed by three Rule-1 fixes ruff's autofix got wrong (two false-positive fixture-import removals, one genuinely unused local) and 9 pre-existing `E402` violations resolved with the same `# noqa: E402` idiom already established elsewhere in the test suite.
- `ruff format` applied only to the declared Phase-12 structural surface (`services/config`, `services/execution`, `services/reconciliation`, `worker/`), not the whole repo.
- 4 mypy errors in the three scoped packages resolved with annotation-only changes (no runtime behavior change): one `assert`-based Optional narrowing justified by a union-of-keys loop invariant, one parameter type widened from `list[PaperOrder]` to `Sequence[PaperOrder]` to match its actual read-only call site, and two local-variable renames in `submit_orders.py` to stop a single variable from being both a definite `PaperOrder` and an `Optional[PaperOrder]` across branches.
- Blocking behavior proven with three reverted deliberate violations (lint unused-import, format spacing, mypy return-type mismatch), each via `pre-commit run <hook> --files <file>` (never `--all-files`), all returning non-zero as required.
- Full suite re-confirmed green at `306 passed / 0 failed` after both tasks, matching 12-BASELINE.md exactly.
- `.planning/milestones/v1.1-paused/REQUIREMENTS.md`: TOOL-01 and TOOL-02 marked Complete (checklist + traceability table) — Phase 12 (Structural Refactor and Tooling) is now fully complete, all STRUCT-01..08 and TOOL-01/02 Complete.

## Task Commits

1. **Task 1: TOOL-01 — ruff config, format pass, and blocking pre-commit hook** - `a94e238` (feat)
2. **Task 2: TOOL-02 — mypy over execution/reconciliation/config, blocking on pre-commit** - `98ac8b1` (feat)

**Plan metadata:** committed separately after this SUMMARY (see final commit below)

## Files Created/Modified

- `.pre-commit-config.yaml` - ruff (lint, repo-wide) + ruff-format (Phase-12 surface only) + local mypy (execution/reconciliation/config) hooks, all blocking
- `pyproject.toml` - `[tool.ruff]`, `[tool.mypy]` config sections; ruff/mypy/pre-commit added to dev deps
- `src/trading_platform/services/reconciliation/matcher.py` - format pass + `assert`-narrowed Optional for mypy
- `src/trading_platform/services/reconciliation/report.py` - format pass + `Sequence[PaperOrder]` parameter widening for mypy
- `src/trading_platform/services/execution/submit_orders.py` - format pass + `retrieved_order`/`failed_order` variable-narrowing fixes for mypy
- `src/trading_platform/services/risk.py` - Rule-1 fix: removed unused `strategy_record` local (F841) that ruff's autofix surfaced
- `tests/test_api_reads.py`, `tests/test_operator_controls.py` - Rule-1 fix: restored two pytest-fixture re-imports (`migrated_analytics_db`, `strategy_config_override`, `migrated_paper_db`) that `ruff check --fix` incorrectly stripped as unused (F401 false positive on the fixture-reuse idiom), with `# noqa: F401` matching the pattern already established in `test_paper_preflight_query_count.py`
- `tests/test_db_lifecycle.py`, `tests/test_market_data_ingestion.py` - added `# noqa: E402` to pre-existing `sys.path.insert`-then-import blocks, matching the idiom already used elsewhere
- ~30 additional `src/` and `tests/` files - repo-wide isort import reordering / unused-import removal only, via `ruff check --fix`, no logic changes

## Decisions Made

- E501 excluded from the blocking ruff lint rule set (see key-decisions above)
- ruff-format hook explicitly scoped via a `files:` regex to the Phase-12 structural surface (Rule 2 — a Rule-3 auto-fixed self-discovered gap: the first commit attempt showed the un-scoped hook would reformat any staged Python file, not just intended ones)
- Task 1's ruff-format pass on `submit_orders.py`/`matcher.py`/`report.py` and Task 2's mypy annotation fixes on the same files were committed together in the Task 2 commit, since the two sets of changes touch overlapping lines

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed unused `strategy_record` local in `services/risk.py`**
- **Found during:** Task 1 (repo-wide `ruff check --fix`)
- **Issue:** `ensure_strategy_record(session, metadata)` was assigned to `strategy_record` but the value was never read; it's called purely for its DB side effect (confirmed by reading `bootstrap.py`'s `ensure_strategy_record`)
- **Fix:** Dropped the unused assignment; the call is now `ensure_strategy_record(session, metadata)` with no LHS
- **Files modified:** `src/trading_platform/services/risk.py`
- **Verification:** Full suite still 306 passed / 0 failed
- **Committed in:** `a94e238` (Task 1 commit)

**2. [Rule 1 - Bug] Restored two pytest-fixture imports `ruff check --fix` incorrectly stripped**
- **Found during:** Task 1, post-lint-fix full-suite run (13 errors: `fixture 'migrated_analytics_db'/'migrated_paper_db' not found`)
- **Issue:** `ruff --fix`'s F401 (unused-import) rule doesn't understand the pytest idiom of importing a fixture function then reusing its name as a test-function parameter — it silently deleted `migrated_analytics_db`/`strategy_config_override` from `test_api_reads.py` and `migrated_paper_db` from `test_operator_controls.py`, breaking fixture resolution for 13 tests
- **Fix:** Restored both imports with `# noqa: F401 (reused DB harness fixture)`, matching the identical pattern already established in `test_paper_preflight_query_count.py` for the same idiom
- **Files modified:** `tests/test_api_reads.py`, `tests/test_operator_controls.py`
- **Verification:** Full suite green at 306 passed / 0 failed (was 293 passed / 13 errors before the fix)
- **Committed in:** `a94e238` (Task 1 commit)

**3. [Rule 3 - Blocking] Scoped the ruff-format pre-commit hook after it reformatted the whole repo on first commit attempt**
- **Found during:** Task 1, first `git commit` attempt (pre-commit hooks are self-gating per this plan's explicit warning)
- **Issue:** The initially-written `.pre-commit-config.yaml` had no `files:` restriction on the `ruff-format` hook. Because Task 1's own commit staged ~55 files (all touched by the repo-wide `ruff check --fix` pass), the hook ran ruff-format against every staged `.py` file — not just the Phase-12 surface — reformatting 35 out-of-scope files (`db/models/*.py`, `services/analytics.py`, `services/bootstrap.py`, several more test files) before the commit failed on the hook's "files were modified" exit code
- **Fix:** Discarded the unintended out-of-scope reformatting (`git checkout -- <file>` for each affected file, restoring working-tree content to the already-staged, intended version) and added an explicit `files:` regex to the ruff-format hook restricting it to `^src/trading_platform/(services/(config|execution|reconciliation)/|worker/)`, so the hook can never again reformat a file outside the declared Phase-12 surface regardless of what else is staged in a given commit
- **Files modified:** `.pre-commit-config.yaml` (added `files:` regex); no source files retained the accidental reformat
- **Verification:** Re-ran the commit; ruff-format hook now reports "Passed" against the same staged set, and a follow-up `ruff format --check` over the full Phase-12 surface confirms no drift
- **Committed in:** `a94e238` (Task 1 commit)

---

**Total deviations:** 3 auto-fixed (2 Rule-1 bug fixes, 1 Rule-3 blocking-issue fix)
**Impact on plan:** All three were necessary for correctness (fixture resolution, dead-code removal) or to honor the plan's own explicit no-repo-wide-reformat constraint. No scope creep — deviation #3 is exactly the failure mode the plan's `<hooks_self_gate_warning>` predicted, self-corrected without bypassing the gate.

## Issues Encountered

None beyond the deviations documented above. The `pg_terminate_backend` teardown flake (12-BASELINE.md documented noise) did not recur in either full-suite run during this plan's execution — both runs were clean 306 passed / 0 failed / 0 errors.

## User Setup Required

None - no external service configuration required. `ruff`, `mypy`, and `pre-commit` are installed into the project's existing `.venv` via `pip install`; the git pre-commit hook is installed at `.git/hooks/pre-commit` and will run automatically for anyone with this venv activated on future commits in this working tree. Contributors using a fresh clone/venv will need to run `pip install -e ".[dev]"` and `pre-commit install` once (not automated by this plan — CI/onboarding automation was explicitly out of scope, this is a local-only gate).

## Next Phase Readiness

- Phase 12 (Structural Refactor and Tooling) is now fully complete: all of STRUCT-01 through STRUCT-08 and TOOL-01/TOOL-02 are Complete in `.planning/milestones/v1.1-paused/REQUIREMENTS.md`.
- v1.1 milestone remains paused pending the orchestrator's/user's next decision (per STATE.md's existing note on the Phase 9 RECON-05/07 marking gap and other pending items) — this plan does not change that status, it only closes out Phase 12's own scope.
- No blockers introduced. The pre-commit gate is local-only (no CI); if a future CI pipeline is added, it should invoke the same `.venv/bin/pre-commit run --all-files` (mindful that `--all-files` DOES intentionally reformat everything the first time it's run in CI against a fresh checkout unless the CI job is adjusted to mirror the local scoping — flagged here for whoever adds CI next, not fixed in this plan since CI infrastructure is explicitly out of scope for Phase 12 per the plan's own `<objective>`).

---
*Phase: 12-structural-refactor-and-tooling*
*Completed: 2026-07-15*

## Self-Check: PASSED

- FOUND: `.pre-commit-config.yaml`
- FOUND: `.planning/phases/12-structural-refactor-and-tooling/12-07-SUMMARY.md`
- FOUND: commit `a94e238` (Task 1)
- FOUND: commit `98ac8b1` (Task 2)
- FOUND: TOOL-01 and TOOL-02 marked Complete in `.planning/milestones/v1.1-paused/REQUIREMENTS.md` traceability table
