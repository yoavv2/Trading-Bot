---
phase: 16-analytics-and-charting
plan: 03
subsystem: ui
tags: [nextjs, react, typescript, recharts, operator-verification, sign-off, analytics, charting]

# Dependency graph
requires:
  - phase: 16-01
    provides: "Backend equity_curve passthrough — StrategyAnalyticsService._summarize_backtest now serializes report['equity_curve'] into the analytics backtest block (ANLX-01 backend)"
  - phase: 16-02
    provides: "EquityCurveChart (ANLX-01 frontend) + SummaryMetricsPanel (ANLX-02) + BacktestAnalyticsSection single-fetch owner, mounted on run-detail for backtest runs only"
provides:
  - "Live operator sign-off that a backtest run's detail page renders a Recharts equity curve (total_equity over session_date) and a labeled summary-metrics panel whose values (Sharpe, max drawdown, win rate, total return %, trade count) agree with the raw RUNS-06 Metrics panel — end-to-end against a running FastAPI backend"
  - "Confirmation that a run with an empty equity_curve renders an honest 'not available' state, a non-backtest run mounts no analytics section, and an API-down analytics fetch renders an endpoint-named ErrorState with correct recovery"
  - "ANLX-01 and ANLX-02 marked complete — the equity chart is live-verified to render real, visually-assessable performance data"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Recharts YAxis for financial equity series uses domain=['auto','auto'] (not the default [0,max]) so intra-range variation is visible rather than flattened against a zero baseline"

key-files:
  created: []
  modified:
    - "console/src/components/runs/detail/EquityCurveChart.tsx — YAxis auto-scaling live-fix (dcd4232)"

key-decisions:
  - "Operator approved after exercising all six verification steps against live data with a real populated backtest run present (run 6aee5ae6, 252-point curve, 4 trades) — unlike the 15-03 broker-empty precedent, the populated-chart path was live-exercised here, so ANLX-01 is marked complete without a data-availability caveat."
  - "A single in-scope one-line rendering bug (EquityCurveChart YAxis flattening all curves against a [0,max] baseline) was fixed live during verification per the checkpoint's step-6 allowance, mirroring the 14-05 precedent, rather than deferred to a gaps plan."

patterns-established:
  - "Pattern: financial time-series charts auto-scale the value axis to the data range so small-but-material swings (e.g. ~1.3%) are visually distinguishable from genuinely flat/no-trade series."

requirements-completed: [ANLX-01, ANLX-02]

# Metrics
duration: single checkpoint session
completed: 2026-07-09
---

# Phase 16 Plan 03: Operator Live-Verify Checkpoint Summary

**Operator live-verified the Phase 16 analytics view end-to-end against a running FastAPI backend and console dev server and approved — a backtest run's detail page renders a Recharts equity curve and labeled Sharpe/max-drawdown/win-rate/total-return/trade-count metrics agreeing with the raw metrics table, empty curves and non-backtest runs degrade honestly, and the section fails to an endpoint-named ErrorState on API-down; a one-line YAxis auto-scaling bug was caught and fixed live.**

## Performance

- **Duration:** single checkpoint session (verified same day the plan was created)
- **Completed:** 2026-07-09
- **Tasks:** 1 (checkpoint:human-verify)
- **Files modified:** 1 (live-fix to EquityCurveChart.tsx; verification otherwise involved no code changes)

## Accomplishments

- **Step 1 (navigation + FetchMeta):** Operator confirmed the analytics section appears below the existing run panels on a backtest run's detail page, with its own "as of …" FetchMeta timestamp and a Refresh control that advances the timestamp on press. PASS.
- **Step 2 (equity curve, ANLX-01):** Operator confirmed a real Recharts line chart renders `total_equity` over `session_date` for a traded run (6aee5ae6) — axes, hover tooltip, visible variation — after the live YAxis fix below. PASS.
- **Step 2b (honest not-available):** Operator confirmed a run with an empty `equity_curve` (2bfab8b4) renders the explicit "equity curve not available" state rather than a blank or broken chart. PASS.
- **Step 3 (summary metrics, ANLX-02):** Operator confirmed all five labeled figures render — Sharpe -0.298, Max drawdown -1.27%, Win rate 25%, Total return -0.28%, Trade count 4 (run 6aee5ae6) — and agree with the raw RUNS-06 Metrics panel on the same page. PASS.
- **Step 4 (backtest-only gating):** Operator confirmed an `operator_control` run (b683ef53) mounts NO equity-curve/analytics section. PASS.
- **Step 5 (honest API-down failure):** Operator stopped the FastAPI backend and refreshed the analytics section; it rendered an ErrorState naming `/api/v1/analytics/strategies/trend_following_daily?...` with HTTP 500 — never a blank or fake-success render. Restarting the backend and refreshing confirmed full recovery. PASS.
- All six verification steps passed. Operator responded "approved." ANLX-01 and ANLX-02 are now live-verified end-to-end.

## Pre-Checkpoint Environment Prep (automation, not a plan task)

Both servers were already running but were **stale relative to the 16-01/16-02 code**, which would have produced a broken/misleading verification. Fixed before presenting the checkpoint (deviation Rule 3 — blocking-issue auto-fix; no git-trackable change, so no commit):

