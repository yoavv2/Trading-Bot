// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { KillSwitchPanel, type KillSwitchData } from "./KillSwitchPanel";
import { KillSwitchControlTrigger } from "@/components/controls/KillSwitchControlTrigger";
import {
  KILL_SWITCH_CHANGED_EVENT,
  dispatchControlChanged,
} from "@/components/controls/controlEvents";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function killSwitchBody(state: "armed" | "tripped"): KillSwitchData {
  return {
    name: "global",
    state,
    is_tripped: state === "tripped",
    last_changed_at: "2026-09-28T00:00:00Z",
    last_change_actor: null,
    last_change_reason: null,
    last_change_run_id: null,
  };
}

/** Stubs fetch; `current.state` can be flipped between requests. */
function stubFetch(current: { state: "armed" | "tripped" }) {
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/v1/system/kill-switch")) {
      return Promise.resolve(jsonResponse(200, killSwitchBody(current.state)));
    }
    if (url.includes("/api/v1/job-types")) {
      return Promise.resolve(
        jsonResponse(200, { mutations_enabled: true, items: [] }),
      );
    }
    throw new Error(`KillSwitchPanel.test.tsx: unexpected fetch URL ${url}`);
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

describe("KillSwitchPanel", () => {
  it("without renderAction renders the state and fetches once", async () => {
    const fn = stubFetch({ state: "armed" });
    render(<KillSwitchPanel />);
    await flush();

    expect(screen.getByText("ARMED")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Kill Switch$/ })).toBeNull();
    expect(killSwitchCalls(fn)).toBe(1);
  });

  it("renderAction receives the fetched data and its output renders, still one fetch", async () => {
    const fn = stubFetch({ state: "tripped" });
    const renderAction = vi.fn((data: KillSwitchData) => (
      <span>{`action for ${data.state}`}</span>
    ));
    render(<KillSwitchPanel renderAction={renderAction} />);
    await flush();

    expect(screen.getByText("action for tripped")).toBeTruthy();
    expect(renderAction).toHaveBeenCalledWith(
      expect.objectContaining({ state: "tripped", is_tripped: true }),
    );
    expect(killSwitchCalls(fn)).toBe(1);
  });

  it("refetches exactly once on the killswitch:changed event", async () => {
    const current = { state: "armed" as "armed" | "tripped" };
    const fn = stubFetch(current);
    render(<KillSwitchPanel />);
    await flush();
    expect(killSwitchCalls(fn)).toBe(1);

    current.state = "tripped";
    act(() => {
      window.dispatchEvent(new CustomEvent(KILL_SWITCH_CHANGED_EVENT));
    });
    await flush();

    expect(killSwitchCalls(fn)).toBe(2);
    expect(screen.getByText("TRIPPED")).toBeTruthy();
  });

  it("keeps the trigger mounted (same DOM node, dialog open) across a state change", async () => {
    // 20-18 caller constraint: the trigger sits at one stable position, so a
    // killswitch:changed refetch that flips is_tripped must not unmount it or
    // its open dialog.
    const current = { state: "armed" as "armed" | "tripped" };
    stubFetch(current);
    render(
      <KillSwitchPanel
        renderAction={(data) => (
          <KillSwitchControlTrigger isTripped={data.is_tripped} />
        )}
      />,
    );
    await flush();

    const trigger = screen.getByRole("button", { name: "Trip Kill Switch" });
    fireEvent.click(trigger);
    await flush();
    expect(
      screen.getByText("Current state: ARMED. This will change it to: TRIPPED."),
    ).toBeTruthy();

    current.state = "tripped";
    act(() => {
      dispatchControlChanged("killswitch");
    });
    await flush();

    expect(screen.getByText("TRIPPED")).toBeTruthy();
    expect(trigger.isConnected).toBe(true);
    expect(trigger.textContent).toBe("Reset Kill Switch");
    // The open dialog still shows the state it was opened against.
    expect(
      screen.getByText("Current state: ARMED. This will change it to: TRIPPED."),
    ).toBeTruthy();
  });
});
