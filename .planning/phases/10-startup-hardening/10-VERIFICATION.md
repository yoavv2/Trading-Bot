---
phase: 10-startup-hardening
verified: 2026-07-13T20:25:47Z
status: human_needed
score: 8/8 must-haves verified (plan-level); 2 items need human judgment on phase-goal-level scope
human_verification:
  - test: "Run `python -m trading_platform.api` (the `trading-platform-api` console script registered in pyproject.toml) with an invalid config (e.g. an out-of-range tolerance in config.yaml) and observe the failure output."
    expected: "A judgment call is needed on whether this is an in-scope 'process entrypoint' for CFG-06/CFG-07. Today `api/app.py::main()` calls the ungated `load_settings()` before `uvicorn.run(...)`, so an invalid config crashes with a raw pydantic traceback (not the single actionable ConfigValidationError message) before the gated `lifespan()` is ever reached. The process still exits non-zero, but not via `enforce_startup_config`/CFG-07's actionable-message contract. Note: the actual Docker/docker-compose production path invokes `uvicorn trading_platform.api.app:app` directly and never calls `main()`, so this only affects the registered console-script entrypoint."
    why_human: "Whether the `trading-platform-api` console-script entrypoint counts as one of the 'every process entrypoint' the phase goal/CFG-06 must-have refers to (vs. only the Docker/uvicorn production path, which IS fully gated via `lifespan`) is a scope-interpretation question, not something resolvable by grep/test alone."
  - test: "Point the API at an unreachable database (e.g. a refused localhost port) and boot it via `uvicorn trading_platform.api.app:app` (the production path); observe whether it exits or serves."
    expected: "A judgment call is needed on whether CFG-04 ('unreachable DB = process exit before any domain code runs') should apply to the API surface. By design (`api/app.py` lifespan, `require_database=False`), the API boots successfully even when the DB is unreachable — it relies on its own `GET /ready` endpoint to report DB health dynamically instead of refusing to boot. This is a deliberate, documented deviation (10-05-SUMMARY.md, key-decisions) made to preserve a supported DB-less API boot mode exercised by `test_app_boot.py`, but it means CFG-04's literal wording ('process exit before any domain code runs') is not enforced for this one entrypoint — the API's request-serving routes ARE domain code that runs against an unreachable DB (each request fails individually rather than the process refusing to start)."
    why_human: "This is an architectural trade-off (fail-fast boot vs. always-available read surface with dynamic health reporting) that a human/product owner should explicitly bless or reject, not something with an objectively correct automated answer."
---

# Phase 10: Startup Hardening Verification Report

**Phase Goal:** The process refuses to boot on invalid config, logs never emit credentials or unmasked broker order IDs under default config, and one canonical DB connection lifecycle governs all execution flows.
**Verified:** 2026-07-13T20:25:47Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | An out-of-range/missing-secret/conflicting raw config payload raises a typed `ConfigValidationError` naming the failed field, not a raw pydantic error | ✓ VERIFIED | `src/trading_platform/core/config_validation.py` — `validate_config` wraps `Settings.model_validate` and translates `ValidationError`; `tests/test_config_validation.py` (12 tests, all pass) pins CFG-01/02/03/05/07 |
| 2 | Backtest mode with empty Alpaca keys boots successfully (empty-keys-still-boots invariant) | ✓ VERIFIED | `_semantic_failures` short-circuits when `mode is ExecutionMode.BACKTEST`; test-pinned |
| 3 | A missing secret, out-of-range tolerance, or unreachable DB causes a non-zero process exit with the single actionable message, before any domain service initializes (worker subcommands, bootstrap) | ✓ VERIFIED | `src/trading_platform/core/startup.py::enforce_startup_config`; wired at all 15 `worker/__main__.py` subcommands + `services/bootstrap.py::run_dry_bootstrap`; `tests/test_startup_validation.py` (9 tests incl. an ordering test) all pass |
| 4 | The API process (FastAPI `lifespan`) also refuses to boot on invalid config | ⚠️ PARTIAL | `lifespan()` calls `enforce_startup_config(mode=BACKTEST, require_database=False)` — config validation (CFG-01/02/03/05/07) IS enforced; DB-reachability (CFG-04) is explicitly SKIPPED for this entrypoint by design. See human_verification #2. |
| 5 | Every log payload is redacted of credentials/tokens/conn-URL passwords/Authorization headers before reaching the logger | ✓ VERIFIED | `src/trading_platform/core/log_sanitizer.py::sanitize`; `emit_structured_log` and `JsonLogFormatter.format` both route the full payload (context + message) through `sanitize`; `tests/test_log_sanitizer.py` (32 tests) + `tests/test_log_enforcement.py` (16 tests incl. message-string-specific regression) all pass |
| 6 | Broker order IDs mask to last-6 by default; full ID only under explicit debug-unmask flag | ✓ VERIFIED | `mask_order_id()`; `LoggingSettings.debug_unmask_ids` (default `False`); enforcement test proves both masked-default and unmask-reveals-full |
| 7 | Execution/reconciliation/config/control modules obtain loggers only via `get_logger`, not direct `logging.getLogger` | ✓ VERIFIED | `grep -rn "logging.getLogger"` across all 12 in-scope modules returns nothing; AST-based import-boundary test in `tests/test_log_enforcement.py` passes |
| 8 | One documented DB connection-lifecycle model governs engine/session access, with a single canonical import path and no competing caching mechanism | ✓ VERIFIED | `src/trading_platform/db/session.py` docstring ("Lifecycle model: EXPLICIT RELOADABLE MANAGER"); `db/__init__.py` re-exports only `Base`; `grep -rn lru_cache src/trading_platform/db/` empty; `.planning/PROJECT.md` Key Decisions row present; `tests/test_db_lifecycle.py` (AST scan + reloadability test) passes |
| 9 | Paper-order broker submission runs within explicit transaction boundaries; commit of broker success occurs only after both the broker call and the state-transition persist succeed | ✓ VERIFIED | `paper_execution.py` lines ~560-644, documented inline comment block; broker call sits outside any open session; success-persist `session_scope` wraps exactly the broker-result writes; test-pinned (`tests/test_paper_execution.py`, 25/25 pass) |
| 10 | A rollback occurring after the broker already accepted an order schedules a reconciliation hand-off (not silently swallowed) | ✓ VERIFIED | `schedule_reconciliation_after_partial_failure()` defined at `paper_execution.py:1584`, invoked at line 649 inside the post-broker `except` block, re-raises after scheduling; NOT called on the broker-call-failed path; test-pinned |

