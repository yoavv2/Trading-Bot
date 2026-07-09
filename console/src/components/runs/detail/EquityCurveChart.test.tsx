// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { EquityCurveChart, type EquityPoint } from "./EquityCurveChart";

// jsdom does not implement ResizeObserver, which Recharts' ResponsiveContainer
// depends on. Without this mock the populated-data branch throws on mount.
// This stays scoped to this test file so the node-environment suites
// (api.test.ts / runScopedFilter.test.ts) are unaffected.
beforeAll(() => {
  if (typeof global.ResizeObserver === "undefined") {
    global.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

afterEach(() => {
  cleanup();
});

const NOT_AVAILABLE_TEXT = "Equity curve not available for this run.";

const samplePoints: EquityPoint[] = [
  {
    session_date: "2026-01-02",
    total_equity: 100000,
    cash: 40000,
    gross_exposure: 60000,
    realized_pnl: 0,
    unrealized_pnl: 0,
    open_positions: 2,
  },
  {
    session_date: "2026-01-03",
    total_equity: 100500,
    cash: 39000,
    gross_exposure: 61500,
    realized_pnl: 200,
    unrealized_pnl: 300,
    open_positions: 3,
  },
];

describe("EquityCurveChart", () => {
  it("renders the not-available state and no chart for an empty array", () => {
    render(<EquityCurveChart points={[]} />);

    expect(screen.getByText(NOT_AVAILABLE_TEXT)).toBeTruthy();
  });

  it("renders the not-available state and no chart for null", () => {
    render(<EquityCurveChart points={null} />);

    expect(screen.getByText(NOT_AVAILABLE_TEXT)).toBeTruthy();
  });

  it("renders a chart without throwing and without the not-available copy for 2+ points", () => {
    render(<EquityCurveChart points={samplePoints} />);

    expect(screen.queryByText(NOT_AVAILABLE_TEXT)).toBeNull();
  });
});
