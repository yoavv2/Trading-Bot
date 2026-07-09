// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { SummaryMetricsPanel } from "./SummaryMetricsPanel";

afterEach(() => {
  cleanup();
});

const NOT_AVAILABLE_TEXT = "Summary metrics not available for this run.";

describe("SummaryMetricsPanel", () => {
  it("renders all five labels and values for a full metrics object", () => {
    render(
      <SummaryMetricsPanel
        metrics={{
          sharpe_ratio: 1.42,
          max_drawdown_pct: -12.5,
          win_rate_pct: 55.3,
          total_return_pct: 8.9,
          trade_count: 37,
        }}
      />,
    );

    expect(screen.getByText("Sharpe")).toBeTruthy();
    expect(screen.getByText("Max drawdown %")).toBeTruthy();
    expect(screen.getByText("Win rate %")).toBeTruthy();
    expect(screen.getByText("Total return %")).toBeTruthy();
    expect(screen.getByText("Trade count")).toBeTruthy();
    expect(screen.getByText("1.42")).toBeTruthy();
    expect(screen.getByText("-12.5")).toBeTruthy();
    expect(screen.getByText("55.3")).toBeTruthy();
    expect(screen.getByText("8.9")).toBeTruthy();
    expect(screen.getByText("37")).toBeTruthy();
    expect(screen.queryByText(NOT_AVAILABLE_TEXT)).toBeNull();
  });

  it("renders the not-available state for null metrics", () => {
    render(<SummaryMetricsPanel metrics={null} />);

    expect(screen.getByText(NOT_AVAILABLE_TEXT)).toBeTruthy();
  });

  it("renders the not-available state for undefined metrics", () => {
    render(<SummaryMetricsPanel metrics={undefined} />);

    expect(screen.getByText(NOT_AVAILABLE_TEXT)).toBeTruthy();
  });

  it("renders '—' for a missing individual key without throwing", () => {
    render(
      <SummaryMetricsPanel
        metrics={{
          max_drawdown_pct: -5,
          win_rate_pct: 60,
          total_return_pct: 4.1,
          trade_count: 10,
          // sharpe_ratio intentionally omitted
        }}
      />,
    );

    expect(screen.getByText("Sharpe")).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByText(NOT_AVAILABLE_TEXT)).toBeNull();
  });
});
