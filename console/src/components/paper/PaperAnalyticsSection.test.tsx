// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { PaperAnalyticsSection } from "./PaperAnalyticsSection";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const ANALYTICS = {
  paper: {
    latest_account_snapshot: null,
    latest_reconciliation: {
      scope: "account",
      run_id: "r",
      status: "succeeded",
      as_of_session: "2026-01-05",
      finding_count: 0,
      blocking_count: 0,
      blocks_execution: false,
      completed_at: "2026-01-05T21:00:00Z",
    },
    recent_execution_findings: [],
  },
};

function stub(active: () => Promise<Response>) {
  const fn = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/backend/api/v1/analytics/strategies/")) {
      return Promise.resolve(jsonResponse(200, ANALYTICS));
    }
    if (url.includes("/backend/api/v1/controls/active-paper-strategy")) {
      return active();
    }
    throw new Error(`unexpected fetch URL ${url}`);
  });
  vi.stubGlobal("fetch", fn);
}

async function flush() {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) {
      await Promise.resolve();
    }
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PaperAnalyticsSection trading-blocked wiring", () => {
  it("shows the blocker from the active-paper-strategy GET instead of the reassuring text", async () => {
    stub(() =>
      Promise.resolve(
        jsonResponse(200, {
          strategy_id: "trend_following_daily",
          trading_blocked_reasons: ["strategy_disabled"],
        }),
      ),
    );
    render(<PaperAnalyticsSection />);
    await flush();

    expect(screen.getByText("TRADING BLOCKED: strategy disabled")).toBeTruthy();
    expect(screen.queryByText("does not block execution")).toBeNull();
  });

  it("shows does not block execution only once the blockers are known and empty", async () => {
    stub(() =>
      Promise.resolve(
        jsonResponse(200, { strategy_id: "trend_following_daily", trading_blocked_reasons: [] }),
      ),
    );
    render(<PaperAnalyticsSection />);
    await flush();

    expect(screen.getByText("does not block execution")).toBeTruthy();
  });

  it("shows Trading permission unknown while loading and when the fetch fails", async () => {
    stub(() => new Promise<Response>(() => {}));
    render(<PaperAnalyticsSection />);
    await flush();
    expect(screen.getByText("Trading permission unknown")).toBeTruthy();
    expect(screen.queryByText("does not block execution")).toBeNull();
    cleanup();

    stub(() => Promise.resolve(jsonResponse(500, { detail: "boom" })));
    render(<PaperAnalyticsSection />);
    await flush();
    expect(screen.getByText("Trading permission unknown")).toBeTruthy();
    expect(screen.queryByText("does not block execution")).toBeNull();
  });
});
