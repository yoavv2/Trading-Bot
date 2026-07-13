---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: Execution Correctness & Hardening
status: executing
stopped_at: Completed 08-04-PLAN.md
last_updated: "2026-07-13T06:37:00.000Z"
last_activity: "2026-07-13 — Phase 8 Wave 3 (08-04) complete. run_paper_order_submission is now lock-guarded end-to-end: session_run_lock() (08-02) wraps the whole side-effecting region, the StrategyRun row is created at status=RUNNING as the literal first persisted write (removed the pre-lock PENDING insert), and reclaim_stale_runs() (08-03) runs immediately after that row exists, before kill-switch/control-state checks. 3 new integration tests prove lock-loser-writes-nothing, running-row-first-with-stale-reclaim, and lock-released-after-kill-switch-block. Two atomic commits (13a6025, bd973a7); no deviations. Next: 08-05 (CLI exit-code mapping + crash/restart end-to-end proof)."
progress:
  total_phases: 5
  completed_phases: 4
  total_plans: 20
  completed_plans: 19
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-07-07)

**Core value:** Build a trustworthy, auditable trading platform that can reproducibly validate a strategy, run it in daily paper trading, and explain every action or blocked action without ambiguity.
**Current focus:** Milestone v1.1 Execution Correctness & Hardening resumed — Phase 8 (Concurrency Guard) in progress, Wave 1 (08-01, 08-02) + Wave 2 (08-03) + Wave 3 (08-04) complete, 08-05 next

## Current Position

Phase: 8 of 12 in v1.1 (Concurrency Guard) — resumed after v1.2 completion; Wave 1 (plans 01 and 02) + Wave 2 (plan 03) + Wave 3 (plan 04) complete
Plan: 08-04 complete (lock-guard + reorder run_paper_order_submission: session_run_lock() wraps the whole side-effecting region LOCK-02, running-row-first as the literal first persisted write LOCK-03, clean typed denial for the lock loser with zero writes LOCK-01, stale reclaim wired in immediately after the running row exists LOCK-05; commits 13a6025, bd973a7). Next: 08-05 (depends on 08-04 — CLI exit-code mapping to CONCURRENT_RUN_LOCK_EXIT_CODE + crash/restart end-to-end proof).
Status: Ready to execute 08-05
Last activity: 2026-07-13 — Phase 8 Wave 3 (08-04) complete. run_paper_order_submission is now lock-guarded end-to-end: session_run_lock() (08-02) wraps the whole side-effecting region, the StrategyRun row is created at status=RUNNING as the literal first persisted write (removed the pre-lock PENDING insert), and reclaim_stale_runs() (08-03) runs immediately after that row exists, before kill-switch/control-state checks. 3 new integration tests prove lock-loser-writes-nothing, running-row-first-with-stale-reclaim, and lock-released-after-kill-switch-block. Full repo suite (167 tests) has no regressions. No deviations — plan executed exactly as written.

Progress (phases across all milestones, v1.1 Phases 9-12 counted as paused/not-yet-executing): [██████░░░░] 11/16 phases complete (v1.0: 6, v1.1: 1 of 6 complete + Phase 8 now in progress (4/5 plans, Wave 1 + Wave 2 + Wave 3 done), v1.2: 4 of 4 complete)

## Performance Metrics

**Velocity:**
- Total plans completed: 30 (v1.0: 16, v1.1: 7, v1.2: 10)
- Average duration: ~7 min (v1.0); v1.1 Phase 7 ranged 3-138 min per plan, Phase 8-01: ~15 min, 08-02: ~15 min, 08-03: ~10 min, 08-04: ~25 min; v1.2 Phase 13-01: 6 min, 13-02: ~20 min, 13-03: 16 min, 13-04: 25 min, 14-02: 12 min, 14-03: ~10 min, 14-04: ~20 min, 15-01: ~20 min, 15-02: ~15 min, 15-03: single checkpoint session, 16-02: ~15 min, 16-01: ~9 min, 16-03: single checkpoint session
- Total execution time: -

