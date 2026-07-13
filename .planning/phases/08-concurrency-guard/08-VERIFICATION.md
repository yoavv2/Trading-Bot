---
phase: 08-concurrency-guard
verified: 2026-07-13T07:37:49Z
status: passed
score: 15/15 must-haves verified
---

# Phase 8: Concurrency Guard Verification Report

**Phase Goal:** At most one active run per `(strategy_id, session_date)` can execute side effects; the lock is acquired before any broker call or state-affecting write, released on all exit paths including crash, and stale runs are detectable and cleanly handled.
**Verified:** 2026-07-13T07:37:49Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

Sourced from ROADMAP.md Success Criteria (1–4) and the union of `must_haves.truths` declared across the five plan frontmatters (5–15).

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A second process attempting to start the same `(strategy_id, session_date)` run while the first holds the advisory lock exits cleanly with a typed message — no broker calls or DB writes occur before the lock is confirmed. | ✓ VERIFIED | `session_run_lock()` raises `ConcurrentRunLockedError` before `yield` (concurrency_guard.py:87-100); `run_paper_order_submission` wraps its whole guarded body inside the lock's `with` (paper_execution.py:229-245); `test_run_paper_order_submission_loser_writes_nothing_and_makes_no_broker_calls` (test_paper_execution.py:1546) asserts zero new `StrategyRun`/`PaperOrder` rows and empty `submitted_intents`. Passes. |
| 2 | A run that holds the lock writes `run_status=running` and `run_started_at` as its first persisted action; a single query can identify any run past the declared heartbeat/timeout threshold as stale. | ✓ VERIFIED | `_create_paper_execution_run` inserts `status=StrategyRunStatus.RUNNING` directly (paper_execution.py:1302), called as the first statement of `_run_paper_order_submission_guarded` (paper_execution.py:281), before `reclaim_stale_runs` or any kill-switch load. `find_stale_runs()` is one `select(...)` query filtering `status=RUNNING, run_type=PAPER_EXECUTION, started_at < cutoff` (stale_runs.py:28-41), tested by `test_find_stale_runs_detects_only_running_past_timeout`. |
| 3 | When the lock is free but a stale `running` row exists, the new run marks that row `stale` and continues; it does not silently overwrite or ignore it. | ✓ VERIFIED | `reclaim_stale_runs()` flips every matching row to `STALE` and inserts one `ExecutionEvent` (`event_type="paper_run_reclaimed_stale"`) per row, never a bare overwrite (stale_runs.py:44-115); called immediately after the running-row write in `_run_paper_order_submission_guarded` (paper_execution.py:296-303). `test_run_paper_order_submission_running_row_first_and_reclaims_stale_predecessor` proves the 40-min-old predecessor becomes STALE with exactly one audit event while the fresh run's own row (inside the window) is untouched and reaches SUCCEEDED. |
| 4 | A restart/crash test confirms the session-scoped advisory lock is released automatically on crash, and a subsequent run can acquire it cleanly without manual intervention. | ✓ VERIFIED | `session_run_lock()` holds the lock on a dedicated AUTOCOMMIT connection; PostgreSQL auto-releases session-level advisory locks when that connection drops (documented in module docstring, concurrency_guard.py:1-12). `test_session_run_lock_acquires_cleanly_after_holder_connection_drops` (unit-primitive level) and `test_run_paper_order_submission_acquires_cleanly_after_crash_and_reclaims_stale_predecessor` (test_concurrency_guard_e2e.py:298) both force-close a raw, non-pooled connection that holds the lock without unlocking, then prove a fresh `run_paper_order_submission()` call acquires cleanly, reclaims the leftover running row to STALE, and reaches SUCCEEDED. |
| 5 (LOCK-01 mechanism) | Advisory lock is keyed on `(strategy_id, session_date)` via a deterministic BIGINT-safe hash. | ✓ VERIFIED | `advisory_lock_key()` (concurrency_guard.py:36-48) — blake2b digest of canonical string, signed big-endian int; `test_deterministic_for_same_inputs`, `test_varies_by_session_date`, `test_fits_signed_bigint_range` pass. |
| 6 (LOCK-04) | `STALE` is a valid closed-enum value in Python and the PostgreSQL enum after migration to head. | ✓ VERIFIED | `StrategyRunStatus.STALE = "stale"` (strategy_run.py:39); migration `0016_phase8_stale_run_status` chains from `0015_phase7_kill_switch` and resolves to a single head (`0016_phase8_stale_run_status`) confirmed via `ScriptDirectory.get_current_head()`; `test_stale_status_round_trips_against_migrated_db` inserts/reads a STALE row against a freshly-migrated temp DB. |
| 7 (LOCK-04) | Stale timeout is read from `execution.safety.stale_run_timeout_minutes` config (default 30), not hardcoded. | ✓ VERIFIED | `ExecutionSafetySettings.stale_run_timeout_minutes: int = Field(default=30, ge=1)` (settings.py:245); consumed via `resolved_settings.execution.safety.stale_run_timeout_minutes` at the `reclaim_stale_runs()` call site (paper_execution.py:301); `test_stale_run_timeout_minutes_defaults_to_30` and `test_stale_run_timeout_minutes_env_override` pass. |
| 8 (LOCK-06) | Lock is released on the kill-switch-blocked exit path, not just the happy path. | ✓ VERIFIED | `test_run_paper_order_submission_kill_switch_blocks_after_lock_and_releases_lock_on_exit` (test_paper_execution.py:1648) blocks a run via kill switch, then acquires `session_run_lock()` again on the same tuple and asserts it succeeds (no `ConcurrentRunLockedError`), proving release on this exit path. |
| 9 (LOCK-01 CLI) | Worker CLI commands exit with a dedicated non-zero code and no traceback under lock contention; still exit 0 on success. | ✓ VERIFIED | `worker/__main__.py` imports `ConcurrentRunLockedError`/`CONCURRENT_RUN_LOCK_EXIT_CODE`; both `run_submit_paper_orders_command` and `run_paper_session_command` catch the exception via shared `_handle_concurrent_run_lock_denied()` and `raise SystemExit(CONCURRENT_RUN_LOCK_EXIT_CODE)` (worker/__main__.py:352-435). `test_submit_paper_orders_exits_with_reserved_exit_code_and_no_side_effects_when_lock_held` asserts `SystemExit.code == 3` and zero DB writes. |

