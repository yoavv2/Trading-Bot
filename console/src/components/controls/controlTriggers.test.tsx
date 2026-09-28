// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { KillSwitchControlTrigger } from "./KillSwitchControlTrigger";
import { StrategyControlTrigger } from "./StrategyControlTrigger";
import { useStrategyControlState } from "./useStrategyControlState";
import {
  KILL_SWITCH_CHANGED_EVENT,
  STRATEGY_CHANGED_EVENT,
  dispatchControlChanged,
} from "./controlEvents";
import type { JobTypesCatalog } from "../jobs/types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const CATALOG_ENABLED: JobTypesCatalog = { mutations_enabled: true, items: [] };
const CATALOG_DISABLED: JobTypesCatalog = { mutations_enabled: false, items: [] };

type CapturedCall = {
  url: string;
  method: string | undefined;
  body: unknown;
};

/**
 * Stubs global fetch for the two endpoint families these components hit:
 * /backend/api/v1/job-types (useMutationCapability, GET) and any
 * /backend/api/v1/controls/... PUT/GET call. Every PUT to a controls
 * endpoint resolves with `putResponse` (default: a changed:true success).
 */
function stubControlFetch(options: {
  catalog?: { status: number; body: unknown };
  putResponse?: { status: number; body: unknown };
  getResponse?: { status: number; body: unknown };
} = {}) {
  const catalog = options.catalog ?? { status: 200, body: CATALOG_ENABLED };
  const calls: CapturedCall[] = [];
  const fn = vi.fn().mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const body = init?.body ? JSON.parse(init.body as string) : null;
      calls.push({ url, method: init?.method, body });

      if (url.includes("/backend/api/v1/job-types")) {
        return Promise.resolve(jsonResponse(catalog.status, catalog.body));
      }
      if (url.includes("/backend/api/v1/controls/")) {
        if (init?.method === "PUT") {
          const putResponse =
            options.putResponse ??
            ({ status: 200, body: { changed: true } } as const);
          return Promise.resolve(
            jsonResponse(putResponse.status, putResponse.body),
          );
        }
        const getResponse =
          options.getResponse ??
          ({
            status: 200,
            body: {
              strategy_id: "trend_following_daily",
              status: "enabled",
              updated_at: null,
            },
          } as const);
        return Promise.resolve(jsonResponse(getResponse.status, getResponse.body));
      }
      throw new Error(`controlTriggers.test.tsx: unexpected fetch URL ${url}`);
    },
  );
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("KillSwitchControlTrigger", () => {
  it("isTripped=false renders 'Trip Kill Switch', opens with the ARMED->TRIPPED body and no RESET field, and calls tripKillSwitch + dispatches killswitch:changed", async () => {
    const { calls } = stubControlFetch();
    const handler = vi.fn();
    window.addEventListener(KILL_SWITCH_CHANGED_EVENT, handler);

    render(<KillSwitchControlTrigger isTripped={false} />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
    await flush();

    expect(
      screen.getByText("Current state: ARMED. This will change it to: TRIPPED."),
    ).toBeTruthy();
    expect(screen.queryByLabelText("Type RESET to confirm")).toBeNull();

    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "  drill  " },
    });
    const confirmButtons = screen.getAllByRole("button", {
      name: "Trip Kill Switch",
    });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);
    await flush();

    const putCall = calls.find((call) => call.method === "PUT");
    expect(putCall?.url).toContain("/api/v1/controls/kill-switch");
    expect(putCall?.body).toEqual({ state: "tripped", reason: "drill" });
    expect(handler).toHaveBeenCalledTimes(1);

    window.removeEventListener(KILL_SWITCH_CHANGED_EVENT, handler);
  });

  it("isTripped=true renders 'Reset Kill Switch', requires the RESET field, and calls resetKillSwitch", async () => {
    const { calls } = stubControlFetch();

    render(<KillSwitchControlTrigger isTripped={true} />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Reset Kill Switch" }));
    await flush();

    expect(
      screen.getByText("Current state: TRIPPED. This will change it to: ARMED."),
    ).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "resume" },
    });
    const confirmButtons = screen.getAllByRole("button", {
      name: "Reset Kill Switch",
    });
    const dialogConfirm = confirmButtons[confirmButtons.length - 1];
    expect(dialogConfirm.hasAttribute("disabled")).toBe(true);

    fireEvent.change(screen.getByLabelText("Type RESET to confirm"), {
      target: { value: "RESET" },
    });
    expect(dialogConfirm.hasAttribute("disabled")).toBe(false);

    fireEvent.click(dialogConfirm);
    await flush();

    const putCall = calls.find((call) => call.method === "PUT");
    expect(putCall?.url).toContain("/api/v1/controls/kill-switch");
    expect(putCall?.body).toEqual({ state: "armed", reason: "resume" });
  });

  it("renders disabled with the inline reason when mutations are off", async () => {
    stubControlFetch({ catalog: { status: 200, body: CATALOG_DISABLED } });

    render(<KillSwitchControlTrigger isTripped={false} />);
    await flush();

    const trigger = screen.getByRole("button", { name: "Trip Kill Switch" });
    expect(trigger.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Mutations disabled on this deployment")).toBeTruthy();
  });

  it("renders disabled with an unknown-state reason when the capability fetch fails", async () => {
    stubControlFetch({ catalog: { status: 500, body: {} } });

    render(<KillSwitchControlTrigger isTripped={false} />);
    await flush();

    const trigger = screen.getByRole("button", { name: "Trip Kill Switch" });
    expect(trigger.hasAttribute("disabled")).toBe(true);
    expect(
      screen.getByText(/Mutation availability unknown/),
    ).toBeTruthy();
  });

  it("keeps showing the Already TRIPPED unchanged notice for the state confirmed at open, even after the live isTripped prop flips underneath the still-open dialog", async () => {
    stubControlFetch({ putResponse: { status: 200, body: { changed: false } } });

    const { rerender } = render(<KillSwitchControlTrigger isTripped={false} />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
    await flush();

    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "drill" },
    });
    const confirmButtons = screen.getAllByRole("button", {
      name: "Trip Kill Switch",
    });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);
    await flush();

    // Simulate the caller refetching and flipping isTripped after the
    // killswitch:changed dispatch from onDone — the dialog must not
    // re-derive its heading/body from the now-live (flipped) prop.
    rerender(<KillSwitchControlTrigger isTripped={true} />);
    await flush();

    expect(
      screen.getByText("Already TRIPPED — no change (recorded)"),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Trip Kill Switch" })).toBeTruthy();
  });
});