**Score:** 9/10 truths cleanly verified; 1 truth (#4, API DB-reachability refusal) is a documented partial requiring a human scope decision — see `human_verification`.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/trading_platform/core/config_validation.py` | `ExecutionMode`, `ConfigValidationError`, `validate_config` | ✓ VERIFIED | 169 lines, substantive, zero I/O confirmed (`grep` for `session_scope\|getLogger\|open(` empty) |
| `tests/test_config_validation.py` | unit tests per CFG invariant | ✓ VERIFIED | 187 lines, 12 tests, all pass |
| `src/trading_platform/core/log_sanitizer.py` | `sanitize`, `mask_order_id` | ✓ VERIFIED | 116 lines, dependency-free stdlib-only, all rules present |
| `src/trading_platform/core/logging.py` | `emit_structured_log` + formatter both sanitize | ✓ VERIFIED | `sanitize(` present at both `emit_structured_log` (context) and `JsonLogFormatter.format` (whole payload incl. message — closes the 10-02-deferred message-string gap) |
| `tests/test_log_sanitizer.py` | redaction + masking tests | ✓ VERIFIED | 222 lines, 32 tests, all pass |
| `src/trading_platform/db/session.py` | documented reloadable manager | ✓ VERIFIED | Lifecycle-model docstring present; keyed `(url, echo)` dict-cache; no `lru_cache` |
| `tests/test_db_lifecycle.py` | import-boundary + reloadability tests | ✓ VERIFIED | 114 lines, all pass |
| `.planning/PROJECT.md` | Key Decision entry | ✓ VERIFIED | Row present recording DB-01/DB-02 choice and rationale |
| `src/trading_platform/services/paper_execution.py` | explicit txn boundary + `schedule_reconciliation` | ✓ VERIFIED | Boundary comment + helper function present and wired |
| `tests/test_paper_execution.py` | commit-after-both / no-commit-on-failure / reconciliation-scheduled tests | ✓ VERIFIED | 1948 lines, 25/25 pass |
| `src/trading_platform/core/startup.py` | `enforce_startup_config` gate | ✓ VERIFIED | 73 lines; validates config then (conditionally) DB reachability; non-zero `SystemExit(78)` |
| `tests/test_startup_validation.py` | exit-on-failure + ordering tests | ✓ VERIFIED | 251 lines, 9 tests, all pass |
| `tests/test_log_enforcement.py` | import-boundary + emitted-line scan | ✓ VERIFIED | 256 lines, 16 tests, all pass |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `validate_config` | `Settings.model_validate` | pydantic-`ValidationError` translation | ✓ WIRED | Confirmed in source; CFG-05 reachable on real path |
| `enforce_startup_config` | `validate_config` + `check_database_connection` | sequential gate | ✓ WIRED | Confirmed in source |
| worker subcommands / API lifespan / bootstrap | `enforce_startup_config` | gate invoked before service construction | ✓ WIRED (worker/bootstrap) / ⚠️ PARTIAL (API — `require_database=False`) | `grep -n "enforce_startup_config"` shows 15 worker call sites + lifespan + bootstrap |
| `emit_structured_log` | `sanitize` | context dict routed through sanitizer | ✓ WIRED | Confirmed; also formatter-level backstop over full payload |
| `sanitize` | broker order id | last-6 mask unless debug-unmask | ✓ WIRED | `mask_order_id`, `ORDER_ID_KEY_PATTERN` confirmed |
| all engine/session consumers | `trading_platform.db.session` | single canonical import path | ✓ WIRED | AST-scan test confirms; `db/__init__.py` exports only `Base` |
| broker submit success | state-transition persist commit | `session_scope`, outside broker call | ✓ WIRED | Confirmed inline in `paper_execution.py` |
| post-broker persist rollback | `schedule_reconciliation_after_partial_failure` | except-block hand-off, re-raise | ✓ WIRED | Confirmed at `paper_execution.py:649` |
| execution/reconciliation/config path modules | `get_logger` | no direct `logging.getLogger` | ✓ WIRED | `grep` returns nothing across all 12 modules; AST import-boundary test passes |
| `JsonLogFormatter.format` | `sanitize` | formatter-level backstop | ✓ WIRED | Confirmed, covers message string (closes 10-02's deferred gap) |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| CFG-01 | 10-01 | required secrets per mode | ✓ SATISFIED | `_semantic_failures`, test-pinned |
| CFG-02 | 10-01 | cross-field: broker=alpaca requires key+secret; paper forbids live endpoint | ✓ SATISFIED | Same function, `base_url` check |
| CFG-03 | 10-01 | mutual exclusion paper vs. live | ✓ SATISFIED | Same function, bidirectional endpoint check |
| CFG-04 | 10-05 | unreachable DB = process exit before domain code | ⚠️ PARTIAL | Enforced at worker subcommands + bootstrap; explicitly skipped at API lifespan (`require_database=False`) — see human_verification #2 |
| CFG-05 | 10-01 | tolerance ranges vs. declared bounds | ✓ SATISFIED | pydantic `Field(ge/le)` translated to named `ConfigValidationError` |
| CFG-06 | 10-05 | all validation before service init; one failure prevents init | ✓ SATISFIED (worker/bootstrap/API-lifespan) / ⚠️ CAVEAT (`main()` console script bypasses the gate via ungated `load_settings()`) | See human_verification #1 |
| CFG-07 | 10-01 | single actionable message naming field + shape | ✓ SATISFIED | `ConfigValidationError.__str__`; caveat for `main()` path (raw pydantic traceback, not the wrapped message) |
| LOG-01 | 10-06 | one logger wrapper only in execution/reconciliation/config paths | ✓ SATISFIED | AST-based import-boundary test, 12 modules |
| LOG-02 | 10-02 | every payload passes through sanitizer | ✓ SATISFIED | `emit_structured_log` + formatter backstop |
| LOG-03 | 10-02 | redacts credentials/keys/tokens/conn-URLs | ✓ SATISFIED | `sanitize()`, test-pinned |
| LOG-04 | 10-02 | redacts Authorization/auth headers | ✓ SATISFIED | `_BEARER_TOKEN_PATTERN`, test-pinned |
| LOG-05 | 10-02 | broker order IDs truncated to last-6, full only behind debug flag | ✓ SATISFIED | `mask_order_id`, test-pinned |
| LOG-06 | 10-06 | enforcement tests assert no leaked line under default config | ✓ SATISFIED | `tests/test_log_enforcement.py`, 16 tests incl. message-string regression |
| DB-01 | 10-03 | one documented lifecycle model, recorded as Key Decision | ✓ SATISFIED | docstring + PROJECT.md row |
| DB-02 | 10-03 | lru_cache vs dict-cache duality resolved | ✓ SATISFIED | `grep lru_cache` empty in `db/` |
| DB-03 | 10-03 | one canonical import path | ✓ SATISFIED | `db/__init__.py` exports only `Base`; AST-scan test |
| DB-04 | 10-04 | explicit transaction boundary on every execution flow | ✓ SATISFIED | `paper_execution.py` (the only broker-side-effect flow), test-pinned |
| DB-05 | 10-04 | commit only after broker success AND state persisted | ✓ SATISFIED | Same, test-pinned |
| DB-06 | 10-04 | rollback after broker effect schedules reconciliation | ✓ SATISFIED | `schedule_reconciliation_after_partial_failure`, test-pinned |

No orphaned requirements — REQUIREMENTS.md's Phase 10 requirement-map rows (lines 187-205) match exactly the union of `requirements:` frontmatter across all six 10-0N-PLAN.md files (CFG-01..07, LOG-01..06, DB-01..06 = 19 IDs, all present in both).

### Anti-Patterns Found

None. `grep -n -E "TODO|FIXME|XXX|HACK|PLACEHOLDER"` across all phase-10-created/modified core files returns nothing. No stub returns (`return null`, `return {}`, empty handlers) found in the reviewed modules — all implementations are substantive and test-backed.

### Human Verification Required

### 1. `trading-platform-api` console-script entrypoint bypasses the startup gate

**Test:** Run `python -m trading_platform.api` (or the installed `trading-platform-api` console script) with an invalid config (e.g. `risk_per_trade: 2.0` in config.yaml).
**Expected:** Judgment needed — today `api/app.py::main()` calls the ungated `load_settings()` before `uvicorn.run(...)`, producing a raw pydantic traceback rather than `enforce_startup_config`'s single actionable message, before the gated `lifespan()` ever runs. The process still exits non-zero (Python's default unhandled-exception behavior), so it technically "refuses to boot," but not via the CFG-07 actionable-message contract. Note: the Docker/docker-compose production path invokes `uvicorn trading_platform.api.app:app` directly and never calls `main()`, so production deployment is unaffected — only the registered `trading-platform-api` console script is.
**Why human:** Whether this console-script entrypoint counts as one of the "every process entrypoint" instances the CFG-06/CFG-07 must-haves require gating is a scope-interpretation call, not resolvable by code inspection alone. (This was self-flagged as an open issue in `10-05-SUMMARY.md`'s "Issues Encountered" section.)

### 2. API lifespan explicitly skips the DB-reachability preflight (CFG-04)

**Test:** Point the API's configured database at an unreachable host/port and boot it via the production path (`uvicorn trading_platform.api.app:app`).
**Expected:** Judgment needed — by design, the API boots successfully regardless of DB reachability (`require_database=False` at the lifespan call site) and instead reports DB health dynamically via `GET /ready`. This is a deliberate, documented trade-off (preserves a DB-less API boot mode exercised by `test_app_boot.py`), but it is a literal deviation from CFG-04's unqualified wording ("unreachable DB = process exit before any domain code runs") for this one entrypoint — API routes ARE domain code, and they will run (and fail per-request) against an unreachable DB rather than the process refusing to start.
**Why human:** This is an architectural trade-off (fail-fast-on-boot vs. always-available read surface with dynamic health reporting) requiring a product/architecture decision, not an objectively resolvable correctness question.

### Gaps Summary

No blocking gaps. All 19 phase-10 requirement IDs have concrete, test-backed implementations in the codebase — verified by reading source (not trusting SUMMARY claims), running the full relevant test suites (72 dedicated unit tests + 25 paper-execution tests, all passing), confirming wiring via grep/AST-boundary tests, and confirming zero orphaned requirements against REQUIREMENTS.md.

The worker CLI (15 subcommands), the dry-bootstrap flow, and the Docker/docker-compose production API boot path (`uvicorn trading_platform.api.app:app`) are all fully gated and refuse to boot on invalid config or (for worker/bootstrap) unreachable DB, with non-zero exit and a single actionable message. The log-sanitization chokepoint (context + message, via both `emit_structured_log` and a formatter-level backstop) and the DB connection-lifecycle consolidation (one documented model, one import path, no competing cache) are both fully verified with no caveats.

The two items requiring human judgment are narrow and specific to the API surface: (1) the registered `trading-platform-api` console-script entrypoint's `main()` function calls `load_settings()` before `uvicorn.run`, bypassing the gate's actionable-message contract (though the actual Docker production path is unaffected since it invokes `uvicorn` directly against the `app` object); (2) the API's `lifespan()` deliberately opts out of the CFG-04 DB-reachability preflight in favor of a dynamic `/ready` health check, a documented design trade-off rather than an oversight. Both were self-flagged in the 10-05 plan's own SUMMARY.md and independently confirmed here by reading `api/app.py` directly. Neither affects the worker/paper-execution/DB-lifecycle/logging tracks, which are unconditionally verified.

---

*Verified: 2026-07-13T20:25:47Z*
*Verifier: Claude (gsd-verifier)*
