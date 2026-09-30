// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrategyOverviewPanel } from "./StrategyOverviewPanel";
import { dispatchControlChanged } from "@/components/controls/controlEvents";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

type Status = "enabled" | "disabled" | "error";
type CatalogState = "success" | "error" | "empty";
type Stub = {
  control: Status;
  mutationsEnabled: boolean;
  catalog?: CatalogState;
};

const STRATEGIES = [
  {
    strategy_id: "donchian_breakout_daily",
    display_name: "Donchian Breakout Daily",
    version: "v1",
    enabled: true,
    description: "Breaks through the recent channel high.",
    config_reference: "config/strategies/donchian_breakout_daily.yaml",
    universe: ["SPY", "QQQ"],
    universe_size: 2,
    indicators: { entry_window: 55 },
    risk: { risk_per_trade: 0.01 },
    exits: { exit_window: 20 },
  },
  {
    strategy_id: "rsi_mean_reversion_daily",
    display_name: "RSI Mean Reversion Daily",
    version: "v1",
    enabled: true,
    description: "Buys oversold RSI readings.",
    config_reference: "config/strategies/rsi_mean_reversion_daily.yaml",
    universe: ["AAPL"],
    universe_size: 1,
    indicators: { rsi_window: 14, oversold: 30 },
    risk: { risk_per_trade: 0.01 },
    exits: { overbought: 70 },
  },
  {
    strategy_id: "time_series_momentum_daily",
    display_name: "Time Series Momentum Daily",
    version: "v1",
    enabled: true,
    description: "Follows twelve-month momentum.",
    config_reference: "config/strategies/time_series_momentum_daily.yaml",
    universe: ["MSFT"],
    universe_size: 1,
    indicators: { lookback_periods: 252 },
    risk: { risk_per_trade: 0.01 },
    exits: { close_below: "lookback_close" },
  },
  {
    strategy_id: "trend_following_daily",
    display_name: "Trend Following Daily",
    version: "v1",
    // Static config flag deliberately opposite of the DB control state in
    // most tests: the badge must never read it.
    enabled: true,
    description: "Uses a dual SMA crossover.",
    config_reference: "config/strategies/trend_following_daily.yaml",
    universe: ["AAPL"],
    universe_size: 1,
    indicators: { short_window: 50, long_window: 200 },
    risk: { risk_per_trade: 0.01 },
    exits: { close_below: "sma_50" },
  },
];

function stubFetch(current: Stub) {
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/controls/strategies/")) {
      if (current.control === "error") {
        return Promise.resolve(jsonResponse(500, { detail: "boom" }));
      }
      return Promise.resolve(
        jsonResponse(200, {
          strategy_id: decodeURIComponent(url.split("/").at(-1) ?? ""),
          status: current.control,
          updated_at: null,
        }),
      );
    }
    if (url.endsWith("/api/v1/strategies")) {
      if (current.catalog === "error") {
        return Promise.resolve(jsonResponse(500, { detail: "catalog boom" }));
      }
      const strategies = current.catalog === "empty" ? [] : STRATEGIES;
      return Promise.resolve(
        jsonResponse(200, {
          count: strategies.length,
          strategies,
          operator_read_api: {},
        }),
      );
    }
    if (url.includes("/api/v1/job-types")) {
      return Promise.resolve(
        jsonResponse(200, {
          mutations_enabled: current.mutationsEnabled,
          items: [],
        }),
      );
    }
    throw new Error(
      `StrategyOverviewPanel.test.tsx: unexpected fetch URL ${url}`,
    );
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StrategyOverviewPanel catalog", () => {
  it("renders all four strategies and defaults to trend_following_daily", async () => {
    stubFetch({ control: "enabled", mutationsEnabled: true });
    render(<StrategyOverviewPanel />);
    await flush();

    const picker = screen.getByLabelText("Selected strategy") as HTMLSelectElement;
    expect(picker.value).toBe("trend_following_daily");
    expect(screen.getAllByRole("option")).toHaveLength(4);
    expect(screen.getByText("4 registered strategies")).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "Trend Following Daily" }),
    ).toBeTruthy();
    expect(screen.getByText("Uses a dual SMA crossover.")).toBeTruthy();
  });

  it("selection changes metadata, control endpoint, and job shortcuts", async () => {
    const fetchSpy = stubFetch({
      control: "disabled",
      mutationsEnabled: true,
    });
    render(<StrategyOverviewPanel />);
    await flush();

    fireEvent.change(screen.getByLabelText("Selected strategy"), {
      target: { value: "rsi_mean_reversion_daily" },
    });
    await flush();

    expect(
      screen.getByRole("heading", { name: "RSI Mean Reversion Daily" }),
    ).toBeTruthy();
    expect(screen.getByText("Buys oversold RSI readings.")).toBeTruthy();
    expect(screen.getByText("rsi_window")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Run backtest" }).getAttribute("href"),
    ).toBe("/jobs/new?type=backtest&strategy_id=rsi_mean_reversion_daily");
    expect(
      screen.getByRole("link", { name: "Evaluate risk" }).getAttribute("href"),
    ).toBe(
      "/jobs/new?type=risk-evaluation&strategy_id=rsi_mean_reversion_daily",
    );
    expect(fetchSpy).toHaveBeenCalledWith(
      "/backend/api/v1/controls/strategies/rsi_mean_reversion_daily",
      { cache: "no-store" },
    );
  });

  it("renders catalog loading, failure, and empty states", async () => {
    let resolveCatalog: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => {
        return new Promise<Response>((resolve) => {
          resolveCatalog = resolve;
        });
      }),
    );
    const loadingView = render(<StrategyOverviewPanel />);
    expect(screen.getByRole("status").textContent).toContain(
      "Loading strategies",
    );
    await act(async () => {
      resolveCatalog?.(
        jsonResponse(200, { count: 0, strategies: [], operator_read_api: {} }),
      );
    });
    expect(screen.getByText("No strategies registered")).toBeTruthy();
    loadingView.unmount();

    stubFetch({
      control: "enabled",
      mutationsEnabled: true,
      catalog: "error",
    });
    const errorView = render(<StrategyOverviewPanel />);
    await flush();
    expect(screen.getByText("Strategy catalog unavailable")).toBeTruthy();
    expect(screen.getByText("/api/v1/strategies — HTTP 500")).toBeTruthy();
    errorView.unmount();

    stubFetch({
      control: "enabled",
      mutationsEnabled: true,
      catalog: "empty",
    });
    render(<StrategyOverviewPanel />);
    await flush();
    expect(screen.getByText("No strategies registered")).toBeTruthy();
    expect(screen.queryByLabelText("Selected strategy")).toBeNull();
  });
});