describe("StrategyControlTrigger", () => {
  it("enabled=true renders 'Disable Strategy' and calls disableStrategy; success dispatches strategy:changed", async () => {
    const { calls } = stubControlFetch();
    const handler = vi.fn();
    window.addEventListener(STRATEGY_CHANGED_EVENT, handler);

    render(
      <StrategyControlTrigger strategyId="trend_following_daily" enabled={true} />,
    );
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Disable Strategy" }));
    await flush();

    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "maintenance" },
    });
    const confirmButtons = screen.getAllByRole("button", {
      name: "Disable Strategy",
    });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);
    await flush();

    const putCall = calls.find((call) => call.method === "PUT");
    expect(putCall?.url).toContain(
      "/api/v1/controls/strategies/trend_following_daily",
    );
    expect(putCall?.body).toEqual({ status: "disabled", reason: "maintenance" });
    expect(handler).toHaveBeenCalledTimes(1);

    window.removeEventListener(STRATEGY_CHANGED_EVENT, handler);
  });

  it("enabled=false renders 'Enable Strategy' and calls enableStrategy", async () => {
    const { calls } = stubControlFetch();

    render(
      <StrategyControlTrigger strategyId="trend_following_daily" enabled={false} />,
    );
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Enable Strategy" }));
    await flush();

    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "resume trading" },
    });
    const confirmButtons = screen.getAllByRole("button", {
      name: "Enable Strategy",
    });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);
    await flush();

    const putCall = calls.find((call) => call.method === "PUT");
    expect(putCall?.url).toContain(
      "/api/v1/controls/strategies/trend_following_daily",
    );
    expect(putCall?.body).toEqual({
      status: "enabled",
      reason: "resume trading",
    });
  });

  it("renders disabled with the inline reason when mutations are off", async () => {
    stubControlFetch({ catalog: { status: 200, body: CATALOG_DISABLED } });

    render(
      <StrategyControlTrigger strategyId="trend_following_daily" enabled={true} />,
    );
    await flush();

    const trigger = screen.getByRole("button", { name: "Disable Strategy" });
    expect(trigger.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Mutations disabled on this deployment")).toBeTruthy();
  });
});