**Score:** 15/15 truths verified (all four ROADMAP success criteria + all cross-cutting plan-level must-haves).

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/trading_platform/db/models/strategy_run.py` | `STALE` member on `StrategyRunStatus` | ✓ VERIFIED | `STALE = "stale"` present, line 39; picked up automatically by the existing `values_callable`/`validate_strings` column mapping. |
| `alembic/versions/0016_phase8_stale_run_status.py` | PG enum migration adding `'stale'` | ✓ VERIFIED | `ALTER TYPE strategy_run_status ADD VALUE IF NOT EXISTS 'stale'`; chains from `0015_phase7_kill_switch`; documented no-op downgrade; head resolves cleanly to a single linear chain. |
| `src/trading_platform/core/settings.py` | `stale_run_timeout_minutes` on `ExecutionSafetySettings` | ✓ VERIFIED | `Field(default=30, ge=1)`, line 245, with explanatory docstring. |
| `src/trading_platform/services/concurrency_guard.py` | `ConcurrentRunLockedError`, `advisory_lock_key()`, `session_run_lock()`, `CONCURRENT_RUN_LOCK_EXIT_CODE` | ✓ VERIFIED | All four exports present (106 lines); non-blocking `pg_try_advisory_lock`/`pg_advisory_unlock` on a dedicated AUTOCOMMIT connection; imported and used by `paper_execution.py` and `worker/__main__.py`. |
| `src/trading_platform/services/stale_runs.py` | `find_stale_runs()`, `reclaim_stale_runs()` | ✓ VERIFIED | Both present (116 lines); single-query detector + tuple-scoped audited reclaim; imported and called by `paper_execution.py`. |
| `src/trading_platform/services/paper_execution.py` | Lock-guarded, reordered `run_paper_order_submission` | ✓ VERIFIED | Lock acquired first (line 230), running-row-first write (line 281), reclaim immediately after (line 296-303), kill-switch/control state loaded only after that (line 305-314). |
| `src/trading_platform/worker/__main__.py` | `ConcurrentRunLockedError` → reserved exit code, both paper commands | ✓ VERIFIED | Shared `_handle_concurrent_run_lock_denied()` helper used by `submit-paper-orders` (line 399-400) and `run-paper-session` (line 434-435). |
| `tests/test_stale_run_config.py`, `tests/test_concurrency_guard.py`, `tests/test_stale_run_reclaim.py`, `tests/test_paper_execution.py` (concurrency tests), `tests/test_concurrency_guard_e2e.py` | Integration tests against real Postgres | ✓ VERIFIED | All present; 38 Phase-8-specific tests pass, plus the 3 concurrency tests embedded in `test_paper_execution.py`; full repo suite (169 tests) passes with no regressions, run live against a local PostgreSQL@14 instance. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `ExecutionSafetySettings.stale_run_timeout_minutes` | `reclaim_stale_runs()` call site | `resolved_settings.execution.safety.stale_run_timeout_minutes` | ✓ WIRED | Confirmed at paper_execution.py:301. |
| `session_run_lock()` | `pg_try_advisory_lock` / `pg_advisory_unlock` | dedicated AUTOCOMMIT connection | ✓ WIRED | concurrency_guard.py:88-104; proven by contention + release-on-exit + crash-release tests. |
| `reclaim_stale_runs()` | `StrategyRun.status = STALE` + `ExecutionEvent` audit row | single query + in-place update + `session.add(ExecutionEvent(...))` | ✓ WIRED | stale_runs.py:89-114; proven with exact-count audit-row assertions in `test_reclaim_stale_runs_marks_all_past_threshold_rows_stale_with_audit`. |
| `run_paper_order_submission()` | `session_run_lock()` + `reclaim_stale_runs()` | `with session_run_lock(...)` wrapping the guarded region; running-row-first, then reclaim | ✓ WIRED | paper_execution.py:230-303; ordering verified by direct code read and by the `running_row_first_and_reclaims_stale_predecessor` test. |
| guarded region entry | `StrategyRun status=running` as first persisted write | direct RUNNING insert replacing the pre-lock PENDING insert | ✓ WIRED | `_create_paper_execution_run` inserts `status=StrategyRunStatus.RUNNING` directly (paper_execution.py:1302); no pre-lock PENDING insert remains in the file (grep confirms only the RUNNING literal at creation). |
| `run_submit_paper_orders_command` / `run_paper_session_command` | `SystemExit(CONCURRENT_RUN_LOCK_EXIT_CODE)` | `except ConcurrentRunLockedError` → log WARNING → `raise SystemExit(code)` | ✓ WIRED | worker/__main__.py:399-400, 434-435; proven by `test_submit_paper_orders_exits_with_reserved_exit_code_and_no_side_effects_when_lock_held`. |

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|-----------------|-------------|--------|----------|
| LOCK-01 | 08-02, 08-04, 08-05 | At most one active run per tuple, enforced by a PG advisory lock | ✓ SATISFIED | `session_run_lock()` + `ConcurrentRunLockedError`; loser-writes-nothing test; CLI exit-code test. |
| LOCK-02 | 08-04 | Lock acquired BEFORE any side effect | ✓ SATISFIED | Lock `with` block wraps the entire guarded body; no write/broker call precedes it (verified by code read + loser-writes-zero-rows test). |
| LOCK-03 | 08-04 | First persisted write after lock acquisition is `run_started_at` + `run_status=running` | ✓ SATISFIED | `_create_paper_execution_run` inserts `status=RUNNING` directly as the guarded body's first statement; `started_at` has a server-default `now()`. |
| LOCK-04 | 08-01, 08-03 | Stale run detectable via a single query | ✓ SATISFIED | `find_stale_runs()` one-query detector; STALE enum + config foundation from 08-01. |
| LOCK-05 | 08-03, 08-04 | Lock held → typed clean exit; lock free + stale row → mark stale and continue | ✓ SATISFIED | `ConcurrentRunLockedError` (typed exit) + `reclaim_stale_runs()` (mark-stale-and-continue), both wired into `run_paper_order_submission`. |
| LOCK-06 | 08-02, 08-05 | Lock release guaranteed on normal exit, crash, and kill-switch trigger — verified by restart/crash test | ✓ SATISFIED | Crash-release proven at both the primitive level (08-02) and end-to-end with a real submission call (08-05); kill-switch release proven in 08-04's test. |

No orphaned requirements: all six `LOCK-*` IDs declared in REQUIREMENTS.md Phase 8 section are claimed by at least one plan's frontmatter `requirements` field, and all six are marked `[x]` / `Complete` in REQUIREMENTS.md.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `.planning/ROADMAP.md` | 87 | Plan checkbox `- [ ] 08-05-PLAN.md` left unchecked while Phase 8 heading (line 49) and all other Phase 8 plan checkboxes (08-01..08-04) show `[x]`, and `08-05-SUMMARY.md` documents completion with verified commits | ℹ️ Info (doc-sync only) | No functional impact — code, tests, and REQUIREMENTS.md are all consistent and complete. This is a documentation bookkeeping gap only (roadmap CLI column-shift pattern noted in project memory); recommend running `roadmap update-plan-progress` for 08-05 or hand-correcting the checkbox before closing the phase in tracking docs. |

No blocker or warning-level anti-patterns found in the Phase 8 source files themselves (no TODO/FIXME/stub returns/empty handlers in `concurrency_guard.py`, `stale_runs.py`, the modified regions of `paper_execution.py`, or the modified regions of `worker/__main__.py`). The `placeholder` hits found in `worker/__main__.py` (`run_placeholder_worker`, the `serve` subcommand) are pre-existing, unrelated code outside Phase 8's scope.

### Human Verification Required

None. Every observable truth and key link is proven by an automated integration test executed live against a real PostgreSQL instance (38 Phase-8-specific tests + full 169-test repo suite, all passing, run during this verification). No visual, real-time, or external-service-dependent behavior is in scope for this phase.

### Gaps Summary

No gaps. All 4 ROADMAP success criteria and all 6 LOCK-01..06 requirements are backed by code that matches the plan text exactly, wired end-to-end, and proven by integration tests run live during this verification (not merely trusted from SUMMARY claims). The only finding is a documentation bookkeeping inconsistency in ROADMAP.md (08-05 plan checkbox unchecked) with no bearing on the phase's functional goal achievement.

---

*Verified: 2026-07-13T07:37:49Z*
*Verifier: Claude (gsd-verifier)*