describe("StrategyOverviewPanel controls and capabilities", () => {
  it("badge follows DB control status even though config enabled is true", async () => {
    stubFetch({ control: "disabled", mutationsEnabled: true });
    render(<StrategyOverviewPanel />);
    await flush();

    expect(screen.getByText("DISABLED")).toBeTruthy();
    expect(screen.queryByText("ENABLED")).toBeNull();
    expect(screen.getByRole("button", { name: "Enable Strategy" })).toBeTruthy();
  });

  it("control-state 500 shows unavailable and no badge or trigger", async () => {
    stubFetch({ control: "error", mutationsEnabled: true });
    render(<StrategyOverviewPanel />);
    await flush();

    expect(screen.getByText("Control state unavailable")).toBeTruthy();
    expect(screen.queryByText("ENABLED")).toBeNull();
    expect(screen.queryByText("DISABLED")).toBeNull();
    expect(screen.queryByRole("button", { name: "Enable Strategy" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Disable Strategy" })).toBeNull();
  });

  it("mutations disabled makes shortcuts disabled with the capability reason", async () => {
    stubFetch({ control: "enabled", mutationsEnabled: false });
    render(<StrategyOverviewPanel />);
    await flush();

    expect(
      (screen.getByRole("button", { name: "Evaluate risk" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Run backtest" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      screen.getAllByText("Mutations disabled on this deployment").length,
    ).toBeGreaterThan(0);
  });

  it("keeps the trigger mounted and dialog open across a state flip", async () => {
    const current: Stub = { control: "enabled", mutationsEnabled: true };
    stubFetch(current);
    render(<StrategyOverviewPanel />);
    await flush();

    const trigger = screen.getByRole("button", { name: "Disable Strategy" });
    fireEvent.click(trigger);
    await flush();
    expect(
      screen.getByText("Current state: ENABLED. This will change it to: DISABLED."),
    ).toBeTruthy();

    current.control = "disabled";
    act(() => {
      dispatchControlChanged("strategy");
    });
    await flush();

    expect(screen.getByText("DISABLED")).toBeTruthy();
    expect(trigger.isConnected).toBe(true);
    expect(trigger.textContent).toBe("Enable Strategy");
    expect(
      screen.getByText("Current state: ENABLED. This will change it to: DISABLED."),
    ).toBeTruthy();
  });

  it("closes an open strategy dialog when the selected strategy changes", async () => {
    stubFetch({ control: "enabled", mutationsEnabled: true });
    render(<StrategyOverviewPanel />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Disable Strategy" }));
    await flush();
    expect(screen.getByRole("dialog")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Selected strategy"), {
      target: { value: "donchian_breakout_daily" },
    });
    await flush();

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      screen.getByRole("heading", { name: "Donchian Breakout Daily" }),
    ).toBeTruthy();
  });
});

describe("WR-C-06: open dialog survives a failed state re-read", () => {
  it("keeps the confirm dialog when refetch fails and hides only the trigger", async () => {
    const current: Stub = { control: "enabled", mutationsEnabled: true };
    stubFetch(current);
    render(<StrategyOverviewPanel />);
    await flush();

    const trigger = screen.getByRole("button", { name: "Disable Strategy" });
    fireEvent.click(trigger);
    await flush();
    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "drill" },
    });

    current.control = "error";
    act(() => {
      dispatchControlChanged("strategy");
    });
    await flush();

    expect(screen.getByText("Control state unavailable")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect((screen.getByLabelText("Reason") as HTMLTextAreaElement).value).toBe(
      "drill",
    );
    expect(trigger.isConnected).toBe(false);
    expect(
      screen.getAllByRole("button", { name: "Disable Strategy" }),
    ).toHaveLength(1);

    current.control = "enabled";
    act(() => {
      dispatchControlChanged("strategy");
    });
    await flush();

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect((screen.getByLabelText("Reason") as HTMLTextAreaElement).value).toBe(
      "drill",
    );
    expect(
      screen.getAllByRole("button", { name: "Disable Strategy" }),
    ).toHaveLength(2);
  });
});
