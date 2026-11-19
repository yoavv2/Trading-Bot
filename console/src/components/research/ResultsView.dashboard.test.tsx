// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ResultsView } from "./ResultsView";
import type { Comparison, Metrics } from "@/lib/research/types";

vi.mock("./CurveChart", () => ({
  CurveChart: ({ runId, benchmarkRunId }: { runId: string; benchmarkRunId?: string | null }) => (
    <div data-testid="selected-curves" data-run-id={runId} data-benchmark-run-id={benchmarkRunId ?? ""} />
  ),
}));

afterEach(cleanup);

function metrics(netReturn: number): Metrics {
  return {
    net_total_return: netReturn,
    cagr: netReturn,
    max_drawdown: -0.08,
    drawdown_duration: 12,
    closed_trades: 14,
    open_at_end: { count: 0, unrealized_pnl: null },
    holding_period: { median_sessions: 5, mean_sessions: 6 },
    win_rate: 0.5,
    profit_factor: 1.5,
    expectancy: 20,
    sharpe_daily_ann: 1,
    sortino_daily_ann: 1.2,
    exposure: 0.4,
    turnover: 3,
    total_costs: { currency: 100, slippage: 80, commission: 20, pct_of_initial_capital: 0.001 },
    rounding_slack: 0,
    skipped_fills: 0,
    zero_quantity_fills: 0,
    flags: [],
    notes: {},
  };
}

function candidate(asset: string, name: string, version: number, runId: string, result: number, rank: number) {
  return {
    strategy_version_id: `${asset}-version-${version}`,
    strategy_name: name,
    version_no: version,
    asset,
    status: "eligible",
    status_reasons: [],
    rank,
    co_leading: false,
    windows: {
      validation: {
        run_id: runId,
        metrics: metrics(result),
        evidence: {
          grade: "meets_predefined_study_conditions",
          grade_reasons: [],
          descriptors: {
            closed_trades: 14,
            open_at_end: 0,
            measured_sessions: 180,
            median_holding_sessions: 5,
            holding_ratio: 30,
            clusters: 11,
            cluster_span_sessions: 5,
            concentration: { best_trade_share: 0.2, best_three_share: 0.4, best_month_share: 0.3 },
          },
          limitations: [],
        },
        excess_return_vs_benchmark: result - 0.1,
      },
      development: null,
    },
    benchmark: { validation: { ...metrics(0.1), run_id: `${runId}-benchmark` }, development: null },
  };
}

const comparison = {
  schema_version: 1,
  study: { study_id: "study", name: "Study", kind: "substantive" },
  revision: { revision_id: "revision", revision_no: 1 },
  settings: { initial_capital: "100000", quantity_policy: "fractional", costs: { slippage_bps: "5", commission_per_order: "0" } },
  mode_label: "Independent tests, one asset each; not a portfolio.",
  windows_evaluated: ["validation"],
  windows: { validation: { start: "2025-01-01", end: "2025-12-31" } },
  data: { provider: "test", adjusted: true, data_freeze_id: "freeze", input_digest: "digest", calendar_start: "2014-01-01" },
  code_sha: "code",
  candidates: [
    candidate("AAA", "Momentum", 1, "aaa-1", 0.12, 1),
    candidate("AAA", "Momentum", 2, "aaa-2", 0.18, 2),
    candidate("BBB", "Breakout", 1, "bbb-1", 0.2, 1),
    candidate("BBB", "Breakout", 2, "bbb-2", 0.3, 2),
  ],
  ranking: { objective: "return_first", constraint_value: 0.2, ranking_key: "return", ranking: [], co_leaders: [], verdict: "leading_candidate_identified", profit_factor_affects_order: false },
  verdict: "leading_candidate_identified",
  verdict_label: "leading candidate under these criteria",
  benchmark_note: "Benchmark is a comparison only.",
  final_test: { state: "not_run" },
  limitations: [],
  evidence_limitations: [],
} as unknown as Comparison;

describe("ResultsView dashboard selection", () => {
  it("updates the selected asset, candidate metrics and curve run IDs", () => {
    render(<ResultsView comparison={comparison} />);
    expect(within(screen.getByRole("region", { name: "Selected candidate metrics" })).getByText("12.00%")).toBeTruthy();
    expect(screen.getByTestId("selected-curves").getAttribute("data-run-id")).toBe("aaa-1");

    fireEvent.click(screen.getByRole("tab", { name: "BBB" }));
    expect(within(screen.getByRole("region", { name: "Selected candidate metrics" })).getByText("20.00%")).toBeTruthy();
    expect(screen.getByTestId("selected-curves").getAttribute("data-run-id")).toBe("bbb-1");
    expect(screen.getByTestId("selected-curves").getAttribute("data-benchmark-run-id")).toBe("bbb-1-benchmark");

    const comparisonTable = screen.getByRole("region", { name: "Candidate comparison" });
    fireEvent.click(within(comparisonTable).getByRole("button", { name: /Breakout v2/ }));
    expect(within(screen.getByRole("region", { name: "Selected candidate metrics" })).getByText("30.00%")).toBeTruthy();
    expect(screen.getByTestId("selected-curves").getAttribute("data-run-id")).toBe("bbb-2");
    expect(screen.getByTestId("selected-curves").getAttribute("data-benchmark-run-id")).toBe("bbb-2-benchmark");
  });
});
