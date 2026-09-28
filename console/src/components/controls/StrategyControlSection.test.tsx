// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrategyControlSection } from "./StrategyControlSection";
import { dispatchControlChanged } from "./controlEvents";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

type Status = "enabled" | "disabled";

function stubFetch(current: { status: Status | "error" }) {
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/controls/strategies/")) {
      if (current.status === "error") {
        return Promise.resolve(jsonResponse(500, { detail: "boom" }));
      }
      return Promise.resolve(
        jsonResponse(200, {
          strategy_id: "trend_following_daily",
          status: current.status,
          updated_at: null,
        }),
      );
    }
    if (url.includes("/api/v1/job-types")) {
      return Promise.resolve(
        jsonResponse(200, { mutations_enabled: true, items: [] }),
      );
    }
    throw new Error(`StrategyControlSection.test.tsx: unexpected fetch URL ${url}`);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StrategyControlSection", () => {
  it("disabled -> DISABLED badge and an Enable Strategy trigger", async () => {
    stubFetch({ status: "disabled" });
    render(<StrategyControlSection />);
    await flush();

    expect(screen.getByText("DISABLED")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Enable Strategy" })).toBeTruthy();
  });

  it("enabled -> ENABLED badge and a Disable Strategy trigger", async () => {
    stubFetch({ status: "enabled" });
    render(<StrategyControlSection />);
    await flush();

    expect(screen.getByText("ENABLED")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Disable Strategy" })).toBeTruthy();
  });

  it("control-state fetch failure -> ErrorState and no Enable/Disable trigger", async () => {
    stubFetch({ status: "error" });
    render(<StrategyControlSection />);
    await flush();

    expect(screen.getByText("Request failed")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Enable Strategy" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Disable Strategy" })).toBeNull();
    expect(screen.queryByText("ENABLED")).toBeNull();
    expect(screen.queryByText("DISABLED")).toBeNull();
  });

  it("keeps the trigger mounted (same DOM node, dialog open) across a strategy:changed state flip", async () => {
    // 20-18 caller constraint: one stable mount position, enabled passed as a prop.
    const current = { status: "enabled" as Status | "error" };
    stubFetch(current);
    render(<StrategyControlSection />);
    await flush();

    const trigger = screen.getByRole("button", { name: "Disable Strategy" });
    fireEvent.click(trigger);
    await flush();
    expect(
      screen.getByText("Current state: ENABLED. This will change it to: DISABLED."),
    ).toBeTruthy();

    current.status = "disabled";
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

describe("WR-C-06: StrategyControlSection keeps an open dialog across a failed state re-read", () => {
  it("keeps the open confirm dialog when the control-state refetch fails and hides the trigger", async () => {
    const current = { status: "enabled" as Status | "error" };
    stubFetch(current);
    render(<StrategyControlSection />);
    await flush();

    const trigger = screen.getByRole("button", { name: "Disable Strategy" });
    fireEvent.click(trigger);
    await flush();
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "drill" } });

    current.status = "error";
    act(() => {
      dispatchControlChanged("strategy");
    });
    await flush();

    expect(screen.getByText("Request failed")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect((screen.getByLabelText("Reason") as HTMLTextAreaElement).value).toBe("drill");
    expect(trigger.isConnected).toBe(false);
    expect(screen.getAllByRole("button", { name: "Disable Strategy" })).toHaveLength(1);

    current.status = "enabled";
    act(() => {
      dispatchControlChanged("strategy");
    });
    await flush();

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect((screen.getByLabelText("Reason") as HTMLTextAreaElement).value).toBe("drill");
    expect(screen.getAllByRole("button", { name: "Disable Strategy" })).toHaveLength(2);
  });
});
