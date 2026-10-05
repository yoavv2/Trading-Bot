// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { useActivePaperStrategy, tradingBlockerLabel } from "./useActivePaperStrategy";
import type { ActivePaperStrategySnapshot } from "./useActivePaperStrategy";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function Probe() {
  return <pre data-testid="snapshot">{JSON.stringify(useActivePaperStrategy())}</pre>;
}

/** The hook result as rendered by the probe (no render-time side effects). */
function snapshot(): ActivePaperStrategySnapshot {
  return JSON.parse(screen.getByTestId("snapshot").textContent ?? "null");
}

function mount(response: () => Promise<Response>) {
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/backend/api/v1/controls/active-paper-strategy")) {
      return response();
    }
    throw new Error(`unexpected fetch URL ${url}`);
  });
  vi.stubGlobal("fetch", fn);
  render(<Probe />);
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

describe("useActivePaperStrategy", () => {
  it("is loading before the response lands, with unknown blockers", async () => {
    mount(() => new Promise<Response>(() => {}));
    await flush();

    expect(snapshot()).toEqual({
      state: "loading",
      strategyId: null,
      displayName: null,
      tradingBlockedReasons: null,
    });
  });

  it("is known with the owner and the blockers, and issues only a GET", async () => {
    const fn = mount(() =>
      Promise.resolve(
        jsonResponse(200, {
          strategy_id: "trend_following_daily",
          display_name: "Trend Following Daily",
          trading_blocked_reasons: ["kill_switch_tripped"],
        }),
      ),
    );
    await flush();

    expect(snapshot()).toEqual({
      state: "known",
      strategyId: "trend_following_daily",
      displayName: "Trend Following Daily",
      tradingBlockedReasons: ["kill_switch_tripped"],
    });
    expect(fn.mock.calls.length).toBeGreaterThan(0);
    for (const call of fn.mock.calls) {
      const init = (call[1] ?? {}) as RequestInit;
      expect(init.method === undefined || init.method === "GET").toBe(true);
      expect(init.body).toBeUndefined();
    }
  });

  it("is known with no owner and an empty blockers list", async () => {
    mount(() =>
      Promise.resolve(
        jsonResponse(200, { strategy_id: null, display_name: null, trading_blocked_reasons: [] }),
      ),
    );
    await flush();

    expect(snapshot()).toEqual({
      state: "known",
      strategyId: null,
      displayName: null,
      tradingBlockedReasons: [],
    });
  });

  it("a failed fetch is unknown with unknown blockers (never none)", async () => {
    mount(() => Promise.resolve(jsonResponse(500, { detail: "boom" })));
    await flush();

    expect(snapshot().state).toBe("unknown");
    expect(snapshot().tradingBlockedReasons).toBeNull();
    expect(snapshot().strategyId).toBeNull();
  });

  it("a malformed body (no strategy_id key) is unknown, not none", async () => {
    mount(() => Promise.resolve(jsonResponse(200, { mutations_enabled: true, items: [] })));
    await flush();

    expect(snapshot().state).toBe("unknown");
  });

  it("an older API without trading_blocked_reasons keeps the blockers unknown", async () => {
    mount(() => Promise.resolve(jsonResponse(200, { strategy_id: "s", display_name: "S" })));
    await flush();

    expect(snapshot().state).toBe("known");
    expect(snapshot().tradingBlockedReasons).toBeNull();
  });

  it("labels every closed blocker and falls back for an unknown one", () => {
    expect(tradingBlockerLabel("kill_switch_tripped")).toBe("kill switch tripped");
    expect(tradingBlockerLabel("brand_new_reason")).toBe("brand new reason");
  });
});
