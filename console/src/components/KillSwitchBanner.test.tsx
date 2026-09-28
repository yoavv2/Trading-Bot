// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import { KillSwitchBanner } from "./KillSwitchBanner";
import { dispatchControlChanged } from "@/components/controls/controlEvents";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

type Mode = "armed" | "tripped" | "error";

function stubFetch(current: { mode: Mode }) {
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/system/kill-switch")) {
      if (current.mode === "error") {
        return Promise.resolve(jsonResponse(500, { detail: "boom" }));
      }
      return Promise.resolve(
        jsonResponse(200, {
          name: "global",
          state: current.mode,
          is_tripped: current.mode === "tripped",
          last_changed_at: "2026-09-28T00:00:00Z",
          last_change_actor: "op",
          last_change_reason: "test",
          last_change_run_id: null,
        }),
      );
    }
    if (url.includes("/api/v1/job-types")) {
      return Promise.resolve(
        jsonResponse(200, { mutations_enabled: true, items: [] }),
      );
    }
    throw new Error(`KillSwitchBanner.test.tsx: unexpected fetch URL ${url}`);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function killSwitchCalls(fn: ReturnType<typeof vi.fn>): number {
  return fn.mock.calls.filter((call) =>
    String(call[0]).includes("/api/v1/system/kill-switch"),
  ).length;
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

describe("KillSwitchBanner", () => {
  it("armed -> slim ARMED bar with a Trip Kill Switch trigger and no red banner", async () => {
    stubFetch({ mode: "armed" });
    render(<KillSwitchBanner />);
    await flush();

    expect(screen.getByText("Kill switch: ARMED")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Trip Kill Switch" })).toBeTruthy();
    expect(screen.queryByText(/KILL SWITCH TRIPPED/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Reset Kill Switch" })).toBeNull();
  });

  it("tripped -> red banner text plus a Reset Kill Switch trigger", async () => {
    stubFetch({ mode: "tripped" });
    render(<KillSwitchBanner />);
    await flush();

    expect(
      screen.getByText("KILL SWITCH TRIPPED — order submission halted"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reset Kill Switch" })).toBeTruthy();
    expect(screen.queryByText("Kill switch: ARMED")).toBeNull();
  });

  it("fetch failure -> amber UNKNOWN banner and no Trip/Reset trigger", async () => {
    stubFetch({ mode: "error" });
    render(<KillSwitchBanner />);
    await flush();

    expect(screen.getByText(/Kill-switch state UNKNOWN/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Trip Kill Switch" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reset Kill Switch" })).toBeNull();
  });

  it("killswitch:changed triggers a refetch", async () => {
    const current = { mode: "armed" as Mode };
    const fn = stubFetch(current);
    render(<KillSwitchBanner />);
    await flush();
    expect(killSwitchCalls(fn)).toBe(1);

    current.mode = "tripped";
    act(() => {
      dispatchControlChanged("killswitch");
    });
    await flush();

    expect(killSwitchCalls(fn)).toBe(2);
    expect(
      screen.getByText("KILL SWITCH TRIPPED — order submission halted"),
    ).toBeTruthy();
  });

  it("keeps the trigger mounted (same DOM node, dialog open) across an armed -> tripped flip", async () => {
    // 20-18 caller constraint: one stable mount position, isTripped as a prop.
    const current = { mode: "armed" as Mode };
    stubFetch(current);
    render(<KillSwitchBanner />);
    await flush();

    const trigger = screen.getByRole("button", { name: "Trip Kill Switch" });
    fireEvent.click(trigger);
    await flush();
    expect(
      screen.getByText("Current state: ARMED. This will change it to: TRIPPED."),
    ).toBeTruthy();

    current.mode = "tripped";
    act(() => {
      dispatchControlChanged("killswitch");
    });
    await flush();

    expect(
      screen.getByText("KILL SWITCH TRIPPED — order submission halted"),
    ).toBeTruthy();
    expect(trigger.isConnected).toBe(true);
    expect(trigger.textContent).toBe("Reset Kill Switch");
    expect(
      screen.getByText("Current state: ARMED. This will change it to: TRIPPED."),
    ).toBeTruthy();
  });
});

describe("WR-C-06: an open dialog survives a failed state re-read", () => {
  it("keeps the open confirm dialog (and typed reason) when the refetch fails, hides the trigger, and re-shows it on recovery", async () => {
    const current = { mode: "armed" as Mode };
    stubFetch(current);
    render(<KillSwitchBanner />);
    await flush();

    const trigger = screen.getByRole("button", { name: "Trip Kill Switch" });
    fireEvent.click(trigger);
    await flush();
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "drill" } });

    current.mode = "error";
    act(() => {
      dispatchControlChanged("killswitch");
    });
    await flush();

    expect(screen.getByText(/Kill-switch state UNKNOWN/)).toBeTruthy();
    // The dialog is still open with the operator's input intact.
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect((screen.getByLabelText("Reason") as HTMLTextAreaElement).value).toBe("drill");
    // No trigger while the state is unknown (honesty rule): only the dialog's
    // own confirm button carries that name now.
    expect(trigger.isConnected).toBe(false);
    expect(screen.getAllByRole("button", { name: "Trip Kill Switch" })).toHaveLength(1);

    current.mode = "armed";
    act(() => {
      dispatchControlChanged("killswitch");
    });
    await flush();

    expect(screen.getByText("Kill switch: ARMED")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect((screen.getByLabelText("Reason") as HTMLTextAreaElement).value).toBe("drill");
    expect(screen.getAllByRole("button", { name: "Trip Kill Switch" })).toHaveLength(2);
  });
});