describe("useStrategyControlState", () => {
  function Probe({ strategyId }: { strategyId: string }) {
    const { loading, result } = useStrategyControlState(strategyId);
    if (loading && !result) {
      return <p>loading</p>;
    }
    if (result?.ok) {
      return <p>{`status:${result.data.status}`}</p>;
    }
    return <p>error</p>;
  }

  it("fetches /api/v1/controls/strategies/{id} and refetches after strategy:changed", async () => {
    const { calls } = stubControlFetch({
      getResponse: {
        status: 200,
        body: {
          strategy_id: "trend_following_daily",
          status: "enabled",
          updated_at: null,
        },
      },
    });

    render(<Probe strategyId="trend_following_daily" />);
    await flush();

    expect(screen.getByText("status:enabled")).toBeTruthy();
    const getCallsBefore = calls.filter((call) => call.method === undefined);
    expect(
      getCallsBefore.some((call) =>
        call.url.includes(
          "/backend/api/v1/controls/strategies/trend_following_daily",
        ),
      ),
    ).toBe(true);
    const countBefore = getCallsBefore.length;

    act(() => {
      dispatchControlChanged("strategy");
    });
    await flush();

    const getCallsAfter = calls.filter((call) => call.method === undefined);
    expect(getCallsAfter.length).toBeGreaterThan(countBefore);
  });
});

describe("WR-C-02: triggers re-verify state after an ambiguous control failure", () => {
  async function tripWith(putResponse: { status: number; body: unknown }, domainEvent: string) {
    stubControlFetch({ putResponse });
    const handler = vi.fn();
    window.addEventListener(domainEvent, handler);
    render(<KillSwitchControlTrigger isTripped={false} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Trip Kill Switch" }));
    await flush();
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "drill" } });
    const buttons = screen.getAllByRole("button", { name: "Trip Kill Switch" });
    fireEvent.click(buttons[buttons.length - 1]);
    await flush();
    window.removeEventListener(domainEvent, handler);
    return handler;
  }

  it("kill switch: a 500 dispatches killswitch:changed so displays re-verify", async () => {
    const handler = await tripWith(
      { status: 500, body: { detail: { code: "internal_error" } } },
      KILL_SWITCH_CHANGED_EVENT,
    );
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("kill switch: a definitive 422 rejection does not dispatch", async () => {
    const handler = await tripWith(
      { status: 422, body: { detail: { code: "invalid_control_reason" } } },
      KILL_SWITCH_CHANGED_EVENT,
    );
    expect(handler).not.toHaveBeenCalled();
  });

  it("strategy: a 503 dispatches strategy:changed", async () => {
    stubControlFetch({
      putResponse: { status: 503, body: { detail: { code: "control_write_failed" } } },
    });
    const handler = vi.fn();
    window.addEventListener(STRATEGY_CHANGED_EVENT, handler);
    render(<StrategyControlTrigger strategyId="trend_following_daily" enabled={true} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Disable Strategy" }));
    await flush();
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "drill" } });
    const buttons = screen.getAllByRole("button", { name: "Disable Strategy" });
    fireEvent.click(buttons[buttons.length - 1]);
    await flush();
    window.removeEventListener(STRATEGY_CHANGED_EVENT, handler);
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