**v1.0 By Phase:** 1: 3/3, 2: 3/3, 3: 3/3, 4: 2/2, 5: 3/3, 6: 3/3 — all complete

**v1.1 By Phase:** 7: 3/3 complete; 8: 4/5, Wave 1 + Wave 2 + Wave 3 complete (08-01: STALE enum + stale_run_timeout_minutes config foundation, LOCK-04; 08-02: advisory-lock primitive session_run_lock()/ConcurrentRunLockedError, LOCK-01/LOCK-06, concurrent Wave-1 executor; 08-03: find_stale_runs() single-query detector LOCK-04 + reclaim_stale_runs() tuple-scoped STALE marking with ExecutionEvent audit LOCK-05; 08-04: run_paper_order_submission reordered to lock-before-writes + running-row-first + reclaim-after-running-write, LOCK-01/02/03/05); 9-12: 0/TBD (paused, resume after Phase 8)

**v1.2 By Phase:** 13: 4/4 complete (01: kill-switch route, 02: console scaffold + proxy, 03: shared fetch client + kill-switch banner, 04: system status screen + operator sign-off), 14: 5/5 complete (14-01: Strategy overview screen + nav links; 14-02: Runs screen — filterable table + drill-down links; 14-03: Run detail shell + Signals/Risk Decisions + runScopedFilter/CappedDisclosure primitives; 14-04: OrdersFillsPanel + run-type-aware MetricsPanel; 14-05: operator live-verify checkpoint — approved, vv1 bug fixed live), 15: 3/3 complete (15-01: PaperAccountPanel + PaperReconciliationPanel + PaperAnalyticsSection + /paper route + nav link; 15-02: PositionsPanel (PAPR-01) + OpenOrdersPanel (PAPR-02) composed into /paper; 15-03: operator live-verify checkpoint — approved, all four PAPR surfaces honest-empty with Alpaca creds unconfigured), 16: 3/3 complete (16-02: EquityCurveChart (ANLX-01 frontend) + SummaryMetricsPanel (ANLX-02) + BacktestAnalyticsSection single-fetch owner, mounted on run-detail for backtest runs only, executed ahead of 16-01 per explicit human override; 16-01: backend equity_curve passthrough — single-line addition to StrategyAnalyticsService._summarize_backtest exposing the already-computed field, service-level pytest extended; 16-03: operator live-verify checkpoint — approved, all 6 steps passed against fresh servers, one in-scope YAxis auto-scale live-fix (dcd4232), ANLX-01 AND ANLX-02 confirmed Complete). Awaiting orchestrator phase-complete.