1. **Backend (port 8000):** process had started (10:27) before the 16-01 code commit (68151c4, 17:23) that adds `equity_curve`; confirmed the field was genuinely absent from a live response. Killed and restarted from the project `.venv`; re-verified `equity_curve` present and populated (6-point and 252-point curves observed on real runs).
2. **Console dev server (port 3000):** Next.js process had started (00:16) before both 16-02 commits (666d877 recharts install + EquityCurveChart, 9669fad SummaryMetricsPanel/BacktestAnalyticsSection at 17:11–17:13) — a newly-installed dependency (recharts) into a running dev server is a classic stale-module-resolution risk. Killed and restarted; re-verified by curling the three verification-relevant run-detail routes (all HTTP 200, no "Module not found"/compile errors in the dev log).

## Task Commits

1. **Task 1: Operator verifies the analytics view end-to-end** — checkpoint (human-verify). Operator responded "approved" after completing steps 1-6 against live data. One in-scope live fix applied (see below), committed by the orchestrator.

**Live-fix commit (orchestrator-committed during verify):** `dcd4232` fix(16-03): auto-scale equity-curve Y-axis so real variation is visible

**Plan metadata:** (this commit, docs)

## Files Created/Modified

- `console/src/components/runs/detail/EquityCurveChart.tsx` — YAxis changed from the default `[0, max]` domain (which flattened every curve against a zero baseline) to `domain={["auto","auto"]}` + `allowDecimals={false}`, committed as `dcd4232`.

## Decisions Made

- Treated the presence of a real populated backtest run (6aee5ae6) as sufficient to live-exercise the ANLX-01 populated-chart path, so ANLX-01 is marked complete with no data-availability caveat — unlike the 15-03 broker-empty checkpoint where populated rendering remained unverified.
- The one-line YAxis rendering bug was fixed live during verification (step-6 in-scope allowance, 14-05 precedent) rather than deferred to a gaps plan, because it was a single directly-in-scope rendering line, not a structural change.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] EquityCurveChart YAxis flattened every equity curve**
- **Found during:** Task 1 (operator live-verify, step 2 / ANLX-01)
- **Issue:** The Recharts `YAxis` defaulted to a `[0, max]` domain, so a run swinging 99.7k–101k (~1.3%) rendered as a visually flat line indistinguishable from a genuine no-trade constant curve — defeating ANLX-01's "visually assess performance" goal.
- **Fix:** Set `domain={["auto","auto"]}` (axis fits the data range) plus `allowDecimals={false}`. Operator re-verified: the traded run (6aee5ae6) now shows visible variation, while a no-trade run (166e2739, 252 points all == 100000.0, trade_count 0) correctly stays flat (honest constant data).
- **Files modified:** console/src/components/runs/detail/EquityCurveChart.tsx
- **Verification:** Operator re-verified live; `npm run test` (19/19), `npm run lint`, and `npm run build` all green after the fix.
- **Committed in:** dcd4232 (orchestrator-committed during verify — not re-committed or reverted here)

---

**Total deviations:** 1 auto-fixed (1 in-scope rendering bug, live-fixed per checkpoint step-6). Plus pre-checkpoint environment prep (two stale-server restarts, no code change).
**Impact on plan:** The YAxis fix was necessary for ANLX-01's core purpose (visual performance assessment). No scope creep — a single directly-in-scope rendering line.

## Issues Encountered

- Both the backend and console dev servers were running from before the 16-01/16-02 code landed and would have served stale behavior (missing `equity_curve` field; potential stale recharts module resolution). Diagnosed by comparing process start times to commit timestamps and restarted both, then programmatically re-verified before presenting the checkpoint. See Pre-Checkpoint Environment Prep above.

## User Setup Required

None. Unlike the 15-03 Alpaca-credentials caveat, this checkpoint live-exercised the populated path — a real backtest run with a non-flat equity curve and computed metrics was present, so no data-availability follow-up is outstanding for ANLX-01/ANLX-02.

## Next Phase Readiness

- Phase 16 (Analytics & Charting) is functionally complete: ANLX-01 (equity curve chart) and ANLX-02 (summary metrics) are both operator-confirmed end-to-end, non-backtest gating and honest not-available/error states hold, and the raw RUNS-06 metrics panel is unchanged.
- Phase 16 and the v1.2 "Operator Console v0" milestone are ready to be marked complete by the orchestrator (this plan does not run `phase complete`).

---
*Phase: 16-analytics-and-charting*
*Completed: 2026-07-09*

## Self-Check: PASSED

- `16-03-SUMMARY.md` verified present on disk.
- Live-fix commit `dcd4232` verified present in git log (orchestrator-committed during verify; not re-committed here).
- REQUIREMENTS.md: ANLX-01 checkbox `[x]` + traceability row updated to Complete; ANLX-02 already Complete; stale "Known gaps" note updated to "resolved".
- STATE.md and ROADMAP.md updated (Phase 16 → 3/3, Complete, 2026-07-09; ROADMAP row column-shift from the known CLI bug corrected to `| 16. Analytics & Charting | v1.2 | 3/3 | Complete | 2026-07-09 |`).
