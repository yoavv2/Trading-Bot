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

type Stub = { control: Status; mutationsEnabled: boolean };

function stubFetch(current: Stub) {
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/controls/strategies/")) {
      if (current.control === "error") {
        return Promise.resolve(jsonResponse(500, { detail: "boom" }));
      }
      return Promise.resolve(
        jsonResponse(200, {
          strategy_id: "trend_following_daily",
          status: current.control,
          updated_at: null,
        }),
      );
    }
    if (url.includes("/api/v1/strategies/")) {
      return Promise.resolve(
        jsonResponse(200, {
          strategy: {
            strategy_id: "trend_following_daily",
            display_name: "Trend Following Daily",
            version: "v1",
            // Static config flag deliberately opposite of the DB control state
            // in most tests: the badge must never read it.
            enabled: true,
            description: "desc",
            config_reference: "config/strategies/trend_following_daily.yaml",
            universe: ["AAPL"],
            universe_size: 1,
            indicators: {},
            risk: {},
            exits: {},
          },
          operator_reads: {},
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
    throw new Error(`StrategyOverviewPanel.test.tsx: unexpected fetch URL ${url}`);
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

describe("StrategyOverviewPanel", () => {
  it("badge follows DB control status (disabled) even though config enabled is true", async () => {
    stubFetch({ control: "disabled", mutationsEnabled: true });
    render(<StrategyOverviewPanel />);
    await flush();

    expect(screen.getByText("DISABLED")).toBeTruthy();
    expect(screen.queryByText("ENABLED")).toBeNull();
    expect(screen.getByRole("button", { name: "Enable Strategy" })).toBeTruthy();
  });

  it("control-state 500 -> 'Control state unavailable' and no badge or trigger", async () => {
    stubFetch({ control: "error", mutationsEnabled: true });
    render(<StrategyOverviewPanel />);
    await flush();

    expect(screen.getByText("Control state unavailable")).toBeTruthy();
    expect(screen.queryByText("ENABLED")).toBeNull();
    expect(screen.queryByText("DISABLED")).toBeNull();
    expect(screen.queryByRole("button", { name: "Enable Strategy" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Disable Strategy" })).toBeNull();
  });

  it("renders both Run backtest and Evaluate risk deep-links when mutations are enabled", async () => {
    stubFetch({ control: "enabled", mutationsEnabled: true });
    render(<StrategyOverviewPanel />);
    await flush();

    expect(
      screen.getByRole("link", { name: "Run backtest" }).getAttribute("href"),
    ).toBe("/jobs/new?type=backtest&strategy_id=trend_following_daily");
    expect(
      screen.getByRole("link", { name: "Evaluate risk" }).getAttribute("href"),
    ).toBe("/jobs/new?type=risk-evaluation&strategy_id=trend_following_daily");
  });

  it("mutations disabled -> shortcuts are disabled buttons with the capability reason", async () => {
    stubFetch({ control: "enabled", mutationsEnabled: false });
    render(<StrategyOverviewPanel />);
    await flush();

    const risk = screen.getByRole("button", { name: "Evaluate risk" });
    expect((risk as HTMLButtonElement).disabled).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Run backtest" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      screen.getAllByText("Mutations disabled on this deployment").length,
    ).toBeGreaterThan(0);
  });

  it("keeps the trigger mounted (same DOM node, dialog open) across a strategy:changed state flip", async () => {
    // 20-18 caller constraint: one stable mount position, enabled passed as a prop.
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
});
