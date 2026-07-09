# Phase 16: Analytics & Charting - Context

**Gathered:** 2026-07-09
**Status:** Ready for planning
**Source:** Targeted decision capture (charting library lock) — no full discuss-phase run; siblings 13/14/15 planned without CONTEXT/RESEARCH and this phase is otherwise fully specified by ROADMAP success criteria + Known Gaps #2 resolution.

<domain>
## Phase Boundary

Delivers the analytics view: an equity-curve chart and standard summary statistics for a selected backtest run. Depends on Phase 14's run-detail page / run-selection UX (already verified in 14-05). Front-end only, plus ONE authorized backend serialization change (Known Gaps #2 narrow exception).

In scope:
- Equity curve chart for a selected backtest run (ANLX-01).
- Summary metrics panel for the run: Sharpe, max drawdown, win rate, P&L, trade count (ANLX-02).
- The one approved backend change: serialize the already-computed `equity_curve` field into the existing analytics response.

Out of scope:
- Any other new backend capability, route, or computation (milestone rule: "no new backend capabilities"; only the Known Gaps #2 exception is authorized).
- Charting on non-backtest run types (equity curve is backtest-only).
</domain>

<decisions>
## Implementation Decisions

### Charting library (LOCKED)
- **Recharts** (v3.9.2, explicit React 19 peer support). This is the "locked charting library" referenced in ANLX-01. PROJECT.md previously listed "charting library" as an unnamed open item; operator locked Recharts on 2026-07-09.
- Render the equity curve with a Recharts line chart. Do NOT hand-roll SVG and do NOT introduce a second charting library.
- Add `recharts` to `console/package.json` dependencies.

### Backend serialization exception (LOCKED — Known Gaps #2)
- The ONLY authorized backend change: add the already-computed `equity_curve` field to the existing analytics response. `materialize_backtest_report()` (`src/trading_platform/services/backtest_reporting.py:84`) already computes it; `StrategyAnalyticsService._summarize_backtest()` (`src/trading_platform/services/analytics.py:127-136`) currently filters it out before it reaches `GET /api/v1/analytics/strategies/{strategy_id}`. Stop filtering it out (or pass it through) so the field leaves the service.
- No new business logic, no new computation, no new route. Read-only exposure of already-computed state.
- Cover the change with a route/service-level test asserting `equity_curve` is present in the serialized response.

### Frontend reuse (LOCKED — established by Phases 13/14)
- Reuse the shared `fetchApi`/`useApiQuery` + `ErrorState`/`FetchMeta` pattern from Phase 13 (13-03). No bespoke fetch/error handling.
- Errors must name the endpoint (established honesty convention from Phases 14/15).
- Honest states are mandatory: if `equity_curve` is absent/empty for a run, the chart panel must render an explicit "not available" state, not a broken/empty chart. Same for missing summary metrics.
- Integrate from the run-detail / run-selection surface built in Phase 14.

### Data source
- Both chart and metrics come from the existing `GET /api/v1/analytics/strategies/{strategy_id}` analytics response (`equity_curve` after the exception above; summary/metrics already present).

</decisions>

<specifics>
## Specific Ideas

- Summary metrics to display (exact set from ANLX-02): Sharpe, max drawdown, win rate, P&L, trade count. Source these from the existing `summary`/`metrics` fields already in the analytics response — do not recompute.
- Equity curve is a single time series (line, no dots) — keep it minimal; drawdown overlay is optional/future, not required.
- Testing convention: vitest for console components (matching Phases 13–15); pytest route/service test for the backend serialization change.
</specifics>

<deferred>
## Deferred Ideas

- Drawdown overlay / additional chart types — not required by ANLX-01/02.
- Charting for run types other than backtest.
- Any additional backend read-surface exposure beyond the single `equity_curve` field.
</deferred>

---

*Phase: 16-analytics-and-charting*
*Context gathered: 2026-07-09 via targeted decision capture*