**Recent Trend:**
- Last activity: v1.2 Operator Console v0 shipped complete (Phases 13-16, 4/4); v1.1 resumed 2026-07-12 with Phase 8 (Concurrency Guard). Wave 1 (08-01, 08-02) + Wave 2 (08-03) + Wave 3 (08-04) complete — STALE enum + stale_run_timeout_minutes config foundation (LOCK-04), the advisory-lock primitive session_run_lock()/ConcurrentRunLockedError (LOCK-01, LOCK-06), stale-run detection/reclaim find_stale_runs()/reclaim_stale_runs() (LOCK-04, LOCK-05), and run_paper_order_submission wired to lock-before-writes + running-row-first + reclaim-after-running-write (LOCK-01/02/03/05); 08-05 (CLI exit-code mapping + crash/restart end-to-end proof) next.
- Trend: v1.1 resumed at Phase 8/12 after prioritizing and shipping the read-only operator console (v1.2)

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Init]: Scope v1.2 to a read-only console consuming the existing FastAPI read surface only — no new backend capabilities in this milestone.
- [07-03]: Persistent global kill switch (`system_controls` table) ships in Phase 7; `OperatorControlService`/`OperatorReadService` expose `get_kill_switch_state()`, but no HTTP route wires it yet (relevant to v1.2 Phase 13 — see ROADMAP.md Known Gaps #1).
- [v1.2-roadmap]: Phase 13 (Console Foundation & System Status) is first because the fetch/error/as-of pattern and kill-switch banner are shared infrastructure every later screen builds on.
- [v1.2-roadmap]: Phase 16 (Analytics & Charting) is last because it depends on the run-detail page/selection UX built in Phase 14.
- [v1.2-roadmap]: Two backend read-surface gaps found during roadmap creation and left unresolved for explicit operator/plan-phase decision rather than silently patched: (1) kill-switch state has no HTTP route (blocks STAT-03, KILL-01); (2) `equity_curve` is computed by `materialize_backtest_report()` but stripped by `StrategyAnalyticsService._summarize_backtest()` before reaching the wired analytics route (blocks ANLX-01). Detail in ROADMAP.md "Known Gaps (Backend Read-Surface)".
- [Phase 13]: Thin GET /api/v1/system/kill-switch route added as the only approved v1.2 backend change (STAT-03, KILL-01)
- [13-02]: create-next-app scaffold worked around npm's rejection of a package named "console" (core module name) by scaffolding to a temp dir and moving it into console/; package.json name field is operator-console, folder is unaffected
- [13-02]: Narrowed console/.gitignore's blanket `.env*` rule with `!.env.example` — the current create-next-app template ignores all .env* files including .env.example, which would have broken the CONS-01 requirement that .env.example be committed
- [13-03]: Added a narrowly-scoped, commented eslint-disable for react-hooks/set-state-in-effect in useApiQuery's mount effect rather than adding request-id/version state indirection, to keep the shared fetch hook a deliberate minimal instrument per plan scope
- [Phase 13]: 13-04: Extracted a scoped StatusPanel wrapper (title/FetchMeta/ErrorState chrome) reused by all five status panels; kept it local to the status screen per plan scope, not a shared lib component.
- [14-01]: This plan owns both new Phase 14 nav links (/strategy and /runs) in layout.tsx to avoid merge conflicts with parallel Wave-1 plans 14-02/14-03, even though the /runs route itself lands in 14-02.
- [14-01]: Strategy Overview screen composed directly from useApiQuery/FetchMeta/ErrorState (not the status-screen-scoped StatusPanel wrapper), per the 13-04 scoping decision; open-ended config dicts (indicators/exits/risk) render generically via Object.entries + JSON.stringify.
- [14-02]: RunsTable/RunFilters composed directly from useApiQuery/ErrorState/FetchMeta (not StatusPanel), consistent with the 13-04 scoping decision; RUNS-01 "created_at" satisfied by started_at (labeled "Started") with an inline honesty comment, since the runs serializer exposes no distinct created_at field.
- [Phase 14]: [14-03]: Run fetch owned by the run-detail page (not RunHeaderPanel) via a single useApiQuery call passed down as props, per the plan's explicit alternative — avoids double-fetching /api/v1/runs/{runId} and gates SignalsRiskPanel on a resolved strategy_id.
- [Phase 14]: [14-04]: MetricsPanel gates run-type at the render boundary (mounting a fetching child only for backtest/paper_execution) rather than conditionally calling useApiQuery inline, keeping every hook call unconditional per rules-of-hooks.
- [15-01]: PaperAnalyticsSection owns the single useApiQuery call to /api/v1/analytics/strategies/trend_following_daily and passes the `.paper` slice down to presentational PaperAccountPanel/PaperReconciliationPanel, per the 14-03 shared-fetch precedent; this plan also owns the Phase 15 nav edit (adds "Paper Trading" to layout.tsx) so 15-02 doesn't touch it.
- [15-01]: recent_execution_findings rendered under a heading that explicitly states its true scope ("strategy-wide, most-recent") rather than implying it belongs to the single latest reconciliation run, to satisfy the PAPR-03 honesty bar.
- [15-02]: PositionsPanel/OpenOrdersPanel deliberately do NOT import CappedDisclosure/runScopedFilter (run-detail-scoped) even though they solve the same disclosure problem as OrdersFillsPanel — inlined equivalent, paper-local hidden-count and 100-row-cap logic instead, per the plan's explicit scoping constraint; the empty-state branch always checks the cap first so a filtered-empty result under `count >= 100` never reads as a false definitive "none".
- [15-03]: Operator approved the live-verify checkpoint with all broker-backed data empty (Alpaca paper credentials unconfigured) — this is the checkpoint's designed condition, not a shortfall: the plan's data-availability note frames honest-empty-rendering as the primary thing this checkpoint proves. Populated-data rendering (non-empty account/positions/orders, hidden-row reveal controls, real >100-row truncation) remains live-unverified until Alpaca paper creds are configured; the same six verification steps re-confirm it then. No code change implied — Phase 15 is complete.
- [Phase 16]: 16-02: recharts installed with --save-exact to satisfy the exact-pin constraint; local AnalyticsResponse type defined in BacktestAnalyticsSection.tsx rather than importing MetricsPanel's, keeping MetricsPanel.tsx diff empty; new component tests use plain Vitest/Chai matchers instead of jest-dom (not in the mandated devDependency set); ResizeObserver mocked locally in EquityCurveChart.test.tsx for Recharts ResponsiveContainer under jsdom.
- [Phase 16]: 16-02 was executed out of dependency order ahead of 16-01 (`depends_on: ["16-01"]`) per explicit human override. 16-02's own frontmatter listed `requirements: [ANLX-01, ANLX-02]`, but only ANLX-02 was marked complete in REQUIREMENTS.md — ANLX-02's data (backtest.metrics) is already exposed by the wired analytics endpoint (same fields MetricsPanel/RUNS-06 already renders live), so the frontend delivered here fully satisfies it. ANLX-01 ("operator can view an equity curve chart") was deliberately left Pending: the frontend (EquityCurveChart, tested, honest not-available state) is done, but the backend `equity_curve` field 16-01 must add hasn't shipped, so no operator can actually view a populated chart yet. Marking ANLX-01 complete now would overclaim; it will be marked complete once 16-01 lands and 16-03 (operator live-verify checkpoint) confirms it end-to-end.
- [16-01]: Confirmed via direct read of `backtest_reporting.py:73-85` that `materialize_backtest_report()` already returns `equity_curve` before making the analytics.py change, rather than trusting the plan's interfaces block alone. Added exactly one line — `"equity_curve": report["equity_curve"],` — to `_summarize_backtest()`'s return dict; `git diff` confirmed no other change. Extended the existing seeded-backtest test with four assertions rather than adding a new test, reusing its DB/backtest fixture. ANLX-01 is still NOT marked complete in this plan despite being listed in its `requirements` frontmatter — per the 16-02 decision above, it requires the 16-03 operator live-verify checkpoint to confirm the chart renders real data end-to-end before it can be marked complete without overclaiming.
- [16-03]: Operator live-verified the analytics view end-to-end and approved — unlike the 15-03 broker-empty checkpoint, a real populated backtest run (6aee5ae6, non-flat 252-point curve, 4 trades) was present, so the ANLX-01 populated-chart path was live-exercised and ANLX-01 is marked Complete with NO data-availability caveat. ANLX-02 was already Complete from 16-02.
- [16-03]: A one-line in-scope rendering bug was fixed live during the checkpoint (step-6 allowance, 14-05 precedent) rather than deferred to a gaps plan — Recharts `YAxis` defaulted to `[0, max]`, flattening every equity curve so a real ~1.3% swing looked identical to a no-trade flat line; changed to `domain=['auto','auto']` + `allowDecimals={false}` (commit dcd4232, orchestrator-committed). Pattern for future financial charts: auto-scale the value axis to the data range so material swings are visible while genuinely constant series stay honestly flat.
- [16-03]: Executor found both the FastAPI backend (started 10:27, before the 16-01 code commit at 17:23) and the Next.js console dev server (started 00:16, before both 16-02 commits at 17:11–17:13) running stale relative to the code under verification; restarted both from clean state and programmatically re-verified (equity_curve present in live response; run-detail routes compile with no module errors) BEFORE presenting the checkpoint, so the operator was never handed a broken verification environment. No git-trackable change from restarts.
- [Phase 08]: [08-01]: Migration 0016 downgrade is an intentional documented no-op; stale_run_timeout_minutes added to ExecutionSafetySettings
- [Phase 08-02]: session_run_lock() uses an AUTOCOMMIT dedicated connection (no idle transaction) so crash-release depends purely on connection drop, not transaction rollback; the crash-release test uses a raw psycopg connection outside the SQLAlchemy pool to guarantee the backend session actually terminates.
- [08-03]: reclaim_stale_runs() flushes but never commits — caller (08-04's lock-guarded entrypoint) owns the transaction boundary. session_date tuple-scoping is done in Python against parameters_snapshot/result_summary's as_of_session JSON field rather than in SQL, since strategy_runs has no session_date column (only the STALE enum migration was authorized this phase). Implementation and tests were found already fully written but uncommitted at session start; correctness was independently re-verified against the plan's own task-level verify commands before splitting into two atomic task commits (9ae052d, 1a806c0).
- [08-04]: Extracted `_run_paper_order_submission_guarded()` as a separate module-level helper containing the whole lock-body rather than nesting ~350 lines under one `with session_run_lock(...)` block, to avoid a large re-indentation diff while preserving the same hard invariants (lock-before-writes, running-row-first, reclaim-after-running-write, durable independent commits, broker I/O outside any open transaction). `_create_paper_execution_run()` dropped its `strategy_status` parameter entirely, since strategy_status is genuinely unknown until after the lock+running-row-write+reclaim sequence completes (per the plan's explicit "do not load kill-switch/control state before the lock" ordering); no test or downstream consumer read `parameters_snapshot.strategy_status`, so this is a clean removal. `run_paper_session` and the `submit-paper-orders`/worker CLI entrypoints inherit the guard automatically (they call `run_paper_order_submission`) but do not yet catch `ConcurrentRunLockedError` — CLI exit-code mapping is explicitly deferred to 08-05, not a gap in this plan.

### Pending Todos

- None in the current milestone execution scope

### Blockers/Concerns

- BACKEND DATA-INTEGRITY (found 2026-07-09 during 14-05 live verification, deferred to a future backend phase): an `operator_control` `strategy_run` had `completed_at` (2026-07-08T17:47:49.391645+03:00) earlier than `started_at` (2026-07-08T17:47:49.468307+03:00). Not a console bug — `RunHeaderPanel` maps/renders both fields correctly; the read-only console honestly surfaces the backend's inverted timestamps. v1.2 authorizes no backend writes, so correcting the timestamp-generation ordering for `operator_control` runs is out of scope here. No blocker to Phase 14/15/16.
- RESOLVED 2026-07-07: both backend read-surface gaps approved as narrow exceptions — Phase 13 adds one thin GET route for `get_kill_switch_state()`; Phase 16 adds the existing `equity_curve` field to the analytics response. No other backend change authorized under this exception. Phase 16's exception was implemented in 16-01 (2026-07-09) as a single-line passthrough.
- `00-VERIFY` remains the gate for resuming v1.1 backend work (Phase 8+). It does NOT block v1.2 Operator Console read-only UI work, which consumes existing read endpoints only.
- The operator `.env` currently overrides the temporary app-boot test environment (`local` instead of expected `test`), so the focused baseline is not green.
- Polygon has a configured non-placeholder credential but has not completed an authorized read-only request in this verification pass.
- Alpaca paper credentials are not configured, so account, positions, and orders remain unverified with POPULATED data — the /paper screen's honest-empty rendering for all four surfaces WAS live-verified and approved in 15-03 (2026-07-09). Populated-data rendering (real balances/positions/orders, hidden-row reveal controls, >100-row truncation) remains unverified until Alpaca paper creds are configured.
- Docker daemon was unavailable during Phase 1 and 2-01 verification; local PostgreSQL@14 (Homebrew) used instead of Docker Compose.

## Session Continuity

Last session: 2026-07-13T06:37:00.000Z
Stopped at: Completed 08-04-PLAN.md
Resume file: None
